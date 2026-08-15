# Hibiki-Zero Apple Silicon Experiment Journal

This is the chronological engineering record for the Hibiki-Zero realtime translation POC on Apple Silicon.

Rules:

- Record what was actually tested, not what we expected.
- Keep failed experiments.
- Record exact SHAs when known.
- Separate performance conclusions from translation-quality conclusions.
- Never promote a change to the baseline from a single subjective observation when a controlled comparison is possible.

---

## 2026-08-14 - Initial PyTorch/MPS compatibility target

### Goal

Run the official Hibiki-Zero 3B PyTorch model on the M4 Max with strict MPS, no CPU fallback, while preserving the official browser websocket protocol.

### Result

Compatibility succeeded:

```text
model load          OK on MPS
Mimi encode         OK
Temporal LM step    OK
Mimi decode         OK
websocket frontend  OK
translated text     OK
translated audio    OK
```

An early runtime bug was found at the NumPy boundary because realtime inference was running with autograd enabled:

```text
RuntimeError: Can't call numpy() on Tensor that requires grad.
```

### Fix

Align realtime inference with inference semantics (`torch.no_grad()`) and detach at the NumPy boundary.

### Decision

**KEEP** the fix. It was a correctness issue, not an optimization.

---

## 2026-08-15 - PyTorch/MPS eager performance failure

### Observation

The model translated while the speaker was still talking, proving streaming semantics worked, but lag grew continuously.

Representative performance-mode run:

```text
frames=125   rtf=1.720   backlog=7.24 s
frames=250   rtf=1.983   backlog=19.70 s
frames=500   rtf=2.168   backlog=46.77 s
frames=1000  rtf=2.257   backlog=100.57 s
frames=1375  rtf=2.802   backlog=198.22 s
```

MPS memory stayed roughly stable around:

```text
allocated ~= 6875 MB
driver    ~= 7528 MB
```

### Interpretation

The frontend's reported delay matched the compute backlog. The problem was not network latency; the backend was processing each 80 ms audio frame slower than realtime.

### Decision

**REJECT PyTorch/MPS eager as the production/demo runtime.** Keep it only as a compatibility/reference implementation.

---

## 2026-08-15 - `torch.compile` on MPS is much worse

### Observation

Compiled mode stabilized around:

```text
rtf ~= 7.78
p50 ~= 616 ms/frame
p95 ~= 680-703 ms/frame
```

Backlog grew by roughly 68 seconds every 125 processed frames.

### Decision

**REJECT `torch.compile`/MPS for this graph.** It is materially worse than eager and is not a warmup-only effect.

---

## 2026-08-15 - Move to MLX q4

### External reference

Bik Huy's Hibiki-Zero MLX port demonstrated the relevant Apple-Silicon architecture:

```text
Rust Mimi encoder CPU  ----+
                           |
                           v
                    Hibiki q4 MLX GPU
                           |
                           v
Rust Mimi decoder CPU  <---+
```

Key findings from that work:

- q4 MLX LM greatly reduces model footprint and improves Metal execution.
- Hibiki-Zero requires MLX fixes for hidden scale, GQA/`kv_repeat`, RoPE variant, and depformer output LayerNorm.
- Rust Mimi releases the GIL and can overlap CPU codec work with GPU LM work.
- Each thread needs its own Rust Mimi state.

### Our implementation

Created an isolated MLX realtime API while preserving:

```text
GET /
GET /health
GET /ready
WS  /api/chat
```

The official Hibiki browser frontend remained available.

---

## 2026-08-15 - All-MLX Metal concurrency crash

### Experiment

Run Mimi encode, Hibiki LM, and Mimi decode with MLX/Metal on separate concurrent threads.

### Failure

Native Metal abort:

