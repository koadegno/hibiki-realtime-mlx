# Hibiki Stage 1B Deterministic Transport Design

Date: 2026-08-15

Status: design approved in conversation; implementation not started.

## Goal

Determine whether the current browser Opus transport materially changes Hibiki lexical decisions compared with lossless raw PCM by replaying the **exact same 24 kHz mono PCM16 source WAV** through both paths at realtime cadence.

Stage 1B must remove live-micro variability from the comparison. The only intended independent variable is the source transport presented to `WS /api/chat`:

```text
                     exact same source.wav
                              |
                    SHA-256 verified PCM
                              |
                +-------------+-------------+
                |                           |
                v                           v
        raw PCM16 transport          official Opus transport
          websocket kind 0x03          websocket kind 0x01
                |                           |
                +-------------+-------------+
                              |
                              v
                         same server
                              |
                              v
                    same Rust Mimi encoder
                              |
                              v
                    same Hibiki q4 MLX LM
                              |
                              v
                    same Rust Mimi decoder
                              |
                 +------------+------------+
                 |                         |
                 v                         v
           transcript.txt            translated.wav
```

This experiment is the final attribution gate before moving to sampling-policy experiments.

## Context and evidence

Stage 1A established that the tested raw-PCM browser chain produced better lexical fidelity than the tested official browser chain. It did **not** isolate Opus compression because multiple browser-path variables differed.

A later physical M4 run showed that raw PCM itself does not reproduce the earlier performance regression:

```text
RTF                    about 0.25-0.27
Mimi encode p50        about 20 ms
Hibiki LM p50          about 20 ms
Rust Mimi decode p50   about 18 ms
queues                  approximately 0/0/0
overloads               0
```

The earlier raw-PCM run with LM p50 around 33-35 ms is therefore treated as a non-reproduced anomaly, not an intrinsic cost of PCM transport.

The deterministic replay harness is already available in `hibiki_mlx_realtime_api/replay.py`. It validates an exact mono, 24 kHz, uncompressed PCM16 WAV, hashes the original PCM bytes, paces 1920-sample frames at 80 ms, records server text, decodes translated Opus, and writes a self-contained result directory.

The first deterministic source specimen has already been captured and replayed. Its exact source-PCM SHA-256 is:

```text
22f929d3860d39c3a0f5acb888a96e3748987a899aa74e48d93df5b59f66e8e3
```

A code review after the latest live test also found that the currently embedded official frontend and Quality Lab still differ in microphone preprocessing:

```text
official embedded frontend:
  echoCancellation = true
  noiseSuppression = false
  autoGainControl = true
  channelCount = 1

quality-lab.js:
  echoCancellation = true
  noiseSuppression = true
  autoGainControl = true
  channelCount = 1
```

Therefore the latest live A/B is still not a clean codec attribution test. Stage 1B deliberately bypasses microphone acquisition and preprocessing entirely by starting from the exact saved WAV.

## Non-goals

Stage 1B does **not**:

- change Hibiki weights;
- change q4 precision;
- change Mimi implementation;
- change text temperature, top-k, or sampling behavior;
- change silence policy;
- evaluate browser microphone preprocessing quality;
- evaluate native-device-rate to 24 kHz resampling quality;
- fix the separate Rust Mimi sequence-length / 8192 long-session issue;
- introduce a new production transport.

If Stage 1B shows that controlled Opus is effectively equivalent to raw PCM, the next transport experiment is a separate Stage 1C focused on browser resampling and/or microphone preprocessing. Stage 2 sampling must not be used to explain away an unresolved Stage 1 transport result.

## Options considered

### Option A - Python replay with `sphn.OpusStreamWriter`

This would be the smallest code change because the existing replay client is Python and `sphn` is already a dependency.

Rejected as the canonical Stage 1B comparison because the exposed stream-writer API does not let the experiment explicitly reproduce the encoder parameters used by the embedded frontend. An Opus result generated through a different encoder configuration would answer a weaker question: "does some Opus path differ from PCM?" rather than "does our current frontend Opus path differ from PCM?"

It may remain useful later as a secondary sanity check, but not as the Stage 1B reference.

### Option B - Deterministic browser replay using the bundled `encoderWorker.min.js`

Recommended.

