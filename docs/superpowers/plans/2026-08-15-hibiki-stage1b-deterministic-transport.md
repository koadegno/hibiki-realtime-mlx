# Hibiki Stage 1B Deterministic Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic browser replay harness that sends one exact mono 24 kHz PCM16 WAV through either raw PCM kind `0x03` or the exact bundled official Opus encoder path kind `0x01`, while producing comparable transcript/audio/manifest artifacts.

**Architecture:** Keep the existing Python PCM replay intact as the headless raw reference. Add a browser-only Stage 1B page backed by a small pure-JavaScript core module that owns WAV validation, exact source framing, PCM16-to-float conversion, encoder constants, and manifest construction. The browser controller owns fresh websocket sessions, realtime pacing, the bundled `encoderWorker.min.js`, translated-Opus decoding, and artifact downloads. No model, server protocol, sampling, Mimi, or silence-policy code changes are required.

**Tech Stack:** Browser Web APIs, existing bundled Opus encoder/decoder workers, vanilla JavaScript, Node.js built-in `node:test`, pytest static/package tests, aiohttp existing static routing, GitHub Actions.

## Global Constraints

- Work only on `feat/hibiki-quality-roadmap`; do not modify `main`.
- Repository is public: never commit credentials, tokens, private repository URLs, recordings, model weights, or generated quality artifacts.
- GitHub Actions remain secret-free, model-free, and `permissions: contents: read`.
- Preserve `GET /`, `GET /quality-lab.html`, `GET /health`, `GET /ready`, and `WS /api/chat`.
- Preserve websocket kinds `0x01` Opus, `0x03` raw PCM input, `0x01` translated Opus output, `0x02` translated text output.
- Do not change Hibiki weights, q4 precision, Mimi, text sampling, or silence policy.
- Stage 1B source must be mono PCM16LE, 24,000 Hz, uncompressed WAV; reject anything else instead of converting it.
- Raw frames are 1,920 samples / 80 ms; official-Opus input frames are 480 samples / 20 ms.
- Opus mode must use the repository's existing `/encoderWorker.min.js` with `encoderSampleRate=24000`, `encoderFrameSize=20`, `maxFramesPerPage=2`, `numberOfChannels=1`, `recordingGain=1`, `resampleQuality=3`, `encoderComplexity=0`, `encoderApplication=2049`, `streamPages=true`, `originalSampleRate=24000`, `wavSampleRate=24000`.
- Each run opens a fresh websocket and fresh encoder/decoder worker state.
- Default deterministic silence tail remains 6.0 seconds.

---

### Task 1: Correct the live Quality Lab preprocessing contract

**Files:**
- Modify: `backend/hibiki_mlx_realtime_api/tests/test_quality_lab.py`
- Modify: `backend/hibiki_mlx_realtime_api/hibiki_mlx_realtime_api/static/quality-lab.js`

**Interfaces:**
- Consumes: actual embedded official frontend microphone constraints.
- Produces: Quality Lab constraint parity for `noiseSuppression`.

- [ ] **Step 1: Change the static assertion first**

Change the existing test from:

```python
assert "noiseSuppression: true" in script
```

to:

```python
assert "noiseSuppression: false" in script
```

- [ ] **Step 2: Run CI and verify RED**

Expected failure: `test_pcm_quality_lab_assets_are_packaged` because `quality-lab.js` still contains `noiseSuppression: true`.

- [ ] **Step 3: Apply the minimal harness correction**

In `quality-lab.js` use:

```javascript
noiseSuppression: false,
```

Do not change the other constraints.

- [ ] **Step 4: Verify the quality-lab test turns GREEN**

Expected: the changed static test passes and existing tests remain unaffected.

---

### Task 2: Add deterministic transport core with executable Node tests

**Files:**
- Create: `backend/hibiki_mlx_realtime_api/hibiki_mlx_realtime_api/static/transport-replay-core.js`
- Create: `backend/hibiki_mlx_realtime_api/tests/test_transport_replay_core.js`

**Interfaces:**
- Produces browser/Node API `HibikiTransportReplayCore` / `module.exports` with:
  - `SAMPLE_RATE`
  - `PCM_FRAME_SAMPLES`
  - `OPUS_FRAME_SAMPLES`
  - `OFFICIAL_ENCODER_CONFIG`
  - `parsePcm16Wav(bytes)`
  - `pcm16ToFloat32(pcmBytes)`
  - `createPcmFrames(pcmBytes, tailSeconds)`
  - `createOpusFrames(pcmBytes, tailSeconds)`
  - `buildManifest(options)`
  - `writePcm16Wav(floatSamples)`

