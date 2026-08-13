/**
 * Microphone capture for the live agent.
 *
 * `record-pcm` owns the complete capture/VAD pipeline.  Keeping capture in one
 * place prevents duplicate microphone streams and duplicate PCM uploads.
 */
import { listenForSpeech } from "/static/js/device-aware-pcm.js";

const SAMPLE_RATE = 16000;
const PCM_BYTES_PER_SAMPLE = 2;
// Match the Live API's 30 ms PCM frames.  Audio must reach the model while
// the speaker is still talking; turn boundaries remain VAD-driven below.
const AUDIO_CHUNK_DURATION_SECONDS = 0.03;
const AUDIO_CHUNK_BYTES = SAMPLE_RATE * PCM_BYTES_PER_SAMPLE * AUDIO_CHUNK_DURATION_SECONDS;
const DEFAULT_VAD_THRESHOLD = 0.01;
const DEFAULT_SILENCE_MS = 1200;
const DEFAULT_MIN_SPEECH_MS = 250;

let stopListening = null;
let speechActive = false;
let audioRecorderHandler = null;
let pendingPcmBuffers = [];
let pendingPcmBytes = 0;

function vadThreshold() {
  const threshold = Number(window.MIC_DETECT_THRESHOLD);
  return Number.isFinite(threshold) && threshold > 0
    ? threshold
    : DEFAULT_VAD_THRESHOLD;
}

function sendControlMessage(type, reason) {
  const ws = window.agentWs;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type, reason, ts: new Date().toISOString() }));
  }
}

function emitVadEvent(phase, reason) {
  // These browser events make VAD state available to UI integrations and tests.
  const detail = { reason, ts: new Date().toISOString() };
  window.dispatchEvent(new CustomEvent(phase === "start" ? "vadstart" : "vadstop", { detail }));
  window.dispatchEvent(new CustomEvent(`narratron:vad-${phase}`, { detail }));

  // The live-agent protocol represents VAD boundaries as activity boundaries.
  sendControlMessage(phase === "start" ? "activity_start" : "activity_end", reason);
}

function emitSpeechActivity(phase) {
  // Unlike VAD chunk events, this maps one-to-one with the complete utterance.
  window.dispatchEvent(new CustomEvent(`narratron:speech-${phase}`));
}

function flushAudioChunk() {
  if (pendingPcmBytes === 0) return;

  const chunk = new Uint8Array(pendingPcmBytes);
  let offset = 0;
  for (const buffer of pendingPcmBuffers) {
    chunk.set(buffer, offset);
    offset += buffer.byteLength;
  }

  pendingPcmBuffers = [];
  pendingPcmBytes = 0;
  if (typeof audioRecorderHandler === "function") audioRecorderHandler(chunk.buffer);
}

function appendPcm(pcm) {
  const source = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  let offset = 0;

  while (offset < source.byteLength) {
    const bytesToCopy = Math.min(AUDIO_CHUNK_BYTES - pendingPcmBytes, source.byteLength - offset);
    pendingPcmBuffers.push(source.slice(offset, offset + bytesToCopy));
    pendingPcmBytes += bytesToCopy;
    offset += bytesToCopy;

    // Flushing a transport frame is not the end of a user turn.  Sending an
    // activity_end here followed by a new activity_start for continuous
    // speech violates the Live API's manual-VAD sequencing precondition.
    if (pendingPcmBytes === AUDIO_CHUNK_BYTES) flushAudioChunk();
  }
}

function finishSpeech() {
  if (!speechActive) return;
  flushAudioChunk();
  emitVadEvent("stop", "speech_end");
  speechActive = false;
  emitSpeechActivity("end");
}

/**
 * Starts one PCM stream, gated by record-pcm's RMS VAD.
 *
 * The return shape intentionally matches the previous canvas integration.
 */
export async function startAudioRecorderWorklet(handler) {
  stopMicrophone();

  speechActive = false;
  pendingPcmBuffers = [];
  pendingPcmBytes = 0;
  audioRecorderHandler = handler;
  stopListening = await listenForSpeech({
    deviceId: window.NARRATRON_MIC_DEVICE_ID || undefined,
    sampleRate: SAMPLE_RATE,
    vadThreshold: vadThreshold(),
    vadSilenceDuration: DEFAULT_SILENCE_MS,
    vadMinRecordingTime: DEFAULT_MIN_SPEECH_MS,
    continuous: true,
    onData: ({ pcm }) => {
      if (!speechActive) return;
      // record-pcm supplies little-endian, signed 16-bit mono PCM bytes.
      appendPcm(pcm);
    },
    onSpeechStart: () => {
      if (speechActive) return;
      speechActive = true;
      emitVadEvent("start", "speech_start");
      emitSpeechActivity("start");
    },
    onSpeechEnd: () => {
      finishSpeech();
    },
    onError: (error) => {
      console.error("[AudioRecorder] record-pcm microphone capture failed:", error);
      finishSpeech();
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
  finishSpeech();
  audioRecorderHandler = null;
}
