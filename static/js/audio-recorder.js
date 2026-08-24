/**
 * Microphone capture for the live agent with Opus compression.
 *
 * Captures microphone audio, gates with RMS VAD, compresses into Opus packets
 * via WebCodecs AudioEncoder, and delivers compressed packets to the WebSocket handler.
 */
import { listenForSpeech } from "/static/js/device-aware-pcm.js";

const SAMPLE_RATE = 16000;
const DEFAULT_VAD_THRESHOLD = 0.01;
const DEFAULT_SILENCE_MS = 1200;
const DEFAULT_MIN_SPEECH_MS = 250;

let stopListening = null;
let speechActive = false;
let audioRecorderHandler = null;
let audioEncoder = null;
let audioTimestampUs = 0;

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

function initAudioEncoder(handler) {
  audioTimestampUs = 0;
  if (typeof window.AudioEncoder !== "function") {
    console.error("[AudioRecorder] AudioEncoder is not supported in this browser.");
    return null;
  }
  try {
    const encoder = new AudioEncoder({
      output: (chunk) => {
        const buffer = new Uint8Array(chunk.byteLength);
        chunk.copyTo(buffer);
        if (typeof handler === "function") {
          handler(buffer.buffer);
        }
      },
      error: (err) => {
        console.error("[AudioRecorder] WebCodecs Opus encoding error:", err);
      },
    });
    encoder.configure({
      codec: "opus",
      sampleRate: SAMPLE_RATE,
      numberOfChannels: 1,
      bitrate: 24000,
    });
    return encoder;
  } catch (err) {
    console.error("[AudioRecorder] Failed to initialize AudioEncoder:", err);
    return null;
  }
}

function finishSpeech() {
  if (!speechActive) return;
  emitVadEvent("stop", "speech_end");
  speechActive = false;
  emitSpeechActivity("end");
}

/**
 * Starts microphone capture gated by RMS VAD and encoded to Opus packets.
 */
export async function startAudioRecorderWorklet(handler) {
  stopMicrophone();

  speechActive = false;
  audioRecorderHandler = handler;
  audioEncoder = initAudioEncoder(handler);

  stopListening = await listenForSpeech({
    deviceId: window.NARRATRON_MIC_DEVICE_ID || undefined,
    sampleRate: SAMPLE_RATE,
    vadThreshold: vadThreshold(),
    vadSilenceDuration: DEFAULT_SILENCE_MS,
    vadMinRecordingTime: DEFAULT_MIN_SPEECH_MS,
    continuous: true,
    onData: ({ float32 }) => {
      if (!speechActive) return;
      if (audioEncoder && audioEncoder.state === "configured" && float32) {
        const audioData = new AudioData({
          format: "f32-planar",
          sampleRate: SAMPLE_RATE,
          numberOfFrames: float32.length,
          numberOfChannels: 1,
          timestamp: audioTimestampUs,
          data: float32,
        });
        audioTimestampUs += Math.round((float32.length / SAMPLE_RATE) * 1_000_000);
        audioEncoder.encode(audioData);
        audioData.close();
      }
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
      console.error("[AudioRecorder] microphone capture failed:", error);
      finishSpeech();
    },
  });

  return [null, null, null];
}

/** Stops capture and closes the Opus encoder. */
export function stopMicrophone() {
  const stop = stopListening;
  stopListening = null;
  if (typeof stop === "function") stop();
  finishSpeech();
  if (audioEncoder) {
    try {
      if (audioEncoder.state === "configured") {
        audioEncoder.flush().catch(() => {});
        audioEncoder.close();
      }
    } catch (e) {}
    audioEncoder = null;
  }
  audioRecorderHandler = null;
}