- [ ] **Step 1: Write Node tests before the module exists**

Use built-in `node:test` and `node:assert/strict`. Construct WAV bytes in memory, including an extra odd-sized metadata chunk, and assert that `parsePcm16Wav()` returns exactly the `data` bytes rather than assuming a 44-byte header.

Test rejection for stereo, 48 kHz, 8-bit, and non-PCM format.

- [ ] **Step 2: Add exact framing tests**

For source length `1921` samples and `tailSeconds=0.16`, assert raw mode yields four 1,920-sample frames: two source frames including zero padding plus two silence frames.

For source length `481` samples and `tailSeconds=0.04`, assert Opus mode yields four 480-sample float frames: two source frames including zero padding plus two silence frames. Assert signed PCM mapping uses `int16 / 32768.0`, including `-32768 -> -1.0` and `32767 -> 32767/32768`.

- [ ] **Step 3: Lock the official encoder config in tests**

Assert exact values:

```javascript
{
  encoderSampleRate: 24000,
  encoderFrameSize: 20,
  maxFramesPerPage: 2,
  numberOfChannels: 1,
  recordingGain: 1,
  resampleQuality: 3,
  encoderComplexity: 0,
  encoderApplication: 2049,
  streamPages: true,
  originalSampleRate: 24000,
  wavSampleRate: 24000,
}
```

- [ ] **Step 4: Verify Node tests are RED**

Run in CI:

```bash
node --test tests/test_transport_replay_core.js
```

Expected: failure because the core module does not exist.

- [ ] **Step 5: Implement the pure core module**

Use a small UMD-style wrapper:

```javascript
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.HibikiTransportReplayCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  // pure functions only
});
```

RIFF parsing must walk chunks, honor even-byte chunk padding, require `fmt ` + `data`, and return a copied `Uint8Array` for exact PCM identity.

- [ ] **Step 6: Verify Node tests GREEN**

Run the Node suite and `node --check` on the core module.

---

### Task 3: Add Stage 1B browser replay and artifact capture

**Files:**
- Create: `backend/hibiki_mlx_realtime_api/hibiki_mlx_realtime_api/static/transport-replay.html`
- Create: `backend/hibiki_mlx_realtime_api/hibiki_mlx_realtime_api/static/transport-replay.js`
- Create: `backend/hibiki_mlx_realtime_api/tests/test_transport_replay.py`

**Interfaces:**
- Input: user-selected canonical WAV, transport `pcm` or `opus`, tail seconds.
- Network: fresh `WS /api/chat` for every run.
- Output: `source.wav`, `transcript.txt`, `translated.wav`, `manifest.json` downloads.

- [ ] **Step 1: Write packaging/static-contract tests first**

Assert all three Stage 1B JS/HTML assets exist, HTML loads `transport-replay-core.js` before `transport-replay.js`, exposes PCM/Opus selection and artifact buttons, and controller references `/encoderWorker.min.js`, `/decoderWorker.min.js`, `new WebSocket`, `crypto.subtle.digest`, and fresh-run cleanup.

- [ ] **Step 2: Verify pytest RED**

Expected: assets are missing.

- [ ] **Step 3: Implement the page**

Provide file input, transport selector, tail input default `6`, source SHA/sample/duration fields, server/run status, source/output counters, transcript, and four download buttons disabled until a successful run.

- [ ] **Step 4: Implement source identity and fresh-session lifecycle**

On file selection:

```javascript
const originalWav = new Uint8Array(await file.arrayBuffer());
const source = core.parsePcm16Wav(originalWav);
const digest = await crypto.subtle.digest("SHA-256", source.pcmBytes);
```

Retain `originalWav` unchanged for `source.wav` download. `runTransport()` must instantiate a fresh websocket and translated-audio decoder for every invocation and clean both in `finally`.

- [ ] **Step 5: Implement raw PCM realtime sender**

For each frame from `core.createPcmFrames`, send:

```javascript
const message = new Uint8Array(1 + frame.length);
message[0] = 3;
message.set(frame, 1);
ws.send(message);
```

Pace against an absolute `performance.now()` deadline, 80 ms per frame.

- [ ] **Step 6: Implement exact official-worker Opus sender**

