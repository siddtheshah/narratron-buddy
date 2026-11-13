// Typed boundary for the legacy browser VAD capture helper.
export interface SpeechDataPacket {
  readonly pcm: Uint8Array;
  readonly float32: Float32Array<ArrayBuffer>;
  readonly rms: number;
}

export interface ListenForSpeechOptions {
  readonly deviceId?: string;
  readonly sampleRate?: number;
  readonly vadThreshold?: number;
  readonly vadSilenceDuration?: number;
  readonly vadMinRecordingTime?: number;
  readonly continuous?: boolean;
  readonly onSpeechStart?: () => void;
  readonly onData: (packet: SpeechDataPacket) => void;
  readonly onSpeechEnd?: () => void;
  readonly onError?: (error: Error) => void;
}

export function listenForSpeech(options: ListenForSpeechOptions): Promise<() => void>;
