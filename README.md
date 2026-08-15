# Hibiki Realtime MLX

Realtime Hibiki-Zero speech-to-speech translation research on Apple Silicon using MLX/Metal.

This repository contains a standalone experimental runtime for Hibiki-Zero on Apple Silicon, together with the quality-analysis tooling used to improve realtime translation fidelity. It includes the MLX runtime, Rust Mimi pipeline, browser quality lab, deterministic replay tooling, a living quality roadmap, and a chronological experiment journal.

## Current architecture

```text
browser / deterministic WAV replay
              |
              v
      PCM16 24 kHz / Opus
              |
              v
      Rust Mimi encoder (CPU)
              |
              v
     Hibiki-Zero 3B q4 MLX
          Apple GPU / Metal
              |
              v
      Rust Mimi decoder (CPU)
              |
              v
      realtime audio + text
```

The Rust Mimi + MLX LM path is the current performance baseline on the physical M4 Max. The repository also retains the all-MLX path and rejected silence/sampling experiments for reproducibility.

## Quality research

The project is intentionally experiment-driven. Read these first:

- [`docs/hibiki-quality-roadmap.md`](docs/hibiki-quality-roadmap.md) — living roadmap with decision gates.
- [`docs/hibiki-experiment-journal.md`](docs/hibiki-experiment-journal.md) — chronological record of what was tested, what happened, and why an approach is kept or rejected.
- [`docs/experiments/2026-08-15-stage1a-raw-pcm-m4.md`](docs/experiments/2026-08-15-stage1a-raw-pcm-m4.md) — latest Stage 1A physical M4 findings.

Current Stage 1 finding: the first raw-PCM browser path produced clearly better lexical fidelity than the original Opus input chain, but the experiment also exposed microphone-preprocessing/resampling confounds. The repository now includes corrected live preprocessing plus deterministic WAV replay so future transport and decoding experiments can use the exact same source samples.

A separate long-session issue has also been identified in Rust Mimi around its default `max_seq_len=8192`; it is tracked in the experiment journal rather than hidden by an arbitrary large limit.

## Quick start on Apple Silicon

Requirements: macOS Apple Silicon, `uv`, and [Go Task](https://taskfile.dev/).

```bash
task hibiki-mlx:sync
task hibiki-mlx:check
task hibiki-mlx:info
```

Start the proven Rust Mimi + MLX pipeline:

```bash
task hibiki-mlx:serve:rust
```

Start the quality-roadmap adaptive candidate:

```bash
task hibiki-mlx:serve:rust:adaptive-reset
```

Then open:

```text
http://127.0.0.1:8998/                  # official Hibiki frontend
http://127.0.0.1:8998/quality-lab.html  # raw PCM quality lab
```

For deterministic replay of a quality-lab WAV:

```bash
HIBIKI_REPLAY_WAV=/absolute/path/to/hibiki-quality-source.wav \
  task hibiki-mlx:replay
```

The replay writes a transcript, translated WAV, and manifest with the SHA-256 of the exact source PCM.

## CI

GitHub Actions validates the lockfile, Ruff, unit tests, browser JavaScript syntax, CLI/replay imports, and Taskfile parsing. MLX/Metal performance and speech quality remain physical Apple-Silicon acceptance tests.

## Licensing and upstream work

This research builds on Kyutai's Hibiki/Moshi ecosystem and the Hibiki-Zero MLX work used by the pinned runtime. See `LICENSE-APACHE`, `LICENSE-MIT`, the service `pyproject.toml`, and the experiment documentation for pinned upstream revisions and provenance.
