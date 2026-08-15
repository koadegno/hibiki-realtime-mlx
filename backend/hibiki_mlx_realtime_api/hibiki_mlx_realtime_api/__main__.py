"""Command-line launcher for the Hibiki-Zero MLX realtime API."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from hibiki_mlx_realtime_api.config import RuntimeConfig


def build_parser() -> argparse.ArgumentParser:
    """Build the lightweight CLI without importing MLX or native audio modules."""
    defaults = RuntimeConfig()
    parser = argparse.ArgumentParser(description="Hibiki-Zero 3B q4 realtime translation on MLX")
    parser.add_argument("--host", default=defaults.host)
    parser.add_argument("--port", type=int, default=defaults.port)
    parser.add_argument("--model-repo", default=defaults.model_repo)
    parser.add_argument("--model-revision", default=defaults.model_revision)
    parser.add_argument("--codec", choices=("mlx", "rust"), default=defaults.codec)
    parser.add_argument("--queue-capacity", type=int, default=defaults.queue_capacity)
    parser.add_argument(
        "--telemetry-interval-frames",
        type=int,
        default=defaults.telemetry_interval_frames,
    )
    parser.add_argument("--max-session-minutes", type=float, default=defaults.max_session_minutes)
    parser.add_argument(
        "--silence-mode",
        choices=("none", "hold", "reset", "adaptive-reset"),
        default=defaults.silence_mode,
        help=(
            "Long-silence strategy: none=baseline, hold=freeze/resume same LM, "
            "reset=freeze then reset LM on resume, adaptive-reset=park after translated tail pads."
        ),
    )
    parser.add_argument(
        "--silence-rms-threshold",
        type=float,
        default=defaults.silence_rms_threshold,
        help="Frames at or below this RMS count toward source silence.",
    )
    parser.add_argument(
        "--speech-rms-threshold",
        type=float,
        default=defaults.speech_rms_threshold,
        help="A parked session resumes only when input RMS reaches this threshold.",
    )
    parser.add_argument(
        "--silence-min-seconds",
        type=float,
        default=defaults.silence_min_seconds,
        help="Minimum source silence before adaptive PAD-based parking is allowed.",
    )
    parser.add_argument(
        "--silence-max-seconds",
        type=float,
        default=defaults.silence_max_seconds,
        help="Hard source-silence cap before generation is parked.",
    )
    parser.add_argument(
        "--silence-pad-frames",
        type=int,
        default=defaults.silence_pad_frames,
        help="Consecutive translated text PAD frames required by adaptive-reset.",
    )
    parser.add_argument(
        "--text-temperature",
        type=float,
        default=defaults.text_temperature,
        help="Hibiki text sampling temperature; reference MLX default is 0.4.",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> RuntimeConfig:
    """Convert parsed CLI arguments into validated runtime configuration."""
    return RuntimeConfig(
        host=args.host,
        port=args.port,
        model_repo=args.model_repo,
        model_revision=args.model_revision,
        codec=args.codec,
        queue_capacity=args.queue_capacity,
        telemetry_interval_frames=args.telemetry_interval_frames,
        max_session_minutes=args.max_session_minutes,
        silence_mode=args.silence_mode,
        silence_rms_threshold=args.silence_rms_threshold,
        speech_rms_threshold=args.speech_rms_threshold,
        silence_min_seconds=args.silence_min_seconds,
        silence_max_seconds=args.silence_max_seconds,
        silence_pad_frames=args.silence_pad_frames,
        text_temperature=args.text_temperature,
    )


def packaged_static_dir() -> Path:
    """Return the vendored official Hibiki frontend directory."""
    return Path(__file__).resolve().with_name("static")


def main(argv: Sequence[str] | None = None) -> None:
    """Start aiohttp and initialize the MLX runtime in the background."""
    args = build_parser().parse_args(argv)
    config = config_from_args(args)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from aiohttp import web

    from hibiki_mlx_realtime_api.runtime import RuntimeManager
    from hibiki_mlx_realtime_api.server import create_app

    manager = RuntimeManager(config)
    app = create_app(manager, static_dir=packaged_static_dir())
    web.run_app(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
