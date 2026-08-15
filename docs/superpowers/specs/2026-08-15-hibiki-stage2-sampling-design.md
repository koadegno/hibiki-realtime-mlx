# Hibiki Stage 2 — Decode-policy and sampling sweep

Date: 2026-08-15

## Goal

Stage 2 asks one narrow question: how much of Hibiki's lexical error/variance comes from the text decoding policy rather than the input transport or model representation?

Stage 1B produced a usable same-WAV PCM/Opus comparison. The controlled transcripts differ, but neither transport is consistently better across the challenge corpus. The remaining `1013 translation backend overloaded` replay issue is a separate robustness bug and must not block the quality roadmap. Stage 2 therefore uses **raw PCM only** and removes transport as an experimental variable.

## Chosen experiment

Reuse the canonical 24 kHz mono PCM16 WAV:

```text
source PCM SHA-256
22f929d3860d39c3a0f5acb888a96e3748987a899aa74e48d93df5b59f66e8e3
```

Run the exact same WAV through the same Rust Mimi + Hibiki q4 MLX + Rust Mimi pipeline with three named text-sampling profiles:

| Profile | text temperature | text top-k | purpose |
|---|---:|---:|---|
| `mlx-current` | 0.4 | 25 | current quality-roadmap baseline |
| `kyutai-reference` | 0.8 | 250 | reference-like wider sampling |
| `greedy` | 0.0 | ignored by sampler | deterministic argmax text decisions |

The audio sampler remains unchanged for every candidate profile:

```text
audio temperature = 0.8
audio top-k = 250
```

This keeps Stage 2 focused on text decoding rather than changing synthesized-audio sampling at the same time.

The rejected historical `0.2 / top-k 25` experiment remains reproducible as a named `historical-cold-0.2` profile, but it is not a Stage 2 candidate because its old run was confounded by overload.

## Why process-wide profiles

Three implementation approaches were considered.

### A. Process-wide named profile selected at server startup — chosen

The CLI and Taskfile select one named profile. Every WebSocket created by that server uses the same profile and seed.

Advantages:

- smallest protocol change;
- impossible for one client to silently request a different sampling policy;
- server logs, `/ready`, and downloaded manifests can all agree on one resolved configuration;
- restarting between candidate profiles gives each experiment a clean process-level model state.

The cost is one server restart between profiles, which is acceptable for this controlled experiment.

### B. Per-WebSocket query-string profile — rejected for Stage 2

This would make A/B runs faster, but adds a new runtime protocol surface and makes it easier to compare sessions whose process-global MLX random state differs in subtle ways.

### C. Browser control message selecting a profile — rejected

This would alter Hibiki's native binary protocol solely for an experiment and creates unnecessary coupling between the quality harness and inference configuration.

## Randomness and repeatability

The pinned MLX sampler uses `mx.random.categorical` for non-zero temperature and `mx.argmax` when `temp == 0`.

Add a process configuration field:

```text
sampling_seed = 299792458
```

For every fresh WebSocket session:

1. reset the model's streaming caches;
2. seed MLX sampling once with the configured seed;
3. construct the profile-specific generator;
4. do **not** reseed when `adaptive-reset` creates a new generator after a natural silence park.

This gives repeated sessions of the same profile the same initial random state while preserving continuous random-state evolution across silence resets inside one session.

`greedy` removes text-token sampling randomness because the pinned sampler uses argmax at temperature zero. Audio sampling remains stochastic internally, but because each fresh session is seeded identically, repeated greedy replays become the first direct test of whether the overall transcript is repeatable under fixed source audio.

## Configuration model

Replace the free-form quality-roadmap `text_temperature` knob with named sampling profiles.

`RuntimeConfig` gains:

```text
sampling_profile
sampling_seed
```

A single profile resolver owns the exact text/audio sampler settings. No other file hard-codes candidate top-k/temperature values.

The CLI exposes:

```text
--sampling-profile {mlx-current,kyutai-reference,greedy,historical-cold-0.2}
--sampling-seed <int>
```

The ordinary default remains `mlx-current` so existing behavior stays equivalent to text `temp=0.4/top-k=25`.

## Runtime data flow

```text
CLI / Taskfile
    |
    v
RuntimeConfig(sampling_profile, sampling_seed)
    |
    v
RuntimeManager
    |
    +--> startup log + /ready metadata
    |
    v
fresh RealtimeSession per WebSocket
    |
    +--> seed MLX once at session start
    |
    v
LoadedLanguageModel.new_generator(profile)
    |
    +--> text Sampler(profile temp/top-k)
    +--> audio Sampler(temp=0.8, top-k=250)
```

