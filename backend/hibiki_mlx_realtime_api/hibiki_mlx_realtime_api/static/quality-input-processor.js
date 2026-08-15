const QUALITY_FRAME_SAMPLES = 1920;

class QualityInputProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frame = new Float32Array(QUALITY_FRAME_SAMPLES);
    this.offset = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || !input[0]) {
      return true;
    }

    const channel = input[0];
    let sourceOffset = 0;
    while (sourceOffset < channel.length) {
      const copyCount = Math.min(
        QUALITY_FRAME_SAMPLES - this.offset,
        channel.length - sourceOffset,
      );
      this.frame.set(
        channel.subarray(sourceOffset, sourceOffset + copyCount),
        this.offset,
      );
      sourceOffset += copyCount;
      this.offset += copyCount;

      if (this.offset === QUALITY_FRAME_SAMPLES) {
        const completed = this.frame;
        this.port.postMessage({ frame: completed }, [completed.buffer]);
        this.frame = new Float32Array(QUALITY_FRAME_SAMPLES);
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor("quality-input-processor", QualityInputProcessor);
