import type { ListenForSpeechOptions } from '../audio/device-aware-pcm.js';

export type MicMode = 'toggle' | 'push-to-talk';

export type ListenForSpeechFn = (options: ListenForSpeechOptions) => Promise<() => void>;

export interface MicConfigAuthState {
  readonly authenticated?: boolean;
  readonly user?: {
    readonly mic_sensitivity?: number | null;
  } | null;
}

export interface CreateMicConfigOptions {
  readonly micModeToggle: HTMLInputElement | null;
  readonly micDeviceSelect: HTMLSelectElement | null;
  readonly micDeviceRefresh: HTMLElement | null;
  readonly micDeviceHint: HTMLElement | null;
  readonly micTestRecordBtn: HTMLButtonElement | null;
  readonly micTestPlayBtn: HTMLButtonElement | null;
  readonly micTestStatus: HTMLElement | null;
  readonly micSensSlider: HTMLInputElement | null;
  readonly micSensValDisplay: HTMLElement | null;
  readonly micConfigModal: HTMLElement | null;
  readonly menuItemMicConfig: HTMLElement | null;
  readonly micConfigCloseBtn: HTMLElement | null;
  readonly micConfigDoneBtn: HTMLElement | null;
  readonly oratorHowtoModal: HTMLElement | null;
  readonly howtoCloseBtn: HTMLElement | null;
  readonly howtoAckBtn: HTMLElement | null;
  readonly getMicMode: () => MicMode;
  readonly setMicMode: (mode: MicMode) => void;
  readonly isRecording: () => boolean;
  readonly cancelMicCapture: () => void;
  readonly updateMicToggleUI: () => void;
  readonly getListenForSpeech: () => ListenForSpeechFn | undefined;
  readonly getAuthState: () => Promise<MicConfigAuthState | null>;
  readonly invalidateAuthStateCache: () => void;
  readonly onOpenMicConfig?: () => void;
  readonly onCloseMicConfig?: () => void;
  readonly createAudioContext?: (sampleRate: number) => AudioContext;
}

export interface MicConfigController {
  refreshMicrophoneDevices(requestPermission?: boolean): Promise<void>;
  stopMicTestRecording(message?: string): void;
  setMicSensitivity(percentVal: string | number, saveDb?: boolean): void;
  openMicConfigModal(): void;
  closeMicConfigModal(): void;
  isMicConfigOpen(): boolean;
  openOratorHowtoModal(options?: { readonly openMicConfigOnClose?: boolean }): void;
  closeOratorHowtoModal(): void;
  isOratorHowtoOpen(): boolean;
  destroy(): void;
}

declare global {
  interface Window {
    NARRATRON_MIC_DEVICE_ID?: string;
    MIC_DETECT_THRESHOLD?: number | string;
    MIC_SENSITIVITY?: number;
  }
}

const MIC_MODE_STORAGE_KEY = 'narratron_mic_mode';
const MIC_DEVICE_STORAGE_KEY = 'narratron_mic_device_id';
const MIC_SENSITIVITY_STORAGE_KEY = 'narratron_mic_sensitivity';
const ORATOR_HOWTO_SEEN_STORAGE_KEY = 'narratron_orator_howto_seen';
const MIC_TEST_SAMPLE_RATE = 16000;
const MIC_TEST_MAX_MS = 10000;

function readErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

/**
 * Manage microphone mode, audio input device enumeration, local 16 kHz PCM test
 * recording/playback, sensitivity persistence, and the mic/how-to modals.
 */
