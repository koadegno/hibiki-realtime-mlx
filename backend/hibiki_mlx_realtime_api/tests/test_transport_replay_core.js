"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const core = require("../hibiki_mlx_realtime_api/static/transport-replay-core.js");

function ascii(value) {
  return Buffer.from(value, "ascii");
}

function u16(value) {
  const buffer = Buffer.alloc(2);
  buffer.writeUInt16LE(value);
  return buffer;
}

function u32(value) {
  const buffer = Buffer.alloc(4);
  buffer.writeUInt32LE(value);
  return buffer;
}

function pcm16(samples) {
  const buffer = Buffer.alloc(samples.length * 2);
  samples.forEach((sample, index) => buffer.writeInt16LE(sample, index * 2));
  return buffer;
}

function riffChunk(id, data) {
  const padding = data.length % 2 ? Buffer.from([0]) : Buffer.alloc(0);
  return Buffer.concat([ascii(id), u32(data.length), data, padding]);
}

function makeWav({
  samples = [0, 1, -1],
  channels = 1,
  sampleRate = 24000,
  bitsPerSample = 16,
  audioFormat = 1,
  junk = null,
} = {}) {
  const bytesPerSample = bitsPerSample / 8;
  const fmt = Buffer.concat([
    u16(audioFormat),
    u16(channels),
    u32(sampleRate),
    u32(sampleRate * channels * bytesPerSample),
    u16(channels * bytesPerSample),
    u16(bitsPerSample),
  ]);
  const chunks = [riffChunk("fmt ", fmt)];
  if (junk) chunks.push(riffChunk("JUNK", Buffer.from(junk)));
  chunks.push(riffChunk("data", pcm16(samples)));
  const body = Buffer.concat([ascii("WAVE"), ...chunks]);
  return Buffer.concat([ascii("RIFF"), u32(body.length), body]);
}

test("parsePcm16Wav extracts the exact data chunk with odd chunk padding", () => {
  const expected = pcm16([-32768, 0, 32767]);
  const wav = makeWav({ samples: [-32768, 0, 32767], junk: [1, 2, 3] });

  const parsed = core.parsePcm16Wav(wav);

  assert.equal(parsed.channels, 1);
  assert.equal(parsed.sampleRate, 24000);
  assert.equal(parsed.bitsPerSample, 16);
  assert.equal(parsed.samples, 3);
  assert.deepEqual(Buffer.from(parsed.pcmBytes), expected);
});

for (const [name, options, pattern] of [
  ["stereo", { channels: 2 }, /mono/i],
  ["48 kHz", { sampleRate: 48000 }, /24000/],
  ["8-bit", { bitsPerSample: 8 }, /16-bit/i],
  ["non-PCM", { audioFormat: 3 }, /PCM/i],
]) {
  test(`parsePcm16Wav rejects ${name}`, () => {
    assert.throws(() => core.parsePcm16Wav(makeWav(options)), pattern);
  });
}

test("createPcmFrames pads the last source frame then appends exact silence", () => {
  const source = pcm16(Array.from({ length: 1921 }, () => 1));
  const frames = core.createPcmFrames(source, 0.16);

  assert.equal(frames.length, 4);
  assert.ok(frames.every((frame) => frame.length === 1920 * 2));
  assert.deepEqual(Buffer.from(frames[0]), source.subarray(0, 1920 * 2));
  assert.deepEqual(Buffer.from(frames[1].subarray(0, 2)), pcm16([1]));
  assert.ok(frames[1].subarray(2).every((value) => value === 0));
  assert.ok(frames[2].every((value) => value === 0));
  assert.ok(frames[3].every((value) => value === 0));
});

test("createOpusFrames uses 20 ms frames but pads source duration to the same 80 ms boundary as raw PCM", () => {
  const samples = new Array(481).fill(0);
  samples[0] = -32768;
  samples[1] = 32767;
  samples[480] = 16384;

  const frames = core.createOpusFrames(pcm16(samples), 0.04);

  assert.equal(frames.length, 6);
  assert.ok(frames.every((frame) => frame instanceof Float32Array));
  assert.ok(frames.every((frame) => frame.length === 480));
  assert.equal(frames[0][0], -1);
  assert.equal(frames[0][1], 32767 / 32768);
  assert.equal(frames[1][0], 0.5);
  assert.ok(frames[1].subarray(1).every((value) => value === 0));
  assert.ok(frames[2].every((value) => value === 0));
  assert.ok(frames[3].every((value) => value === 0));
  assert.ok(frames[4].every((value) => value === 0));
  assert.ok(frames[5].every((value) => value === 0));
});

test("official encoder configuration exactly matches the resolved 24 kHz frontend worker config", () => {
  assert.deepEqual(core.OFFICIAL_ENCODER_CONFIG, {
    bufferLength: 960,
    encoderSampleRate: 24000,
    encoderFrameSize: 20,
    maxFramesPerPage: 2,
    numberOfChannels: 1,
    recordingGain: 1,
    resampleQuality: 3,
    encoderComplexity: 0,
    encoderApplication: 2049,
    streamPages: true,
    wavBitDepth: 16,
    originalSampleRate: 24000,
    wavSampleRate: 24000,
  });
});

test("buildManifest keeps source identity shared while transport metadata differs", () => {
  const shared = {
    label: "stage1b",
    serverUrl: "ws://127.0.0.1:8998/api/chat",
    sourcePcmSha256: "abc123",
    sourceSamples: 1920,
    tailSeconds: 6,
    outputSamples: 24000,
    transcriptChars: 42,
  };

  const raw = core.buildManifest({ ...shared, transport: "pcm" });
  const opus = core.buildManifest({ ...shared, transport: "opus" });

  assert.equal(raw.source_pcm_sha256, opus.source_pcm_sha256);
  assert.equal(raw.source_samples, opus.source_samples);
  assert.equal(raw.transport, "raw-pcm16le");
  assert.equal(raw.protocol, "hibiki-native-pcm16le-kind-3");
  assert.equal(raw.input_frame_samples, 1920);
  assert.equal(raw.encoder, null);
  assert.equal(opus.transport, "opus-official-worker");
  assert.equal(opus.protocol, "hibiki-native-opus-kind-1");
  assert.equal(opus.input_frame_samples, 480);
  assert.deepEqual(opus.encoder, core.OFFICIAL_ENCODER_CONFIG);
});

test("writePcm16Wav produces canonical 24 kHz mono PCM16", () => {
  const wav = core.writePcm16Wav(new Float32Array([-1, -0.5, 0, 0.5, 1]));
  const parsed = core.parsePcm16Wav(wav);
  const values = new DataView(
    parsed.pcmBytes.buffer,
    parsed.pcmBytes.byteOffset,
    parsed.pcmBytes.byteLength,
  );

  assert.equal(parsed.sampleRate, 24000);
  assert.equal(parsed.channels, 1);
  assert.equal(parsed.bitsPerSample, 16);
  assert.deepEqual(
    Array.from({ length: 5 }, (_, index) => values.getInt16(index * 2, true)),
    [-32768, -16384, 0, 16384, 32767],
  );
});
