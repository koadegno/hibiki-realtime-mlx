# Stage 1B M4 run - Rust Mimi cross-session reset invalidation

Date: 2026-08-15

Status: **FIRST PCM/OPUS PAIR INVALID FOR FINAL ATTRIBUTION; RUST SESSION STATE FIXED IN CODE; CLEAN A/B RERUN REQUIRED.**

## Canonical source

```text
format              24 kHz mono PCM16 WAV
source samples      4,638,720
source duration     193.28 s
silence tail        6 s
source PCM SHA-256  22f929d3860d39c3a0f5acb888a96e3748987a899aa74e48d93df5b59f66e8e3
```

## Raw PCM run

The raw transport completed normally through websocket kind `0x03`.

```text
input frames        2491
output packets      2489
output samples      4,778,880
output duration     199.12 s
transcript chars    1359
```

The generated manifest and artifacts are complete and remain useful as a historical raw-PCM specimen.

Its transcript begins with the first two challenge utterances represented separately:

```text
The girl arrives tomorrow.
The young son arrives tomorrow.
```

The first translation omits `young`, but the first utterance itself is not missing.

### Why the RAW run must also be repeated for the final A/B

Before the first WebSocket, process startup created the process-wide Rust Mimi pair, ran one warmup frame through it, then called `reset()` and reused that same pair for the RAW session.

The upstream reset bug described below means that this warmup/reset sequence was not equivalent to constructing a fresh tokenizer. The contamination is tiny compared with the later Opus run, but Stage 1B is intended to be a strict transport-only experiment. Therefore this completed RAW artifact set is retained for evidence and debugging but is not the final clean comparator.

## First Opus run

The same canonical source was then replayed through the bundled Opus worker and websocket kind `0x01` without restarting the server process.

The run failed before source completion:

```text
input 20 ms frames fed   6422
Opus pages sent          3213
translated packets       1602
translated samples       3,075,840
browser error             websocket 1011: translation worker failed
```

`6422 * 20 ms = 128.44 s`, so the Opus result contains only a prefix of the source. Its partial transcript also omitted the first challenge utterance.

**Do not use that omission as evidence against Opus.** The backend was not starting this transport with fresh Rust Mimi state.

## Backend failure

The Rust Mimi encoder failed with:

```text
ValueError: narrow invalid args start + len > dim_len:
[8192, 32], dim: 0, start: 8192, len:2
```

The failure occurred after approximately:

```text
startup Mimi warmup       small non-zero advance
first RAW websocket      ~= 199 s
second Opus websocket    ~= 128 s
cumulative streamed time ~= 327 s
```

The Rust Mimi transformer operates internally at 25 Hz. The cumulative position is therefore close to the configured 8192-step positional limit:

```text
327 s * 25 Hz ~= 8175 transformer steps
```

The exact internal framing/warmup advance accounts for the remaining small difference and the failure is at the exact configured boundary, `start=8192`.

## Root cause in rustymimi 0.4.1

The pinned environment uses `rustymimi 0.4.1`.

In that release:

1. the Python `Tokenizer` defaults `max_seq_len` to `8192` and `reset()` delegates to `Mimi.reset_state()`;
2. Rust Mimi streaming self-attention owns both a KV cache and a separate absolute `pos` used to select RoPE rows;
3. `reset_kv_cache()` clears the KV cache but does not reset that `pos` counter;
4. `Mimi.reset_state()` in this release also does not reset its `downsample` streaming module.

Our runtime compounded the upstream behavior by constructing one process-wide Rust `CodecPair`, warming it once, then passing that same pair to every WebSocket. `RealtimeSession.start()` called `reset()`, but that reset was insufficient to make rustymimi 0.4.1 equivalent to a newly constructed tokenizer.

Consequences:

```text
startup warmup / websocket A advances Rust Mimi state
                  |
                  v
Tokenizer.reset() clears only part of that state
                  |
                  v
next websocket begins with constructor-owned state not fully fresh
                  |
                  v
cumulative RoPE position eventually reaches 8192
```

The first Opus transcript is therefore invalid for transport attribution from its first frame, not merely truncated at the final exception. The prior RAW session is much less affected, but its startup warmup contamination also prevents using the old PCM/Opus pair as a strict final A/B.

## Fix

The runtime now treats the Rust codec pair created during process startup as a readiness/warmup object only.

Each Rust WebSocket session constructs a fresh pair of Rust Mimi tokenizers:

```text
process-wide Hibiki MLX language model
              |
       websocket/session
              |
       +------+------+
       |             |
fresh Mimi enc   fresh Mimi dec
```

This resets RoPE position, KV state, convolution/downsample state, and every other constructor-owned streaming field by construction. The all-MLX codec path is unchanged.

### TDD evidence

Regression test:

```text
test_runtime_creates_fresh_rust_codec_pair_for_each_session
```

RED commit:

```text
b8ef1ec95c667758e8311aa06ca80e9e1714a02f
47 passed, 1 failed
expected three codec-pair constructions: warmup + session A + session B
observed one shared pair
```

GREEN implementation commit:

```text
491295104ba64ea14f1b24c1fe3d704fd7a5d4ea
```

GitHub Actions passed lockfile verification, Ruff, all Python tests, browser JavaScript syntax, deterministic Node transport tests, CLI smoke, and Taskfile parsing.

## Scope / remaining limitation

This fix addresses **startup/cross-WebSocket Rust Mimi state contamination**. It deliberately does not hide the separate `max_seq_len=8192` limitation by choosing an arbitrary huge Rust Mimi cache.

A single uninterrupted Rust Mimi session long enough to exhaust its configured positional table remains a separate long-session engineering problem. The current Stage 1B source plus tail is about 199 seconds and is below that single-session boundary when started from fresh Mimi state.

## Stage 1B decision

```text
first RAW specimen      KEEP AS HISTORICAL EVIDENCE; RERUN FOR CLEAN A/B
first Opus specimen     INVALIDATE
Stage 1 transport gate  NOT DECIDED
```

After updating and restarting the server, rerun **both** transports with the exact same WAV, SHA, adaptive-reset settings, and six-second tail. The new runtime guarantees a newly constructed Rust Mimi encoder/decoder pair for each WebSocket, so the rerun restores the intended invariant: transport is the only variable between the two arms.
