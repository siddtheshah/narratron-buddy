const SAMPLE_RATE = 16_000; // Test-lab-local module.
const settings = {
  microphone: document.querySelector("#microphone"),
  sensitivity: document.querySelector("#sensitivity"),
  speechDuration: document.querySelector("#speech-duration"),
  silenceDuration: document.querySelector("#silence-duration"),
  playbackGain: document.querySelector("#playback-gain"),
};
const outputs = {
  sensitivity: document.querySelector("#sensitivity-value"),
  speechDuration: document.querySelector("#speech-duration-value"),
  silenceDuration: document.querySelector("#silence-duration-value"),
  playbackGain: document.querySelector("#playback-gain-value"),
};
const recordButton = document.querySelector("#record-button");
const refreshMicrophonesButton = document.querySelector("#refresh-microphones");
const clearButton = document.querySelector("#clear-button");
const status = document.querySelector("#status");
const playback = document.querySelector("#playback");
const log = document.querySelector("#log");

let stopListening = null;
let speechActive = false;
let speechChunks = [];
let playbackUrl = null;

async function refreshMicrophones(requestPermission = false) {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  try {
    if (requestPermission) {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((track) => track.stop());
    }
    const previousId = settings.microphone.value;
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter(({ kind }) => kind === "audioinput");
    settings.microphone.replaceChildren(new Option("System default microphone", ""));
    devices.forEach((device, index) => settings.microphone.add(new Option(device.label || `Microphone ${index + 1} (allow access to identify)`, device.deviceId)));
    settings.microphone.value = [...settings.microphone.options].some(({ value }) => value === previousId) ? previousId : "";
    document.querySelector("#microphone-hint").textContent = devices.some(({ label }) => label)
      ? `${devices.length} microphone${devices.length === 1 ? "" : "s"} available. The selection is used for the next recording.`
      : "Click Refresh devices and allow microphone access to identify each microphone.";
  } catch (error) {
    setStatus(`Unable to list microphones: ${error.message}`, "error");
  }
}

async function listenForSpeechWithDevice(options) {
  const { deviceId, onData, onError, onSpeechStart, onSpeechEnd, sampleRate, vadThreshold, vadSilenceDuration, vadMinRecordingTime, continuous } = options;
  let listening = true;
  let speaking = false;
  let context;
  let stream;
  let source;
  let worklet;
  let silenceStart = null;
  let speechStart = null;
  const cleanup = () => {
    listening = false;
    worklet?.disconnect();
    source?.disconnect();
    if (context?.state !== "closed") context.close();
    stream?.getTracks().forEach((track) => track.stop());
  };
  const stop = () => {
    if (!listening) return;
    if (speaking) onSpeechEnd();
    cleanup();
  };
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { ...(deviceId ? { deviceId: { exact: deviceId } } : {}), channelCount: 1, sampleRate: { ideal: sampleRate }, echoCancellation: true, noiseSuppression: true },
    });
    if (!listening) return cleanup();
    context = new AudioContext({ sampleRate });
    const sourceCode = `class Processor extends AudioWorkletProcessor {
      constructor() { super(); this.buffer = new Float32Array(4096); this.index = 0; }
      process(inputs) { const input = inputs[0]?.[0]; if (!input) return true;
        for (const sample of input) { this.buffer[this.index++] = sample; if (this.index === this.buffer.length) {
          let sum = 0; for (const value of this.buffer) sum += value * value;
          const pcm = new Int16Array(this.buffer.length);
          for (let i = 0; i < this.buffer.length; i++) { const value = Math.max(-1, Math.min(1, this.buffer[i])); pcm[i] = value < 0 ? value * 0x8000 : value * 0x7fff; }
          this.port.postMessage({ pcm: new Uint8Array(pcm.buffer), rms: Math.sqrt(sum / this.buffer.length) }); this.index = 0;
        }} return true; }
    } registerProcessor('vad-lab-device-processor', Processor);`;
    const url = URL.createObjectURL(new Blob([sourceCode], { type: "application/javascript" }));
    await context.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    if (!listening) return cleanup();
    source = context.createMediaStreamSource(stream);
    worklet = new AudioWorkletNode(context, "vad-lab-device-processor");
    worklet.port.onmessage = ({ data: { pcm, rms } }) => {
      if (!listening || !pcm?.length) return;
      if (rms >= vadThreshold) {
        silenceStart = null;
        if (!speaking) { speaking = true; speechStart = Date.now(); onSpeechStart(); }
        onData({ pcm });
      } else if (speaking) {
        onData({ pcm });
        silenceStart ??= Date.now();
        if (Date.now() - speechStart > vadMinRecordingTime && Date.now() - silenceStart > vadSilenceDuration) {
          speaking = false; speechStart = null; silenceStart = null; onSpeechEnd();
          if (!continuous) cleanup();
        }
      }
    };
    source.connect(worklet);
    worklet.connect(context.destination);
  } catch (error) {
    cleanup();
    onError(error instanceof Error ? error : new Error(String(error)));
    return null;
  }
  return stop;
}