`adaptive-reset` may reset Hibiki generation after a long pause, but it keeps the selected profile and does not reset the RNG seed mid-session.

## Experiment metadata

`GET /ready` becomes additive and reports at least:

```json
{
  "sampling_profile": "greedy",
  "sampling_seed": 299792458
}
```

The deterministic browser replay reads this metadata before opening the WebSocket and records it in the manifest. Stage 2 artifact labels include the profile so outputs cannot be confused, for example:

```text
stage2-greedy-pcm-transcript.txt
stage2-greedy-pcm-translated.wav
stage2-greedy-pcm-manifest.json
```

Stage 2 uses `transport-replay.html` in **Raw PCM16LE** mode only. Opus remains available for Stage 1/debugging, but is outside the Stage 2 measurement.

## Taskfile ergonomics

The main adaptive-reset task accepts:

```text
HIBIKI_SAMPLING_PROFILE
HIBIKI_SAMPLING_SEED
```

and defaults to `mlx-current` / `299792458`.

Add explicit convenience tasks:

```text
hibiki-mlx:serve:rust:adaptive-reset:mlx-current
hibiki-mlx:serve:rust:adaptive-reset:kyutai-reference
hibiki-mlx:serve:rust:adaptive-reset:greedy
```

The historical cold task maps to `historical-cold-0.2` instead of using an unlabelled temperature override.

## Logging

At runtime readiness and at session creation, log the active:

```text
sampling_profile
sampling_seed
text temperature
audio temperature
text top-k
audio top-k
```

This is experiment metadata, not per-frame telemetry.

## Error handling

- unknown profile: configuration error before server start;
- negative/out-of-range seed: configuration error;
- replay cannot obtain ready runtime metadata: fail before sending the experiment instead of producing an unlabeled artifact;
- profile or seed changes require a server restart; there is no live mutation API in Stage 2.

## Tests

TDD coverage must include:

1. profile resolver returns the exact candidate settings;
2. invalid profile/seed is rejected;
3. CLI maps named profile/seed into `RuntimeConfig`;
4. generator receives profile-specific text sampler and unchanged audio sampler;
5. session seeds MLX exactly once at fresh session start and does not reseed on adaptive generation reset;
6. `/ready` exposes profile and seed;
7. deterministic replay manifest contains runtime sampling metadata and profile-specific label;
8. Taskfile exposes the three Stage 2 convenience tasks and no longer requires a free-form temperature for those tasks;
9. existing Rust/MLX, silence, PCM, Opus, frontend, CLI, and replay tests remain green.

## Physical M4 experiment

For each candidate profile:

1. start a clean server with the corresponding Stage 2 task;
2. confirm `/ready` reports the expected profile and seed;
3. replay the canonical WAV through **Raw PCM16LE** with six seconds of tail;
4. download manifest, transcript, and translated WAV;
5. record RTF, encode/LM/decode p50/p95, queues, overloads, parks/resumes/resets;
6. compare targeted semantics: girl/son, six/ten, numbers, names, omissions, repetitions, and post-silence recovery.

Run `greedy` at least twice. If the two greedy transcripts differ materially despite identical source/profile/seed, Stage 2 must first isolate the remaining randomness before interpreting profile quality.

## Decision gate

### Greedy is repeatable and materially better

Sampling is a meaningful contributor. Continue a controlled profile sweep around the best low-variance policy.

### Greedy is repeatable but repeats the same lexical mistakes

The error is likely upstream of text sampling or encoded in the model/logits themselves. Move to Stage 3 quantization fidelity using the same corpus.

### `mlx-current` or `kyutai-reference` is better than greedy

Keep stochastic sampling, but choose the best reproducible profile subject to realtime constraints.

### Repeated runs vary materially even with greedy + fixed seed

Do not attribute differences to profile quality yet. Identify the remaining stochastic state (most likely audio sampling / model feedback) with the minimum additional determinism needed for interpretation.

## Non-goals

Stage 2 does not:

- fix the deferred Opus replay `1013` robustness issue;
- change Rust Mimi;
- change q4 weights or quantization;
- change confidence/lookahead logic;
- change silence thresholds/policy;
- tune audio sampler parameters;
- begin fine-tuning.