Use the exact encoder worker already shipped with the official frontend and initialize it with the same observable configuration. Feed it samples decoded from the exact canonical 24 kHz PCM16 WAV rather than a microphone.

Advantages:

- uses the same bundled Opus implementation as the official frontend;
- keeps encoder settings explicit and reviewable;
- uses exactly the same source samples in PCM and Opus modes;
- requires no new native dependency;
- keeps CI secret-free and GPU/model-free;
- gives a direct path to replay artifacts that a human can inspect.

Trade-off: this replay is browser-driven instead of fully headless Python. That is acceptable for Stage 1B because exact transport fidelity is more important than CLI purity. The existing Python PCM replay remains the canonical headless raw reference.

### Option C - Add a new configurable libopus binding / native encoder

This could make the entire comparison headless and allow explicit encoder controls.

Rejected for now because it adds dependency/build complexity before we know that such infrastructure is needed. It would also create a second Opus implementation to validate against the browser worker. This violates the narrow Stage 1B objective.

## Recommended architecture

Add a small deterministic transport replay page, separate from the live Quality Lab.

Suggested route:

```text
/transport-replay.html
```

It has one purpose: replay a canonical WAV through either raw PCM or the official bundled Opus encoder, capture the result, and download the experiment artifacts.

### Shared source loader

The page accepts a local WAV file and validates before enabling a run:

```text
RIFF/WAVE
format = PCM
channels = 1
sample rate = 24000 Hz
bits per sample = 16
```

It extracts the exact PCM16 data bytes without normalization or resampling and computes SHA-256 over those bytes using `crypto.subtle.digest`.

The displayed hash is the experiment identity. The expected first specimen is:

```text
22f929d3860d39c3a0f5acb888a96e3748987a899aa74e48d93df5b59f66e8e3
```

The UI must not silently convert an incompatible WAV. Invalid input stops the experiment with a precise error.

### Raw PCM mode

Raw mode reproduces the existing deterministic Python replay semantics:

```text
source PCM16
    |
    v
1920 samples = 80 ms
    |
    v
0x03 + exact PCM16LE
    |
    v
WS /api/chat
```

The final partial source frame is zero-padded to 1920 samples. A deterministic silence tail is then appended using the same configurable duration as the current replay harness; default remains 6.0 seconds.

Frames are paced from a monotonic browser clock. The sender must compensate for scheduler jitter by scheduling against an absolute next deadline rather than sleeping a fixed 80 ms after each send.

### Controlled Opus mode

Opus mode uses the repository's existing `/encoderWorker.min.js` and mirrors the observable official frontend configuration:

```text
encoderSampleRate = 24000
encoderFrameSize = 20 ms
maxFramesPerPage = 2
numberOfChannels = 1
recordingGain = 1
resampleQuality = 3
encoderComplexity = 0
encoderApplication = 2049
streamPages = true
originalSampleRate = 24000
wavSampleRate = 24000
```

Because the canonical WAV is already exactly 24 kHz, this stage deliberately avoids the official frontend's native-device-rate -> 24 kHz resampling. That is a feature of the experimental design, not an omission: Stage 1B isolates codec/page-framing loss from resampling loss.

Feed exact float representations of the PCM16 source into the worker at 20 ms = 480-sample cadence. The conversion is deterministic:

```text
int16 sample / 32768.0 -> float input to encoder worker
```

The worker may emit header pages and audio pages according to its normal protocol. Every emitted Opus/Ogg page sent to the server is prefixed with websocket input kind `0x01`.

The same six-second deterministic silence tail is encoded through the same worker after source EOF so PCM and Opus runs receive equivalent source duration.

Each mode opens a **fresh websocket/session**. Never run PCM and Opus through the same Hibiki session state.

### Output capture

Both modes consume the existing server protocol unchanged:

```text
0x00   handshake
0x01   translated Opus audio
0x02   translated UTF-8 text
```

Translated Opus is decoded with the already bundled decoder worker to canonical 24 kHz mono PCM for artifact export.

Each run produces logically equivalent artifacts:

```text
source.wav
transcript.txt
translated.wav
manifest.json
```

The browser may package these as individual downloads in V1. A ZIP dependency is not required.

`source.wav` must be byte-for-byte the selected source file, not a regenerated approximation.

