import {
    initializeChatController,
    type ChatAuthUser,
    type ChatController,
} from '../shared/chat-controller.js';
import {
    initializeStoryLogInspector,
    type StoryLogInspectorController,
} from '../shared/story-log-inspector.js';
import {
    initializeRemoteAudio,
    type RemoteMusicState,
    type RemoteSpeechItem,
} from './remote-audio.js';
import { initializeRemoteGame } from './remote-game.js';
import type { GamePayload } from '../game/game-renderer.js';
import { buildPcmWorkletSource } from '../audio/pcm-worklet-processor.js';

const REMOTE_PCM_WORKLET_SOURCE = buildPcmWorkletSource(
    'remote-pcm-processor',
    16_000,
    480,
    'float32',
);

export interface RemoteAgentThought {
    readonly text?: string | null;
    readonly time?: number | null;
    readonly highlighted?: boolean;
}

export interface RemoteStatusState {
    readonly theater_id?: string | null;
    readonly join_key?: string | null;
    readonly agent_running?: boolean;
    readonly pinned?: boolean;
    readonly music_pinned?: boolean;
    readonly viewer_collab_enabled?: boolean;
    readonly preview_url?: string | null;
    readonly is_owner?: boolean;
    readonly is_orator?: boolean;
    readonly is_contributor?: boolean;
    readonly is_adventure_mode?: boolean;
    readonly agent_thought?: RemoteAgentThought | null;
    readonly music?: RemoteMusicState | null;
    readonly committed_scene_speech_generation?: number | null;
    readonly game_state?: GamePayload | null;
}

export interface RemoteSceneAsset {
    readonly id: string;
    readonly name: string;
    readonly url?: string | null;
    readonly description?: string | null;
}

export interface RemoteMusicTrack {
    readonly id: string;
    readonly name: string;
    readonly url?: string | null;
}

export interface RemotePlaylistGroup {
    readonly id: string;
    readonly name: string;
    readonly tracks?: readonly RemoteMusicTrack[] | null;
}

interface RemoteMusicCardAsset {
    readonly id: string;
    readonly name: string;
    readonly url?: string | null;
    readonly isGroup?: boolean;
    readonly isTrack?: boolean;
    readonly playlistName?: string;
    readonly tracks?: readonly RemoteMusicTrack[] | null;
}

export interface BatonUser {
    readonly id: number;
    readonly username?: string | null;
    readonly name?: string | null;
}

export interface BatonRequest {
    readonly target_user_id: number;
    readonly target_username?: string | null;
    readonly timeout_seconds?: number | null;
}

export interface BatonState {
    readonly owner?: BatonUser | null;
    readonly active_orator?: BatonUser | null;
    readonly contributors?: readonly BatonUser[] | null;
    readonly active_viewers?: readonly BatonUser[] | null;
    readonly baton_request?: BatonRequest | null;
    readonly detail?: string;
}

export interface RemoteController {
    destroy(): void;
}

interface AuthMeResponse {
    readonly authenticated?: boolean;
    readonly user?: ChatAuthUser | null;
}

interface ErrorDetailResponse {
    readonly detail?: unknown;
}

interface CanvasStateSocketPayload extends RemoteSpeechItem {
    readonly type?: string;
    readonly domains?: readonly string[];
}

interface DoodleSocketPayload {
    readonly type?: string;
    readonly baton_state?: BatonState;
    readonly active_viewers?: readonly BatonUser[];
}

interface AgentSocketPayload {
    readonly type?: string;
    readonly detail?: string;
}

interface SwipeStart {
    readonly x: number;
    readonly y: number;
}

interface RemoteAudioWindow extends Window {
    webkitAudioContext?: typeof AudioContext;
    AudioEncoder?: typeof globalThis.AudioEncoder;
    AudioData?: typeof globalThis.AudioData;
}

function getErrorMessage(error: unknown, fallback = 'Request failed.'): string {
    return error instanceof Error && error.message ? error.message : fallback;
}

function isNetworkOrTimeoutError(error: unknown): boolean {
    return error instanceof Error && (error.name === 'TypeError' || error.name === 'TimeoutError');
}

