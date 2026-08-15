# Hibiki Quality Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lossless browser-to-server PCM reference path so the user can compare current Opus capture against raw 24 kHz PCM without changing Hibiki/Mimi/model behavior.

**Architecture:** Keep `/` and input kind `0x01` unchanged as the official Opus baseline. Extend `WS /api/chat` with experimental client input kind `0x03` carrying little-endian mono PCM16 at 24 kHz. Add a static `/quality-lab.html` plus an AudioWorklet that captures exactly 1920 samples per 80 ms Hibiki frame, sends PCM16 frames, receives the existing Opus/text output protocol, and can save the source PCM as WAV for later replay experiments.

**Tech Stack:** aiohttp, NumPy, browser Web Audio API / AudioWorklet, existing Opus decoder worker/output worklet, pytest, GitHub Actions.

## Global Constraints

- Do not modify model weights, Mimi behavior, text sampling, or silence-policy logic in Stage 1.
- Preserve `GET /`, `GET /health`, `GET /ready`, and `WS /api/chat`.
- Preserve existing `0x01` Opus input behavior byte-for-byte.
- Preserve server output kinds `0x00`, `0x01`, and `0x02`.
- Raw PCM reference input is `0x03 + PCM16LE`, mono, exactly 24,000 Hz.
- Reject malformed raw PCM payloads instead of silently interpreting them.
- Keep the Rust Mimi + MLX LM path as the user-test baseline.
- The official frontend at `/` remains unchanged.

---

### Task 1: Extend websocket input protocol with PCM16 reference frames

**Files:**
- Modify: `backend/hibiki_mlx_realtime_api/hibiki_mlx_realtime_api/server.py`
- Modify: `backend/hibiki_mlx_realtime_api/tests/test_server.py`

**Interfaces:**
- Consumes: websocket binary messages.
- Produces: direct `np.float32` frames passed to `session.submit_pcm()`.
- Protocol: kind `0x03`, followed by little-endian signed 16-bit mono PCM at 24 kHz.

- [ ] **Step 1: Write failing server tests**

Add tests proving that two raw PCM16 messages totaling one 1920-sample frame are accumulated and converted to float32 in `[-1, 1]`, while existing Opus input still passes unchanged.

- [ ] **Step 2: Run CI and verify RED**

Expected: the new PCM test fails because kind `0x03` is currently unknown.

- [ ] **Step 3: Implement minimal protocol support**

Refactor the receive loop around a small decoder boundary:

```python
OPUS_INPUT_KIND = 1
PCM16_INPUT_KIND = 3
PCM16_BYTES_PER_SAMPLE = 2


def _pcm16le_to_float32(payload: bytes) -> np.ndarray:
    if len(payload) % PCM16_BYTES_PER_SAMPLE:
        raise ValueError("PCM16 payload must contain complete 16-bit samples")
    pcm_i16 = np.frombuffer(payload, dtype="<i2")
    return np.asarray(pcm_i16, dtype=np.float32) / 32768.0
```

Kind `0x01` continues through `OpusStreamReader`; kind `0x03` goes through `_pcm16le_to_float32`.

- [ ] **Step 4: Run unit suite GREEN**

Expected: all existing tests plus the new PCM protocol tests pass.

---

### Task 2: Add the raw PCM browser quality lab

**Files:**
- Create: `backend/hibiki_mlx_realtime_api/hibiki_mlx_realtime_api/static/quality-lab.html`
- Create: `backend/hibiki_mlx_realtime_api/hibiki_mlx_realtime_api/static/quality-input-processor.js`
- Modify: `backend/hibiki_mlx_realtime_api/tests/test_server.py`

**Interfaces:**
- Browser input: `AudioContext({sampleRate: 24000})` -> AudioWorklet -> blocks of 1920 float samples.
- Websocket input: `0x03 + PCM16LE`.
- Websocket output: existing `0x01` Opus audio and `0x02` UTF-8 text.
- Browser output: existing `/decoderWorker.min.js` and `/audio-output-processor.js` assets.

- [ ] **Step 1: Write a failing static-route test**

The test static fixture includes a quality lab and asserts `GET /quality-lab.html` is served.

- [ ] **Step 2: Add the AudioWorklet**

The worklet must buffer arbitrary Web Audio render quanta and post exactly 1920 float samples to the page for each complete Hibiki frame.

- [ ] **Step 3: Add the quality lab HTML/JS**

The page must show:

```text
transport: RAW PCM16LE
requested sample rate: 24000 Hz
actual AudioContext sample rate: <value>
websocket state
input frame count
received audio frame count
transcript
```

If actual sample rate is not 24 kHz, the page must refuse to start the experiment and explain why rather than making a misleading comparison.

- [ ] **Step 4: Add WAV capture**

Keep a bounded in-memory copy of submitted PCM for the manual experiment and offer `Download source WAV` after stopping. Write a standard mono PCM16 24 kHz RIFF/WAV header in browser JavaScript.

- [ ] **Step 5: Verify static and websocket tests GREEN**

---

### Task 3: Make Stage 1 discoverable and CI-covered

**Files:**
- Modify: `.github/workflows/hibiki-mlx-poc.yml`
- Modify: `backend/hibiki_mlx_realtime_api/README.md`
- Modify: `Taskfile.yml`

**Interfaces:**
- CI should run on both `feat/hibiki-zero-mlx-realtime-api` and `feat/hibiki-quality-roadmap`.
- Task alias should make the recommended user-test server obvious without changing runtime behavior.

- [ ] **Step 1: Extend workflow branch trigger**

Add `feat/hibiki-quality-roadmap` to the existing unit workflow push branches.

- [ ] **Step 2: Add a user-facing task alias**

Add:

```text
hibiki-quality:serve:adaptive-reset
hibiki-quality:serve:hold
```

These delegate to the already-tested silence profiles and do not introduce a new model configuration.

- [ ] **Step 3: Document the A/B test**

README instructions:

```text
A /                  existing Opus transport
B /quality-lab.html  raw PCM16 transport
```

Use identical phrases and report which lexical errors change.

- [ ] **Step 4: Fresh full verification**

Run via GitHub Actions:

```text
uv lock --check
ruff check
pytest
CLI --help
Go Task parse
```

Expected: all green on the final SHA.

---

## Stage 1 user acceptance test

Use `adaptive-reset` first because it was one of the two user-acceptable silence profiles.

Speak the same short challenge list into `/` and `/quality-lab.html`:

```text
La jeune fille arrive demain.
Le jeune fils arrive demain.
La fille a six livres.
Le fils a dix livres.
Elle a cinq cents euros.
```

Repeat each phrase at least three times without intentionally over-articulating.

Record:

```text
Opus transcript/result
PCM transcript/result
whether gender/number/name errors changed
subjective latency
RTF / queues
```

Download the source WAV from the quality lab; that WAV becomes the seed input for Stage 1B deterministic replay.