export function createMicConfigController(options: CreateMicConfigOptions): MicConfigController {
  const {
    micModeToggle,
    micDeviceSelect,
    micDeviceRefresh,
    micDeviceHint,
    micTestRecordBtn,
    micTestPlayBtn,
    micTestStatus,
    micSensSlider,
    micSensValDisplay,
    micConfigModal,
    menuItemMicConfig,
    micConfigCloseBtn,
    micConfigDoneBtn,
    oratorHowtoModal,
    howtoCloseBtn,
    howtoAckBtn,
    getMicMode,
    setMicMode,
    isRecording,
    cancelMicCapture,
    updateMicToggleUI,
    getListenForSpeech,
    getAuthState,
    invalidateAuthStateCache,
    onOpenMicConfig,
    onCloseMicConfig,
    createAudioContext = (sampleRate: number) => new AudioContext({ sampleRate }),
  } = options;

  let stopMicTestCapture: (() => void) | null = null;
  let micTestStarting = false;
  let micTestChunks: Uint8Array[] = [];
  let micTestBytes = 0;
  let micTestTimer: number | null = null;
  let micTestPlayback: AudioBufferSourceNode | null = null;
  let micTestPlaybackContext: AudioContext | null = null;

  const onMicModeChange = (): void => {
    if (!micModeToggle) return;
    cancelMicCapture();
    const nextMode: MicMode = micModeToggle.checked ? 'push-to-talk' : 'toggle';
    setMicMode(nextMode);
    try {
      localStorage.setItem(MIC_MODE_STORAGE_KEY, nextMode);
    } catch {
      // Storage can be unavailable in private or restricted browser contexts.
    }
    updateMicToggleUI();
  };

  if (micModeToggle) {
    micModeToggle.checked = getMicMode() === 'push-to-talk';
    micModeToggle.addEventListener('change', onMicModeChange);
  }
  updateMicToggleUI();

  async function refreshMicrophoneDevices(requestPermission = false): Promise<void> {
    if (!navigator.mediaDevices?.enumerateDevices || !micDeviceSelect) return;
    try {
      if (requestPermission) {
        const permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        permissionStream.getTracks().forEach((track) => track.stop());
      }
      let selected = '';
      try {
        selected = localStorage.getItem(MIC_DEVICE_STORAGE_KEY) || '';
      } catch {
        selected = '';
      }
      const devices = (await navigator.mediaDevices.enumerateDevices()).filter(
        (device) => device.kind === 'audioinput',
      );
      micDeviceSelect.replaceChildren(new Option('System default microphone', ''));
      devices.forEach((device, index) => {
        micDeviceSelect.add(
          new Option(
            device.label || `Microphone ${index + 1} (allow access to identify)`,
            device.deviceId,
          ),
        );
      });
      micDeviceSelect.value = [...micDeviceSelect.options].some(
        (option) => option.value === selected,
      )
        ? selected
        : '';
      window.NARRATRON_MIC_DEVICE_ID = micDeviceSelect.value;
      if (micDeviceHint) {
        micDeviceHint.textContent = devices.some((device) => device.label)
          ? 'Your selection is used when you connect.'
          : 'Refresh and allow microphone access to identify devices.';
      }
    } catch {
      if (micDeviceHint) {
        micDeviceHint.textContent = 'Unable to list microphones. Check browser permissions.';
      }
    }
  }

  const onDeviceRefreshClick = (): void => {
    void refreshMicrophoneDevices(true);
  };

  const onDeviceSelectChange = (): void => {
    if (!micDeviceSelect) return;
    window.NARRATRON_MIC_DEVICE_ID = micDeviceSelect.value;
    try {
      localStorage.setItem(MIC_DEVICE_STORAGE_KEY, micDeviceSelect.value);
    } catch {
      // Storage can be unavailable in private or restricted browser contexts.
    }
    if (micDeviceHint) {
      micDeviceHint.textContent = 'Saved in this browser. Reconnect to use this microphone.';
    }
  };

  const onDeviceChange = (): void => {
    void refreshMicrophoneDevices();
  };

  micDeviceRefresh?.addEventListener('click', onDeviceRefreshClick);
  micDeviceSelect?.addEventListener('change', onDeviceSelectChange);
  navigator.mediaDevices?.addEventListener('devicechange', onDeviceChange);
  void refreshMicrophoneDevices();

  function setMicTestStatus(message: string): void {
    if (micTestStatus) micTestStatus.textContent = message;
  }

  function setMicTestRecordingUI(recording: boolean): void {
    if (!micTestRecordBtn) return;
    micTestRecordBtn.textContent = recording ? 'Stop recording' : 'Record test';
    micTestRecordBtn.setAttribute('aria-pressed', String(recording));
  }

  function stopMicTestRecording(
    message = 'Recording ready. Play it back to verify your microphone.',
  ): void {
    if (micTestTimer !== null) {
      window.clearTimeout(micTestTimer);
      micTestTimer = null;
    }
    const stop = stopMicTestCapture;
    stopMicTestCapture = null;
    if (stop) stop();
    setMicTestRecordingUI(false);
    if (micTestPlayBtn) micTestPlayBtn.disabled = micTestBytes === 0;
    setMicTestStatus(
      micTestBytes
        ? message
        : 'No audio was captured. Check browser microphone permission and try again.',
    );
  }

  async function startMicTestRecording(): Promise<void> {
    if (micTestStarting) return;
    if (stopMicTestCapture) {
      stopMicTestRecording();
      return;
    }
    if (isRecording()) {
      setMicTestStatus('Disconnect the live microphone before running a local mic test.');
      return;
    }
    const listenForSpeech = getListenForSpeech();
    if (!listenForSpeech) {
      setMicTestStatus('Audio capture is still loading. Please try again in a moment.');
      return;
    }

    micTestStarting = true;
    micTestChunks = [];
    micTestBytes = 0;
    if (micTestPlayBtn) micTestPlayBtn.disabled = true;
    setMicTestRecordingUI(true);
    setMicTestStatus('Recording from the selected microphone… speak now (up to 10 seconds).');
    try {
      // A zero threshold deliberately includes quiet samples: this is a
      // recording diagnostic, not a VAD test.
      const stop = await listenForSpeech({
        deviceId: window.NARRATRON_MIC_DEVICE_ID || undefined,
        sampleRate: MIC_TEST_SAMPLE_RATE,
        vadThreshold: 0,
        continuous: true,
        onData: ({ pcm }) => {
          const chunk = new Uint8Array(pcm);
          micTestChunks.push(chunk);
          micTestBytes += chunk.byteLength;
        },
        onError: (error: Error) => setMicTestStatus(`Microphone test failed: ${error.message}`),
      });
      stopMicTestCapture = stop;
      micTestTimer = window.setTimeout(
        () => stopMicTestRecording('10-second recording ready. Play it back to verify your microphone.'),
        MIC_TEST_MAX_MS,
      );
    } catch (error: unknown) {
      setMicTestRecordingUI(false);
      setMicTestStatus(`Could not start microphone test: ${readErrorMessage(error, 'Unknown error')}`);
    } finally {
      micTestStarting = false;
    }
  }

  async function playMicTestRecording(): Promise<void> {
    if (!micTestBytes) return;
    if (micTestPlayback) {
      micTestPlayback.stop();
      micTestPlayback = null;
    }
    if (micTestPlaybackContext && micTestPlaybackContext.state !== 'closed') {
      await micTestPlaybackContext.close();
    }

    const pcm = new Uint8Array(micTestBytes);
    let offset = 0;
    micTestChunks.forEach((chunk) => {
      pcm.set(chunk, offset);
      offset += chunk.byteLength;
    });
    const context = createAudioContext(MIC_TEST_SAMPLE_RATE);
    const audioBuffer = context.createBuffer(1, pcm.byteLength / 2, MIC_TEST_SAMPLE_RATE);
    const samples = audioBuffer.getChannelData(0);
    const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);
    for (let index = 0; index < samples.length; index += 1) {
      samples[index] = view.getInt16(index * 2, true) / 32768;
    }
    const source = context.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(context.destination);
    source.onended = () => {
      if (micTestPlayback === source) {
        micTestPlayback = null;
        setMicTestStatus(
          'Playback finished. If you heard yourself clearly, your mic and PCM path are working.',
        );
      }
      if (context.state !== 'closed') {
        void context.close();
      }
    };
    micTestPlaybackContext = context;
    micTestPlayback = source;
    await context.resume();
    source.start();
    setMicTestStatus('Playing your PCM microphone test…');
  }

  const onMicTestRecordClick = (): void => {
    void startMicTestRecording();
  };

  const onMicTestPlayClick = (): void => {
    void playMicTestRecording();
  };

  micTestRecordBtn?.addEventListener('click', onMicTestRecordClick);
  micTestPlayBtn?.addEventListener('click', onMicTestPlayClick);

  function setMicSensitivity(percentVal: string | number, saveDb = false): void {
    const val = Math.max(0, Math.min(100, parseInt(String(percentVal), 10) || 50));
    if (micSensSlider) micSensSlider.value = String(val);
    if (micSensValDisplay) micSensValDisplay.textContent = `${val}%`;

    const s = val / 100.0;
    // record-pcm uses RMS VAD.  Higher sensitivity lowers the threshold.
    window.MIC_DETECT_THRESHOLD = 0.01 * Math.pow(10, (0.5 - s) * 2);
    window.MIC_SENSITIVITY = s;

    try {
      localStorage.setItem(MIC_SENSITIVITY_STORAGE_KEY, val.toString());
    } catch {
      // Storage can be unavailable in private or restricted browser contexts.
    }

    if (saveDb) {
      fetch('/api/auth/mic-sensitivity', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mic_sensitivity: s }),
      })
        .then((res) => {
          if (res.ok) invalidateAuthStateCache();
        })
        .catch((err: unknown) => console.debug('Failed to sync mic_sensitivity to server:', err));
    }
  }

  const onMicSensInput = (event: Event): void => {
    const target = event.target as HTMLInputElement;
    setMicSensitivity(target.value, false);
  };

  const onMicSensChange = (event: Event): void => {
    const target = event.target as HTMLInputElement;
    setMicSensitivity(target.value, true);
  };

  micSensSlider?.addEventListener('input', onMicSensInput);
  micSensSlider?.addEventListener('change', onMicSensChange);

  (function initMicSensitivity(): void {
    let initialVal = 50;
    try {
      const stored = localStorage.getItem(MIC_SENSITIVITY_STORAGE_KEY);
      if (stored !== null && !Number.isNaN(parseInt(stored, 10))) {
        initialVal = parseInt(stored, 10);
      }
    } catch {
      // Storage can be unavailable in private or restricted browser contexts.
    }

    getAuthState()
      .then((data) => {
        const userSensitivity = data?.user?.mic_sensitivity;
        if (data?.authenticated && userSensitivity !== undefined && userSensitivity !== null) {
          const userVal = Math.round(userSensitivity * 100);
          setMicSensitivity(userVal, false);
        } else {
          setMicSensitivity(initialVal, false);
        }
      })
      .catch(() => setMicSensitivity(initialVal, false));
  })();

  function openMicConfigModal(): void {
    if (!micConfigModal) return;
    if (oratorHowtoModal) {
      delete oratorHowtoModal.dataset.openMicConfigOnClose;
      oratorHowtoModal.classList.remove('active');
      oratorHowtoModal.setAttribute('aria-hidden', 'true');
    }
    onOpenMicConfig?.();
    void refreshMicrophoneDevices();
    micConfigModal.classList.add('active');
    micConfigModal.setAttribute('aria-hidden', 'false');
  }

  function closeMicConfigModal(): void {
    onCloseMicConfig?.();
    if (!micConfigModal) return;
    if (stopMicTestCapture) stopMicTestRecording();
    micConfigModal.classList.remove('active');
    micConfigModal.setAttribute('aria-hidden', 'true');
  }

  function openOratorHowtoModal(
    modalOptions: { readonly openMicConfigOnClose?: boolean } = {},
  ): void {
    if (!oratorHowtoModal) return;
    const { openMicConfigOnClose = false } = modalOptions;
    oratorHowtoModal.dataset.openMicConfigOnClose = String(openMicConfigOnClose);
    oratorHowtoModal.classList.add('active');
    oratorHowtoModal.setAttribute('aria-hidden', 'false');
  }

  function closeOratorHowtoModal(): void {
    if (!oratorHowtoModal) return;
    const openMicConfigOnClose = oratorHowtoModal.dataset.openMicConfigOnClose === 'true';
    delete oratorHowtoModal.dataset.openMicConfigOnClose;
    oratorHowtoModal.classList.remove('active');
    oratorHowtoModal.setAttribute('aria-hidden', 'true');
    try {
      localStorage.setItem(ORATOR_HOWTO_SEEN_STORAGE_KEY, 'true');
    } catch {
      // Storage can be unavailable in private or restricted browser contexts.
    }
    if (openMicConfigOnClose) openMicConfigModal();
  }

  const onMicConfigBackdropClick = (event: MouseEvent): void => {
    if (event.target === micConfigModal) closeMicConfigModal();
  };

  const onOratorHowtoBackdropClick = (event: MouseEvent): void => {
    if (event.target === oratorHowtoModal) closeOratorHowtoModal();
  };

  menuItemMicConfig?.addEventListener('click', openMicConfigModal);
  micConfigCloseBtn?.addEventListener('click', closeMicConfigModal);
  micConfigDoneBtn?.addEventListener('click', closeMicConfigModal);
  micConfigModal?.addEventListener('click', onMicConfigBackdropClick);

  howtoCloseBtn?.addEventListener('click', closeOratorHowtoModal);
  howtoAckBtn?.addEventListener('click', closeOratorHowtoModal);
  oratorHowtoModal?.addEventListener('click', onOratorHowtoBackdropClick);

  return {
    refreshMicrophoneDevices,
    stopMicTestRecording,
    setMicSensitivity,
    openMicConfigModal,
    closeMicConfigModal,
    isMicConfigOpen: () => Boolean(micConfigModal?.classList.contains('active')),
    openOratorHowtoModal,
    closeOratorHowtoModal,
    isOratorHowtoOpen: () => Boolean(oratorHowtoModal?.classList.contains('active')),
    destroy: () => {
      if (micTestTimer !== null) {
        window.clearTimeout(micTestTimer);
        micTestTimer = null;
      }
      if (stopMicTestCapture) {
        stopMicTestCapture();
        stopMicTestCapture = null;
      }
      if (micTestPlayback) {
        try {
          micTestPlayback.stop();
        } catch {
          // Ignore playback teardown errors.
        }
        micTestPlayback = null;
      }
      if (micTestPlaybackContext && micTestPlaybackContext.state !== 'closed') {
        void micTestPlaybackContext.close();
        micTestPlaybackContext = null;
      }
      micModeToggle?.removeEventListener('change', onMicModeChange);
      micDeviceRefresh?.removeEventListener('click', onDeviceRefreshClick);
      micDeviceSelect?.removeEventListener('change', onDeviceSelectChange);
      navigator.mediaDevices?.removeEventListener('devicechange', onDeviceChange);
      micTestRecordBtn?.removeEventListener('click', onMicTestRecordClick);
      micTestPlayBtn?.removeEventListener('click', onMicTestPlayClick);
      micSensSlider?.removeEventListener('input', onMicSensInput);
      micSensSlider?.removeEventListener('change', onMicSensChange);
      menuItemMicConfig?.removeEventListener('click', openMicConfigModal);
      micConfigCloseBtn?.removeEventListener('click', closeMicConfigModal);
      micConfigDoneBtn?.removeEventListener('click', closeMicConfigModal);
      micConfigModal?.removeEventListener('click', onMicConfigBackdropClick);
      howtoCloseBtn?.removeEventListener('click', closeOratorHowtoModal);
      howtoAckBtn?.removeEventListener('click', closeOratorHowtoModal);
      oratorHowtoModal?.removeEventListener('click', onOratorHowtoBackdropClick);
    },
  };
}