function thresholdFromSensitivity(value) {
  // Matches the production canvas mapping: higher sensitivity means a lower RMS threshold.
  return 0.01 * Math.pow(10, (0.5 - Number(value) / 100) * 2);
}

function settingsSnapshot() {
  return {
    sensitivity: Number(settings.sensitivity.value),
    vadThreshold: thresholdFromSensitivity(settings.sensitivity.value),
    vadMinRecordingTime: Number(settings.speechDuration.value),
    vadSilenceDuration: Number(settings.silenceDuration.value),
    playbackGainDb: Number(settings.playbackGain.value),
  };
}

function updateSettingLabels() {
  const config = settingsSnapshot();
  outputs.sensitivity.textContent = `${config.sensitivity}% (RMS ${config.vadThreshold.toFixed(4)})`;
  outputs.speechDuration.textContent = `${config.vadMinRecordingTime} ms`;
  outputs.silenceDuration.textContent = `${config.vadSilenceDuration} ms`;
  outputs.playbackGain.textContent = `+${config.playbackGainDb} dB`;
}

function writeLog(message, type = "") {
  const line = document.createElement("div");
  line.className = type;
  line.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
  log.append(line);
  log.scrollTop = log.scrollHeight;
}

function setStatus(message, state = "") {
  status.textContent = message;
  status.className = `status ${state}`;
}

function setControlsRecording(recording) {
  recordButton.textContent = recording ? "Stop and prepare playback" : "Start microphone test";
  recordButton.classList.toggle("recording", recording);
  settings.sensitivity.disabled = recording;
  settings.speechDuration.disabled = recording;
  settings.silenceDuration.disabled = recording;
  settings.microphone.disabled = recording;
  refreshMicrophonesButton.disabled = recording;
}