```text
-[_MTLCommandBuffer addCompletedHandler:]:976:
failed assertion `Completed handler provided after commit call'
```

There was no useful Python exception because the process aborted natively.

### Root cause hypothesis

Concurrent MLX command-buffer ownership across the three GPU worker threads was unsafe for this streaming workload.

### Fix

The all-MLX mode was changed to a single ordered Metal worker:

```text
Mimi MLX encode -> Hibiki MLX LM -> Mimi MLX decode
```

### Decision

**KEEP** the serial all-MLX implementation as a valid comparison path.

**REJECT** concurrent multi-thread all-MLX execution.

---

## 2026-08-15 - Real M4 Max benchmark: performance solved

### Rust Mimi + MLX LM

Long run results:

```text
rtf ~= 0.254-0.266 initially
LM p50 ~= 20-21 ms
LM p95 ~= 22-23 ms
queues ~= 0/0/0
overloads = 0
execution = pipelined
```

A previous long run measured roughly `RTF ~= 0.26` over thousands of frames.

### All-MLX serial

Long run results:

```text
rtf ~= 0.445-0.456
LM p50 ~= 25-26 ms
LM p95 ~= 30 ms
queues = 0/0/0
overloads = 0
execution = serial_mlx
```

### User observation

Both were dramatically smoother than PyTorch/MPS. Audio stutter disappeared and translation latency became acceptable for a demo.

### Decision

**PRIMARY PERFORMANCE BASELINE: Rust Mimi + Hibiki q4 MLX.**

The all-MLX path remains useful but is slower end-to-end on this M4 Max because Mimi competes with the LM for Metal resources instead of overlapping CPU/GPU work.

---

## 2026-08-15 - Long silence hallucination / degradation

### Baseline symptom

After long silence, the continuous model can begin producing repetitions or invented translation. When speech later resumes, translation may no longer correspond cleanly to the new utterance.

Earlier PyTorch tests could not isolate this because huge compute backlog meant output corresponded to old source audio. MLX removed that confounder: the problem still exists qualitatively even when the backend is realtime.

### Reference observation

The MLX port's file-inference path pads with silence after source EOF only long enough to flush delayed translation, then stops after sustained PAD output because continuing through silence can hallucinate/repeat.

Important distinction discovered later:

```text
file EOF -> flush tail -> stop
```

is not equivalent to:

```text
natural realtime pause -> arbitrarily reset the streaming model
```

---

## 2026-08-15 - First silence parking attempt broke the output clock

### Experiment

Park the LM after the translated tail appears complete and stop advancing the pipeline during silence.

### Failure

The browser reported large delays even though backend RTF was good.

### Root cause

The browser audio clock continued, but the server stopped emitting output frames. The frontend interpreted the missing translated frames as model lag.

Repeated LM resets also damaged semantic continuity.

### Decision

**REJECT any silence strategy that stops the 12.5 Hz output timeline.**

New invariant:

```text
model semantic state may park/reset
browser output clock must continue
```

---

## 2026-08-15 - Silence experiment matrix

Tested commit baseline for this matrix: `d25cfceefe63804ec7ba12fe65ba8b2bc931117a`.

Four profiles were prepared.

### A - `adaptive-reset`

Observed behavior:

```text
rtf ~= 0.25-0.29
LM p50 ~= 20 ms
LM p95 ~= 22-23 ms
queues essentially empty
```

Examples:

```text
park at 6.48 s source silence after 12 PADs
resume on speech with generation reset
later park at hard 8.00 s cap
```

User assessment: among the two acceptable candidates. Worth further exploration.

### B - `hold`

Observed behavior:

```text
rtf ~= 0.26-0.28
LM p50 ~= 20-21 ms
LM p95 ~= 22-23 ms
queues essentially empty
```

The same LM state is preserved during the parked period and resumed when speech returns.

User assessment: among the two acceptable candidates. Worth further exploration.

### C - `reset`

Observed after a long pause/resume:

```text
LM p95 jumped to ~60 ms
input queue reached 7
later input queue saturated
websocket closed with overload protection
```

User assessment: worst qualitative candidate; after the pause it stopped translating usefully.

### Decision

**REJECT current `reset` design.**

### D - `adaptive-reset-cold`, text temperature 0.2

Observed:

```text
rtf=1.107
LM p50 ~= 60.9 ms
LM p95 ~= 65.7 ms
input queue=15
then queue saturation / websocket close
```

A second connection saturated immediately.

### Decision

**REJECT this exact cold profile.**

Do not infer that all alternative text sampling is bad: this run confounded sampling with a severe execution/performance regression.

### Current silence candidates

```text
KEEP FOR DEVELOPMENT:
- adaptive-reset
- hold

