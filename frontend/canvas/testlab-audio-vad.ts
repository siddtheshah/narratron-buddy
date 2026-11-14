/** Microphone VAD Lab controller: device selection, RMS VAD capture, and WAV playback. */

export const SAMPLE_RATE = 16_000;

export interface VadSpeechPacket {
  readonly pcm: Uint8Array;
}

export interface ListenForSpeechDeviceOptions {
  readonly deviceId?: string;
  readonly sampleRate: number;
  readonly vadThreshold: number;
  readonly vadSilenceDuration: number;
  readonly vadMinRecordingTime: number;
  readonly continuous: boolean;
  readonly onData: (packet: VadSpeechPacket) => void;
  readonly onError: (error: Error) => void;
  readonly onSpeechStart: () => void;
  readonly onSpeechEnd: () => void;
}

export interface VadSettingsSnapshot {
  readonly sensitivity: number;
  readonly vadThreshold: number;
  readonly vadMinRecordingTime: number;
  readonly vadSilenceDuration: number;
  readonly playbackGainDb: number;
}

export interface AudioVadLabController {
  refreshMicrophones(requestPermission?: boolean): Promise<void>;
  startRecording(): Promise<void>;
  stopRecording(): void;
  destroy(): void;
}

interface WorkletSpeechMessage {
  readonly pcm?: Uint8Array;
  readonly rms?: number;
}

const PROCESSOR_WORKLET_SOURCE = `class Processor extends AudioWorkletProcessor {
  constructor() { super(); this.buffer = new Float32Array(4096); this.index = 0; }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const sample of input) {
      this.buffer[this.index++] = sample;
      if (this.index === this.buffer.length) {
        let sum = 0;
        for (const value of this.buffer) sum += value * value;
        const pcm = new Int16Array(this.buffer.length);
        for (let i = 0; i < this.buffer.length; i++) {
          const value = Math.max(-1, Math.min(1, this.buffer[i]));
          pcm[i] = value < 0 ? value * 0x8000 : value * 0x7fff;
        }
        this.port.postMessage({ pcm: new Uint8Array(pcm.buffer), rms: Math.sqrt(sum / this.buffer.length) });
        this.index = 0;
      }
    }
    return true;
  }
}
registerProcessor('vad-lab-device-processor', Processor);`;

/** Maps a 0–100 sensitivity slider value to the production canvas RMS VAD threshold. */
export function thresholdFromSensitivity(value: number | string): number {
  return 0.01 * Math.pow(10, (0.5 - Number(value) / 100) * 2);
}

/** Encodes signed 16-bit mono PCM chunks into a gain-adjusted WAV Blob at SAMPLE_RATE. */
export function pcmToWav(chunks: readonly ArrayBuffer[], gainDb: number): Blob {
  const bytes = chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
  const wav = new ArrayBuffer(44 + bytes);
  const view = new DataView(wav);
  const write = (offset: number, text: string): void => {
    [...text].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  };
  write(0, 'RIFF');
  view.setUint32(4, 36 + bytes, true);
  write(8, 'WAVEfmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, SAMPLE_RATE, true);
  view.setUint32(28, SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, 'data');
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
  return new Blob([wav], { type: 'audio/wav' });
}

/** Captures 16 kHz PCM speech segments gated by RMS VAD from the selected microphone. */
export async function listenForSpeechWithDevice(
  options: ListenForSpeechDeviceOptions,
): Promise<(() => void) | null> {
  const {
    deviceId,
    onData,
    onError,
    onSpeechStart,
    onSpeechEnd,
    sampleRate,
    vadThreshold,
    vadSilenceDuration,
    vadMinRecordingTime,
    continuous,
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
      return null;
    }
    context = new AudioContext({ sampleRate });
    const url = URL.createObjectURL(
      new Blob([PROCESSOR_WORKLET_SOURCE], { type: 'application/javascript' }),
    );
    await context.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    if (!listening) {
      cleanup();
      return null;
    }
    source = context.createMediaStreamSource(stream);
    worklet = new AudioWorkletNode(context, 'vad-lab-device-processor');
    worklet.port.onmessage = (event: MessageEvent<WorkletSpeechMessage>): void => {
      const payload = event.data;
      if (!listening || !payload?.pcm?.length || payload.rms === undefined) return;
      const { pcm, rms } = payload;
      if (rms >= vadThreshold) {
        silenceStart = null;
        if (!speaking) {
          speaking = true;
          speechStart = Date.now();
          onSpeechStart();
        }
        onData({ pcm });
      } else if (speaking) {
        onData({ pcm });
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
      }
    };
    source.connect(worklet);
    worklet.connect(context.destination);
  } catch (error: unknown) {
    cleanup();
    onError(error instanceof Error ? error : new Error(String(error)));
    return null;
  }
  return stop;
}

