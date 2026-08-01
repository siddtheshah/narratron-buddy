/**
 * Microphone capture for the live agent.
 *
 * `record-pcm` owns the complete capture/VAD pipeline.  Keeping capture in one
 * place prevents duplicate microphone streams and duplicate PCM uploads.
 */
import { listenForSpeech } from "https://cdn.jsdelivr.net/npm/record-pcm@1.1.3/dist/index.mjs";

const SAMPLE_RATE = 16000;
const DEFAULT_VAD_THRESHOLD = 0.01;
const DEFAULT_SILENCE_MS = 600;
const DEFAULT_MIN_SPEECH_MS = 180;

let stopListening = null;
let speechActive = false;

function vadThreshold() {
  const threshold = Number(window.MIC_DETECT_THRESHOLD);
  return Number.isFinite(threshold) && threshold > 0
    ? threshold
    : DEFAULT_VAD_THRESHOLD;
}

function sendControlMessage(type) {
  const ws = window.agentWs;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type, ts: new Date().toISOString() }));
  }
}

function emitVadEvent(phase) {
  // These browser events make VAD state available to UI integrations and tests.
  const detail = { ts: new Date().toISOString() };
  window.dispatchEvent(new CustomEvent(phase === "start" ? "vadstart" : "vadstop", { detail }));
  window.dispatchEvent(new CustomEvent(`narratron:vad-${phase}`, { detail }));

  // The live-agent protocol represents VAD boundaries as activity boundaries.
  sendControlMessage(phase === "start" ? "activity_start" : "activity_end");
}

/**
 * Starts one PCM stream, gated by record-pcm's RMS VAD.
 *
 * The return shape intentionally matches the previous canvas integration.
 */
export async function startAudioRecorderWorklet(audioRecorderHandler) {
  stopMicrophone();

  speechActive = false;
  stopListening = listenForSpeech({
    sampleRate: SAMPLE_RATE,
    vadThreshold: vadThreshold(),
    vadSilenceDuration: DEFAULT_SILENCE_MS,
    vadMinRecordingTime: DEFAULT_MIN_SPEECH_MS,
    continuous: true,
    onData: ({ pcm }) => {
      if (!speechActive || typeof audioRecorderHandler !== "function") return;
      // record-pcm supplies little-endian, signed 16-bit mono PCM bytes.
      audioRecorderHandler(pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength));
    },
    onSpeechStart: () => {
      if (speechActive) return;
      speechActive = true;
      emitVadEvent("start");
    },
    onSpeechEnd: () => {
      if (!speechActive) return;
      speechActive = false;
      emitVadEvent("stop");
    },
    onError: (error) => {
      console.error("[AudioRecorder] record-pcm microphone capture failed:", error);
      if (speechActive) {
        speechActive = false;
        emitVadEvent("stop");
      }
    },
  });

  // record-pcm opens the stream asynchronously.  There are no public worklet,
  // context, or MediaStream handles to expose; the canvas only stores these for
  // cleanup and calls stopMicrophone below.
  return [null, null, null];
}

/** Stops capture and emits the final VAD boundary when speech was active. */
export function stopMicrophone() {
  const stop = stopListening;
  stopListening = null;
  if (typeof stop === "function") stop();

  if (speechActive) {
    speechActive = false;
    emitVadEvent("stop");
  }
}
