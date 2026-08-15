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
Mimi encode p50 / p95
Hibiki LM p50 / p95
Mimi decode p50 / p95
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

The challenge set is now backed by a deterministic replay corpus rather than requiring a new live recording for every configuration.

---

# Stage 1 - Isolate input transport loss

Status: **STAGE 1B IMPLEMENTED — WAITING FOR PHYSICAL M4 SAME-WAV A/B**

### Question

Does the current browser Opus transport materially change Hibiki lexical decisions compared with lossless raw PCM before Mimi sees the signal?

### Stage 1A result - live raw PCM reference

The Stage 1A Quality Lab at `/quality-lab.html` added a lossless 24 kHz mono PCM16 input reference using websocket kind `0x03`, while `/` kept the existing official Opus path.

The physically tested raw-PCM chain was judged clearly better for translation/transcript fidelity than the physically tested official browser chain. However that live comparison is **not clean codec attribution evidence**:

- the official path includes native-device-rate capture plus opus-recorder resampling to 24 kHz;
- a later code review confirmed the bundled official frontend uses `noiseSuppression=false`;
- the Quality Lab used `noiseSuppression=true` during the latest relevant live comparison;
- the Quality Lab has now been corrected to `noiseSuppression=false` for future live checks.

Therefore:

```text
KEEP raw PCM as the quality reference.
KEEP the live A/B as evidence that the tested complete chains differ.
DO NOT claim Opus itself caused the difference from Stage 1A.
```

### Stage 1A performance update

An earlier raw-PCM run showed an apparent inference slowdown (`LM p50 ~= 33-35 ms`, `RTF ~= 0.55-0.72`). That slowdown did not reproduce in the later long raw-PCM run with per-stage timing telemetry.

Representative later values:

```text
RTF                 ~= 0.25 - 0.27
Mimi encode p50      ~= 20 ms
Hibiki LM p50        ~= 20 ms
Rust Mimi decode p50 ~= 18 ms
queues               ~= 0/0/0
overloads            = 0
```

Raw PCM transport is therefore not currently treated as intrinsically slower; the earlier slowdown remains recorded as a non-reproduced anomaly.

### Deterministic corpus exists

The first Quality Lab WAV has been captured and successfully replayed through the raw-PCM client.

```text
format                24 kHz mono PCM16 WAV
duration              about 193.28 s
source PCM SHA-256     22f929d3860d39c3a0f5acb888a96e3748987a899aa74e48d93df5b59f66e8e3
```

The replay preserves the exact source data identity and produces:

```text
source.wav
transcript.txt
translated.wav
manifest.json
```

The first replay correctly distinguished the initial `la jeune fille` / `le jeune fils` challenge pair while still exposing useful lexical/semantic mistakes over the longer corpus.

### Stage 1B - exact same WAV through two transports

`/transport-replay.html` is the attribution harness.

```text
                     exact same source.wav
                              |
                    SHA-256 verified PCM
                              |
                +-------------+-------------+
                |                           |
                v                           v
        raw PCM16 transport          bundled official Opus
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
```

Raw mode sends exact 1920-sample / 80 ms PCM frames. Opus mode feeds exact 480-sample / 20 ms frames into the repository's bundled `encoderWorker.min.js` using the resolved 24 kHz frontend worker configuration. Because the canonical WAV is already 24 kHz, Stage 1B intentionally avoids device-rate resampling and isolates Opus encoding/page framing.

Each transport opens a fresh websocket/session. Both use the same configurable deterministic tail, default six seconds. Browser pacing is absolute-clock based and fails closed if the tab falls materially behind instead of bursting queued frames.

### Stage 1B acceptance rule

Run PCM and Opus against the same unchanged `adaptive-reset` server configuration. Before comparing quality, require:

```text
same source_pcm_sha256
same source_samples
same tail_seconds
same server URL
RTF < 1.0
no sustained queue growth
overloads = 0
```

Then compare transcript differences, challenge semantics, names/numbers/gender, omissions/repetitions, translated audio continuity, and stage p50/p95 telemetry.

Current sampling is stochastic. One differing pair is evidence but not enough for a strong causal claim; materially different results should be repeated on the exact same WAV.

### Decision gates

**A — controlled Opus is consistently worse than raw PCM**

```text
Opus encoding/page framing materially contributes to lexical loss.
```

Keep raw PCM as reference and continue Stage 1 by tuning/replacing the Opus transport before changing model sampling.

**B — controlled Opus and raw PCM are effectively equivalent**

```text
Opus compression itself is unlikely to explain the live-browser quality gap.
```

Open Stage 1C focused narrowly on native-rate -> 24 kHz resampling and microphone preprocessing.

**C — repeated runs vary more than the transport difference**

```text
Transport effect is below current stochastic output variance.
```

Record Stage 1B as inconclusive and do only the minimum decode-policy determinism work needed to make the transport comparison interpretable.

Stage 2 does not begin until this gate is interpreted.

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
