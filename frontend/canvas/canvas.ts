import { createA2UICanvasRenderer, type A2UISurface } from './a2ui-renderer.js';
import { initializeActionWheel, type ActionWheelController } from './action-wheel.js';
import { createAgentActivityController, type ToolActivitySnapshot } from './agent-activity.js';
import {
  createAgentConnectionController,
  type AgentConnectionController,
  type AgentThoughtPayload,
} from './agent-connection.js';
import {
  createCanvasAssetManager,
  type CanvasAssetManager,
  type MusicSelectionState,
  type StampManagerTab,
  type TheaterCharacter,
  type TheaterScene,
} from './asset-manager.js';
import type {
  SetMicrophoneTransmissionEnabledFn,
  StartAudioRecorderWorkletFn,
  StopMicrophoneFn,
} from './agent-connection.js';
import { createBatonManager, type BatonState, type BatonUser } from './baton-manager.js';
import { initializeBeyond20Listener } from './beyond20-listener.js';
import { initializeCanvasBookmarks, type CanvasBookmarksController } from './bookmarks.js';
import { initializeChatController } from '../shared/chat-controller.js';
import { createClipShareController } from './clip-share.js';
import { createCollabBarController } from './collab-bar.js';
import {
  createCornerPanels,
  type CornerPanelsController,
  type HiddenStickyEntry,
  type StickyNoteItem,
} from './corner-panels.js';
import { canvasCursorState } from './cursor-state.js';
import {
  ClearCanvasDoodleAction,
  CompoundDeleteDoodleAction,
  DeleteSegmentsDoodleAction,
  DeleteStampDoodleAction,
  DeleteTextDoodleAction,
  DoodleAction,
  DrawStrokeDoodleAction,
  EditTextDoodleAction,
  MoveTextDoodleAction,
  PlaceStampDoodleAction,
  PlaceTextDoodleAction,
  TransformStampDoodleAction,
  restoreSegmentsToServer,
  type DoodleActionInput,
  type DoodleMessage,
  type DoodleUndoManager,
} from './doodle-actions.js';
import {
  createDoodleSession,
  isActiveCanvasWindow,
  type DoodleSessionController,
} from './doodle-session.js';
import { createFullscreenControlsController } from './fullscreen-controls.js';
import { createGameRenderer, type GamePayload } from '../game/game-renderer.js';
import {
  createHistoryPagingController,
  type HistoryItem,
  type HistoryPagingController,
  type HistoryResourceManager,
  type LatestHistoryPayload,
} from './history-paging.js';
import {
  createHotkeyControlsController,
  type HotkeyControlsController,
  type HotkeySpec,
} from './hotkey-controls.js';
import { createImageRenderer } from './image-renderer.js';
import { playCanvasAnimation } from './media.js';
import {
  createMicConfigController,
  type ListenForSpeechFn,
  type MicConfigController,
  type MicMode,
} from './mic-config.js';
import {
  createMusicPlayer,
  MUSIC_MUTED_STORAGE_KEY,
  type MusicPlayerController,
  type MusicStatePayload,
} from './music-player.js';
import { createNarrationPane } from './narration-pane.js';
import { createCanvasPinControl } from './pin-control.js';
import { createCanvasScene, type SceneDialogueLine, type SceneTextSpan } from './scene.js';
import { createSpeechReplayControls, type SpeechReplayControls } from './speech-replay.js';
import {
  createCanvasStampsController,
  stampNameFromPrompt,
  updateStampManagerProfileLinks,
  type CanvasStampsController,
  type ReferenceItem,
  type StampItem,
  type StampUser,
} from './stamps.js';
import { startCanvasStatePolling } from '../shared/state-polling.js';
import { initializeStoryLogInspector } from '../shared/story-log-inspector.js';
import {
  createTheaterModalsController,
  type TheaterModalsController,
} from './theater-modals.js';
import { setupTheaterReport } from './theater-report.js';

export interface CanvasAuthUser extends StampUser {
  readonly id?: number;
  readonly username?: string;
  readonly profile_color?: string | null;
  readonly mic_sensitivity?: number | null;
}

export interface CanvasAuthState {
  readonly authenticated: boolean;
  readonly testing_use_local?: boolean;
  readonly server_run_id?: string | null;
  readonly user?: CanvasAuthUser | null;
}

interface TheaterActionResponse {
  readonly detail?: string;
  readonly pinned?: boolean;
  readonly music_pinned?: boolean;
  readonly orator_cursor?: number | null;
}

interface TheaterCollabResponse {
  readonly viewer_collab_enabled?: boolean;
}

interface TheaterSaveResponse {
  readonly detail?: string;
}

interface TheaterDetailsResponse {
  readonly references?: readonly ReferenceItem[];
  readonly metadata?: {
    readonly is_owner?: boolean;
    readonly is_active_orator?: boolean;
    readonly is_contributor?: boolean;
    readonly is_adventure_mode?: boolean;
    readonly config?: {
      readonly story_planning?: {
        readonly adventure_mode?: boolean;
      };
    };
  };
}

interface CanvasLatestPayload extends LatestHistoryPayload {
  readonly tool_activity?: ToolActivitySnapshot | null;
  readonly agent_thought?: AgentThoughtPayload | null;
  readonly pinned?: boolean;
  readonly music_pinned?: boolean;
  readonly scene_dialogue?: readonly SceneDialogueLine[] | null;
  readonly narration?: string | null;
  readonly narration_spans?: readonly SceneTextSpan[] | null;
  readonly committed_scene_speech_generation?: number;
  readonly interactive_surfaces?: readonly A2UISurface[] | null;
  readonly game_state?: GamePayload | null;
  readonly sticky_notes?: readonly StickyNoteItem[] | null;
  readonly hidden_stickies?: readonly HiddenStickyEntry[] | null;
  readonly viewer_collab_enabled?: boolean;
  readonly music?: MusicStatePayload | null;
}

interface CanvasStateSocketMessage {
  readonly type?: string;
  readonly audio_url?: string;
  readonly speech_generation?: number;
  readonly domains?: readonly string[];
}

declare global {
  interface Window {
    theaterId?: string | null;
    currenttheaterId?: string | null;
    theaterStorage?: Storage | null;
    _isActiveOratorState?: boolean;
    _isContributor?: boolean;
    cachedTheaterReferences?: readonly ReferenceItem[] | null;
    cachedTheaterStamps?: StampItem[] | null;
    cachedTheaterCharacters?: readonly TheaterCharacter[] | null;
    cachedTheaterScenes?: readonly TheaterScene[] | null;
    updateCanvasStickyNotes?: CornerPanelsController['updateCanvasStickyNotes'];
    isStickiesRevealedInCache?: CornerPanelsController['isStickiesRevealedInCache'];
    setStickiesRevealedInCache?: CornerPanelsController['setStickiesRevealedInCache'];
    STICKIES_REVEAL_CACHE_TIMEOUT_MS?: number;
    setCanvasTextHidden?: CornerPanelsController['setCanvasTextHidden'];
    __resolveWebpQualityUrl?: (rawUrl: string) => string;
    __historyResourceManager?: HistoryResourceManager;
    doodleUndoManager?: DoodleUndoManager;
    undoLastDoodleAction?: () => boolean;
    undoDoodleAction?: () => boolean;
    addContributor?: (targetUserId: string | number) => void;
    removeContributor?: (targetUserId: string | number) => void;
    requestBatonPass?: (targetUserId: string | number) => void;
    setTextHotkey?: (hk: string | Partial<HotkeySpec>) => void;
    setTextCommandHotkey?: (hk: string | Partial<HotkeySpec>) => void;
    enableAudioInput?: () => void;
    connectAgentAndMic?: () => Promise<void>;
    sendOratorAudio?: (base64Data: string) => void;
    streamFullAudio?: (
      fullBase64Data: string,
      chunkSize?: number,
      delayMs?: number,
    ) => Promise<void>;
    toggleFullScreen?: () => void;
    togglePopoutPanel?: () => void;
    openPulloutMenu?: () => void;
    closePulloutMenu?: () => void;
    openOratorHowtoModal?: MicConfigController['openOratorHowtoModal'];
    closeOratorHowtoModal?: MicConfigController['closeOratorHowtoModal'];
    openMicConfigModal?: MicConfigController['openMicConfigModal'];
    closeMicConfigModal?: MicConfigController['closeMicConfigModal'];
    openTheaterConfigModal?: () => Promise<void>;
    closeTheaterConfigModal?: () => void;
    openAdventureGuideModal?: () => void;
    closeAdventureGuideModal?: () => void;
    initAdventureGuide?: (isAdventureMode: boolean, currentTheaterId?: string | null) => void;
  }
}

interface CanvasCompatWindow extends Window {
  doodleUndoStack?: DoodleAction[];
  setMicHotkey?: (hk: string | Partial<HotkeySpec>) => void;
}

const compatWindow: CanvasCompatWindow = window;

const HTML_ESCAPE_MAP: Readonly<Record<string, string>> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

export function escapeHtml(str: unknown): string {
  return String(str ?? '').replace(/[&<>"']/g, (char) => HTML_ESCAPE_MAP[char] ?? char);
}

function getButtonElement(id: string): HTMLButtonElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLButtonElement ? el : null;
}

function getInputElement(id: string): HTMLInputElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLInputElement ? el : null;
}

function getTextAreaElement(id: string): HTMLTextAreaElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLTextAreaElement ? el : null;
}

