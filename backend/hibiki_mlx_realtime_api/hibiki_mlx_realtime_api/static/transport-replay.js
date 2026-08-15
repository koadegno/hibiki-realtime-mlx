(() => {
  "use strict";

  const core = globalThis.HibikiTransportReplayCore;
  if (!core) {
    throw new Error("HibikiTransportReplayCore is not loaded");
  }

  const PCM_INPUT_KIND = 3;
  const OPUS_INPUT_KIND = 1;
  const SETTLE_SECONDS = 1;
  const MAX_SCHEDULER_LAG_MS = 250;
  const OPUS_PAGE_INTERVAL_MS =
    core.OFFICIAL_ENCODER_CONFIG.encoderFrameSize *
    core.OFFICIAL_ENCODER_CONFIG.maxFramesPerPage;

  const sourceFileElement = document.getElementById("sourceFile");
  const transportElement = document.getElementById("transport");
  const tailSecondsElement = document.getElementById("tailSeconds");
  const runButton = document.getElementById("run");
  const cancelButton = document.getElementById("cancel");
  const sourceHashElement = document.getElementById("sourceHash");
  const sourceSamplesElement = document.getElementById("sourceSamples");
  const sourceDurationElement = document.getElementById("sourceDuration");
  const samplingProfileElement = document.getElementById("samplingProfile");
  const samplingSeedElement = document.getElementById("samplingSeed");
  const runStatusElement = document.getElementById("runStatus");
  const serverUrlElement = document.getElementById("serverUrl");
  const inputFramesElement = document.getElementById("inputFrames");
  const inputPacketsElement = document.getElementById("inputPackets");
  const outputPacketsElement = document.getElementById("outputPackets");
  const outputSamplesElement = document.getElementById("outputSamples");
  const errorElement = document.getElementById("error");
  const transcriptElement = document.getElementById("transcript");
  const downloadSourceButton = document.getElementById("downloadSource");
  const downloadTranscriptButton = document.getElementById("downloadTranscript");
  const downloadTranslatedButton = document.getElementById("downloadTranslated");
  const downloadManifestButton = document.getElementById("downloadManifest");

  let selectedSource = null;
  let completedResult = null;
  let activeFailure = null;

  function websocketUrl() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}/api/chat`;
  }

  function setError(message) {
    errorElement.textContent = message || "";
  }

  function setRunStatus(message) {
    runStatusElement.textContent = message;
  }

  function setArtifactButtons(enabled) {
    for (const button of [
      downloadSourceButton,
      downloadTranscriptButton,
      downloadTranslatedButton,
      downloadManifestButton,
    ]) {
      button.disabled = !enabled;
    }
  }

  function setRunning(running) {
    sourceFileElement.disabled = running;
    transportElement.disabled = running;
    tailSecondsElement.disabled = running;
    runButton.disabled = running || selectedSource === null;
    cancelButton.disabled = !running;
  }

  function resetCounters() {
    inputFramesElement.textContent = "0";
    inputPacketsElement.textContent = "0";
    outputPacketsElement.textContent = "0";
    outputSamplesElement.textContent = "0";
    transcriptElement.textContent = "";
  }

  function displayRuntimeMetadata(metadata) {
    samplingProfileElement.textContent = metadata ? metadata.sampling_profile : "—";
    samplingSeedElement.textContent = metadata ? String(metadata.sampling_seed) : "—";
  }

  function createFailureChannel() {
    let rejectFailure;
    let failed = false;
    const promise = new Promise((_, reject) => {
      rejectFailure = reject;
    });
    promise.catch(() => undefined);
    return {
      promise,
      fail(error) {
        if (failed) return;
        failed = true;
        rejectFailure(error instanceof Error ? error : new Error(String(error)));
      },
    };
  }

  function sleep(milliseconds) {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  }

  async function waitUntil(deadline, failure) {
    const delay = deadline - performance.now();
    if (delay > 0) {
      await Promise.race([sleep(delay), failure.promise]);
    }
    const lag = performance.now() - deadline;
    if (lag > MAX_SCHEDULER_LAG_MS) {
      throw new Error(
        `Browser replay clock fell ${lag.toFixed(0)} ms behind. Keep this tab active and rerun; ` +
          "the experiment will not burst queued frames to catch up.",
      );
    }
  }

  async function feedAtCadence(items, intervalMs, failure, sendItem, onProgress = null) {
    let deadline = performance.now();
    for (let index = 0; index < items.length; index += 1) {
      if (index > 0) {
        deadline += intervalMs;
        await waitUntil(deadline, failure);
      }
      await Promise.race([Promise.resolve(sendItem(items[index], index)), failure.promise]);
      if (onProgress) {
        onProgress(index + 1);
      } else {
        inputFramesElement.textContent = String(index + 1);
      }
    }
  }

  function asBytes(value) {
    if (value instanceof Uint8Array) {
      return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    }
    if (value instanceof ArrayBuffer) {
      return new Uint8Array(value);
    }
    if (ArrayBuffer.isView(value)) {
      return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    }
    throw new Error("Expected binary worker/server payload");
  }

  function containsAscii(bytes, text) {
    if (text.length === 0 || bytes.length < text.length) return false;
    outer: for (let offset = 0; offset <= bytes.length - text.length; offset += 1) {
      for (let index = 0; index < text.length; index += 1) {
        if (bytes[offset + index] !== text.charCodeAt(index)) continue outer;
      }
      return true;
    }
    return false;
  }

  function isOpusHeaderPage(page) {
    return containsAscii(page, "OpusHead") || containsAscii(page, "OpusTags");
  }

  function concatenateFloat32(parts) {
    const total = parts.reduce((sum, part) => sum + part.length, 0);
    const output = new Float32Array(total);
    let offset = 0;
    for (const part of parts) {
      output.set(part, offset);
      offset += part.length;
    }
    return output;
  }

  function createCapture(failure) {
    const textParts = [];
    const audioParts = [];
    let outputPackets = 0;
    let outputSamples = 0;
    const decoder = new Worker("/decoderWorker.min.js");

    decoder.onerror = (event) => {
      failure.fail(new Error(`Translated Opus decoder failed: ${event.message || "worker error"}`));
    };
    decoder.onmessage = (event) => {
      try {
        if (!event.data || !event.data[0]) return;
        const value = event.data[0];
        const frame = value instanceof Float32Array ? value : new Float32Array(value);
        if (frame.length === 0) return;
        audioParts.push(new Float32Array(frame));
        outputSamples += frame.length;
        outputSamplesElement.textContent = String(outputSamples);
      } catch (error) {
        failure.fail(error);
      }
    };
    decoder.postMessage({
      command: "init",
      bufferLength: 960,
      decoderSampleRate: core.SAMPLE_RATE,
      outputBufferSampleRate: core.SAMPLE_RATE,
      resampleQuality: 0,
    });

    return {
      decoder,
      consume(bytes) {
        if (bytes.length === 0) {
          throw new Error("Received an empty Hibiki server message");
        }
        const kind = bytes[0];
        const payload = bytes.slice(1);
        if (kind === 1) {
          outputPackets += 1;
          outputPacketsElement.textContent = String(outputPackets);
          decoder.postMessage({ command: "decode", pages: payload });
          return;
        }
        if (kind === 2) {
          const text = new TextDecoder().decode(payload);
          textParts.push(text);
          transcriptElement.textContent = textParts.join("");
          return;
        }
        throw new Error(`Unexpected Hibiki server message kind after handshake: ${kind}`);
      },
      transcript() {
        return textParts.join("");
      },
      translatedPcm() {
        return concatenateFloat32(audioParts);
      },
      outputPacketCount() {
        return outputPackets;
      },
    };
  }

  async function fetchRuntimeMetadata() {
    const response = await fetch("/ready", { cache: "no-store" });
    let payload;
    try {
      payload = await response.json();
    } catch (error) {
      throw new Error(`Hibiki /ready did not return JSON: ${String(error)}`);
    }

    if (!response.ok || payload.ready !== true) {
      throw new Error(`Hibiki runtime is not ready: ${payload.phase || response.status}`);
    }
    if (typeof payload.sampling_profile !== "string" || payload.sampling_profile.length === 0) {
      throw new Error("Hibiki /ready is missing Stage 2 sampling_profile metadata");
    }
    if (!Number.isInteger(payload.sampling_seed)) {
      throw new Error("Hibiki /ready is missing Stage 2 sampling_seed metadata");
    }
    for (const field of ["text_temperature", "audio_temperature"]) {
      if (typeof payload[field] !== "number" || !Number.isFinite(payload[field])) {
        throw new Error(`Hibiki /ready is missing Stage 2 ${field} metadata`);
      }
    }
    for (const field of ["text_top_k", "audio_top_k"]) {
      if (!Number.isInteger(payload[field]) || payload[field] <= 0) {
        throw new Error(`Hibiki /ready is missing Stage 2 ${field} metadata`);
      }
    }
    return payload;
  }

  function openWebSocket(url, failure, consume) {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
      let ready = false;
      let intentionallyClosed = false;
      let handshakeSettled = false;
      const timeout = window.setTimeout(() => {
        if (handshakeSettled) return;
        handshakeSettled = true;
        reject(new Error("WebSocket handshake timed out after 10 seconds"));
        ws.close();
      }, 10000);

      function fail(error) {
        if (!ready && !handshakeSettled) {
          handshakeSettled = true;
          window.clearTimeout(timeout);
          reject(error);
          return;
        }
        failure.fail(error);
      }

      ws.onerror = () => fail(new Error(`WebSocket error while connected to ${url}`));
      ws.onclose = (event) => {
        if (intentionallyClosed) return;
        fail(
          new Error(
            `WebSocket closed before replay completion: code=${event.code} ` +
              `reason=${event.reason || "none"}`,
          ),
        );
      };
      ws.onmessage = (event) => {
        try {
          const bytes = asBytes(event.data);
          if (!ready) {
            if (bytes.length !== 1 || bytes[0] !== 0) {
              throw new Error("Expected the Hibiki kind-0 handshake before output data");
            }
            ready = true;
            handshakeSettled = true;
            window.clearTimeout(timeout);
            resolve({
              ws,
              close() {
                intentionallyClosed = true;
                if (ws.readyState === WebSocket.OPEN) {
                  ws.close(1000, "Deterministic replay complete");
                } else if (ws.readyState === WebSocket.CONNECTING) {
                  ws.close();
                }
              },
            });
            return;
          }
          consume(bytes);
        } catch (error) {
          fail(error);
        }
      };
    });
  }

  async function sendPcm(ws, pcmBytes, tailSeconds, failure) {
    const frames = core.createPcmFrames(pcmBytes, tailSeconds);
    await feedAtCadence(frames, 80, failure, (frame) => {
      if (ws.readyState !== WebSocket.OPEN) {
        throw new Error("WebSocket is not open while sending raw PCM");
      }
      const message = new Uint8Array(1 + frame.length);
      message[0] = PCM_INPUT_KIND;
      message.set(frame, 1);
      ws.send(message);
    });
    return { inputFrames: frames.length, inputPackets: 0 };
  }

  async function preencodeOfficialOpus(pcmBytes, tailSeconds, failure) {
    const frames = core.createOpusFrames(pcmBytes, tailSeconds);
    const worker = new Worker("/encoderWorker.min.js");
    const headerPages = [];
    const audioPages = [];
    let readyResolve;
    let doneResolve;
    let ready = false;
    let done = false;
    const readyPromise = new Promise((resolve) => {
      readyResolve = resolve;
    });
    const donePromise = new Promise((resolve) => {
      doneResolve = resolve;
    });

    worker.onerror = (event) => {
      failure.fail(new Error(`Official Opus encoder failed: ${event.message || "worker error"}`));
    };
    worker.onmessage = (event) => {
      try {
        const data = event.data || {};
        if (data.message === "ready") {
          if (!ready) {
            ready = true;
            readyResolve();
          }
          return;
        }
        if (data.message === "page") {
          const page = new Uint8Array(asBytes(data.page));
          if (isOpusHeaderPage(page)) {
            headerPages.push(page);
          } else {
            audioPages.push(page);
          }
          return;
        }
        if (data.message === "done" && !done) {
          done = true;
          doneResolve();
        }
      } catch (error) {
        failure.fail(error);
      }
    };

    try {
      worker.postMessage({ command: "init", ...core.OFFICIAL_ENCODER_CONFIG });
      await Promise.race([readyPromise, failure.promise]);
      worker.postMessage({ command: "getHeaderPages" });

      for (let index = 0; index < frames.length; index += 1) {
        worker.postMessage({ command: "encode", buffers: [frames[index]] });
        if ((index + 1) % 256 === 0) {
          await Promise.race([sleep(0), failure.promise]);
        }
      }

      worker.postMessage({ command: "done" });
      await Promise.race([donePromise, failure.promise]);

      if (headerPages.length < 2) {
        throw new Error(
          `Official Opus pre-encode produced ${headerPages.length} header pages; expected OpusHead and OpusTags`,
        );
      }
      if (audioPages.length === 0) {
        throw new Error("Official Opus pre-encode produced no audio pages");
      }

      return {
        inputFrames: frames.length,
        headerPages,
        audioPages,
      };
    } finally {
      worker.terminate();
    }
  }

  async function sendPreencodedOpus(ws, preencoded, failure) {
    let inputPackets = 0;

    function sendPage(page) {
      if (ws.readyState !== WebSocket.OPEN) {
        throw new Error("WebSocket is not open while sending an Opus page");
      }
      const message = new Uint8Array(1 + page.length);
      message[0] = OPUS_INPUT_KIND;
      message.set(page, 1);
      ws.send(message);
      inputPackets += 1;
      inputPacketsElement.textContent = String(inputPackets);
    }

    for (const page of preencoded.headerPages) {
      sendPage(page);
    }

    const framesPerPage = core.OFFICIAL_ENCODER_CONFIG.maxFramesPerPage;
    await feedAtCadence(
      preencoded.audioPages,
      OPUS_PAGE_INTERVAL_MS,
      failure,
      (page) => sendPage(page),
      (audioPagesSent) => {
        const framesSent = Math.min(preencoded.inputFrames, audioPagesSent * framesPerPage);
        inputFramesElement.textContent = String(framesSent);
      },
    );
    inputFramesElement.textContent = String(preencoded.inputFrames);

    return {
      inputFrames: preencoded.inputFrames,
      inputPackets,
      inputHeaderPages: preencoded.headerPages.length,
      inputAudioPages: preencoded.audioPages.length,
    };
  }

  async function sha256Hex(bytes) {
    const copy = bytes.slice();
    const digest = await crypto.subtle.digest("SHA-256", copy.buffer);
    return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join(
      "",
    );
  }

  async function loadSourceFile() {
    const file = sourceFileElement.files && sourceFileElement.files[0];
    selectedSource = null;
    completedResult = null;
    setArtifactButtons(false);
    setError("");
    sourceHashElement.textContent = "—";
    sourceSamplesElement.textContent = "—";
    sourceDurationElement.textContent = "—";
    runButton.disabled = true;

    if (!file) {
      setRunStatus("Load a source WAV.");
      return;
    }

    try {
      setRunStatus("Validating source WAV…");
      const originalWav = new Uint8Array(await file.arrayBuffer());
      const source = core.parsePcm16Wav(originalWav);
      const sourceHash = await sha256Hex(source.pcmBytes);
      selectedSource = { fileName: file.name, originalWav, source, sourceHash };
      sourceHashElement.textContent = sourceHash;
      sourceSamplesElement.textContent = String(source.samples);
      sourceDurationElement.textContent = `${(source.samples / core.SAMPLE_RATE).toFixed(2)} s`;
      setRunStatus("Source is canonical and ready.");
      runButton.disabled = false;
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error));
      setRunStatus("Source rejected.");
    }
  }

  async function runTransport() {
    if (!selectedSource || activeFailure) return;

    const tailSeconds = Number(tailSecondsElement.value);
    if (!Number.isFinite(tailSeconds) || tailSeconds < 0) {
      setError("Silence tail must be a finite number >= 0.");
      return;
    }
    const transport = transportElement.value;
    if (transport !== "pcm" && transport !== "opus") {
      setError(`Unknown transport: ${transport}`);
      return;
    }

    completedResult = null;
    setArtifactButtons(false);
    resetCounters();
    setError("");
    displayRuntimeMetadata(null);
    setRunning(true);

    const failure = createFailureChannel();
    activeFailure = failure;
    const capture = createCapture(failure);
    const url = websocketUrl();
    serverUrlElement.textContent = url;
    let session = null;
    let preencodedOpus = null;
    let runtimeMetadata = null;

    try {
      setRunStatus("Reading runtime sampling identity…");
      runtimeMetadata = await Promise.race([fetchRuntimeMetadata(), failure.promise]);
      displayRuntimeMetadata(runtimeMetadata);

      if (transport === "opus") {
        setRunStatus("Pre-encoding the exact WAV with the bundled official Opus worker…");
        preencodedOpus = await Promise.race([
          preencodeOfficialOpus(selectedSource.source.pcmBytes, tailSeconds, failure),
          failure.promise,
        ]);
      }

      setRunStatus(`Connecting fresh ${transport.toUpperCase()} session…`);
      session = await openWebSocket(url, failure, (bytes) => capture.consume(bytes));
      setRunStatus(
        transport === "pcm"
          ? `Stage 2 ${runtimeMetadata.sampling_profile}: replaying exact PCM16 at 80 ms cadence…`
          : `Replaying pre-encoded official Opus pages at ${OPUS_PAGE_INTERVAL_MS} ms cadence…`,
      );

      const sendResult = await Promise.race([
        transport === "pcm"
          ? sendPcm(session.ws, selectedSource.source.pcmBytes, tailSeconds, failure)
          : sendPreencodedOpus(session.ws, preencodedOpus, failure),
        failure.promise,
      ]);

      setRunStatus("Source + deterministic tail sent; settling translated output…");
      await Promise.race([sleep(SETTLE_SECONDS * 1000), failure.promise]);
      session.close();

      const transcript = capture.transcript();
      const translatedPcm = capture.translatedPcm();
      const translatedWav = core.writePcm16Wav(translatedPcm);
      const label =
        transport === "pcm"
          ? `stage2-${runtimeMetadata.sampling_profile}-pcm`
          : "stage1b-opus";
      const manifest = core.buildManifest({
        label,
        serverUrl: url,
        sourcePcmSha256: selectedSource.sourceHash,
        sourceSamples: selectedSource.source.samples,
        tailSeconds,
        outputSamples: translatedPcm.length,
        transcriptChars: transcript.length,
        transport,
      });
      manifest.source_file_name = selectedSource.fileName;
      manifest.input_frames = sendResult.inputFrames;
      manifest.input_opus_pages = sendResult.inputPackets;
      manifest.output_packets = capture.outputPacketCount();
      manifest.settle_seconds = SETTLE_SECONDS;
      manifest.sampling_profile = runtimeMetadata.sampling_profile;
      manifest.sampling_seed = runtimeMetadata.sampling_seed;
      manifest.text_temperature = runtimeMetadata.text_temperature;
      manifest.text_top_k = runtimeMetadata.text_top_k;
      manifest.audio_temperature = runtimeMetadata.audio_temperature;
      manifest.audio_top_k = runtimeMetadata.audio_top_k;
      if (transport === "opus") {
        manifest.opus_preencoded_before_websocket = true;
        manifest.opus_page_interval_ms = OPUS_PAGE_INTERVAL_MS;
        manifest.input_opus_header_pages = sendResult.inputHeaderPages;
        manifest.input_opus_audio_pages = sendResult.inputAudioPages;
      }

      completedResult = {
        label: manifest.label,
        sourceWav: selectedSource.originalWav.slice(),
        transcript,
        translatedWav,
        manifest,
      };
      outputSamplesElement.textContent = String(translatedPcm.length);
      setArtifactButtons(true);
      setRunStatus(
        `Completed ${transport.toUpperCase()} run (${runtimeMetadata.sampling_profile}). ` +
          "Download all four artifacts before changing server/profile.",
      );
    } catch (error) {
      setRunStatus("Run failed — no completed manifest/artifact set was accepted.");
      setError(error instanceof Error ? error.message : String(error));
      completedResult = null;
      setArtifactButtons(false);
    } finally {
      if (session) session.close();
      capture.decoder.terminate();
      activeFailure = null;
      setRunning(false);
    }
  }

  function downloadBytes(bytes, mimeType, fileName) {
    const blob = new Blob([bytes], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function requireCompletedResult() {
    if (!completedResult) {
      throw new Error("No completed deterministic replay is available for download");
    }
    return completedResult;
  }

  sourceFileElement.addEventListener("change", loadSourceFile);
  runButton.addEventListener("click", runTransport);
  cancelButton.addEventListener("click", () => {
    if (activeFailure) activeFailure.fail(new Error("Run cancelled by user"));
  });
  downloadSourceButton.addEventListener("click", () => {
    const result = requireCompletedResult();
    downloadBytes(result.sourceWav, "audio/wav", `${result.label}-source.wav`);
  });
  downloadTranscriptButton.addEventListener("click", () => {
    const result = requireCompletedResult();
    downloadBytes(
      new TextEncoder().encode(result.transcript),
      "text/plain;charset=utf-8",
      `${result.label}-transcript.txt`,
    );
  });
  downloadTranslatedButton.addEventListener("click", () => {
    const result = requireCompletedResult();
    downloadBytes(result.translatedWav, "audio/wav", `${result.label}-translated.wav`);
  });
  downloadManifestButton.addEventListener("click", () => {
    const result = requireCompletedResult();
    downloadBytes(
      new TextEncoder().encode(`${JSON.stringify(result.manifest, null, 2)}\n`),
      "application/json",
      `${result.label}-manifest.json`,
    );
  });

  serverUrlElement.textContent = websocketUrl();
  displayRuntimeMetadata(null);
  setArtifactButtons(false);
  setRunning(false);
})();
