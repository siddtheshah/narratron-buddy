import { createAgentActivityController } from "/static/generated/canvas/agent-activity.js";
import { createCanvasAssetManager } from "/static/generated/canvas/asset-manager.js";
import { createBatonManager } from "/static/generated/canvas/baton-manager.js";
import { createCollabBarController } from "/static/generated/canvas/collab-bar.js";
import { createCornerPanels } from "/static/generated/canvas/corner-panels.js";
import { createDoodleSession, isActiveCanvasWindow } from "/static/generated/canvas/doodle-session.js";
import { createHistoryPagingController } from "/static/generated/canvas/history-paging.js";
import { createLayoutResizeController } from "/static/generated/canvas/layout-resize.js";
import { createMicConfigController } from "/static/generated/canvas/mic-config.js";
import { createMusicPlayer, MUSIC_MUTED_STORAGE_KEY } from "/static/generated/canvas/music-player.js";
import { createCanvasPinControl } from "/static/generated/canvas/pin-control.js";
import { createSpeechReplayControls } from "/static/generated/canvas/speech-replay.js";
import {
  createCanvasStampsController,
  stampNameFromPrompt,
  updateStampManagerProfileLinks,
} from "/static/generated/canvas/stamps.js";
import { startCanvasStatePolling } from "/static/generated/canvas/state-polling.js";

        import { setupTheaterReport } from '/static/js/theater-report.js';
        import { initializeStoryLogInspector } from "/static/js/story-log-inspector.js";
        import { initializeCanvasBookmarks } from "/static/js/canvas-bookmarks.js";
        import { initializeActionWheel } from "/static/js/action-wheel.js?v=cursor-cycle-20261028-1";
        import { canvasCursorState } from '/static/generated/canvas/cursor-state.js';
        import { initializeChatController } from "/static/js/chat-controller.js";
        import { initializeBeyond20Listener } from "/static/js/beyond20-listener.js";
        import {
            DoodleAction,
            DrawStrokeDoodleAction,
            DeleteSegmentsDoodleAction,
            PlaceTextDoodleAction,
            EditTextDoodleAction,
            MoveTextDoodleAction,
            DeleteTextDoodleAction,
            PlaceStampDoodleAction,
            TransformStampDoodleAction,
            DeleteStampDoodleAction,
            CompoundDeleteDoodleAction,
            ClearCanvasDoodleAction,
            restoreSegmentsToServer,
            createDoodleUndoManager
        } from '/static/js/canvas-doodle-actions.js?v=20261101-1';

        // Plain Space is reserved for typing, never canvas shortcuts or button activation.
        function ignorePlainSpace(event) {
            if (event.code !== 'Space' || event.altKey || event.ctrlKey || event.shiftKey || event.metaKey) return;
            const active = document.activeElement;
            const isEditing = active && (['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName) || active.isContentEditable);
            if (!isEditing) event.preventDefault();
            event.stopImmediatePropagation();
        }
        window.addEventListener('keydown', ignorePlainSpace, true);
        window.addEventListener('keyup', ignorePlainSpace, true);

        function escapeHtml(str) {
            return String(str ?? '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        // Keep canvas identity self-contained: this page must still recognize
        // its owner if a shared UI script fails to load. Concurrent consumers
        // on this page nevertheless share the same auth request.
        let currentUser = null;
        let currentBatonState = null;
        let canvasAuthStatePromise = null;

        function getCanvasAuthState() {
            if (!canvasAuthStatePromise) {
                canvasAuthStatePromise = fetch('/api/auth/me')
                    .then(res => res.json())
                    .then(async data => {
                        if (data && data.testing_use_local && data.server_run_id) {
                            try {
                                const storageKey = 'narratron_server_run_id';
                                const storedRunId = localStorage.getItem(storageKey);
                                if (storedRunId !== data.server_run_id) {
                                    if (!data.authenticated) {
                                        const autoRes = await fetch('/api/auth/auto-login', { method: 'POST' });
                                        if (autoRes.ok) {
                                            const autoData = await autoRes.json();
                                            data = { ...data, authenticated: true, user: autoData.user };
                                        }
                                    }
                                    localStorage.setItem(storageKey, data.server_run_id);
                                }
                            } catch (e) {
                                console.warn('Canvas testing auto-login check failed:', e);
                            }
                        }
                        if (data && data.authenticated && data.user) {
                            currentUser = data.user;
                            updateStampManagerProfileLinks(currentUser);
                        }
                        return data;
                    })
                    .catch(error => {
                        canvasAuthStatePromise = null;
                        throw error;
                    });
            }
            return canvasAuthStatePromise;
        }

        window.onAuthSuccess = function (action, data) {
            window.location.reload();
        };

        // Corner panels: Scene Suggestions, Story Sticky Notes, and Hide Canvas Text
        const cornerPanels = createCornerPanels({
            getTheaterId: () => (typeof theaterId !== 'undefined' ? theaterId : null),
            suggestionsButton: document.getElementById('suggestions-toggle-btn'),
            suggestionsList: document.getElementById('suggestions-list'),
            suggestionsLoading: document.getElementById('suggestions-loading'),
            stickyNotesButton: document.getElementById('sticky-notes-toggle-btn'),
            stickyNotesList: document.getElementById('sticky-notes-list'),
            stickyNotesLoading: document.getElementById('sticky-notes-loading'),
            stickyNotesBadge: document.getElementById('sticky-notes-count-badge'),
            hideCanvasTextButton: document.getElementById('hide-canvas-text-btn'),
            imageContainer: document.getElementById('image-container'),
        });
        window.updateCanvasStickyNotes = cornerPanels.updateCanvasStickyNotes;
        window.isStickiesRevealedInCache = cornerPanels.isStickiesRevealedInCache;
        window.setStickiesRevealedInCache = cornerPanels.setStickiesRevealedInCache;
        window.STICKIES_REVEAL_CACHE_TIMEOUT_MS = cornerPanels.STICKIES_REVEAL_CACHE_TIMEOUT_MS;
        window.setCanvasTextHidden = cornerPanels.setCanvasTextHidden;

        let startAudioRecorderWorklet;
        let setMicrophoneTransmissionEnabled;
        let stopMicrophone;
        let listenForSpeech;
        import(`/static/js/audio-recorder.js?cache_bust=${Date.now()}`)
            .then((module) => {
                startAudioRecorderWorklet = module.startAudioRecorderWorklet;
                setMicrophoneTransmissionEnabled = module.setMicrophoneTransmissionEnabled;
                stopMicrophone = module.stopMicrophone;
            })
            .catch((err) => {
                console.error("Failed to load audio-recorder.js:", err);
            });
        import(`/static/js/device-aware-pcm.js?cache_bust=${Date.now()}`)
            .then((module) => {
                listenForSpeech = module.listenForSpeech;
            })
            .catch((err) => {
                console.error("Failed to load device-aware-pcm.js:", err);
            });
        import { createImageRenderer } from "/static/js/canvas-renderers.js?v=zoom-effect-origin-20261030-1";
        import { createA2UICanvasRenderer } from "/static/generated/canvas/a2ui-renderer.js";
        import { createGameRenderer } from "/static/js/game-renderer.js?v=17";
        import { getAnimationSignature, playCanvasAnimation } from "/static/generated/canvas/media.js";
        import { createCanvasScene } from "/static/generated/canvas/scene.js";
        import { createNarrationPane } from "/static/js/narration-pane.js";

        let isMuted = (() => {
            try {
                return localStorage.getItem(MUSIC_MUTED_STORAGE_KEY) === 'true';
            } catch (_) {
                return false;
            }
        })();
        let musicPlayer = null;
        // A state notification can arrive while the initial snapshot is still
        // in flight.  Only the most recently requested snapshot may update the
        // canvas, otherwise a late empty startup response can erase the first
        // story-planner scene.
        let latestStateRequestSequence = 0;
        const currentImage = document.getElementById('current-image');
        const canvasPinBtn = document.getElementById('canvas-pin-btn');
        const canvasPinLabel = document.getElementById('canvas-pin-label');
        let canvasPinned = false;
        let musicPinned = false;
        const bgLayer = document.getElementById('bg-layer');
        const promptTextSpan = document.getElementById('prompt-text');
        const chatMessages = document.getElementById('chat-messages');
        const chatForm = document.getElementById('chat-form');
        const chatInput = document.getElementById('chat-input');
        const agentThoughtText = document.getElementById('agent-thought-text');
        const narrationPane = await createNarrationPane(document.getElementById('narration-pane-mount'));
        const a2uiCanvasLayer = document.getElementById('a2ui-canvas-layer');
        const gameRenderer = createGameRenderer({
            container: document.getElementById('game-state-layer'),
            toggleButton: document.getElementById('game-state-toggle-btn'),
            actionUrl: () => `/api/game/action?theater_id=${encodeURIComponent(currenttheaterId || '')}`,
            canContribute: () => isCollabModeEnabled() && hasContributorsPermission(),
            canMove: () => isCurrentOrator() || hasContributorsPermission(),
            canEdit: () => isCurrentOrator(),
            refresh: () => fetchLatestImage(),
            beforeOpen: async () => {
                if (isCurrentOrator() && !isAgentStarted) await startAgentTheater();
            },
        });
        const a2uiCanvasRenderer = createA2UICanvasRenderer({
            container: a2uiCanvasLayer,
            actionUrl: () => `/api/a2ui/action?theater_id=${encodeURIComponent(currenttheaterId || '')}`,
            surfaceUrl: surfaceId => `/api/a2ui/surfaces/${encodeURIComponent(surfaceId)}?theater_id=${encodeURIComponent(currenttheaterId || '')}`,
            canEdit: () => isCurrentOrator(),
        });
        const latestSpeechReplayBtn = document.getElementById('latest-speech-replay-btn');
        let speechReplayControls = null;

        const { updateSceneDialogue, enqueueSceneSpeech, stopSceneSpeech, replayLatestSpeech } = createCanvasScene({
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

        // Generate anonymous user ID and read theater context
        const myUserId = "Viewer_" + Math.floor(Math.random() * 10000);
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.has('role')) {
            urlParams.delete('role');
            const cleanQuery = urlParams.toString() ? `?${urlParams.toString()}` : window.location.pathname;
            window.history.replaceState({}, '', cleanQuery + window.location.hash);
        }
        const theaterId = urlParams.get('theater_id');
        window.theaterId = theaterId;
        window.currenttheaterId = theaterId;

        function isCurrentOrator() {
            return Boolean(window._isActiveOratorState);
        }

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
            onUpdated: (state) => actionWheel.updateState(state.pinned, state.musicPinned),
        });

        function updateCanvasPinButton() {
            pinControl.update();
        }

        const actionWheel = initializeActionWheel({
            isOrator: isCurrentOrator,
            canvasTarget: document.getElementById('image-container'),
            onOpen: () => { doodleSession?.cancelDrawing(); },
            onCenterAction: () => {
                if (typeof cycleCanvasCursor === 'function') {
                    cycleCanvasCursor();
                }
            },
            onRebindStart: () => {
                isRebindingHotkey = false;
                isRebindingTextHotkey = false;
                for (const item of [menuItemHotkey, menuItemTextHotkey]) {
                    if (item) { item.style.background = ''; item.style.color = ''; }
                }
                updateHotkeyUI();
                updateTextHotkeyUI();
            },
            onPreviousImage: () => {
                if (typeof stepOratorPreviousImage === 'function') {
                    stepOratorPreviousImage();
                }
            },
            sendAction: async (action) => {
                const response = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/orator-action`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action }),
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.detail || 'Unable to apply action.');
                canvasPinned = Boolean(result.pinned);
                musicPinned = Boolean(result.music_pinned);
                if (result.orator_cursor !== undefined && result.orator_cursor !== null) {
                    historyPaging.setOratorCursorIndex(Number(result.orator_cursor));
                    if (!historyPaging.isNavigating() && historyPaging.getCurrentViewIndex() !== historyPaging.getOratorCursorIndex()) {
                        displayHistoryImage(historyPaging.getOratorCursorIndex(), 'none');
                    }
                }
                updateCanvasPinButton();
                actionWheel.updateState(canvasPinned, musicPinned);
            },
        });
        // Modular Chat Controller
        chatInput?.addEventListener('focus', () => stopSceneSpeech());
        const chatController = initializeChatController({
            messagesContainer: chatMessages,
            chatForm: chatForm,
            chatInput: chatInput,
            nameInput: document.getElementById('chat-name-input'),
            nameDisplay: document.getElementById('chat-name-display'),
            nameStatusIcon: document.getElementById('chat-name-status-icon'),
            loginChip: document.getElementById('chat-login-chip'),
            onOpenLogin: () => {
                if (typeof openAuthModal === 'function') openAuthModal('login');
            },
            hideSuggestionsBtn: document.getElementById('hide-suggestions-btn'),
            theaterId: theaterId,
            isCurrentOrator: () => isCurrentOrator(),
            isTheaterOwner: () => isTheaterStampOwner,
            onSettingsApplied: async () => {
                cleanupAgentConnection();
                await checkAgentStatus();
            },
            getAgentWs: () => ((isCurrentOrator() && agentWs) ? agentWs : null),
            getAuthState: () => getCanvasAuthState(),
        });

        function isCollabModeEnabled() {
            return (typeof viewerCollabEnabled !== 'undefined') ? Boolean(viewerCollabEnabled) : false;
        }

        function hasContributorsPermission() {
            if (isCurrentOrator()) return true;
            if (window._isContributor) return true;
            if (typeof currentBatonState !== 'undefined' && currentBatonState) {
                const myId = (typeof currentUser !== 'undefined' && currentUser) ? currentUser.id : null;
                if (myId) {
                    if (currentBatonState.owner && currentBatonState.owner.id === myId) return true;
                    if (currentBatonState.active_orator && currentBatonState.active_orator.id === myId) return true;
                    if (Array.isArray(currentBatonState.contributors) && currentBatonState.contributors.some(u => u.id === myId)) {
                        return true;
                    }
                }
            }
            return false;
        }

        // Beyond20 Roll & Character Event Integration
        const beyond20Listener = initializeBeyond20Listener({
            chatController: chatController,
            theaterId: theaterId,
            isContributor: () => hasContributorsPermission(),
            isCollabModeEnabled: () => isCollabModeEnabled(),
            isCurrentOrator: () => isCurrentOrator(),
            getAgentWs: () => ((isCurrentOrator() && agentWs) ? agentWs : null),
        });

        // Backward compatibility helpers for canvas state refresh
        function fetchChat() {
            return chatController.fetchChat();
        }

        function fetchSuggestions() {
            return chatController.fetchSuggestions();
        }

        const oratorCommandOverlay = document.getElementById('orator-command-overlay');
        const oratorCommandForm = document.getElementById('orator-command-form');
        const oratorCommandInput = document.getElementById('orator-command-input');
        const oratorCommandStatus = document.getElementById('orator-command-status');

        function isOratorCommandOpen() {
            return !!oratorCommandOverlay?.classList.contains('active');
        }

        function closeOratorCommand() {
            if (!oratorCommandOverlay) return;
            oratorCommandOverlay.classList.remove('active');
            oratorCommandOverlay.setAttribute('aria-hidden', 'true');
            oratorCommandInput.value = '';
            oratorCommandStatus.textContent = '';
        }

        function openOratorCommand() {
            if (!isCurrentOrator() || !oratorCommandOverlay) return;
            stopSceneSpeech();
            oratorCommandOverlay.classList.add('active');
            oratorCommandOverlay.setAttribute('aria-hidden', 'false');
            oratorCommandStatus.textContent = '';
            requestAnimationFrame(() => oratorCommandInput.focus());
        }

        function isTextHotkeyEvent(event) {
            if (typeof currentTextHotkey === 'undefined' || !currentTextHotkey) return false;
            return event.code === currentTextHotkey.code &&
                !!event.altKey === !!currentTextHotkey.altKey &&
                !!event.ctrlKey === !!currentTextHotkey.ctrlKey &&
                !!event.shiftKey === !!currentTextHotkey.shiftKey;
        }

        oratorCommandInput?.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' || isTextHotkeyEvent(event)) {
                event.preventDefault();
                event.stopPropagation();
                closeOratorCommand();
            }
        });

        oratorCommandForm?.addEventListener('submit', async (event) => {
            event.preventDefault();
            const text = oratorCommandInput.value.trim();
            if (!text) return;

            oratorCommandInput.disabled = true;
            oratorCommandStatus.textContent = 'Sending…';
            try {
                const response = await fetch(`/api/orator/command?theater_id=${encodeURIComponent(theaterId)}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text }),
                });
                if (!response.ok) {
                    const detail = await response.json().catch(() => ({}));
                    throw new Error(detail.detail || 'Narratron could not receive the command.');
                }
                closeOratorCommand();
            } catch (error) {
                oratorCommandStatus.textContent = error.message;
            } finally {
                oratorCommandInput.disabled = false;
                if (isOratorCommandOpen()) oratorCommandInput.focus();
            }
        });
        const suggestionsHiddenStorageKey = `narratron-suggestions-hidden:${theaterId || 'default'}`;
        let suggestionsHidden = false;
        if (!theaterId) {
            console.error("Theater ID missing from URL parameters.");
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
            throw new Error("Missing required theater_id parameter in URL");
        }

        // Music player controls and state
        musicPlayer = createMusicPlayer({
            theaterId,
            muteButton: document.getElementById('music-mute-btn'),
            statusElement: document.getElementById('music-status'),
            volumeSlider: document.getElementById('music-volume-slider'),
            volumePercentElement: document.getElementById('volume-percent'),
            playbackStorage: typeof theaterStorage !== 'undefined' ? theaterStorage : null,
            onMuteStopSpeech: () => {
                if (typeof stopSceneSpeech === 'function') {
                    stopSceneSpeech();
                }
            },
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
                checkAgentStatus().then(() => ensureAgentConnectionWhenActive());
            },
        });

        function updateAgentActivity(toolActivity = {}) {
            agentActivity.update(toolActivity);
        }

        function setPcmBroadcasting(isBroadcasting) {
            agentActivity.setPcmBroadcasting(isBroadcasting);
        }

        const currenttheaterId = theaterId;

        const saveTheaterBtn = document.getElementById('save-theater-btn');
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
                        method: 'POST'
                    });
                    const data = await res.json();
                    if (res.ok) {
                        saveTheaterBtn.innerHTML = '✓';
                        setTimeout(() => {
                            saveTheaterBtn.innerHTML = originalText;
                            saveTheaterBtn.disabled = false;
                        }, 2000);
                    } else {
                        alert('Save failed: ' + (data.detail || 'Unknown error'));
                        saveTheaterBtn.innerHTML = originalText;
                        saveTheaterBtn.disabled = false;
                    }
                } catch (e) {
                    alert('Failed to save theater: ' + e.message);
                    saveTheaterBtn.innerHTML = originalText;
                    saveTheaterBtn.disabled = false;
                }
            });
        }

        const ghostImage = document.getElementById('ghost-image');
        const rendererCanvas = document.getElementById('canvas-renderer');
        let historyPaging = null;
        const imageRenderer = createImageRenderer({
            canvas: rendererCanvas,
            image: currentImage,
            backgroundLayer: bgLayer,
            loadedClassNames: ['t-crossfade', 'loaded'],
            imageEffectsEnabled: () => !(historyPaging && historyPaging.isMinimalMode()),
        });
        const resizeRendererCanvas = imageRenderer.resize;
        const applyImageTransition = imageRenderer.applyTransition;
        const stopImageAnimation = imageRenderer.stopSequence ? imageRenderer.stopSequence.bind(imageRenderer) : null;

        window.addEventListener('resize', resizeRendererCanvas);
        resizeRendererCanvas();

        const imgContainer = document.getElementById('image-container');
        const pagingControls = document.getElementById('history-paging-controls');
        let lastGameCanvasRevision = null;
        let doodleSession = null;
        let canvasBookmarks = null;

        initializeStoryLogInspector(theaterId);

        historyPaging = createHistoryPagingController({
            theaterId,
            isOrator: () => typeof isCurrentOrator === 'function' ? isCurrentOrator() : false,
            imageRenderer,
            playCanvasAnimation,
            promptButton: promptBtn,
            promptTextSpan,
            currentImage,
            ghostImage,
            backgroundLayer: bgLayer,
            pagingControls,
            prevButton: document.getElementById('page-prev-btn'),
            nextButton: document.getElementById('page-next-btn'),
            headButton: document.getElementById('page-head-btn'),
            syncButton: document.getElementById('page-sync-btn'),
            pageIndicator: document.getElementById('page-indicator'),
            newHeadBadge: document.getElementById('new-head-badge'),
            imageContainer: imgContainer,
            minimalModeInput: document.getElementById('minimal-mode-input'),
            hasDoodleCanvas: () => Boolean(document.getElementById('doodle-canvas')),
            isSelectionActive: () => Boolean(typeof window !== 'undefined' && window.isCanvasSelectionActive),
            onRefreshBookmarks: () => canvasBookmarks?.refresh(),
            onRefreshToolInteraction: () => doodleSession?.refreshCanvasToolInteraction(),
            onDisplayPageDoodles: (item) => doodleSession?.displayPageDoodles(item),
        });

        canvasBookmarks = initializeCanvasBookmarks({
            theaterId,
            isOrator: isCurrentOrator,
            getHistory: () => historyPaging.getHistory(),
            getIndex: () => historyPaging.getCurrentViewIndex(),
            navigate: async index => {
                const response = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/orator-action`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: 'bookmark_image', history_index: index }),
                });
                if (!response.ok) throw new Error('Bookmark navigation failed');
                const result = await response.json();
                historyPaging.setOratorCursorIndex(Number(result.orator_cursor));
                historyPaging.displayHistoryImage(historyPaging.getOratorCursorIndex(), 'none');
            },
        });

        const resolveWebpQualityUrl = (rawUrl) => historyPaging.resolveWebpQualityUrl(rawUrl);
        window.__resolveWebpQualityUrl = resolveWebpQualityUrl;
        window.__historyResourceManager = historyPaging.resourceManager;
        const setMinimalMode = (enabled) => historyPaging.setMinimalMode(enabled);
        const stepOratorPreviousImage = () => historyPaging.stepOratorPreviousImage();
        const stepOratorNextImage = () => historyPaging.stepOratorNextImage();
        const syncToOratorCursor = () => historyPaging.syncToOratorCursor();
        const displayHistoryImage = (index, transition = 'none') => historyPaging.displayHistoryImage(index, transition);
        const updatePagingControlsUI = () => historyPaging.updatePagingControlsUI();

        async function fetchLatestImage() {
            const requestSequence = ++latestStateRequestSequence;
            try {
                const apiUrl = currenttheaterId ? `/api/latest?theater_id=${encodeURIComponent(currenttheaterId)}` : '/api/latest';
                const response = await fetch(apiUrl);
                const data = await response.json();
                if (requestSequence !== latestStateRequestSequence) return;
                updateAgentActivity(data.tool_activity);
                updateAgentThought(data.agent_thought);
                canvasPinned = Boolean(data.pinned);
                musicPinned = Boolean(data.music_pinned);
                actionWheel.updateState(canvasPinned, musicPinned);
                updateCanvasPinButton();
                updateSceneDialogue(
                    data.scene_dialogue,
                    data.narration,
                    data.narration_spans,
                    data.committed_scene_speech_generation,
                );
                a2uiCanvasRenderer.render(data.interactive_surfaces);
                gameRenderer.render(data.game_state);
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

                // Process music payload
                if (data.music) {
                    musicPlayer?.applyMusicState(data.music);
                    updateAssetPlaybackSelection(data.music);
                }

                historyPaging.applyLatestState(data);
            } catch (err) {
                console.error("Failed to fetch image data:", err);
            }
        }

        let lastAgentThought = null;

        function updateAgentThought(thought) {
            const text = thought && thought.text ? thought.text.trim() : '';
            agentThoughtText.closest('.agent-thought-card').classList.toggle(
                'is-highlighted', Boolean(thought && thought.highlighted && text)
            );
            if (text === lastAgentThought) return;
            const isWandering = text.toLowerCase() === 'wandering';
            agentThoughtText.textContent = isWandering
                ? 'Narratron has wandered away. (Try resummoning?)'
                : (text || 'Waiting for Narratron to think…');
            agentThoughtText.classList.toggle('is-wandering', isWandering);
            agentActivity.setDisconnected(isWandering);
            lastAgentThought = text;
        }

        // Doodle Logic with Full HiDPI Persistence & Replay
        const canvas = document.getElementById('doodle-canvas');
        const doodlesToggleBtn = document.getElementById('doodles-toggle-btn');

        doodleSession = createDoodleSession({
            theaterId: currenttheaterId,
            canvas,
            imgContainer,
            pagingControls,
            doodleToolbar: document.getElementById('doodle-toolbar'),
            doodlesToggleBtn,
            paletteToggle: document.getElementById('doodle-palette-toggle'),
            paletteMenu: document.getElementById('doodle-palette-menu'),
            colorBtns: Array.from(document.querySelectorAll('.color-btn')),
            clearBtn: document.getElementById('clear-doodle-btn'),
            undoBtn: document.getElementById('undo-doodle-btn'),
            brushSizeSlider: document.getElementById('brush-size'),
            textAnnotationToggle: document.getElementById('text-annotation-toggle'),
            textAnnotationMenu: document.getElementById('text-annotation-menu'),
            textAnnotationFont: document.getElementById('text-annotation-font'),
            textAnnotationSize: document.getElementById('text-annotation-size'),
            textAnnotationSizeValue: document.getElementById('text-annotation-size-value'),
            textAnnotationColors: Array.from(document.querySelectorAll('.text-annotation-color')),
            textLayer: document.getElementById('canvas-text-layer'),
            stampLayer: document.getElementById('canvas-stamp-layer'),
            isOrator: () => typeof isCurrentOrator === 'function' && isCurrentOrator(),
            isCollabModeEnabled: () => isCollabModeEnabled(),
            isPagedBack: () => historyPaging.isPagedBack(),
            getPage: () => historyPaging.getCurrentItem()?.canvas_page || null,
            canPlaceStamps: () => canPlaceStamps(),
            stopSceneSpeech: () => stopSceneSpeech(),
            setDrawingColorSelected: (selected) => narrationPane.setDrawingColorSelected(selected),
            onMoveStamp: (action, previous, commit) => stampsController.moveStampAnnotation(action, previous, commit),
            onRemoveStamp: (action) => stampsController.removeStampAnnotation(action),
            onSelectStamp: (action) => stampsController.selectStampAnnotation(action),
            onApplyStampAnnotation: (action) => stampsController.applyStampAnnotation(action),
            onBringStampToFront: (stampId) => stampsController.bringStampToFront(stampId),
            onDoodlesDisplayChanged: () => {
                if (typeof isStampManagerOpen !== 'undefined' && isStampManagerOpen) {
                    updateStampManagerPermissionBanner();
                    if (cachedUserStamps) renderStampManagerGrid(cachedUserStamps);
                }
            },
            onHandleSocketData: (data) => typeof handleDoodleSocketData === 'function' && handleDoodleSocketData(data),
            onToggleMicSocketMessage: () => {
                if (isCurrentOrator() && agentConnectBtn && !agentConnectBtn.disabled) {
                    agentConnectBtn.click();
                }
            },
            onBatonStateSocketMessage: (batonState, activeViewers) => {
                if (typeof updateBatonStateUI === 'function') {
                    updateBatonStateUI(batonState, activeViewers);
                }
            },
            isActionWheelRightClickBound: () => Boolean(
                isCurrentOrator()
                && actionWheel
                && typeof actionWheel.isRightClickBound === 'function'
                && actionWheel.isRightClickBound()
            ),
        });

        const doodleRenderer = doodleSession.doodleRenderer;
        const doodleUndoManager = doodleSession.doodleUndoManager;
        const resizeDoodleCanvas = () => doodleSession.resizeDoodleCanvas();
        const redrawAllDoodles = () => doodleSession.redrawAllDoodles();
        const displayPageDoodles = (item) => doodleSession.displayPageDoodles(item);
        const requestPageDoodles = (pageId) => doodleSession.requestPageDoodles(pageId);
        const sendOrQueueDoodleMessage = (message) => doodleSession.sendOrQueueDoodleMessage(message);
        const flushDrawBatch = () => doodleSession.flushDrawBatch();
        const pushUndoAction = (action) => doodleSession.pushUndoAction(action);
        const undoLastDoodleAction = () => doodleSession.undoLastDoodleAction();
        const updateTextAnnotationAvailability = () => doodleSession.updateTextAnnotationAvailability();
        const setDoodlesDisplay = (enabled) => doodleSession.setDoodlesDisplay(enabled);
        const cycleCanvasCursor = () => doodleSession.cycleCanvasCursor();

        window.doodleUndoManager = doodleUndoManager;
        try {
            Object.defineProperty(window, 'doodleUndoStack', {
                get() { return doodleSession.getDoodleUndoStack(); },
                set(val) { doodleSession.setDoodleUndoStack(val); },
                configurable: true
            });
        } catch (_) {
            window.doodleUndoStack = doodleSession.getDoodleUndoStack();
        }
        window.undoLastDoodleAction = undoLastDoodleAction;
        window.undoDoodleAction = undoLastDoodleAction;
        window.DoodleAction = typeof DoodleAction !== 'undefined' ? DoodleAction : undefined;
        window.DrawStrokeDoodleAction = typeof DrawStrokeDoodleAction !== 'undefined' ? DrawStrokeDoodleAction : undefined;
        window.DeleteSegmentsDoodleAction = typeof DeleteSegmentsDoodleAction !== 'undefined' ? DeleteSegmentsDoodleAction : undefined;
        window.PlaceTextDoodleAction = typeof PlaceTextDoodleAction !== 'undefined' ? PlaceTextDoodleAction : undefined;
        window.EditTextDoodleAction = typeof EditTextDoodleAction !== 'undefined' ? EditTextDoodleAction : undefined;
        window.MoveTextDoodleAction = typeof MoveTextDoodleAction !== 'undefined' ? MoveTextDoodleAction : undefined;
        window.DeleteTextDoodleAction = typeof DeleteTextDoodleAction !== 'undefined' ? DeleteTextDoodleAction : undefined;
        window.PlaceStampDoodleAction = typeof PlaceStampDoodleAction !== 'undefined' ? PlaceStampDoodleAction : undefined;
        window.TransformStampDoodleAction = typeof TransformStampDoodleAction !== 'undefined' ? TransformStampDoodleAction : undefined;
        window.DeleteStampDoodleAction = typeof DeleteStampDoodleAction !== 'undefined' ? DeleteStampDoodleAction : undefined;
        window.CompoundDeleteDoodleAction = typeof CompoundDeleteDoodleAction !== 'undefined' ? CompoundDeleteDoodleAction : undefined;
        window.ClearCanvasDoodleAction = typeof ClearCanvasDoodleAction !== 'undefined' ? ClearCanvasDoodleAction : undefined;
        window.restoreSegmentsToServer = typeof restoreSegmentsToServer !== 'undefined' ? restoreSegmentsToServer : undefined;

        // ========================================
        // Asset Manager & Canvas Stamp Dropping
        // ========================================
        const stampManagerGrid = document.getElementById('stamp-manager-grid');
        const stampManagerEmpty = document.getElementById('stamp-manager-empty');
        const stampManagerCount = document.getElementById('stamp-manager-count');
        const stampManagerPermissionBanner = document.getElementById('stamp-manager-permission-banner');
        const stampManagerTabs = document.getElementById('stamp-manager-tabs');
        const stampTabStampsBtn = document.getElementById('stamp-tab-stamps');
        const stampTabScenesBtn = document.getElementById('stamp-tab-scenes');
        const stampTabCharactersBtn = document.getElementById('stamp-tab-characters');
        const scenesManagerGrid = document.getElementById('scenes-manager-grid');
        const scenesManagerEmpty = document.getElementById('scenes-manager-empty');
        const scenesEmptyMessage = document.getElementById('scenes-empty-message');
        const charactersManagerGrid = document.getElementById('characters-manager-grid');
        const charactersManagerEmpty = document.getElementById('characters-manager-empty');
        const charactersEmptyMessage = document.getElementById('characters-empty-message');

        let isStampManagerOpen = false;
        let activeStampManagerTab = 'stamps';
        let cachedUserStamps = null;
        let cachedTheaterStamps = [];
        let cachedTheaterScenes = null;
        let cachedTheaterCharacters = null;
        let isFetchingTheaterScenes = false;
        let isFetchingTheaterCharacters = false;
        let isPushingScene = false;
        let isPushingCharacter = false;
        let isTheaterStampOwner = false;

        const stampsController = createCanvasStampsController({
            canvas,
            imgContainer,
            getTheaterId: () => theaterId || currenttheaterId || new URLSearchParams(window.location.search).get('theater_id'),
            getCurrentUser: () => currentUser,
            setCurrentUser: user => { currentUser = user; },
            getMyUserId: () => myUserId,
            getAuthState: () => getCanvasAuthState(),
            openAuthModal: mode => {
                if (typeof openAuthModal === 'function') openAuthModal(mode);
            },
            hasContributorPermission: () => hasContributorsPermission(),
            isOrator: () => typeof isCurrentOrator === 'function' && isCurrentOrator(),
            isOwner: () => isTheaterStampOwner,
            isDoodlesEnabled: () => doodleSession.isDoodlesEnabled(),
            isPagedBack: () => historyPaging.isPagedBack(),
            enableDoodles: () => {
                setDoodlesDisplay(true);
                sendOrQueueDoodleMessage({ type: 'toggle_doodles', enabled: true });
            },
            selectSelectCursor: () => canvasCursorState.select({ type: 'select' }),
            getDoodleActions: () => doodleSession.getDoodleActions(),
            redrawDoodles: () => redrawAllDoodles(),
            sendDoodleMessage: sendOrQueueDoodleMessage,
            rememberPendingEdit: (clientMessageId, previous) => doodleSession.getPendingTextEdits().set(clientMessageId, previous),
            getActiveDeleteTransaction: () => doodleSession.getActiveDeleteTransaction(),
            pushUndoAction: action => {
                if (typeof pushUndoAction === 'function') pushUndoAction(action);
            },
            getActiveTab: () => activeStampManagerTab,
            isPlaylistExpanded: playlistName => expandedPlaylists.has(playlistName),
            resolveImageUrl: url => typeof resolveWebpQualityUrl === 'function' ? resolveWebpQualityUrl(url) : url,
            getNormalizedDropPoint: event => historyPaging.getCurrentItem()?.canvas_page
                ? doodleRenderer.normalizedPoint(event) : null,
            getReferenceSources: () => ({
                characters: typeof cachedTheaterCharacters !== 'undefined' ? cachedTheaterCharacters : window.cachedTheaterCharacters,
                scenes: typeof cachedTheaterScenes !== 'undefined' ? cachedTheaterScenes : window.cachedTheaterScenes,
                references: window.cachedTheaterReferences,
            }),
            onTabsVisibilityUpdate: () => {
                if (typeof updateStampTabsVisibility === 'function') updateStampTabsVisibility();
            },
            onGenerationPermissionUpdate: () => {
                updateAssetGenerationUI();
                document.getElementById('scene-generation-form').hidden = !isTheaterStampOwner || activeStampManagerTab !== 'scenes';
                document.getElementById('scene-generation-status').hidden = !isTheaterStampOwner || activeStampManagerTab !== 'scenes';
            },
            onOpenNonStampTab: tab => {
                if (tab === 'scenes') {
                    if (cachedTheaterScenes) renderScenesManagerGrid(cachedTheaterScenes);
                    if (typeof loadTheaterScenes === 'function') loadTheaterScenes();
                } else if (tab === 'characters') {
                    if (cachedTheaterCharacters) renderCharactersManagerGrid(cachedTheaterCharacters);
                    if (typeof loadTheaterCharacters === 'function') loadTheaterCharacters();
                } else if (tab === 'playlists' || tab === 'animations') {
                    loadExtraAssets(tab);
                }
            },
            getIsOpen: () => isStampManagerOpen,
            setIsOpen: open => { isStampManagerOpen = open; },
            getCachedUserStamps: () => cachedUserStamps,
            setCachedUserStamps: stamps => { cachedUserStamps = stamps; },
            getCachedTheaterStamps: () => cachedTheaterStamps,
            setCachedTheaterStamps: stamps => { cachedTheaterStamps = stamps; },
        });

        const pendingStampGenerations = stampsController.pendingGenerations;

        function updateStampGenerationStatus(message = '') {
            stampsController.updateGenerationStatus(message);
        }

        function updateStampGenerationPermission() {
            stampsController.updateGenerationPermission();
        }

        function extractTaggedCanvasReferences(promptText) {
            return stampsController.extractTaggedReferences(promptText);
        }

        function attachCanvasReferenceAutocomplete(textarea) {
            stampsController.attachReferenceAutocomplete(textarea);
        }

        function initCanvasReferenceAutocomplete() {
            ['stamp-generation-prompt', 'scene-generation-prompt', 'character-generation-prompt', 'animation-generation-prompt'].forEach(id => {
                attachCanvasReferenceAutocomplete(document.getElementById(id));
            });
        }

        initCanvasReferenceAutocomplete();

        const expandedPlaylists = new Set();

        function updateAssetGenerationUI() {
            if (typeof assetManager !== 'undefined' && assetManager) {
                assetManager.updateAssetGenerationUI();
            }
        }

        function applyAssetSearch() {
            stampsController.applyAssetSearch();
        }

        function setStampManagerOpen(open) {
            stampsController.setOpen(open);
        }

        function updateStampManagerPermissionBanner() {
            stampsController.updatePermissionBanner();
        }

        stampTabStampsBtn?.addEventListener('click', () => {
            if (typeof switchStampManagerTab === 'function') switchStampManagerTab('stamps');
        });
        stampTabScenesBtn?.addEventListener('click', () => {
            if (typeof switchStampManagerTab === 'function') switchStampManagerTab('scenes');
        });
        stampTabCharactersBtn?.addEventListener('click', () => {
            if (typeof switchStampManagerTab === 'function') switchStampManagerTab('characters');
        });

        function getStampUsageStorageKey() {
            return stampsController.getStampUsageStorageKey();
        }

        function getStampLastUsedTimestamp(stampId) {
            return stampsController.getStampLastUsedTimestamp(stampId);
        }

        function recordStampUsage(stampId) {
            stampsController.recordStampUsage(stampId);
        }

        async function loadUserStamps() {
            return stampsController.loadUserStamps();
        }

        function renderStampManagerGrid(stamps) {
            stampsController.renderGrid(stamps);
        }

        function canPlaceStamps() {
            return stampsController.canPlaceStamps();
        }

        function placeStampOnCanvas(stampData, normX, normY) {
            stampsController.placeStampOnCanvas(stampData, normX, normY);
        }

        // Canvas-state notifications are deliberately separate from the
        // doodle socket. They invalidate REST data; they never carry canvas
        // state themselves, so reconnects and rolling deploys remain simple.
        let canvasStateWs = null;
        let canvasStateReconnectDelay = 1000;
        let canvasStateReconnectTimer = null;
        let canvasStateRefreshTimer = null;
        const pendingCanvasStateDomains = new Set();

        function refreshCanvasState(domains = ['latest', 'chat', 'suggestions']) {
            domains.forEach(domain => pendingCanvasStateDomains.add(domain));
            if (canvasStateRefreshTimer) return;
            canvasStateRefreshTimer = setTimeout(() => {
                canvasStateRefreshTimer = null;
                const pending = new Set(pendingCanvasStateDomains);
                pendingCanvasStateDomains.clear();
                if (pending.has('latest')) fetchLatestImage();
                if (pending.has('chat')) fetchChat();
                if (pending.has('suggestions')) fetchSuggestions();
            }, 50);
        }

        function connectCanvasStateSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const socketUrl = `${protocol}//${window.location.host}/ws/canvas-state` +
                (currenttheaterId ? `?theater_id=${encodeURIComponent(currenttheaterId)}` : '');
            canvasStateWs = new WebSocket(socketUrl);

            canvasStateWs.onopen = () => {
                canvasStateReconnectDelay = 1000;
            };
            canvasStateWs.onmessage = event => {
                let message;
                try {
                    message = JSON.parse(event.data);
                } catch (error) {
                    console.warn('Ignoring invalid canvas-state message.', error);
                    return;
                }
                if (message.type === 'state_ready') {
                    refreshCanvasState();
                } else if (message.type === 'scene_speech_ready') {
                    enqueueSceneSpeech(message.audio_url, message.speech_generation);
                } else if (message.type === 'state_changed' && Array.isArray(message.domains)) {
                    refreshCanvasState(message.domains);
                }
            };
            canvasStateWs.onclose = () => {
                canvasStateWs = null;
                if (!canvasStateReconnectTimer) {
                    canvasStateReconnectTimer = setTimeout(() => {
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

        // ========================================
        // Asset Manager Tab Switching (Scenes & Characters for Orator)
        // ========================================
        let assetMusicState = null;
        const assetManager = createCanvasAssetManager({
            getTheaterId: () => (typeof theaterId !== 'undefined' && theaterId) || (typeof currenttheaterId !== 'undefined' && currenttheaterId) || new URLSearchParams(window.location.search).get('theater_id'),
            isOrator: isCurrentOrator,
            isStampOwner: () => Boolean(isTheaterStampOwner),
            isManagerOpen: () => Boolean(isStampManagerOpen),
            getActiveTab: () => activeStampManagerTab,
            setActiveTab: (tab) => { activeStampManagerTab = tab; },
            getCachedScenes: () => cachedTheaterScenes,
            setCachedScenes: (scenes) => { cachedTheaterScenes = scenes; },
            getCachedCharacters: () => cachedTheaterCharacters,
            setCachedCharacters: (characters) => { cachedTheaterCharacters = characters; },
            getCurrentTrackSrc: () => musicPlayer ? musicPlayer.getCurrentTrackSrc() : null,
            setCurrentTrackSrc: (src) => { musicPlayer?.setCurrentTrackSrc(src); },
            nameFromPrompt: (prompt) => stampNameFromPrompt(prompt),
            extractTaggedReferences: (prompt) => extractTaggedCanvasReferences(prompt),
            applyAssetSearch: () => applyAssetSearch(),
            resolveWebpQualityUrl: (url) => typeof resolveWebpQualityUrl === 'function' ? resolveWebpQualityUrl(url) : url,
            showShareToast: (message) => {
                if (typeof showShareToast === 'function') showShareToast(message);
            },
            fetchLatestImage: () => {
                if (typeof fetchLatestImage === 'function') fetchLatestImage();
                else if (typeof refreshCanvasState === 'function') refreshCanvasState(['latest']);
            },
            refreshCanvasState: (domains) => {
                if (typeof refreshCanvasState === 'function') refreshCanvasState(domains);
            },
            onShowStampsTab: () => {
                updateStampGenerationPermission();
                updateStampManagerPermissionBanner();
                if (stampManagerCount) {
                    const count = (typeof cachedUserStamps !== 'undefined' && cachedUserStamps) ? cachedUserStamps.length : 0;
                    stampManagerCount.textContent = `${count}/10`;
                    const theaterCount = (typeof cachedTheaterStamps !== 'undefined' && cachedTheaterStamps) ? cachedTheaterStamps.length : 0;
                    const pendingCount = (typeof pendingStampGenerations !== 'undefined' && pendingStampGenerations && pendingStampGenerations.size) ? ` · ${pendingStampGenerations.size} generating` : '';
                    stampManagerCount.title = `${count} user stamps · ${theaterCount} theater stamps${pendingCount}`;
                }
                if (cachedUserStamps) {
                    renderStampManagerGrid(cachedUserStamps);
                } else {
                    loadUserStamps();
                }
            },
            onTheaterStampsUpdated: (stamps) => {
                cachedTheaterStamps = stamps;
                if (isStampManagerOpen) renderStampManagerGrid(cachedUserStamps || []);
            },
            expandedPlaylists,
        });

        function updateStampTabsVisibility() {
            assetManager.updateStampTabsVisibility();
        }
        function preloadItemImages(items) {
            assetManager.preloadItemImages(items);
        }
        function handleDoodleSocketData(data) {
            return assetManager.handleDoodleSocketData(data);
        }
        function updateAssetPlaybackSelection(music) {
            assetMusicState = music;
            assetManager.updateAssetPlaybackSelection(music);
        }
        function loadExtraAssets(kind) {
            return assetManager.loadExtraAssets(kind);
        }
        function switchStampManagerTab(tab) {
            assetManager.switchStampManagerTab(tab);
        }
        function loadTheaterScenes() {
            return assetManager.loadTheaterScenes();
        }
        function renderScenesManagerGrid(scenes) {
            assetManager.renderScenesManagerGrid(scenes);
        }
        function pushSceneToCanvas(scene, cardEl) {
            return assetManager.pushSceneToCanvas(scene, cardEl);
        }
        function loadTheaterCharacters() {
            return assetManager.loadTheaterCharacters();
        }
        function renderCharactersManagerGrid(characters) {
            assetManager.renderCharactersManagerGrid(characters);
        }
        function pushCharacterToCanvas(character, cardEl) {
            return assetManager.pushCharacterToCanvas(character, cardEl);
        }

        // ========================================
        // Orator vs Viewer Logic
        // ========================================
        const roleBadge = document.getElementById('role-badge');
        const oratorControls = document.getElementById('orator-controls');
        const agentStartStopBtn = document.getElementById('agent-start-stop-btn');
        const agentConnectBtn = document.getElementById('agent-connect-btn');
        const statusIndicator = document.getElementById('status-indicator');
        const viewerCollabToggleBtn = document.getElementById('viewer-collab-toggle-btn');
        const collabSection = document.getElementById('collab-section');
        const hideSuggestionsBtn = document.getElementById('hide-suggestions-btn');
        let viewerCollabEnabled = false;
        const collabBar = createCollabBarController({
            theaterId,
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
                if (typeof gameRenderer !== 'undefined' && gameRenderer && typeof gameRenderer.render === 'function') {
                    gameRenderer.render();
                }
            },
        });
        updateTextAnnotationAvailability();

        function updateViewerCollabBar(enabled) {
            collabBar.updateBar(enabled);
        }

        function setSuggestionsHidden(hidden, persist = true) {
            suggestionsHidden = Boolean(hidden);
            chatMessages.classList.toggle('suggestions-hidden', suggestionsHidden);
            if (hideSuggestionsBtn) {
                hideSuggestionsBtn.textContent = suggestionsHidden ? '👁' : '🙈';
                hideSuggestionsBtn.setAttribute('aria-label', suggestionsHidden ? 'Show viewer suggestions' : 'Hide viewer suggestions');
                hideSuggestionsBtn.classList.toggle('is-hidden', suggestionsHidden);
                hideSuggestionsBtn.title = suggestionsHidden
                    ? 'Show viewer suggestions in this chat'
                    : 'Hide viewer suggestions from this chat';
            }
            if (persist) localStorage.setItem(suggestionsHiddenStorageKey, String(suggestionsHidden));
        }

        // Viewer Collab Mode toggle
        if (viewerCollabToggleBtn) {
            viewerCollabToggleBtn.addEventListener('click', async () => {
                const requestedState = !viewerCollabEnabled;
                try {
                    const response = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/collab`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ enabled: requestedState })
                    });
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                    const result = await response.json();
                    viewerCollabEnabled = Boolean(result.viewer_collab_enabled);
                    viewerCollabToggleBtn.textContent = viewerCollabEnabled ? '👥 Collab: On' : '👥 Collab: Off';
                    viewerCollabToggleBtn.classList.toggle('active', viewerCollabEnabled);
                    updateTextAnnotationAvailability();
                    updateViewerCollabStatus(viewerCollabEnabled);
                } catch (err) {
                    console.error('Failed to toggle viewer collab mode:', err);
                }
            });
        }

        // State variables
        let agentWs = null;
        let audioRecorderNode = null;
        let audioRecorderContext = null;
        let micStream = null;
        let isRecording = false;
        let isMicAwaiting = false;
        let micCaptureReady = false;
        let micMode = localStorage.getItem('narratron_mic_mode') === 'push-to-talk' ? 'push-to-talk' : 'toggle';
        let micPressHeld = false;
        let micPressSource = null;
        let micRequestId = 0;
        let isAgentStarted = false;
        let agentReconnectTimer = null;

        // Role UI management
        const backBtn = document.getElementById('back-btn');
        const suggestionsToggleBtn = document.getElementById('suggestions-toggle-btn');
        const stickyNotesToggleBtn = document.getElementById('sticky-notes-toggle-btn');
        const gameStateToggleBtn = document.getElementById('game-state-toggle-btn');

        if (hideSuggestionsBtn) {
            hideSuggestionsBtn.addEventListener('click', () => setSuggestionsHidden(!suggestionsHidden));
        }
        setSuggestionsHidden(localStorage.getItem(suggestionsHiddenStorageKey) === 'true', false);

        function applyRoleUI(isOrator) {
            const wasActiveOrator = window._isActiveOratorState;
            window._isActiveOratorState = Boolean(isOrator);
            const isNowOrator = window._isActiveOratorState;
            updateViewerCollabBar(viewerCollabEnabled);
            updateCanvasPinButton();
            updateTextAnnotationAvailability();
            if (typeof updateStampTabsVisibility === 'function') updateStampTabsVisibility();

            if (isNowOrator) {
                const cursorIndex = historyPaging.getOratorCursorIndex();
                if (cursorIndex >= 0 && cursorIndex < historyPaging.getHistory().length) {
                    displayHistoryImage(cursorIndex, 'none');
                }
                updatePagingControlsUI();
                if (backBtn) backBtn.href = '/deploy?from=canvas';
                roleBadge.textContent = 'Orator';
                roleBadge.style.color = '#f97316';
                roleBadge.style.background = 'rgba(249, 115, 22, 0.2)';
                roleBadge.style.borderColor = 'rgba(249, 115, 22, 0.4)';
                if (oratorControls) oratorControls.style.display = 'flex';
                if (doodlesToggleBtn) doodlesToggleBtn.style.display = 'inline-flex';
                if (suggestionsToggleBtn) suggestionsToggleBtn.style.display = 'flex';
                if (stickyNotesToggleBtn) stickyNotesToggleBtn.style.display = 'flex';
                if (collabSection) collabSection.style.display = 'flex';
                if (hideSuggestionsBtn) hideSuggestionsBtn.style.display = 'inline-flex';

                if (!wasActiveOrator) {
                    checkAgentStatus().then(status => {
                        if (status && status.agent_running) {
                            if (!agentWs || agentWs.readyState !== WebSocket.OPEN) {
                                connectAgent().catch(err => console.warn("Auto-connect agent on baton pass failed:", err));
                            }
                        }
                    }).catch(() => { });
                } else if (!agentWs || agentWs.readyState !== WebSocket.OPEN) {
                    statusIndicator.classList.add('disconnected');
                }

                // Show Orator How-To popup automatically on first visit
                if (!localStorage.getItem('narratron_orator_howto_seen')) {
                    setTimeout(() => micConfig.openOratorHowtoModal({ openMicConfigOnClose: true }), 400);
                }
            } else {
                if (backBtn) backBtn.href = '/';
                roleBadge.textContent = 'Viewer';
                roleBadge.style.color = '#a5b4fc';
                roleBadge.style.background = 'rgba(99, 102, 241, 0.2)';
                roleBadge.style.borderColor = 'rgba(99, 102, 241, 0.4)';
                if (oratorControls) oratorControls.style.display = 'none';
                if (doodlesToggleBtn) doodlesToggleBtn.style.display = 'none';
                if (suggestionsToggleBtn) suggestionsToggleBtn.style.display = 'none';
                if (stickyNotesToggleBtn) stickyNotesToggleBtn.style.display = 'none';
                if (collabSection) collabSection.style.display = 'none';
                if (hideSuggestionsBtn) hideSuggestionsBtn.style.display = 'none';

                if (micCaptureReady || isMicAwaiting || micPressHeld) cancelMicCapture();
                statusIndicator.classList.remove('disconnected');
            }

            if (typeof isStampManagerOpen !== 'undefined' && isStampManagerOpen) {
                if (typeof updateStampTabsVisibility === 'function') updateStampTabsVisibility();
                if (typeof activeStampManagerTab === 'undefined' || activeStampManagerTab === 'stamps') {
                    updateStampManagerPermissionBanner();
                    if (cachedUserStamps) renderStampManagerGrid(cachedUserStamps);
                } else if (activeStampManagerTab === 'scenes') {
                    if (cachedTheaterScenes) renderScenesManagerGrid(cachedTheaterScenes);
                } else if (activeStampManagerTab === 'characters') {
                    if (cachedTheaterCharacters) renderCharactersManagerGrid(cachedTheaterCharacters);
                }
            }
        }

        // Initialize as viewer until server state verifies role
        applyRoleUI(false);

        // Theater ownership & orator verification for Orator controls
        const activetheaterId = urlParams.get('theater_id');
        if (activetheaterId) {
            fetch(`/api/theaters/${activetheaterId}`)
                .then(res => res.ok ? res.json() : null)
                .then(data => {
                    if (data && Array.isArray(data.references)) {
                        window.cachedTheaterReferences = data.references;
                    }
                    if (data && data.metadata) {
                        const isOwner = data.metadata.is_owner;
                        isTheaterStampOwner = Boolean(isOwner);
                        updateStampGenerationPermission();
                        const builderLink = document.getElementById('menu-item-theater-builder');
                        if (builderLink && isOwner) {
                            builderLink.href = `/theater-editor?theater_id=${encodeURIComponent(activetheaterId)}`;
                            builderLink.style.display = 'flex';
                        }
                        const isActiveOrator = data.metadata.is_active_orator;
                        const isContributor = data.metadata.is_contributor;
                        window._isContributor = Boolean(isOwner || isActiveOrator || isContributor);
                        if (typeof gameRenderer !== 'undefined' && gameRenderer && typeof gameRenderer.render === 'function') {
                            gameRenderer.render();
                        }
                        if (typeof isStampManagerOpen !== 'undefined' && isStampManagerOpen) {
                            if (typeof updateStampTabsVisibility === 'function') updateStampTabsVisibility();
                            updateStampManagerPermissionBanner();
                            if (cachedUserStamps) renderStampManagerGrid(cachedUserStamps);
                        }
                        if (isActiveOrator) {
                            applyRoleUI(true);
                        } else if (!isOwner && !isContributor) {
                            applyRoleUI(false);
                            const viewerGuideStorageKey = `narratron_viewer_collab_guide_seen:${activetheaterId}`;
                            if (!localStorage.getItem(viewerGuideStorageKey)) {
                                setTimeout(openViewerCollabGuide, 400);
                            }
                        }
                        const isAdventure = Boolean(
                            data.metadata.is_adventure_mode ||
                            (data.metadata.config && data.metadata.config.story_planning && data.metadata.config.story_planning.adventure_mode)
                        );
                        initAdventureGuide(isAdventure, activetheaterId);
                    }
                })
                .catch(err => console.error("Error checking theater ownership:", err));
        }

        // ========================================
        // Baton Passing & Co-Orator Management JS
        // ========================================
        const batonManager = createBatonManager({
            theaterId,
            getCurrentUser: () => currentUser,
            batonStatusBadge: document.getElementById('baton-status-badge'),
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
                if (typeof gameRenderer !== 'undefined' && gameRenderer && typeof gameRenderer.render === 'function') {
                    gameRenderer.render();
                }
                if (typeof isStampManagerOpen !== 'undefined' && isStampManagerOpen) {
                    updateStampManagerPermissionBanner();
                    if (cachedUserStamps) renderStampManagerGrid(cachedUserStamps);
                }
            },
        });

        const checkBatonState = () => batonManager.checkBatonState();
        const updateBatonStateUI = (batonState, activeViewers) => batonManager.updateBatonStateUI(batonState, activeViewers);
        window.addContributor = (targetUserId) => { void batonManager.addContributor(targetUserId); };
        window.removeContributor = (targetUserId) => { void batonManager.removeContributor(targetUserId); };
        window.requestBatonPass = (targetUserId) => { void batonManager.requestBatonPass(targetUserId); };

        // Fetch current user details
        getCanvasAuthState()
            .then(data => {
                if (data.authenticated && data.user) {
                    currentUser = data.user;
                    if (currentBatonState) {
                        updateBatonStateUI(currentBatonState, batonManager.getActiveViewers());
                    }
                    checkBatonState();
                }
            })
            .catch(() => { });



        let toastElem = null;
        let toastTimer = null;

        function showShareToast(message) {
            if (!toastElem) {
                toastElem = document.createElement('div');
                toastElem.className = 'share-toast';
                document.body.appendChild(toastElem);
            }
            toastElem.textContent = message;
            // Force reflow
            void toastElem.offsetWidth;
            toastElem.classList.add('show');

            if (toastTimer) clearTimeout(toastTimer);
            toastTimer = setTimeout(() => {
                toastElem.classList.remove('show');
            }, 2200);
        }

        const shareTheaterBtn = document.getElementById('share-theater-btn');
        if (shareTheaterBtn) {
            shareTheaterBtn.addEventListener('click', async () => {
                if (!theaterId) return;

                try {
                    const response = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}`);
                    const data = await response.json();
                    const metadata = data && data.metadata;
                    if (!response.ok || !metadata) {
                        throw new Error(data.detail || 'Unable to load theater sharing details.');
                    }

                    const joinKey = (metadata.join_key && !metadata.join_key.includes('Owner'))
                        ? metadata.join_key
                        : (new URL(window.location.href)).searchParams.get('join_key');

                    const shareUrl = new URL('/canvas', window.location.origin);
                    shareUrl.searchParams.set('theater_id', theaterId);
                    if (joinKey) {
                        shareUrl.searchParams.set('join_key', joinKey);
                    }

                    if (navigator.clipboard && navigator.clipboard.writeText) {
                        navigator.clipboard.writeText(shareUrl.toString()).then(() => {
                            showShareToast("✨ Share link copied to clipboard!");
                        }).catch(() => {
                            showShareToast("🔗 Link ready to share!");
                        });
                    } else {
                        showShareToast("🔗 Link ready to share!");
                    }
                } catch (err) {
                    console.error('Failed to create theater share link:', err);
                    showShareToast('Unable to create share link.');
                }
            });
        }

        const clipShareBtn = document.getElementById('clip-share-btn');
        const clipShareModal = document.getElementById('clip-share-modal');
        const clipRecordBtn = document.getElementById('clip-record-btn');
        const clipCancelBtn = document.getElementById('clip-cancel-btn');

        function closeClipShareModal() {
            clipShareModal?.classList.remove('show');
            clipShareModal?.setAttribute('aria-hidden', 'true');
        }

        clipShareBtn?.addEventListener('click', () => {
            clipShareModal?.classList.add('show');
            clipShareModal?.setAttribute('aria-hidden', 'false');
        });
        clipCancelBtn?.addEventListener('click', closeClipShareModal);
        clipShareModal?.addEventListener('click', event => {
            if (event.target === clipShareModal) closeClipShareModal();
        });
        clipRecordBtn?.addEventListener('click', async () => {
            closeClipShareModal();
            const clipUrl = new URL('/obs', window.location.origin);
            new URL(window.location.href).searchParams.forEach((value, key) => clipUrl.searchParams.set(key, value));
            clipUrl.searchParams.set('clip', '1');
            const clipWindow = window.open(clipUrl.toString(), 'narratron-tiktok-clip', 'popup=yes,width=960,height=640');
            if (!clipWindow) {
                showShareToast('Allow pop-ups to open the clean clip stage.');
            } else {
                showShareToast('🎬 The clean clip stage is ready in a new window.');
            }
        });

        const obsTheaterBtn = document.getElementById('obs-theater-btn');
        if (obsTheaterBtn) {
            obsTheaterBtn.addEventListener('click', async () => {
                const currentUrl = new URL(window.location.href);
                const obsUrl = new URL('/obs', window.location.origin);
                // Copy all search params (theater_id, join_key, role)
                currentUrl.searchParams.forEach((val, key) => {
                    obsUrl.searchParams.set(key, val);
                });

                if (theaterId && !obsUrl.searchParams.get('theater_id')) {
                    obsUrl.searchParams.set('theater_id', theaterId);
                }

                if (!obsUrl.searchParams.get('join_key') && theaterId) {
                    try {
                        const response = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}`);
                        if (response.ok) {
                            const data = await response.json();
                            const joinKey = data && data.metadata && data.metadata.join_key;
                            if (joinKey && !joinKey.includes('Owner')) {
                                obsUrl.searchParams.set('join_key', joinKey);
                            }
                        }
                    } catch (err) {
                        console.error('Failed to fetch theater join key for OBS link:', err);
                    }
                }

                const obsLink = obsUrl.toString();

                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(obsLink).then(() => {
                        showShareToast("📹 OBS Browser Source link copied!");
                    }).catch(() => {
                        showShareToast("📹 OBS link ready!");
                    });
                } else {
                    showShareToast("📹 OBS link ready!");
                }
            });
        }





        // Audio Recorder Callback with Opus compression (~20-60 bytes per chunk)
        let clientAudioChunkCount = 0;
        let clientAudioBuffers = [];
        let clientAudioTotalBytes = 0;
        let clientAudioLastFlushTime = Date.now();

        function flushClientAudioBuffer() {
            clientAudioBuffers = [];
            clientAudioTotalBytes = 0;
            clientAudioLastFlushTime = Date.now();
        }

        function audioRecorderHandler(opusBuffer) {
            // The recorder owns capture validity. Deliver startup packets and
            // the final encoder flush even before/after the UI recording flag.
            if (!agentWs || agentWs.readyState !== WebSocket.OPEN) {
                return;
            }
            clientAudioChunkCount++;
            if (clientAudioChunkCount % 100 === 1) {
                console.log(`[AudioRecorder] Streaming Opus audio chunk #${clientAudioChunkCount} (${opusBuffer.byteLength} bytes) to Agent WebSocket.`);
            }
            agentWs.send(opusBuffer);
        }

        // ========================================
        // Agent Memory Lifecycle API Helpers
        // ========================================
        function showInsufficientCreditsModal(message) {
            const modal = document.getElementById('insufficient-credits-modal');
            const msgElem = document.getElementById('credits-modal-message');
            if (msgElem && message) {
                msgElem.textContent = message;
            }
            if (modal) {
                modal.classList.add('active');
                modal.setAttribute('aria-hidden', 'false');
            }
        }

        function hideInsufficientCreditsModal() {
            const modal = document.getElementById('insufficient-credits-modal');
            if (modal) {
                modal.classList.remove('active');
                modal.setAttribute('aria-hidden', 'true');
            }
        }

        document.getElementById('credits-modal-deploy-btn')?.addEventListener('click', () => {
            window.location.href = '/deploy?from=canvas';
        });
        document.getElementById('credits-modal-dismiss-btn')?.addEventListener('click', hideInsufficientCreditsModal);
        document.getElementById('credits-modal-close-btn')?.addEventListener('click', hideInsufficientCreditsModal);

        window.showInsufficientCreditsModal = showInsufficientCreditsModal;
        window.hideInsufficientCreditsModal = hideInsufficientCreditsModal;

        function setAgentStartedUI(started) {
            isAgentStarted = started;
            window.isAgentStarted = started;
            if (!started) gameRenderer.close();
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

        function updateMicToggleUI() {
            if (!agentConnectBtn) return;
            agentConnectBtn.textContent = isMicAwaiting
                ? '🎙️ Awaiting'
                : micMode === 'push-to-talk'
                    ? isRecording ? '🎙️ Talking' : '🎙️ Hold to Talk'
                    : isRecording ? '🎙️ Mic Off' : '🎙️ Mic On';
            agentConnectBtn.style.background = isRecording ? '#ef4444' : isMicAwaiting ? '#eab308' : '#3b82f6';
            agentConnectBtn.disabled = isMicAwaiting && micMode === 'toggle';
            agentConnectBtn.setAttribute('aria-pressed', String(isRecording));
            agentConnectBtn.title = micMode === 'push-to-talk' ? 'Hold this button or the mic hotkey to speak' : 'Click or press the mic hotkey to toggle the microphone';
        }

        function ensureAgentConnectionWhenActive() {
            if (!isCurrentOrator() || !isAgentStarted || !isActiveCanvasWindow()) return;
            if (agentWs && (agentWs.readyState === WebSocket.OPEN || agentWs.readyState === WebSocket.CONNECTING)) return;
            connectAgent().catch((err) => {
                console.warn('Automatic agent connection failed:', err);
                if (!agentReconnectTimer && isAgentStarted && isActiveCanvasWindow()) {
                    agentReconnectTimer = window.setTimeout(() => {
                        agentReconnectTimer = null;
                        ensureAgentConnectionWhenActive();
                    }, 3000);
                }
            });
        }

        window.startAgentTheater = startAgentTheater;
        window.stopAgentTheater = stopAgentTheater;


        async function checkAgentStatus() {
            try {
                const res = await fetch(`/api/theaters/${theaterId}/agent/status`);
                if (res.ok) {
                    const data = await res.json();
                    if (data.insufficient_credits) {
                        setAgentStartedUI(false);
                        cleanupAgentConnection();
                    } else {
                        setAgentStartedUI(!!data.agent_running);
                    }
                    ensureAgentConnectionWhenActive();
                    return data;
                }
            } catch (e) {
                console.warn("Failed to fetch agent status:", e);
            }
        }

        async function startAgentTheater() {
            if (agentStartStopBtn) {
                agentStartStopBtn.textContent = 'Starting...';
                agentStartStopBtn.style.background = '#eab308'; // Amber
            }
            try {
                const res = await fetch(`/api/theaters/${theaterId}/agent/start`, { method: 'POST' });
                const data = await res.json();
                if (!res.ok || data.insufficient_credits) {
                    setAgentStartedUI(false);
                    const insufficientCredits = res.status === 402 || data.insufficient_credits === true;
                    const msg = data.detail || (insufficientCredits
                        ? "The theater owner's credit balance is 0 or less. The owner must top up credits on the Deploy page."
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
            } catch (err) {
                console.error("Error starting agent theater:", err);
                setAgentStartedUI(false);
                throw err;
            }
        }

        async function stopAgentTheater() {
            // Prevent the socket close handler from reconnecting while a stop is in flight.
            setAgentStartedUI(false);
            cleanupAgentConnection();
            if (agentStartStopBtn) {
                agentStartStopBtn.textContent = 'Stopping...';
                agentStartStopBtn.style.background = '#eab308'; // Amber
            }
            try {
                await fetch(`/api/theaters/${theaterId}/agent/stop`, { method: 'POST' });
            } catch (e) {
                console.warn("Error stopping agent theater:", e);
            } finally {
                setAgentStartedUI(false);
            }
        }

        if (agentStartStopBtn) {
            agentStartStopBtn.addEventListener('keydown', (e) => {
                if (['Space', ' '].includes(e.code) || ['Space', ' ', 'Spacebar'].includes(e.key)) {
                    e.preventDefault();
                }
            });
            agentStartStopBtn.addEventListener('keyup', (e) => {
                if (['Space', ' '].includes(e.code) || ['Space', ' ', 'Spacebar'].includes(e.key)) {
                    e.preventDefault();
                }
            });
            agentStartStopBtn.addEventListener('click', async () => {
                agentStartStopBtn.blur();
                if (isAgentStarted) {
                    await stopAgentTheater();
                } else {
                    await startAgentTheater();
                }
            });
        }

        // Check agent status when active window gains focus for Orators
        document.addEventListener('visibilitychange', () => {
            if (isActiveCanvasWindow() && isCurrentOrator()) checkAgentStatus();
        });
        window.addEventListener('focus', () => {
            if (isCurrentOrator()) checkAgentStatus();
        });

        // Connect Agent WebSocket
        function connectAgent() {
            if (agentWs && agentWs.readyState === WebSocket.OPEN) {
                return Promise.resolve(agentWs);
            }

            if (agentWs && agentWs.readyState === WebSocket.CONNECTING) {
                return new Promise((resolve, reject) => {
                    const pendingOpen = agentWs.onopen;
                    const pendingError = agentWs.onerror;
                    agentWs.onopen = () => {
                        if (typeof pendingOpen === 'function') pendingOpen();
                        resolve(agentWs);
                    };
                    agentWs.onerror = (err) => {
                        if (typeof pendingError === 'function') pendingError(err);
                        reject(err);
                    };
                });
            }

            const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const agentWsUrl = `${wsProtocol}//${window.location.host}/ws/${encodeURIComponent(theaterId)}/agent`;

            agentWs = new WebSocket(agentWsUrl);
            window.agentWs = agentWs; // Expose globally for testing/automation

            return new Promise((resolve, reject) => {
                agentWs.onopen = () => {
                    console.log("Agent WebSocket connected.");
                    statusIndicator.classList.remove('disconnected'); // Set back to green
                    updateMicToggleUI();
                    resolve(agentWs);
                };

                agentWs.onclose = () => {
                    console.log("Agent WebSocket disconnected.");
                    cleanupAgentConnection();
                    ensureAgentConnectionWhenActive();
                };

                agentWs.onerror = (err) => {
                    console.error("Agent WebSocket error:", err);
                    cleanupAgentConnection();
                    reject(err);
                };

                window.isSetupComplete = false;
                agentWs.onmessage = (e) => {
                    console.log("WS_MSG:" + e.data);
                    try {
                        const data = JSON.parse(e.data);
                        if (data.type === 'insufficient_credits') {
                            cleanupAgentConnection();
                            setAgentStartedUI(false);
                            showInsufficientCreditsModal(data.detail || "Agent stopped because the theater owner's credit balance is 0 or less.");
                        } else if (data.type === 'agent_failed') {
                            updateAgentThought({ text: 'wandering' });
                            cleanupAgentConnection();
                            setAgentStartedUI(false);
                        } else if (data.setupComplete || data.type === 'pong') {
                            window.isSetupComplete = true;
                            if (typeof updateAgentActivity === 'function') {
                                updateAgentActivity({ live_ready: true });
                            }
                        }
                    } catch (err) { }
                };

                if (!window.agentPingInterval) {
                    window.agentPingInterval = setInterval(() => {
                        if (agentWs && agentWs.readyState === WebSocket.OPEN) {
                            try {
                                agentWs.send(JSON.stringify({ type: 'ping', ts: Date.now() }));
                            } catch (e) { }
                        }
                    }, 5000);
                }
            });
        }

        // Disconnect and cleanup
        function cleanupAgentConnection() {
            if (micCaptureReady || isMicAwaiting || micPressHeld) cancelMicCapture();
            if (window.agentPingInterval) {
                clearInterval(window.agentPingInterval);
                window.agentPingInterval = null;
            }
            if (agentReconnectTimer) {
                clearTimeout(agentReconnectTimer);
                agentReconnectTimer = null;
            }
            if (agentWs) {
                try { agentWs.close(); } catch (e) { }
                agentWs = null;
                window.agentWs = null; // Clear globally
                window.isSetupComplete = false;
            }
            if (typeof updateAgentActivity === 'function') {
                updateAgentActivity({ live_ready: false });
            }
            updateMicToggleUI();
            statusIndicator.classList.add('disconnected'); // Set back to red
        }

        // Start mic recording
        async function startMicStream(requestId) {
            if (requestId !== micRequestId) return;
            if (!agentWs || agentWs.readyState !== WebSocket.OPEN) {
                console.log("Agent WebSocket not open yet; connecting before starting microphone capture.");
                await connectAgent();
            }

            if (requestId !== micRequestId) return;
            if (!agentWs || agentWs.readyState !== WebSocket.OPEN) {
                throw new Error("Agent WebSocket is not ready for audio forwarding.");
            }

            await micConfig.refreshMicrophoneDevices(true);
            if (requestId !== micRequestId) return;
            const [node, context, stream, started] = await startAudioRecorderWorklet(
                audioRecorderHandler, micMode !== 'push-to-talk' || micPressHeld
            );
            if (!started) {
                isMicAwaiting = false;
                updateMicToggleUI();
                return;
            }
            audioRecorderNode = node;
            audioRecorderContext = context;
            micStream = stream;
            micCaptureReady = true;
            isRecording = micMode !== 'push-to-talk' || micPressHeld;
            setMicrophoneTransmissionEnabled(isRecording);
            isMicAwaiting = false;
            updateMicToggleUI();
        }

        // Stop mic recording
        function stopMicStream() {
            try { flushClientAudioBuffer(); } catch (e) { }
            // The VAD recorder owns the active speech segment and emits its
            // activity_end when stopped. It currently returns no MediaStream,
            // so this cannot be conditional on micStream being populated.
            try { stopMicrophone(micStream); } catch (e) { }
            if (micStream) {
                micStream = null;
            }
            if (audioRecorderContext) {
                try { audioRecorderContext.close(); } catch (e) { }
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
        async function enableMic() {
            if (micCaptureReady || isMicAwaiting) return;
            const requestId = ++micRequestId;
            try {
                isMicAwaiting = true;
                updateMicToggleUI();
                if (!isAgentStarted) await startAgentTheater();
                if (requestId !== micRequestId) return;
                await connectAgent();
                if (requestId !== micRequestId) return;
                await startMicStream(requestId);
                if (requestId !== micRequestId) return;
            } catch (err) {
                if (requestId !== micRequestId) return;
                isMicAwaiting = false;
                console.error("Failed to enable microphone audio:", err);
                alert(err.message || 'Could not enable microphone audio.');
                updateMicToggleUI();
            }
        }

        function releaseMicPress() {
            micPressHeld = false;
            micPressSource = null;
            if (micMode === 'push-to-talk') {
                if (setMicrophoneTransmissionEnabled) setMicrophoneTransmissionEnabled(false);
                isRecording = false;
                setPcmBroadcasting(false);
                updateMicToggleUI();
            }
        }

        function cancelMicCapture() {
            micPressHeld = false;
            micPressSource = null;
            micRequestId += 1;
            if (micCaptureReady || isMicAwaiting) stopMicStream();
        }

        function beginMicPress(source) {
            if (micMode !== 'push-to-talk' || micPressHeld || !isCurrentOrator()) return;
            stopSceneSpeech();
            micPressHeld = true;
            micPressSource = source;
            if (micCaptureReady) {
                setMicrophoneTransmissionEnabled(true);
                isRecording = true;
                updateMicToggleUI();
            } else {
                enableMic();
            }
        }

        agentConnectBtn.addEventListener('click', () => {
            if (micMode === 'push-to-talk') return;
            if (isRecording) cancelMicCapture();
            else enableMic();
        });
        agentConnectBtn.addEventListener('pointerdown', (event) => {
            if (micMode !== 'push-to-talk' || event.button !== 0) return;
            event.preventDefault();
            agentConnectBtn.setPointerCapture(event.pointerId);
            beginMicPress('pointer');
        });
        agentConnectBtn.addEventListener('pointerup', () => {
            if (micPressSource === 'pointer') releaseMicPress();
        });
        agentConnectBtn.addEventListener('pointercancel', () => {
            if (micPressSource === 'pointer') releaseMicPress();
        });
        window.addEventListener('blur', () => {
            if (micMode === 'push-to-talk' && micPressHeld) releaseMicPress();
        });
        document.addEventListener('visibilitychange', () => {
            if (document.hidden && micMode === 'push-to-talk' && micPressHeld) releaseMicPress();
        });

        // ========================================
        // Configurable Mic Hotkey Logic
        // ========================================
        const micHotkeyBtn = null;
        const hotkeyDisplay = null;
        let isRebindingHotkey = false;

        // Default hotkey: Ctrl + Shift + [
        let currentHotkey = {
            code: 'BracketLeft',
            key: '[',
            altKey: false,
            ctrlKey: true,
            shiftKey: true
        };

        // Load the browser-specific hotkey preference.
        try {
            const saved = localStorage.getItem('narratron_mic_hotkey');
            if (saved) {
                currentHotkey = JSON.parse(saved);
            }
        } catch (e) { }

        // ========================================
        // Configurable Text Hotkey Logic
        // ========================================
        const menuItemTextHotkey = document.getElementById('menu-item-text-hotkey');
        let isRebindingTextHotkey = false;

        // Default text input hotkey: Shift + Space
        let currentTextHotkey = {
            code: 'Space',
            key: ' ',
            altKey: false,
            ctrlKey: false,
            shiftKey: true
        };

        // Load the browser-specific text hotkey preference.
        try {
            const savedTextHotkey = localStorage.getItem('narratron_text_hotkey');
            if (savedTextHotkey) {
                currentTextHotkey = JSON.parse(savedTextHotkey);
            }
        } catch (e) { }

        function formatHotkeyLabel(hk) {
            if (!hk) return '';
            let parts = [];
            if (hk.ctrlKey) parts.push('Ctrl');
            if (hk.altKey) parts.push('Alt');
            if (hk.shiftKey) parts.push('Shift');

            let codeLabel = hk.code || hk.key || '[';
            if (codeLabel.startsWith('Key')) {
                codeLabel = codeLabel.replace('Key', '');
            } else if (codeLabel.startsWith('Digit')) {
                codeLabel = codeLabel.replace('Digit', '');
            } else if (codeLabel === 'BracketLeft') {
                codeLabel = '[';
            } else if (codeLabel === 'BracketRight') {
                codeLabel = ']';
            } else if (codeLabel === 'Backquote') {
                codeLabel = '`';
            } else if (codeLabel === 'Semicolon') {
                codeLabel = ';';
            } else if (codeLabel === 'Quote') {
                codeLabel = "'";
            } else if (codeLabel === 'Comma') {
                codeLabel = ',';
            } else if (codeLabel === 'Period') {
                codeLabel = '.';
            } else if (codeLabel === 'Slash') {
                codeLabel = '/';
            } else if (codeLabel === 'Backslash') {
                codeLabel = '\\';
            } else if (codeLabel === 'Minus') {
                codeLabel = '-';
            } else if (codeLabel === 'Equal') {
                codeLabel = '=';
            } else if (codeLabel === 'Space') {
                codeLabel = 'Space';
            }
            if (codeLabel !== 'Space') {
                codeLabel = codeLabel.toUpperCase();
            }
            parts.push(codeLabel);
            return parts.join(' + ');
        }

        function updateHotkeyUI() {
            const label = `Mic Hotkey: ${formatHotkeyLabel(currentHotkey)}`;
            if (hotkeyDisplay) {
                hotkeyDisplay.textContent = label;
            }
            const menuHotkeyDisplay = document.getElementById('menu-hotkey-display');
            if (menuHotkeyDisplay) {
                menuHotkeyDisplay.textContent = label;
            }
            const howtoMicDisplay = document.getElementById('howto-mic-hotkey-display');
            if (howtoMicDisplay) {
                howtoMicDisplay.textContent = formatHotkeyLabel(currentHotkey);
            }
        }

        function updateTextHotkeyUI() {
            const label = `Text Hotkey: ${formatHotkeyLabel(currentTextHotkey)}`;
            const menuTextHotkeyDisplay = document.getElementById('menu-text-hotkey-display');
            if (menuTextHotkeyDisplay) {
                menuTextHotkeyDisplay.textContent = label;
            }
            const howtoTextDisplay = document.getElementById('howto-text-hotkey-display');
            if (howtoTextDisplay) {
                howtoTextDisplay.textContent = formatHotkeyLabel(currentTextHotkey);
            }
            const dismissHotkeyDisplay = document.getElementById('orator-command-dismiss-hotkey');
            if (dismissHotkeyDisplay) {
                dismissHotkeyDisplay.textContent = formatHotkeyLabel(currentTextHotkey);
            }
        }

        updateHotkeyUI();
        updateTextHotkeyUI();

        // Rebind hotkey click handler
        const menuItemHotkey = document.getElementById('menu-item-hotkey');
        function startHotkeyRebind(btnElem, labelElem) {
            actionWheel.cancelRebinding();
            if (isRebindingHotkey) return;
            if (isRebindingTextHotkey) {
                isRebindingTextHotkey = false;
                if (menuItemTextHotkey) {
                    menuItemTextHotkey.style.background = '';
                    menuItemTextHotkey.style.color = '';
                }
                updateTextHotkeyUI();
            }
            isRebindingHotkey = true;
            if (btnElem) {
                btnElem.style.background = 'rgba(234, 179, 8, 0.25)';
                btnElem.style.color = '#fef08a';
            }
            if (labelElem) {
                labelElem.textContent = 'Press key combo to rebind...';
            }
        }

        function startTextHotkeyRebind(btnElem, labelElem) {
            actionWheel.cancelRebinding();
            if (isRebindingTextHotkey) return;
            if (isRebindingHotkey) {
                isRebindingHotkey = false;
                if (menuItemHotkey) {
                    menuItemHotkey.style.background = '';
                    menuItemHotkey.style.color = '';
                }
                updateHotkeyUI();
            }
            isRebindingTextHotkey = true;
            if (btnElem) {
                btnElem.style.background = 'rgba(234, 179, 8, 0.25)';
                btnElem.style.color = '#fef08a';
            }
            if (labelElem) {
                labelElem.textContent = 'Press key combo to rebind...';
            }
        }

        if (menuItemHotkey) {
            menuItemHotkey.addEventListener('click', (e) => {
                e.stopPropagation();
                const menuHotkeyDisplay = document.getElementById('menu-hotkey-display');
                startHotkeyRebind(menuItemHotkey, menuHotkeyDisplay);
            });
        }
        if (menuItemTextHotkey) {
            menuItemTextHotkey.addEventListener('click', (e) => {
                e.stopPropagation();
                const menuTextHotkeyDisplay = document.getElementById('menu-text-hotkey-display');
                startTextHotkeyRebind(menuItemTextHotkey, menuTextHotkeyDisplay);
            });
        }
        if (micHotkeyBtn) {
            micHotkeyBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                startHotkeyRebind(micHotkeyBtn, hotkeyDisplay);
            });
        }

        // Global Keydown Listener for Hotkey
        window.addEventListener('keydown', (e) => {
            if (!isCurrentOrator()) return;
            if (document.activeElement === agentStartStopBtn && (['Space', ' '].includes(e.code) || ['Space', ' ', 'Spacebar'].includes(e.key))) {
                e.preventDefault();
                return;
            }
            if (micMode === 'push-to-talk' && document.activeElement === agentConnectBtn && ['Space', 'Enter'].includes(e.code)) {
                e.preventDefault();
                if (!e.repeat) beginMicPress('button-key');
                return;
            }

            // Rebinding text hotkey state
            if (isRebindingTextHotkey) {
                e.preventDefault();
                e.stopPropagation();

                // If user presses Escape, cancel rebinding
                if (e.code === 'Escape') {
                    isRebindingTextHotkey = false;
                    if (menuItemTextHotkey) {
                        menuItemTextHotkey.style.background = '';
                        menuItemTextHotkey.style.color = '';
                    }
                    updateTextHotkeyUI();
                    return;
                }

                // Ignore standalone modifier keypresses
                if (['ControlLeft', 'ControlRight', 'AltLeft', 'AltRight', 'ShiftLeft', 'ShiftRight', 'MetaLeft', 'MetaRight'].includes(e.code)) {
                    return;
                }

                currentTextHotkey = {
                    code: e.code,
                    key: e.key,
                    altKey: e.altKey,
                    ctrlKey: e.ctrlKey,
                    shiftKey: e.shiftKey
                };

                try {
                    localStorage.setItem('narratron_text_hotkey', JSON.stringify(currentTextHotkey));
                } catch (err) { }

                isRebindingTextHotkey = false;
                if (menuItemTextHotkey) {
                    menuItemTextHotkey.style.background = 'rgba(16, 185, 129, 0.25)';
                    menuItemTextHotkey.style.color = '#6ee7b7';
                }
                updateTextHotkeyUI();

                setTimeout(() => {
                    if (menuItemTextHotkey) {
                        menuItemTextHotkey.style.background = '';
                        menuItemTextHotkey.style.color = '';
                    }
                }, 600);
                return;
            }

            // Rebinding hotkey state
            if (isRebindingHotkey) {
                e.preventDefault();
                e.stopPropagation();

                // If user presses Escape, cancel rebinding
                if (e.code === 'Escape') {
                    isRebindingHotkey = false;
                    if (micHotkeyBtn) {
                        micHotkeyBtn.style.background = 'rgba(255,255,255,0.08)';
                        micHotkeyBtn.style.color = '#a5b4fc';
                    }
                    if (menuItemHotkey) {
                        menuItemHotkey.style.background = '';
                        menuItemHotkey.style.color = '';
                    }
                    updateHotkeyUI();
                    return;
                }

                // Ignore standalone modifier keypresses
                if (['ControlLeft', 'ControlRight', 'AltLeft', 'AltRight', 'ShiftLeft', 'ShiftRight', 'MetaLeft', 'MetaRight'].includes(e.code)) {
                    return;
                }

                currentHotkey = {
                    code: e.code,
                    key: e.key,
                    altKey: e.altKey,
                    ctrlKey: e.ctrlKey,
                    shiftKey: e.shiftKey
                };

                try {
                    localStorage.setItem('narratron_mic_hotkey', JSON.stringify(currentHotkey));
                } catch (err) { }

                isRebindingHotkey = false;
                if (micHotkeyBtn) {
                    micHotkeyBtn.style.background = '#10b981';
                    micHotkeyBtn.style.color = '#fff';
                }
                if (menuItemHotkey) {
                    menuItemHotkey.style.background = 'rgba(16, 185, 129, 0.25)';
                    menuItemHotkey.style.color = '#6ee7b7';
                }
                updateHotkeyUI();

                setTimeout(() => {
                    if (micHotkeyBtn) {
                        micHotkeyBtn.style.background = 'rgba(255,255,255,0.08)';
                        micHotkeyBtn.style.color = '#a5b4fc';
                    }
                    if (menuItemHotkey) {
                        menuItemHotkey.style.background = '';
                        menuItemHotkey.style.color = '';
                    }
                }, 600);
                return;
            }

            // Normal hotkey listening for toggling mic or text command
            if (e.repeat) return;

            // Match configurable text input hotkey
            const matchTextCode = e.code === currentTextHotkey.code;
            const matchTextAlt = !!e.altKey === !!currentTextHotkey.altKey;
            const matchTextCtrl = !!e.ctrlKey === !!currentTextHotkey.ctrlKey;
            const matchTextShift = !!e.shiftKey === !!currentTextHotkey.shiftKey;

            if (matchTextCode && matchTextAlt && matchTextCtrl && matchTextShift) {
                e.preventDefault();
                if (isOratorCommandOpen()) {
                    closeOratorCommand();
                } else {
                    const activeEl = document.activeElement;
                    if (activeEl && (['INPUT', 'TEXTAREA', 'SELECT'].includes(activeEl.tagName) || activeEl.isContentEditable)) {
                        return;
                    }
                    openOratorCommand();
                }
                return;
            }

            // Do not trigger other hotkeys if orator command is currently open
            if (isOratorCommandOpen()) return;

            // Do not trigger hotkey if typing in input or textarea
            const activeEl = document.activeElement;
            if (activeEl && (['INPUT', 'TEXTAREA', 'SELECT'].includes(activeEl.tagName) || activeEl.isContentEditable)) {
                return;
            }

            // Match current hotkey
            const matchCode = e.code === currentHotkey.code;
            const matchAlt = !!e.altKey === !!currentHotkey.altKey;
            const matchCtrl = !!e.ctrlKey === !!currentHotkey.ctrlKey;
            const matchShift = !!e.shiftKey === !!currentHotkey.shiftKey;

            if (matchCode && matchAlt && matchCtrl && matchShift) {
                e.preventDefault();

                if (micMode === 'push-to-talk') beginMicPress('hotkey');
                else if (agentConnectBtn && !agentConnectBtn.disabled) agentConnectBtn.click();
            }
        });
        window.addEventListener('keyup', (e) => {
            if (document.activeElement === agentStartStopBtn && (['Space', ' '].includes(e.code) || ['Space', ' ', 'Spacebar'].includes(e.key))) {
                e.preventDefault();
                return;
            }
            if (micPressSource === 'button-key' && ['Space', 'Enter'].includes(e.code)) {
                e.preventDefault();
                releaseMicPress();
            } else if (micPressSource === 'hotkey' && (
                e.code === currentHotkey.code
                || !!e.altKey !== !!currentHotkey.altKey
                || !!e.ctrlKey !== !!currentHotkey.ctrlKey
                || !!e.shiftKey !== !!currentHotkey.shiftKey
            )) {
                releaseMicPress();
            }
        });

        window.setMicHotkey = (code) => {
            currentHotkey = { code: code, key: code, altKey: false, ctrlKey: false, shiftKey: false };
            try { localStorage.setItem('narratron_mic_hotkey', JSON.stringify(currentHotkey)); } catch (e) { }
            updateHotkeyUI();
        };

        window.setTextHotkey = (hk) => {
            if (typeof hk === 'string') {
                currentTextHotkey = { code: hk, key: hk, altKey: false, ctrlKey: false, shiftKey: false };
            } else if (hk && typeof hk === 'object') {
                currentTextHotkey = {
                    code: hk.code || 'Space',
                    key: hk.key || ' ',
                    altKey: !!hk.altKey,
                    ctrlKey: !!hk.ctrlKey,
                    shiftKey: !!hk.shiftKey
                };
            }
            try { localStorage.setItem('narratron_text_hotkey', JSON.stringify(currentTextHotkey)); } catch (e) { }
            updateTextHotkeyUI();
        };
        window.setTextCommandHotkey = window.setTextHotkey;

        // Expose helper functions on window for automated evaluation
        window.enableAudioInput = () => {
            isRecording = true;
            if (agentConnectBtn) {
                agentConnectBtn.textContent = '🎙️ Mic Off';
                agentConnectBtn.style.background = '#ef4444';
            }
        };

        window.connectAgentAndMic = async () => {
            if (!isAgentStarted) {
                try {
                    await startAgentTheater();
                } catch (e) {
                    console.error("Failed to start agent theater before connecting:", e);
                }
            }
            connectAgent();
            const checkConnect = setInterval(() => {
                if (agentWs && agentWs.readyState === WebSocket.OPEN) {
                    window.enableAudioInput();
                    clearInterval(checkConnect);
                }
            }, 100);
        };

        window.sendOratorAudio = (base64Data) => {
            const ws = window.agentWs || agentWs;
            if (ws && ws.readyState === WebSocket.OPEN) {
                const binary = atob(base64Data);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) {
                    bytes[i] = binary.charCodeAt(i);
                }
                ws.send(bytes.buffer);
            } else {
                console.warn("[OratorAudio] WebSocket not ready. State:", ws ? ws.readyState : "null");
            }
        };

        window.streamFullAudio = async (fullBase64Data, chunkSize = 2048, delayMs = 64) => {
            const binary = atob(fullBase64Data);
            const totalBytes = binary.length;
            let offset = 0;
            const ws = window.agentWs || agentWs;

            return new Promise((resolve) => {
                const interval = setInterval(() => {
                    if (offset >= totalBytes) {
                        clearInterval(interval);
                        resolve();
                        return;
                    }
                    const end = Math.min(offset + chunkSize, totalBytes);
                    const chunk = new Uint8Array(end - offset);
                    for (let i = offset; i < end; i++) {
                        chunk[i - offset] = binary.charCodeAt(i);
                    }
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(chunk.buffer);
                    }
                    offset = end;
                }, delayMs);
            });
        };

        // ========================================
        // Full Screen & Pop-out Panel Controls
        // ========================================
        const fullscreenBtn = document.getElementById('fullscreen-btn');
        const fullscreenIcon = document.getElementById('fullscreen-icon');

        function isFullscreenActive() {
            return !!(document.fullscreenElement || document.webkitFullscreenElement || document.mozFullScreenElement || document.msFullscreenElement);
        }

        let mouseInactivityTimer = null;

        function showCinematicUI() {
            if (!document.body.classList.contains('fullscreen-cinematic')) return;
            document.body.classList.add('ui-visible');
            if (mouseInactivityTimer) clearTimeout(mouseInactivityTimer);
            mouseInactivityTimer = setTimeout(() => {
                document.body.classList.remove('ui-visible');
            }, 2500);
        }

        function handleCinematicMouseMove() {
            showCinematicUI();
        }

        function updateFullscreenUI() {
            const active = isFullscreenActive();
            if (fullscreenBtn) {
                fullscreenBtn.title = active ? "Exit Full Screen (Esc)" : "Full Screen (F11)";
            }
            if (fullscreenIcon) {
                if (active) {
                    fullscreenIcon.innerHTML = `<path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3"/>`;
                } else {
                    fullscreenIcon.innerHTML = `<path d="M8 3H5a2 2 0 0 1-2 2v3m18 0V5a2 2 0 0 1-2-2h-3m0 18h3a2 2 0 0 1 2-2v-3M3 16v3a2 2 0 0 1 2 2h3"/>`;
                }
            }

            if (active) {
                if (!document.body.classList.contains('fullscreen-cinematic')) {
                    setPanelCollapsed(true);
                }
                document.body.classList.add('fullscreen-cinematic');
                window.addEventListener('mousemove', handleCinematicMouseMove);
                showCinematicUI();
            } else {
                document.body.classList.remove('fullscreen-cinematic', 'ui-visible');
                window.removeEventListener('mousemove', handleCinematicMouseMove);
                if (mouseInactivityTimer) {
                    clearTimeout(mouseInactivityTimer);
                    mouseInactivityTimer = null;
                }
            }

            setTimeout(() => {
                resizeRendererCanvas();
                resizeDoodleCanvas();
            }, 100);
        }

        function toggleFullScreen() {
            if (!isFullscreenActive()) {
                const docEl = document.documentElement;
                if (docEl.requestFullscreen) docEl.requestFullscreen();
                else if (docEl.webkitRequestFullscreen) docEl.webkitRequestFullscreen();
                else if (docEl.mozRequestFullScreen) docEl.mozRequestFullScreen();
                else if (docEl.msRequestFullscreen) docEl.msRequestFullscreen();
            } else {
                if (document.exitFullscreen) document.exitFullscreen();
                else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
                else if (document.mozCancelFullScreen) document.mozCancelFullScreen();
                else if (document.msExitFullscreen) document.msExitFullscreen();
            }
        }

        if (fullscreenBtn) {
            fullscreenBtn.addEventListener('click', toggleFullScreen);
        }

        ['fullscreenchange', 'webkitfullscreenchange', 'mozfullscreenchange', 'MSFullscreenChange'].forEach(evt => {
            document.addEventListener(evt, updateFullscreenUI);
        });

        // Panel drawer collapse/expand and resize settle cover
        const layoutResize = createLayoutResizeController({
            container: document.getElementById('image-container'),
            sidebar: document.getElementById('chat-sidebar'),
            expandButton: document.getElementById('popout-expand-btn'),
            sideToggleButton: document.getElementById('chat-side-toggle-btn'),
            onSettle: () => {
                resizeRendererCanvas();
                resizeDoodleCanvas();
            },
            isFullscreenActive,
            onCinematicShow: showCinematicUI,
        });

        function setPanelCollapsed(collapsed, manual = false) {
            layoutResize.setPanelCollapsed(collapsed, manual);
        }

        window.toggleFullScreen = toggleFullScreen;
        window.togglePopoutPanel = () => layoutResize.togglePanel();

        // ========================================
        // Pullout Drawer Menu Logic
        // ========================================
        const pulloutOverlay = document.getElementById('pullout-overlay');
        const menuLeftToggleBtn = document.getElementById('menu-left-toggle-btn');
        const pulloutCloseBtn = document.getElementById('pullout-close-btn');
        const menuItemHowto = document.getElementById('menu-item-howto');
        const menuItemShare = document.getElementById('menu-item-share');
        const menuItemClip = document.getElementById('menu-item-clip');
        const menuItemObs = document.getElementById('menu-item-obs');

        function openPulloutMenu() {
            if (!pulloutOverlay) return;
            pulloutOverlay.classList.add('active');
            pulloutOverlay.setAttribute('aria-hidden', 'false');
        }

        function closePulloutMenu() {
            if (!pulloutOverlay) return;
            pulloutOverlay.classList.remove('active');
            pulloutOverlay.setAttribute('aria-hidden', 'true');
        }

        if (menuLeftToggleBtn) menuLeftToggleBtn.addEventListener('click', openPulloutMenu);
        if (pulloutCloseBtn) pulloutCloseBtn.addEventListener('click', closePulloutMenu);

        if (pulloutOverlay) {
            pulloutOverlay.addEventListener('click', (e) => {
                if (e.target === pulloutOverlay) {
                    closePulloutMenu();
                }
            });
        }

        if (menuItemHowto) {
            menuItemHowto.addEventListener('click', () => {
                closePulloutMenu();
                micConfig.openOratorHowtoModal();
            });
        }

        if (menuItemShare) {
            menuItemShare.addEventListener('click', () => {
                closePulloutMenu();
                const shareBtn = document.getElementById('share-theater-btn');
                if (shareBtn) shareBtn.click();
            });
        }

        if (menuItemClip) {
            menuItemClip.addEventListener('click', () => {
                closePulloutMenu();
                clipShareBtn?.click();
            });
        }

        if (menuItemObs) {
            menuItemObs.addEventListener('click', () => {
                closePulloutMenu();
                const obsBtn = document.getElementById('obs-theater-btn');
                if (obsBtn) obsBtn.click();
            });
        }

        // Microphone settings, PCM test, sensitivity, and Orator How-To modals
        const micConfig = createMicConfigController({
            micModeToggle: document.getElementById('menu-mic-mode-toggle'),
            micDeviceSelect: document.getElementById('menu-mic-device-select'),
            micDeviceRefresh: document.getElementById('menu-mic-device-refresh'),
            micDeviceHint: document.getElementById('menu-mic-device-hint'),
            micTestRecordBtn: document.getElementById('menu-mic-test-record'),
            micTestPlayBtn: document.getElementById('menu-mic-test-play'),
            micTestStatus: document.getElementById('menu-mic-test-status'),
            micSensSlider: document.getElementById('menu-mic-sensitivity-slider'),
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
            isRecording: () => isRecording,
            cancelMicCapture,
            updateMicToggleUI,
            getListenForSpeech: () => listenForSpeech,
            getAuthState: () => getCanvasAuthState(),
            invalidateAuthStateCache: () => {
                canvasAuthStatePromise = null;
            },
            onOpenMicConfig: () => {
                closePulloutMenu();
            },
            onCloseMicConfig: () => {
                actionWheel.cancelRebinding();
                if (isRebindingHotkey) {
                    isRebindingHotkey = false;
                    if (menuItemHotkey) {
                        menuItemHotkey.style.background = '';
                        menuItemHotkey.style.color = '';
                    }
                    updateHotkeyUI();
                }
                if (isRebindingTextHotkey) {
                    isRebindingTextHotkey = false;
                    if (menuItemTextHotkey) {
                        menuItemTextHotkey.style.background = '';
                        menuItemTextHotkey.style.color = '';
                    }
                    updateTextHotkeyUI();
                }
            },
        });

        // ========================================
        // Viewer Collaboration Guide Modal Logic
        // ========================================
        function updateViewerCollabStatus(enabled) {
            collabBar.updateStatus(enabled);
        }

        function openViewerCollabGuide() {
            collabBar.openGuide();
        }

        function closeViewerCollabGuide() {
            collabBar.closeGuide();
        }

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                if (isOratorCommandOpen()) {
                    closeOratorCommand();
                } else if (pulloutOverlay && pulloutOverlay.classList.contains('active')) {
                    closePulloutMenu();
                } else if (micConfig.isMicConfigOpen()) {
                    micConfig.closeMicConfigModal();
                } else if (micConfig.isOratorHowtoOpen()) {
                    micConfig.closeOratorHowtoModal();
                } else if (collabBar.isGuideOpen()) {
                    closeViewerCollabGuide();
                } else if (adventureGuideModal && adventureGuideModal.classList.contains('active')) {
                    closeAdventureGuideModal();
                } else if (theaterConfigModal && theaterConfigModal.classList.contains('active')) {
                    closeTheaterConfigModal();
                }
            }
        });

        // ========================================
        // Adventure Mode Guide Modal Logic
        // ========================================
        const adventureGuideModal = document.getElementById('adventure-guide-modal');
        const menuItemAdventureGuide = document.getElementById('menu-item-adventure-guide');
        const adventureGuideCloseBtn = document.getElementById('adventure-guide-close-btn');
        const adventureGuideAckBtn = document.getElementById('adventure-guide-ack-btn');

        function openAdventureGuideModal() {
            if (!adventureGuideModal) return;
            closePulloutMenu();
            adventureGuideModal.classList.add('active');
            adventureGuideModal.setAttribute('aria-hidden', 'false');
        }

        function closeAdventureGuideModal() {
            if (!adventureGuideModal) return;
            adventureGuideModal.classList.remove('active');
            adventureGuideModal.setAttribute('aria-hidden', 'true');
            const tid = theaterId || 'default';
            localStorage.setItem('narratron_adventure_guide_seen', 'true');
            localStorage.setItem(`narratron_adventure_guide_seen:${tid}`, 'true');
        }

        function initAdventureGuide(isAdventureMode, currentTheaterId) {
            if (menuItemAdventureGuide) {
                menuItemAdventureGuide.style.display = isAdventureMode ? 'flex' : 'none';
            }

            if (!isAdventureMode) return;

            const tid = currentTheaterId || theaterId || 'default';
            const globalSeenKey = 'narratron_adventure_guide_seen';
            const theaterSeenKey = `narratron_adventure_guide_seen:${tid}`;

            const hasSeen = localStorage.getItem(globalSeenKey) === 'true' || localStorage.getItem(theaterSeenKey) === 'true';

            if (!hasSeen) {
                // First time in adventure mode: show modal
                setTimeout(openAdventureGuideModal, 450);
            }
        }

        if (menuItemAdventureGuide) menuItemAdventureGuide.addEventListener('click', openAdventureGuideModal);
        if (adventureGuideCloseBtn) adventureGuideCloseBtn.addEventListener('click', closeAdventureGuideModal);
        if (adventureGuideAckBtn) adventureGuideAckBtn.addEventListener('click', closeAdventureGuideModal);
        if (adventureGuideModal) {
            adventureGuideModal.addEventListener('click', (e) => {
                if (e.target === adventureGuideModal) closeAdventureGuideModal();
            });
        }

        window.openAdventureGuideModal = openAdventureGuideModal;
        window.closeAdventureGuideModal = closeAdventureGuideModal;
        window.initAdventureGuide = initAdventureGuide;

        // ========================================
        // Theater Config Modal Logic
        // ========================================
        const theaterConfigModal = document.getElementById('theater-config-modal');
        const menuItemTheaterConfig = document.getElementById('menu-item-theater-config');
        const configModalCloseBtn = document.getElementById('config-modal-close-btn');
        const configModalCancelBtn = document.getElementById('config-modal-cancel-btn');
        const configModalFormatBtn = document.getElementById('config-modal-format-btn');
        const configModalSaveBtn = document.getElementById('config-modal-save-btn');
        const theaterConfigEditor = document.getElementById('theater-config-editor');
        const theaterConfigStatus = document.getElementById('theater-config-status');
        async function openTheaterConfigModal() {
            if (!theaterConfigModal) return;
            closePulloutMenu();
            theaterConfigModal.classList.add('active');
            theaterConfigModal.setAttribute('aria-hidden', 'false');
            if (theaterConfigStatus) {
                theaterConfigStatus.style.display = 'none';
                theaterConfigStatus.textContent = '';
            }
            if (theaterConfigEditor) {
                theaterConfigEditor.value = 'Loading theater.yaml...';
                theaterConfigEditor.disabled = true;
                theaterConfigEditor.style.borderColor = 'rgba(255, 255, 255, 0.15)';
            }

            try {
                const res = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/config`);
                if (!res.ok) {
                    throw new Error(`HTTP ${res.status}: Failed to load theater.yaml`);
                }
                const data = await res.json();
                if (theaterConfigEditor) {
                    theaterConfigEditor.value = data.config_yaml || '';
                    theaterConfigEditor.disabled = false;
                }
            } catch (err) {
                if (theaterConfigStatus) {
                    theaterConfigStatus.style.display = 'block';
                    theaterConfigStatus.style.background = 'rgba(239, 68, 68, 0.15)';
                    theaterConfigStatus.style.border = '1px solid rgba(239, 68, 68, 0.4)';
                    theaterConfigStatus.style.color = '#f87171';
                    theaterConfigStatus.textContent = err.message || 'Error loading theater.yaml';
                }
                if (theaterConfigEditor) {
                    theaterConfigEditor.disabled = false;
                }
            }
        }

        function closeTheaterConfigModal() {
            if (!theaterConfigModal) return;
            theaterConfigModal.classList.remove('active');
            theaterConfigModal.setAttribute('aria-hidden', 'true');
        }

        async function formatTheaterConfig(quiet = false) {
            if (!theaterConfigEditor) return false;
            const yamlText = theaterConfigEditor.value;

            try {
                const res = await fetch('/api/theaters/format-yaml', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ config_yaml: yamlText }),
                });
                const data = await res.json();

                if (!res.ok) {
                    throw new Error(data.detail || 'Invalid YAML syntax');
                }

                if (data.formatted_yaml !== undefined) {
                    theaterConfigEditor.value = data.formatted_yaml;
                }
                theaterConfigEditor.style.borderColor = 'rgba(34, 197, 94, 0.5)';

                if (theaterConfigStatus) {
                    if (!quiet) {
                        theaterConfigStatus.style.display = 'block';
                        theaterConfigStatus.style.background = 'rgba(34, 197, 94, 0.15)';
                        theaterConfigStatus.style.border = '1px solid rgba(34, 197, 94, 0.4)';
                        theaterConfigStatus.style.color = '#4ade80';
                        theaterConfigStatus.textContent = '✨ YAML formatted & syntax validated successfully!';
                    } else {
                        theaterConfigStatus.style.display = 'none';
                    }
                }
                return true;
            } catch (err) {
                theaterConfigEditor.style.borderColor = 'rgba(239, 68, 68, 0.6)';
                if (theaterConfigStatus) {
                    theaterConfigStatus.style.display = 'block';
                    theaterConfigStatus.style.background = 'rgba(239, 68, 68, 0.15)';
                    theaterConfigStatus.style.border = '1px solid rgba(239, 68, 68, 0.4)';
                    theaterConfigStatus.style.color = '#f87171';
                    theaterConfigStatus.textContent = '❌ ' + (err.message || 'YAML Syntax Error');
                }
                return false;
            }
        }

        async function saveTheaterConfig() {
            if (!theaterConfigEditor || !configModalSaveBtn) return;

            // Pre-save validation & formatting check from within the editor
            const isValid = await formatTheaterConfig(true);
            if (!isValid) {
                return; // Abort save if YAML formatting/syntax check fails
            }

            const yamlText = theaterConfigEditor.value;

            if (theaterConfigStatus) {
                theaterConfigStatus.style.display = 'block';
                theaterConfigStatus.style.background = 'rgba(99, 102, 241, 0.15)';
                theaterConfigStatus.style.border = '1px solid rgba(99, 102, 241, 0.4)';
                theaterConfigStatus.style.color = '#a5b4fc';
                theaterConfigStatus.textContent = 'Saving theater.yaml directly to current theater...';
            }

            configModalSaveBtn.disabled = true;

            try {
                const res = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/config`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ config_yaml: yamlText }),
                });

                const data = await res.json();
                if (!res.ok) {
                    throw new Error(data.detail || 'Failed to save theater.yaml');
                }

                if (theaterConfigStatus) {
                    theaterConfigStatus.style.display = 'block';
                    theaterConfigStatus.style.background = 'rgba(34, 197, 94, 0.15)';
                    theaterConfigStatus.style.border = '1px solid rgba(34, 197, 94, 0.4)';
                    theaterConfigStatus.style.color = '#4ade80';
                    theaterConfigStatus.textContent = '✅ ' + (data.message || 'Saved directly to current theater. Restart to apply configs, and ensure you save the theater state to keep these settings.');
                }
            } catch (err) {
                if (theaterConfigStatus) {
                    theaterConfigStatus.style.display = 'block';
                    theaterConfigStatus.style.background = 'rgba(239, 68, 68, 0.15)';
                    theaterConfigStatus.style.border = '1px solid rgba(239, 68, 68, 0.4)';
                    theaterConfigStatus.style.color = '#f87171';
                    theaterConfigStatus.textContent = '❌ ' + (err.message || 'Error saving configuration');
                }
            } finally {
                configModalSaveBtn.disabled = false;
            }
        }


        if (menuItemTheaterConfig) menuItemTheaterConfig.addEventListener('click', openTheaterConfigModal);
        if (configModalCloseBtn) configModalCloseBtn.addEventListener('click', closeTheaterConfigModal);
        if (configModalCancelBtn) configModalCancelBtn.addEventListener('click', closeTheaterConfigModal);
        if (configModalFormatBtn) configModalFormatBtn.addEventListener('click', () => formatTheaterConfig(false));
        if (configModalSaveBtn) configModalSaveBtn.addEventListener('click', saveTheaterConfig);

        if (theaterConfigModal) {
            theaterConfigModal.addEventListener('click', (e) => {
                if (e.target === theaterConfigModal) {
                    closeTheaterConfigModal();
                }
            });
        }

        window.openPulloutMenu = openPulloutMenu;
        window.closePulloutMenu = closePulloutMenu;
        window.openOratorHowtoModal = micConfig.openOratorHowtoModal;
        window.closeOratorHowtoModal = micConfig.closeOratorHowtoModal;
        window.openMicConfigModal = micConfig.openMicConfigModal;
        window.closeMicConfigModal = micConfig.closeMicConfigModal;
        window.openTheaterConfigModal = openTheaterConfigModal;
        window.closeTheaterConfigModal = closeTheaterConfigModal;
        setupTheaterReport(theaterId, closePulloutMenu);
