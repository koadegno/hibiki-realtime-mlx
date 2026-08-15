"""Runtime configuration for the Hibiki-Zero MLX service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CodecBackend = Literal["rust", "mlx"]
SilenceMode = Literal["none", "hold", "reset", "adaptive-reset"]


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Resolved process configuration for one Hibiki-Zero MLX server."""

    host: str = "127.0.0.1"
    port: int = 8998
    model_repo: str = "huybik/hibiki-zero-3b-mlx-q4"
    model_revision: str = "7704e4f8e6fef6432abc95d73fb9d659df470eb9"
    codec: CodecBackend = "mlx"
    queue_capacity: int = 16
    telemetry_interval_frames: int = 125
    max_session_minutes: float = 30.0
    silence_mode: SilenceMode = "none"
    silence_rms_threshold: float = 0.002
    speech_rms_threshold: float = 0.006
    silence_min_seconds: float = 4.0
    silence_max_seconds: float = 8.0
    silence_pad_frames: int = 12
    text_temperature: float = 0.4

    def __post_init__(self) -> None:
        if self.codec not in {"rust", "mlx"}:
            raise ValueError(f"unsupported codec: {self.codec}")
        if self.silence_mode not in {"none", "hold", "reset", "adaptive-reset"}:
            raise ValueError(f"unsupported silence_mode: {self.silence_mode}")
        if self.queue_capacity < 2:
            raise ValueError("queue_capacity must be >= 2")
        if self.port <= 0 or self.port > 65535:
            raise ValueError("port must be in 1..65535")
        if self.telemetry_interval_frames <= 0:
            raise ValueError("telemetry_interval_frames must be > 0")
        if self.max_session_minutes <= 0:
            raise ValueError("max_session_minutes must be > 0")
        if self.silence_rms_threshold < 0:
            raise ValueError("silence_rms_threshold must be >= 0")
        if self.speech_rms_threshold <= self.silence_rms_threshold:
            raise ValueError("speech_rms_threshold must be > silence_rms_threshold")
        if self.silence_min_seconds <= 0:
            raise ValueError("silence_min_seconds must be > 0")
        if self.silence_max_seconds < self.silence_min_seconds:
            raise ValueError("silence_max_seconds must be >= silence_min_seconds")
        if self.silence_pad_frames <= 0:
            raise ValueError("silence_pad_frames must be > 0")
        if self.text_temperature < 0:
            raise ValueError("text_temperature must be >= 0")