Create `new Worker("/encoderWorker.min.js")`. Wait for `message === "ready"` after `command:"init"`, then request `getHeaderPages`. Send every emitted `message:"page"` payload to the server as kind `0x01`. Feed each 480-sample frame via:

```javascript
worker.postMessage({ command: "encode", buffers: [frame] });
```

at absolute 20 ms deadlines. At the end send `{command:"done"}` and wait for the worker's terminal `done` message so trailing pages are not lost.

- [ ] **Step 7: Capture translated text/audio**

Handle server output kinds strictly. Concatenate kind `0x02` text. Send kind `0x01` payloads into `/decoderWorker.min.js` configured for 24 kHz input/output and collect decoded float samples. Unknown kinds fail the run.

- [ ] **Step 8: Build completed manifest only after success**

Use `core.buildManifest()` with actual output counts and transport-specific protocol/config. A failed or interrupted run must not enable artifact downloads.

- [ ] **Step 9: Verify pytest + Node syntax GREEN**

Run pytest static tests and `node --check` for both new JS files.

---

### Task 4: Put Stage 1B into public CI and user docs

**Files:**
- Modify: `.github/workflows/hibiki-mlx-poc.yml`
- Modify: `backend/hibiki_mlx_realtime_api/README.md`
- Modify: `README.md`

**Interfaces:**
- CI stays Linux/model-free/secret-free.
- Physical M4 test remains manual.

- [ ] **Step 1: Extend browser CI**

Add:

```bash
node --check hibiki_mlx_realtime_api/static/transport-replay-core.js
node --check hibiki_mlx_realtime_api/static/transport-replay.js
node --test tests/test_transport_replay_core.js
```

Do not add secrets, permissions, model downloads, or network-dependent package installs.

- [ ] **Step 2: Document `/transport-replay.html`**

Document the exact M4 workflow: start `hibiki-mlx:serve:rust:adaptive-reset`, open `/transport-replay.html`, load the same WAV, run PCM then Opus without restarting/changing server configuration, download four artifacts after each run, compare matching hashes/manifests and server telemetry.

- [ ] **Step 3: Verify CI workflow still declares `permissions: contents: read`**

No new environment secret or credential may appear.

---

### Task 5: Update living experiment records without erasing history

**Files:**
- Modify: `docs/hibiki-quality-roadmap.md`
- Modify: `docs/hibiki-experiment-journal.md`
- Modify: `docs/experiments/2026-08-15-stage1a-raw-pcm-m4.md`

**Interfaces:**
- Records already observed facts separately from Stage 1B results that still require physical M4 execution.

- [ ] **Step 1: Correct current Stage 1 status**

Record that the deterministic corpus exists and Stage 1B tooling is implemented/awaiting physical A/B rather than saying Stage 1A is still waiting for its first WAV.

- [ ] **Step 2: Append latest M4 evidence**

Record latest raw-PCM run metrics approximately `RTF 0.25-0.27`, encode p50 about `20 ms`, LM p50 about `20 ms`, decode p50 about `18 ms`, queues empty, overloads zero. State that the previous PCM slowdown did not reproduce.

- [ ] **Step 3: Correct microphone-preprocessing interpretation**

Explicitly record actual bundled frontend `noiseSuppression=false` and that the latest lab run used `true`; therefore previous live browser A/B evidence remains useful but is not clean codec attribution.

- [ ] **Step 4: Record translated-energy observation carefully**

State that saved translated output did not show a simple low-amplitude failure and the perceived energy drop disappeared for direct-microphone speech, while external-speaker recapture remains a confound. Do not promote this to a model conclusion.

---

### Task 6: Final verification and branch handoff

**Files:** all changed files.

- [ ] **Step 1: Run complete GitHub Actions validation on final SHA**

Require lockfile check, Ruff, pytest, Node syntax, Node transport core tests, CLI smoke, and Taskfile parse to all pass.

- [ ] **Step 2: Inspect final diff for public-repo hygiene**

Search changed text for credentials/tokens/private repo references and confirm no WAV/model/generated artifact was added.

- [ ] **Step 3: Confirm server/model code is unchanged**

Stage 1B should touch static experiment assets, tests, CI, and docs only; `server.py`, `session.py`, `model.py`, `runtime.py`, lockfile, and model configuration should remain untouched.

- [ ] **Step 4: Hand off physical M4 acceptance commands**

Give the user the final branch SHA, pull/sync commands, server command, `/transport-replay.html` URL, and the exact PCM-then-Opus test sequence. Do not claim transport quality conclusions until that physical run is returned.
