(() => {
  "use strict";

  const SAMPLE_RATE = 24000;
  const FRAME_SAMPLES = 1920;
  const PCM_INPUT_KIND = 3;

  const startButton = document.getElementById("start");
  const stopButton = document.getElementById("stop");
  const downloadButton = document.getElementById("download");
  const errorElement = document.getElementById("error");
  const sampleRateElement = document.getElementById("sampleRate");
  const wsStateElement = document.getElementById("wsState");
  const inputFramesElement = document.getElementById("inputFrames");
  const outputFramesElement = document.getElementById("outputFrames");
  const transcriptElement = document.getElementById("transcript");

  let inputAudioContext = null;
  let outputAudioContext = null;
  let mediaStream = null;
  let sourceNode = null;
  let inputWorklet = null;
  let inputSink = null;
  let outputWorklet = null;
  let decoder = null;
  let ws = null;
  let inputFrameCount = 0;
  let outputPacketCount = 0;
  let capturedPcm = [];
  let running = false;

  function setError(message) {
    errorElement.textContent = message || "";
  }

  function setWsState(value) {
    wsStateElement.textContent = value;
  }

  function floatFrameToPcm16Bytes(frame) {
    const bytes = new Uint8Array(1 + frame.length * 2);
    const view = new DataView(bytes.buffer);
    bytes[0] = PCM_INPUT_KIND;
    const captured = new Int16Array(frame.length);
    for (let i = 0; i < frame.length; i += 1) {
      const x = Math.max(-1, Math.min(1, frame[i]));
      const sample = x < 0 ? Math.round(x * 32768) : Math.round(x * 32767);
      captured[i] = sample;
      view.setInt16(1 + i * 2, sample, true);
    }
    capturedPcm.push(captured);
    return bytes;
  }

  function initDecoder() {
    if (!outputAudioContext) {
      throw new Error("Output AudioContext is not initialized");
    }
    decoder = new Worker("/decoderWorker.min.js");
    decoder.onmessage = (event) => {
      if (!event.data || !outputWorklet) return;
      const frame = event.data[0];
      outputWorklet.port.postMessage({ frame, type: "audio" });
    };
    decoder.postMessage({
      command: "init",
      bufferLength: (960 * outputAudioContext.sampleRate) / SAMPLE_RATE,
      decoderSampleRate: SAMPLE_RATE,
      outputBufferSampleRate: outputAudioContext.sampleRate,
      resampleQuality: 0,
    });
  }

  function handleServerMessage(bytes) {
    if (bytes.length === 0) return;
    const kind = bytes[0];
    const payload = bytes.slice(1);
    if (kind === 0) {
      setWsState("ready");
      return;
    }
    if (kind === 1) {
      outputPacketCount += 1;
      outputFramesElement.textContent = String(outputPacketCount);
      if (decoder) decoder.postMessage({ command: "decode", pages: payload });
      return;
    }
    if (kind === 2) {
      const text = new TextDecoder().decode(payload);
      transcriptElement.textContent += text;
    }
  }

  async function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${protocol}//${window.location.host}/api/chat`);
    ws.binaryType = "arraybuffer";
    setWsState("connecting");

    await new Promise((resolve, reject) => {
      const timeout = window.setTimeout(
        () => reject(new Error("WebSocket handshake timed out")),
        10000,
      );
      ws.onopen = () => {
        setWsState("open; waiting for Hibiki handshake");
      };
      ws.onerror = () => {
        window.clearTimeout(timeout);
        reject(new Error("Could not open /api/chat"));
      };
      ws.onmessage = (event) => {
        const bytes = new Uint8Array(event.data);
        if (bytes.length === 0) return;
        if (bytes[0] === 0) {
          window.clearTimeout(timeout);
          setWsState("ready");
          resolve();
          return;
        }
        handleServerMessage(bytes);
      };
      ws.onclose = (event) => {
        window.clearTimeout(timeout);
        setWsState(`closed (${event.code})`);
        if (running) {
          setError(
            `WebSocket closed while running: code=${event.code} reason=${event.reason || "none"}`,
          );
        }
      };
    });

    ws.onmessage = (event) => handleServerMessage(new Uint8Array(event.data));
  }

  async function stop(enableDownload = true) {
    running = false;
    startButton.disabled = false;
    stopButton.disabled = true;

    if (inputWorklet) inputWorklet.disconnect();
    if (sourceNode) sourceNode.disconnect();
    if (inputSink) inputSink.disconnect();
    if (outputWorklet) outputWorklet.disconnect();
    if (mediaStream) mediaStream.getTracks().forEach((track) => track.stop());
    if (decoder) decoder.terminate();
    if (ws && ws.readyState <= WebSocket.OPEN) ws.close(1000, "quality lab stopped");
    if (inputAudioContext && inputAudioContext.state !== "closed") {
      await inputAudioContext.close();
    }
    if (outputAudioContext && outputAudioContext.state !== "closed") {
      await outputAudioContext.close();
    }

    inputWorklet = null;
    sourceNode = null;
    inputSink = null;
    outputWorklet = null;
    mediaStream = null;
    decoder = null;
    ws = null;
    inputAudioContext = null;
    outputAudioContext = null;

    if (enableDownload && capturedPcm.length > 0) downloadButton.disabled = false;
  }

  async function start() {
    if (running) return;
    setError("");
    transcriptElement.textContent = "";
    capturedPcm = [];
    inputFrameCount = 0;
    outputPacketCount = 0;
    inputFramesElement.textContent = "0";
    outputFramesElement.textContent = "0";
    downloadButton.disabled = true;

    try {
      const ready = await fetch("/ready");
      if (!ready.ok) {
        throw new Error(`Backend is not ready: ${await ready.text()}`);
      }

      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: false,
          autoGainControl: true,
          channelCount: 1,
        },
      });

      inputAudioContext = new AudioContext({ sampleRate: SAMPLE_RATE });
      outputAudioContext = new AudioContext();
      await Promise.all([inputAudioContext.resume(), outputAudioContext.resume()]);
      sampleRateElement.textContent = `${inputAudioContext.sampleRate} Hz`;
      if (inputAudioContext.sampleRate !== SAMPLE_RATE) {
        throw new Error(
          `This browser created an input AudioContext at ${inputAudioContext.sampleRate} Hz instead of 24000 Hz. ` +
            "Stage 1 must not silently resample with an unknown path, so the PCM comparison is stopped.",
        );
      }

      await inputAudioContext.audioWorklet.addModule("/quality-input-processor.js");
      await outputAudioContext.audioWorklet.addModule("/audio-output-processor.js");

      outputWorklet = new AudioWorkletNode(outputAudioContext, "audio-output-processor");
      outputWorklet.connect(outputAudioContext.destination);
      initDecoder();
      await connectWebSocket();

      sourceNode = inputAudioContext.createMediaStreamSource(mediaStream);
      inputWorklet = new AudioWorkletNode(inputAudioContext, "quality-input-processor");
      inputSink = inputAudioContext.createGain();
      inputSink.gain.value = 0;
      sourceNode.connect(inputWorklet);
      inputWorklet.connect(inputSink);
      inputSink.connect(inputAudioContext.destination);

      running = true;
      inputWorklet.port.onmessage = (event) => {
        if (!running || !ws || ws.readyState !== WebSocket.OPEN) return;
        const frame = event.data.frame;
        if (!(frame instanceof Float32Array) || frame.length !== FRAME_SAMPLES) {
          setError(`Unexpected input frame shape: ${frame ? frame.length : "missing"}`);
          return;
        }
        ws.send(floatFrameToPcm16Bytes(frame));
        inputFrameCount += 1;
        inputFramesElement.textContent = String(inputFrameCount);
      };

      startButton.disabled = true;
      stopButton.disabled = false;
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error));
      await stop(false);
    }
  }

  function downloadWav() {
    const totalSamples = capturedPcm.reduce((sum, frame) => sum + frame.length, 0);
    if (totalSamples === 0) return;

    const buffer = new ArrayBuffer(44 + totalSamples * 2);
    const view = new DataView(buffer);
    const writeAscii = (offset, value) => {
      for (let i = 0; i < value.length; i += 1) {
        view.setUint8(offset + i, value.charCodeAt(i));
      }
    };
    writeAscii(0, "RIFF");
    view.setUint32(4, 36 + totalSamples * 2, true);
    writeAscii(8, "WAVE");
    writeAscii(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, SAMPLE_RATE, true);
    view.setUint32(28, SAMPLE_RATE * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeAscii(36, "data");
    view.setUint32(40, totalSamples * 2, true);

    let offset = 44;
    for (const frame of capturedPcm) {
      for (let i = 0; i < frame.length; i += 1) {
        view.setInt16(offset, frame[i], true);
        offset += 2;
      }
    }

    const blob = new Blob([buffer], { type: "audio/wav" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `hibiki-quality-source-${new Date().toISOString().replace(/[:.]/g, "-")}.wav`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  startButton.addEventListener("click", start);
  stopButton.addEventListener("click", () => stop(true));
  downloadButton.addEventListener("click", downloadWav);
})();
