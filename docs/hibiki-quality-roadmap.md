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
- Treat `adaptive-reset` and `hold` as the two silence-policy candidates still worth exploring. `reset` and the historical cold profile are currently rejected; see the experiment journal.

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

Status: **COMPLETED FOR QUALITY ATTRIBUTION — INPUT TRANSPORT IS NOT THE DOMINANT EXPLANATION**

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

### Deterministic corpus

The canonical Quality Lab WAV is:

```text
format                24 kHz mono PCM16 WAV
duration              193.28 s
source samples         4,638,720
source PCM SHA-256     22f929d3860d39c3a0f5acb888a96e3748987a899aa74e48d93df5b59f66e8e3
```

The replay preserves the exact source data identity and produces:

```text
source.wav
transcript.txt
translated.wav
manifest.json
```

### Stage 1B result - exact same WAV through PCM and Opus

The physical M4 Stage 1B experiment produced usable RAW PCM and bundled-official-Opus artifact sets from the same source identity.

Shared identity/result geometry:

```text
source_pcm_sha256     22f929d3860d39c3a0f5acb888a96e3748987a899aa74e48d93df5b59f66e8e3
source_samples        4,638,720
source_seconds        193.28
tail_seconds          6
output_samples        4,778,880
output_seconds        199.12
output_packets        2,489
```

The controlled Opus run used the bundled worker at 24 kHz with 20 ms frames, two frames per page, no source resampling, and pre-encoded pages paced at 40 ms/page before the server.

Both transcripts preserve the opening challenge distinction:

```text
PCM:
The girl arrives tomorrow.
The young son arrives tomorrow.

Opus:
The girl arrives tomorrow.
The young son arrives tomorrow.
```

The longer transcripts do differ, but neither transport wins consistently. Examples include:

- PCM and Opus both make lexical/semantic mistakes after the controlled opening phrases;
- Opus is better on some phrases (`a lovely, sweet woman` versus PCM's wrong `a charming girl`);
- PCM is better on other passages, including some soldier/road wording;
- omissions, substitutions, names, and numbers occur on both sides.

This pattern is compatible with stochastic decoding variance being at least as large as the transport effect in this corpus. The experiment does **not** prove that Opus is lossless or equivalent to PCM; it does show that Opus compression/page framing is not currently supported as the dominant cause of Hibiki's lexical mistakes.

### Deferred Stage 1 robustness issue

A separate replay robustness problem remains: some later Opus sessions can close with websocket `1013` / `translation backend overloaded`. That issue is retained for later debugging but is **not a Stage 2 quality blocker** because the accepted Stage 1B artifacts completed and the roadmap question is transport attribution, not multi-run harness robustness.

### Stage 1 decision

```text
DO NOT spend the next quality cycle tuning Opus.
DO NOT attribute girl/boy, numbers, names, or omission errors primarily to transport.
USE raw PCM as the controlled reference transport for the next stages.
MOVE to decode-policy/sampling attribution.
```

This is the roadmap's Gate C interpretation: observed output variance prevents a strong small-effect transport attribution, so the next experiment reduces decode-policy variance before changing precision or training.

---

# Stage 2 - Decode-policy parity and sampling sweep

Status: **IMPLEMENTED — WAITING FOR PHYSICAL M4 PROFILE REPLAYS**

### Question

How much translation variance/error is introduced by our text decoding policy rather than the acoustic/model representation?

### Controlled profiles

Run the exact same canonical WAV through **Raw PCM16LE only** with:

```text
mlx-current         text temp 0.4, top-k 25
kyutai-reference    text temp 0.8, top-k 250
greedy              text temp 0.0, argmax
```

The historical `0.2 / top-k 25` configuration is retained under the explicit name `historical-cold-0.2` for reproducibility only. Its old run entered `RTF > 1` and saturated the queue, so it is not a Stage 2 quality candidate.

For every candidate, keep the audio sampler fixed:

```text
audio temp 0.8
audio top-k 250
```

This stage changes text decoding only.

### Reproducibility controls

The service now has named sampling profiles and a default session seed:

```text
sampling_seed = 299792458
```

Each fresh websocket session:

1. resets Hibiki streaming caches;
2. seeds the MLX random sampler once;
3. creates the profile-specific generator;
4. keeps RNG evolution continuous when `adaptive-reset` creates a replacement generator after a natural silence park.

`greedy` uses text temperature zero, which maps to argmax in the pinned sampler. This removes text-token categorical sampling. Audio sampling remains stochastic, but the identical per-session seed makes repeated greedy replays the first direct repeatability check for the complete autoregressive system.

### Runtime identity

`GET /ready` now exposes the exact process sampling configuration:

```json
{
  "sampling_profile": "greedy",
  "sampling_seed": 299792458,
  "text_temperature": 0.0,
  "text_top_k": 250,
  "audio_temperature": 0.8,
  "audio_top_k": 250
}
```

`/transport-replay.html` reads this identity before opening the websocket. Raw-PCM Stage 2 manifests and filenames include the profile, for example:

```text
stage2-greedy-pcm-transcript.txt
stage2-greedy-pcm-translated.wav
stage2-greedy-pcm-manifest.json
```

A run is rejected before artifact creation if runtime sampling metadata cannot be resolved.

### M4 experiment commands

Use one clean server process per candidate profile:

```text
task hibiki-mlx:serve:rust:adaptive-reset:mlx-current
task hibiki-mlx:serve:rust:adaptive-reset:kyutai-reference
task hibiki-mlx:serve:rust:adaptive-reset:greedy
```

For every run:

```text
transport        Raw PCM16LE
source           canonical SHA above
tail             6 seconds
silence policy   adaptive-reset
codec            Rust Mimi
LM               Hibiki q4 MLX
```

Run `greedy` at least twice with the exact same server/profile/source. The two sessions are independently reseeded with the same seed.

### Decision gate

**Greedy is repeatable and materially better**

```text
Text sampling is a meaningful contributor to lexical mistakes.
```

Continue a controlled low-variance sampling sweep around the best policy.

**Greedy is repeatable but repeats the same lexical mistakes**

```text
The mistake is likely upstream of text sampling or represented directly in the model/logits.
```

Move to Stage 3 quantization fidelity using the same corpus.

**`mlx-current` or `kyutai-reference` is better than greedy**

Keep stochastic text sampling, but choose the best reproducible profile subject to realtime constraints.

**Repeated greedy runs differ materially despite the same fixed seed**

Do not judge profile quality yet. Isolate the remaining stochastic feedback (most likely audio sampling/model feedback) with the minimum additional determinism needed.

Every candidate remains subject to:

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
historical cold sampling/reset experiment rejected
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
