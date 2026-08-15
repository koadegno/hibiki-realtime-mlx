# Stage 1B transport result and Stage 2 sampling implementation

Date: 2026-08-15

## Scope

This record closes the quality-attribution part of Stage 1 and records the implementation state of Stage 2. It deliberately does not claim any Stage 2 quality winner before a physical M4 replay.

## Canonical source

```text
format                24 kHz mono PCM16 WAV
source samples         4,638,720
source duration        193.28 s
source PCM SHA-256     22f929d3860d39c3a0f5acb888a96e3748987a899aa74e48d93df5b59f66e8e3
tail                   6 s
```

## Stage 1B accepted artifacts

The user supplied completed RAW PCM and bundled-official-Opus artifact sets.

Shared result geometry:

```text
output samples         4,778,880
output duration        199.12 s
output packets         2,489
```

PCM manifest:

```text
transport              raw-pcm16le
input frames           2,491
transcript chars       1,485
```

Opus manifest:

```text
transport              opus-official-worker
20 ms input frames     9,964
Opus pages total       4,985
header pages           2
audio pages            4,983
page cadence           40 ms
transcript chars       1,604
preencoded before WS   true
```

The controlled Opus path used the repository's bundled encoder worker at 24 kHz, with no source resampling in this experiment.

## Opening challenge result

Both accepted transcripts start with the same correct gender/relationship distinction:

```text
The girl arrives tomorrow.
The young son arrives tomorrow.
```

This directly rejects the earlier suspicion that the bundled Opus path necessarily destroys enough information to force the opening `girl`/`son` mistake.

## Longer-transcript interpretation

The two generated transcripts are not identical. Neither transport is consistently better over the complete sample.

Examples observed in the supplied transcripts:

- PCM says `He was married to a charming girl`, which is semantically wrong for the source passage;
- Opus says `He was married to a lovely, sweet woman`, which is better there;
- both runs contain later lexical/semantic corruption;
- names, numbers, road/soldier wording, omissions, and substitutions differ between runs rather than forming a stable PCM-wins pattern.

### Decision

```text
KEEP raw PCM as the controlled quality reference.
DO NOT optimize Opus as the next quality step.
DO NOT attribute the current lexical failures primarily to transport.
MOVE to text decode-policy/sampling attribution.
```

This is a Gate C interpretation: stochastic generation differences are large enough that the transport effect cannot be cleanly isolated as the dominant error source from the current pair.

## Deferred robustness issue

A separate Opus replay issue remains where a later websocket may close with:

```text
1013 translation backend overloaded
```

That issue is retained for later robustness/debugging work. It does not invalidate the completed accepted artifact pair above and is explicitly outside the Stage 2 quality experiment.

---

# Stage 2 implementation

## Named profiles

The process now resolves one named text-sampling profile:

```text
mlx-current          text temp 0.4   top-k 25
kyutai-reference     text temp 0.8   top-k 250
greedy               text temp 0.0   top-k 250 (argmax at temp 0)
historical-cold-0.2  text temp 0.2   top-k 25 (reproducibility only)
```

The audio sampler is fixed for every profile:

```text
audio temp 0.8
audio top-k 250
```

## Session seed policy

Default:

```text
sampling seed = 299792458
```

A fresh websocket session:

1. resets Hibiki streaming state;
2. calls `mx.random.seed(seed)` exactly once;
3. builds the selected profile generator;
4. does not reseed when `adaptive-reset` rebuilds the generator after a long silence.

The same rule applies to the pipelined Rust-Mimi path and the serial all-MLX path.

## Runtime metadata

`GET /ready` exposes:

```text
sampling_profile
sampling_seed
text_temperature
text_top_k
audio_temperature
audio_top_k
```

Runtime readiness, session creation, and connection acceptance also log sampling identity.

## Replay metadata

`/transport-replay.html` now reads `/ready` before opening the websocket. A run fails before artifact creation if a ready runtime or complete sampling identity cannot be resolved.

For raw PCM, labels are profile-specific:

```text
stage2-mlx-current-pcm
stage2-kyutai-reference-pcm
stage2-greedy-pcm
```

The manifest records the active profile, seed, and resolved text/audio sampler settings.

Stage 1 Opus replay remains available, but Stage 2 measurement uses RAW PCM only.

## User-facing tasks

```text
task hibiki-mlx:serve:rust:adaptive-reset:mlx-current
task hibiki-mlx:serve:rust:adaptive-reset:kyutai-reference
task hibiki-mlx:serve:rust:adaptive-reset:greedy
```

The old cold experiment is preserved as the named `historical-cold-0.2` profile instead of an unlabeled free-form temperature override.

## Required physical experiment

No Stage 2 quality conclusion has been made yet.

On the M4 Max, replay the exact canonical WAV through Raw PCM16LE with six seconds of tail for:

1. `mlx-current` once;
2. `kyutai-reference` once;
3. `greedy` twice.

The two greedy runs are especially important. If fixed-source, fixed-profile, fixed-seed greedy transcripts still differ materially, isolate the remaining autoregressive randomness before using profile differences as causal evidence.

For every run retain:

```text
manifest.json
transcript.txt
translated.wav
server log
```

and compare challenge semantics, names/numbers, omissions/repetitions, post-silence recovery, RTF, p50/p95 stage timings, queue depths, and overload count.
