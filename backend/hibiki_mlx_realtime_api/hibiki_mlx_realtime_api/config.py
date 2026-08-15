"""Runtime configuration for the Hibiki-Zero MLX service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CodecBackend = Literal["rust", "mlx"]
SilenceMode = Literal["none", "hold", "reset", "adaptive-reset"]
SamplingProfile = Literal[
    "mlx-current",
    "kyutai-reference",
    "greedy",
    "historical-cold-0.2",
]


@dataclass(frozen=True, slots=True)
class SamplingSettings:
    """Resolved text/audio sampler settings for one named experiment profile."""

    text_temperature: float
    text_top_k: int
    audio_temperature: float = 0.8
    audio_top_k: int = 250


_SAMPLING_PROFILES: dict[SamplingProfile, SamplingSettings] = {
    "mlx-current": SamplingSettings(text_temperature=0.4, text_top_k=25),
    "kyutai-reference": SamplingSettings(text_temperature=0.8, text_top_k=250),
    "greedy": SamplingSettings(text_temperature=0.0, text_top_k=250),
    "historical-cold-0.2": SamplingSettings(text_temperature=0.2, text_top_k=25),
}


def resolve_sampling_profile(profile: SamplingProfile) -> SamplingSettings:
    """Return immutable sampler settings for one supported named profile."""
    try:
        return _SAMPLING_PROFILES[profile]
    except KeyError as exc:
        raise ValueError(f"unsupported sampling_profile: {profile}") from exc


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
    sampling_profile: SamplingProfile = "mlx-current"
    sampling_seed: int = 299792458

    def __post_init__(self) -> None:
        if self.codec not in {"rust", "mlx"}:
            raise ValueError(f"unsupported codec: {self.codec}")
        if self.silence_mode not in {"none", "hold", "reset", "adaptive-reset"}:
            raise ValueError(f"unsupported silence_mode: {self.silence_mode}")
        resolve_sampling_profile(self.sampling_profile)
        if not 0 <= self.sampling_seed <= 0xFFFFFFFF:
            raise ValueError("sampling_seed must be in 0..4294967295")
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