function getFormElement(id: string): HTMLFormElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLFormElement ? el : null;
}

function getImageElement(id: string): HTMLImageElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLImageElement ? el : null;
}

function getCanvasElement(id: string): HTMLCanvasElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLCanvasElement ? el : null;
}

function getAnchorElement(id: string): HTMLAnchorElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLAnchorElement ? el : null;
}

function readChatInputElement(id: string): HTMLTextAreaElement | HTMLInputElement | null {
  const el = document.getElementById(id);
  if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
    return el;
  }
  return null;
}

function readErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

function readInitialMuteSetting(): boolean {
  try {
    return localStorage.getItem(MUSIC_MUTED_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

function readInitialMicMode(): MicMode {
  try {
    return localStorage.getItem('narratron_mic_mode') === 'push-to-talk'
      ? 'push-to-talk'
      : 'toggle';
  } catch {
    return 'toggle';
  }
}

function readStoredFlag(key: string): boolean {
  try {
    return localStorage.getItem(key) === 'true';
  } catch {
    return false;
  }
}

function writeStoredValue(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Ignore storage errors in restricted browser contexts.
  }
}

function hasStoredFlag(key: string): boolean {
  try {
    return Boolean(localStorage.getItem(key));
  } catch {
    return false;
  }
}

// Shared page state declared before any controller wiring to prevent TDZ errors
// during startup rendering and early WebSocket/auth callbacks.
let currentUser: CanvasAuthUser | null = null;
let currentBatonState: BatonState | null = null;
let canvasAuthStatePromise: Promise<CanvasAuthState> | null = null;
let viewerCollabEnabled = false;
let isTheaterStampOwner = false;
let isStampManagerOpen = false;
let activeStampManagerTab: StampManagerTab = 'stamps';
let cachedUserStamps: StampItem[] | null = null;
const emptyTheaterStamps: StampItem[] = [];
let cachedTheaterStamps: StampItem[] | null = null;
let cachedTheaterScenes: TheaterScene[] | null = null;
let cachedTheaterCharacters: TheaterCharacter[] | null = null;
let assetMusicState: MusicSelectionState | null = null;

let isMuted = readInitialMuteSetting();
let latestStateRequestSequence = 0;
let canvasPinned = false;
let musicPinned = false;
let lastGameCanvasRevision: number | null | undefined = null;
let micMode: MicMode = readInitialMicMode();

let musicPlayer: MusicPlayerController | null = null;
let historyPaging: HistoryPagingController | null = null;
let doodleSession: DoodleSessionController | null = null;
let canvasBookmarks: CanvasBookmarksController | null = null;
let speechReplayControls: SpeechReplayControls | null = null;
let stampsController: CanvasStampsController | null = null;
let assetManager: CanvasAssetManager | null = null;
let actionWheel: ActionWheelController | null = null;
let agentConnection: AgentConnectionController | null = null;
let hotkeyControls: HotkeyControlsController | null = null;
let micConfig: MicConfigController | null = null;
let theaterModals: TheaterModalsController | null = null;

function isCurrentOrator(): boolean {
  return Boolean(window._isActiveOratorState);
}

function isCollabModeEnabled(): boolean {
  return Boolean(viewerCollabEnabled);
}

function hasContributorsPermission(): boolean {
  if (isCurrentOrator()) return true;
  if (window._isContributor) return true;
  if (currentBatonState) {
    const myId = currentUser ? currentUser.id : null;
    if (myId !== null && myId !== undefined) {
      if (currentBatonState.owner && currentBatonState.owner.id === myId) return true;
      if (currentBatonState.active_orator && currentBatonState.active_orator.id === myId) return true;
      if (
        Array.isArray(currentBatonState.contributors) &&
        currentBatonState.contributors.some((user) => user.id === myId)
      ) {
        return true;
      }
    }
  }
  return false;
}

// Keep canvas identity self-contained: this page must still recognize
// its owner if a shared UI script fails to load. Concurrent consumers
// on this page nevertheless share the same auth request.
export function getCanvasAuthState(): Promise<CanvasAuthState> {
  if (!canvasAuthStatePromise) {
    canvasAuthStatePromise = fetch('/api/auth/me')
      .then((res) => res.json() as Promise<CanvasAuthState>)
      .then(async (initialData) => {
        let data = initialData;
        const serverRunId = data.server_run_id;
        if (data && data.testing_use_local && serverRunId) {
          try {
            const storageKey = 'narratron_server_run_id';
            const storedRunId = localStorage.getItem(storageKey);
            if (storedRunId !== serverRunId) {
              if (!data.authenticated) {
                const autoRes = await fetch('/api/auth/auto-login', { method: 'POST' });
                if (autoRes.ok) {
                  const autoData = (await autoRes.json()) as { readonly user?: CanvasAuthUser | null };
                  data = { ...data, authenticated: true, user: autoData.user ?? null };
                }
              }
              localStorage.setItem(storageKey, serverRunId);
            }
          } catch (error: unknown) {
            console.warn('Canvas testing auto-login check failed:', error);
          }
        }
        if (data && data.authenticated && data.user) {
          currentUser = data.user;
          updateStampManagerProfileLinks(currentUser);
        }
        return data;
      })
      .catch((error: unknown) => {
        canvasAuthStatePromise = null;
        throw error;
      });
  }
  return canvasAuthStatePromise;
}

window.onAuthSuccess = (): void => {
  window.location.reload();
};

const urlParams = new URLSearchParams(window.location.search);
if (urlParams.has('role')) {
  urlParams.delete('role');
  const cleanQuery = urlParams.toString() ? `?${urlParams.toString()}` : window.location.pathname;
  window.history.replaceState({}, '', cleanQuery + window.location.hash);
}
const theaterId = urlParams.get('theater_id');
window.theaterId = theaterId;
window.currenttheaterId = theaterId;

// Corner panels: Scene Suggestions, Story Sticky Notes, and Hide Canvas Text
const cornerPanels = createCornerPanels({
  getTheaterId: () => theaterId,
  suggestionsButton: document.getElementById('suggestions-toggle-btn'),
  suggestionsList: document.getElementById('suggestions-list'),
  suggestionsLoading: document.getElementById('suggestions-loading'),
  stickyNotesButton: document.getElementById('sticky-notes-toggle-btn'),
  stickyNotesList: document.getElementById('sticky-notes-list'),
  stickyNotesLoading: document.getElementById('sticky-notes-loading'),
  stickyNotesBadge: document.getElementById('sticky-notes-count-badge'),
  hideCanvasTextButton: getButtonElement('hide-canvas-text-btn'),
  imageContainer: document.getElementById('image-container'),
});
window.updateCanvasStickyNotes = cornerPanels.updateCanvasStickyNotes;
window.isStickiesRevealedInCache = cornerPanels.isStickiesRevealedInCache;
window.setStickiesRevealedInCache = cornerPanels.setStickiesRevealedInCache;
window.STICKIES_REVEAL_CACHE_TIMEOUT_MS = cornerPanels.STICKIES_REVEAL_CACHE_TIMEOUT_MS;
window.setCanvasTextHidden = cornerPanels.setCanvasTextHidden;

let startAudioRecorderWorklet: StartAudioRecorderWorkletFn | undefined;
let setMicrophoneTransmissionEnabled: SetMicrophoneTransmissionEnabledFn | undefined;
let stopMicrophone: StopMicrophoneFn | undefined;
let listenForSpeech: ListenForSpeechFn | undefined;

import('../audio/audio-recorder.js')
  .then((module) => {
    startAudioRecorderWorklet = module.startAudioRecorderWorklet;
    setMicrophoneTransmissionEnabled = module.setMicrophoneTransmissionEnabled;
    stopMicrophone = module.stopMicrophone;
  })
  .catch((err: unknown) => {
    console.error('Failed to load audio-recorder.js:', err);
  });

import('../audio/device-aware-pcm.js')
  .then((module) => {
    listenForSpeech = module.listenForSpeech;
  })
  .catch((err: unknown) => {
    console.error('Failed to load device-aware-pcm.js:', err);
  });

const currentImage = getImageElement('current-image');
const canvasPinBtn = getButtonElement('canvas-pin-btn');
const canvasPinLabel = document.getElementById('canvas-pin-label');
const bgLayer = document.getElementById('bg-layer');
const promptTextSpan = document.getElementById('prompt-text');
const chatMessages = document.getElementById('chat-messages');
const chatForm = getFormElement('chat-form');
const chatInput = readChatInputElement('chat-input');
const agentThoughtText = document.getElementById('agent-thought-text');
const narrationPane = await createNarrationPane(document.getElementById('narration-pane-mount'));
const a2uiCanvasLayer = document.getElementById('a2ui-canvas-layer');
const gameStateLayer = document.getElementById('game-state-layer');
const gameStateToggleBtn = document.getElementById('game-state-toggle-btn');

const gameRenderer =
  gameStateLayer && gameStateToggleBtn
    ? createGameRenderer({
        container: gameStateLayer,
        toggleButton: gameStateToggleBtn,
        actionUrl: () => `/api/game/action?theater_id=${encodeURIComponent(theaterId || '')}`,
        canContribute: () => isCollabModeEnabled() && hasContributorsPermission(),
        canMove: () => isCurrentOrator() || hasContributorsPermission(),
        canEdit: () => isCurrentOrator(),
        refresh: () => fetchLatestImage(),
        beforeOpen: async () => {
          if (isCurrentOrator() && agentConnection && !agentConnection.isAgentStarted()) {
            await agentConnection.startAgentTheater();
          }
        },
      })
    : null;

const a2uiCanvasRenderer = a2uiCanvasLayer
  ? createA2UICanvasRenderer({
      container: a2uiCanvasLayer,
      actionUrl: () => `/api/a2ui/action?theater_id=${encodeURIComponent(theaterId || '')}`,
      surfaceUrl: (surfaceId: string) =>
        `/api/a2ui/surfaces/${encodeURIComponent(surfaceId)}?theater_id=${encodeURIComponent(theaterId || '')}`,
      canEdit: () => isCurrentOrator(),
    })
  : null;

const latestSpeechReplayBtn = getButtonElement('latest-speech-replay-btn');

const { updateSceneDialogue, enqueueSceneSpeech, stopSceneSpeech, replayLatestSpeech } =
  createCanvasScene({
    narrationPane,
    audioEnabled: () => !isMuted,
    onSpeechStateChange: (state) => speechReplayControls?.updateButtonState(state),
  });

speechReplayControls = createSpeechReplayControls({
  replayButton: latestSpeechReplayBtn,
  replayLatestSpeech,
  stopSceneSpeech,
  getMicMode: () => micMode,
});

const pinControl = createCanvasPinControl({
  button: canvasPinBtn,
  label: canvasPinLabel,
  theaterId,
  isOrator: isCurrentOrator,
  getState: () => ({ pinned: canvasPinned, musicPinned }),
  setState: (state) => {
    canvasPinned = state.pinned;
    musicPinned = state.musicPinned;
  },
  onUpdated: (state) => actionWheel?.updateState(state.pinned, state.musicPinned),
});

function updateCanvasPinButton(): void {
  pinControl.update();
}

const myUserId = `Viewer_${Math.floor(Math.random() * 10000)}`;

function getActiveAgentSocket(): WebSocket | null {
  const agentWs = agentConnection?.getAgentWs() ?? null;
  return isCurrentOrator() && agentWs ? agentWs : null;
}

chatInput?.addEventListener('focus', () => stopSceneSpeech());
const chatController = initializeChatController({
  messagesContainer: chatMessages,
  chatForm,
  chatInput,
  nameInput: getInputElement('chat-name-input'),
  nameDisplay: document.getElementById('chat-name-display'),
  nameStatusIcon: document.getElementById('chat-name-status-icon'),
  loginChip: document.getElementById('chat-login-chip'),
  onOpenLogin: () => {
    if (typeof window.openAuthModal === 'function') {
      window.openAuthModal('login');
    }
  },
  hideSuggestionsBtn: document.getElementById('hide-suggestions-btn'),
  theaterId: theaterId ?? undefined,
  isCurrentOrator,
  isTheaterOwner: () => isTheaterStampOwner,
  onSettingsApplied: async () => {
    agentConnection?.cleanupAgentConnection();
    await agentConnection?.checkAgentStatus();
  },
  getAgentWs: getActiveAgentSocket,
  getAuthState: () => getCanvasAuthState(),
});

initializeBeyond20Listener({
  chatController,
  theaterId: theaterId ?? undefined,
  isContributor: hasContributorsPermission,
  isCollabModeEnabled,
  isCurrentOrator,
  getAgentWs: getActiveAgentSocket,
});

function fetchChat(): Promise<void> {
  return chatController.fetchChat();
}

function fetchSuggestions(): Promise<void> {
  return chatController.fetchSuggestions();
}

const suggestionsHiddenStorageKey = `narratron-suggestions-hidden:${theaterId || 'default'}`;
let suggestionsHidden = false;

if (!theaterId) {
  console.error('Theater ID missing from URL parameters.');
  const container = document.getElementById('image-container');
  if (container) {
    container.innerHTML = `
      <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; color: #f87171; text-align: center; padding: 2rem;">
        <h2 style="font-size: 1.5rem; margin-bottom: 0.5rem; font-weight: 600;">⚠️ No Active Theater</h2>
        <p style="color: #94a3b8; max-width: 400px; margin-bottom: 1.5rem; font-size: 0.95rem;">No theater ID was specified in the URL. Please launch or join a theater from the deployer page.</p>
        <a href="/deploy" style="background: rgba(99, 102, 241, 0.2); border: 1px solid rgba(99, 102, 241, 0.4); color: #a5b4fc; padding: 0.6rem 1.2rem; border-radius: 20px; text-decoration: none; font-size: 0.9rem; transition: all 0.2s;">Go to Deployer Dashboard →</a>
      </div>
    `;
  }
  throw new Error('Missing required theater_id parameter in URL');
}

const activeTheaterId: string = theaterId;
const currenttheaterId = activeTheaterId;

function updateAssetPlaybackSelection(music: MusicSelectionState | null): void {
  assetMusicState = music;
  assetManager?.updateAssetPlaybackSelection(music);
}

musicPlayer = createMusicPlayer({
  theaterId: activeTheaterId,
  muteButton: document.getElementById('music-mute-btn'),
  statusElement: document.getElementById('music-status'),
  volumeSlider: getInputElement('music-volume-slider'),
  volumePercentElement: document.getElementById('volume-percent'),
  playbackStorage: window.theaterStorage ?? null,
  onMuteStopSpeech: () => stopSceneSpeech(),
  onMutedChange: (muted) => {
    isMuted = muted;
  },
  onTrackChange: () => {
    if (assetMusicState) updateAssetPlaybackSelection(assetMusicState);
  },
});

const promptBtn = document.getElementById('prompt-toggle-btn');
const agentActivity = createAgentActivityController({
  pcmBroadcastIndicator: document.getElementById('pcm-broadcast-indicator'),
  drawingIndicator: document.getElementById('agent-drawing-indicator'),
  writingIndicator: document.getElementById('agent-writing-indicator'),
  musicIndicator: document.getElementById('agent-music-indicator'),
  diceIndicator: document.getElementById('agent-dice-indicator'),
  referenceIndicator: document.getElementById('agent-reference-indicator'),
  disconnectedIndicator: document.getElementById('agent-disconnected-indicator'),
  onReconnect: () => {
    void agentConnection?.checkAgentStatus().then(() => {
      agentConnection?.ensureAgentConnectionWhenActive();
    });
  },
});

function updateAgentActivity(toolActivity: ToolActivitySnapshot = {}): void {
  agentActivity.update(toolActivity);
}

function setPcmBroadcasting(isBroadcasting: boolean): void {
  agentActivity.setPcmBroadcasting(isBroadcasting);
}

const saveTheaterBtn = getButtonElement('save-theater-btn');
if (saveTheaterBtn) {
  saveTheaterBtn.addEventListener('click', async () => {
    if (!currenttheaterId) {
      alert('No active theater ID found to save.');
      return;
    }
    const originalText = saveTheaterBtn.innerHTML;
    saveTheaterBtn.innerHTML = '⏳';
    saveTheaterBtn.disabled = true;
    try {
      const res = await fetch(`/api/theaters/${encodeURIComponent(currenttheaterId)}/save`, {
        method: 'POST',
      });
      const data = (await res.json()) as TheaterSaveResponse;
      if (res.ok) {
        saveTheaterBtn.innerHTML = '✓';
        window.setTimeout(() => {
          saveTheaterBtn.innerHTML = originalText;
          saveTheaterBtn.disabled = false;
        }, 2000);
      } else {
        alert('Save failed: ' + (data.detail || 'Unknown error'));
        saveTheaterBtn.innerHTML = originalText;
        saveTheaterBtn.disabled = false;
      }
    } catch (error: unknown) {
      alert('Failed to save theater: ' + readErrorMessage(error, 'Unknown error'));
      saveTheaterBtn.innerHTML = originalText;
      saveTheaterBtn.disabled = false;
    }
  });
}

const ghostImage = getImageElement('ghost-image');
const rendererCanvas = getCanvasElement('canvas-renderer');
const imageRenderer = createImageRenderer({
  canvas: rendererCanvas,
  image: currentImage,
  backgroundLayer: bgLayer,
  loadedClassNames: ['t-crossfade', 'loaded'],
  imageEffectsEnabled: () => !(historyPaging && historyPaging.isMinimalMode()),
});
const resizeRendererCanvas = imageRenderer.resize;

window.addEventListener('resize', resizeRendererCanvas);
resizeRendererCanvas();

const imgContainer = document.getElementById('image-container');
const pagingControls = document.getElementById('history-paging-controls');

initializeStoryLogInspector(activeTheaterId);

historyPaging = createHistoryPagingController({
  theaterId: activeTheaterId,
  isOrator: isCurrentOrator,
  imageRenderer,
  playCanvasAnimation,
  promptButton: promptBtn,
  promptTextSpan,
  currentImage,
  ghostImage,
  backgroundLayer: bgLayer,
  pagingControls,
  prevButton: getButtonElement('page-prev-btn'),
  nextButton: getButtonElement('page-next-btn'),
  headButton: document.getElementById('page-head-btn'),
  syncButton: document.getElementById('page-sync-btn'),
  pageIndicator: document.getElementById('page-indicator'),
  newHeadBadge: document.getElementById('new-head-badge'),
  imageContainer: imgContainer,
  minimalModeInput: getInputElement('minimal-mode-input'),
  hasDoodleCanvas: () => Boolean(document.getElementById('doodle-canvas')),
  isSelectionActive: () => Boolean(window.isCanvasSelectionActive),
  onRefreshBookmarks: () => canvasBookmarks?.refresh(),
  onRefreshToolInteraction: () => doodleSession?.refreshCanvasToolInteraction(),
  onDisplayPageDoodles: (item: HistoryItem) => doodleSession?.displayPageDoodles(item),
});

canvasBookmarks = initializeCanvasBookmarks({
  theaterId: activeTheaterId,
  isOrator: isCurrentOrator,
  getHistory: () => historyPaging?.getHistory() ?? [],
  getIndex: () => historyPaging?.getCurrentViewIndex() ?? 0,
  navigate: async (index: number) => {
    const response = await fetch(
      `/api/theaters/${encodeURIComponent(activeTheaterId)}/orator-action`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'bookmark_image', history_index: index }),
      },
    );
    if (!response.ok) throw new Error('Bookmark navigation failed');
    const result = (await response.json()) as TheaterActionResponse;
    if (historyPaging) {
      historyPaging.setOratorCursorIndex(Number(result.orator_cursor));
      historyPaging.displayHistoryImage(historyPaging.getOratorCursorIndex(), 'none');
    }
  },
});

