from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hibiki_mlx_realtime_api.config import RuntimeConfig
from hibiki_mlx_realtime_api.runtime import RuntimeManager, RuntimePhase


class FakeCodecPair:
    def __init__(self, calls: list[tuple[object, ...]]) -> None:
        self._calls = calls
        self.warmup_calls = 0

    def warmup(self) -> None:
        self.warmup_calls += 1
        self._calls.append(("warmup",))


@pytest.mark.asyncio
async def test_runtime_loads_model_and_codec_before_becoming_ready(tmp_path: Path) -> None:
    mimi_path = tmp_path / "mimi.safetensors"
    mimi_path.write_bytes(b"x")
    files = SimpleNamespace(mimi=mimi_path)
    loaded = SimpleNamespace(
        lm_config=SimpleNamespace(
            audio_codebooks=32,
            other_codebooks=16,
            generated_codebooks=16,
        )
    )
    calls: list[tuple[object, ...]] = []
    codec_pair = FakeCodecPair(calls)
    session = object()

    def resolve_files(config: RuntimeConfig) -> object:
        calls.append(("resolve", config.model_repo, config.model_revision))
        return files

    def load_model(resolved: object) -> object:
        calls.append(("model", resolved))
        return loaded

    def codec_factory(
        kind: str,
        *,
        mimi_path: Path,
        num_codebooks: int,
        checkpoint_codebooks: int,
    ) -> object:
        calls.append(("codec", kind, mimi_path, num_codebooks, checkpoint_codebooks))
        return codec_pair

    def session_factory(**kwargs: object) -> object:
        calls.append(("session", kwargs))
        return session

    config = RuntimeConfig(codec="mlx", max_session_minutes=2.0)
    manager = RuntimeManager(
        config,
        resolve_files=resolve_files,
        load_model=load_model,
        codec_factory=codec_factory,
        session_factory=session_factory,
    )

    await manager.initialize()

    assert manager.snapshot.phase is RuntimePhase.READY
    assert manager.snapshot.ready is True
    assert manager.snapshot.error is None
    assert calls[:4] == [
        (
            "resolve",
            "huybik/hibiki-zero-3b-mlx-q4",
            "7704e4f8e6fef6432abc95d73fb9d659df470eb9",
        ),
        ("model", files),
        ("codec", "mlx", mimi_path, 16, 32),
        ("warmup",),
    ]
    assert codec_pair.warmup_calls == 1

    created = manager.create_session()
    assert created is session
    session_kwargs = calls[-1][1]
    assert session_kwargs["loaded_model"] is loaded
    assert session_kwargs["codecs"] is codec_pair
    assert session_kwargs["queue_capacity"] == 16
    assert session_kwargs["max_steps"] == 1508
    assert session_kwargs["telemetry_interval_frames"] == 125


@pytest.mark.asyncio
async def test_runtime_captures_initialization_failure() -> None:
    def fail(_: RuntimeConfig) -> object:
        raise RuntimeError("boom")

    manager = RuntimeManager(RuntimeConfig(), resolve_files=fail)
    await manager.initialize()

    assert manager.snapshot.phase is RuntimePhase.FAILED
    assert manager.snapshot.ready is False
    assert manager.snapshot.error == "RuntimeError: boom"
    with pytest.raises(RuntimeError, match="not ready"):
        manager.create_session()


@pytest.mark.asyncio
async def test_runtime_background_start_is_idempotent() -> None:
    def fail(_: RuntimeConfig) -> object:
        raise RuntimeError("x")

    manager = RuntimeManager(RuntimeConfig(), resolve_files=fail)

    first = manager.start_background()
    second = manager.start_background()
    assert first is second

    await first
    await manager.close()
    assert manager.snapshot.phase is RuntimePhase.CLOSED
