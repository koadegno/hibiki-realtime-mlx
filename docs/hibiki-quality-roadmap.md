# Hibiki-Zero MLX Quality Roadmap

Status: living roadmap. Update this document after every experiment that changes our understanding.

## Goal

Reach a demo-quality realtime FR/ES/PT/DE -> EN Hibiki-Zero experience on Apple Silicon while preserving the performance already demonstrated on the M4 Max.

The current performance target is already met by the preferred runtime:

- Rust Mimi encode/decode on CPU.
- Hibiki-Zero q4 LM on MLX/Metal GPU.
- Pipelined execution.
- Sustained RTF well below 1.0 with no growing queue.

The remaining work is primarily translation fidelity, long-session stability, silence behavior, and reproducible evaluation.

## Non-negotiable constraints

- Keep the browser frontend available for every user-facing experiment.
- Keep `WS /api/chat`, `GET /`, `GET /health`, and `GET /ready` working.
- Keep the Rust Mimi + MLX LM pipeline as the performance baseline unless a measured alternative is better.
- Do not solve quality problems by assuming a specific microphone, room, speaker, or source-audio quality.
- Do not accept a quality improvement that creates sustained `RTF >= 1.0`, growing queues, or audible stutter.
- Prefer controlled A/B experiments over subjective one-off changes.
- Preserve the exact tested input whenever possible so multiple model configurations see the same audio.
- Treat `adaptive-reset` and `hold` as the two silence-policy candidates still worth exploring. `reset` and `adaptive-reset-cold` are currently rejected; see the experiment journal.

## System map

```text
                                  QUALITY INVESTIGATION POINTS

  human speech
      |
      v
+-------------+    +----------------+    +---------------+
| Browser mic | -> | INPUT TRANSPORT| -> | 24 kHz PCM    |
+-------------+    | Opus / raw PCM |    +-------+-------+
                   +----------------+            |
                         [Stage 1]                v
                                          +-------------+
                                          | Rust Mimi   |
                                          | encoder CPU |
                                          +------+------+ 
                                                 |
                                                 | source audio tokens
                                                 v
                    +----------------------------+----------------------------+
                    |                Hibiki-Zero q4 MLX LM                   |
                    |                                                        |
                    | sampling      precision       confidence       timing   |
                    | [Stage 2]     [Stage 3]       [Stage 4]        [Stage 5]|
                    +----------------------------+----------------------------+
                                                 |
                      text tokens + target audio tokens
                                                 |
                            +--------------------+--------------------+
                            |                                         |
                            v                                         v
                    +---------------+                         +---------------+
                    | transcript EN |                         | Rust Mimi     |
                    +---------------+                         | decoder CPU   |
                                                              +-------+-------+
                                                                      |
                                                                      v
                                                               Opus -> browser

                         Long pause / segmentation policy
                                      [Stage 6]
```

## Evaluation rule

Every stage must answer one narrow question. We only keep a change when the measurement supports its hypothesis.

For realtime tests, always record at least:

```text
RTF
LM p50 / p95
queue depths
input overload count
first-translation latency
subjective audio continuity
translation errors on known phrases
behavior after long silence
```

For quality tests, build and reuse a small challenge set containing pairs such as:

```text
la jeune fille / le jeune fils
la fille / le fils
elle arrive / il arrive
six / dix
cent / cinq cents
proper names
numbers and dates
short phrases after a long silence
```

The challenge set must progressively become a deterministic replay corpus rather than a new live recording on every run.

---

# Stage 1 - Isolate input transport loss

Status: **STAGE 1A READY FOR M4 USER TEST**

### Question

Does the browser's current Opus capture path destroy acoustic detail before Mimi sees it?

The official frontend currently uses Opus with low encoder complexity and no explicit high bitrate. We need a lossless reference before changing the model.

### Deliverable 1A - Raw PCM quality lab

Implemented on `feat/hibiki-quality-roadmap`:

- the production-like official frontend at `/` is unchanged;
- `/quality-lab.html` captures with a requested 24 kHz `AudioContext`;
- mono PCM16 is sent directly over the existing websocket using experimental binary kind `0x03`;
- the server accumulates raw PCM into the same 1920-sample Mimi frames used by the Opus path;
- translated text and translated Opus audio use the existing output protocol unchanged;
- the lab displays the actual browser sample rate and refuses to run if it is not exactly 24 kHz;
- the exact submitted PCM can be downloaded as a 24 kHz mono WAV for deterministic replay;
- malformed PCM16 input is rejected instead of silently decoded.

Server protocol extension:

```text
client -> server
0x01 + bytes        existing Opus input
0x03 + PCM16LE      experimental raw 24 kHz mono input

server -> client
0x00                existing handshake
0x01 + Opus         existing translated audio
0x02 + UTF-8        existing translated text
```

### Test

Use the same speaker, machine, room, silence policy, and phrases twice:

```text
A: http://127.0.0.1:8998/                  current Opus path
B: http://127.0.0.1:8998/quality-lab.html  raw PCM path
```

Recommended first runtime: `hibiki-mlx:serve:rust:adaptive-reset`. Repeat with `hold` only if the transport result is ambiguous.

### Decision gate

- If raw PCM is clearly more accurate, keep PCM as the reference and Stage 1B will tune Opus bitrate/complexity/resampling against it.
- If there is no meaningful difference, stop spending time on transport and move directly to Stage 2.
- If the quality lab cannot obtain a real 24 kHz browser AudioContext, add a stateful high-quality server resampler before drawing a conclusion.

### Deliverable 1B - Deterministic replay harness

Status: **WAITING FOR THE FIRST CAPTURED QUALITY-LAB WAV**

