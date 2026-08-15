from __future__ import annotations

from hibiki_mlx_realtime_api.__main__ import build_parser, config_from_args, packaged_static_dir


def test_cli_defaults_to_pinned_mlx_runtime() -> None:
    config = config_from_args(build_parser().parse_args([]))
    assert config.codec == "mlx"
    assert config.model_revision == "7704e4f8e6fef6432abc95d73fb9d659df470eb9"
    assert config.queue_capacity == 16
    assert config.silence_mode == "none"
    assert config.text_temperature == 0.4


def test_cli_allows_rust_codec_benchmark() -> None:
    config = config_from_args(build_parser().parse_args(["--codec", "rust"]))
    assert config.codec == "rust"


def test_cli_exposes_adaptive_silence_reset_controls() -> None:
    config = config_from_args(
        build_parser().parse_args(
            [
                "--silence-mode",
                "adaptive-reset",
                "--silence-rms-threshold",
                "0.002",
                "--speech-rms-threshold",
                "0.006",
                "--silence-min-seconds",
                "4",
                "--silence-max-seconds",
                "8",
                "--silence-pad-frames",
                "12",
                "--text-temperature",
                "0.2",
            ]
        )
    )

    assert config.silence_mode == "adaptive-reset"
    assert config.silence_rms_threshold == 0.002
    assert config.speech_rms_threshold == 0.006
    assert config.silence_min_seconds == 4.0
    assert config.silence_max_seconds == 8.0
    assert config.silence_pad_frames == 12
    assert config.text_temperature == 0.2


def test_official_frontend_is_packaged_with_service() -> None:
    static = packaged_static_dir()
    assert (static / "index.html").is_file()
    assert (static / "encoderWorker.min.js").is_file()