function isPermissionDeniedError(error: unknown): boolean {
    return error instanceof Error && (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError');
}

function requireElement<T extends HTMLElement>(element: HTMLElement | null, id: string): T {
    if (!element) {
        throw new Error(`Missing remote element: ${id}`);
    }
    return element as T;
}

export function initializeRemote(): RemoteController {
    const controls = requireElement<HTMLFieldSetElement>(document.getElementById('controls'), 'controls');
    const status = requireElement<HTMLElement>(document.getElementById('connection-status'), 'connection-status');
    const feedback = requireElement<HTMLElement>(document.getElementById('feedback'), 'feedback');
    const command = requireElement<HTMLInputElement>(document.getElementById('command'), 'command');
    const commandForm = requireElement<HTMLFormElement>(document.getElementById('command-form'), 'command-form');
    const micBtn = document.getElementById('mic-btn');
    const keyboardBtn = document.getElementById('keyboard-btn');
    const commandModal = document.getElementById('command-modal');
    const commandModalClose = document.getElementById('command-modal-close');
    const commandCancelBtn = document.getElementById('command-cancel-btn');
    const commandModalBackdrop = document.getElementById('command-modal-backdrop');
    const canvasLink = requireElement<HTMLAnchorElement>(document.getElementById('canvas-link'), 'canvas-link');
    const previewImage = requireElement<HTMLImageElement>(document.getElementById('preview-image'), 'preview-image');
    const previewStatus = requireElement<HTMLElement>(document.getElementById('preview-status'), 'preview-status');
    const previewPlaceholder = requireElement<HTMLElement>(document.getElementById('preview-placeholder'), 'preview-placeholder');
    const previewView = requireElement<HTMLElement>(document.getElementById('canvas-preview'), 'canvas-preview');
    const previewFrame = document.querySelector<HTMLElement>('.preview-frame');
    if (!previewFrame) {
        throw new Error('Missing remote element: .preview-frame');
    }
    const previewAudioFeedback = requireElement<HTMLElement>(
        document.getElementById('preview-audio-feedback'),
        'preview-audio-feedback',
    );
    const previewAudio = initializeRemoteAudio({
        frame: previewFrame,
        feedback: previewAudioFeedback,
        onError: () => showToast('Audio could not play. Tap the canvas to try again.', 'error'),
    });
    const tabs = [...document.querySelectorAll<HTMLElement>('[data-view]')];
    const views = tabs.map((tab) => document.getElementById(tab.getAttribute('aria-controls') ?? ''));
    const viewContainer = requireElement<HTMLElement>(document.getElementById('remote-views'), 'remote-views');
    const scenesCarousel = requireElement<HTMLElement>(document.getElementById('scenes-carousel'), 'scenes-carousel');
    const musicCarousel = requireElement<HTMLElement>(document.getElementById('music-carousel'), 'music-carousel');
    const assetStatus = requireElement<HTMLElement>(document.getElementById('asset-status'), 'asset-status');
    const retryAssets = requireElement<HTMLElement>(document.getElementById('retry-assets'), 'retry-assets');
    const toast = document.getElementById('toast');
    const toastMessage = document.getElementById('toast-message');
    const toastDismiss = document.getElementById('toast-dismiss');
    let toastTimer: number | undefined;
    let lastRefreshError: string | null = null;
    let assetSequence = 0;
    let assetsLoading = false;
    let lastAssetsSignature: string | null = null;
    const theaterId = new URLSearchParams(location.search).get('theater_id');
    const storyLog: StoryLogInspectorController | undefined = initializeStoryLogInspector(theaterId, { embedded: true });
    const previewTabs = [...document.querySelectorAll<HTMLElement>('.preview-tabs [role="tab"]')];
    const previewThoughtTab = document.getElementById('preview-thought-tab');
    const storyControls = requireElement<HTMLFieldSetElement>(
        document.getElementById('story-input-controls'),
        'story-input-controls',
    );
    const composer = requireElement<HTMLElement>(document.getElementById('remote-composer'), 'remote-composer');
    const agentThoughtCard = document.getElementById('agent-thought-card');
    const agentThoughtText = document.getElementById('agent-thought-text');
    let selectedPreviewIndex = 0;
    let lastAgentThought: string | null = null;

    function isAudioActive(): boolean {
        return (!previewView.hidden || previewAudio.isUnmuted()) && !document.hidden;
    }

    function updateAgentThought(thought: RemoteAgentThought | null | undefined): void {
        const text = thought && thought.text ? thought.text.trim() : '';
        const isHighlighted = Boolean(thought && thought.highlighted && text);
        if (agentThoughtCard) {
            agentThoughtCard.classList.toggle('is-highlighted', isHighlighted);
        }
        if (previewThoughtTab) {
            previewThoughtTab.classList.toggle('is-highlighted', isHighlighted);
        }
        if (text === lastAgentThought) return;
        const isWandering = text.toLowerCase() === 'wandering';
        if (agentThoughtText) {
            agentThoughtText.textContent = isWandering
                ? 'Narratron has wandered away. (Try resummoning?)'
                : (text || 'Waiting for Narratron to think…');
            agentThoughtText.classList.toggle('is-wandering', isWandering);
        }
        lastAgentThought = text;
    }

    function renderPreviewTab(): void {
        if (previewTabs[selectedPreviewIndex]?.hidden) {
            selectedPreviewIndex = 0;
        }
        previewTabs.forEach((tab, index) => {
            const selected = index === selectedPreviewIndex;
            tab.setAttribute('aria-selected', String(selected));
            tab.tabIndex = selected ? 0 : -1;
            const panel = document.getElementById(tab.getAttribute('aria-controls') ?? '');
            if (panel) panel.hidden = !selected;
        });
        const readingStory = !previewView.hidden && previewTabs[selectedPreviewIndex]?.id === 'preview-story-tab';
        storyLog?.setOpen(readingStory);
        const slot = document.getElementById(readingStory ? 'story-composer-slot' : 'inputs-composer-slot');
        if (slot && composer.parentElement !== slot) slot.appendChild(composer);
        composer.hidden = readingStory && !isOratorUser();
    }

    previewTabs.forEach((tab, index) => {
        function select(): void {
            if (tab.hidden) return;
            selectedPreviewIndex = index;
            renderPreviewTab();
            tab.focus();
        }
        tab.addEventListener('click', select);
        tab.addEventListener('keydown', (event: KeyboardEvent) => {
            const visibleTabs = previewTabs.filter((t) => !t.hidden);
            const currentPos = visibleTabs.indexOf(tab);
            let nextPos: number | null = null;
            if (event.key === 'Home') nextPos = 0;
            else if (event.key === 'End') nextPos = visibleTabs.length - 1;
            else if (event.key === 'ArrowRight') nextPos = (currentPos + 1) % visibleTabs.length;
            else if (event.key === 'ArrowLeft') nextPos = (currentPos - 1 + visibleTabs.length) % visibleTabs.length;
            if (nextPos === null) return;
            event.preventDefault();
            visibleTabs[nextPos].click();
        });
    });

    let theaterKey = (document.body && document.body.dataset ? document.body.dataset.joinKey : '') ||
                     new URLSearchParams(location.search).get('join_key') ||
                     new URLSearchParams(location.search).get('key') ||
                     '';

    function setTheaterKey(key: string): void {
        if (key) {
            theaterKey = key;
        }
        const keyEl = document.getElementById('theater-key');
        if (keyEl) {
            if (theaterKey) {
                keyEl.textContent = theaterKey;
                keyEl.title = `Key: ${theaterKey}`;
                keyEl.hidden = false;
            } else {
                keyEl.textContent = '';
                keyEl.hidden = true;
            }
        }
        const batonKeyEl = document.getElementById('baton-theater-key');
        if (batonKeyEl) {
            batonKeyEl.textContent = theaterKey || '';
        }
    }

    let state: RemoteStatusState | null = null;
    let busy = false;
    let generation = 0;
    let refreshSequence = 0;
    let pollTimer: number | undefined;
    let previewUrl: string | null = null;
    let previewLoaded = false;
    let currentUser: ChatAuthUser | null = null;
    let chatController: ChatController | null = null;
    let currentBatonState: BatonState | null = null;
    let batonTimerInterval: number | null = null;
    let doodleWs: WebSocket | null = null;
    let doodleReconnectTimer: number | undefined;
    let audioWs: WebSocket | null = null;
    let audioReconnectTimer: number | undefined;
    let audioRefreshTimer: number | undefined;

    const remoteGame = initializeRemoteGame({
        actionUrl: () => {
            const params = new URLSearchParams({
                theater_id: theaterId ?? '',
                ...(theaterKey ? { join_key: theaterKey } : {}),
            });
            return `/api/game/action?${params}`;
        },
        canEdit: () => Boolean(state && isOratorUser()),
        canContribute: () => Boolean(state?.viewer_collab_enabled && state?.is_contributor),
        refresh: () => void refresh(generation),
    });

    function connectAudioSocket(): void {
        if (!theaterId || !/^[a-zA-Z0-9_-]+$/.test(theaterId)) return;
        if (audioWs && (audioWs.readyState === WebSocket.OPEN || audioWs.readyState === WebSocket.CONNECTING)) return;
        window.clearTimeout(audioReconnectTimer);
        const params = new URLSearchParams({ theater_id: theaterId });
        if (theaterKey) params.set('join_key', theaterKey);
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const socket = new WebSocket(`${protocol}//${location.host}/ws/canvas-state?${params}`);
        audioWs = socket;
        socket.onmessage = (event: MessageEvent<string>) => {
            let data: CanvasStateSocketPayload;
            try {
                data = JSON.parse(event.data) as CanvasStateSocketPayload;
            } catch {
                return;
            }
            if (data.type === 'scene_speech_ready') previewAudio.enqueueSpeech(data);
            if (!document.hidden && (data.type === 'state_ready' ||
                (data.type === 'state_changed' && data.domains?.includes('latest')))) {
                window.clearTimeout(audioRefreshTimer);
                audioRefreshTimer = window.setTimeout(() => {
                    if (!busy) void refresh(generation);
                }, 50);
            }
        };
        socket.onclose = () => {
            if (audioWs !== socket) return;
            audioWs = null;
            if (!document.hidden) audioReconnectTimer = window.setTimeout(connectAudioSocket, 3000);
        };
        socket.onerror = () => socket.close();
    }

    function connectViewerSocket(): void {
        if (!theaterId || !/^[a-zA-Z0-9_-]+$/.test(theaterId)) return;
        if (doodleWs && (doodleWs.readyState === WebSocket.OPEN || doodleWs.readyState === WebSocket.CONNECTING)) {
            return;
        }
        window.clearTimeout(doodleReconnectTimer);
        const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const joinKey = theaterKey ||
                        new URLSearchParams(location.search).get('join_key') ||
                        new URLSearchParams(location.search).get('key');
        let wsUrl = `${wsProtocol}//${location.host}/ws/doodle?theater_id=${encodeURIComponent(theaterId)}`;
        if (joinKey) {
            wsUrl += `&join_key=${encodeURIComponent(joinKey)}`;
        }
        try {
            const socket = new WebSocket(wsUrl);
            doodleWs = socket;

            socket.onmessage = (event: MessageEvent<string>) => {
                try {
                    const data = JSON.parse(event.data) as DoodleSocketPayload | null;
                    if (data && data.type === 'baton_state' && data.baton_state) {
                        updateBatonStateUI(data.baton_state, data.active_viewers);
                    }
                } catch {
                    // Ignore malformed socket payloads.
                }
            };

            socket.onclose = () => {
                if (doodleWs === socket) {
                    doodleWs = null;
                    if (!document.hidden) {
                        doodleReconnectTimer = window.setTimeout(connectViewerSocket, 3000);
                    }
                }
            };

            socket.onerror = () => {
                try {
                    socket.close();
                } catch {
                    // Socket may already be closed.
                }
            };
        } catch {
            // Ignore WebSocket construction failure when offline.
        }
    }

    const batonHolderDisplay = document.getElementById('baton-holder-display');
    const menuTakeBackBatonBtn = document.getElementById('menu-take-back-baton-btn');
    const copyTheaterKeyBtn = document.getElementById('copy-theater-key-btn');
    const activeViewersList = document.getElementById('active-viewers-list');
    const contributorsList = document.getElementById('contributors-list');
    const batonRequestModal = document.getElementById('baton-request-modal');
    const batonTimerBar = document.getElementById('baton-timer-bar');
    const acceptBatonBtn = document.getElementById('accept-baton-btn');
    const declineBatonBtn = document.getElementById('decline-baton-btn');

    copyTheaterKeyBtn?.addEventListener('click', async () => {
        if (!theaterKey) {
            showToast('No theater key available to copy.', 'error');
            return;
        }
        let copied = false;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            try {
                await navigator.clipboard.writeText(theaterKey);
                copied = true;
            } catch {
                // Fall back to execCommand below.
            }
        }
        if (!copied) {
            try {
                const textarea = document.createElement('textarea');
                textarea.value = theaterKey;
                textarea.style.position = 'fixed';
                textarea.style.opacity = '0';
                document.body.appendChild(textarea);
                textarea.select();
                copied = document.execCommand('copy');
                document.body.removeChild(textarea);
            } catch {
                // Clipboard unavailable.
            }
        }
        if (copied) {
            const textEl = copyTheaterKeyBtn.querySelector('.copy-btn-text');
            const orig = textEl ? textEl.textContent : 'Copy Key';
            if (textEl) textEl.textContent = 'Copied!';
            copyTheaterKeyBtn.classList.add('copied');
            showToast('Theater key copied to clipboard!', 'info');
            window.setTimeout(() => {
                if (textEl) textEl.textContent = orig;
                copyTheaterKeyBtn.classList.remove('copied');
            }, 2000);
        } else {
            showToast('Failed to copy key to clipboard.', 'error');
        }
    });

    function isOwnerUser(): boolean {
        if (state && typeof state.is_owner === 'boolean') {
            return state.is_owner;
        }
        if (currentUser && currentBatonState?.owner) {
            return currentUser.id === currentBatonState.owner.id;
        }
        return document.body.dataset.isOwner === 'true' || document.body.dataset.ownerIdle === 'true';
    }

    function isOratorUser(): boolean {
        if (state && typeof state.is_orator === 'boolean') {
            return state.is_orator;
        }
        if (currentUser && currentBatonState) {
            if (currentBatonState.active_orator) {
                return currentUser.id === currentBatonState.active_orator.id;
            }
            if (currentBatonState.owner) {
                return currentUser.id === currentBatonState.owner.id;
            }
        }
        return document.body.dataset.pureViewer !== 'true' && document.body.dataset.ownerIdle !== 'true';
    }

    function renderTabsVisibility(): void {
        const isOwner = isOwnerUser();
        const isOrator = isOratorUser();

        if (!isOwner && !isOrator) {
            document.body.dataset.pureViewer = 'true';
            delete document.body.dataset.ownerIdle;
        } else if (isOwner && !isOrator) {
            document.body.dataset.ownerIdle = 'true';
            delete document.body.dataset.pureViewer;
        } else {
            delete document.body.dataset.pureViewer;
            delete document.body.dataset.ownerIdle;
        }

        const assetsTab = document.getElementById('assets-tab');
        const inputsTab = document.getElementById('inputs-tab');
        const partyTab = document.getElementById('party-tab') || document.getElementById('baton-tab') || document.getElementById('collab-tab');
        const previewTab = document.getElementById('preview-tab');

        if (assetsTab) assetsTab.hidden = !isOrator;
        if (inputsTab) inputsTab.hidden = !isOrator;
        if (partyTab) partyTab.hidden = !isOwner;
        if (previewTab) previewTab.hidden = false;
    }

    function activeIndex(): number {
        const previewIdx = tabs.findIndex((tab) => tab.dataset.view === 'preview');
        const isOwner = isOwnerUser();
        const isOrator = isOratorUser();

        if (!isOwner && !isOrator) {
            return previewIdx >= 0 ? previewIdx : 2;
        }

        const hash = location.hash;
        const index = tabs.findIndex((tab) => hash === `#${tab.dataset.view}`);
        if (index >= 0) {
            const targetView = tabs[index].dataset.view;
            if ((targetView === 'assets' || targetView === 'inputs') && !isOrator) {
                // Cannot view assets/inputs if not orator
            } else if ((targetView === 'party' || targetView === 'baton' || targetView === 'collab') && !isOwner) {
                // Cannot view party/baton if not owner
            } else {
                return index;
            }
        }
        if (hash === '#baton' && isOwner) {
            const partyIdx = tabs.findIndex((tab) => tab.dataset.view === 'party');
            if (partyIdx >= 0) return partyIdx;
        }
        if (hash === '#controls' && isOrator) {
            const inputsIdx = tabs.findIndex((tab) => tab.dataset.view === 'inputs' || tab.dataset.view === 'controls');
            if (inputsIdx >= 0) return inputsIdx;
        }

        if (isOrator) {
            const inputsIdx = tabs.findIndex((tab) => tab.dataset.view === 'inputs');
            return inputsIdx >= 0 ? inputsIdx : 1;
        }
        if (isOwner) {
            const partyIdx = tabs.findIndex((tab) => tab.dataset.view === 'party' || tab.dataset.view === 'baton' || tab.dataset.view === 'collab');
            return partyIdx >= 0 ? partyIdx : (previewIdx >= 0 ? previewIdx : 2);
        }
        return previewIdx >= 0 ? previewIdx : 2;
    }

    function renderView(focus = false): void {
        renderTabsVisibility();
        const isOwner = isOwnerUser();
        const isOrator = isOratorUser();
        const selected = activeIndex();
        tabs.forEach((tab, index) => {
            const isSelected = index === selected;
            tab.setAttribute('aria-selected', String(isSelected));
            tab.tabIndex = isSelected ? 0 : -1;
            const view = views[index];
            if (view) {
                view.hidden = index !== selected;
            }
        });
        document.title = `${tabs[selected].textContent} · Narratron Mobile`;
        renderPreviewTab();
        previewAudio.update(state, isAudioActive());
        if (focus && !tabs[selected].hidden) tabs[selected].focus();
        if (tabs[selected].dataset.view === 'preview') {
            updatePreview();
            void chatController?.fetchChat();
        }
        if (tabs[selected].dataset.view === 'assets' && isOrator) void loadAssets();
        void isOwner;
    }

    function selectView(index: number): void {
        if (index < 0 || index >= tabs.length) return;
        const targetView = tabs[index].dataset.view;
        const isOwner = isOwnerUser();
        const isOrator = isOratorUser();
        if ((targetView === 'assets' || targetView === 'inputs') && !isOrator) return;
        if ((targetView === 'party' || targetView === 'baton' || targetView === 'collab') && !isOwner) return;
        if (targetView === 'party' || targetView === 'baton' || targetView === 'collab') void checkBatonState();
        if (index === activeIndex()) return;
        location.hash = tabs[index].dataset.view ?? '';
    }

    tabs.forEach((tab, index) => {
        tab.addEventListener('click', () => selectView(index));
        tab.addEventListener('keydown', (event: KeyboardEvent) => {
            const isOwner = isOwnerUser();
            const isOrator = isOratorUser();
            if (!isOwner && !isOrator) return;
            const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length
                : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length
                : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
            if (next === null) return;
            event.preventDefault();
            selectView(next);
        });
    });

    const handleHashChange = (): void => renderView(true);
    window.addEventListener('hashchange', handleHashChange);

    let swipeStart: SwipeStart | null = null;
    viewContainer.addEventListener('touchstart', (event: TouchEvent) => {
        const nested = event.target instanceof Element
            ? event.target.closest('.asset-carousel, .asset-card, input, textarea, button, #chat-messages, #remote-story-log, #remote-game-viewport')
            : null;
        swipeStart = event.touches.length === 1 && !nested
            ? { x: event.touches[0].clientX, y: event.touches[0].clientY }
            : null;
    }, { passive: true });
    viewContainer.addEventListener('touchmove', (event: TouchEvent) => {
        if (event.touches.length !== 1) swipeStart = null;
    }, { passive: true });
    viewContainer.addEventListener('touchcancel', () => { swipeStart = null; }, { passive: true });
    viewContainer.addEventListener('touchend', (event: TouchEvent) => {
        const start = swipeStart;
        swipeStart = null;
        if (!start || event.touches.length || !event.changedTouches.length) return;
        const isOwner = isOwnerUser();
        const isOrator = isOratorUser();
        if (!isOwner && !isOrator) return;
        const touch = event.changedTouches[0];
        const dx = touch.clientX - start.x;
        const dy = touch.clientY - start.y;
        if (Math.abs(dx) >= 60 && Math.abs(dx) > Math.abs(dy) * 1.5) {
            selectView(activeIndex() + (dx < 0 ? 1 : -1));
        }
    }, { passive: true });

    function createAssetCard(item: RemoteSceneAsset | RemoteMusicCardAsset, type: 'scene' | 'music'): HTMLElement {
        const card = document.createElement('article');
        card.className = 'asset-card';
        card.setAttribute('role', 'button');
        card.setAttribute('tabindex', '0');
        card.setAttribute('aria-label', item.name);
        card.title = `Double tap to send "${item.name}" to canvas`;
        if (type === 'scene') {
            card.style.setProperty('--card-accent', '#cd9aff');
            if (item.url) {
                const image = document.createElement('img');
                image.src = item.url;
                image.alt = item.name;
                image.loading = 'lazy';
                image.referrerPolicy = 'no-referrer';
                card.append(image);
            } else {
                const placeholder = document.createElement('div');
                placeholder.className = 'music-placeholder';
                placeholder.style.color = '#cd9aff';
                placeholder.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>';
                card.append(placeholder);
            }
        } else {
            card.style.setProperty('--card-accent', '#45e0ff');
            const placeholder = document.createElement('div');
            placeholder.className = 'music-placeholder';
            placeholder.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>';
            card.append(placeholder);
        }

        const title = document.createElement('h3');
        title.textContent = item.name;
        card.append(title);

        function trigger(): void {
            if (!state || busy) return;
            const path = `${basePath()}/${type === 'scene' ? 'scenes' : 'playlists'}/push`;
            const payload: Readonly<Record<string, string | boolean>> = type === 'scene'
                ? { scene_id: item.id }
                : { id: item.id };
            void send(card, path, payload);
        }

        let pointerStartX = 0;
        let pointerStartY = 0;
        let moved = false;
        let lastTap = 0;
        let lastTriggerTime = 0;

        card.addEventListener('pointerdown', (event: PointerEvent) => {
            pointerStartX = event.clientX;
            pointerStartY = event.clientY;
            moved = false;
        });

        card.addEventListener('pointermove', (event: PointerEvent) => {
            if (Math.hypot(event.clientX - pointerStartX, event.clientY - pointerStartY) > 10) {
                moved = true;
            }
        });

        card.addEventListener('pointerup', (event: PointerEvent) => {
            if (moved) return;
            if (event.button !== 0 && event.pointerType === 'mouse') return;
            const now = Date.now();
            if (now - lastTap < 400) {
                lastTap = 0;
                if (now - lastTriggerTime >= 500) {
                    lastTriggerTime = now;
                    trigger();
                }
            } else {
                lastTap = now;
            }
        });

        card.addEventListener('dblclick', (event: MouseEvent) => {
            event.preventDefault();
            const now = Date.now();
            if (now - lastTriggerTime >= 500) {
                lastTriggerTime = now;
                trigger();
            }
        });

        card.addEventListener('keydown', (event: KeyboardEvent) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                const now = Date.now();
                if (now - lastTap < 500) {
                    lastTap = 0;
                    if (now - lastTriggerTime >= 500) {
                        lastTriggerTime = now;
                        trigger();
                    }
                } else {
                    lastTap = now;
                }
            }
        });

        return card;
    }

    async function loadAssets(force = false): Promise<void> {
        if (assetsLoading && !force) return;
        const sequence = ++assetSequence;
        const hasRenderedAssets = scenesCarousel.children.length > 0 || musicCarousel.children.length > 0;
        if (!hasRenderedAssets || force) {
            scenesCarousel.replaceChildren();
            musicCarousel.replaceChildren();
            retryAssets.hidden = true;
            assetStatus.textContent = state ? 'Loading assets…' : 'Reconnect to browse assets.';
        }
        if (!state) {
            assetsLoading = false;
            return;
        }
        assetsLoading = true;
        try {
            const [scenes, playlists] = await Promise.all([
                request<RemoteSceneAsset[]>(`${basePath()}/scenes`),
                request<RemotePlaylistGroup[]>(`${basePath()}/playlists`),
            ]);
            if (sequence !== assetSequence || !state) return;

            const signature = JSON.stringify({ scenes, playlists });
            if (!force && hasRenderedAssets && signature === lastAssetsSignature) {
                return;
            }
            lastAssetsSignature = signature;

            const musicEntries: RemoteMusicCardAsset[] = (playlists || []).flatMap((group) => [
                { ...group, isGroup: true },
                ...(group.tracks || []).map((track) => ({ ...track, playlistName: group.name, isTrack: true })),
            ]);

            if (!scenes.length && !musicEntries.length) {
                assetStatus.textContent = 'No scenes available in this theater yet.';
            } else if (!scenes.length) {
                assetStatus.textContent = 'No scenes available in this theater yet.';
            } else {
                assetStatus.textContent = '';
            }

            const sceneNodes: HTMLElement[] = [];
            if (scenes.length) {
                for (const scene of scenes) {
                    sceneNodes.push(createAssetCard(scene, 'scene'));
                }
            } else {
                const empty = document.createElement('div');
                empty.className = 'asset-empty';
                empty.textContent = 'No scenes available.';
                sceneNodes.push(empty);
            }

            const musicNodes: HTMLElement[] = [];
            if (musicEntries.length) {
                for (const music of musicEntries) {
                    musicNodes.push(createAssetCard(music, 'music'));
                }
            } else {
                const empty = document.createElement('div');
                empty.className = 'asset-empty';
                empty.textContent = 'No music available.';
                musicNodes.push(empty);
            }

            scenesCarousel.replaceChildren(...sceneNodes);
            musicCarousel.replaceChildren(...musicNodes);
        } catch (error: unknown) {
            if (sequence !== assetSequence) return;
            const detail = getErrorMessage(error);
            if (!hasRenderedAssets || force) {
                assetStatus.textContent = detail;
                retryAssets.hidden = false;
            }
            showToast(detail, 'error');
        } finally {
            if (sequence === assetSequence) assetsLoading = false;
        }
    }

    retryAssets.addEventListener('click', () => { void loadAssets(true); });

    function renderPreview(): void {
        previewImage.hidden = !state || !previewLoaded;
        previewPlaceholder.hidden = Boolean(state && previewLoaded);
        previewStatus.textContent = !state ? 'Unavailable' : !previewUrl ? 'No scene' : previewLoaded ? 'Current scene' : 'Loading…';
        previewPlaceholder.textContent = !state ? 'Reconnect to see the canvas.' : !previewUrl ? 'No scene on the canvas yet.' : 'Loading scene…';
    }

    function updatePreview(): void {
        previewAudio.update(state, isAudioActive());
        updateAgentThought(state?.agent_thought);
        if (previewView.hidden && state) return;
        const nextUrl = state?.preview_url || null;
        if (nextUrl !== previewUrl) {
            previewUrl = nextUrl;
            previewLoaded = false;
            previewImage.removeAttribute('src');
            if (previewUrl) previewImage.src = previewUrl;
        }
        renderPreview();
    }

    previewImage.addEventListener('load', () => {
        previewLoaded = true;
        renderPreview();
    });
    previewImage.addEventListener('error', () => {
        previewLoaded = false;
        renderPreview();
        previewStatus.textContent = 'Unavailable';
        previewPlaceholder.textContent = 'Scene could not load. Retrying…';
        // The next status poll retries without blocking the remote controls.
        previewUrl = null;
    });

    function dismissToast(): void {
        if (!toast || toast.hidden) return;
        window.clearTimeout(toastTimer);
        toast.classList.remove('show');
        const onEnd = (): void => {
            toast.removeEventListener('transitionend', onEnd);
            if (!toast.classList.contains('show')) {
                toast.hidden = true;
            }
        };
        toast.addEventListener('transitionend', onEnd);
        window.setTimeout(() => {
            if (!toast.classList.contains('show')) {
                toast.hidden = true;
            }
        }, 300);
    }

    function showToast(text: string, type: 'error' | 'info' = 'error'): void {
        if (!toast || !toastMessage) return;
        window.clearTimeout(toastTimer);
        toastMessage.textContent = text;
        toast.dataset.type = type;
        toast.hidden = false;
        void toast.offsetWidth;
        toast.classList.add('show');
        toastTimer = window.setTimeout(() => {
            dismissToast();
        }, 5000);
    }

    if (toastDismiss) {
        toastDismiss.addEventListener('click', () => {
            dismissToast();
        });
    }

    const handleDocumentKeyDown = (event: KeyboardEvent): void => {
        if (event.key === 'Escape') {
            if (toast && !toast.hidden && toast.classList.contains('show')) {
                dismissToast();
                return;
            }
            if (commandModal && !commandModal.hidden) {
                closeCommandModal();
            }
        }
    };
    document.addEventListener('keydown', handleDocumentKeyDown);

    function message(text: string, error = false): void {
        feedback.textContent = text;
        feedback.dataset.error = String(error);
    }

    function render(): void {
        const adventureMode = Boolean(state?.is_adventure_mode);
        const previewStoryTab = document.getElementById('preview-story-tab');
        if (previewStoryTab) previewStoryTab.hidden = !adventureMode;
        const previewGameTab = document.getElementById('preview-game-tab');
        if (previewGameTab) previewGameTab.hidden = !state?.game_state;
        remoteGame.render(state?.game_state || null);
        const storyDirection = document.getElementById('story-direction');
        if (storyDirection) storyDirection.hidden = !adventureMode;
        if (!adventureMode && previewTabs[selectedPreviewIndex]?.id === 'preview-story-tab') {
            selectedPreviewIndex = 0;
        }
        renderPreviewTab();
        const isOwner = Boolean(currentUser && currentBatonState?.owner && currentUser.id === currentBatonState.owner.id);
        const isActiveOrator = Boolean(currentUser && currentBatonState?.active_orator && currentUser.id === currentBatonState.active_orator.id);
        const isNowOrator = !currentBatonState || Boolean(isActiveOrator || (isOwner && !currentBatonState.active_orator));

        controls.disabled = !state || busy || !isNowOrator;
        storyControls.disabled = !state || busy || !isNowOrator;
        const storyInputStatus = document.getElementById('story-input-status');
        if (storyInputStatus) {
            storyInputStatus.textContent = !isOratorUser()
                ? 'The baton holder controls story input.' : !state ? 'Reconnecting…'
                    : !state.agent_running ? 'Turn the agent on in Inputs to continue the story.' : '';
        }
        if (!state) {
            assetSequence += 1;
            assetsLoading = false;
            lastAssetsSignature = null;
            scenesCarousel.replaceChildren();
            musicCarousel.replaceChildren();
            assetStatus.textContent = 'Reconnect to browse assets.';
        } else if (!views[0]?.hidden && !assetsLoading && !scenesCarousel.children.length && !musicCarousel.children.length && retryAssets.hidden
            && assetStatus.textContent === 'Reconnect to browse assets.') {
            void loadAssets();
        }
        document.querySelectorAll<HTMLButtonElement>('[data-live]').forEach((button) => {
            button.disabled = !state?.agent_running || busy || !isNowOrator;
        });
        command.disabled = !state?.agent_running || busy || !isNowOrator;
        if (!state?.agent_running || !isNowOrator) {
            if (micTransmitting) void stopMicrophone('agent_stopped');
            if (commandModal && !commandModal.hidden) closeCommandModal();
        }
        const pinSpecs: ReadonlyArray<readonly [string, boolean | undefined, string]> = [
            ['scene-pin', state?.pinned, 'scene'],
            ['music-pin', state?.music_pinned, 'music'],
        ];
        for (const [id, pinned, label] of pinSpecs) {
            const button = document.getElementById(id);
            if (!button) continue;
            button.setAttribute('aria-pressed', String(Boolean(pinned)));
            const pinLabel = `${pinned ? 'Unpin' : 'Pin'} ${label}`;
            button.setAttribute('aria-label', pinLabel);
            button.title = pinLabel;
        }
        const collaboration = document.getElementById('collaboration');
        if (collaboration) {
            collaboration.setAttribute('aria-pressed', String(Boolean(state?.viewer_collab_enabled)));
            const collaborationLabel = `Turn collaboration ${state?.viewer_collab_enabled ? 'off' : 'on'}`;
            collaboration.setAttribute('aria-label', collaborationLabel);
            collaboration.title = collaborationLabel;
        }
        const collaborationStatus = document.getElementById('collaboration-status');
        if (collaborationStatus) {
            collaborationStatus.textContent = `Collaboration ${state?.viewer_collab_enabled ? 'on' : 'off'}`;
        }
        const agentToggle = document.getElementById('agent-toggle');
        if (agentToggle) {
            agentToggle.setAttribute('aria-pressed', String(Boolean(state?.agent_running)));
            const agentLabel = state?.agent_running ? 'Turn agent off' : 'Turn agent on';
            agentToggle.setAttribute('aria-label', agentLabel);
            agentToggle.title = agentLabel;
        }
        renderTabsVisibility();
        renderView();
    }

    async function request<TResponse>(
        path: string,
        body?: Readonly<Record<string, string | boolean>>,
    ): Promise<TResponse> {
        const response = await fetch(path, {
            method: body === undefined ? 'GET' : 'POST',
            credentials: 'same-origin',
            cache: 'no-store',
            headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
            body: body === undefined ? undefined : JSON.stringify(body),
            signal: AbortSignal.timeout(10000),
        });
        if (!response.ok) {
            const data = (await response.json().catch(() => ({}))) as ErrorDetailResponse;
            throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status}).`);
        }
        return (await response.json()) as TResponse;
    }

    function basePath(): string {
        return `/api/theaters/${encodeURIComponent(theaterId ?? '')}`;
    }

    async function refresh(version: number): Promise<void> {
        const sequence = ++refreshSequence;
        window.clearTimeout(pollTimer);
        try {
            const next = await request<RemoteStatusState>(`${basePath()}/remote`);
            if (version !== generation || sequence !== refreshSequence) return;
            state = next;
            lastRefreshError = null;
            if (next && next.join_key) {
                setTheaterKey(next.join_key);
            }
            updatePreview();
            if (!previewView.hidden) void chatController?.fetchChat();
            status.dataset.connected = 'true';
            status.textContent = next.agent_running ? 'Connected · Agent on' : 'Connected · Agent off';
            await checkBatonState();
        } catch (error: unknown) {
            if (version !== generation || sequence !== refreshSequence) return;
            state = null;
            updatePreview();
            status.dataset.connected = 'false';
            status.textContent = 'Disconnected';
            const detail = isNetworkOrTimeoutError(error)
                ? 'Connection lost. Retrying…'
                : getErrorMessage(error);
            if (lastRefreshError !== detail) {
                lastRefreshError = detail;
                showToast(detail, 'error');
            }
        } finally {
            if (version === generation && sequence === refreshSequence) {
                render();
                if (!document.hidden) pollTimer = window.setTimeout(() => void refresh(version), 2000);
            }
        }
    }

    async function send(
        button: HTMLElement,
        path: string,
        payload: Readonly<Record<string, string | boolean>>,
        onSuccess: () => void = () => {},
    ): Promise<void> {
        if (!state || busy) return;
        const version = generation;
        busy = true;
        refreshSequence += 1;
        window.clearTimeout(pollTimer);
        viewContainer.querySelectorAll<HTMLElement>('[data-error]').forEach((control) => {
            delete control.dataset.error;
            control.removeAttribute('aria-describedby');
        });
        button.setAttribute('aria-busy', 'true');
        render();
        message('Sending…');
        try {
            await request<unknown>(path, payload);
            if (version !== generation) return;
            onSuccess();
            message('Accepted.');
            if (button.closest('.asset-card')) assetStatus.textContent = 'Sent to canvas.';
        } catch (error: unknown) {
            if (version !== generation) return;
            const detail = isNetworkOrTimeoutError(error)
                ? 'Delivery could not be confirmed. Check the canvas before trying again.'
                : getErrorMessage(error);
            button.dataset.error = detail;
            const parent = button.closest<HTMLElement>('.bar, .asset-card, .command-modal-dialog');
            if (parent) parent.dataset.error = detail;
            button.setAttribute('aria-describedby', 'feedback');
            message('', true);
            if (button.closest('.asset-card')) assetStatus.textContent = detail;
            showToast(detail, 'error');
        } finally {
            if (version === generation) {
                await refresh(version);
                busy = false;
                button.removeAttribute('aria-busy');
                render();
            }
        }
    }

    const agentToggleBtn = document.getElementById('agent-toggle');
    agentToggleBtn?.addEventListener('click', () => {
        const action = state?.agent_running ? 'stop' : 'start';
        void send(agentToggleBtn, `${basePath()}/agent/${action}`, {});
    });
    controls.querySelectorAll<HTMLElement>('[data-action]').forEach((button) => {
        button.addEventListener('click', () => {
            const action = button.dataset.action ?? '';
            void send(button, `${basePath()}/orator-action`, { action });
        });
    });
    const collaborationBtn = document.getElementById('collaboration');
    collaborationBtn?.addEventListener('click', () => {
        void send(collaborationBtn, `${basePath()}/collab`, { enabled: !state?.viewer_collab_enabled });
    });

    async function fetchCurrentUser(): Promise<void> {
        try {
            const res = await fetch('/api/auth/me', { credentials: 'same-origin' });
            if (res.ok) {
                const data = (await res.json()) as AuthMeResponse | null;
                if (data && data.authenticated && data.user) {
                    currentUser = data.user;
                    if (currentBatonState) {
                        updateBatonStateUI(currentBatonState, currentBatonState.active_viewers || []);
                    }
                }
            }
        } catch {
            // Leave currentUser null when unauthenticated or offline.
        }
    }

    let isCheckingBaton = false;
    async function checkBatonState(): Promise<void> {
        if (!theaterId || isCheckingBaton) return;
        isCheckingBaton = true;
        try {
            const res = await fetch(`${basePath()}/baton`, {
                credentials: 'same-origin',
                cache: 'no-store',
            });
            if (res.ok) {
                const data = (await res.json()) as BatonState | null;
                if (data && !data.detail) {
                    updateBatonStateUI(data, data.active_viewers || []);
                }
            }
        } catch (err: unknown) {
            console.error('Error fetching baton state:', err);
        } finally {
            isCheckingBaton = false;
        }
    }

    function updateBatonStateUI(batonState: BatonState, activeViewers?: readonly BatonUser[]): void {
        currentBatonState = batonState;
        const viewers = activeViewers || batonState.active_viewers || [];
        const isOwner = Boolean(currentUser && batonState.owner && currentUser.id === batonState.owner.id);
        const isActiveOrator = Boolean(currentUser && batonState.active_orator && currentUser.id === batonState.active_orator.id);

        if (batonHolderDisplay) {
            const holder = batonState.active_orator || batonState.owner;
            const holderName = (holder && (holder.username || holder.name)) || 'Unknown';
            batonHolderDisplay.textContent = `👑 Orator: @${holderName}`;
        }

        if (menuTakeBackBatonBtn) {
            menuTakeBackBatonBtn.style.display = (isOwner && !isActiveOrator) ? 'inline-flex' : 'none';
        }

        if (activeViewersList) {
            const allowedIds = (batonState.contributors || []).map((u) => u.id);
            const otherViewers = viewers.filter((u) => u.id !== (batonState.owner ? batonState.owner.id : null));
            if (otherViewers.length === 0) {
                activeViewersList.innerHTML = '<div class="collab-empty">No other connected viewers online.</div>';
            } else {
                activeViewersList.replaceChildren();
                otherViewers.forEach((v) => {
                    const row = document.createElement('div');
                    row.className = 'collab-user-row';
                    const name = document.createElement('span');
                    name.className = 'collab-user-name';
                    name.textContent = `👤 @${v.username || v.id}`;
                    row.append(name);

                    const actions = document.createElement('div');
                    actions.className = 'collab-user-actions';
                    const isAllowed = allowedIds.includes(v.id);
                    if (isAllowed) {
                        const badge = document.createElement('span');
                        badge.className = 'collab-badge allowed';
                        badge.textContent = 'Contributor';
                        actions.append(badge);
                    } else if (isOwner) {
                        const addBtn = document.createElement('button');
                        addBtn.type = 'button';
                        addBtn.className = 'collab-action-btn add-btn';
                        addBtn.textContent = '+ Contributor';
                        addBtn.addEventListener('click', () => { void addContributor(v.id); });
                        actions.append(addBtn);
                    } else {
                        const badge = document.createElement('span');
                        badge.className = 'collab-badge viewer';
                        badge.textContent = 'Viewer';
                        actions.append(badge);
                    }
                    row.append(actions);
                    activeViewersList.append(row);
                });
            }
        }

        if (contributorsList) {
            const contribs = batonState.contributors || [];
            if (contribs.length === 0) {
                contributorsList.innerHTML = '<div class="collab-empty">No contributors added yet.</div>';
            } else {
                contributorsList.replaceChildren();
                contribs.forEach((u) => {
                    const row = document.createElement('div');
                    row.className = 'collab-user-row';
                    const name = document.createElement('span');
                    name.className = 'collab-user-name';
                    name.textContent = `🎙️ @${u.username || u.id}`;
                    row.append(name);

                    const actions = document.createElement('div');
                    actions.className = 'collab-user-actions';
                    const isTargeting = Boolean(batonState.baton_request && batonState.baton_request.target_user_id === u.id);
                    const isCurrentOrator = Boolean(batonState.active_orator && batonState.active_orator.id === u.id);

                    if (isCurrentOrator) {
                        const badge = document.createElement('span');
                        badge.className = 'collab-badge orator';
                        badge.textContent = 'Active Orator 👑';
                        actions.append(badge);
                    } else if (isTargeting) {
                        const badge = document.createElement('span');
                        badge.className = 'collab-badge pending';
                        badge.textContent = 'Pending 30s...';
                        actions.append(badge);
                    } else if (isOwner) {
                        const passBtn = document.createElement('button');
                        passBtn.type = 'button';
                        passBtn.className = 'collab-action-btn pass-btn';
                        passBtn.textContent = 'Pass Baton 🎙️';
                        passBtn.addEventListener('click', () => { void requestBatonPass(u.id); });
                        actions.append(passBtn);
                    } else {
                        const badge = document.createElement('span');
                        badge.className = 'collab-badge allowed';
                        badge.textContent = 'Contributor';
                        actions.append(badge);
                    }

                    if (isOwner && !isCurrentOrator) {
                        const removeBtn = document.createElement('button');
                        removeBtn.type = 'button';
                        removeBtn.className = 'collab-action-btn remove-btn';
                        removeBtn.textContent = 'Remove';
                        removeBtn.addEventListener('click', () => { void removeContributor(u.id); });
                        actions.append(removeBtn);
                    }

                    row.append(actions);
                    contributorsList.append(row);
                });
            }
        }

        if (batonState.baton_request && currentUser && batonState.baton_request.target_user_id === currentUser.id) {
            showBatonRequestModal(batonState.baton_request);
        } else {
            hideBatonRequestModal();
        }

        render();
    }

    function showBatonRequestModal(batonReq: BatonRequest): void {
        if (!batonRequestModal) return;
        if (batonRequestModal.style.display !== 'none' && batonRequestModal.classList.contains('active')) return;

        batonRequestModal.style.display = 'flex';
        batonRequestModal.classList.add('active');
        batonRequestModal.setAttribute('aria-hidden', 'false');

        const totalMs = (batonReq.timeout_seconds || 30) * 1000;
        const startTime = Date.now();

        if (batonTimerInterval !== null) window.clearInterval(batonTimerInterval);

        batonTimerInterval = window.setInterval(() => {
            const elapsed = Date.now() - startTime;
            const remaining = Math.max(0, totalMs - elapsed);
            const pct = (remaining / totalMs) * 100;
            if (batonTimerBar) batonTimerBar.style.width = `${pct}%`;

            if (remaining <= 0) {
                if (batonTimerInterval !== null) {
                    window.clearInterval(batonTimerInterval);
                    batonTimerInterval = null;
                }
                void declineBatonPass();
            }
        }, 100);
    }

    function hideBatonRequestModal(): void {
        if (batonRequestModal) {
            batonRequestModal.classList.remove('active');
            batonRequestModal.setAttribute('aria-hidden', 'true');
            batonRequestModal.style.display = 'none';
        }
        if (batonTimerInterval !== null) {
            window.clearInterval(batonTimerInterval);
            batonTimerInterval = null;
        }
    }

    async function acceptBatonPass(): Promise<void> {
        if (!theaterId) return;
        try {
            const res = await fetch(`${basePath()}/baton/accept`, {
                method: 'POST',
                credentials: 'same-origin',
            });
            const data = (await res.json()) as BatonState | null;
            hideBatonRequestModal();
            if (data && !data.detail) {
                updateBatonStateUI(data, data.active_viewers ?? undefined);
                showToast('You accepted the baton! You are now the active orator.', 'info');
                await refresh(generation);
            } else if (data && data.detail) {
                showToast(data.detail, 'error');
            }
        } catch (err: unknown) {
            hideBatonRequestModal();
            showToast(getErrorMessage(err, 'Error accepting baton'), 'error');
        }
    }

    async function declineBatonPass(): Promise<void> {
        if (!theaterId) return;
        try {
            const res = await fetch(`${basePath()}/baton/decline`, {
                method: 'POST',
                credentials: 'same-origin',
            });
            const data = (await res.json()) as BatonState | null;
            hideBatonRequestModal();
            if (data && !data.detail) {
                updateBatonStateUI(data, data.active_viewers ?? undefined);
            }
        } catch (err: unknown) {
            hideBatonRequestModal();
            showToast(getErrorMessage(err, 'Error declining baton'), 'error');
        }
    }

    async function takeBackBaton(): Promise<void> {
        if (!theaterId) return;
        try {
            const res = await fetch(`${basePath()}/baton/takeback`, {
                method: 'POST',
                credentials: 'same-origin',
            });
            const data = (await res.json()) as BatonState | null;
            if (data && !data.detail) {
                updateBatonStateUI(data, data.active_viewers ?? undefined);
                showToast('Baton taken back.', 'info');
                await refresh(generation);
            } else if (data && data.detail) {
                showToast(data.detail, 'error');
            }
        } catch (err: unknown) {
            showToast(getErrorMessage(err, 'Error taking back baton'), 'error');
        }
    }

    async function addContributor(targetUserId: number): Promise<void> {
        if (!theaterId) return;
        try {
            const res = await fetch(`${basePath()}/baton/contributors`, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target_user_id: targetUserId }),
            });
            const data = (await res.json()) as BatonState | null;
            if (data && !data.detail) {
                updateBatonStateUI(data, data.active_viewers ?? undefined);
            } else if (data && data.detail) {
                showToast(data.detail, 'error');
            }
        } catch (err: unknown) {
            showToast(getErrorMessage(err), 'error');
        }
    }

    async function removeContributor(targetUserId: number): Promise<void> {
        if (!theaterId) return;
        try {
            const res = await fetch(`${basePath()}/baton/contributors/${targetUserId}`, {
                method: 'DELETE',
                credentials: 'same-origin',
            });
            const data = (await res.json()) as BatonState | null;
            if (data && !data.detail) {
                updateBatonStateUI(data, data.active_viewers ?? undefined);
            } else if (data && data.detail) {
                showToast(data.detail, 'error');
            }
        } catch (err: unknown) {
            showToast(getErrorMessage(err), 'error');
        }
    }

    async function requestBatonPass(targetUserId: number): Promise<void> {
        if (!theaterId) return;
        try {
            const res = await fetch(`${basePath()}/baton/request`, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target_user_id: targetUserId, timeout_seconds: 30 }),
            });
            const data = (await res.json()) as BatonState | null;
            if (data && !data.detail) {
                updateBatonStateUI(data, data.active_viewers ?? undefined);
                showToast('Baton pass requested (30s)...', 'info');
            } else if (data && data.detail) {
                showToast(data.detail, 'error');
            }
        } catch (err: unknown) {
            showToast(getErrorMessage(err), 'error');
        }
    }

    if (menuTakeBackBatonBtn) {
        menuTakeBackBatonBtn.addEventListener('click', () => { void takeBackBaton(); });
    }
    if (acceptBatonBtn) {
        acceptBatonBtn.addEventListener('click', () => { void acceptBatonPass(); });
    }
    if (declineBatonBtn) {
        declineBatonBtn.addEventListener('click', () => { void declineBatonPass(); });
    }

    function openCommandModal(): void {
        if (!state?.agent_running) {
            showToast('Turn the agent on first.', 'error');
            return;
        }
        if (!commandModal) return;
        commandModal.hidden = false;
        keyboardBtn?.setAttribute('aria-expanded', 'true');
        command.focus();
    }

    function closeCommandModal(): void {
        if (!commandModal || commandModal.hidden) return;
        commandModal.hidden = true;
        keyboardBtn?.setAttribute('aria-expanded', 'false');
        keyboardBtn?.focus();
    }

    keyboardBtn?.addEventListener('click', openCommandModal);
    commandModalClose?.addEventListener('click', closeCommandModal);
    commandCancelBtn?.addEventListener('click', closeCommandModal);
    commandModalBackdrop?.addEventListener('click', closeCommandModal);

    commandForm.addEventListener('submit', (event: SubmitEvent) => {
        event.preventDefault();
        const text = command.value.trim();
        if (!text) {
            message('', true);
            showToast('Enter an instruction first.', 'error');
            return;
        }
        const submitButton = commandForm.querySelector<HTMLButtonElement>('button[type="submit"]');
        if (!submitButton) return;
        void send(submitButton, `/api/orator/command?${new URLSearchParams({ theater_id: theaterId ?? '' })}`, { text }, () => {
            if (command.value.trim() === text) command.value = '';
            closeCommandModal();
        });
    });

    let micTransmitting = false;
    let micStarting = false;
    let micPointerDownTime = 0;
    let micHolding = false;
    let agentWs: WebSocket | null = null;
    let micStream: MediaStream | null = null;
    let micAudioContext: AudioContext | null = null;
    let micWorkletNode: AudioWorkletNode | null = null;
    let micEncoder: AudioEncoder | null = null;
    let micTimestampUs = 0;
    let agentPingTimer: number | null = null;

    function updateMicUI(): void {
        if (!micBtn) return;
        const active = micTransmitting;
        micBtn.setAttribute('aria-pressed', String(active));
        micBtn.classList.toggle('transmitting', active);
        const textSpan = micBtn.querySelector('.btn-text');
        if (textSpan) {
            textSpan.textContent = active ? 'Listening…' : 'Microphone';
        }
        const label = active ? 'Stop microphone' : 'Microphone';
        micBtn.setAttribute('aria-label', label);
        micBtn.title = active ? 'Stop transmitting audio' : 'Transmit audio to Narratron';
    }

    function startAgentPing(): void {
        stopAgentPing();
        agentPingTimer = window.setInterval(() => {
            if (agentWs && agentWs.readyState === WebSocket.OPEN) {
                try {
                    agentWs.send(JSON.stringify({ type: 'ping', ts: Date.now() }));
                } catch {
                    // Ignore send failures on closing socket.
                }
            }
        }, 5000);
    }

    function stopAgentPing(): void {
        if (agentPingTimer !== null) {
            window.clearInterval(agentPingTimer);
            agentPingTimer = null;
        }
    }

    function connectAgentWs(): Promise<WebSocket> {
        if (agentWs && agentWs.readyState === WebSocket.OPEN) {
            return Promise.resolve(agentWs);
        }
        if (agentWs && agentWs.readyState === WebSocket.CONNECTING) {
            const connectingSocket = agentWs;
            return new Promise<WebSocket>((resolve, reject) => {
                const pendingOpen = connectingSocket.onopen;
                const pendingError = connectingSocket.onerror;
                connectingSocket.onopen = (e: Event) => {
                    if (typeof pendingOpen === 'function') pendingOpen.call(connectingSocket, e);
                    resolve(connectingSocket);
                };
                connectingSocket.onerror = (e: Event) => {
                    if (typeof pendingError === 'function') pendingError.call(connectingSocket, e);
                    reject(new Error('Agent connection failed.'));
                };
            });
        }
        const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${wsProtocol}//${location.host}/ws/${encodeURIComponent(theaterId ?? '')}/agent`;
        const socket = new WebSocket(wsUrl);
        agentWs = socket;
        window.agentWs = socket;

        return new Promise<WebSocket>((resolve, reject) => {
            socket.onopen = () => {
                resolve(socket);
            };
            socket.onerror = () => {
                if (agentWs === socket) {
                    agentWs = null;
                    window.agentWs = null;
                }
                reject(new Error('Could not connect to Narratron live agent.'));
            };
            socket.onclose = () => {
                if (agentWs === socket) {
                    agentWs = null;
                    window.agentWs = null;
                    stopAgentPing();
                    if (micTransmitting) {
                        void stopMicrophone('connection_closed');
                    }
                }
            };
            socket.onmessage = (event: MessageEvent<string>) => {
                try {
                    const data = JSON.parse(event.data) as AgentSocketPayload;
                    if (data.type === 'insufficient_credits') {
                        void stopMicrophone('insufficient_credits');
                        showToast(data.detail || 'The theater owner has insufficient credits.', 'error');
                    } else if (data.type === 'agent_failed') {
                        void stopMicrophone('agent_failed');
                        showToast('Agent session failed.', 'error');
                    }
                } catch {
                    // Ignore non-JSON messages.
                }
            };
        });
    }

    function cleanupMicCapture(): void {
        if (micWorkletNode) {
            try { micWorkletNode.disconnect(); } catch { /* Already disconnected. */ }
            micWorkletNode = null;
        }
        if (micAudioContext && micAudioContext.state !== 'closed') {
            try { void micAudioContext.close(); } catch { /* Already closed. */ }
            micAudioContext = null;
        }
        if (micStream) {
            try {
                micStream.getTracks().forEach((track) => track.stop());
            } catch {
                // Ignore track stop errors.
            }
            micStream = null;
        }
        if (micEncoder && micEncoder.state !== 'closed') {
            try { micEncoder.close(); } catch { /* Already closed. */ }
            micEncoder = null;
        }
    }

    async function startMicrophone(): Promise<void> {
        if (micTransmitting || micStarting) return;
        if (!state?.agent_running) {
            showToast('Turn the agent on first.', 'error');
            return;
        }
        micStarting = true;
        micBtn?.setAttribute('aria-busy', 'true');
        try {
            if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') {
                throw new Error('Microphone access is not supported in this browser.');
            }

            const socket = await connectAgentWs();
            if (!socket || socket.readyState !== WebSocket.OPEN) {
                throw new Error('Agent connection is not ready.');
            }
            startAgentPing();

            const audioWin = window as RemoteAudioWindow;
            if (typeof audioWin.AudioEncoder !== 'function') {
                throw new Error('Your browser does not support AudioEncoder for live audio.');
            }

            micStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount: 1,
                    sampleRate: { ideal: 16000 },
                    echoCancellation: true,
                    noiseSuppression: true,
                },
            });

            const AudioCtx = window.AudioContext || audioWin.webkitAudioContext;
            if (!AudioCtx) {
                throw new Error('AudioContext is not supported in this browser.');
            }
            const audioContext = new AudioCtx({ sampleRate: 16000 });
            micAudioContext = audioContext;

            micTimestampUs = 0;
            const AudioEncoderCtor = audioWin.AudioEncoder;
            const encoder = new AudioEncoderCtor({
                output: (chunk: EncodedAudioChunk) => {
                    if (!agentWs || agentWs.readyState !== WebSocket.OPEN) return;
                    const buffer = new Uint8Array(chunk.byteLength);
                    chunk.copyTo(buffer);
                    agentWs.send(buffer.buffer);
                },
                error: (err: DOMException) => {
                    console.error('[RemoteMic] Opus encoder error:', err);
                    showToast('Audio encoding error.', 'error');
                    void stopMicrophone('encoder_error');
                },
            });
            encoder.configure({
                codec: 'opus',
                sampleRate: 16000,
                numberOfChannels: 1,
                bitrate: 24000,
            });
            micEncoder = encoder;

            socket.send(JSON.stringify({
                type: 'activity_start',
                reason: 'remote_mic',
                ts: new Date().toISOString(),
            }));

            const blob = new Blob([REMOTE_PCM_WORKLET_SOURCE], { type: 'application/javascript' });
            const blobUrl = URL.createObjectURL(blob);
            try {
                await audioContext.audioWorklet.addModule(blobUrl);
            } finally {
                URL.revokeObjectURL(blobUrl);
            }

            const source = audioContext.createMediaStreamSource(micStream);
            const worklet = new AudioWorkletNode(audioContext, 'remote-pcm-processor');
            micWorkletNode = worklet;
            const AudioDataCtor = audioWin.AudioData ?? globalThis.AudioData;

            worklet.port.onmessage = (event: MessageEvent<Float32Array<ArrayBuffer> | null | undefined>) => {
                if (!micTransmitting || !micEncoder || micEncoder.state !== 'configured') return;
                const float32 = event.data;
                if (!float32) return;
                try {
                    const audioData = new AudioDataCtor({
                        format: 'f32-planar',
                        sampleRate: 16000,
                        numberOfFrames: float32.length,
                        numberOfChannels: 1,
                        timestamp: micTimestampUs,
                        data: float32,
                    });
                    micTimestampUs += Math.round((float32.length / 16000) * 1000000);
                    micEncoder.encode(audioData);
                    audioData.close();
                } catch (encodeErr: unknown) {
                    console.error('[RemoteMic] Frame encode failed:', encodeErr);
                }
            };

            source.connect(worklet);
            worklet.connect(audioContext.destination);

            micTransmitting = true;
            micStarting = false;
            micBtn?.removeAttribute('aria-busy');
            updateMicUI();
            message('Transmitting audio…');
        } catch (err: unknown) {
            micStarting = false;
            micTransmitting = false;
            micBtn?.removeAttribute('aria-busy');
            cleanupMicCapture();
            updateMicUI();
            const msg = isPermissionDeniedError(err)
                ? 'Microphone permission denied.'
                : getErrorMessage(err, 'Could not access microphone.');
            showToast(msg, 'error');
        }
    }

    async function stopMicrophone(reason = 'user_stop'): Promise<void> {
        if (!micTransmitting && !micStarting) return;
        micTransmitting = false;
        micStarting = false;
        micBtn?.removeAttribute('aria-busy');
        updateMicUI();
        message('');

        if (micEncoder && micEncoder.state === 'configured') {
            try {
                await micEncoder.flush();
            } catch {
                // Ignore flush error during teardown.
            }
        }

        if (agentWs && agentWs.readyState === WebSocket.OPEN) {
            try {
                agentWs.send(JSON.stringify({
                    type: 'activity_end',
                    reason,
                    ts: new Date().toISOString(),
                }));
            } catch {
                // Ignore send error during teardown.
            }
        }

        cleanupMicCapture();
    }

    micBtn?.addEventListener('pointerdown', (event: PointerEvent) => {
        if (event.button !== 0) return;
        if (!state?.agent_running) {
            showToast('Turn the agent on first.', 'error');
            return;
        }
        micPointerDownTime = Date.now();
        micHolding = true;
        if (!micTransmitting) {
            void startMicrophone();
        } else {
            void stopMicrophone('user_toggle');
            micHolding = false;
        }
    });

    micBtn?.addEventListener('pointerup', () => {
        if (!micHolding) return;
        micHolding = false;
        const duration = Date.now() - micPointerDownTime;
        if (duration > 400 && micTransmitting) {
            void stopMicrophone('user_release');
        }
    });

    micBtn?.addEventListener('pointercancel', () => {
        if (micHolding) {
            micHolding = false;
            if (micTransmitting) void stopMicrophone('pointer_cancel');
        }
    });

    micBtn?.addEventListener('keydown', (event: KeyboardEvent) => {
        if (event.key === ' ' && !event.repeat) {
            event.preventDefault();
            if (!micTransmitting) void startMicrophone();
        }
    });

    micBtn?.addEventListener('keyup', (event: KeyboardEvent) => {
        if (event.key === ' ') {
            event.preventDefault();
            if (micTransmitting) void stopMicrophone('space_release');
        }
    });

    micBtn?.addEventListener('click', () => {
        if (Date.now() - micPointerDownTime < 350) return;
        if (!micTransmitting) void startMicrophone();
        else void stopMicrophone('user_click');
    });

    const handlePageHide = (): void => {
        previewAudio.update(null, false);
        window.clearTimeout(audioReconnectTimer);
        window.clearTimeout(audioRefreshTimer);
        if (audioWs) {
            audioWs.close();
            audioWs = null;
        }
        if (micTransmitting) void stopMicrophone('page_hide');
        if (doodleWs) {
            try { doodleWs.close(); } catch { /* Ignore close errors. */ }
            doodleWs = null;
        }
    };
    window.addEventListener('pagehide', handlePageHide);

    const handleVisibilityChange = (): void => {
        window.clearTimeout(pollTimer);
        if (document.hidden) {
            previewAudio.update(state, false);
            if (micTransmitting) void stopMicrophone('page_hidden');
        } else {
            connectViewerSocket();
            connectAudioSocket();
            if (theaterId && !busy) {
                void refresh(generation);
            }
        }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);

    render();
    renderView();
    void fetchCurrentUser().then(() => {
        if (theaterId && /^[a-zA-Z0-9_-]+$/.test(theaterId)) {
            chatController = initializeChatController({
                messagesContainer: document.getElementById('chat-messages'),
                chatForm: document.getElementById('chat-form') as HTMLFormElement | null,
                chatInput: document.getElementById('chat-input') as HTMLTextAreaElement | null,
                nameDisplay: document.getElementById('chat-name-display'),
                theaterId,
                getAuthState: async () => ({ authenticated: Boolean(currentUser), user: currentUser }),
                isCurrentOrator: isOratorUser,
                getAgentWs: () => agentWs,
            });
        }
        return checkBatonState();
    });
    connectViewerSocket();
    connectAudioSocket();
    if (theaterId && /^[a-zA-Z0-9_-]+$/.test(theaterId)) {
        const theaterIdEl = document.getElementById('theater-id');
        if (theaterIdEl) {
            theaterIdEl.textContent = theaterId;
            theaterIdEl.title = theaterId;
        }
        setTheaterKey(theaterKey);
        canvasLink.href = `/canvas?${new URLSearchParams({ theater_id: theaterId })}`;
        canvasLink.hidden = false;
        void refresh(generation);
    } else {
        const msg = 'Open the remote from your theater.';
        status.textContent = msg;
        showToast(msg, 'error');
    }

    return {
        destroy(): void {
            generation += 1;
            window.clearTimeout(pollTimer);
            window.clearTimeout(toastTimer);
            window.clearTimeout(audioReconnectTimer);
            window.clearTimeout(audioRefreshTimer);
            window.clearTimeout(doodleReconnectTimer);
            stopAgentPing();
            if (batonTimerInterval !== null) {
                window.clearInterval(batonTimerInterval);
                batonTimerInterval = null;
            }
            window.removeEventListener('hashchange', handleHashChange);
            window.removeEventListener('pagehide', handlePageHide);
            document.removeEventListener('keydown', handleDocumentKeyDown);
            document.removeEventListener('visibilitychange', handleVisibilityChange);
            handlePageHide();
            if (agentWs) {
                try { agentWs.close(); } catch { /* Ignore close errors. */ }
                agentWs = null;
                window.agentWs = null;
            }
        },
    };
}

if (typeof document !== 'undefined' && document.getElementById('controls') && document.getElementById('remote-views')) {
    initializeRemote();
}