## Manifest contract

The browser manifest must contain enough information to prove the two runs used the same source and to reconstruct the experimental configuration:

```json
{
  "label": "stage1b-opus",
  "transport": "opus-official-worker",
  "protocol": "hibiki-native-opus-kind-1",
  "sample_rate": 24000,
  "source_pcm_sha256": "22f929d3860d39c3a0f5acb888a96e3748987a899aa74e48d93df5b59f66e8e3",
  "source_samples": 4638720,
  "source_seconds": 193.28,
  "tail_seconds": 6.0,
  "input_frame_samples": 480,
  "input_frame_seconds": 0.02,
  "encoder": {
    "implementation": "encoderWorker.min.js",
    "sample_rate": 24000,
    "frame_size_ms": 20,
    "max_frames_per_page": 2,
    "channels": 1,
    "recording_gain": 1,
    "resample_quality": 3,
    "complexity": 0,
    "application": 2049,
    "stream_pages": true
  },
  "output_samples": 0,
  "output_seconds": 0.0,
  "transcript_chars": 0
}
```

The PCM manifest uses:

```text
transport = raw-pcm16le
protocol  = hibiki-native-pcm16le-kind-3
input_frame_samples = 1920
input_frame_seconds = 0.08
encoder = null
```

`source_samples`, `source_seconds`, `output_samples`, `output_seconds`, and `transcript_chars` are populated from the actual run rather than copied from the example values.

## Fairness invariants

A Stage 1B comparison is valid only when all of the following are true:

- both manifests contain the same `source_pcm_sha256`;
- both use the same source sample count;
- both use the same tail duration;
- both use the same server URL;
- server/model configuration is unchanged between runs;
- silence mode is unchanged between runs;
- text sampling configuration is unchanged between runs;
- each transport gets a fresh Hibiki session;
- neither run reports input overload or a growing queue;
- the Opus run uses the exact bundled worker and configuration listed above.

The UI should display the source hash and active transport prominently so accidental mixed-source testing is obvious.

## Live Quality Lab correction

Separately from deterministic Stage 1B, change the Quality Lab microphone constraint to match the actual embedded official frontend:

```text
noiseSuppression: false
```

This is a correctness fix to the test harness, not evidence about which microphone setting is better.

The experiment journal must explicitly record that the earlier documentation incorrectly described the official frontend as using noise suppression. Historical observations remain valid observations of the tested chains, but they must not be used as clean codec attribution evidence.

## Error handling

The transport replay page fails closed for experimental-integrity errors:

- unsupported/corrupt WAV -> no run;
- wrong sample rate/channel count/bit depth/format -> no run;
- WebSocket handshake timeout -> abort run, no "successful" manifest;
- websocket closes before source + tail completes -> mark run failed;
- encoder worker error -> abort Opus run;
- unexpected server message kind -> abort run;
- translated Opus decode failure -> mark output artifact incomplete;
- source hash changes after loading -> impossible by design because the selected file bytes are retained for the run.

A failed run may expose diagnostic information, but must not produce a manifest that looks equivalent to a completed experiment.

## Testing strategy

### Unit/static tests in public CI

CI must remain model-free and secret-free.

Add tests that verify:

- the transport replay static page and JS are packaged/served;
- WAV parsing accepts canonical mono 24 kHz PCM16 and rejects incompatible formats;
- source data bytes used for SHA calculation are the exact WAV data chunk;
- PCM framing produces 1920-sample frames and deterministic tail duration;
- Opus feeder slices exact 480-sample / 20 ms frames and deterministic tail duration;
- the official encoder configuration constants exactly match the values in this design;
- a fresh websocket/session is created for each run;
- manifest transport/protocol fields differ correctly while source hash remains shared;
- browser JS passes `node --check`;
- existing Python replay tests remain green;
- existing websocket/server protocol tests remain green.

Do not download model weights or call Hugging Face in GitHub Actions. Do not add repository secrets. Keep workflow permissions at `contents: read` unless a future, separately reviewed workflow genuinely requires more.

### Physical M4 acceptance test

Run the same canonical WAV twice against the same `adaptive-reset` server process/configuration:

```text
A = raw PCM16
B = official-worker Opus
```

Save both artifact sets and the server log covering both sessions.

