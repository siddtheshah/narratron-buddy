/** A small record-pcm-compatible VAD capture helper with explicit device selection. */
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

interface WorkletSpeechMessage {
  readonly pcm?: Uint8Array;
  readonly float32?: Float32Array<ArrayBuffer>;
  readonly rms?: number;
}

const PROCESSOR_WORKLET_SOURCE =
  "class P extends AudioWorkletProcessor { constructor(){super();this.b=new Float32Array(480);this.i=0;this.phase=0;this.step=sampleRate/16000;this.last=0} emit(){let q=0;for(const x of this.b)q+=x*x;const p=new Int16Array(this.b.length);for(let j=0;j<this.b.length;j++){const x=Math.max(-1,Math.min(1,this.b[j]));p[j]=x<0?x*32768:x*32767}this.port.postMessage({pcm:new Uint8Array(p.buffer),float32:this.b.slice(),rms:Math.sqrt(q/this.b.length)});this.i=0} process(inputs){const a=inputs[0]?.[0];if(!a)return true;if(sampleRate===16000){for(const s of a){this.b[this.i++]=s;if(this.i===this.b.length)this.emit()}return true}for(let n=0;n<a.length;n++){const current=a[n];while(this.phase<=n){const previous=n?a[n-1]:this.last;const t=this.phase-(n-1);this.b[this.i++]=previous+(current-previous)*t;if(this.i===this.b.length)this.emit();this.phase+=this.step}this.last=current}this.phase-=a.length;return true} } registerProcessor('narratron-device-pcm',P);";

/** Captures 16 kHz PCM speech segments gated by RMS VAD from the selected microphone. */
export async function listenForSpeech(options: ListenForSpeechOptions): Promise<() => void> {
  const {
    deviceId,
    onData,
    onError = (): void => {},
    onSpeechStart = (): void => {},
    onSpeechEnd = (): void => {},
    sampleRate = 16000,
    vadThreshold = 0.01,
    vadSilenceDuration = 600,
    vadMinRecordingTime = 180,
    continuous = true,
  } = options;

  let listening = true;
  let speaking = false;
  let context: AudioContext | undefined;
  let stream: MediaStream | undefined;
  let source: MediaStreamAudioSourceNode | undefined;
  let worklet: AudioWorkletNode | undefined;
  let silenceStart: number | null = null;
  let speechStart: number | null = null;

  const cleanup = (): void => {
    listening = false;
    worklet?.disconnect();
    source?.disconnect();
    if (context && context.state !== 'closed') {
      void context.close();
    }
    stream?.getTracks().forEach((track: MediaStreamTrack) => track.stop());
  };

  const stop = (): void => {
    if (!listening) return;
    if (speaking) onSpeechEnd();
    cleanup();
  };

  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
        channelCount: 1,
        sampleRate: { ideal: sampleRate },
        echoCancellation: true,
        noiseSuppression: true,
      },
    });
    if (!listening) {
      cleanup();
      return stop;
    }

    context = new AudioContext({ sampleRate });
    // iOS Safari often keeps the hardware rate (44.1/48 kHz) despite the
    // requested 16 kHz context. Resample before emitting packets so their
    // data and declared rate always agree.
    const url = URL.createObjectURL(
      new Blob([PROCESSOR_WORKLET_SOURCE], { type: 'application/javascript' }),
    );
    await context.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    if (!listening) {
      cleanup();
      return stop;
    }

    source = context.createMediaStreamSource(stream);
    worklet = new AudioWorkletNode(context, 'narratron-device-pcm');
    worklet.port.onmessage = (event: MessageEvent<WorkletSpeechMessage>): void => {
      const payload = event.data;
      if (!listening || !payload?.pcm?.length || !payload.float32 || payload.rms === undefined) {
        return;
      }
      const { pcm, float32, rms } = payload;
      if (rms >= vadThreshold) {
        silenceStart = null;
        if (!speaking) {
          speaking = true;
          speechStart = Date.now();
          onSpeechStart();
        }
        onData({ pcm, float32, rms });
        return;
      }
      if (!speaking) return;
      onData({ pcm, float32, rms });
      silenceStart ??= Date.now();
      if (
        speechStart !== null &&
        Date.now() - speechStart > vadMinRecordingTime &&
        Date.now() - silenceStart > vadSilenceDuration
      ) {
        speaking = false;
        speechStart = null;
        silenceStart = null;
        onSpeechEnd();
        if (!continuous) cleanup();
      }
    };
    source.connect(worklet);
    worklet.connect(context.destination);
    return stop;
  } catch (error: unknown) {
    cleanup();
    const normalizedError = error instanceof Error ? error : new Error(String(error));
    onError(normalizedError);
    throw normalizedError;
  }
}