const resolveWebpQualityUrl = (rawUrl: string): string =>
  historyPaging ? historyPaging.resolveWebpQualityUrl(rawUrl) : rawUrl;
window.__resolveWebpQualityUrl = resolveWebpQualityUrl;
window.__historyResourceManager = historyPaging.resourceManager;

const displayHistoryImage = (index: number, transition = 'none'): void => {
  historyPaging?.displayHistoryImage(index, transition);
};
const updatePagingControlsUI = (): void => {
  historyPaging?.updatePagingControlsUI();
};

let lastAgentThought: string | null = null;

function updateAgentThought(thought: AgentThoughtPayload | null | undefined): void {
  const text = thought && thought.text ? thought.text.trim() : '';
  agentThoughtText
    ?.closest('.agent-thought-card')
    ?.classList.toggle('is-highlighted', Boolean(thought && thought.highlighted && text));
  if (text === lastAgentThought) return;
  const isWandering = text.toLowerCase() === 'wandering';
  if (agentThoughtText) {
    agentThoughtText.textContent = isWandering
      ? 'Narratron has wandered away. (Try resummoning?)'
      : text || 'Waiting for Narratron to think…';
    agentThoughtText.classList.toggle('is-wandering', isWandering);
  }
  agentActivity.setDisconnected(isWandering);
  lastAgentThought = text;
}

