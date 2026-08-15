from __future__ import annotations

import pytest

import hibiki_mlx_realtime_api.config as config_module
from hibiki_mlx_realtime_api.config import RuntimeConfig


def test_runtime_config_defaults_to_mlx_codec() -> None:
    config = RuntimeConfig()

    assert config.host == "127.0.0.1"
    assert config.port == 8998
    assert config.codec == "mlx"
    assert config.model_repo == "huybik/hibiki-zero-3b-mlx-q4"
    assert config.queue_capacity == 16
    assert config.telemetry_interval_frames == 125
    assert config.silence_mode == "none"
    assert config.sampling_profile == "mlx-current"
    assert config.sampling_seed == 299792458


def test_sampling_profiles_resolve_exact_settings() -> None:
    current = config_module.resolve_sampling_profile("mlx-current")
    reference = config_module.resolve_sampling_profile("kyutai-reference")
    greedy = config_module.resolve_sampling_profile("greedy")
    cold = config_module.resolve_sampling_profile("historical-cold-0.2")

    assert (current.text_temperature, current.text_top_k) == (0.4, 25)
    assert (reference.text_temperature, reference.text_top_k) == (0.8, 250)
    assert (greedy.text_temperature, greedy.text_top_k) == (0.0, 250)
    assert (cold.text_temperature, cold.text_top_k) == (0.2, 25)
    for profile in (current, reference, greedy, cold):
        assert (profile.audio_temperature, profile.audio_top_k) == (0.8, 250)


def test_runtime_config_accepts_adaptive_reset_profile() -> None:
    config = RuntimeConfig(
        silence_mode="adaptive-reset",
        silence_rms_threshold=0.002,
        speech_rms_threshold=0.006,
        silence_min_seconds=4.0,
        silence_max_seconds=8.0,
        silence_pad_frames=12,
        sampling_profile="greedy",
        sampling_seed=123,
    )

    assert config.silence_mode == "adaptive-reset"
    assert config.silence_min_seconds == 4.0
    assert config.silence_max_seconds == 8.0
    assert config.silence_pad_frames == 12
    assert config.sampling_profile == "greedy"
    assert config.sampling_seed == 123


def test_runtime_config_rejects_unknown_codec() -> None:
    with pytest.raises(ValueError, match="codec"):
        RuntimeConfig(codec="cuda")


def test_runtime_config_rejects_unknown_silence_mode() -> None:
    with pytest.raises(ValueError, match="silence_mode"):
        RuntimeConfig(silence_mode="magic")


def test_runtime_config_rejects_unknown_sampling_profile() -> None:
    with pytest.raises(ValueError, match="sampling_profile"):
        RuntimeConfig(sampling_profile="magic")


def test_runtime_config_rejects_invalid_sampling_seed() -> None:
    with pytest.raises(ValueError, match="sampling_seed"):
        RuntimeConfig(sampling_seed=-1)
    with pytest.raises(ValueError, match="sampling_seed"):
        RuntimeConfig(sampling_seed=0x1_0000_0000)


def test_runtime_config_rejects_tiny_queue() -> None:
    with pytest.raises(ValueError, match="queue_capacity"):
        RuntimeConfig(queue_capacity=1)


def test_runtime_config_rejects_overlapping_silence_hysteresis() -> None:
    with pytest.raises(ValueError, match="speech_rms_threshold"):
        RuntimeConfig(silence_rms_threshold=0.01, speech_rms_threshold=0.005)


def test_runtime_config_rejects_invalid_silence_window() -> None:
    with pytest.raises(ValueError, match="silence_max_seconds"):
        RuntimeConfig(
            silence_mode="adaptive-reset",
            silence_min_seconds=9.0,
            silence_max_seconds=8.0,
        )
