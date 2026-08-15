# Hibiki-Zero MLX Realtime API

Apple-Silicon-native realtime speech-to-speech translation POC for Hibiki-Zero 3B. The language model is the pinned q4 MLX artifact and the browser surface stays compatible with the native Hibiki frontend/protocol.

## Runtime contract

- Python 3.12 managed by `uv`.
- Hibiki-Zero 3B q4 repository: `huybik/hibiki-zero-3b-mlx-q4`.
- Pinned model revision: `7704e4f8e6fef6432abc95d73fb9d659df470eb9`.
- Pinned Hibiki MLX runtime source: `huybik/hibiki-zero-mlx@888946ec1bdaa1decc123ce6d39a2ecddf0ae032`.
- 24 kHz mono audio, 1920 samples/frame, 12.5 Hz.
- MLX language model on the Apple GPU.
- `mlx` codec mode: Mimi encode + Hibiki LM + Mimi decode all use MLX/Metal.
- `rust` codec mode: `rustymimi` encode/decode run on CPU workers while the Hibiki LM remains on MLX/Metal.
- One active translation client for this POC.
- Bounded realtime queues. Input saturation closes the WebSocket instead of accumulating unbounded latency.

## HTTP/WebSocket surface

- `GET /` — vendored official Hibiki-Zero browser frontend.
- `GET /quality-lab.html` — experimental lossless-input quality frontend.
- `GET /health` — process liveness.
- `GET /ready` — q4 LM + selected Mimi codec loaded and warmed.
- `WS /api/chat` — Hibiki binary protocol plus one experimental input kind:
  - kind `0`: server handshake;
  - kind `1`: Opus audio input/output;
  - kind `2`: UTF-8 translated text;
  - kind `3`: experimental client input only, mono PCM16LE at 24 kHz.

The server output protocol remains unchanged: translated audio is still kind `1` Opus and translated text is kind `2` UTF-8.

## Install and repository checks

Run from the repository root:

```bash
task hibiki-mlx:sync
task hibiki-mlx:check
task hibiki-mlx:info
```

`hibiki-mlx:info` must finish with `MLX GPU compute: OK` before starting the model.

## Preferred Apple-Silicon runtime

The M4 Max measurements showed that Rust Mimi + MLX LM is faster than the all-MLX serial path because CPU Mimi can overlap the GPU language-model step.

Start one of the two silence-policy candidates still under evaluation. For the first Stage 1 transport test, use adaptive reset:

```bash
set -o pipefail
PYTHONUNBUFFERED=1 task hibiki-mlx:serve:rust:adaptive-reset 2>&1 \
  | tee ~/HIBIKI_REALTIME_API_V1/hibiki-quality-stage1.log
```

The other still-useful candidate is:

```bash
PYTHONUNBUFFERED=1 task hibiki-mlx:serve:rust:hold
```

In another terminal:

```bash
task hibiki-mlx:health
task hibiki-mlx:ready
```

Wait until `/ready` reports:

```json
{"status":"ready","phase":"ready","ready":true,"error":null}
```

## Stage 1: Opus versus raw PCM input

This experiment isolates browser input transport quality without changing Hibiki, Mimi, model weights, translated-audio output, or the selected silence policy.

### A — existing Opus frontend

Open:

```text
http://127.0.0.1:8998/
```

This is the existing official browser frontend and therefore the baseline capture/Opus path.

### B — raw PCM reference frontend

Open:

```text
http://127.0.0.1:8998/quality-lab.html
```

The quality lab requests a 24 kHz browser `AudioContext`, captures exactly 1920 samples per Hibiki frame, converts them to PCM16LE, and sends them directly as websocket kind `3`.

The page deliberately refuses to run if the browser does not actually create a 24 kHz `AudioContext`. A hidden or unknown resampling path would invalidate the A/B comparison.

The translated output still comes back through the existing Opus path so Stage 1 changes only the model input transport.

The quality lab can also download the exact submitted source as a mono 24 kHz PCM16 WAV. Keep that WAV: it becomes the deterministic replay input for the next part of the quality roadmap.

### Challenge phrases

Use the same phrases several times in both frontends without intentionally over-articulating:

```text
La jeune fille arrive demain.
Le jeune fils arrive demain.
La fille a six livres.
Le fils a dix livres.
Elle a cinq cents euros.
```

Record whether gender, number, or similar-word errors change between Opus and raw PCM. Also preserve the server log and the WAV downloaded from the quality lab.

## Benchmark A: all MLX/Metal

The all-MLX comparison path remains available:

```bash
set -o pipefail
PYTHONUNBUFFERED=1 task hibiki-mlx:serve:mlx 2>&1 \
  | tee ~/HIBIKI_REALTIME_API_V1/hibiki-mlx-all-metal.log
```

It is useful as an architecture comparison but is not the preferred M4 Max runtime after measured Rust+MLX results.

## Benchmark B: Rust Mimi + MLX LM baseline

The no-silence-intervention baseline remains available as:

```bash
set -o pipefail
PYTHONUNBUFFERED=1 task hibiki-mlx:serve:rust 2>&1 \
  | tee ~/HIBIKI_REALTIME_API_V1/hibiki-mlx-rust-mimi.log
```

## Performance logs

Realtime sessions report summaries such as:

```text
realtime input_frames=125 lm_frames=124 rtf=0.27 lm_p50_ms=20.5 lm_p95_ms=22.5 queues=0/0/0 overloads=0
```

Interpretation:

- `rtf < 1.0` is mandatory for sustained realtime.
- `rtf < 0.8` is the preferred acceptance target to leave headroom.
- `queues=0/0/0` or small transient values are healthy.
- Queues that remain near their capacity indicate one pipeline stage cannot keep up.
- `overloads` must remain `0`; saturation deliberately terminates the WebSocket rather than hiding growing lag.
- Smooth browser audio and a non-growing frontend lag are required in addition to numeric RTF.

## Quality roadmap and journal

Repository-level living documents:

```text
docs/hibiki-quality-roadmap.md
docs/hibiki-experiment-journal.md
```

The roadmap defines the next quality experiments. The journal records every meaningful result, including rejected approaches, so failed experiments are not repeated later.

## Failure capture

For a startup/load failure, preserve the complete server log and also run:

```bash
task hibiki-mlx:info
task hibiki-mlx:ready
```

For a realtime failure, preserve the log from `accepted Hibiki MLX connection` through the first exception, saturation event, or at least 20 seconds of translation telemetry.

Do not add CPU fallbacks to the language model. The point of this service is to validate an MLX/Metal Hibiki runtime on Apple Silicon.