const shareTheaterBtn = document.getElementById('share-theater-btn');
const clipShareBtn = document.getElementById('clip-share-btn');
const clipShareModal = document.getElementById('clip-share-modal');
const obsTheaterBtn = document.getElementById('obs-theater-btn');

const clipShare = createClipShareController({
  theaterId: activeTheaterId,
  getTheaterId: () => currenttheaterId,
  shareTheaterBtn,
  clipShareBtn,
  clipShareModal,
  clipRecordBtn: document.getElementById('clip-record-btn'),
  clipCancelBtn: document.getElementById('clip-cancel-btn'),
  obsTheaterBtn,
});

function showShareToast(message: string): void {
  clipShare.showShareToast(message);
}

async function fetchLatestImage(): Promise<void> {
  const requestSequence = ++latestStateRequestSequence;
  try {
    const apiUrl = currenttheaterId
      ? `/api/latest?theater_id=${encodeURIComponent(currenttheaterId)}`
      : '/api/latest';
    const response = await fetch(apiUrl);
    const data = (await response.json()) as CanvasLatestPayload;
    if (requestSequence !== latestStateRequestSequence) return;

    updateAgentActivity(data.tool_activity ?? {});
    updateAgentThought(data.agent_thought);
    canvasPinned = Boolean(data.pinned);
    musicPinned = Boolean(data.music_pinned);
    actionWheel?.updateState(canvasPinned, musicPinned);
    updateCanvasPinButton();
    updateSceneDialogue(
      data.scene_dialogue,
      data.narration,
      data.narration_spans,
      data.committed_scene_speech_generation,
    );
    a2uiCanvasRenderer?.render(data.interactive_surfaces ?? null);
    gameRenderer?.render(data.game_state ?? null);

    const gameCanvasRevision = data.game_state?.canvas_revision;
    if (gameCanvasRevision !== undefined && gameCanvasRevision !== lastGameCanvasRevision) {
      lastGameCanvasRevision = gameCanvasRevision;
      void requestPageDoodles();
    }
    if (data.sticky_notes !== undefined && typeof window.updateCanvasStickyNotes === 'function') {
      window.updateCanvasStickyNotes(data.sticky_notes, data.hidden_stickies);
    }
    if (data.viewer_collab_enabled !== undefined && data.viewer_collab_enabled !== viewerCollabEnabled) {
      viewerCollabEnabled = Boolean(data.viewer_collab_enabled);
      if (viewerCollabToggleBtn) {
        viewerCollabToggleBtn.textContent = viewerCollabEnabled ? '👥 Collab: On' : '👥 Collab: Off';
        viewerCollabToggleBtn.classList.toggle('active', viewerCollabEnabled);
      }
      updateTextAnnotationAvailability();
    }
    updateViewerCollabStatus(viewerCollabEnabled);

    if (data.music) {
      musicPlayer?.applyMusicState(data.music);
      updateAssetPlaybackSelection({
        ...data.music,
        music_id: data.music.music_id ?? undefined,
        playlist: data.music.playlist ?? undefined,
      });
    }

    historyPaging?.applyLatestState(data);
  } catch (err: unknown) {
    console.error('Failed to fetch image data:', err);
  }
}

const canvas = getCanvasElement('doodle-canvas');
const doodlesToggleBtn = getButtonElement('doodles-toggle-btn');
const roleBadge = document.getElementById('role-badge');
const oratorControls = document.getElementById('orator-controls');
const agentStartStopBtn = getButtonElement('agent-start-stop-btn');
const agentConnectBtn = getButtonElement('agent-connect-btn');
const statusIndicator = document.getElementById('status-indicator');
const viewerCollabToggleBtn = getButtonElement('viewer-collab-toggle-btn');
const collabSection = document.getElementById('collab-section');
const hideSuggestionsBtn = getButtonElement('hide-suggestions-btn');
const backBtn = getAnchorElement('back-btn');
const suggestionsToggleBtn = document.getElementById('suggestions-toggle-btn');
const stickyNotesToggleBtn = document.getElementById('sticky-notes-toggle-btn');

if (!canvas || !imgContainer) {
  throw new Error('Canvas workspace elements (#doodle-canvas and #image-container) are missing');
}

doodleSession = createDoodleSession({
  theaterId: currenttheaterId,
  canvas,
  imgContainer,
  pagingControls,
  doodleToolbar: document.getElementById('doodle-toolbar'),
  doodlesToggleBtn,
  paletteToggle: document.getElementById('doodle-palette-toggle'),
  paletteMenu: document.getElementById('doodle-palette-menu'),
  colorBtns: Array.from(document.querySelectorAll<HTMLElement>('.color-btn')),
  clearBtn: document.getElementById('clear-doodle-btn'),
  undoBtn: getButtonElement('undo-doodle-btn'),
  brushSizeSlider: getInputElement('brush-size'),
  textAnnotationToggle: getButtonElement('text-annotation-toggle'),
  textAnnotationMenu: document.getElementById('text-annotation-menu'),
  textAnnotationFont: (() => {
    const el = document.getElementById('text-annotation-font');
    return el instanceof HTMLSelectElement ? el : null;
  })(),
  textAnnotationSize: getInputElement('text-annotation-size'),
  textAnnotationSizeValue: document.getElementById('text-annotation-size-value'),
  textAnnotationColors: Array.from(document.querySelectorAll<HTMLElement>('.text-annotation-color')),
  textLayer: document.getElementById('canvas-text-layer'),
  stampLayer: document.getElementById('canvas-stamp-layer'),
  isOrator: isCurrentOrator,
  isCollabModeEnabled,
  isPagedBack: () => Boolean(historyPaging?.isPagedBack()),
  getPage: () => historyPaging?.getCurrentItem()?.canvas_page || null,
  canPlaceStamps: () => canPlaceStamps(),
  stopSceneSpeech: () => stopSceneSpeech(),
  setDrawingColorSelected: (selected) => narrationPane.setDrawingColorSelected(selected),
  onMoveStamp: (action, previous, commit) => stampsController?.moveStampAnnotation(action, previous, commit),
  onRemoveStamp: (action) => stampsController?.removeStampAnnotation(action),
  onSelectStamp: (action) => stampsController?.selectStampAnnotation(action),
  onApplyStampAnnotation: (action) => stampsController?.applyStampAnnotation(action),
  onBringStampToFront: (stampId) => stampsController?.bringStampToFront(stampId),
  onDoodlesDisplayChanged: () => {
    if (isStampManagerOpen) {
      updateStampManagerPermissionBanner();
      if (cachedUserStamps) renderStampManagerGrid(cachedUserStamps);
    }
  },
  onHandleSocketData: (data) => Boolean(assetManager?.handleDoodleSocketData(data)),
  onToggleMicSocketMessage: () => {
    if (isCurrentOrator() && agentConnectBtn && !agentConnectBtn.disabled) {
      agentConnectBtn.click();
    }
  },
  onBatonStateSocketMessage: (batonState, activeViewers) => {
    updateBatonStateUI(
      batonState as BatonState,
      Array.isArray(activeViewers) ? (activeViewers as readonly BatonUser[]) : null,
    );
  },
  isActionWheelRightClickBound: () =>
    Boolean(isCurrentOrator() && actionWheel && actionWheel.isRightClickBound()),
});

