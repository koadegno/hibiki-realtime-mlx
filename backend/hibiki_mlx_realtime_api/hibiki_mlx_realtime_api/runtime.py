"""Process lifecycle for one loaded Hibiki-Zero MLX runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from typing import Any

from hibiki_mlx_realtime_api.codecs import create_codec_pair
from hibiki_mlx_realtime_api.config import RuntimeConfig
from hibiki_mlx_realtime_api.model import load_language_model, resolve_model_files
from hibiki_mlx_realtime_api.session import RealtimeSession

_FRAME_RATE = 12.5


class RuntimePhase(str, Enum):
    STARTING = "starting"
    RESOLVING_MODEL = "resolving_model"
    LOADING_MODEL = "loading_model"
    LOADING_CODEC = "loading_codec"
    READY = "ready"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """Cheap immutable readiness view for HTTP handlers."""

    phase: RuntimePhase
    ready: bool
    error: str | None


class RuntimeManager:
    """Load one MLX model and one codec pair, then create fresh sessions."""

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        resolve_files: Callable[[RuntimeConfig], Any] = resolve_model_files,
        load_model: Callable[[Any], Any] = load_language_model,
        codec_factory: Callable[..., Any] = create_codec_pair,
        session_factory: Callable[..., Any] = RealtimeSession,
    ) -> None:
        self.config = config
        self._resolve_files = resolve_files
        self._load_model = load_model
        self._codec_factory = codec_factory
        self._session_factory = session_factory
        self._phase = RuntimePhase.STARTING
        self._error: str | None = None
        self._files: Any = None
        self._loaded_model: Any = None
        self._codecs: Any = None
        self._load_task: asyncio.Task[None] | None = None

    @property
    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            phase=self._phase,
            ready=self._phase is RuntimePhase.READY,
            error=self._error,
        )

    async def initialize(self) -> None:
        """Resolve artifacts and warm all process-wide inference state."""
        if self._phase is RuntimePhase.READY:
            return
        try:
            self._phase = RuntimePhase.RESOLVING_MODEL
            files = await asyncio.to_thread(self._resolve_files, self.config)

            self._phase = RuntimePhase.LOADING_MODEL
            loaded_model = await asyncio.to_thread(self._load_model, files)

            self._phase = RuntimePhase.LOADING_CODEC
            lm_config = loaded_model.lm_config
            num_codebooks = max(
                int(lm_config.other_codebooks),
                int(lm_config.generated_codebooks),
            )
            checkpoint_codebooks = int(lm_config.audio_codebooks)
            codecs = await asyncio.to_thread(
                self._codec_factory,
                self.config.codec,
                mimi_path=files.mimi,
                num_codebooks=num_codebooks,
                checkpoint_codebooks=checkpoint_codebooks,
            )
            await asyncio.to_thread(codecs.warmup)

            self._files = files
            self._loaded_model = loaded_model
            self._codecs = codecs
            self._error = None
            self._phase = RuntimePhase.READY
        except Exception as exc:  # lifecycle boundary converts load failure into readiness state
            self._error = f"{type(exc).__name__}: {exc}"
            self._phase = RuntimePhase.FAILED

    def start_background(self) -> asyncio.Task[None]:
        """Start initialization once while allowing `/health` to serve immediately."""
        if self._load_task is None:
            self._load_task = asyncio.create_task(self.initialize(), name="hibiki-mlx-load")
        return self._load_task

    def create_session(self) -> Any:
        """Create session state without reloading the model or Mimi weights."""
        if self._phase is not RuntimePhase.READY:
            raise RuntimeError(f"runtime is not ready: {self._phase.value}")
        max_steps = int(self.config.max_session_minutes * 60.0 * _FRAME_RATE) + 8
        return self._session_factory(
            loaded_model=self._loaded_model,
            codecs=self._codecs,
            queue_capacity=self.config.queue_capacity,
            max_steps=max_steps,
            telemetry_interval_frames=self.config.telemetry_interval_frames,
            silence_mode=self.config.silence_mode,
            silence_rms_threshold=self.config.silence_rms_threshold,
            speech_rms_threshold=self.config.speech_rms_threshold,
            silence_min_seconds=self.config.silence_min_seconds,
            silence_max_seconds=self.config.silence_max_seconds,
            silence_pad_frames=self.config.silence_pad_frames,
            text_temperature=self.config.text_temperature,
        )

    async def close(self) -> None:
        if self._load_task is not None and not self._load_task.done():
            self._load_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._load_task
        self._phase = RuntimePhase.CLOSED
