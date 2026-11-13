/** Microphone capture with serialized VAD boundaries and Opus delivery. */
import {
  listenForSpeech,
  type ListenForSpeechOptions,
  type SpeechDataPacket,
} from '/static/generated/canvas/device-aware-pcm.js';

export type OpusPacketHandler = (opusBuffer: ArrayBuffer) => void;
export type AudioRecorderStartResult = readonly [null, null, null, boolean];
export type VadPhase = 'start' | 'stop';
export type SpeechActivityPhase = 'start' | 'end';

export interface VadEventDetail {
  readonly reason: string;
  readonly ts: string;
}

export interface AgentSocket {
  readonly readyState: number;
  send(data: string | ArrayBufferLike | Blob | ArrayBufferView): void;
}

interface CaptureSession {
  readonly options: ListenForSpeechOptions;
  setTransmissionEnabled(enabled: boolean): void;
  installStop(stop: () => void): void;
  stop(): Promise<void>;
}

declare global {
  interface Window {
    agentWs?: AgentSocket | null;
    MIC_DETECT_THRESHOLD?: number | string;
    NARRATRON_MIC_DEVICE_ID?: string;
    AudioEncoder?: typeof globalThis.AudioEncoder;
  }
}

const SAMPLE_RATE = 16000;
const DEFAULT_VAD_THRESHOLD = 0.01;
const DEFAULT_SILENCE_MS = 1200;
const DEFAULT_MIN_SPEECH_MS = 250;

let activeCapture: CaptureSession | null = null;
let captureGeneration = 0;
let pendingStop: Promise<void> = Promise.resolve();
let transmissionEnabled = true;

function vadThreshold(): number {
  const threshold = Number(window.MIC_DETECT_THRESHOLD);
  return Number.isFinite(threshold) && threshold > 0
    ? threshold
    : DEFAULT_VAD_THRESHOLD;
}

function emitVadEvent(
  socket: AgentSocket | null | undefined,
  phase: VadPhase,
  reason: string,
): void {
  const detail: VadEventDetail = { reason, ts: new Date().toISOString() };
  window.dispatchEvent(
    new CustomEvent<VadEventDetail>(phase === 'start' ? 'vadstart' : 'vadstop', { detail }),
  );
  window.dispatchEvent(new CustomEvent<VadEventDetail>(`narratron:vad-${phase}`, { detail }));
  if (window.agentWs === socket && socket?.readyState === WebSocket.OPEN) {
    socket.send(
      JSON.stringify({
        type: phase === 'start' ? 'activity_start' : 'activity_end',
        ...detail,
      }),
    );
  }
}

function emitSpeechActivity(phase: SpeechActivityPhase): void {
  window.dispatchEvent(new CustomEvent(`narratron:speech-${phase}`));
}