const doodleRenderer = doodleSession.doodleRenderer;
const doodleUndoManager = doodleSession.doodleUndoManager;
const resizeDoodleCanvas = (): void => doodleSession?.resizeDoodleCanvas();
const redrawAllDoodles = (): void => doodleSession?.redrawAllDoodles();
const requestPageDoodles = (pageId?: string | null): Promise<void> =>
  doodleSession ? doodleSession.requestPageDoodles(pageId) : Promise.resolve();
const sendOrQueueDoodleMessage = (message: DoodleMessage): void =>
  doodleSession?.sendOrQueueDoodleMessage(message);
const pushUndoAction = (action: DoodleActionInput): void => doodleSession?.pushUndoAction(action);
const undoLastDoodleAction = (): boolean => doodleSession?.undoLastDoodleAction() ?? false;
const updateTextAnnotationAvailability = (): void =>
  doodleSession?.updateTextAnnotationAvailability();
const setDoodlesDisplay = (enabled: boolean): void => doodleSession?.setDoodlesDisplay(enabled);

window.doodleUndoManager = doodleUndoManager;
try {
  Object.defineProperty(window, 'doodleUndoStack', {
    get(): DoodleAction[] {
      return doodleSession?.getDoodleUndoStack() ?? [];
    },
    set(val: DoodleAction[]): void {
      doodleSession?.setDoodleUndoStack(val);
    },
    configurable: true,
  });
} catch {
  compatWindow.doodleUndoStack = doodleSession.getDoodleUndoStack();
}
window.undoLastDoodleAction = undoLastDoodleAction;
window.undoDoodleAction = undoLastDoodleAction;
window.DoodleAction = DoodleAction;
window.DrawStrokeDoodleAction = DrawStrokeDoodleAction;
window.DeleteSegmentsDoodleAction = DeleteSegmentsDoodleAction;
window.PlaceTextDoodleAction = PlaceTextDoodleAction;
window.EditTextDoodleAction = EditTextDoodleAction;
window.MoveTextDoodleAction = MoveTextDoodleAction;
window.DeleteTextDoodleAction = DeleteTextDoodleAction;
window.PlaceStampDoodleAction = PlaceStampDoodleAction;
window.TransformStampDoodleAction = TransformStampDoodleAction;
window.DeleteStampDoodleAction = DeleteStampDoodleAction;
window.CompoundDeleteDoodleAction = CompoundDeleteDoodleAction;
window.ClearCanvasDoodleAction = ClearCanvasDoodleAction;
window.restoreSegmentsToServer = restoreSegmentsToServer;

// Asset Manager & Canvas Stamp Dropping
const stampManagerCount = document.getElementById('stamp-manager-count');
const stampTabStampsBtn = document.getElementById('stamp-tab-stamps');
const stampTabScenesBtn = document.getElementById('stamp-tab-scenes');
const stampTabCharactersBtn = document.getElementById('stamp-tab-characters');
const expandedPlaylists = new Set<string>();

stampsController = createCanvasStampsController({
  canvas,
  imgContainer,
  getTheaterId: () =>
    theaterId || currenttheaterId || new URLSearchParams(window.location.search).get('theater_id'),
  getCurrentUser: () => currentUser,
  setCurrentUser: (user) => {
    currentUser = user as CanvasAuthUser | null;
  },
  getMyUserId: () => myUserId,
  getAuthState: () => getCanvasAuthState(),
  openAuthModal: (mode) => {
    if (typeof window.openAuthModal === 'function') {
      window.openAuthModal(mode);
    }
  },
  hasContributorPermission: hasContributorsPermission,
  isOrator: isCurrentOrator,
  isOwner: () => isTheaterStampOwner,
  isDoodlesEnabled: () => doodleSession?.isDoodlesEnabled() ?? false,
  isPagedBack: () => historyPaging?.isPagedBack() ?? false,
  enableDoodles: () => {
    setDoodlesDisplay(true);
    sendOrQueueDoodleMessage({ type: 'toggle_doodles', enabled: true });
  },
  selectSelectCursor: () => canvasCursorState.select({ type: 'select' }),
  getDoodleActions: () => doodleSession?.getDoodleActions() ?? [],
  redrawDoodles: redrawAllDoodles,
  sendDoodleMessage: sendOrQueueDoodleMessage,
  rememberPendingEdit: (clientMessageId, previous) => {
    doodleSession?.getPendingTextEdits().set(clientMessageId, {
      type: previous.type,
      id: previous.id,
      stamp_id: previous.stamp_id,
      user_id: previous.user_id,
      canvas_asset_id: previous.canvas_asset_id,
      managed_by: previous.managed_by,
      url: previous.url,
      name: previous.name,
      x: previous.x,
      y: previous.y,
      size: previous.size,
      rotation: previous.rotation,
      client_message_id: previous.client_message_id,
    });
  },
  getActiveDeleteTransaction: () => doodleSession?.getActiveDeleteTransaction() ?? null,
  pushUndoAction,
  getActiveTab: () => activeStampManagerTab,
  isPlaylistExpanded: (playlistName) => expandedPlaylists.has(playlistName),
  resolveImageUrl: resolveWebpQualityUrl,
  getNormalizedDropPoint: (event) =>
    historyPaging?.getCurrentItem()?.canvas_page ? doodleRenderer.normalizedPoint(event) : null,
  getReferenceSources: () => ({
    characters: cachedTheaterCharacters ?? window.cachedTheaterCharacters,
    scenes: cachedTheaterScenes ?? window.cachedTheaterScenes,
    references: window.cachedTheaterReferences,
  }),
  onTabsVisibilityUpdate: () => {
    assetManager?.updateStampTabsVisibility();
  },
  onGenerationPermissionUpdate: () => {
    assetManager?.updateAssetGenerationUI();
    const sceneForm = document.getElementById('scene-generation-form');
    if (sceneForm) {
      sceneForm.hidden = !isTheaterStampOwner || activeStampManagerTab !== 'scenes';
    }
    const sceneStatus = document.getElementById('scene-generation-status');
    if (sceneStatus) {
      sceneStatus.hidden = !isTheaterStampOwner || activeStampManagerTab !== 'scenes';
    }
  },
  onOpenNonStampTab: (tab) => {
    if (tab === 'scenes') {
      if (cachedTheaterScenes) assetManager?.renderScenesManagerGrid(cachedTheaterScenes);
      void assetManager?.loadTheaterScenes();
    } else if (tab === 'characters') {
      if (cachedTheaterCharacters) {
        assetManager?.renderCharactersManagerGrid(cachedTheaterCharacters);
      }
      void assetManager?.loadTheaterCharacters();
    } else if (tab === 'playlists' || tab === 'animations') {
      void assetManager?.loadExtraAssets(tab);
    }
  },
  getIsOpen: () => isStampManagerOpen,
  setIsOpen: (open) => {
    isStampManagerOpen = open;
  },
  getCachedUserStamps: () => cachedUserStamps,
  setCachedUserStamps: (stamps) => {
    cachedUserStamps = stamps;
  },
  getCachedTheaterStamps: () => cachedTheaterStamps ?? window.cachedTheaterStamps ?? emptyTheaterStamps,
  setCachedTheaterStamps: (stamps) => {
    cachedTheaterStamps = stamps;
  },
});

const pendingStampGenerations = stampsController.pendingGenerations;

function updateStampGenerationPermission(): void {
  stampsController?.updateGenerationPermission();
}

function extractTaggedCanvasReferences(promptText: string): string[] {
  return stampsController ? stampsController.extractTaggedReferences(promptText) : [];
}

function initCanvasReferenceAutocomplete(): void {
  for (const id of [
    'stamp-generation-prompt',
    'scene-generation-prompt',
    'character-generation-prompt',
    'animation-generation-prompt',
  ]) {
    stampsController?.attachReferenceAutocomplete(getTextAreaElement(id));
  }
}

initCanvasReferenceAutocomplete();

function applyAssetSearch(): void {
  stampsController?.applyAssetSearch();
}

function updateStampManagerPermissionBanner(): void {
  stampsController?.updatePermissionBanner();
}

function loadUserStamps(): Promise<void> {
  return stampsController ? stampsController.loadUserStamps() : Promise.resolve();
}

function renderStampManagerGrid(stamps: readonly StampItem[]): void {
  stampsController?.renderGrid(stamps);
}

function canPlaceStamps(): boolean {
  return stampsController ? stampsController.canPlaceStamps() : false;
}

stampTabStampsBtn?.addEventListener('click', () => {
  assetManager?.switchStampManagerTab('stamps');
});
stampTabScenesBtn?.addEventListener('click', () => {
  assetManager?.switchStampManagerTab('scenes');
});
stampTabCharactersBtn?.addEventListener('click', () => {
  assetManager?.switchStampManagerTab('characters');
});