REJECT FOR NOW:
- reset
- adaptive-reset-cold 0.2
```

---

## 2026-08-15 - Translation fidelity becomes the main target

### User symptom

Translation can confuse acoustically similar words, e.g. a phrase intended as:

```text
la jeune fille
```

may become semantically wrong English such as:

```text
the young boy
```

This is not merely synthesized-audio quality. The semantic decision itself is wrong.

### New investigation axes

We identified four likely inference-stack contributors before considering fine-tuning:

```text
1. browser input transport / Opus loss
2. text decoding policy and sampling
3. q4 quantization / sensitive text layers
4. insufficient evidence at the moment of commitment
```

### Suspicious frontend detail

The current official frontend capture path uses Opus with:

```text
encoderSampleRate = 24000
encoderFrameSize = 20 ms
encoderComplexity = 0
resampleQuality = 3
no explicit high bitrate
```

This makes transport fidelity the first variable to isolate. We must not change the model until we know whether the source information is already being damaged before Mimi.

### Decision

Start the quality roadmap with a **lossless raw PCM browser reference path**, while keeping the existing Opus frontend unchanged for A/B comparison.

---

## 2026-08-15 - Stage 1A implemented: raw PCM transport reference

### Branch isolation

A new branch was created from the exact SHA used for the user's silence experiment matrix rather than from later unvalidated experiments:

```text
feat/hibiki-quality-roadmap
base: d25cfceefe63804ec7ba12fe65ba8b2bc931117a
```

This keeps the quality roadmap based on an M4-tested state and prevents discarded follow-up experiments from becoming accidental baseline behavior.

### TDD protocol result

A new websocket test first sent experimental input kind `0x03`. Before implementation the CI result was deliberately RED:

```text
30 passed, 1 failed
warning: unknown Hibiki websocket message kind: 3
```

After implementing PCM16LE decoding, the full CI turned GREEN.

### Implemented experiment

The server now accepts:

```text
0x01 + Opus bytes        existing path, unchanged
0x03 + PCM16LE bytes     Stage 1 raw reference
```

Raw PCM16 samples are converted directly to normalized float32, accumulated into the same 1920-sample frames, and passed to the same session. Odd-length/malformed PCM16 payloads close the websocket with code `1003` rather than being silently interpreted.

The output protocol remains unchanged:

```text
0x01 + Opus translated audio
0x02 + translated UTF-8 text
```

### Quality lab frontend

Added:

```text
http://127.0.0.1:8998/quality-lab.html
```

The page:

- requests `AudioContext({sampleRate: 24000})`;
- refuses to start if the actual context is not exactly 24 kHz;
- uses an AudioWorklet that emits exactly 1920 float samples per 80 ms frame;
- converts those samples to PCM16LE and sends kind `0x03`;
- plays existing translated Opus output;
- displays transcript and frame counters;
- includes the initial challenge phrases;
- can download the exact submitted source PCM as a mono 24 kHz WAV.

### Why the WAV matters

This is the bridge to Stage 1B:

```text
live mic
   |
   +----> raw PCM quality lab ----> Hibiki
   |
   +----> downloadable WAV
                    |
                    v
             deterministic replay
                    |
          +---------+---------+
          |         |         |
       sampling    q4/q8   confidence tests
```

Future stages can therefore use the same source signal instead of asking the user to pronounce a phrase again for every configuration.

### Scope discipline

Stage 1A deliberately does **not** change:

```text
Hibiki weights
q4 precision
Mimi codec implementation
text sampling
silence policy
translated-audio transport
```

Only the browser-to-server source transport changes. This gives the next M4 test a single interpretable variable.

### Verification status

Linux CI validates protocol conversion, malformed-input rejection, packaged lab assets, lockfile, Ruff, tests, CLI import, and Taskfile parsing. The physical M4/browser A/B comparison is still required before deciding whether Opus materially hurts lexical fidelity.

### Decision

**READY FOR M4 USER TEST.**

Use `adaptive-reset` first for both A and B so the only experimental variable is source transport.

---

## Next journal entry

Record the physical M4 A/B result:

```text
A = official `/` Opus input
B = `/quality-lab.html` raw PCM16 input
```

Keep the quality-lab source WAV and use it to implement Stage 1B deterministic replay.