function createCapture(
  handler: OpusPacketHandler,
  socket: AgentSocket | null | undefined,
): CaptureSession {
  let accepting = true;
  let transmitting = transmissionEnabled;
  let closed = false;
  let speaking = false;
  let timestampUs = 0;
  let stopListening: (() => void) | null = null;
  let operations: Promise<void> = Promise.resolve();

  const encoder = new AudioEncoder({
    output: (chunk: EncodedAudioChunk): void => {
      // A draining encoder belongs to its original socket, even after disconnect.
      if (closed || window.agentWs !== socket || socket?.readyState !== WebSocket.OPEN) return;
      const buffer = new Uint8Array(chunk.byteLength);
      chunk.copyTo(buffer);
      handler(buffer.buffer);
    },
    error: (error: DOMException): void => {
      console.error('[AudioRecorder] WebCodecs Opus encoding error:', error);
    },
  });
  try {
    encoder.configure({
      codec: 'opus',
      sampleRate: SAMPLE_RATE,
      numberOfChannels: 1,
      bitrate: 24000,
    });
  } catch (error: unknown) {
    encoder.close();
    throw error;
  }

  const enqueue = (operation: () => void | Promise<void>): Promise<void> => {
    operations = operations.then(operation).catch((error: unknown) => {
      console.error('[AudioRecorder] Audio delivery failed:', error);
    });
    return operations;
  };

  const finishSpeech = async (): Promise<void> => {
    if (!speaking) return;
    // Keep the next start and its PCM queued until the previous Opus output
    // and end boundary have both reached the socket.
    if (encoder.state === 'configured') {
      try {
        await encoder.flush();
      } catch (error: unknown) {
        console.warn('[AudioRecorder] Could not flush final Opus audio packet:', error);
      }
    }
    speaking = false;
    emitVadEvent(socket, 'stop', 'speech_end');
    emitSpeechActivity('end');
  };

  return {
    setTransmissionEnabled(enabled: boolean): void {
      if (transmitting === enabled) return;
      transmitting = enabled;
      if (!enabled) {
        void enqueue(finishSpeech);
      }
    },
    options: {
      deviceId: window.NARRATRON_MIC_DEVICE_ID || undefined,
      sampleRate: SAMPLE_RATE,
      vadThreshold: vadThreshold(),
      vadSilenceDuration: DEFAULT_SILENCE_MS,
      vadMinRecordingTime: DEFAULT_MIN_SPEECH_MS,
      continuous: true,
      onSpeechStart: (): void => {
        if (!accepting || !transmitting) return;
        void enqueue(() => {
          if (speaking) return;
          speaking = true;
          emitVadEvent(socket, 'start', 'speech_start');
          emitSpeechActivity('start');
        });
      },
      onData: ({ float32 }: SpeechDataPacket): void => {
        if (!accepting || !transmitting || !float32) return;
        void enqueue(() => {
          if (encoder.state !== 'configured') return;
          // The VAD may already be speaking when push-to-talk is pressed.
          if (!speaking) {
            speaking = true;
            emitVadEvent(socket, 'start', 'speech_start');
            emitSpeechActivity('start');
          }
          const audioData = new AudioData({
            format: 'f32-planar',
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
      onSpeechEnd: (): void => {
        if (accepting && transmitting) {
          void enqueue(finishSpeech);
        }
      },
      onError: (error: Error): void => {
        if (!accepting) return;
        console.error('[AudioRecorder] microphone capture failed:', error);
        void enqueue(finishSpeech);
      },
    },
    installStop(stop: () => void): void {
      // Initialization may finish after this capture was already stopped.
      if (accepting) stopListening = stop;
      else stop();
    },
    stop(): Promise<void> {
      accepting = false;
      if (stopListening) stopListening();
      stopListening = null;
      return enqueue(finishSpeech).finally(() => {
        closed = true;
        if (encoder.state !== 'closed') encoder.close();
      });
    },
  };
}

/** Starts microphone capture gated by RMS VAD and encoded to Opus packets. */
export async function startAudioRecorderWorklet(
  handler: OpusPacketHandler,
  initiallyEnabled = true,
): Promise<AudioRecorderStartResult> {
  const stopping = stopMicrophone();
  transmissionEnabled = initiallyEnabled;
  const generation = ++captureGeneration;
  await stopping;
  if (generation !== captureGeneration) return [null, null, null, false];

  if (typeof window.AudioEncoder !== 'function') {
    throw new Error(
      'Your browser is out of date and needs Opus audio support. Please update your browser and try again.',
    );
  }

  const capture = createCapture(handler, window.agentWs);
  activeCapture = capture;
  try {
    capture.installStop(await listenForSpeech(capture.options));
  } catch (error: unknown) {
    if (activeCapture === capture) await stopMicrophone();
    throw error;
  }
  if (generation !== captureGeneration) return [null, null, null, false];
  return [null, null, null, true];
}

/** Mutes delivery without closing the device or its audio worklet. */
export function setMicrophoneTransmissionEnabled(enabled: boolean): void {
  transmissionEnabled = enabled;
  activeCapture?.setTransmissionEnabled(enabled);
}

/** Stops capture immediately, then drains its encoder before another capture starts. */
export function stopMicrophone(): Promise<void> {
  captureGeneration += 1;
  const capture = activeCapture;
  activeCapture = null;
  if (capture) pendingStop = capture.stop();
  return pendingStop;
}