assetManager = createCanvasAssetManager({
  getTheaterId: () =>
    theaterId || currenttheaterId || new URLSearchParams(window.location.search).get('theater_id'),
  isOrator: isCurrentOrator,
  isStampOwner: () => Boolean(isTheaterStampOwner),
  isManagerOpen: () => Boolean(isStampManagerOpen),
  getActiveTab: () => activeStampManagerTab,
  setActiveTab: (tab) => {
    activeStampManagerTab = tab;
  },
  getCachedScenes: () => cachedTheaterScenes,
  setCachedScenes: (scenes) => {
    cachedTheaterScenes = scenes;
  },
  getCachedCharacters: () => cachedTheaterCharacters,
  setCachedCharacters: (characters) => {
    cachedTheaterCharacters = characters;
  },
  getCurrentTrackSrc: () => (musicPlayer ? musicPlayer.getCurrentTrackSrc() : null),
  setCurrentTrackSrc: (src) => {
    musicPlayer?.setCurrentTrackSrc(src);
  },
  nameFromPrompt: (prompt) => stampNameFromPrompt(prompt),
  extractTaggedReferences: (prompt) => extractTaggedCanvasReferences(prompt),
  applyAssetSearch,
  resolveWebpQualityUrl,
  showShareToast,
  fetchLatestImage: () => {
    void fetchLatestImage();
  },
  refreshCanvasState: (domains) => refreshCanvasState(domains),
  onShowStampsTab: () => {
    updateStampGenerationPermission();
    updateStampManagerPermissionBanner();
    if (stampManagerCount) {
      const count = cachedUserStamps ? cachedUserStamps.length : 0;
      stampManagerCount.textContent = `${count}/10`;
      const theaterCount = cachedTheaterStamps ? cachedTheaterStamps.length : 0;
      const pendingCount = pendingStampGenerations.size
        ? ` · ${pendingStampGenerations.size} generating`
        : '';
      stampManagerCount.title = `${count} user stamps · ${theaterCount} theater stamps${pendingCount}`;
    }
    if (cachedUserStamps) {
      renderStampManagerGrid(cachedUserStamps);
    } else {
      void loadUserStamps();
    }
  },
  onTheaterStampsUpdated: (stamps) => {
    cachedTheaterStamps = stamps.map((item) => ({
      id: item.id,
      name: item.name ?? '',
      url: item.url ?? '',
    }));
    if (isStampManagerOpen) {
      renderStampManagerGrid(cachedUserStamps || []);
    }
  },
  expandedPlaylists,
});

// Canvas-state notifications are deliberately separate from the
// doodle socket. They invalidate REST data; they never carry canvas
// state themselves, so reconnects and rolling deploys remain simple.
let canvasStateWs: WebSocket | null = null;
let canvasStateReconnectDelay = 1000;
let canvasStateReconnectTimer: number | null = null;
let canvasStateRefreshTimer: number | null = null;
const pendingCanvasStateDomains = new Set<string>();

function refreshCanvasState(domains: readonly string[] = ['latest', 'chat', 'suggestions']): void {
  domains.forEach((domain) => pendingCanvasStateDomains.add(domain));
  if (canvasStateRefreshTimer !== null) return;
  canvasStateRefreshTimer = window.setTimeout(() => {
    canvasStateRefreshTimer = null;
    const pending = new Set(pendingCanvasStateDomains);
    pendingCanvasStateDomains.clear();
    if (pending.has('latest')) void fetchLatestImage();
    if (pending.has('chat')) void fetchChat();
    if (pending.has('suggestions')) void fetchSuggestions();
  }, 50);
}

function connectCanvasStateSocket(): void {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socketUrl =
    `${protocol}//${window.location.host}/ws/canvas-state` +
    (currenttheaterId ? `?theater_id=${encodeURIComponent(currenttheaterId)}` : '');
  canvasStateWs = new WebSocket(socketUrl);

  canvasStateWs.onopen = () => {
    canvasStateReconnectDelay = 1000;
  };
  canvasStateWs.onmessage = (event: MessageEvent<string>) => {
    let message: CanvasStateSocketMessage;
    try {
      message = JSON.parse(event.data) as CanvasStateSocketMessage;
    } catch (error: unknown) {
      console.warn('Ignoring invalid canvas-state message.', error);
      return;
    }
    if (message.type === 'state_ready') {
      refreshCanvasState();
    } else if (message.type === 'scene_speech_ready' && message.audio_url) {
      enqueueSceneSpeech(message.audio_url, message.speech_generation ?? 0);
    } else if (message.type === 'state_changed' && Array.isArray(message.domains)) {
      refreshCanvasState(message.domains);
    }
  };
  canvasStateWs.onclose = () => {
    canvasStateWs = null;
    if (canvasStateReconnectTimer === null) {
      canvasStateReconnectTimer = window.setTimeout(() => {
        canvasStateReconnectTimer = null;
        connectCanvasStateSocket();
      }, canvasStateReconnectDelay);
      canvasStateReconnectDelay = Math.min(canvasStateReconnectDelay * 2, 30000);
    }
  };
}

startCanvasStatePolling(refreshCanvasState);
refreshCanvasState();
connectCanvasStateSocket();

const collabBar = createCollabBarController({
  theaterId: activeTheaterId,
  bar: document.getElementById('viewer-collab-bar'),
  barStatus: document.getElementById('viewer-collab-bar-status'),
  guideBtn: document.getElementById('viewer-collab-guide-btn'),
  guideModal: document.getElementById('viewer-collab-guide-modal'),
  guideCloseBtn: document.getElementById('viewer-collab-guide-close-btn'),
  guideAckBtn: document.getElementById('viewer-collab-guide-ack-btn'),
  guideStatus: document.getElementById('viewer-collab-status'),
  isOrator: isCurrentOrator,
  isCollabEnabled: () => viewerCollabEnabled,
  onBarUpdated: () => {
    gameRenderer?.render();
  },
});
updateTextAnnotationAvailability();

function updateViewerCollabBar(enabled: boolean): void {
  collabBar.updateBar(enabled);
}

function updateViewerCollabStatus(enabled: boolean): void {
  collabBar.updateStatus(enabled);
}

function setSuggestionsHidden(hidden: boolean, persist = true): void {
  suggestionsHidden = Boolean(hidden);
  chatMessages?.classList.toggle('suggestions-hidden', suggestionsHidden);
  if (hideSuggestionsBtn) {
    hideSuggestionsBtn.textContent = suggestionsHidden ? '👁' : '🙈';
    hideSuggestionsBtn.setAttribute(
      'aria-label',
      suggestionsHidden ? 'Show viewer suggestions' : 'Hide viewer suggestions',
    );
    hideSuggestionsBtn.classList.toggle('is-hidden', suggestionsHidden);
    hideSuggestionsBtn.title = suggestionsHidden
      ? 'Show viewer suggestions in this chat'
      : 'Hide viewer suggestions from this chat';
  }
  if (persist) {
    writeStoredValue(suggestionsHiddenStorageKey, String(suggestionsHidden));
  }
}

if (viewerCollabToggleBtn) {
  viewerCollabToggleBtn.addEventListener('click', async () => {
    const requestedState = !viewerCollabEnabled;
    try {
      const response = await fetch(`/api/theaters/${encodeURIComponent(activeTheaterId)}/collab`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: requestedState }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = (await response.json()) as TheaterCollabResponse;
      viewerCollabEnabled = Boolean(result.viewer_collab_enabled);
      viewerCollabToggleBtn.textContent = viewerCollabEnabled
        ? '👥 Collab: On'
        : '👥 Collab: Off';
      viewerCollabToggleBtn.classList.toggle('active', viewerCollabEnabled);
      updateTextAnnotationAvailability();
      updateViewerCollabStatus(viewerCollabEnabled);
    } catch (err: unknown) {
      console.error('Failed to toggle viewer collab mode:', err);
    }
  });
}

if (hideSuggestionsBtn) {
  hideSuggestionsBtn.addEventListener('click', () => setSuggestionsHidden(!suggestionsHidden));
}
setSuggestionsHidden(readStoredFlag(suggestionsHiddenStorageKey), false);

agentConnection = createAgentConnectionController({
  theaterId: activeTheaterId,
  agentStartStopBtn,
  agentConnectBtn,
  statusIndicator,
  insufficientCreditsModal: document.getElementById('insufficient-credits-modal'),
  creditsModalMessage: document.getElementById('credits-modal-message'),
  creditsModalDeployBtn: document.getElementById('credits-modal-deploy-btn'),
  creditsModalDismissBtn: document.getElementById('credits-modal-dismiss-btn'),
  creditsModalCloseBtn: document.getElementById('credits-modal-close-btn'),
  isCurrentOrator,
  isActiveCanvasWindow,
  getMicMode: () => micMode,
  refreshMicrophoneDevices: (requestPermission = false) =>
    micConfig ? micConfig.refreshMicrophoneDevices(requestPermission) : Promise.resolve(),
  getStartAudioRecorderWorklet: () => startAudioRecorderWorklet,
  getSetMicrophoneTransmissionEnabled: () => setMicrophoneTransmissionEnabled,
  getStopMicrophone: () => stopMicrophone,
  closeGameRenderer: () => gameRenderer?.close(),
  showShareToast,
  updateAgentThought,
  updateAgentActivity: (toolActivity) => updateAgentActivity(toolActivity ?? {}),
  setPcmBroadcasting,
  stopSceneSpeech,
});

// Expose helper functions on window for automated evaluation.
window.enableAudioInput = (): void => {
  agentConnection?.setRecording(true);
  if (agentConnectBtn) {
    agentConnectBtn.textContent = '🎙️ Mic Off';
    agentConnectBtn.style.background = '#ef4444';
  }
};

window.connectAgentAndMic = async (): Promise<void> => {
  if (!agentConnection) return;
  if (!agentConnection.isAgentStarted()) {
    try {
      await agentConnection.startAgentTheater();
    } catch (error: unknown) {
      console.error('Failed to start agent theater before connecting:', error);
    }
  }
  void agentConnection.connectAgent();
  const checkConnect = window.setInterval(() => {
    const ws = agentConnection?.getAgentWs();
    if (ws && ws.readyState === WebSocket.OPEN) {
      window.enableAudioInput?.();
      window.clearInterval(checkConnect);
    }
  }, 100);
};

