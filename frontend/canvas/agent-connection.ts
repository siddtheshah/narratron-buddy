import type { ToolActivitySnapshot } from './agent-activity.js';
import type { AgentSocket, AudioRecorderStartResult, OpusPacketHandler } from '../audio/audio-recorder.js';
import { isActiveCanvasWindow as defaultIsActiveCanvasWindow } from './doodle-session.js';
import type { MicMode } from './mic-config.js';

export type MicPressSource = 'pointer' | 'button-key' | 'hotkey';

export type StartAudioRecorderWorkletFn = (
  handler: OpusPacketHandler,
  initiallyEnabled?: boolean,
) => Promise<
  | AudioRecorderStartResult
  | readonly [AudioWorkletNode | null, AudioContext | null, MediaStream | null, boolean]
>;

export type SetMicrophoneTransmissionEnabledFn = (enabled: boolean) => void;
export type StopMicrophoneFn = (stream?: MediaStream | null) => Promise<void> | void;

export interface AgentStatusResponse {
  readonly agent_running?: boolean;
  readonly insufficient_credits?: boolean;
  readonly detail?: string;
}

export interface StartAgentResponse {
  readonly ok?: boolean;
  readonly agent_running?: boolean;
  readonly insufficient_credits?: boolean;
  readonly detail?: string;
}

export interface AgentThoughtPayload {
  readonly text: string;
  readonly highlighted?: boolean;
}

interface AgentSocketMessage {
  readonly type?: string;
  readonly detail?: string;
  readonly setupComplete?: boolean;
}

export interface CreateAgentConnectionOptions {
  readonly theaterId: string | null;
  readonly agentStartStopBtn: HTMLButtonElement | null;
  readonly agentConnectBtn: HTMLButtonElement | null;
  readonly statusIndicator: HTMLElement | null;
  readonly insufficientCreditsModal?: HTMLElement | null;
  readonly creditsModalMessage?: HTMLElement | null;
  readonly creditsModalDeployBtn?: HTMLElement | null;
  readonly creditsModalDismissBtn?: HTMLElement | null;
  readonly creditsModalCloseBtn?: HTMLElement | null;
  readonly isCurrentOrator: () => boolean;
  readonly isActiveCanvasWindow?: () => boolean;
  readonly getMicMode: () => MicMode;
  readonly refreshMicrophoneDevices: (requestPermission?: boolean) => Promise<void>;
  readonly getStartAudioRecorderWorklet: () => StartAudioRecorderWorkletFn | undefined;
  readonly getSetMicrophoneTransmissionEnabled: () => SetMicrophoneTransmissionEnabledFn | undefined;
  readonly getStopMicrophone: () => StopMicrophoneFn | undefined;
  readonly closeGameRenderer: () => void;
  readonly showShareToast: (message: string) => void;
  readonly updateAgentThought: (thought: AgentThoughtPayload) => void;
  readonly updateAgentActivity?: (toolActivity?: ToolActivitySnapshot | null) => void;
  readonly setPcmBroadcasting: (isBroadcasting: boolean) => void;
  readonly stopSceneSpeech: () => void;
  readonly navigateTo?: (url: string) => void;
  readonly alertUser?: (message: string) => void;
}

export interface AgentConnectionController {
  flushClientAudioBuffer(): void;
  audioRecorderHandler(opusBuffer: ArrayBuffer): void;
  showInsufficientCreditsModal(message?: string): void;
  hideInsufficientCreditsModal(): void;
  setAgentStartedUI(started: boolean): void;
  updateMicToggleUI(): void;
  ensureAgentConnectionWhenActive(): void;
  checkAgentStatus(): Promise<AgentStatusResponse | undefined>;
  startAgentTheater(): Promise<StartAgentResponse>;
  stopAgentTheater(): Promise<void>;
  connectAgent(): Promise<WebSocket>;
  cleanupAgentConnection(): void;
  enableMic(): Promise<void>;
  beginMicPress(source: MicPressSource): void;
  releaseMicPress(): void;
  cancelMicCapture(): void;
  getAgentWs(): WebSocket | null;
  isAgentStarted(): boolean;
  isRecording(): boolean;
  setRecording(recording: boolean): void;
  isMicAwaiting(): boolean;
  isMicCaptureReady(): boolean;
  isMicPressHeld(): boolean;
  getMicPressSource(): MicPressSource | null;
  destroy(): void;
}