Use WAV captured by the quality lab to replay the exact same 24 kHz PCM through a session without relying on a live microphone. Store experiment metadata next to results.

The output of Stage 1 is a small reproducible source corpus and a conclusion about whether Opus is materially responsible for lexical mistakes.

---

# Stage 2 - Decode-policy parity and sampling sweep

Status: **PLANNED**

### Question

How much translation variance/error is introduced by our text decoding policy rather than the acoustic/model representation?

### Profiles

Run the exact same replay corpus through at least:

```text
mlx-port-current    text temp 0.4, top-k 25
kyutai-reference    text temp 0.8, top-k 250
greedy              text temp 0.0
controlled variants around the best profile
```

Do not reuse the previous `0.2` live failure as a quality result: that run entered `RTF > 1` and saturated the queue, so it confounded quality and execution behavior.

### Deliverable

Expose named sampling profiles rather than hand-edited constants, seed the sampler where deterministic behavior is possible, and emit the active sampling profile in startup/session logs.

### Decision gate

Keep the profile with the best repeatable lexical accuracy subject to:

```text
sustained RTF < 0.8 preferred
sustained RTF < 1.0 mandatory
no growing input queue
no new repetition/hallucination regression
```

---

# Stage 3 - Quantization fidelity

Status: **PLANNED**

### Question

Is q4 materially changing lexical decisions compared with higher precision?

### Experiments

Compare on identical Mimi source tokens:

```text
A: current q4 model
B: q8 model or locally converted q8 artifact
C: selective mixed precision
   - keep most transformer weights q4
   - keep text output head / text-critical layers at q8 or bf16
```

The first implementation should favor a minimal mixed-precision change rather than loading the whole 3B model in high precision if the sensitive layers can be isolated.

### Decision gate

Keep higher precision only if it fixes repeatable lexical errors at an acceptable memory/RTF cost.

---

# Stage 4 - Confidence instrumentation

Status: **PLANNED**

### Question

Can we detect moments where Hibiki is likely to make an acoustic/lexical mistake before it commits?

### Instrument without changing output first

Expose from the text logits:

```text
top-1 probability
top-2 probability
top1-top2 margin
entropy
PAD probability
EOS probability
selected token
```

Log only bounded summaries plus explicit low-confidence events so realtime performance is not destroyed by instrumentation.

Correlate those events with the challenge corpus errors.

### Decision gate

Only build adaptive waiting if confidence features actually separate good decisions from known errors better than chance.

---

# Stage 5 - Confidence-driven adaptive wait / lookahead

Status: **PLANNED**

### Question

Can Hibiki improve an ambiguous decision by observing a little more source audio instead of immediately committing?

### Approach

Use the model's native ability to delay output rather than globally adding fixed latency.

```text
normal confident frame
        |
        +--------------------------> emit normally

ambiguous frame
        |
        v
 wait 1 source frame (+80 ms)
        |
   confidence OK? ---- yes -------> commit
        |
        no
        v
 optionally wait another frame
```

Start with a maximum adaptive penalty of 1-3 frames. Do not jump directly to a permanent 1-2 second buffer.

Potential later experiment: keep two local hypotheses for a very short horizon and select after 160-320 ms of extra evidence. Only attempt this if cache-copy cost is manageable on MLX.

### Decision gate

Keep adaptive waiting only if challenge-set accuracy improves enough to justify the measured added latency.

---

# Stage 6 - Long silence and segment lifecycle

Status: **PARTIALLY EXPLORED**

### Current candidates

```text
adaptive-reset   worth keeping for experiments
hold             worth keeping for experiments
reset            rejected in current form
adaptive-reset-cold rejected
```

The final policy must satisfy two independent clocks:

```text
source/model semantic state       may pause/reset at a safe segment boundary
browser output audio clock        must continue continuously at 12.5 Hz
```

Do not reset state merely because the microphone RMS briefly drops. Hibiki can still have delayed translation content to emit.

Stage 6 resumes after Stages 1-5 because some apparent silence hallucinations may be amplified by bad lexical sampling or model precision.

---

# Stage 7 - Reproducible quality benchmark

Status: **PLANNED**

Promote the challenge corpus into a repeatable benchmark with:

- source WAV;
- source transcript;
- expected English translation(s);
- generated text;
- generated audio;
- runtime metrics;
- configuration manifest;
- human notes for meaning, gender, names, numbers, omissions, repetitions, and latency.

Automated metrics may be added, but they do not replace targeted semantic checks. A translation can score reasonably while changing `girl` to `boy`, which is unacceptable for our product goal.

---

# Stage 8 - Robustness/adaptation if inference changes are insufficient

Status: **PLANNED / LAST RESORT**

Only after the inference stack is characterized:

1. optional sidecar streaming ASR as a confidence/reranking signal, not as the default cascade;
2. vocabulary/context biasing where technically compatible;
3. LoRA or targeted fine-tuning on ambiguous phonetic pairs, accents, noise, codec augmentations, names, and domain vocabulary;
4. model-level robustness training if production requirements exceed the foundation checkpoint.

Do not begin training until the replay benchmark proves the error is genuinely in the model rather than transport, sampling, or q4 conversion.

---

# Working loop

```text
        +-----------------------+
        | user tests one stage  |
        +-----------+-----------+
                    |
                    v
        +-----------------------+
        | append journal entry  |
        +-----------+-----------+
                    |
                    v
        +-----------------------+
        | keep / reject / revise|
        +-----------+-----------+
                    |
                    v
        +-----------------------+
        | update roadmap        |
        +-----------+-----------+
                    |
                    v
             next narrow test
```

A failed experiment is still useful when it eliminates a hypothesis. Never silently delete failed approaches from history; mark them rejected in the journal and keep the baseline easy to recover.