window.sendOratorAudio = (base64Data: string): void => {
  const ws = window.agentWs || agentConnection?.getAgentWs();
  if (ws && ws.readyState === WebSocket.OPEN) {
    const binary = atob(base64Data);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i);
    }
    ws.send(bytes.buffer);
  } else {
    console.warn('[OratorAudio] WebSocket not ready. State:', ws ? ws.readyState : 'null');
  }
};

window.streamFullAudio = async (
  fullBase64Data: string,
  chunkSize = 2048,
  delayMs = 64,
): Promise<void> => {
  const binary = atob(fullBase64Data);
  const totalBytes = binary.length;
  let offset = 0;
  const ws = window.agentWs || agentConnection?.getAgentWs();

  return new Promise<void>((resolve) => {
    const interval = window.setInterval(() => {
      if (offset >= totalBytes) {
        window.clearInterval(interval);
        resolve();
        return;
      }
      const end = Math.min(offset + chunkSize, totalBytes);
      const chunk = new Uint8Array(end - offset);
      for (let i = offset; i < end; i += 1) {
        chunk[i - offset] = binary.charCodeAt(i);
      }
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(chunk.buffer);
      }
      offset = end;
    }, delayMs);
  });
};

hotkeyControls = createHotkeyControlsController({
  isCurrentOrator,
  getMicMode: () => micMode,
  getMicPressSource: () => agentConnection?.getMicPressSource() ?? null,
  beginMicPress: (source) => agentConnection?.beginMicPress(source),
  releaseMicPress: () => agentConnection?.releaseMicPress(),
  agentStartStopBtn,
  agentConnectBtn,
  menuItemHotkey: document.getElementById('menu-item-hotkey'),
  menuHotkeyDisplay: document.getElementById('menu-hotkey-display'),
  howtoMicDisplay: document.getElementById('howto-mic-hotkey-display'),
  menuItemTextHotkey: document.getElementById('menu-item-text-hotkey'),
  menuTextHotkeyDisplay: document.getElementById('menu-text-hotkey-display'),
  howtoTextDisplay: document.getElementById('howto-text-hotkey-display'),
  dismissHotkeyDisplay: document.getElementById('orator-command-dismiss-hotkey'),
  oratorCommandOverlay: document.getElementById('orator-command-overlay'),
  oratorCommandForm: getFormElement('orator-command-form'),
  oratorCommandInput: getInputElement('orator-command-input'),
  oratorCommandStatus: document.getElementById('orator-command-status'),
  getTheaterId: () => activeTheaterId,
  stopSceneSpeech,
  onRebindStart: () => actionWheel?.cancelRebinding(),
});

actionWheel = initializeActionWheel({
  isOrator: isCurrentOrator,
  canvasTarget: document.getElementById('image-container'),
  onOpen: () => {
    doodleSession?.cancelDrawing();
  },
  onCenterAction: () => {
    doodleSession?.cycleCanvasCursor();
  },
  onRebindStart: () => {
    hotkeyControls?.cancelRebinding();
  },
  onPreviousImage: () => {
    historyPaging?.stepOratorPreviousImage();
  },
  sendAction: async (action) => {
    const response = await fetch(
      `/api/theaters/${encodeURIComponent(theaterId ?? '')}/orator-action`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      },
    );
    const result = (await response.json()) as TheaterActionResponse;
    if (!response.ok) {
      throw new Error(result.detail || 'Unable to apply action.');
    }
    canvasPinned = Boolean(result.pinned);
    musicPinned = Boolean(result.music_pinned);
    if (result.orator_cursor !== undefined && result.orator_cursor !== null && historyPaging) {
      historyPaging.setOratorCursorIndex(Number(result.orator_cursor));
      if (
        !historyPaging.isNavigating() &&
        historyPaging.getCurrentViewIndex() !== historyPaging.getOratorCursorIndex()
      ) {
        historyPaging.displayHistoryImage(historyPaging.getOratorCursorIndex(), 'none');
      }
    }
    updateCanvasPinButton();
    actionWheel?.updateState(canvasPinned, musicPinned);
  },
});

compatWindow.setMicHotkey = hotkeyControls.setMicHotkey;
window.setTextHotkey = hotkeyControls.setTextHotkey;
window.setTextCommandHotkey = hotkeyControls.setTextHotkey;

const fullscreenControls = createFullscreenControlsController({
  fullscreenBtn: document.getElementById('fullscreen-btn'),
  fullscreenIcon: document.getElementById('fullscreen-icon'),
  container: imgContainer,
  sidebar: document.getElementById('chat-sidebar'),
  expandButton: document.getElementById('popout-expand-btn'),
  sideToggleButton: document.getElementById('chat-side-toggle-btn'),
  onResizeCanvases: () => {
    resizeRendererCanvas();
    resizeDoodleCanvas();
  },
});

window.toggleFullScreen = fullscreenControls.toggleFullScreen;
window.togglePopoutPanel = (): void => fullscreenControls.togglePanel();

micConfig = createMicConfigController({
  micModeToggle: getInputElement('menu-mic-mode-toggle'),
  micDeviceSelect: (() => {
    const el = document.getElementById('menu-mic-device-select');
    return el instanceof HTMLSelectElement ? el : null;
  })(),
  micDeviceRefresh: document.getElementById('menu-mic-device-refresh'),
  micDeviceHint: document.getElementById('menu-mic-device-hint'),
  micTestRecordBtn: getButtonElement('menu-mic-test-record'),
  micTestPlayBtn: getButtonElement('menu-mic-test-play'),
  micTestStatus: document.getElementById('menu-mic-test-status'),
  micSensSlider: getInputElement('menu-mic-sensitivity-slider'),
  micSensValDisplay: document.getElementById('menu-mic-sensitivity-val'),
  micConfigModal: document.getElementById('mic-config-modal'),
  menuItemMicConfig: document.getElementById('menu-item-mic-config'),
  micConfigCloseBtn: document.getElementById('mic-config-close-btn'),
  micConfigDoneBtn: document.getElementById('mic-config-done-btn'),
  oratorHowtoModal: document.getElementById('orator-howto-modal'),
  howtoCloseBtn: document.getElementById('howto-close-btn'),
  howtoAckBtn: document.getElementById('howto-ack-btn'),
  getMicMode: () => micMode,
  setMicMode: (mode) => {
    micMode = mode;
  },
  isRecording: () => agentConnection?.isRecording() ?? false,
  cancelMicCapture: () => agentConnection?.cancelMicCapture(),
  updateMicToggleUI: () => agentConnection?.updateMicToggleUI(),
  getListenForSpeech: () => listenForSpeech,
  getAuthState: () => getCanvasAuthState(),
  invalidateAuthStateCache: () => {
    canvasAuthStatePromise = null;
  },
  onOpenMicConfig: () => {
    theaterModals?.closePulloutMenu();
  },
  onCloseMicConfig: () => {
    actionWheel?.cancelRebinding();
    hotkeyControls?.cancelRebinding();
  },
});

theaterModals = createTheaterModalsController({
  theaterId: activeTheaterId,
  getTheaterId: () => currenttheaterId,
  pulloutOverlay: document.getElementById('pullout-overlay'),
  menuLeftToggleBtn: document.getElementById('menu-left-toggle-btn'),
  pulloutCloseBtn: document.getElementById('pullout-close-btn'),
  menuItemHowto: document.getElementById('menu-item-howto'),
  menuItemShare: document.getElementById('menu-item-share'),
  menuItemClip: document.getElementById('menu-item-clip'),
  menuItemObs: document.getElementById('menu-item-obs'),
  menuItemTheaterBuilder: getAnchorElement('menu-item-theater-builder'),
  suggestionsToggleBtn,
  stickyNotesToggleBtn,
  shareTheaterBtn,
  clipShareBtn,
  obsTheaterBtn,
  onOpenOratorHowto: () => micConfig?.openOratorHowtoModal(),
  onShareTheater: () => {
    void clipShare.shareTheaterLink();
  },
  onOpenClipShare: () => clipShare.openClipShareModal(),
  onCopyObsLink: () => {
    void clipShare.copyObsLink();
  },
  collabGuide: collabBar,
  adventureGuideModal: document.getElementById('adventure-guide-modal'),
  menuItemAdventureGuide: document.getElementById('menu-item-adventure-guide'),
  adventureGuideCloseBtn: document.getElementById('adventure-guide-close-btn'),
  adventureGuideAckBtn: document.getElementById('adventure-guide-ack-btn'),
  theaterConfigModal: document.getElementById('theater-config-modal'),
  menuItemTheaterConfig: document.getElementById('menu-item-theater-config'),
  configModalCloseBtn: document.getElementById('config-modal-close-btn'),
  configModalCancelBtn: document.getElementById('config-modal-cancel-btn'),
  configModalFormatBtn: document.getElementById('config-modal-format-btn'),
  configModalSaveBtn: getButtonElement('config-modal-save-btn'),
  theaterConfigEditor: getTextAreaElement('theater-config-editor'),
  theaterConfigStatus: document.getElementById('theater-config-status'),
  isOratorCommandOpen: () => hotkeyControls?.isOratorCommandOpen() ?? false,
  closeOratorCommand: () => hotkeyControls?.closeOratorCommand(),
  isMicConfigOpen: () => micConfig?.isMicConfigOpen() ?? false,
  closeMicConfigModal: () => micConfig?.closeMicConfigModal(),
  isOratorHowtoOpen: () => micConfig?.isOratorHowtoOpen() ?? false,
  closeOratorHowtoModal: () => micConfig?.closeOratorHowtoModal(),
  isClipShareOpen: () => clipShare.isClipShareOpen(),
  closeClipShareModal: () => clipShare.closeClipShareModal(),
});

