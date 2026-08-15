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
- `GET /quality-lab.html` — experimental live lossless-input quality frontend.
- `GET /transport-replay.html` — deterministic same-WAV raw-PCM/official-Opus Stage 1B frontend.
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

Start one of the two silence-policy candidates still under evaluation. For Stage 1 transport work, use adaptive reset:

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

## Stage 1A: live browser input comparison

The existing official frontend is available at:

```text
http://127.0.0.1:8998/
```

The live raw-PCM reference is available at:

```text
http://127.0.0.1:8998/quality-lab.html
```

The quality lab requests a 24 kHz browser `AudioContext`, captures exactly 1920 samples per Hibiki frame, converts them to PCM16LE, and sends them directly as websocket kind `3`. It refuses to run if the browser does not actually create a 24 kHz input context.

The current bundled official frontend requests `echoCancellation=true`, `noiseSuppression=false`, `autoGainControl=true`, and mono input. The Quality Lab now mirrors those microphone constraints. Earlier live A/B observations were made while these constraints were not identical, so they remain useful observations of the tested chains but are not clean attribution evidence for the Opus codec itself.

The quality lab can download the exact submitted source as a mono 24 kHz PCM16 WAV. That file is the source specimen for deterministic Stage 1B.

## Stage 1B: exact same WAV through PCM and official Opus

Open:

```text
http://127.0.0.1:8998/transport-replay.html
```

This page does not use the microphone. It accepts only an uncompressed, mono, 24 kHz PCM16 WAV and computes SHA-256 over the exact WAV `data` bytes. It rejects incompatible files rather than resampling or normalizing them.

Run both transports against one unchanged server configuration:

### A — raw PCM16LE

The replay slices the exact source into 1920-sample / 80 ms frames, zero-pads the final partial frame, appends the configured deterministic silence tail, and sends websocket kind `0x03` at realtime cadence.

### B — official bundled Opus worker

The replay starts a fresh `/encoderWorker.min.js` instance and feeds the same source as 480-sample / 20 ms float frames. The worker uses the resolved 24 kHz frontend configuration:

```text
bufferLength       = 960
encoderSampleRate  = 24000
encoderFrameSize   = 20 ms
maxFramesPerPage   = 2
numberOfChannels   = 1
recordingGain      = 1
resampleQuality    = 3
encoderComplexity  = 0
encoderApplication = 2049
streamPages        = true
wavBitDepth        = 16
originalSampleRate = 24000
wavSampleRate      = 24000
```

`originalSampleRate` and `wavSampleRate` are deliberately 24 kHz in Stage 1B because the source specimen is already 24 kHz. This isolates Opus encoding/page framing instead of mixing browser device-rate resampling into the experiment.

Every generated Opus/Ogg page is sent as websocket kind `0x01`. Each PCM or Opus run receives a fresh Hibiki websocket/session plus fresh encoder/decoder worker state.

### Stage 1B artifacts

A successful run enables four downloads:

```text
source.wav
transcript.txt
translated.wav
manifest.json
```

`source.wav` is the exact selected file. Both manifests must have the same:

```text
source_pcm_sha256
source_samples
tail_seconds
url
```

before the translations are compared. The first captured corpus has source-PCM SHA-256:

```text
22f929d3860d39c3a0f5acb888a96e3748987a899aa74e48d93df5b59f66e8e3
```

Keep the replay tab active. Timing is scheduled against an absolute browser clock; if the tab falls materially behind realtime, the run fails instead of sending a burst that would invalidate the queue/RTF comparison.

Do not restart or reconfigure the server between A and B. Run PCM first, download all four artifacts, then choose Opus with the same WAV and run again.

Because current text sampling is stochastic, a single differing A/B pair is evidence, not a strong causal conclusion. If the transports differ materially, repeat both on the exact same WAV before deciding that Opus is systematically responsible.

Do not commit source recordings or generated experiment artifacts to the public repository.

## Headless raw-PCM replay

The existing Python replay remains useful as the canonical CLI raw reference:

```bash
HIBIKI_REPLAY_WAV=/absolute/path/to/hibiki-quality-source.wav \
  task hibiki-mlx:replay
```

It writes the same style of source/transcript/translated-audio/manifest result and paces raw PCM at realtime cadence.

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
realtime input_frames=125 lm_frames=124 rtf=0.27 encode_p50_ms=20.0 lm_p50_ms=20.5 decode_p50_ms=18.0 queues=0/0/0 overloads=0
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