/** Wires the Audio VAD Lab DOM controls, device list, recording lifecycle, and playback. */
export function initAudioVadLab(): AudioVadLabController | null {
  const rawMicrophone = document.querySelector('#microphone');
  const rawSensitivity = document.querySelector('#sensitivity');
  const rawSpeechDuration = document.querySelector('#speech-duration');
  const rawSilenceDuration = document.querySelector('#silence-duration');
  const rawPlaybackGain = document.querySelector('#playback-gain');

  const rawSensitivityValue = document.querySelector('#sensitivity-value');
  const rawSpeechDurationValue = document.querySelector('#speech-duration-value');
  const rawSilenceDurationValue = document.querySelector('#silence-duration-value');
  const rawPlaybackGainValue = document.querySelector('#playback-gain-value');

  const rawRecordButton = document.querySelector('#record-button');
  const rawRefreshMicrophonesButton = document.querySelector('#refresh-microphones');
  const rawClearButton = document.querySelector('#clear-button');
  const rawStatus = document.querySelector('#status');
  const rawPlayback = document.querySelector('#playback');
  const rawLog = document.querySelector('#log');
  const rawMicrophoneHint = document.querySelector('#microphone-hint');

  if (
    !(rawMicrophone instanceof HTMLSelectElement) ||
    !(rawSensitivity instanceof HTMLInputElement) ||
    !(rawSpeechDuration instanceof HTMLInputElement) ||
    !(rawSilenceDuration instanceof HTMLInputElement) ||
    !(rawPlaybackGain instanceof HTMLInputElement) ||
    !(rawSensitivityValue instanceof HTMLElement) ||
    !(rawSpeechDurationValue instanceof HTMLElement) ||
    !(rawSilenceDurationValue instanceof HTMLElement) ||
    !(rawPlaybackGainValue instanceof HTMLElement) ||
    !(rawRecordButton instanceof HTMLButtonElement) ||
    !(rawRefreshMicrophonesButton instanceof HTMLButtonElement) ||
    !(rawClearButton instanceof HTMLButtonElement) ||
    !(rawStatus instanceof HTMLElement) ||
    !(rawPlayback instanceof HTMLAudioElement) ||
    !(rawLog instanceof HTMLElement) ||
    !(rawMicrophoneHint instanceof HTMLElement)
  ) {
    return null;
  }

  const microphone = rawMicrophone;
  const sensitivity = rawSensitivity;
  const speechDuration = rawSpeechDuration;
  const silenceDuration = rawSilenceDuration;
  const playbackGain = rawPlaybackGain;

  const sensitivityValue = rawSensitivityValue;
  const speechDurationValue = rawSpeechDurationValue;
  const silenceDurationValue = rawSilenceDurationValue;
  const playbackGainValue = rawPlaybackGainValue;

  const recordButton = rawRecordButton;
  const refreshMicrophonesButton = rawRefreshMicrophonesButton;
  const clearButton = rawClearButton;
  const status = rawStatus;
  const playback = rawPlayback;
  const log = rawLog;
  const microphoneHint = rawMicrophoneHint;

  let stopListening: (() => void) | null = null;
  let speechActive = false;
  let speechChunks: ArrayBuffer[] = [];
  let playbackUrl: string | null = null;

  function settingsSnapshot(): VadSettingsSnapshot {
    return {
      sensitivity: Number(sensitivity.value),
      vadThreshold: thresholdFromSensitivity(sensitivity.value),
      vadMinRecordingTime: Number(speechDuration.value),
      vadSilenceDuration: Number(silenceDuration.value),
      playbackGainDb: Number(playbackGain.value),
    };
  }

  function updateSettingLabels(): void {
    const config = settingsSnapshot();
    sensitivityValue.textContent = `${config.sensitivity}% (RMS ${config.vadThreshold.toFixed(4)})`;
    speechDurationValue.textContent = `${config.vadMinRecordingTime} ms`;
    silenceDurationValue.textContent = `${config.vadSilenceDuration} ms`;
    playbackGainValue.textContent = `+${config.playbackGainDb} dB`;
  }

  function writeLog(message: string, type = ''): void {
    const line = document.createElement('div');
    line.className = type;
    line.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    log.append(line);
    log.scrollTop = log.scrollHeight;
  }

  function setStatus(message: string, state = ''): void {
    status.textContent = message;
    status.className = `status ${state}`;
  }

  function setControlsRecording(recording: boolean): void {
    recordButton.textContent = recording ? 'Stop and prepare playback' : 'Start microphone test';
    recordButton.classList.toggle('recording', recording);
    sensitivity.disabled = recording;
    speechDuration.disabled = recording;
    silenceDuration.disabled = recording;
    microphone.disabled = recording;
    refreshMicrophonesButton.disabled = recording;
  }

  function publishPlayback(announce = true): void {
    if (!speechChunks.length) {
      setStatus(
        'No speech was retained. Raise sensitivity or speak closer to the microphone.',
        'error',
      );
      writeLog('No PCM data was received while VAD reported speech.');
      return;
    }
    if (playbackUrl) URL.revokeObjectURL(playbackUrl);
    const bytes = speechChunks.reduce((total, chunk) => total + chunk.byteLength, 0);
    const { playbackGainDb } = settingsSnapshot();
    playbackUrl = URL.createObjectURL(pcmToWav(speechChunks, playbackGainDb));
    playback.src = playbackUrl;
    playback.hidden = false;
    clearButton.disabled = false;
    setStatus(
      `Ready: ${(bytes / 32_000).toFixed(2)} seconds of VAD-filtered speech retained at +${playbackGainDb} dB playback gain.`,
    );
    if (announce) {
      writeLog(
        `Prepared ${(bytes / 1024).toFixed(1)} KiB of speech-only PCM at +${playbackGainDb} dB for playback.`,
      );
    }
  }

  async function refreshMicrophones(requestPermission = false): Promise<void> {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    try {
      if (requestPermission) {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((track: MediaStreamTrack) => track.stop());
      }
      const previousId = microphone.value;
      const devices = (await navigator.mediaDevices.enumerateDevices()).filter(
        ({ kind }) => kind === 'audioinput',
      );
      microphone.replaceChildren(new Option('System default microphone', ''));
      devices.forEach((device, index) => {
        microphone.add(
          new Option(
            device.label || `Microphone ${index + 1} (allow access to identify)`,
            device.deviceId,
          ),
        );
      });
      microphone.value = [...microphone.options].some(({ value }) => value === previousId)
        ? previousId
        : '';
      microphoneHint.textContent = devices.some(({ label }) => label)
        ? `${devices.length} microphone${devices.length === 1 ? '' : 's'} available. The selection is used for the next recording.`
        : 'Click Refresh devices and allow microphone access to identify each microphone.';
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);
      setStatus(`Unable to list microphones: ${message}`, 'error');
    }
  }

  function stopRecording(): void {
    const stop = stopListening;
    stopListening = null;
    if (typeof stop === 'function') stop();
    if (speechActive) writeLog('Speech end (recording stopped).', 'stop');
    speechActive = false;
    setControlsRecording(false);
    publishPlayback();
  }

  async function startRecording(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus(
        'This browser does not provide microphone capture. Use HTTPS or localhost.',
        'error',
      );
      return;
    }
    speechChunks = [];
    log.replaceChildren();
    const config = settingsSnapshot();
    setControlsRecording(true);
    setStatus('Requesting microphone access…', 'recording');
    writeLog(
      `Starting VAD: threshold ${config.vadThreshold.toFixed(4)}, min speech ${config.vadMinRecordingTime} ms, silence ${config.vadSilenceDuration} ms.`,
    );
    try {
      const selectedMicrophone =
        microphone.selectedOptions[0]?.textContent || 'system default microphone';
      const stop = await listenForSpeechWithDevice({
        sampleRate: SAMPLE_RATE,
        deviceId: microphone.value || undefined,
        vadThreshold: config.vadThreshold,
        vadMinRecordingTime: config.vadMinRecordingTime,
        vadSilenceDuration: config.vadSilenceDuration,
        continuous: true,
        onData: ({ pcm }: VadSpeechPacket): void => {
          if (!speechActive || !pcm.byteLength) return;
          // Copy each buffer: the worklet/source buffer may be reused after this callback returns.
          const copy = new ArrayBuffer(pcm.byteLength);
          new Uint8Array(copy).set(pcm);
          speechChunks.push(copy);
        },
        onSpeechStart: (): void => {
          if (speechActive) return;
          speechActive = true;
          setStatus('Listening — speech detected and retained.', 'recording');
          writeLog('Speech start.', 'start');
        },
        onSpeechEnd: (): void => {
          if (!speechActive) return;
          speechActive = false;
          setStatus('Listening — silence discarded; waiting for more speech.', 'recording');
          writeLog('Speech end — subsequent silence is discarded.', 'stop');
        },
        onError: (error: Error): void => {
          console.error('record-pcm VAD failed', error);
          stopListening = null;
          speechActive = false;
          setControlsRecording(false);
          const message = error.message || String(error);
          setStatus(`Microphone/VAD error: ${message}`, 'error');
          writeLog(`Error: ${message}`);
        },
      });
      if (!stop) {
        return;
      }
      stopListening = stop;
      await refreshMicrophones();
      if (stopListening !== null) {
        setStatus(
          `Listening on ${selectedMicrophone} — speak to begin a VAD-filtered recording.`,
          'recording',
        );
      }
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);
      setControlsRecording(false);
      setStatus(`Unable to start microphone: ${message}`, 'error');
      writeLog(`Start error: ${message}`);
    }
  }

  const settingInputs: ReadonlyArray<readonly [string, HTMLSelectElement | HTMLInputElement]> = [
    ['microphone', microphone],
    ['sensitivity', sensitivity],
    ['speechDuration', speechDuration],
    ['silenceDuration', silenceDuration],
    ['playbackGain', playbackGain],
  ];
  const settingListeners: Array<{
    element: HTMLSelectElement | HTMLInputElement;
    handler: () => void;
  }> = [];

  for (const [name, input] of settingInputs) {
    const handler = (): void => {
      updateSettingLabels();
      if (name === 'playbackGain' && speechChunks.length) {
        publishPlayback(false);
      }
    };
    input.addEventListener('input', handler);
    settingListeners.push({ element: input, handler });
  }

  const onRecordClick = (): void => {
    if (stopListening) {
      stopRecording();
    } else {
      void startRecording();
    }
  };
  recordButton.addEventListener('click', onRecordClick);

  const onRefreshClick = (): void => {
    void refreshMicrophones(true);
  };
  refreshMicrophonesButton.addEventListener('click', onRefreshClick);

  const onMicrophoneChange = (): void => {
    writeLog(
      `Selected microphone: ${microphone.selectedOptions[0]?.textContent || 'system default microphone'}.`,
    );
  };
  microphone.addEventListener('change', onMicrophoneChange);

  const onDeviceChange = (): void => {
    void refreshMicrophones();
  };
  navigator.mediaDevices?.addEventListener('devicechange', onDeviceChange);

  const onClearClick = (): void => {
    if (playbackUrl) URL.revokeObjectURL(playbackUrl);
    playbackUrl = null;
    speechChunks = [];
    playback.removeAttribute('src');
    playback.hidden = true;
    clearButton.disabled = true;
    setStatus('Recording cleared. Choose settings, then start another microphone test.');
    writeLog('Playback cleared.');
  };
  clearButton.addEventListener('click', onClearClick);

  const onBeforeUnload = (): void => {
    if (typeof stopListening === 'function') stopListening();
    if (playbackUrl) URL.revokeObjectURL(playbackUrl);
  };
  window.addEventListener('beforeunload', onBeforeUnload);

  updateSettingLabels();
  void refreshMicrophones();

  return {
    refreshMicrophones,
    startRecording,
    stopRecording,
    destroy(): void {
      for (const { element, handler } of settingListeners) {
        element.removeEventListener('input', handler);
      }
      recordButton.removeEventListener('click', onRecordClick);
      refreshMicrophonesButton.removeEventListener('click', onRefreshClick);
      microphone.removeEventListener('change', onMicrophoneChange);
      navigator.mediaDevices?.removeEventListener('devicechange', onDeviceChange);
      clearButton.removeEventListener('click', onClearClick);
      window.removeEventListener('beforeunload', onBeforeUnload);
      if (typeof stopListening === 'function') stopListening();
      stopListening = null;
      if (playbackUrl) URL.revokeObjectURL(playbackUrl);
      playbackUrl = null;
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initAudioVadLab();
  });
} else {
  initAudioVadLab();
}