Verify before judging translation quality:

```text
same source_pcm_sha256
same source_samples
same tail_seconds
RTF < 1.0
no sustained queue growth
overloads = 0
```

Then compare:

- exact transcript differences;
- target meaning on known challenge phrases;
- gender/number/name errors;
- omissions and repetitions;
- translated audio continuity;
- server encode / LM / decode p50 and p95.

Because model sampling is currently stochastic, one PCM-vs-Opus pair is evidence but not sufficient for a strong claim. If the first comparison differs materially, repeat both transports on the same WAV enough times to determine whether the difference is systematic rather than one sampling draw. Stage 2 will later add explicit sampling profiles/seeding where technically possible.

## Decision gates

### Gate A - Controlled Opus is consistently worse than raw PCM

Conclusion:

```text
Opus codec/page-framing path is materially contributing to lexical loss.
```

Keep raw PCM as reference. Stage 1 continues by tuning/replacing the Opus transport against the deterministic corpus before touching model sampling.

### Gate B - Controlled Opus and raw PCM are effectively equivalent

Conclusion:

```text
Opus compression itself is unlikely to be the main cause of the live-browser quality gap.
```

Close Stage 1B and open Stage 1C focused on native-rate -> 24 kHz resampling and microphone preprocessing. Do not spend time tuning Opus complexity/bitrate without evidence.

### Gate C - Results vary too much across repeated runs

Conclusion:

```text
transport effect is smaller than current stochastic output variance.
```

Record Stage 1B as inconclusive and move only to the minimal decode-policy determinism work necessary to make the transport comparison interpretable. Do not claim either transport wins.

## Documentation updates included with implementation

Update these living records with facts from the already completed M4 run before recording new Stage 1B outcomes:

- `docs/hibiki-experiment-journal.md`
- `docs/hibiki-quality-roadmap.md`
- `docs/experiments/2026-08-15-stage1a-raw-pcm-m4.md`

Required corrections:

- deterministic replay corpus now exists;
- previous PCM performance slowdown was not reproduced;
- current raw PCM performance returned to the established realtime baseline;
- translated output WAV does not show a simple low-energy failure;
- user reports the perceived energy drop disappears with direct microphone speech and is associated with external-speaker capture;
- actual embedded official frontend uses `noiseSuppression = false`;
- Quality Lab used `noiseSuppression = true` in the latest test, so the live A/B remains confounded;
- Stage 1B is now the deterministic same-WAV transport attribution experiment.

Do not rewrite or delete old failed observations. Correct the interpretation and append the new evidence chronologically.

## Public-repository security constraints

This repository is public. Stage 1B must remain safe to fork and run without private infrastructure.

- Never commit API keys, SSH keys, access tokens, cookies, private repository URLs, private hostnames, user credentials, or `.env` contents.
- Never echo or interpolate secrets into GitHub Actions logs.
- Do not add required GitHub Actions secrets for Stage 1B.
- Test only deterministic/local/static behavior in CI.
- Keep generated user WAVs and quality-run artifacts out of Git unless the user deliberately chooses a sanitized public corpus in a later, separately reviewed change.
- Documentation must refer only to the public repository and public dependencies.

## Definition of done

Stage 1B implementation is ready for the physical experiment when:

1. `/transport-replay.html` loads from the packaged server.
2. It accepts only canonical 24 kHz mono PCM16 WAV input and displays its exact PCM SHA-256.
3. Raw mode sends the exact source over kind `0x03` at deterministic realtime cadence.
4. Opus mode sends the same source through the bundled `encoderWorker.min.js` with the official configuration and kind `0x01`.
5. Each run starts a fresh Hibiki session.
6. Both modes export comparable transcript, translated WAV, and manifest metadata.
7. Quality Lab microphone constraints match the actual embedded frontend.
8. Unit/static tests, Ruff, existing pytest suite, JS syntax checks, CLI smoke, lockfile check, and Taskfile parse pass.
9. CI uses no secrets and retains read-only contents permission.
10. Roadmap, journal, and Stage 1A experiment notes reflect the latest evidence without erasing historical failures.

Only after the physical same-WAV PCM-vs-Opus experiment is analyzed should the project decide whether to continue transport work, investigate resampling/preprocessing, or advance to Stage 2 sampling.