declare global {
  interface Window {
    agentWs?: AgentSocket | null;
    isSetupComplete?: boolean;
    agentPingInterval?: number | null;
    isAgentStarted?: boolean;
    showInsufficientCreditsModal?: (message?: string) => void;
    hideInsufficientCreditsModal?: () => void;
    startAgentTheater?: () => Promise<StartAgentResponse>;
    stopAgentTheater?: () => Promise<void>;
  }
}

const AGENT_RECONNECT_DELAY_MS = 3000;
const AGENT_PING_INTERVAL_MS = 5000;
const DEFAULT_INSUFFICIENT_CREDITS_START_MSG =
  "The theater owner's credit balance is 0 or less. The owner must top up credits on the Deploy page.";
const DEFAULT_INSUFFICIENT_CREDITS_WS_MSG =
  "Agent stopped because the theater owner's credit balance is 0 or less.";

function isSpaceKeyEvent(event: KeyboardEvent): boolean {
  return ['Space', ' '].includes(event.code) || ['Space', ' ', 'Spacebar'].includes(event.key);
}

function readErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

/**
 * Manage the live agent theater lifecycle, WebSocket connection and keepalive
 * ping, insufficient-credits modal, and toggle/push-to-talk microphone capture.
 */
export function createAgentConnectionController(
  deps: CreateAgentConnectionOptions,
): AgentConnectionController {
  const {
    theaterId,
    agentStartStopBtn,
    agentConnectBtn,
    statusIndicator,
    insufficientCreditsModal = document.getElementById('insufficient-credits-modal'),
    creditsModalMessage = document.getElementById('credits-modal-message'),
    creditsModalDeployBtn = document.getElementById('credits-modal-deploy-btn'),
    creditsModalDismissBtn = document.getElementById('credits-modal-dismiss-btn'),
    creditsModalCloseBtn = document.getElementById('credits-modal-close-btn'),
    isCurrentOrator,
    isActiveCanvasWindow = defaultIsActiveCanvasWindow,
    getMicMode,
    refreshMicrophoneDevices,
    getStartAudioRecorderWorklet,
    getSetMicrophoneTransmissionEnabled,
    getStopMicrophone,
    closeGameRenderer,
    showShareToast,
    updateAgentThought,
    updateAgentActivity,
    setPcmBroadcasting,
    stopSceneSpeech,
    navigateTo = (url: string): void => {
      window.location.href = url;
    },
    alertUser = (message: string): void => {
      window.alert(message);
    },
  } = deps;

  let agentWs: WebSocket | null = null;
  let audioRecorderNode: AudioWorkletNode | null = null;
  let audioRecorderContext: AudioContext | null = null;
  let micStream: MediaStream | null = null;
  let isRecording = false;
  let isMicAwaiting = false;
  let micCaptureReady = false;
  let micPressHeld = false;
  let micPressSource: MicPressSource | null = null;
  let micRequestId = 0;
  let agentStarted = false;
  let agentReconnectTimer: number | null = null;
  let destroyed = false;

  // Audio Recorder Callback with Opus compression (~20-60 bytes per chunk)
  let clientAudioChunkCount = 0;
  let clientAudioBuffers: ArrayBuffer[] = [];
  let clientAudioTotalBytes = 0;
  let clientAudioLastFlushTime = Date.now();

  function flushClientAudioBuffer(): void {
    clientAudioBuffers = [];
    clientAudioTotalBytes = 0;
    clientAudioLastFlushTime = Date.now();
  }

  function audioRecorderHandler(opusBuffer: ArrayBuffer): void {
    // The recorder owns capture validity. Deliver startup packets and
    // the final encoder flush even before/after the UI recording flag.
    if (!agentWs || agentWs.readyState !== WebSocket.OPEN) {
      return;
    }
    clientAudioChunkCount += 1;
    if (clientAudioChunkCount % 100 === 1) {
      console.log(
        `[AudioRecorder] Streaming Opus audio chunk #${clientAudioChunkCount} (${opusBuffer.byteLength} bytes) to Agent WebSocket.`,
      );
    }
    agentWs.send(opusBuffer);
  }

  function showInsufficientCreditsModal(message?: string): void {
    const modal = insufficientCreditsModal ?? document.getElementById('insufficient-credits-modal');
    const msgElem = creditsModalMessage ?? document.getElementById('credits-modal-message');
    if (msgElem && message) {
      msgElem.textContent = message;
    }
    if (modal) {
      modal.classList.add('active');
      modal.setAttribute('aria-hidden', 'false');
    }
  }

  function hideInsufficientCreditsModal(): void {
    const modal = insufficientCreditsModal ?? document.getElementById('insufficient-credits-modal');
    if (modal) {
      modal.classList.remove('active');
      modal.setAttribute('aria-hidden', 'true');
    }
  }

  function setAgentStartedUI(started: boolean): void {
    agentStarted = started;
    window.isAgentStarted = started;
    if (!started) closeGameRenderer();
    if (!agentStartStopBtn) return;
    if (started) {
      agentStartStopBtn.textContent = '🛑 Banish';
      agentStartStopBtn.style.background = '#ef4444'; // Red
      agentStartStopBtn.classList.remove('summon-guide');
    } else {
      agentStartStopBtn.textContent = '🚀 Summon';
      agentStartStopBtn.style.background = '#10b981'; // Emerald
      agentStartStopBtn.classList.add('summon-guide');
    }
    if (started) ensureAgentConnectionWhenActive();
  }

  function updateMicToggleUI(): void {
    if (!agentConnectBtn) return;
    const micMode = getMicMode();
    agentConnectBtn.textContent = isMicAwaiting
      ? '🎙️ Awaiting'
      : micMode === 'push-to-talk'
        ? isRecording
          ? '🎙️ Talking'
          : '🎙️ Hold to Talk'
        : isRecording
          ? '🎙️ Mic Off'
          : '🎙️ Mic On';
    agentConnectBtn.style.background = isRecording
      ? '#ef4444'
      : isMicAwaiting
        ? '#eab308'
        : '#3b82f6';
    agentConnectBtn.disabled = isMicAwaiting && micMode === 'toggle';
    agentConnectBtn.setAttribute('aria-pressed', String(isRecording));
    agentConnectBtn.title =
      micMode === 'push-to-talk'
        ? 'Hold this button or the mic hotkey to speak'
        : 'Click or press the mic hotkey to toggle the microphone';
  }

  function ensureAgentConnectionWhenActive(): void {
    if (destroyed || !isCurrentOrator() || !agentStarted || !isActiveCanvasWindow()) return;
    if (
      agentWs &&
      (agentWs.readyState === WebSocket.OPEN || agentWs.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    connectAgent().catch((err: unknown) => {
      console.warn('Automatic agent connection failed:', err);
      if (
        !destroyed &&
        agentReconnectTimer === null &&
        agentStarted &&
        isActiveCanvasWindow()
      ) {
        agentReconnectTimer = window.setTimeout(() => {
          agentReconnectTimer = null;
          ensureAgentConnectionWhenActive();
        }, AGENT_RECONNECT_DELAY_MS);
      }
    });
  }

  async function checkAgentStatus(): Promise<AgentStatusResponse | undefined> {
    if (!theaterId) return undefined;
    try {
      const res = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/agent/status`);
      if (res.ok) {
        const data = (await res.json()) as AgentStatusResponse;
        if (data.insufficient_credits) {
          setAgentStartedUI(false);
          cleanupAgentConnection();
        } else {
          setAgentStartedUI(Boolean(data.agent_running));
        }
        ensureAgentConnectionWhenActive();
        return data;
      }
    } catch (e: unknown) {
      console.warn('Failed to fetch agent status:', e);
    }
    return undefined;
  }

  async function startAgentTheater(): Promise<StartAgentResponse> {
    if (agentStartStopBtn) {
      agentStartStopBtn.textContent = 'Starting...';
      agentStartStopBtn.style.background = '#eab308'; // Amber
    }
    try {
      if (!theaterId) {
        throw new Error('Missing theaterId');
      }
      const res = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/agent/start`, {
        method: 'POST',
      });
      const data = (await res.json()) as StartAgentResponse;
      if (!res.ok || data.insufficient_credits) {
        setAgentStartedUI(false);
        const insufficientCredits = res.status === 402 || data.insufficient_credits === true;
        const msg =
          data.detail ||
          (insufficientCredits
            ? DEFAULT_INSUFFICIENT_CREDITS_START_MSG
            : `Could not start Narratron (${res.status}). Please try again.`);
        if (insufficientCredits) {
          showInsufficientCreditsModal(msg);
        } else {
          showShareToast(msg);
        }
        throw new Error(msg);
      }
      setAgentStartedUI(true);
      return data;
    } catch (err: unknown) {
      console.error('Error starting agent theater:', err);
      setAgentStartedUI(false);
      throw err;
    }
  }

  async function stopAgentTheater(): Promise<void> {
    // Prevent the socket close handler from reconnecting while a stop is in flight.
    setAgentStartedUI(false);
    cleanupAgentConnection();
    if (agentStartStopBtn) {
      agentStartStopBtn.textContent = 'Stopping...';
      agentStartStopBtn.style.background = '#eab308'; // Amber
    }
    try {
      if (theaterId) {
        await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/agent/stop`, {
          method: 'POST',
        });
      }
    } catch (e: unknown) {
      console.warn('Error stopping agent theater:', e);
    } finally {
      setAgentStartedUI(false);
    }
  }

  function connectAgent(): Promise<WebSocket> {
    if (!theaterId) {
      return Promise.reject(new Error('Missing theaterId'));
    }
    if (agentWs && agentWs.readyState === WebSocket.OPEN) {
      return Promise.resolve(agentWs);
    }

    if (agentWs && agentWs.readyState === WebSocket.CONNECTING) {
      const connectingWs = agentWs;
      return new Promise<WebSocket>((resolve, reject) => {
        const pendingOpen = connectingWs.onopen;
        const pendingError = connectingWs.onerror;
        connectingWs.onopen = (ev: Event): void => {
          if (typeof pendingOpen === 'function') pendingOpen.call(connectingWs, ev);
          resolve(connectingWs);
        };
        connectingWs.onerror = (err: Event): void => {
          if (typeof pendingError === 'function') pendingError.call(connectingWs, err);
          reject(err);
        };
      });
    }

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const agentWsUrl = `${wsProtocol}//${window.location.host}/ws/${encodeURIComponent(theaterId)}/agent`;

    const socket = new WebSocket(agentWsUrl);
    agentWs = socket;
    window.agentWs = socket; // Expose globally for testing/automation

    return new Promise<WebSocket>((resolve, reject) => {
      socket.onopen = (): void => {
        console.log('Agent WebSocket connected.');
        statusIndicator?.classList.remove('disconnected'); // Set back to green
        updateMicToggleUI();
        resolve(socket);
      };

      socket.onclose = (): void => {
        console.log('Agent WebSocket disconnected.');
        if (agentWs !== null && agentWs !== socket) return;
        cleanupAgentConnection();
        if (!destroyed) ensureAgentConnectionWhenActive();
      };

      socket.onerror = (err: Event): void => {
        console.error('Agent WebSocket error:', err);
        if (agentWs !== null && agentWs !== socket) {
          reject(err);
          return;
        }
        cleanupAgentConnection();
        reject(err);
      };

      window.isSetupComplete = false;
      socket.onmessage = (e: MessageEvent<unknown>): void => {
        console.log(`WS_MSG:${String(e.data)}`);
        if (typeof e.data !== 'string') return;
        try {
          const data = JSON.parse(e.data) as AgentSocketMessage;
          if (data.type === 'insufficient_credits') {
            cleanupAgentConnection();
            setAgentStartedUI(false);
            showInsufficientCreditsModal(data.detail || DEFAULT_INSUFFICIENT_CREDITS_WS_MSG);
          } else if (data.type === 'agent_failed') {
            updateAgentThought({ text: 'wandering' });
            cleanupAgentConnection();
            setAgentStartedUI(false);
          } else if (data.setupComplete || data.type === 'pong') {
            window.isSetupComplete = true;
            updateAgentActivity?.({ live_ready: true });
          }
        } catch {
          // Ignore non-JSON frames.
        }
      };

      if (!window.agentPingInterval) {
        window.agentPingInterval = window.setInterval(() => {
          if (agentWs && agentWs.readyState === WebSocket.OPEN) {
            try {
              agentWs.send(JSON.stringify({ type: 'ping', ts: Date.now() }));
            } catch {
              // Ignore transient send errors on closing sockets.
            }
          }
        }, AGENT_PING_INTERVAL_MS);
      }
    });
  }

  function cleanupAgentConnection(): void {
    if (micCaptureReady || isMicAwaiting || micPressHeld) cancelMicCapture();
    if (window.agentPingInterval) {
      window.clearInterval(window.agentPingInterval);
      window.agentPingInterval = null;
    }
    if (agentReconnectTimer !== null) {
      window.clearTimeout(agentReconnectTimer);
      agentReconnectTimer = null;
    }
    if (agentWs) {
      try {
        agentWs.close();
      } catch {
        // Ignore close errors.
      }
      agentWs = null;
      window.agentWs = null; // Clear globally
      window.isSetupComplete = false;
    }
    updateAgentActivity?.({ live_ready: false });
    updateMicToggleUI();
    statusIndicator?.classList.add('disconnected'); // Set back to red
  }

  async function startMicStream(requestId: number): Promise<void> {
    if (requestId !== micRequestId) return;
    if (!agentWs || agentWs.readyState !== WebSocket.OPEN) {
      console.log('Agent WebSocket not open yet; connecting before starting microphone capture.');
      await connectAgent();
    }

    if (requestId !== micRequestId) return;
    if (!agentWs || agentWs.readyState !== WebSocket.OPEN) {
      throw new Error('Agent WebSocket is not ready for audio forwarding.');
    }

    await refreshMicrophoneDevices(true);
    if (requestId !== micRequestId) return;
    const startAudioRecorderWorklet = getStartAudioRecorderWorklet();
    if (typeof startAudioRecorderWorklet !== 'function') {
      throw new Error('Audio recorder is still loading. Please try again in a moment.');
    }
    const [node, context, stream, started] = await startAudioRecorderWorklet(
      audioRecorderHandler,
      getMicMode() !== 'push-to-talk' || micPressHeld,
    );
    if (!started || requestId !== micRequestId) {
      isMicAwaiting = false;
      updateMicToggleUI();
      return;
    }
    audioRecorderNode = node;
    audioRecorderContext = context;
    micStream = stream;
    micCaptureReady = true;
    isRecording = getMicMode() !== 'push-to-talk' || micPressHeld;
    getSetMicrophoneTransmissionEnabled()?.(isRecording);
    isMicAwaiting = false;
    updateMicToggleUI();
  }

  function stopMicStream(): void {
    try {
      flushClientAudioBuffer();
    } catch {
      // Ignore buffer flush errors during teardown.
    }
    // The VAD recorder owns the active speech segment and emits its
    // activity_end when stopped. It currently returns no MediaStream,
    // so this cannot be conditional on micStream being populated.
    try {
      const stopMicrophone = getStopMicrophone();
      if (stopMicrophone) {
        void stopMicrophone(micStream);
      }
    } catch {
      // Ignore microphone teardown errors.
    }
    if (micStream) {
      micStream = null;
    }
    if (audioRecorderContext) {
      try {
        void audioRecorderContext.close();
      } catch {
        // Ignore audio context close errors.
      }
      audioRecorderContext = null;
    }
    audioRecorderNode = null;
    micCaptureReady = false;
    isRecording = false;
    isMicAwaiting = false;
    setPcmBroadcasting(false);
    updateMicToggleUI();
  }

  // The agent socket follows agent lifecycle and tab focus. Enabling the mic
  // summons an inactive agent first, then only controls audio forwarding.
  async function enableMic(): Promise<void> {
    if (micCaptureReady || isMicAwaiting) return;
    const requestId = ++micRequestId;
    try {
      isMicAwaiting = true;
      updateMicToggleUI();
      if (!agentStarted) await startAgentTheater();
      if (requestId !== micRequestId) return;
      await connectAgent();
      if (requestId !== micRequestId) return;
      await startMicStream(requestId);
      if (requestId !== micRequestId) return;
    } catch (err: unknown) {
      if (requestId !== micRequestId) return;
      isMicAwaiting = false;
      console.error('Failed to enable microphone audio:', err);
      alertUser(readErrorMessage(err, 'Could not enable microphone audio.'));
      updateMicToggleUI();
    }
  }

  function releaseMicPress(): void {
    micPressHeld = false;
    micPressSource = null;
    if (getMicMode() === 'push-to-talk') {
      getSetMicrophoneTransmissionEnabled()?.(false);
      isRecording = false;
      setPcmBroadcasting(false);
      updateMicToggleUI();
    }
  }

  function cancelMicCapture(): void {
    micPressHeld = false;
    micPressSource = null;
    micRequestId += 1;
    if (micCaptureReady || isMicAwaiting) stopMicStream();
  }

  function beginMicPress(source: MicPressSource): void {
    if (getMicMode() !== 'push-to-talk' || micPressHeld || !isCurrentOrator()) return;
    stopSceneSpeech();
    micPressHeld = true;
    micPressSource = source;
    if (micCaptureReady) {
      getSetMicrophoneTransmissionEnabled()?.(true);
      isRecording = true;
      updateMicToggleUI();
    } else {
      void enableMic();
    }
  }

  const onDeployBtnClick = (): void => {
    navigateTo('/deploy?from=canvas');
  };
  creditsModalDeployBtn?.addEventListener('click', onDeployBtnClick);
  creditsModalDismissBtn?.addEventListener('click', hideInsufficientCreditsModal);
  creditsModalCloseBtn?.addEventListener('click', hideInsufficientCreditsModal);

  const onStartStopKeyDown = (event: KeyboardEvent): void => {
    if (isSpaceKeyEvent(event)) {
      event.preventDefault();
    }
  };
  const onStartStopKeyUp = (event: KeyboardEvent): void => {
    if (isSpaceKeyEvent(event)) {
      event.preventDefault();
    }
  };
  const onStartStopClick = (): void => {
    agentStartStopBtn?.blur();
    if (agentStarted) {
      void stopAgentTheater();
    } else {
      startAgentTheater().catch(() => undefined);
    }
  };

  if (agentStartStopBtn) {
    agentStartStopBtn.addEventListener('keydown', onStartStopKeyDown);
    agentStartStopBtn.addEventListener('keyup', onStartStopKeyUp);
    agentStartStopBtn.addEventListener('click', onStartStopClick);
  }

  const onStatusVisibilityChange = (): void => {
    if (isActiveCanvasWindow() && isCurrentOrator()) {
      void checkAgentStatus();
    }
  };
  const onWindowFocus = (): void => {
    if (isCurrentOrator()) {
      void checkAgentStatus();
    }
  };
  document.addEventListener('visibilitychange', onStatusVisibilityChange);
  window.addEventListener('focus', onWindowFocus);

  const onConnectBtnClick = (): void => {
    if (getMicMode() === 'push-to-talk') return;
    if (isRecording) cancelMicCapture();
    else void enableMic();
  };
  const onConnectBtnPointerDown = (event: PointerEvent): void => {
    if (getMicMode() !== 'push-to-talk' || event.button !== 0 || !agentConnectBtn) return;
    event.preventDefault();
    agentConnectBtn.setPointerCapture(event.pointerId);
    beginMicPress('pointer');
  };
  const onConnectBtnPointerUp = (): void => {
    if (micPressSource === 'pointer') releaseMicPress();
  };
  const onConnectBtnPointerCancel = (): void => {
    if (micPressSource === 'pointer') releaseMicPress();
  };
  const onWindowBlur = (): void => {
    if (getMicMode() === 'push-to-talk' && micPressHeld) releaseMicPress();
  };
  const onPttVisibilityChange = (): void => {
    if (document.hidden && getMicMode() === 'push-to-talk' && micPressHeld) releaseMicPress();
  };

  if (agentConnectBtn) {
    agentConnectBtn.addEventListener('click', onConnectBtnClick);
    agentConnectBtn.addEventListener('pointerdown', onConnectBtnPointerDown);
    agentConnectBtn.addEventListener('pointerup', onConnectBtnPointerUp);
    agentConnectBtn.addEventListener('pointercancel', onConnectBtnPointerCancel);
  }
  window.addEventListener('blur', onWindowBlur);
  document.addEventListener('visibilitychange', onPttVisibilityChange);

  window.showInsufficientCreditsModal = showInsufficientCreditsModal;
  window.hideInsufficientCreditsModal = hideInsufficientCreditsModal;
  window.startAgentTheater = startAgentTheater;
  window.stopAgentTheater = stopAgentTheater;

  return {
    flushClientAudioBuffer,
    audioRecorderHandler,
    showInsufficientCreditsModal,
    hideInsufficientCreditsModal,
    setAgentStartedUI,
    updateMicToggleUI,
    ensureAgentConnectionWhenActive,
    checkAgentStatus,
    startAgentTheater,
    stopAgentTheater,
    connectAgent,
    cleanupAgentConnection,
    enableMic,
    beginMicPress,
    releaseMicPress,
    cancelMicCapture,
    getAgentWs: (): WebSocket | null => agentWs,
    isAgentStarted: (): boolean => agentStarted,
    isRecording: (): boolean => isRecording,
    setRecording: (recording: boolean): void => {
      isRecording = recording;
    },
    isMicAwaiting: (): boolean => isMicAwaiting,
    isMicCaptureReady: (): boolean => micCaptureReady,
    isMicPressHeld: (): boolean => micPressHeld,
    getMicPressSource: (): MicPressSource | null => micPressSource,
    destroy: (): void => {
      destroyed = true;
      cleanupAgentConnection();
      creditsModalDeployBtn?.removeEventListener('click', onDeployBtnClick);
      creditsModalDismissBtn?.removeEventListener('click', hideInsufficientCreditsModal);
      creditsModalCloseBtn?.removeEventListener('click', hideInsufficientCreditsModal);
      if (agentStartStopBtn) {
        agentStartStopBtn.removeEventListener('keydown', onStartStopKeyDown);
        agentStartStopBtn.removeEventListener('keyup', onStartStopKeyUp);
        agentStartStopBtn.removeEventListener('click', onStartStopClick);
      }
      document.removeEventListener('visibilitychange', onStatusVisibilityChange);
      window.removeEventListener('focus', onWindowFocus);
      if (agentConnectBtn) {
        agentConnectBtn.removeEventListener('click', onConnectBtnClick);
        agentConnectBtn.removeEventListener('pointerdown', onConnectBtnPointerDown);
        agentConnectBtn.removeEventListener('pointerup', onConnectBtnPointerUp);
        agentConnectBtn.removeEventListener('pointercancel', onConnectBtnPointerCancel);
      }
      window.removeEventListener('blur', onWindowBlur);
      document.removeEventListener('visibilitychange', onPttVisibilityChange);
      if (window.showInsufficientCreditsModal === showInsufficientCreditsModal) {
        delete window.showInsufficientCreditsModal;
      }
      if (window.hideInsufficientCreditsModal === hideInsufficientCreditsModal) {
        delete window.hideInsufficientCreditsModal;
      }
      if (window.startAgentTheater === startAgentTheater) {
        delete window.startAgentTheater;
      }
      if (window.stopAgentTheater === stopAgentTheater) {
        delete window.stopAgentTheater;
      }
      // Retain references in closure cleanly; no-op read to keep fields live if needed.
      void audioRecorderNode;
      void clientAudioBuffers;
      void clientAudioTotalBytes;
      void clientAudioLastFlushTime;
    },
  };
}
