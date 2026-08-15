# Stage 1A physical M4 result - raw PCM input

Date: 2026-08-15

Status: **RAW PCM won the tested live-chain lexical A/B; codec attribution remains unresolved.**

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


B - Stage 1A quality lab

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

The comparison proves a difference in the **complete input chains that were physically tested**. It does not prove that Opus compression alone is responsible because the official path also contains device-rate capture/resampling and the two live tests did not use identical microphone-processing settings.

## Correction to the microphone-preprocessing record

A later review of the actual vendored official frontend showed that an earlier interpretation in this document was reversed.

The embedded official frontend requests:

```text
echoCancellation = true
noiseSuppression = false
autoGainControl = true
channelCount = 1
```

The Quality Lab used `noiseSuppression = true` during the latest relevant live test. It has now been corrected to `false` so future live checks match the actual bundled frontend.

This correction does not erase the original observation that the tested raw-PCM chain sounded/transcribed better. It means that observation **must not be used as clean evidence that Opus itself caused the lexical difference**. Stage 1B therefore bypasses microphone acquisition entirely and starts from one exact saved WAV.

## User quality result

The tested raw PCM path was judged **clearly better for translation and transcript fidelity** than the tested official browser chain.

The downloaded Quality Lab source WAV was also judged clear and clean when listened to directly, with no obvious source-audio defect.

Decision:

```text
KEEP raw PCM as the canonical quality reference input.
DO NOT conclude "Opus codec is the sole cause" from the live A/B.
USE deterministic same-WAV Stage 1B for transport attribution.
```

## Translated-audio energy observation

During the first Quality Lab tests, translated audio was perceived as weaker and the generated/playback voice sometimes sounded unusual.

A concrete playback confound was found after the first test:

```text
official frontend output:
  AudioContext() at native device rate
  decoded 24 kHz Opus -> resampled to output AudioContext rate

initial quality lab output:
  same forced 24 kHz AudioContext used for input and output
```

The Quality Lab was changed to use two contexts:

```text
inputAudioContext  = AudioContext({sampleRate: 24000})
outputAudioContext = AudioContext()  # native output rate
```

The later deterministic translated WAV does **not** support a simple "Hibiki output amplitude became too low" explanation. The user also reports that the perceived energy drop disappears when speaking directly into the microphone and is mainly observed when source speech is played through external speakers and recaptured by the microphone.

External-speaker recapture, echo cancellation, AGC, room acoustics, and noise suppression are therefore still confounds. Do not modify the model or Mimi gain based on this symptom alone.

## Performance observation

An earlier raw-PCM live run had shown:

```text
RTF          ~= 0.546 - 0.722
LM p50       ~= 33 - 35 ms
LM p95       ~= 42 - 58 ms
queues       ~= empty, occasional depth 1
overloads    = 0
```

That slowdown **did not reproduce** in the later long raw-PCM run with stage timing telemetry. Representative later values were:

```text
RTF               ~= 0.25 - 0.27
Mimi encode p50    ~= 20 ms
Hibiki LM p50      ~= 20 ms
Rust Mimi decode p50 ~= 18 ms
queues             ~= 0/0/0
overloads          = 0
```

The session also survived repeated adaptive-reset park/resume cycles without queue growth. Therefore raw PCM transport itself is no longer considered an intrinsic explanation for the earlier LM slowdown; the earlier performance anomaly remains recorded but is not reproduced.

## Deterministic source replay result

The first Quality Lab WAV has now been captured and successfully replayed. It is the canonical source specimen for follow-up experiments.

```text
source format       24 kHz mono PCM16 WAV
source duration     about 193.28 s
source PCM SHA-256  22f929d3860d39c3a0f5acb888a96e3748987a899aa74e48d93df5b59f66e8e3
```

The deterministic raw replay pipeline is:

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

The replay records the SHA-256 of the exact PCM data bytes and refuses stereo, non-24-kHz, compressed, or non-PCM16 input. The source input is deterministic; Hibiki output is not assumed deterministic yet while sampling remains stochastic.

The first replay correctly distinguished the initial challenge pair corresponding to `la jeune fille` and `le jeune fils`, while the longer corpus still contains lexical/semantic errors useful for later controlled experiments.

## Stage 1B transport isolation

Stage 1B tooling now reuses the **same saved WAV** for both transports:

```text
                  exact same source PCM
                         |
               +---------+---------+
               |                   |
               v                   v
          raw 24 kHz          bundled official
          kind 0x03            Opus worker
                                   |
                                kind 0x01
               |                   |
               +---------+---------+
                         |
                         v
                 same Mimi + Hibiki
```

The Opus side uses the same bundled `encoderWorker.min.js` as the official frontend but receives an already-24-kHz source, so Stage 1B isolates codec/page-framing loss rather than device-rate resampling. Each transport gets a fresh websocket/session.

Stage 1B is implemented and awaits the physical M4 same-WAV A/B before any transport-quality conclusion is made.

## Separate long-session issue discovered in the same test period

A prior long Rust-Mimi session failed at the exact sequence boundary:

```text
ValueError: narrow invalid args start + len > dim_len:
[8192, 32], dim: 0, start: 8192, len:2
```

The Rust Mimi Python binding defaults `max_seq_len` to `8192`. Its source comments describe the transformer timeline as 25 Hz, making this approximately a five-minute state limit. After exhaustion, new sessions in the same process can fail immediately because the affected tokenizer state has already reached the boundary.

This is a distinct long-session lifecycle problem. Do not hide it by simply choosing a huge sequence length until memory/state behavior is characterized.

## Next decision

Run the physical Stage 1B pair using the exact same source hash and unchanged `adaptive-reset` server configuration.

```text
A = raw PCM16 kind 0x03
B = bundled official-worker Opus kind 0x01
```

Before comparing quality, verify identical source hash/sample count/tail and healthy realtime telemetry. Because text sampling is currently stochastic, repeat materially different results before attributing them to transport.

Only after this deterministic split is interpreted should Stage 2 sampling experiments begin.
