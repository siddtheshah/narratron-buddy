/** Microphone capture with serialized VAD boundaries and Opus delivery. */
import { listenForSpeech } from "/static/js/device-aware-pcm.js";

const SAMPLE_RATE = 16000;
const DEFAULT_VAD_THRESHOLD = 0.01;
const DEFAULT_SILENCE_MS = 1200;
const DEFAULT_MIN_SPEECH_MS = 250;

let activeCapture = null;
let captureGeneration = 0;
let pendingStop = Promise.resolve();

function vadThreshold() {
  const threshold = Number(window.MIC_DETECT_THRESHOLD);
  return Number.isFinite(threshold) && threshold > 0
    ? threshold
    : DEFAULT_VAD_THRESHOLD;
}

function emitVadEvent(socket, phase, reason) {
  const detail = { reason, ts: new Date().toISOString() };
  window.dispatchEvent(new CustomEvent(phase === "start" ? "vadstart" : "vadstop", { detail }));
  window.dispatchEvent(new CustomEvent(`narratron:vad-${phase}`, { detail }));
  if (window.agentWs === socket && socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({
      type: phase === "start" ? "activity_start" : "activity_end",
      ...detail,
    }));
  }
}

function emitSpeechActivity(phase) {
  window.dispatchEvent(new CustomEvent(`narratron:speech-${phase}`));
}

function createCapture(handler, socket) {
  let accepting = true;
  let closed = false;
  let speaking = false;
  let timestampUs = 0;
  let stopListening = null;
  let operations = Promise.resolve();

  const encoder = new AudioEncoder({
    output: (chunk) => {
      // A draining encoder belongs to its original socket, even after disconnect.
      if (closed || window.agentWs !== socket || socket?.readyState !== WebSocket.OPEN) return;
      const buffer = new Uint8Array(chunk.byteLength);
      chunk.copyTo(buffer);
      handler(buffer.buffer);
    },
    error: (error) => {
      console.error("[AudioRecorder] WebCodecs Opus encoding error:", error);
    },
  });
  try {
    encoder.configure({ codec: "opus", sampleRate: SAMPLE_RATE, numberOfChannels: 1, bitrate: 24000 });
  } catch (error) {
    encoder.close();
    throw error;
  }

  const enqueue = (operation) => {
    operations = operations.then(operation).catch((error) => {
      console.error("[AudioRecorder] Audio delivery failed:", error);
    });
    return operations;
  };

  const finishSpeech = async () => {
    if (!speaking) return;
    // Keep the next start and its PCM queued until the previous Opus output
    // and end boundary have both reached the socket.
    if (encoder.state === "configured") {
      try {
        await encoder.flush();
      } catch (error) {
        console.warn("[AudioRecorder] Could not flush final Opus audio packet:", error);
      }
    }
    speaking = false;
    emitVadEvent(socket, "stop", "speech_end");
    emitSpeechActivity("end");
  };

  return {
    options: {
      deviceId: window.NARRATRON_MIC_DEVICE_ID || undefined,
      sampleRate: SAMPLE_RATE,
      vadThreshold: vadThreshold(),
      vadSilenceDuration: DEFAULT_SILENCE_MS,
      vadMinRecordingTime: DEFAULT_MIN_SPEECH_MS,
      continuous: true,
      onSpeechStart: () => {
        if (!accepting) return;
        enqueue(() => {
          if (speaking) return;
          speaking = true;
          emitVadEvent(socket, "start", "speech_start");
          emitSpeechActivity("start");
        });
      },
      onData: ({ float32 }) => {
        if (!accepting || !float32) return;
        enqueue(() => {
          if (!speaking || encoder.state !== "configured") return;
          const audioData = new AudioData({
            format: "f32-planar",
            sampleRate: SAMPLE_RATE,
            numberOfFrames: float32.length,
            numberOfChannels: 1,
            timestamp: timestampUs,
            data: float32,
          });
          timestampUs += Math.round((float32.length / SAMPLE_RATE) * 1_000_000);
          try {
            encoder.encode(audioData);
          } finally {
            audioData.close();
          }
        });
      },
      onSpeechEnd: () => {
        if (accepting) enqueue(finishSpeech);
      },
      onError: (error) => {
        if (!accepting) return;
        console.error("[AudioRecorder] microphone capture failed:", error);
        enqueue(finishSpeech);
      },
    },
    installStop(stop) {
      // Initialization may finish after this capture was already stopped.
      if (accepting) stopListening = stop;
      else stop();
    },
    stop() {
      accepting = false;
      if (stopListening) stopListening();
      stopListening = null;
      return enqueue(finishSpeech).finally(() => {
        closed = true;
        if (encoder.state !== "closed") encoder.close();
      });
    },
  };
}

/** Starts microphone capture gated by RMS VAD and encoded to Opus packets. */
export async function startAudioRecorderWorklet(handler) {
  const stopping = stopMicrophone();
  const generation = ++captureGeneration;
  await stopping;
  if (generation !== captureGeneration) return [null, null, null, false];

  if (typeof window.AudioEncoder !== "function") {
    throw new Error("Your browser is out of date and needs Opus audio support. Please update your browser and try again.");
  }

  const capture = createCapture(handler, window.agentWs);
  activeCapture = capture;
  try {
    capture.installStop(await listenForSpeech(capture.options));
  } catch (error) {
    if (activeCapture === capture) await stopMicrophone();
    throw error;
  }
  if (generation !== captureGeneration) return [null, null, null, false];
  return [null, null, null, true];
}

/** Stops capture immediately, then drains its encoder before another capture starts. */
export function stopMicrophone() {
  captureGeneration += 1;
  const capture = activeCapture;
  activeCapture = null;
  if (capture) pendingStop = capture.stop();
  return pendingStop;
}