window.openPulloutMenu = theaterModals.openPulloutMenu;
window.closePulloutMenu = theaterModals.closePulloutMenu;
window.openOratorHowtoModal = micConfig.openOratorHowtoModal;
window.closeOratorHowtoModal = micConfig.closeOratorHowtoModal;
window.openMicConfigModal = micConfig.openMicConfigModal;
window.closeMicConfigModal = micConfig.closeMicConfigModal;
window.openTheaterConfigModal = theaterModals.openTheaterConfigModal;
window.closeTheaterConfigModal = theaterModals.closeTheaterConfigModal;
window.openAdventureGuideModal = theaterModals.openAdventureGuideModal;
window.closeAdventureGuideModal = theaterModals.closeAdventureGuideModal;
window.initAdventureGuide = theaterModals.initAdventureGuide;
setupTheaterReport(activeTheaterId, theaterModals.closePulloutMenu);

function applyRoleUI(isOrator: boolean): void {
  const wasActiveOrator = window._isActiveOratorState;
  window._isActiveOratorState = Boolean(isOrator);
  const isNowOrator = window._isActiveOratorState;
  updateViewerCollabBar(viewerCollabEnabled);
  updateCanvasPinButton();
  updateTextAnnotationAvailability();
  assetManager?.updateStampTabsVisibility();
  theaterModals?.applyCornerControlsRoleVisibility(isNowOrator);

  if (isNowOrator) {
    if (historyPaging) {
      const cursorIndex = historyPaging.getOratorCursorIndex();
      if (cursorIndex >= 0 && cursorIndex < historyPaging.getHistory().length) {
        displayHistoryImage(cursorIndex, 'none');
      }
      updatePagingControlsUI();
    }
    if (backBtn) backBtn.href = '/deploy?from=canvas';
    if (roleBadge) {
      roleBadge.textContent = 'Orator';
      roleBadge.style.color = '#f97316';
      roleBadge.style.background = 'rgba(249, 115, 22, 0.2)';
      roleBadge.style.borderColor = 'rgba(249, 115, 22, 0.4)';
    }
    if (oratorControls) oratorControls.style.display = 'flex';
    if (doodlesToggleBtn) doodlesToggleBtn.style.display = 'inline-flex';
    if (collabSection) collabSection.style.display = 'flex';
    if (hideSuggestionsBtn) hideSuggestionsBtn.style.display = 'inline-flex';

    const existingWs = agentConnection?.getAgentWs() ?? null;
    if (!wasActiveOrator) {
      agentConnection
        ?.checkAgentStatus()
        .then((status) => {
          const ws = agentConnection?.getAgentWs() ?? null;
          if (status?.agent_running && (!ws || ws.readyState !== WebSocket.OPEN)) {
            agentConnection?.connectAgent().catch((err: unknown) => {
              console.warn('Auto-connect agent on baton pass failed:', err);
            });
          }
        })
        .catch(() => {});
    } else if (!existingWs || existingWs.readyState !== WebSocket.OPEN) {
      statusIndicator?.classList.add('disconnected');
    }

    if (!hasStoredFlag('narratron_orator_howto_seen')) {
      window.setTimeout(() => micConfig?.openOratorHowtoModal({ openMicConfigOnClose: true }), 400);
    }
  } else {
    if (backBtn) backBtn.href = '/';
    if (roleBadge) {
      roleBadge.textContent = 'Viewer';
      roleBadge.style.color = '#a5b4fc';
      roleBadge.style.background = 'rgba(99, 102, 241, 0.2)';
      roleBadge.style.borderColor = 'rgba(99, 102, 241, 0.4)';
    }
    if (oratorControls) oratorControls.style.display = 'none';
    if (doodlesToggleBtn) doodlesToggleBtn.style.display = 'none';
    if (collabSection) collabSection.style.display = 'none';
    if (hideSuggestionsBtn) hideSuggestionsBtn.style.display = 'none';

    if (
      agentConnection &&
      (agentConnection.isMicCaptureReady() ||
        agentConnection.isMicAwaiting() ||
        agentConnection.isMicPressHeld())
    ) {
      agentConnection.cancelMicCapture();
    }
    statusIndicator?.classList.remove('disconnected');
  }

  if (isStampManagerOpen) {
    assetManager?.updateStampTabsVisibility();
    if (activeStampManagerTab === 'stamps') {
      updateStampManagerPermissionBanner();
      if (cachedUserStamps) renderStampManagerGrid(cachedUserStamps);
    } else if (activeStampManagerTab === 'scenes') {
      if (cachedTheaterScenes) assetManager?.renderScenesManagerGrid(cachedTheaterScenes);
    } else if (activeStampManagerTab === 'characters') {
      if (cachedTheaterCharacters) {
        assetManager?.renderCharactersManagerGrid(cachedTheaterCharacters);
      }
    }
  }
}

// Initialize as viewer until server state verifies role.
applyRoleUI(false);

fetch(`/api/theaters/${encodeURIComponent(activeTheaterId)}`)
  .then((res) => (res.ok ? (res.json() as Promise<TheaterDetailsResponse>) : null))
  .then((data) => {
    if (data && Array.isArray(data.references)) {
      window.cachedTheaterReferences = data.references;
    }
    if (data && data.metadata) {
      const isOwner = Boolean(data.metadata.is_owner);
      isTheaterStampOwner = isOwner;
      updateStampGenerationPermission();
      theaterModals?.applyOwnerMenuVisibility(isOwner, activeTheaterId);

      const isActiveOrator = Boolean(data.metadata.is_active_orator);
      const isContributor = Boolean(data.metadata.is_contributor);
      window._isContributor = Boolean(isOwner || isActiveOrator || isContributor);
      gameRenderer?.render();
      if (isStampManagerOpen) {
        assetManager?.updateStampTabsVisibility();
        updateStampManagerPermissionBanner();
        if (cachedUserStamps) renderStampManagerGrid(cachedUserStamps);
      }
      if (isActiveOrator) {
        applyRoleUI(true);
      } else if (!isOwner && !isContributor) {
        applyRoleUI(false);
        theaterModals?.maybeAutoOpenViewerCollabGuide(activeTheaterId, 400);
      }
      const isAdventure = Boolean(
        data.metadata.is_adventure_mode || data.metadata.config?.story_planning?.adventure_mode,
      );
      theaterModals?.initAdventureGuide(isAdventure, activeTheaterId);
    }
  })
  .catch((err: unknown) => {
    console.error('Error checking theater ownership:', err);
  });

// Baton Passing & Co-Orator Management
const batonManager = createBatonManager({
  theaterId: activeTheaterId,
  getCurrentUser: () => {
    if (!currentUser || currentUser.id === undefined || currentUser.id === null) {
      return null;
    }
    return {
      id: currentUser.id,
      username: currentUser.username ?? null,
    };
  },
  batonStatusBadge: getButtonElement('baton-status-badge'),
  menuTakeBackBatonBtn: document.getElementById('menu-take-back-baton-btn'),
  oratorsModal: document.getElementById('orators-modal'),
  oratorsCloseBtn: document.getElementById('orators-close-btn'),
  acceptBatonBtn: document.getElementById('accept-baton-btn'),
  declineBatonBtn: document.getElementById('decline-baton-btn'),
  onRoleChange: (isNowOrator) => applyRoleUI(isNowOrator),
  onBatonStateUpdated: ({ batonState, isOwner, isContributor }) => {
    currentBatonState = batonState;
    isTheaterStampOwner = Boolean(isOwner);
    updateStampGenerationPermission();
    window._isContributor = Boolean(isContributor);
    gameRenderer?.render();
    if (isStampManagerOpen) {
      updateStampManagerPermissionBanner();
      if (cachedUserStamps) renderStampManagerGrid(cachedUserStamps);
    }
  },
});

const checkBatonState = (): Promise<BatonState | null> => batonManager.checkBatonState();
const updateBatonStateUI = (
  batonState: BatonState,
  activeViewers?: readonly BatonUser[] | null,
): void => batonManager.updateBatonStateUI(batonState, activeViewers);

window.addContributor = (targetUserId: string | number): void => {
  void batonManager.addContributor(targetUserId).catch((error: unknown) => {
    console.error('Unable to update baton permissions:', error);
  });
};
window.removeContributor = (targetUserId: string | number): void => {
  void batonManager.removeContributor(targetUserId).catch((error: unknown) => {
    console.error('Unable to update baton permissions:', error);
  });
};
window.requestBatonPass = (targetUserId: string | number): void => {
  void batonManager.requestBatonPass(targetUserId).catch((error: unknown) => {
    console.error('Unable to update baton permissions:', error);
  });
};

// Fetch current user details
getCanvasAuthState()
  .then((data) => {
    if (data.authenticated && data.user) {
      currentUser = data.user;
      if (currentBatonState) {
        updateBatonStateUI(currentBatonState, batonManager.getActiveViewers());
      }
      void checkBatonState();
    }
  })
  .catch(() => {});
