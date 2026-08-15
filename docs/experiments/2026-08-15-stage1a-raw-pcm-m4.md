# Stage 1A physical M4 result - raw PCM input

Date: 2026-08-15

Status: **RAW PCM wins lexical-fidelity A/B; transport root cause not fully isolated yet.**

## Runtime

```text
Mac Studio M4 Max
Rust Mimi encode/decode on CPU
Hibiki-Zero q4 LM on MLX/Metal
silence mode: adaptive-reset
text temperature: 0.4
```

## Experiment

Two browser input paths were compared against the same Hibiki backend configuration.

```text
A - official frontend

mic
 |
 v
browser microphone preprocessing
 |
 v
native browser AudioContext
 |
 v
opus-recorder resampling -> 24 kHz
 |
 v
Opus
 |
 v
server -> Rust Mimi -> Hibiki q4 MLX


B - Stage 1A quality lab used in the first physical test

mic
 |
 v
browser microphone preprocessing
 |
 v
AudioContext requested at 24 kHz
 |
 v
PCM16LE, websocket kind 0x03
 |
 v
server -> Rust Mimi -> Hibiki q4 MLX
```

The comparison therefore proves a difference in the **current complete input chains**. It does not yet prove that Opus compression alone is responsible, because the official path also contains the opus-recorder resampling path.

A second input confound was found during post-test code review. The official frontend requests:

```text
echoCancellation = true
noiseSuppression = true
autoGainControl = true
channelCount = 1
```

The first quality-lab test accidentally used `noiseSuppression = false`. The lab has now been corrected to match all four official microphone constraints. The original physical result remains evidence that the tested raw-PCM chain was better, but the next controlled live comparison must use the corrected constraints before attributing the gain to transport/resampling.

## User quality result

The raw PCM path was judged **clearly better for translation and transcript fidelity**.

The downloaded quality-lab source WAV was also judged clear and clean when listened to directly, with no obvious source-audio defect.

Decision:

```text
KEEP raw PCM as the canonical quality reference input.
DO NOT yet conclude "Opus codec is the sole cause".
RETEST with identical microphone preprocessing before Stage 1B attribution.
```

## New translated-audio symptom

With the first quality-lab implementation, translated audio was audible but noticeably weaker, and the generated/playback voice sometimes sounded unusual. This had not been observed with the official frontend.

A concrete playback confound was found after the test:

```text
official frontend output:
  AudioContext() at native device rate
  decoded 24 kHz Opus -> resampled to output AudioContext rate

initial quality lab output:
  same forced 24 kHz AudioContext used for input and output
```

The quality lab has now been changed to use two contexts:

```text
inputAudioContext  = AudioContext({sampleRate: 24000})
outputAudioContext = AudioContext()  # native output rate
```

Translated Opus playback now follows the same sample-rate strategy as the official frontend. This change still requires physical M4 validation; it is not yet accepted as the complete explanation of the weak/strange voice.

The deterministic replay harness also writes `translated.wav`, which will let us distinguish a browser playback problem from genuinely different target-audio tokens produced by Hibiki.

## Performance observation

Representative official Opus-path telemetry from the preceding M4 run:

```text
RTF          ~= 0.247 - 0.307
LM p50       ~= 19.6 - 20.5 ms
LM p95       ~= 21.6 - 22.3 ms
queues       ~= empty
```

Representative raw-PCM telemetry:

```text
RTF          ~= 0.546 - 0.722
LM p50       ~= 33 - 35 ms
LM p95       ~= 42 - 58 ms
queues       ~= empty, occasional depth 1
overloads    = 0
```

The PCM path remains realtime, but this slowdown is too large to ignore because source transport was intended to be the only inference-side variable.

The runtime telemetry has therefore been extended to report separate rolling percentiles for:

```text
Mimi encode p50/p95
Hibiki LM p50/p95
Mimi decode p50/p95
```

The next M4 run must use these stage timings before proposing a performance fix.

## Deterministic source replay

The quality-lab WAV is now the canonical source specimen for follow-up experiments.

```text
canonical 24 kHz PCM16 WAV
             |
             v
      1920 samples/frame
             |
             v
       pace at 80 ms
             |
             v
        WS kind 0x03
             |
             v
           Hibiki
        /          \
       v            v
 transcript.txt  translated.wav
       \            /
        v          v
         manifest.json
```

The replay records the SHA-256 of the exact PCM bytes and refuses stereo, non-24-kHz, compressed, or non-PCM16 input. The source input is deterministic; Hibiki output is not assumed deterministic yet while sampling remains stochastic.

## Separate long-session issue discovered in the same test period

A prior long Rust-Mimi session failed at the exact sequence boundary:

```text
ValueError: narrow invalid args start + len > dim_len:
[8192, 32], dim: 0, start: 8192, len:2
```

The Rust Mimi Python binding defaults `max_seq_len` to `8192`. Its source comments describe the transformer timeline as 25 Hz, making this approximately a five-minute state limit. After exhaustion, new sessions in the same process can fail immediately because the affected tokenizer state has already reached the boundary.

This is a distinct long-session lifecycle problem. Do not hide it by simply choosing a huge sequence length until memory/state behavior is characterized.

## Next decisions

```text
1. Retest quality-lab with the same microphone preprocessing as the official frontend.
2. Retest translated output after the native-rate playback fix.
3. Read encode / LM / decode p50+p95 from the new telemetry.
4. Replay the downloaded WAV through the deterministic PCM client.
5. Compare transcript.txt and translated.wav against the live result.
6. Build Stage 1B transport isolation using the same source WAV:

                  exact same PCM
                       |
             +---------+---------+
             |                   |
             v                   v
        raw 24 kHz          controlled Opus
             |                   |
             +---------+---------+
                       |
                       v
               same Mimi + Hibiki
```

Only after this split should Stage 2 sampling experiments begin.
