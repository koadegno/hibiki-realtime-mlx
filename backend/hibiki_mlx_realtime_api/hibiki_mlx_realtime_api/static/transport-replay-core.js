(function (root, factory) {
  "use strict";

  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.HibikiTransportReplayCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  "use strict";

  const SAMPLE_RATE = 24000;
  const PCM_FRAME_SAMPLES = 1920;
  const OPUS_FRAME_SAMPLES = 480;
  const PCM_BYTES_PER_SAMPLE = 2;
  const PCM_FRAME_SECONDS = PCM_FRAME_SAMPLES / SAMPLE_RATE;
  const OPUS_FRAME_SECONDS = OPUS_FRAME_SAMPLES / SAMPLE_RATE;

  const OFFICIAL_ENCODER_CONFIG = Object.freeze({
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

  function asUint8Array(value) {
    if (value instanceof Uint8Array) {
      return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    }
    if (value instanceof ArrayBuffer) {
      return new Uint8Array(value);
    }
    if (ArrayBuffer.isView(value)) {
      return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    }
    throw new TypeError("expected WAV/PCM bytes as Uint8Array or ArrayBuffer");
  }

  function asciiAt(bytes, offset, length) {
    let value = "";
    for (let index = 0; index < length; index += 1) {
      value += String.fromCharCode(bytes[offset + index]);
    }
    return value;
  }

  function parsePcm16Wav(input) {
    const bytes = asUint8Array(input);
    if (bytes.byteLength < 12) {
      throw new Error("WAV is too short to contain a RIFF/WAVE header");
    }
    if (asciiAt(bytes, 0, 4) !== "RIFF" || asciiAt(bytes, 8, 4) !== "WAVE") {
      throw new Error("source must be a RIFF/WAVE file");
    }

    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    let fmt = null;
    let pcmBytes = null;
    let offset = 12;

    while (offset + 8 <= bytes.byteLength) {
      const id = asciiAt(bytes, offset, 4);
      const size = view.getUint32(offset + 4, true);
      const dataOffset = offset + 8;
      const dataEnd = dataOffset + size;
      if (dataEnd > bytes.byteLength) {
        throw new Error(`WAV chunk ${id} exceeds file length`);
      }

      if (id === "fmt ") {
        if (size < 16) {
          throw new Error("WAV fmt chunk is shorter than 16 bytes");
        }
        fmt = {
          audioFormat: view.getUint16(dataOffset, true),
          channels: view.getUint16(dataOffset + 2, true),
          sampleRate: view.getUint32(dataOffset + 4, true),
          blockAlign: view.getUint16(dataOffset + 12, true),
          bitsPerSample: view.getUint16(dataOffset + 14, true),
        };
      } else if (id === "data" && pcmBytes === null) {
        pcmBytes = bytes.slice(dataOffset, dataEnd);
      }

      offset = dataEnd + (size & 1);
    }

    if (fmt === null) {
      throw new Error("WAV is missing the fmt chunk");
    }
    if (pcmBytes === null) {
      throw new Error("WAV is missing the data chunk");
    }
    if (fmt.audioFormat !== 1) {
      throw new Error(`WAV must use integer PCM format 1, got ${fmt.audioFormat}`);
    }
    if (fmt.channels !== 1) {
      throw new Error(`WAV must be mono, got ${fmt.channels} channels`);
    }
    if (fmt.sampleRate !== SAMPLE_RATE) {
      throw new Error(`WAV must be 24000 Hz, got ${fmt.sampleRate} Hz`);
    }
    if (fmt.bitsPerSample !== 16) {
      throw new Error(`WAV must use 16-bit PCM, got ${fmt.bitsPerSample}-bit`);
    }
    if (fmt.blockAlign !== PCM_BYTES_PER_SAMPLE) {
      throw new Error(`WAV block alignment must be 2 bytes, got ${fmt.blockAlign}`);
    }
    if (pcmBytes.byteLength % PCM_BYTES_PER_SAMPLE !== 0) {
      throw new Error("WAV data contains an incomplete PCM16 sample");
    }

    return {
      pcmBytes,
      samples: pcmBytes.byteLength / PCM_BYTES_PER_SAMPLE,
      channels: fmt.channels,
      sampleRate: fmt.sampleRate,
      bitsPerSample: fmt.bitsPerSample,
    };
  }

  function validateTailSeconds(tailSeconds) {
    if (!Number.isFinite(tailSeconds) || tailSeconds < 0) {
      throw new Error("tailSeconds must be a finite number >= 0");
    }
  }

  function validatePcmBytes(input) {
    const bytes = asUint8Array(input);
    if (bytes.byteLength % PCM_BYTES_PER_SAMPLE !== 0) {
      throw new Error("PCM16 payload contains an incomplete sample");
    }
    return bytes;
  }

  function pcm16ToFloat32(input) {
    const bytes = validatePcmBytes(input);
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const result = new Float32Array(bytes.byteLength / PCM_BYTES_PER_SAMPLE);
    for (let index = 0; index < result.length; index += 1) {
      result[index] = view.getInt16(index * PCM_BYTES_PER_SAMPLE, true) / 32768.0;
    }
    return result;
  }

  function createPcmFrames(input, tailSeconds) {
    validateTailSeconds(tailSeconds);
    const bytes = validatePcmBytes(input);
    const frameBytes = PCM_FRAME_SAMPLES * PCM_BYTES_PER_SAMPLE;
    const frames = [];

    for (let offset = 0; offset < bytes.byteLength; offset += frameBytes) {
      const frame = new Uint8Array(frameBytes);
      frame.set(bytes.subarray(offset, Math.min(offset + frameBytes, bytes.byteLength)));
      frames.push(frame);
    }

    const tailFrames = Math.round(tailSeconds / PCM_FRAME_SECONDS);
    for (let index = 0; index < tailFrames; index += 1) {
      frames.push(new Uint8Array(frameBytes));
    }
    return frames;
  }

  function createOpusFrames(input, tailSeconds) {
    validateTailSeconds(tailSeconds);
    const source = pcm16ToFloat32(input);
    const frames = [];

    for (let offset = 0; offset < source.length; offset += OPUS_FRAME_SAMPLES) {
      const frame = new Float32Array(OPUS_FRAME_SAMPLES);
      frame.set(source.subarray(offset, Math.min(offset + OPUS_FRAME_SAMPLES, source.length)));
      frames.push(frame);
    }

    const tailFrames = Math.round(tailSeconds / OPUS_FRAME_SECONDS);
    for (let index = 0; index < tailFrames; index += 1) {
      frames.push(new Float32Array(OPUS_FRAME_SAMPLES));
    }
    return frames;
  }

  function buildManifest({
    label,
    serverUrl,
    sourcePcmSha256,
    sourceSamples,
    tailSeconds,
    outputSamples,
    transcriptChars,
    transport,
  }) {
    if (transport !== "pcm" && transport !== "opus") {
      throw new Error(`unknown transport: ${transport}`);
    }

    const isPcm = transport === "pcm";
    return {
      label,
      url: serverUrl,
      transport: isPcm ? "raw-pcm16le" : "opus-official-worker",
      protocol: isPcm ? "hibiki-native-pcm16le-kind-3" : "hibiki-native-opus-kind-1",
      sample_rate: SAMPLE_RATE,
      source_pcm_sha256: sourcePcmSha256,
      source_samples: sourceSamples,
      source_seconds: sourceSamples / SAMPLE_RATE,
      tail_seconds: tailSeconds,
      input_frame_samples: isPcm ? PCM_FRAME_SAMPLES : OPUS_FRAME_SAMPLES,
      input_frame_seconds: isPcm ? PCM_FRAME_SECONDS : OPUS_FRAME_SECONDS,
      encoder: isPcm ? null : { ...OFFICIAL_ENCODER_CONFIG },
      output_samples: outputSamples,
      output_seconds: outputSamples / SAMPLE_RATE,
      transcript_chars: transcriptChars,
    };
  }

  function writeAscii(view, offset, value) {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index));
    }
  }

  function writePcm16Wav(input) {
    const samples = input instanceof Float32Array ? input : new Float32Array(input);
    const buffer = new ArrayBuffer(44 + samples.length * PCM_BYTES_PER_SAMPLE);
    const view = new DataView(buffer);

    writeAscii(view, 0, "RIFF");
    view.setUint32(4, 36 + samples.length * PCM_BYTES_PER_SAMPLE, true);
    writeAscii(view, 8, "WAVE");
    writeAscii(view, 12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, SAMPLE_RATE, true);
    view.setUint32(28, SAMPLE_RATE * PCM_BYTES_PER_SAMPLE, true);
    view.setUint16(32, PCM_BYTES_PER_SAMPLE, true);
    view.setUint16(34, 16, true);
    writeAscii(view, 36, "data");
    view.setUint32(40, samples.length * PCM_BYTES_PER_SAMPLE, true);

    for (let index = 0; index < samples.length; index += 1) {
      const clipped = Math.max(-1, Math.min(1, samples[index]));
      const pcm = clipped < 0 ? Math.round(clipped * 32768) : Math.round(clipped * 32767);
      view.setInt16(44 + index * PCM_BYTES_PER_SAMPLE, pcm, true);
    }

    return new Uint8Array(buffer);
  }

  return Object.freeze({
    SAMPLE_RATE,
    PCM_FRAME_SAMPLES,
    OPUS_FRAME_SAMPLES,
    OFFICIAL_ENCODER_CONFIG,
    parsePcm16Wav,
    pcm16ToFloat32,
    createPcmFrames,
    createOpusFrames,
    buildManifest,
    writePcm16Wav,
  });
});