function pcmToWav(chunks, gainDb) {
  const bytes = chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
  const wav = new ArrayBuffer(44 + bytes);
  const view = new DataView(wav);
  const write = (offset, text) => [...text].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  write(0, "RIFF");
  view.setUint32(4, 36 + bytes, true);
  write(8, "WAVEfmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, SAMPLE_RATE, true);
  view.setUint32(28, SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, bytes, true);
  const gain = Math.pow(10, gainDb / 20);
  let offset = 44;
  for (const chunk of chunks) {
    const source = new DataView(chunk);
    for (let index = 0; index < chunk.byteLength; index += 2) {
      const amplified = Math.round(source.getInt16(index, true) * gain);
      view.setInt16(offset + index, Math.max(-32_768, Math.min(32_767, amplified)), true);
    }
    offset += chunk.byteLength;
  }
  return new Blob([wav], { type: "audio/wav" });
}

function publishPlayback(announce = true) {
  if (!speechChunks.length) {
    setStatus("No speech was retained. Raise sensitivity or speak closer to the microphone.", "error");
    writeLog("No PCM data was received while VAD reported speech.");
    return;
  }
  if (playbackUrl) URL.revokeObjectURL(playbackUrl);
  const bytes = speechChunks.reduce((total, chunk) => total + chunk.byteLength, 0);
  const { playbackGainDb } = settingsSnapshot();
  playbackUrl = URL.createObjectURL(pcmToWav(speechChunks, playbackGainDb));
  playback.src = playbackUrl;
  playback.hidden = false;
  clearButton.disabled = false;
  setStatus(`Ready: ${(bytes / 32_000).toFixed(2)} seconds of VAD-filtered speech retained at +${playbackGainDb} dB playback gain.`);
  if (announce) writeLog(`Prepared ${(bytes / 1024).toFixed(1)} KiB of speech-only PCM at +${playbackGainDb} dB for playback.`);
}

function stopRecording() {
  const stop = stopListening;
  stopListening = null;
  if (typeof stop === "function") stop();
  if (speechActive) writeLog("Speech end (recording stopped).", "stop");
  speechActive = false;
  setControlsRecording(false);
  publishPlayback();
}

async function startRecording() {
  if (!navigator.mediaDevices?.getUserMedia) {
    setStatus("This browser does not provide microphone capture. Use HTTPS or localhost.", "error");
    return;
  }
  speechChunks = [];
  log.replaceChildren();
  const config = settingsSnapshot();
  setControlsRecording(true);
  setStatus("Requesting microphone access…", "recording");
  writeLog(`Starting VAD: threshold ${config.vadThreshold.toFixed(4)}, min speech ${config.vadMinRecordingTime} ms, silence ${config.vadSilenceDuration} ms.`);
  try {
    const selectedMicrophone = settings.microphone.selectedOptions[0]?.textContent || "system default microphone";
    stopListening = await listenForSpeechWithDevice({
      sampleRate: SAMPLE_RATE,
      deviceId: settings.microphone.value || undefined,
      vadThreshold: config.vadThreshold,
      vadMinRecordingTime: config.vadMinRecordingTime,
      vadSilenceDuration: config.vadSilenceDuration,
      continuous: true,
      onData: ({ pcm }) => {
        if (!speechActive || !pcm?.byteLength) return;
        // Copy each buffer: record-pcm may reuse its source buffer after this callback returns.
        speechChunks.push(pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength));
      },
      onSpeechStart: () => {
        if (speechActive) return;
        speechActive = true;
        setStatus("Listening — speech detected and retained.", "recording");
        writeLog("Speech start.", "start");
      },
      onSpeechEnd: () => {
        if (!speechActive) return;
        speechActive = false;
        setStatus("Listening — silence discarded; waiting for more speech.", "recording");
        writeLog("Speech end — subsequent silence is discarded.", "stop");
      },
      onError: (error) => {
        console.error("record-pcm VAD failed", error);
        stopListening = null;
        speechActive = false;
        setControlsRecording(false);
        setStatus(`Microphone/VAD error: ${error?.message || error}`, "error");
        writeLog(`Error: ${error?.message || error}`);
      },
    });
    await refreshMicrophones();
    setStatus(`Listening on ${selectedMicrophone} — speak to begin a VAD-filtered recording.`, "recording");
  } catch (error) {
    setControlsRecording(false);
    setStatus(`Unable to start microphone: ${error.message}`, "error");
    writeLog(`Start error: ${error.message}`);
  }
}

Object.entries(settings).forEach(([name, input]) => input.addEventListener("input", () => {
  updateSettingLabels();
  if (name === "playbackGain" && speechChunks.length) publishPlayback(false);
}));
recordButton.addEventListener("click", () => stopListening ? stopRecording() : startRecording());
refreshMicrophonesButton.addEventListener("click", () => refreshMicrophones(true));
settings.microphone.addEventListener("change", () => writeLog(`Selected microphone: ${settings.microphone.selectedOptions[0]?.textContent || "system default microphone"}.`));
navigator.mediaDevices?.addEventListener("devicechange", () => refreshMicrophones());
clearButton.addEventListener("click", () => {
  if (playbackUrl) URL.revokeObjectURL(playbackUrl);
  playbackUrl = null;
  speechChunks = [];
  playback.removeAttribute("src");
  playback.hidden = true;
  clearButton.disabled = true;
  setStatus("Recording cleared. Choose settings, then start another microphone test.");
  writeLog("Playback cleared.");
});
window.addEventListener("beforeunload", () => {
  if (typeof stopListening === "function") stopListening();
  if (playbackUrl) URL.revokeObjectURL(playbackUrl);
});
updateSettingLabels();
refreshMicrophones();
