
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

        function updateStampManagerProfileLinks(user) {
            const profileLink = document.getElementById('stamp-manager-profile-link');
            const emptyProfileBtn = document.getElementById('stamp-manager-empty-profile-btn');
            const targetUrl = (user && user.username) ? `/users/${encodeURIComponent(user.username)}` : '/profile';
            if (profileLink) {
                profileLink.href = targetUrl;
                profileLink.style.display = user && user.username ? 'inline-flex' : 'none';
            }
            if (emptyProfileBtn) emptyProfileBtn.href = targetUrl;
        }

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

        // Scene Suggestions Hover & Element Fingerprint Caching Logic
        (function initSuggestionsHandler() {
            const btn = document.getElementById('suggestions-toggle-btn');
            const list = document.getElementById('suggestions-list');
            const loading = document.getElementById('suggestions-loading');
            if (!btn || !list || !loading) return;

            let cachedSuggestions = null;
            let cachedFingerprint = null;
            let isFetching = false;

            function escapeHtml(str) {
                return String(str)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;');
            }

            function renderSuggestions(items) {
                if (!items || items.length === 0) {
                    list.innerHTML = '<div style="font-size: 0.78rem; color: #94a3b8;">No suggestions available right now.</div>';
                    return;
                }
                list.innerHTML = items.map(item => `
                    <div class="suggestion-card">
                        <div class="suggestion-card-header">
                            <span class="suggestion-card-title">${escapeHtml(item.title || '')}</span>
                            <span class="suggestion-card-badge">${escapeHtml(item.category || 'Idea')}</span>
                        </div>
                        <div class="suggestion-card-desc">${escapeHtml(item.description || '')}</div>
                    </div>
                `).join('');
            }

            async function triggerSuggestionsFetch() {
                if (isFetching) return;
                isFetching = true;

                if (!cachedSuggestions) {
                    loading.style.display = 'flex';
                    list.style.display = 'none';
                }

                try {
                    let currentTheaterId = new URLSearchParams(window.location.search).get('theater_id');
                    if (!currentTheaterId && typeof theaterId !== 'undefined' && theaterId) {
                        currentTheaterId = theaterId;
                    }
                    if (!currentTheaterId) {
                        const pathParts = window.location.pathname.split('/').filter(Boolean);
                        if (pathParts.length >= 2 && pathParts[0] === 'theaters') {
                            currentTheaterId = pathParts[1];
                        }
                    }
                    if (!currentTheaterId) {
                        console.warn('Cannot fetch suggestions: theater_id missing.');
                        if (!cachedSuggestions) {
                            list.innerHTML = '<div style="font-size: 0.78rem; color: #cbd5e1;">Launch a theater to view suggestions.</div>';
                        }
                        return;
                    }

                    const response = await fetch(`/theaters/${encodeURIComponent(currentTheaterId)}/suggestions`);
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                    const data = await response.json();

                    const newFingerprint = data.elements_fingerprint || '';
                    const newSuggestions = data.suggestions || [];

                    // Keep suggestions cached until elements update (fingerprint changes)
                    if (!cachedSuggestions || newFingerprint !== cachedFingerprint) {
                        cachedSuggestions = newSuggestions;
                        cachedFingerprint = newFingerprint;
                    }
                    renderSuggestions(cachedSuggestions);
                } catch (err) {
                    console.error('Failed to load suggestions:', err);
                    if (!cachedSuggestions) {
                        list.innerHTML = '<div style="font-size: 0.78rem; color: #ef4444;">Unable to generate suggestions at this time.</div>';
                    }
                } finally {
                    isFetching = false;
                    loading.style.display = 'none';
                    list.style.display = 'flex';
                }
            }

            btn.addEventListener('pointerdown', (e) => e.stopPropagation());
            btn.addEventListener('mousedown', (e) => e.stopPropagation());
            btn.addEventListener('mouseenter', () => {
                btn.classList.add('expanded');
                triggerSuggestionsFetch();
            });

            btn.addEventListener('mouseleave', () => {
                btn.classList.remove('expanded');
            });
        })();

        // Story Planning Sticky Notes Hover Handler
        (function initStickyNotesHandler() {
            const btn = document.getElementById('sticky-notes-toggle-btn');
            const list = document.getElementById('sticky-notes-list');
            const loading = document.getElementById('sticky-notes-loading');
            const badge = document.getElementById('sticky-notes-count-badge');
            if (!btn || !list || !loading) return;

            let cachedNotes = null;
            let cachedHidden = [];
            let hiddenRevealed = false;
            let isFetching = false;
            let isPinned = false;
            const STICKIES_REVEAL_CACHE_TIMEOUT_MS = 60 * 60 * 1000; // 1 hour

            function getStickiesRevealedCacheKey(tid) {
                return `narratron_stickies_revealed:${tid || 'default'}`;
            }

            function isStickiesRevealedInCache(tid) {
                try {
                    const key = getStickiesRevealedCacheKey(tid);
                    const raw = localStorage.getItem(key);
                    if (!raw) return false;
                    const parsed = JSON.parse(raw);
                    if (parsed && typeof parsed === 'object') {
                        if (typeof parsed.expiresAt === 'number' && Date.now() > parsed.expiresAt) {
                            localStorage.removeItem(key);
                            return false;
                        }
                        return Boolean(parsed.revealed);
                    }
                    if (typeof parsed === 'number') {
                        if (Date.now() > parsed) {
                            localStorage.removeItem(key);
                            return false;
                        }
                        return true;
                    }
                } catch (_) { }
                return false;
            }

            function setStickiesRevealedInCache(tid, revealed) {
                try {
                    const key = getStickiesRevealedCacheKey(tid);
                    if (!revealed) {
                        localStorage.removeItem(key);
                        return;
                    }
                    const payload = {
                        revealed: true,
                        revealedAt: Date.now(),
                        expiresAt: Date.now() + STICKIES_REVEAL_CACHE_TIMEOUT_MS
                    };
                    localStorage.setItem(key, JSON.stringify(payload));
                } catch (_) { }
            }

            function isHiddenRevealed() {
                const tid = resolveTheaterId();
                if (isStickiesRevealedInCache(tid)) {
                    return true;
                }
                return Boolean(hiddenRevealed);
            }

            function escapeHtml(str) {
                return String(str)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;');
            }

            function isHiddenItem(item, hiddenSet) {
                if (!item) return false;
                if (item.hidden === true || item.is_hidden === true) return true;
                const rawTopic = String(item.topic || item.name || '').trim().toLowerCase();
                if (!rawTopic) return false;
                if (hiddenSet.has(rawTopic)) return true;
                const normalized = rawTopic.replace(/[_\s-]+/g, ' ');
                for (const h of hiddenSet) {
                    if (h.replace(/[_\s-]+/g, ' ') === normalized) return true;
                }
                return false;
            }

            function areNotesEqual(a, b) {
                if (a === b) return true;
                if (!Array.isArray(a) || !Array.isArray(b)) return false;
                if (a.length !== b.length) return false;
                for (let i = 0; i < a.length; i++) {
                    const x = a[i], y = b[i];
                    if (!x || !y) return false;
                    if ((x.topic || x.name) !== (y.topic || y.name)) return false;
                    if ((x.info || x.content) !== (y.info || y.content)) return false;
                    if (Boolean(x.hidden || x.is_hidden) !== Boolean(y.hidden || y.is_hidden)) return false;
                }
                return true;
            }

            function areHiddenEqual(a, b) {
                if (a === b) return true;
                if (!Array.isArray(a) || !Array.isArray(b)) return false;
                if (a.length !== b.length) return false;
                const sA = new Set(a.map(x => String(typeof x === 'object' && x ? (x.topic || x.name || '') : x).trim().toLowerCase()));
                const sB = new Set(b.map(x => String(typeof x === 'object' && x ? (x.topic || x.name || '') : x).trim().toLowerCase()));
                if (sA.size !== sB.size) return false;
                for (const item of sA) {
                    if (!sB.has(item)) return false;
                }
                return true;
            }

            function renderStickyNotes(items, hiddenList) {
                if (Array.isArray(items)) {
                    cachedNotes = items;
                }
                if (Array.isArray(hiddenList) && hiddenList.length > 0) {
                    cachedHidden = hiddenList;
                }

                const notes = cachedNotes || [];
                const hiddenSet = new Set(
                    (cachedHidden || []).map(h => {
                        const val = typeof h === 'object' && h !== null ? (h.topic || h.name || '') : h;
                        return String(val).trim().toLowerCase();
                    }).filter(Boolean)
                );

                const visibleItems = [];
                const hiddenItems = [];

                for (const item of notes) {
                    if (isHiddenItem(item, hiddenSet)) {
                        hiddenItems.push(item);
                    } else {
                        visibleItems.push(item);
                    }
                }

                const isRevealed = isHiddenRevealed();

                if (badge) {
                    if (notes.length > 0) {
                        const countText = isRevealed
                            ? String(notes.length)
                            : (visibleItems.length > 0 ? String(visibleItems.length) : (hiddenItems.length > 0 ? `${hiddenItems.length} 🔒` : '0'));
                        badge.textContent = countText;
                        badge.style.display = 'inline-block';
                    } else {
                        badge.style.display = 'none';
                    }
                }

                if (notes.length === 0) {
                    list.innerHTML = '<div style="font-size: 0.78rem; color: #94a3b8;">No sticky notes active for this scene.</div>';
                    return;
                }

                let html = '';

                if (visibleItems.length === 0 && hiddenItems.length > 0) {
                    html += '<div style="font-size: 0.78rem; color: #94a3b8; font-style: italic;">No standard sticky notes active.</div>';
                } else {
                    html += visibleItems.map(item => `
                        <div class="sticky-note-card">
                            <div class="sticky-note-card-header">
                                <span class="sticky-note-card-topic">${escapeHtml(item.topic || item.name || '')}</span>
                            </div>
                            <div class="sticky-note-card-info">${escapeHtml(item.info || item.content || '')}</div>
                        </div>
                    `).join('');
                }

                if (hiddenItems.length > 0) {
                    const count = hiddenItems.length;
                    const label = isRevealed
                        ? `🙈 Hide ${count} hidden sticky${count > 1 ? ' notes' : ' note'}`
                        : `👁️ Show ${count} hidden sticky${count > 1 ? ' notes' : ' note'}`;
                    const btnClass = isRevealed
                        ? 'sticky-notes-hidden-toggle-btn revealed'
                        : 'sticky-notes-hidden-toggle-btn';
                    const containerDisplay = isRevealed ? 'flex' : 'none';

                    html += `
                        <div class="sticky-notes-hidden-section">
                            <button type="button" id="sticky-notes-hidden-toggle-btn" class="${btnClass}" title="Toggle reveal of hidden stickies">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                    ${isRevealed
                            ? '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>'
                            : '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>'
                        }
                                </svg>
                                <span class="sticky-notes-hidden-toggle-text">${label}</span>
                            </button>
                            <div id="sticky-notes-hidden-container" class="sticky-notes-hidden-container" style="display: ${containerDisplay};">
                                ${hiddenItems.map(item => `
                                    <div class="sticky-note-card is-hidden-sticky">
                                        <div class="sticky-note-card-header">
                                            <span class="sticky-note-card-topic">
                                                ${escapeHtml(item.topic || item.name || '')}
                                                <span class="sticky-note-hidden-badge">Hidden</span>
                                            </span>
                                        </div>
                                        <div class="sticky-note-card-info">${escapeHtml(item.info || item.content || '')}</div>
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    `;
                }

                list.innerHTML = html;

                const toggleBtn = document.getElementById('sticky-notes-hidden-toggle-btn');
                if (toggleBtn) {
                    toggleBtn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        e.preventDefault();
                        const nextRevealed = !isHiddenRevealed();
                        hiddenRevealed = nextRevealed;
                        setStickiesRevealedInCache(resolveTheaterId(), nextRevealed);
                        renderStickyNotes(cachedNotes, cachedHidden);
                        if (nextRevealed) {
                            const hiddenContainer = document.getElementById('sticky-notes-hidden-container');
                            if (hiddenContainer) {
                                hiddenContainer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                            }
                        }
                    });
                }
            }

            function resolveTheaterId() {
                try {
                    const sp = new URLSearchParams(window.location.search);
                    const tid = sp.get('theater_id');
                    if (tid) return tid;
                } catch (_) { }
                if (typeof window !== 'undefined') {
                    if (window.currenttheaterId) return window.currenttheaterId;
                    if (window.theaterId) return window.theaterId;
                }
                try {
                    const pathParts = window.location.pathname.split('/').filter(Boolean);
                    if (pathParts.length >= 2 && pathParts[0] === 'theaters') {
                        return pathParts[1];
                    }
                } catch (_) { }
                return null;
            }

            async function triggerStickyNotesFetch() {
                if (isFetching) return;
                isFetching = true;

                if (!cachedNotes) {
                    loading.style.display = 'flex';
                    list.style.display = 'none';
                }

                try {
                    const currentTheaterId = resolveTheaterId();
                    if (!currentTheaterId) {
                        return;
                    }

                    let joinKey = '';
                    try {
                        joinKey = new URLSearchParams(window.location.search).get('join_key') || '';
                    } catch (_) { }
                    const query = joinKey ? `?join_key=${encodeURIComponent(joinKey)}` : '';
                    let response = null;
                    try {
                        response = await fetch(`/theaters/${encodeURIComponent(currentTheaterId)}/sticky-notes${query}`);
                    } catch (_) { }

                    if (!response || !response.ok) {
                        try {
                            const apiQuery = `theater_id=${encodeURIComponent(currentTheaterId)}${joinKey ? `&join_key=${encodeURIComponent(joinKey)}` : ''}`;
                            response = await fetch(`/api/sticky-notes?${apiQuery}`);
                        } catch (_) { }
                    }

                    if (response && response.ok) {
                        const data = await response.json();
                        let notesChanged = false;
                        let hiddenChanged = false;
                        if (Array.isArray(data.sticky_notes)) {
                            if (!areNotesEqual(cachedNotes, data.sticky_notes)) {
                                cachedNotes = data.sticky_notes;
                                notesChanged = true;
                            }
                        }
                        if (Array.isArray(data.hidden_stickies) && data.hidden_stickies.length > 0) {
                            if (!areHiddenEqual(cachedHidden, data.hidden_stickies)) {
                                cachedHidden = data.hidden_stickies;
                                hiddenChanged = true;
                            }
                        }
                        if (notesChanged || hiddenChanged || !list.hasChildNodes()) {
                            renderStickyNotes(cachedNotes, cachedHidden);
                        }
                    }
                } catch (err) {
                    console.error('Failed to load sticky notes:', err);
                    if (!cachedNotes) {
                        list.innerHTML = '<div style="font-size: 0.78rem; color: #ef4444;">Unable to load sticky notes at this time.</div>';
                    }
                } finally {
                    isFetching = false;
                    loading.style.display = 'none';
                    list.style.display = 'flex';
                }
            }

            window.updateCanvasStickyNotes = function (notes, hidden) {
                let notesChanged = false;
                let hiddenChanged = false;

                if (Array.isArray(notes)) {
                    if (!areNotesEqual(cachedNotes, notes)) {
                        cachedNotes = notes;
                        notesChanged = true;
                    }
                }
                if (Array.isArray(hidden) && hidden.length > 0) {
                    if (!areHiddenEqual(cachedHidden, hidden)) {
                        cachedHidden = hidden;
                        hiddenChanged = true;
                    }
                }

                if (notesChanged || hiddenChanged || !list.hasChildNodes()) {
                    renderStickyNotes(cachedNotes, cachedHidden);
                }
            };

            window.isStickiesRevealedInCache = isStickiesRevealedInCache;
            window.setStickiesRevealedInCache = setStickiesRevealedInCache;
            window.STICKIES_REVEAL_CACHE_TIMEOUT_MS = STICKIES_REVEAL_CACHE_TIMEOUT_MS;

            btn.addEventListener('pointerdown', (e) => e.stopPropagation());
            btn.addEventListener('mousedown', (e) => e.stopPropagation());

            let leaveTimeout = null;
            btn.addEventListener('mouseenter', () => {
                if (leaveTimeout) {
                    clearTimeout(leaveTimeout);
                    leaveTimeout = null;
                }
                btn.classList.add('expanded');
                triggerStickyNotesFetch();
            });

            btn.addEventListener('mouseleave', () => {
                if (!isPinned) {
                    if (leaveTimeout) clearTimeout(leaveTimeout);
                    leaveTimeout = setTimeout(() => {
                        if (!isPinned) {
                            btn.classList.remove('expanded');
                        }
                        leaveTimeout = null;
                    }, 250);
                }
            });

            btn.addEventListener('click', (e) => {
                if (e.target.closest('#sticky-notes-hidden-toggle-btn') || e.target.closest('.sticky-note-card')) {
                    return;
                }
                isPinned = !isPinned;
                btn.classList.toggle('expanded', isPinned);
                if (isPinned) {
                    triggerStickyNotesFetch();
                }
            });

            // Initial fetch to load sticky notes and hidden configuration on canvas load
            triggerStickyNotesFetch();
        })();

        // Hide All Text On Canvas Button Logic
        (function initHideCanvasTextHandler() {
            const btn = document.getElementById('hide-canvas-text-btn');
            const imgContainer = document.getElementById('image-container');
            if (!btn || !imgContainer) return;

            const storageKey = 'narratron-hide-canvas-text';
            let isHidden = false;

            function setCanvasTextHidden(hidden, persist = true) {
                isHidden = Boolean(hidden);
                imgContainer.classList.toggle('hide-canvas-text', isHidden);
                btn.classList.toggle('active', isHidden);
                btn.setAttribute('aria-pressed', String(isHidden));
                btn.title = isHidden ? 'Show all text on canvas' : 'Hide all text on canvas';
                btn.setAttribute('aria-label', isHidden ? 'Show all text on canvas' : 'Hide all text on canvas');

                const visibleIcon = btn.querySelector('.hide-text-icon-visible');
                const hiddenIcon = btn.querySelector('.hide-text-icon-hidden');
                if (visibleIcon) visibleIcon.style.display = isHidden ? 'none' : 'block';
                if (hiddenIcon) hiddenIcon.style.display = isHidden ? 'block' : 'none';

                if (persist) {
                    try {
                        localStorage.setItem(storageKey, String(isHidden));
                    } catch (_) {}
                }
            }

            btn.addEventListener('pointerdown', (e) => e.stopPropagation());
            btn.addEventListener('mousedown', (e) => e.stopPropagation());
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                setCanvasTextHidden(!isHidden);
            });

            // Restore user's saved preference
            try {
                if (localStorage.getItem(storageKey) === 'true') {
                    setCanvasTextHidden(true, false);
                }
            } catch (_) {}

            window.setCanvasTextHidden = setCanvasTextHidden;
        })();

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
        import { createDoodleRenderer, createImageRenderer } from "/static/js/canvas-renderers.js?v=zoom-effect-origin-20261030-1";
        import { createA2UICanvasRenderer } from "/static/js/a2ui-canvas-renderer.js?v=20261005-1";
        import { createGameRenderer } from "/static/js/game-renderer.js?v=17";
        import { getAnimationSignature, playCanvasAnimation } from "/static/js/canvas-media.js";
        import { createCanvasScene } from "/static/js/canvas-scene.js";
        import { createNarrationPane } from "/static/js/narration-pane.js";

        let lastTime = -1;
        let lastImageRawUrl = null;
        let lastImageEffect = null;
        const MINIMAL_MODE_STORAGE_KEY = 'narratron.canvas.performance.minimalMode';
        const LEGACY_IMAGE_EFFECTS_STORAGE_KEY = 'narratron.canvas.performance.disableImageEffects';
        const MUSIC_MUTED_STORAGE_KEY = 'narratron_music_muted';
        let isMuted = localStorage.getItem(MUSIC_MUTED_STORAGE_KEY) === 'true';
        let minimalMode = (() => {
            try {
                // Preserve the old performance preference when upgrading to
                // Minimal Mode, which includes disabling image effects.
                return localStorage.getItem(MINIMAL_MODE_STORAGE_KEY) === 'true'
                    || localStorage.getItem(LEGACY_IMAGE_EFFECTS_STORAGE_KEY) === 'true';
            } catch (_) {
                return false;
            }
        })();
        document.documentElement.classList.toggle('minimal-mode', minimalMode);
        let isFirstCanvasLoad = true;
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
        function updateSpeechReplayButtonUI(state) {
            if (!latestSpeechReplayBtn) return;
            const hasLatest = Boolean(state && state.hasLatestSpeech);
            const isPlaying = Boolean(state && state.playing);
            latestSpeechReplayBtn.disabled = !hasLatest;
            latestSpeechReplayBtn.classList.toggle('playing', isPlaying);
            latestSpeechReplayBtn.title = hasLatest
                ? (isPlaying ? 'Replaying latest speech… (click to restart)' : 'Replay latest speech')
                : 'No speech to replay';
        }

        const { updateSceneDialogue, enqueueSceneSpeech, stopSceneSpeech, replayLatestSpeech } = createCanvasScene({
            narrationPane,
            audioEnabled: () => !isMuted,
            onSpeechStateChange: updateSpeechReplayButtonUI,
        });

        latestSpeechReplayBtn?.addEventListener('click', () => {
            replayLatestSpeech();
        });
        latestSpeechReplayBtn?.addEventListener('pointerdown', (e) => e.stopPropagation());
        latestSpeechReplayBtn?.addEventListener('mousedown', (e) => e.stopPropagation());

        let vadSpeechTimer = null;
        let isVadSpeaking = false;

        function handleVadSpeechStart() {
            if (micMode === 'push-to-talk') return;
            if (isVadSpeaking) return;
            isVadSpeaking = true;
            if (vadSpeechTimer) {
                clearTimeout(vadSpeechTimer);
                vadSpeechTimer = null;
            }
            vadSpeechTimer = setTimeout(() => {
                if (isVadSpeaking && micMode !== 'push-to-talk') {
                    stopSceneSpeech();
                }
            }, 1000);
        }

        function handleVadSpeechEnd() {
            isVadSpeaking = false;
            if (vadSpeechTimer) {
                clearTimeout(vadSpeechTimer);
                vadSpeechTimer = null;
            }
        }

        window.addEventListener('narratron:speech-start', handleVadSpeechStart);
        window.addEventListener('narratron:speech-end', handleVadSpeechEnd);
        window.addEventListener('vadstart', handleVadSpeechStart);
        window.addEventListener('vadstop', handleVadSpeechEnd);

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

        function updateCanvasPinButton() {
            if (!canvasPinBtn) return;
            const allPinned = canvasPinned && musicPinned;
            canvasPinBtn.style.display = isCurrentOrator() ? 'inline-flex' : 'none';
            canvasPinBtn.classList.toggle('active', allPinned);
            canvasPinBtn.setAttribute('aria-pressed', canvasPinned !== musicPinned ? 'mixed' : String(allPinned));
            canvasPinBtn.title = allPinned
                ? 'Unpin image and music and allow changes'
                : 'Pin the current image and music and block changes';
            if (canvasPinLabel) canvasPinLabel.textContent = allPinned ? 'Image and music pinned' : 'Pin image and music';
        }

        canvasPinBtn?.addEventListener('click', async () => {
            if (!isCurrentOrator() || !theaterId || canvasPinBtn.disabled) return;
            canvasPinBtn.disabled = true;
            try {
                const response = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/canvas-pin`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pinned: !(canvasPinned && musicPinned) }),
                });
                const result = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(result.detail || 'Unable to update the canvas pin.');
                canvasPinned = Boolean(result.pinned);
                musicPinned = Boolean(result.music_pinned);
                updateCanvasPinButton();
                actionWheel.updateState(canvasPinned, musicPinned);
            } catch (error) {
                console.error('Failed to update canvas pin:', error);
                canvasPinBtn.title = error.message;
            } finally {
                canvasPinBtn.disabled = false;
            }
        });

        const actionWheel = initializeActionWheel({
            isOrator: isCurrentOrator,
            canvasTarget: document.getElementById('image-container'),
            onOpen: () => { isDrawing = false; flushDrawBatch(); },
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
                    oratorCursorIndex = Number(result.orator_cursor);
                    if (!isNavigating && currentViewIndex !== oratorCursorIndex) {
                        displayHistoryImage(oratorCursorIndex, 'none');
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
        let currentPlaylistTracks = [];
        // Each playlist keeps its own shuffle cycle. A track is added to its
        // played set as soon as it starts, so changing playlists does not reset
        // the cycle when the user later returns to it.
        const playlistPlaybackState = new Map();
        let bgMusic = new Audio();
        let outgoingMusic = null;
        let isMusicPausedState = false;
        let lastPlaylistTime = 0;
        let activePlaylistName = null;
        let fadeInterval = null;
        let outgoingFadeInterval = null;
        let currentTrackSrc = null;
        let lastMusicStateSave = 0;
        const musicPlaybackStorageKey = `narratron-music-playback:${theaterId}`;
        let restoredMusicPlayback = loadMusicPlaybackState();

        function loadMusicPlaybackState() {
            try {
                const saved = JSON.parse(theaterStorage.getItem(musicPlaybackStorageKey));
                if (!saved || !saved.playlist || !saved.track) return null;

                for (const [playlistName, playedTracks] of saved.playbackStates || []) {
                    playlistPlaybackState.set(playlistName, {
                        playedTracks: new Set(playedTracks),
                        lastPlayedTrack: playedTracks[playedTracks.length - 1] || null,
                    });
                }
                return saved;
            } catch (error) {
                console.warn("Could not restore saved music playback state:", error);
                return null;
            }
        }

        function saveMusicPlaybackState() {
            if (!activePlaylistName || !currentTrackSrc || !bgMusic) return;
            try {
                theaterStorage.setItem(musicPlaybackStorageKey, JSON.stringify({
                    playlist: activePlaylistName,
                    track: currentTrackSrc,
                    position: Number.isFinite(bgMusic.currentTime) ? bgMusic.currentTime : 0,
                    playbackStates: [...playlistPlaybackState.entries()].map(([name, state]) => [
                        name,
                        [...state.playedTracks],
                    ]),
                }));
            } catch (error) {
                console.warn("Could not save music playback state:", error);
            }
        }

        const musicMuteBtn = document.getElementById('music-mute-btn');
        const musicStatusSpan = document.getElementById('music-status');
        const musicVolumeSlider = document.getElementById('music-volume-slider');
        const volumePercentSpan = document.getElementById('volume-percent');

        function setMusicMuted(muted) {
            isMuted = muted;
            localStorage.setItem(MUSIC_MUTED_STORAGE_KEY, String(muted));
            if (bgMusic) bgMusic.muted = muted;
            if (outgoingMusic) outgoingMusic.muted = muted;
            if (muted && typeof stopSceneSpeech === 'function') {
                stopSceneSpeech();
            }
            musicMuteBtn.textContent = muted ? '🔇 Unmute' : '🔊 Mute';
            musicMuteBtn.style.background = muted ? 'rgba(239, 68, 68, 0.2)' : 'rgba(255,255,255,0.08)';
            musicMuteBtn.style.borderColor = muted ? 'rgba(239, 68, 68, 0.4)' : 'var(--glass-border)';
        }

        // Set initial volume and restore the user's saved mute preference
        bgMusic.volume = parseFloat(musicVolumeSlider.value);
        setMusicMuted(isMuted);

        function setupBgMusicListeners(audioObj) {
            if (!audioObj) return;
            audioObj.addEventListener('ended', () => {
                if (audioObj === bgMusic) {
                    playNextTrack();
                }
            });
            audioObj.addEventListener('timeupdate', () => {
                if (audioObj === bgMusic && Date.now() - lastMusicStateSave >= 1000) {
                    lastMusicStateSave = Date.now();
                    saveMusicPlaybackState();
                }
            });
        }
        setupBgMusicListeners(bgMusic);
        window.addEventListener('beforeunload', saveMusicPlaybackState);

        function cancelFade() {
            if (fadeInterval) {
                clearInterval(fadeInterval);
                fadeInterval = null;
            }
        }

        function cancelOutgoingFade() {
            if (outgoingFadeInterval) {
                clearInterval(outgoingFadeInterval);
                outgoingFadeInterval = null;
            }
            if (outgoingMusic) {
                try {
                    outgoingMusic.pause();
                    outgoingMusic.src = "";
                } catch (e) {
                    console.warn("Error stopping outgoing music:", e);
                }
                outgoingMusic = null;
            }
        }

        function gentleFadeInCurve(progress) {
            // Quadratic ease-in for gentle volume swell
            return Math.pow(progress, 2);
        }

        function gentleFadeOutCurve(progress) {
            // Quadratic ease-out for smooth volume decrease
            return Math.pow(1 - progress, 2);
        }

        function fadeIn(durationMs = 3500) {
            cancelFade();
            if (isMusicPausedState) {
                if (bgMusic) try { bgMusic.pause(); } catch (e) { }
                return;
            }
            const targetVolume = parseFloat(musicVolumeSlider.value);
            if (isMuted || targetVolume === 0) {
                if (bgMusic) bgMusic.volume = targetVolume;
                return;
            }

            if (bgMusic) bgMusic.volume = 0;
            const startTime = Date.now();

            fadeInterval = setInterval(() => {
                if (isMusicPausedState) {
                    cancelFade();
                    if (bgMusic) try { bgMusic.pause(); } catch (e) { }
                    return;
                }
                const elapsed = Date.now() - startTime;
                const progress = Math.min(1, elapsed / durationMs);
                const currentTarget = parseFloat(musicVolumeSlider.value);
                const gentleVal = gentleFadeInCurve(progress) * currentTarget;

                if (bgMusic) {
                    bgMusic.volume = Math.min(currentTarget, Math.max(0, gentleVal));
                }

                if (progress >= 1) {
                    cancelFade();
                    if (bgMusic && !isMuted) {
                        bgMusic.volume = currentTarget;
                    }
                }
            }, 30);
        }

        function fadeOut(onComplete, durationMs = 2500) {
            cancelFade();
            if (!bgMusic) {
                if (onComplete) onComplete();
                return;
            }
            if (bgMusic.paused || isMuted || bgMusic.volume === 0) {
                try { bgMusic.pause(); } catch (e) { }
                if (onComplete) onComplete();
                return;
            }

            const startVolume = bgMusic.volume;
            const startTime = Date.now();

            fadeInterval = setInterval(() => {
                const elapsed = Date.now() - startTime;
                const progress = Math.min(1, elapsed / durationMs);
                const gentleVal = startVolume * gentleFadeOutCurve(progress);

                if (bgMusic) {
                    bgMusic.volume = Math.max(0, gentleVal);
                }

                if (progress >= 1) {
                    cancelFade();
                    if (onComplete) onComplete();
                }
            }, 30);
        }

        function getPlaylistPlaybackState(playlistName, tracks) {
            let state = playlistPlaybackState.get(playlistName);
            if (!state) {
                state = { playedTracks: new Set(), lastPlayedTrack: null };
                playlistPlaybackState.set(playlistName, state);
            }

            // Keep the state valid if a playlist's contents change while the
            // canvas is open (for example, after tracks are added or removed).
            const availableTracks = new Set(tracks);
            state.playedTracks = new Set(
                [...state.playedTracks].filter(track => availableTracks.has(track))
            );
            return state;
        }

        function chooseNextTrack(playlistName, tracks) {
            const state = getPlaylistPlaybackState(playlistName, tracks);
            let candidates = tracks.filter(track => !state.playedTracks.has(track));

            // Every song has played once: begin a new shuffle cycle.
            if (candidates.length === 0) {
                state.playedTracks.clear();
                candidates = [...tracks];
                // Avoid an immediate repeat at the cycle boundary when there
                // is another track to choose from.
                if (candidates.length > 1 && state.lastPlayedTrack) {
                    candidates = candidates.filter(track => track !== state.lastPlayedTrack);
                }
            }

            const track = candidates[Math.floor(Math.random() * candidates.length)];
            state.playedTracks.add(track);
            state.lastPlayedTrack = track;
            return track;
        }

        function getRestoredTrack(playlistName, tracks) {
            if (!restoredMusicPlayback) return null;

            if (restoredMusicPlayback.playlist !== playlistName) {
                // The agent selected a different playlist while this viewer was
                // away, so its old track must not override the current choice.
                restoredMusicPlayback = null;
                theaterStorage.removeItem(musicPlaybackStorageKey);
                return null;
            }

            const track = tracks.includes(restoredMusicPlayback.track)
                ? restoredMusicPlayback.track
                : null;
            const position = Math.max(0, Number(restoredMusicPlayback.position) || 0);
            restoredMusicPlayback = null;
            return track ? { track, position } : null;
        }

        function playPlaylist(playlistName, tracks) {
            console.log("Playing playlist tracks:", tracks);
            currentPlaylistTracks = tracks;
            if (currentPlaylistTracks.length > 0) {
                playTrack(chooseNextTrack(playlistName, currentPlaylistTracks));
            }
        }

        function playTrack(src, startTime = 0) {
            console.log("Playing track:", src);
            const blendDuration = 3500;
            if (bgMusic && !bgMusic.paused && bgMusic.src && currentTrackSrc && currentTrackSrc !== src) {
                // Crossfade / blend outgoing music with incoming track
                cancelOutgoingFade();
                outgoingMusic = bgMusic;
                const outgoingStartVol = outgoingMusic.volume;
                const fadeOutStartTime = Date.now();

                // Detach listeners from outgoing music
                outgoingMusic.onended = null;
                outgoingMusic.ontimeupdate = null;

                outgoingFadeInterval = setInterval(() => {
                    const elapsed = Date.now() - fadeOutStartTime;
                    const progress = Math.min(1, elapsed / blendDuration);
                    const gentleVal = outgoingStartVol * gentleFadeOutCurve(progress);
                    if (outgoingMusic) {
                        outgoingMusic.volume = Math.max(0, gentleVal);
                    }
                    if (progress >= 1) {
                        cancelOutgoingFade();
                    }
                }, 30);

                bgMusic = new Audio();
                setupBgMusicListeners(bgMusic);
                startTrack(src, startTime, blendDuration);
            } else {
                startTrack(src, startTime, blendDuration);
            }
        }

        function startTrack(src, startTime = 0, fadeDurationMs = 3500) {
            currentTrackSrc = src;
            if (assetMusicState) updateAssetPlaybackSelection(assetMusicState);
            bgMusic.src = src;
            bgMusic.muted = isMuted;
            if (startTime > 0) {
                const restorePosition = () => {
                    bgMusic.currentTime = startTime;
                };
                if (bgMusic.readyState >= HTMLMediaElement.HAVE_METADATA) {
                    restorePosition();
                } else {
                    bgMusic.addEventListener('loadedmetadata', restorePosition, { once: true });
                }
            }
            const targetVol = parseFloat(musicVolumeSlider.value);
            if (!isMuted && targetVol > 0) {
                bgMusic.volume = 0;
            } else {
                bgMusic.volume = targetVol;
            }
            bgMusic.play().then(() => {
                if (isMusicPausedState) {
                    if (bgMusic) try { bgMusic.pause(); } catch (e) { }
                    return;
                }
                fadeIn(fadeDurationMs);
                saveMusicPlaybackState();
            }).catch(err => {
                console.warn("Audio autoplay blocked/failed. User interaction may be required. Error:", err);
            });
        }

        function playNextTrack() {
            if (currentPlaylistTracks.length === 0) return;
            playTrack(chooseNextTrack(activePlaylistName, currentPlaylistTracks));
        }
        function stopPlaylist() {
            console.log("Stopping playlist");
            cancelOutgoingFade();
            fadeOut(() => {
                if (bgMusic) {
                    bgMusic.pause();
                    bgMusic.src = "";
                }
                currentPlaylistTracks = [];
                currentTrackSrc = null;
            }, 2500);
        }

        musicMuteBtn.addEventListener('click', () => {
            setMusicMuted(!isMuted);
            if (isMuted) {
                cancelFade();
            } else {
                // Try to play if it was blocked or paused
                if (bgMusic && bgMusic.paused && currentPlaylistTracks.length > 0) {
                    bgMusic.volume = 0;
                    bgMusic.play().then(() => fadeIn(2500)).catch(() => { });
                } else {
                    fadeIn(2000);
                }
            }
        });

        musicVolumeSlider.addEventListener('input', (e) => {
            cancelFade();
            const volume = parseFloat(e.target.value);
            bgMusic.volume = volume;
            volumePercentSpan.textContent = Math.round(volume * 100) + '%';

            if (volume > 0 && isMuted) {
                setMusicMuted(false);
            }
        });

        const promptBtn = document.getElementById('prompt-toggle-btn');
        const pcmBroadcastIndicator = document.getElementById('pcm-broadcast-indicator');
        const drawingIndicator = document.getElementById('agent-drawing-indicator');
        const writingIndicator = document.getElementById('agent-writing-indicator');
        const musicIndicator = document.getElementById('agent-music-indicator');
        const diceIndicator = document.getElementById('agent-dice-indicator');
        const referenceIndicator = document.getElementById('agent-reference-indicator');
        const disconnectedIndicator = document.getElementById('agent-disconnected-indicator');
        const agentToolActivity = {};
        const agentToolActivityTiming = new Map();
        const agentActivityStaleMs = 30 * 1000;
        const agentActivityMaxMs = 10 * 60 * 1000;
        const agentActivityFields = [
            'image_generating', 'animation_generating', 'user_action_processing',
            'music_generating', 'dice_rolling', 'reference_generating',
        ];

        if (disconnectedIndicator) {
            disconnectedIndicator.addEventListener('click', () => {
                checkAgentStatus().then(() => ensureAgentConnectionWhenActive());
            });
        }

        function updateAgentActivity(toolActivity = {}) {
            // Each tool needs its own confirmation: a live-connection heartbeat
            // must not keep an unrelated tool indicator alive after an error.
            Object.assign(agentToolActivity, toolActivity);
            const now = Date.now();
            for (const field of agentActivityFields) {
                let timing = agentToolActivityTiming.get(field);
                if (toolActivity?.[field] === false) {
                    agentToolActivityTiming.delete(field);
                } else if (toolActivity?.[field] === true) {
                    if (!timing) {
                        timing = { startedAt: now, confirmedAt: now, timedOut: false };
                        agentToolActivityTiming.set(field, timing);
                    }
                    timing.confirmedAt = now;
                }
                if (timing && toolActivity?.[field] !== false) {
                    // Latch the hard timeout until completion, so a stuck true
                    // flag cannot repeatedly bring the indicator back.
                    timing.timedOut ||= now - timing.startedAt >= agentActivityMaxMs;
                    agentToolActivity[field] = !timing.timedOut
                        && now - timing.confirmedAt < agentActivityStaleMs;
                }
            }
            toolActivity = agentToolActivity;
            const setVisible = (element, visible) => {
                if (!element) return;
                const active = Boolean(visible);
                if (element.classList.contains('active') !== active) {
                    element.classList.toggle('active', active);
                }
                const hidden = String(!active);
                if (element.getAttribute('aria-hidden') !== hidden) {
                    element.setAttribute('aria-hidden', hidden);
                }
            };
            const isGeneratingImage = Boolean(toolActivity.image_generating);
            const isGeneratingAnimation = Boolean(toolActivity.animation_generating);
            const isDrawing = isGeneratingImage || isGeneratingAnimation;
            setVisible(drawingIndicator, isDrawing);
            if (drawingIndicator) {
                if (isGeneratingAnimation && !isGeneratingImage) {
                    drawingIndicator.title = 'Agent is generating an animation';
                } else {
                    drawingIndicator.title = 'Agent is painting a scene';
                }
            }
            setVisible(writingIndicator, toolActivity.user_action_processing);
            setVisible(musicIndicator, toolActivity.music_generating);
            setVisible(diceIndicator, toolActivity.dice_rolling);
            setVisible(referenceIndicator, Boolean(toolActivity.reference_generating));
            const diceResult = toolActivity.dice_result || {};
            const tier = ['low', 'middle', 'high'].includes(diceResult.tier) ? diceResult.tier : '';
            if (diceIndicator) {
                for (const resultTier of ['low', 'middle', 'high']) {
                    const className = `dice-result-${resultTier}`;
                    const selected = resultTier === tier;
                    if (diceIndicator.classList.contains(className) !== selected) {
                        diceIndicator.classList.toggle(className, selected);
                    }
                }
                diceIndicator.title = diceResult.notation
                    ? `Rolled ${diceResult.notation}: ${diceResult.total}`
                    : 'The story is rolling the dice';
            }
        }

        // Also expire indicators when requests fail and no new snapshot arrives.
        setInterval(() => updateAgentActivity(), 1000);

        function setPcmBroadcasting(isBroadcasting) {
            if (!pcmBroadcastIndicator) return;
            pcmBroadcastIndicator.classList.toggle('active', isBroadcasting);
            pcmBroadcastIndicator.setAttribute('aria-hidden', String(!isBroadcasting));
        }

        // These map to the complete RMS VAD speech segment, not PCM chunk boundaries.
        window.addEventListener('narratron:speech-start', () => setPcmBroadcasting(true));
        window.addEventListener('narratron:speech-end', () => setPcmBroadcasting(false));
        let promptPinned = false;

        promptBtn.addEventListener('mouseenter', () => {
            promptBtn.classList.add('expanded');
        });

        promptBtn.addEventListener('mouseleave', () => {
            if (!promptPinned) {
                promptBtn.classList.remove('expanded');
            }
        });

        promptBtn.addEventListener('click', () => {
            promptPinned = !promptPinned;
            if (!promptPinned) {
                promptBtn.classList.remove('expanded');
            } else {
                promptBtn.classList.add('expanded');
            }
        });

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
        const imageRenderer = createImageRenderer({
            canvas: rendererCanvas,
            image: currentImage,
            backgroundLayer: bgLayer,
            loadedClassNames: ['t-crossfade', 'loaded'],
            imageEffectsEnabled: () => !minimalMode,
        });
        const resizeRendererCanvas = imageRenderer.resize;
        const applyImageTransition = imageRenderer.applyTransition;
        const stopImageAnimation = imageRenderer.stopSequence ? imageRenderer.stopSequence.bind(imageRenderer) : null;

        function resolveWebpQualityUrl(rawUrl) {
            if (!rawUrl || typeof rawUrl !== 'string') return rawUrl;
            if (!minimalMode) return rawUrl;
            const pathPart = rawUrl.split('?')[0].split('#')[0].toLowerCase();
            if (!pathPart.endsWith('.webp')) return rawUrl;
            try {
                const urlObj = new URL(rawUrl, window.location.origin);
                if (!urlObj.searchParams.has('quality')) {
                    urlObj.searchParams.set('quality', 'low');
                }
                if (rawUrl.startsWith('/') || !rawUrl.includes('://')) {
                    return urlObj.pathname + urlObj.search + urlObj.hash;
                }
                return urlObj.toString();
            } catch (_) {
                const sep = rawUrl.includes('?') ? '&' : '?';
                return `${rawUrl}${sep}quality=low`;
            }
        }
        window.__resolveWebpQualityUrl = resolveWebpQualityUrl;

        function setMinimalMode(enabled) {
            const previousMode = minimalMode;
            minimalMode = Boolean(enabled);
            document.documentElement.classList.toggle('minimal-mode', minimalMode);
            try {
                localStorage.setItem(MINIMAL_MODE_STORAGE_KEY, String(minimalMode));
                localStorage.removeItem(LEGACY_IMAGE_EFFECTS_STORAGE_KEY);
            } catch (_) {
                // Storage can be unavailable in private or restricted browser contexts.
            }
            if (minimalMode) stopImageAnimation?.();
            imageRenderer.setImageEffectsEnabled?.(!minimalMode);
            if (previousMode !== minimalMode && currentViewIndex >= 0 && currentViewIndex < imageHistory.length) {
                if (typeof loadedHistoryResources !== 'undefined') {
                    loadedHistoryResources.forEach((res) => unloadHistoryResource(res));
                    loadedHistoryResources.clear();
                }
                displayHistoryImage(currentViewIndex, 'none');
            }
        }

        window.addEventListener('resize', resizeRendererCanvas);
        resizeRendererCanvas();

        let imageHistory = [];
        let currentViewIndex = -1;
        let oratorCursorIndex = -1;
        let isNavigating = false;
        let isPagedBack = false;
        let hasNewHeadWhilePagedBack = false;
        let lastAnimationSignature = '';

        const canvasBookmarks = initializeCanvasBookmarks({
            theaterId,
            isOrator: isCurrentOrator,
            getHistory: () => imageHistory,
            getIndex: () => currentViewIndex,
            navigate: async index => {
                const response = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/orator-action`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: 'bookmark_image', history_index: index }),
                });
                if (!response.ok) throw new Error('Bookmark navigation failed');
                const result = await response.json();
                oratorCursorIndex = Number(result.orator_cursor);
                displayHistoryImage(oratorCursorIndex, 'none');
            },
        });

        function stepOratorPreviousImage() {
            if (oratorCursorIndex > 0) {
                oratorCursorIndex -= 1;
            } else if (oratorCursorIndex < 0 && imageHistory.length > 1) {
                oratorCursorIndex = imageHistory.length - 2;
            } else if (currentViewIndex > 0) {
                oratorCursorIndex = currentViewIndex - 1;
            }
            isNavigating = false;
            isPagedBack = false;
            hasNewHeadWhilePagedBack = false;
            currentViewIndex = oratorCursorIndex;
            if (oratorCursorIndex >= 0 && oratorCursorIndex < imageHistory.length) {
                displayHistoryImage(oratorCursorIndex, 'none');
            } else {
                updatePagingControlsUI();
            }
        }

        async function triggerOratorPreviousImage() {
            stepOratorPreviousImage();
            try {
                const response = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/orator-action`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: 'previous_image' }),
                });
                const result = await response.json();
                if (response.ok && result.orator_cursor !== undefined && result.orator_cursor !== null) {
                    oratorCursorIndex = Number(result.orator_cursor);
                    currentViewIndex = oratorCursorIndex;
                    updatePagingControlsUI();
                }
            } catch (err) {
                console.error("Failed to trigger previous image action:", err);
            }
        }

        function stepOratorNextImage() {
            if (oratorCursorIndex >= 0 && oratorCursorIndex < imageHistory.length - 1) {
                oratorCursorIndex += 1;
            } else if (currentViewIndex >= 0 && currentViewIndex < imageHistory.length - 1) {
                oratorCursorIndex = currentViewIndex + 1;
            }
            isNavigating = false;
            isPagedBack = false;
            hasNewHeadWhilePagedBack = false;
            currentViewIndex = oratorCursorIndex;
            if (oratorCursorIndex >= 0 && oratorCursorIndex < imageHistory.length) {
                displayHistoryImage(oratorCursorIndex, 'none');
            } else {
                updatePagingControlsUI();
            }
        }

        async function triggerOratorNextImage() {
            stepOratorNextImage();
            try {
                const response = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/orator-action`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: 'next_image' }),
                });
                const result = await response.json();
                if (response.ok && result.orator_cursor !== undefined && result.orator_cursor !== null) {
                    oratorCursorIndex = Number(result.orator_cursor);
                    currentViewIndex = oratorCursorIndex;
                    updatePagingControlsUI();
                }
            } catch (err) {
                console.error("Failed to trigger next image action:", err);
            }
        }

        const HISTORY_WINDOW_RADIUS = 5;
        const loadedHistoryResources = new Map();

        function unloadHistoryResource(resource) {
            if (!resource) return;
            if (resource.img) {
                try {
                    resource.img.onload = null;
                    resource.img.onerror = null;
                    resource.img.src = '';
                } catch (e) { }
                resource.img = null;
            }
            if (Array.isArray(resource.frames)) {
                resource.frames.forEach((frame) => {
                    if (frame) {
                        try {
                            frame.onload = null;
                            frame.onerror = null;
                            frame.src = '';
                        } catch (e) { }
                    }
                });
                resource.frames = [];
            }
            if (resource.video) {
                try {
                    resource.video.pause();
                    resource.video.removeAttribute('src');
                    resource.video.load();
                } catch (e) { }
                resource.video = null;
            }
        }

        function pruneHistoryResources(centerIndex) {
            if (typeof centerIndex !== 'number' || centerIndex < 0) return;
            const minIndex = centerIndex - HISTORY_WINDOW_RADIUS;
            const maxIndex = centerIndex + HISTORY_WINDOW_RADIUS;
            for (const [index, resource] of loadedHistoryResources.entries()) {
                if (index < minIndex || index > maxIndex) {
                    unloadHistoryResource(resource);
                    loadedHistoryResources.delete(index);
                }
            }
        }

        function trackHistoryResource(index, resource) {
            if (index < 0) return;
            if (loadedHistoryResources.has(index)) {
                const prev = loadedHistoryResources.get(index);
                if (prev !== resource) {
                    unloadHistoryResource(prev);
                }
            }
            loadedHistoryResources.set(index, resource);
            pruneHistoryResources(currentViewIndex >= 0 ? currentViewIndex : index);
        }

        function preloadHistoryNeighbor(index) {
            if (index < 0 || index >= imageHistory.length || loadedHistoryResources.has(index)) return;
            const item = imageHistory[index];
            if (!item || !item.url || item.animation) return;
            const img = new Image();
            img.src = resolveWebpQualityUrl(item.url);
            trackHistoryResource(index, { type: 'image', img });
        }

        window.__historyResourceManager = {
            loadedHistoryResources,
            pruneHistoryResources,
            unloadHistoryResource,
            HISTORY_WINDOW_RADIUS,
        };

        initializeStoryLogInspector(theaterId);

        function playAnimation(animation) {
            if (minimalMode) {
                // Animated media defeats the point of Minimal Mode. Present a
                // still source where one exists, without starting a decoder or
                // a requestAnimationFrame loop.
                const stillUrl = animation.type === 'triframe'
                    ? animation.frames?.[0]
                    : animation.type === 'layered'
                        ? animation.layers?.[0]?.url
                        : animation.poster_url;
                if (stillUrl) {
                    applyImageTransition(resolveWebpQualityUrl(stillUrl), 'none', 'none');
                }
                return;
            }
            playCanvasAnimation(imageRenderer, animation, url => {
                const effective = resolveWebpQualityUrl(url);
                return animation.type === 'triframe'
                    ? effective + (effective.includes('?') ? '&' : '?') + 't=' + Date.now()
                    : effective;
            });
        }

        function updatePagingControlsUI() {
            canvasBookmarks.refresh();
            const controls = document.getElementById('history-paging-controls');
            const prevBtn = document.getElementById('page-prev-btn');
            const nextBtn = document.getElementById('page-next-btn');
            const headBtn = document.getElementById('page-head-btn');
            const indicator = document.getElementById('page-indicator');
            const newBadge = document.getElementById('new-head-badge');
            const doodleCanvas = document.getElementById('doodle-canvas');

            if (!controls) return;

            const isOrator = typeof isCurrentOrator === 'function' ? isCurrentOrator() : false;
            if (isOrator) {
                isNavigating = false;
                isPagedBack = false;
                hasNewHeadWhilePagedBack = false;
            }

            const total = Math.max(1, imageHistory ? imageHistory.length : 1);
            const currentNum = currentViewIndex >= 0 ? currentViewIndex + 1 : total;

            if (indicator) {
                indicator.textContent = `${currentNum} / ${total}`;
            }

            if (prevBtn) {
                prevBtn.disabled = (currentViewIndex <= 0 || total <= 1);
                prevBtn.style.opacity = prevBtn.disabled ? '0.35' : '1';
                prevBtn.style.cursor = prevBtn.disabled ? 'default' : 'pointer';
            }

            if (nextBtn) {
                nextBtn.disabled = (currentViewIndex >= total - 1 || total <= 1);
                nextBtn.style.opacity = nextBtn.disabled ? '0.35' : '1';
                nextBtn.style.cursor = nextBtn.disabled ? 'default' : 'pointer';
            }

            if (headBtn) {
                headBtn.style.display = (!isOrator && isNavigating) ? 'flex' : 'none';
            }

            if (newBadge) {
                newBadge.style.display = (!isOrator && isNavigating && hasNewHeadWhilePagedBack) ? 'inline-block' : 'none';
            }

            if (doodleCanvas) {
                refreshCanvasToolInteraction();
            }
        }

        function displayHistoryImage(index, transition = 'none') {
            if (index < 0 || index >= imageHistory.length) return;
            const isOrator = typeof isCurrentOrator === 'function' ? isCurrentOrator() : false;
            if (isOrator) {
                isNavigating = false;
                isPagedBack = false;
                hasNewHeadWhilePagedBack = false;
            }
            currentViewIndex = index;
            isPagedBack = isNavigating;
            if (!isNavigating) {
                hasNewHeadWhilePagedBack = false;
            }

            pruneHistoryResources(currentViewIndex);

            const item = imageHistory[currentViewIndex];
            if (item) displayPageDoodles(item);
            if (!item || (!item.url && !item.animation)) return;

            promptBtn.classList.remove('expanded');
            promptBtn.style.display = 'none';
            promptPinned = false;
            const promptText = item.prompt || (item.animation && item.animation.scene_prompt) || "";
            if (promptText) {
                promptTextSpan.textContent = promptText;
                promptBtn.style.display = 'flex';
            } else {
                promptBtn.style.display = 'none';
            }

            const animSignature = getAnimationSignature(item.animation);
            if (animSignature) {
                playAnimation(item.animation);
                trackHistoryResource(currentViewIndex, {
                    type: 'animation',
                    animation: item.animation,
                });
            } else if (item.url) {
                const effectiveUrl = resolveWebpQualityUrl(item.url);
                const imgUrl = effectiveUrl + (effectiveUrl.includes('?') ? '&' : '?') + 't=' + new Date().getTime();
                applyImageTransition(imgUrl, transition, item.effect || 'gleam3');
                const cachedImg = new Image();
                cachedImg.src = imgUrl;
                trackHistoryResource(currentViewIndex, {
                    type: 'image',
                    img: cachedImg,
                });
            }

            preloadHistoryNeighbor(currentViewIndex - 1);
            preloadHistoryNeighbor(currentViewIndex + 1);

            updatePagingControlsUI();
        }

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
                if (data.music && data.music.time > lastPlaylistTime) {
                    lastPlaylistTime = data.music.time;
                    isMusicPausedState = Boolean(data.music.paused);
                    const activeMusicId = data.music.music_id || data.music.playlist;
                    if (activeMusicId) {
                        if (activePlaylistName !== activeMusicId) {
                            activePlaylistName = activeMusicId;
                            musicStatusSpan.textContent = '\u{1F3B5} ' + activeMusicId;
                            const restoredTrack = getRestoredTrack(activeMusicId, data.music.tracks);
                            if (restoredTrack) {
                                currentPlaylistTracks = data.music.tracks;
                                playTrack(restoredTrack.track, restoredTrack.position);
                            } else {
                                playPlaylist(activeMusicId, data.music.tracks);
                            }
                        }

                        if (data.music.paused) {
                            musicStatusSpan.textContent = '\u{1F3B5} ' + activeMusicId + ' (Paused)';
                            if (bgMusic) {
                                try { bgMusic.pause(); } catch (e) { }
                            }
                            fadeOut(() => { if (bgMusic) try { bgMusic.pause(); } catch (e) { } }, 300);
                        } else {
                            musicStatusSpan.textContent = '\u{1F3B5} ' + activeMusicId;
                            if (bgMusic && bgMusic.paused && currentPlaylistTracks.length > 0) {
                                bgMusic.volume = 0;
                                bgMusic.play().then(() => fadeIn(1500)).catch(err => console.warn(err));
                            }
                        }
                    } else {
                        activePlaylistName = null;
                        musicStatusSpan.textContent = '\u{1F3B5} Silent';
                        stopPlaylist();
                    }
                }

                if (data.music) updateAssetPlaybackSelection(data.music);

                if (data.history && Array.isArray(data.history) && data.history.length > 0) {
                    imageHistory = data.history;
                } else if (data.latest) {
                    imageHistory = [{
                        url: data.latest,
                        prompt: data.prompt || (data.animation && data.animation.scene_prompt) || "",
                        time: data.time || 0,
                        transition: data.transition || "fade",
                        effect: data.effect || "gleam3",
                        path: data.latest,
                        animation: data.animation || null
                    }];
                }
                const isOrator = typeof isCurrentOrator === 'function' ? isCurrentOrator() : false;
                if (!isOrator || isFirstCanvasLoad || oratorCursorIndex < 0) {
                    if (data.orator_cursor !== undefined && data.orator_cursor !== null) {
                        oratorCursorIndex = Math.max(0, Math.min(Number(data.orator_cursor), imageHistory.length - 1));
                    } else if (imageHistory.length > 0) {
                        oratorCursorIndex = imageHistory.length - 1;
                    }
                }
                pruneHistoryResources(currentViewIndex >= 0 ? currentViewIndex : (oratorCursorIndex >= 0 ? oratorCursorIndex : imageHistory.length - 1));

                const currentImageEffect = data.effect || 'gleam3';
                const animationSignature = getAnimationSignature(data.animation);
                // A show_image call may change only presentation (for example,
                // applying haze to an image the fallback already displayed).
                // Treat that as an update even when the URL has not changed.
                const isNewImage = (data.latest || animationSignature) && (
                    data.time > lastTime
                    || data.latest !== lastImageRawUrl
                    || currentImageEffect !== lastImageEffect
                    || animationSignature !== lastAnimationSignature
                );
                if (isFirstCanvasLoad) {
                    isFirstCanvasLoad = false;
                    currentViewIndex = (oratorCursorIndex >= 0 && oratorCursorIndex < imageHistory.length)
                        ? oratorCursorIndex
                        : Math.max(0, imageHistory.length - 1);
                    isNavigating = false;
                    isPagedBack = false;
                    hasNewHeadWhilePagedBack = false;
                    pruneHistoryResources(currentViewIndex);
                    if (data.latest || animationSignature) {
                        lastTime = data.time;
                        lastImageRawUrl = data.latest;
                        lastImageEffect = currentImageEffect;
                        lastAnimationSignature = animationSignature;
                    }
                    displayHistoryImage(currentViewIndex, 'none');
                } else if (isNewImage || (!isOrator && !isNavigating && currentViewIndex !== oratorCursorIndex)) {
                    lastTime = data.time;
                    lastImageRawUrl = data.latest;
                    lastImageEffect = currentImageEffect;
                    lastAnimationSignature = animationSignature;
                    const transition = (data.transition || 'crossfade').toLowerCase().trim();

                    if (isOrator) {
                        isNavigating = false;
                        isPagedBack = false;
                        hasNewHeadWhilePagedBack = false;
                        if (data.orator_cursor !== undefined && data.orator_cursor !== null) {
                            oratorCursorIndex = Math.max(0, Math.min(Number(data.orator_cursor), imageHistory.length - 1));
                        } else {
                            oratorCursorIndex = Math.max(0, imageHistory.length - 1);
                        }
                    }

                    if (!isNavigating) {
                        currentViewIndex = (oratorCursorIndex >= 0 && oratorCursorIndex < imageHistory.length)
                            ? oratorCursorIndex
                            : Math.max(0, imageHistory.length - 1);
                        isPagedBack = false;
                        hasNewHeadWhilePagedBack = false;
                        pruneHistoryResources(currentViewIndex);

                        displayHistoryImage(currentViewIndex, transition);
                    } else {
                        hasNewHeadWhilePagedBack = true;
                        updatePagingControlsUI();
                    }
                } else if (!data.latest && !data.animation && imageHistory.length === 0) {
                    bgLayer.style.backgroundImage = 'none';
                    if (currentImage) currentImage.classList.remove('loaded');
                    if (ghostImage) { ghostImage.src = ''; ghostImage.style.opacity = '0'; }
                    if (promptBtn) promptBtn.style.display = 'none';
                    loadedHistoryResources.forEach((res) => unloadHistoryResource(res));
                    loadedHistoryResources.clear();
                    updatePagingControlsUI();
                } else {
                    updatePagingControlsUI();
                }
            } catch (err) {
                console.error("Failed to fetch image data:", err);
            }
        }

        document.getElementById('page-prev-btn')?.addEventListener('click', () => {
            if (typeof isCurrentOrator === 'function' && isCurrentOrator()) {
                if (currentViewIndex > 0) {
                    void triggerOratorPreviousImage();
                }
            } else if (currentViewIndex > 0) {
                isNavigating = true;
                isPagedBack = true;
                displayHistoryImage(currentViewIndex - 1, 'none');
            }
        });

        document.getElementById('page-next-btn')?.addEventListener('click', () => {
            if (typeof isCurrentOrator === 'function' && isCurrentOrator()) {
                if (currentViewIndex < imageHistory.length - 1) {
                    void triggerOratorNextImage();
                }
            } else if (currentViewIndex < imageHistory.length - 1) {
                isNavigating = true;
                isPagedBack = true;
                displayHistoryImage(currentViewIndex + 1, 'none');
            }
        });

        const syncToOratorCursor = () => {
            if (typeof isCurrentOrator === 'function' && isCurrentOrator()) return;
            isNavigating = false;
            isPagedBack = false;
            hasNewHeadWhilePagedBack = false;
            const target = (oratorCursorIndex >= 0 && oratorCursorIndex < imageHistory.length)
                ? oratorCursorIndex
                : Math.max(0, imageHistory.length - 1);
            if (target >= 0 && target < imageHistory.length) {
                displayHistoryImage(target, 'crossfade');
            } else {
                updatePagingControlsUI();
            }
        };

        document.getElementById('page-head-btn')?.addEventListener('click', syncToOratorCursor);
        document.getElementById('page-sync-btn')?.addEventListener('click', syncToOratorCursor);

        // Paging bar hover discovery: reveal controls when mouse moves near top center,
        // without letting the discovery area block clicks to underlying canvas or interactive UI elements.
        const pagingControls = document.getElementById('history-paging-controls');
        const imgContainer = document.getElementById('image-container');
        if (pagingControls && imgContainer) {
            let pagingHoverTimeout = null;
            imgContainer.addEventListener('pointermove', (e) => {
                if (typeof window !== 'undefined' && window.isCanvasSelectionActive) return;
                if (pagingControls.classList.contains('inspector-open')) return;
                const rect = imgContainer.getBoundingClientRect();
                const relY = e.clientY - rect.top;
                const relX = e.clientX - rect.left;
                const centerX = rect.width / 2;
                // Discovery zone: within 70px of the container top and within 180px of center
                const inZone = relY >= 0 && relY <= 70 && Math.abs(relX - centerX) <= 180;
                if (inZone) {
                    if (pagingHoverTimeout) {
                        clearTimeout(pagingHoverTimeout);
                        pagingHoverTimeout = null;
                    }
                    pagingControls.classList.add('hover-discovered');
                } else if (pagingControls.classList.contains('hover-discovered')) {
                    if (!pagingHoverTimeout) {
                        pagingHoverTimeout = setTimeout(() => {
                            pagingControls.classList.remove('hover-discovered');
                            pagingHoverTimeout = null;
                        }, 350);
                    }
                }
            });
            imgContainer.addEventListener('pointerleave', () => {
                if (pagingHoverTimeout) {
                    clearTimeout(pagingHoverTimeout);
                    pagingHoverTimeout = null;
                }
                pagingControls.classList.remove('hover-discovered');
            });
        }

        window.addEventListener('keydown', (e) => {
            const active = document.activeElement;
            if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) {
                return;
            }
            if (e.key === 'ArrowLeft') {
                if (typeof isCurrentOrator === 'function' && isCurrentOrator()) {
                    if (currentViewIndex > 0) {
                        void triggerOratorPreviousImage();
                    }
                } else if (currentViewIndex > 0) {
                    isNavigating = true;
                    isPagedBack = true;
                    displayHistoryImage(currentViewIndex - 1, 'none');
                }
            } else if (e.key === 'ArrowRight') {
                if (typeof isCurrentOrator === 'function' && isCurrentOrator()) {
                    if (currentViewIndex < imageHistory.length - 1) {
                        void triggerOratorNextImage();
                    }
                } else if (currentViewIndex < imageHistory.length - 1) {
                    isNavigating = true;
                    isPagedBack = true;
                    displayHistoryImage(currentViewIndex + 1, 'none');
                }
            }
        });

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
            if (disconnectedIndicator) {
                disconnectedIndicator.classList.toggle('active', isWandering);
                disconnectedIndicator.setAttribute('aria-hidden', String(!isWandering));
            }
            lastAgentThought = text;
        }

        // Doodle Logic with Full HiDPI Persistence & Replay
        const canvas = document.getElementById('doodle-canvas');
        const ctx = canvas ? canvas.getContext('2d') : null;
        const colorBtns = document.querySelectorAll('.color-btn');
        const clearBtn = document.getElementById('clear-doodle-btn');
        const undoBtn = document.getElementById('undo-doodle-btn');
        const brushSizeSlider = document.getElementById('brush-size');
        const doodleToolbar = document.getElementById('doodle-toolbar');
        const doodlesToggleBtn = document.getElementById('doodles-toggle-btn');
        const paletteToggle = document.getElementById('doodle-palette-toggle');
        const paletteMenu = document.getElementById('doodle-palette-menu');
        const textAnnotationToggle = document.getElementById('text-annotation-toggle');
        const textAnnotationMenu = document.getElementById('text-annotation-menu');
        const textAnnotationFont = document.getElementById('text-annotation-font');
        const textAnnotationSize = document.getElementById('text-annotation-size');
        const textAnnotationSizeValue = document.getElementById('text-annotation-size-value');
        const textAnnotationColors = document.querySelectorAll('.text-annotation-color');

        let currentColor = null;
        let lastSelectedTool = { type: 'marker', color: '#ef4444' };
        let currentBrushSize = 3;
        let isDrawing = false;
        let currentStrokeId = null;
        let lastNormX = 0;
        let lastNormY = 0;
        let doodlesEnabled = true;
        let displayedDoodlePageId = null;
        let doodlePageRequestSequence = 0;
        let lastGameCanvasRevision = null;
        let textAnnotationMode = false;
        let currentTextColor = '#ffffff';
        let activeTextEditor = null;

        // Stores all active doodle stroke segments for replay across window resizes
        let doodleActions = [];
        let doodleUndoStack = [];
        let activeDeleteTransaction = null;
        let pendingDrawBatch = null;
        let drawBatchFrame = null;
        // Keep drawing responsive even while reconnecting.  The queue
        // is flushed only after the server's initial snapshot arrives,
        // so that snapshot cannot overwrite locally drawn strokes.
        const pendingDoodleMessages = [];
        const inFlightDoodleMessages = new Map();
        const pendingTextEdits = new Map();
        let nextDoodleMessageId = 0;
        let ws = null;
        let doodleSocketReady = false;
        let doodleReconnectTimer = null;
        const DOODLE_RECONNECT_DELAY_MS = 5000;

        function updateUndoButtonState() {
            if (!undoBtn) return;
            const canUndo = (doodleUndoManager ? doodleUndoManager.canUndo() : (doodleUndoStack && doodleUndoStack.length > 0)) && doodlesEnabled && !isPagedBack;
            undoBtn.disabled = !canUndo;
            undoBtn.style.opacity = canUndo ? '1' : '0.4';
            undoBtn.style.cursor = canUndo ? 'pointer' : 'not-allowed';
        }

        function getDoodleUndoContext() {
            return {
                get doodleActions() { return doodleActions; },
                set doodleActions(val) { doodleActions = val; },
                doodleRenderer: typeof doodleRenderer !== 'undefined' ? doodleRenderer : null,
                redrawAllDoodles: typeof redrawAllDoodles === 'function' ? redrawAllDoodles : () => {},
                sendOrQueueDoodleMessage: typeof sendOrQueueDoodleMessage === 'function' ? sendOrQueueDoodleMessage : () => {},
                restoreSegmentsToServer: typeof restoreSegmentsToServer === 'function' ? restoreSegmentsToServer : () => {},
            };
        }

        const doodleUndoManager = typeof createDoodleUndoManager === 'function'
            ? createDoodleUndoManager({
                getStack: () => doodleUndoStack,
                getContext: getDoodleUndoContext,
                onStateChange: () => updateUndoButtonState(),
                isEnabled: () => doodlesEnabled && !isPagedBack
            })
            : null;

        function pushUndoAction(action) {
            if (doodleUndoManager) {
                doodleUndoManager.push(action);
            } else {
                const doodleAction = (action instanceof DoodleAction || (action && typeof action.undo === 'function'))
                    ? action
                    : (typeof DoodleAction !== 'undefined' ? DoodleAction.from(action) : action);
                if (!doodleAction) return;
                doodleUndoStack.push(doodleAction);
                if (doodleUndoStack.length > 100) doodleUndoStack.shift();
                if (typeof updateUndoButtonState === 'function') updateUndoButtonState();
            }
        }

        function undoLastDoodleAction() {
            if (doodleUndoManager) {
                return doodleUndoManager.undo();
            }
            if (doodleUndoStack.length === 0) return false;
            const action = doodleUndoStack.pop();
            if (typeof updateUndoButtonState === 'function') updateUndoButtonState();
            if (action && typeof action.undo === 'function') {
                const context = getDoodleUndoContext();
                return action.undo(context);
            }
            return false;
        }

        window.doodleUndoManager = doodleUndoManager;
        try {
            Object.defineProperty(window, 'doodleUndoStack', {
                get() { return doodleUndoManager ? doodleUndoManager.stack : doodleUndoStack; },
                set(val) {
                    if (doodleUndoManager) doodleUndoManager.stack = val;
                    doodleUndoStack = val;
                },
                configurable: true
            });
        } catch (_) {
            window.doodleUndoStack = doodleUndoStack;
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

        function renderDoodleBatch(data) {
            const points = data.points;
            if (!Array.isArray(points) || points.length < 4) return;
            const batchStrokeId = data.stroke_id || data.client_message_id || ((typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : ('batch-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7)));
            const rect = { width: canvas.clientWidth, height: canvas.clientHeight };
            for (let index = 0; index < points.length - 2; index += 2) {
                const action = {
                    type: 'draw',
                    stroke_id: batchStrokeId,
                    x0: points[index], y0: points[index + 1],
                    x1: points[index + 2], y1: points[index + 3],
                    color: data.color, size: data.size || 3
                };
                doodleActions.push(action);
                doodleRenderer.renderStrokeSegment(action);
            }
        }

        function displayPageDoodles(item) {
            const pageId = item.page_id || null;
            if (displayedDoodlePageId === pageId) return;
            flushDrawBatch();
            isDrawing = false;
            activeTextEditor?.remove();
            activeTextEditor = null;
            displayedDoodlePageId = pageId;
            doodleActions = [];
            if (typeof doodleUndoStack !== 'undefined') doodleUndoStack = [];
            if (typeof updateUndoButtonState === 'function') updateUndoButtonState();
            doodleRenderer?.clearSelectedSegments();
            setDoodlesDisplay(item.doodles_enabled !== false);
            redrawAllDoodles();
            void requestPageDoodles(pageId);
        }

        async function requestPageDoodles(pageId = displayedDoodlePageId) {
            if (!pageId) return;
            const sequence = ++doodlePageRequestSequence;
            if (ws && ws.readyState === WebSocket.OPEN) {
                doodleSocketReady = false;
                ws.send(JSON.stringify({type: 'subscribe_doodles', page_id: pageId}));
                return;
            }
            try {
                const params = new URLSearchParams({theater_id: currenttheaterId, page_id: pageId});
                const response = await fetch('/api/doodles?' + params);
                if (!response.ok) throw new Error(`Unable to load page doodles (${response.status}).`);
                const snapshot = await response.json();
                if (sequence === doodlePageRequestSequence) applyDoodleSnapshot(snapshot);
            } catch (error) {
                console.warn('Could not load page doodles.', error);
            }
        }

        function applyDoodleSnapshot(data) {
            if (data.page_id && displayedDoodlePageId && data.page_id !== displayedDoodlePageId) return;
            displayedDoodlePageId = data.page_id || displayedDoodlePageId;
            doodleActions = [];
            (data.stamps || []).forEach(action => doodleActions.push(action));
            (data.batches || []).forEach(renderDoodleBatch);
            (data.annotations || []).forEach(action => doodleActions.push(action));
            if (data.enabled !== undefined) setDoodlesDisplay(data.enabled);
            redrawAllDoodles();
        }

        function flushDrawBatch() {
            drawBatchFrame = null;
            if (!pendingDrawBatch) return;
            const batch = pendingDrawBatch;
            pendingDrawBatch = null;
            sendOrQueueDoodleMessage(batch);
        }

        function isActiveCanvasWindow() {
            return document.visibilityState === 'visible' && document.hasFocus();
        }

        function sendOrQueueDoodleMessage(message) {
            if (message.page_id === undefined && displayedDoodlePageId) {
                message.page_id = displayedDoodlePageId;
            }
            if (!message.client_message_id) {
                nextDoodleMessageId += 1;
                message.client_message_id = `${Date.now()}-${nextDoodleMessageId}`;
            }
            if (ws && ws.readyState === WebSocket.OPEN && doodleSocketReady) {
                try {
                    ws.send(JSON.stringify(message));
                    inFlightDoodleMessages.set(message.client_message_id, message);
                } catch (_) {
                    pendingDoodleMessages.push(message);
                    scheduleDoodleReconnect(0);
                }
                return;
            }
            pendingDoodleMessages.push(message);
            scheduleDoodleReconnect(0);
        }

        function flushQueuedDoodleMessages() {
            if (!ws || ws.readyState !== WebSocket.OPEN || !doodleSocketReady) return;
            const queuedMessages = pendingDoodleMessages.splice(0);
            for (const message of queuedMessages) {
                sendOrQueueDoodleMessage(message);
                if (message.page_id && message.page_id !== displayedDoodlePageId) continue;
                if (message.type === 'draw_batch') renderDoodleBatch(message);
                if (message.type === 'clear') {
                    doodleActions = [];
                    redrawAllDoodles();
                }
                if (message.type === 'toggle_doodles') setDoodlesDisplay(message.enabled);
                if (message.type === 'text') applyTextAnnotation(message);
                if (message.type === 'stamp') applyStampAnnotation(message);
                if (message.type === 'remove_stamp') {
                    const idx = doodleActions.findIndex(a => a.type === 'stamp' && a.id === message.id);
                    if (idx >= 0) {
                        doodleActions.splice(idx, 1);
                        redrawAllDoodles();
                    }
                }
                if (message.type === 'remove_text') {
                    const idx = doodleActions.findIndex(a => a.type === 'text' && a.id === message.id);
                    if (idx >= 0) {
                        doodleActions.splice(idx, 1);
                        redrawAllDoodles();
                    }
                }
                if (message.type === 'remove_draw') {
                    const strokeId = message.stroke_id;
                    const ids = Array.isArray(message.ids) ? new Set(message.ids) : null;
                    doodleActions = doodleActions.filter(a => {
                        if (a.type !== 'draw') return true;
                        if (strokeId && a.stroke_id === strokeId) return false;
                        if (ids && a.id && ids.has(a.id)) return false;
                        return true;
                    });
                    redrawAllDoodles();
                }
            }
        }

        function scheduleDoodleReconnect(delay = DOODLE_RECONNECT_DELAY_MS) {
            // A closed socket does not need to be revived merely to
            // redraw server-persisted strokes. Reconnect only when
            // this browser has local doodle changes to deliver.
            if (!pendingDoodleMessages.length || doodleReconnectTimer || !isActiveCanvasWindow()) return;
            doodleReconnectTimer = setTimeout(() => {
                doodleReconnectTimer = null;
                connectDoodleSocket();
            }, delay);
        }

        function setDoodlesDisplay(enabled) {
            doodlesEnabled = Boolean(enabled);
            if (canvas) {
                canvas.style.display = doodlesEnabled ? 'block' : 'none';
                if (doodlesEnabled) doodleRenderer.resize(doodleActions);
            }
            if (doodleToolbar) {
                doodleToolbar.style.display = doodlesEnabled ? 'flex' : 'none';
            }
            if (doodlesToggleBtn) {
                doodlesToggleBtn.textContent = doodlesEnabled ? '✏️ Doodles: On' : '✏️ Doodles: Off';
                doodlesToggleBtn.title = doodlesEnabled ? 'Hide doodles for this page.' : 'Show doodles for this page.';
                doodlesToggleBtn.style.color = doodlesEnabled ? '#a5b4fc' : '#94a3b8';
                doodlesToggleBtn.style.background = doodlesEnabled ? 'rgba(99, 102, 241, 0.2)' : 'rgba(255, 255, 255, 0.08)';
                doodlesToggleBtn.style.borderColor = doodlesEnabled ? 'rgba(99, 102, 241, 0.4)' : 'var(--glass-border)';
            }
            updateTextAnnotationAvailability();
            if (typeof updateUndoButtonState === 'function') updateUndoButtonState();
            refreshCanvasToolInteraction();
            if (typeof isStampManagerOpen !== 'undefined' && isStampManagerOpen) {
                updateStampManagerPermissionBanner();
                if (cachedUserStamps) renderStampManagerGrid(cachedUserStamps);
            }
        }

        if (doodlesToggleBtn) {
            doodlesToggleBtn.addEventListener('click', () => {
                if (!isCurrentOrator()) return;
                const newEnabled = !doodlesEnabled;
                setDoodlesDisplay(newEnabled);
                sendOrQueueDoodleMessage({ type: 'toggle_doodles', enabled: newEnabled });
            });
        }

        if (brushSizeSlider) {
            brushSizeSlider.addEventListener('input', (e) => {
                currentBrushSize = parseInt(e.target.value);
            });
        }

        const doodleRenderer = createDoodleRenderer({
            canvas,
            getPage: () => imageHistory[currentViewIndex]?.canvas_page || null,
            isVisible: () => doodlesEnabled,
            textLayer: document.getElementById('canvas-text-layer'),
            canEditText: () => canAddTextAnnotation() && !isPagedBack && canvasCursorState.current.type === 'select',
            onEditText: action => openTextAnnotationEditor(action),
            onMoveText: (action, previous, commit) => moveTextAnnotation(action, previous, commit),
            stampLayer: document.getElementById('canvas-stamp-layer'),
            canMoveStamp: () => canPlaceStamps() && !isPagedBack && canvasCursorState.current.type === 'select',
            onMoveStamp: (action, previous, commit) => moveStampAnnotation(action, previous, commit),
            onRemoveStamp: action => removeStampAnnotation(action),
            onSelectStamp: action => selectStampAnnotation(action),
            onRemoveText: action => removeTextAnnotation(action),
            canSelectSegment: () => doodlesEnabled && !isPagedBack && canvasCursorState.current.type === 'select',
            onRemoveSegment: (removed) => {
                const removedSet = new Set((removed || []).map(r => r.id || r));
                const removedSegments = doodleActions.filter(a => removedSet.has(a.id) || removedSet.has(a));
                doodleActions = doodleActions.filter(a => !removedSet.has(a.id) && !removedSet.has(a));
                if (removedSegments.length > 0) {
                    if (typeof activeDeleteTransaction !== 'undefined' && activeDeleteTransaction) {
                        activeDeleteTransaction.segments.push(...removedSegments);
                    } else if (typeof pushUndoAction === 'function') {
                        pushUndoAction(typeof DeleteSegmentsDoodleAction === 'function'
                            ? new DeleteSegmentsDoodleAction(removedSegments)
                            : {
                                type: 'delete_segments',
                                segments: removedSegments
                            });
                    }
                    sendOrQueueDoodleMessage({
                        type: 'remove_draw',
                        ids: removedSegments.map(s => s.id).filter(Boolean)
                    });
                }
                redrawAllDoodles();
            },
        });
        const resizeDoodleCanvas = () => doodleRenderer.resize(doodleActions);
        const redrawAllDoodles = () => doodleRenderer.redraw(doodleActions);
        const renderSegment = doodleRenderer.renderSegment;
        const renderTextAnnotation = doodleRenderer.renderText;

        window.addEventListener('resize', resizeDoodleCanvas);

        function setPaletteOpen(open) {
            if (!paletteToggle || !paletteMenu) return;
            paletteToggle.setAttribute('aria-expanded', String(open));
            paletteMenu.classList.toggle('open', open);
            paletteMenu.hidden = !open;
        }

        function canAddTextAnnotation() {
            return doodlesEnabled && (isCurrentOrator() || isCollabModeEnabled());
        }

        function setTextAnnotationMenuOpen(open) {
            if (!textAnnotationToggle || !textAnnotationMenu) return;
            textAnnotationToggle.setAttribute('aria-expanded', String(open));
            textAnnotationMenu.classList.toggle('open', open);
            textAnnotationMenu.hidden = !open;
        }

        function refreshCanvasToolInteraction() {
            const textLayer = document.getElementById('canvas-text-layer');
            if (textLayer) {
                textLayer.hidden = !doodlesEnabled;
                textLayer.style.zIndex = currentColor !== null ? '4' : '6';
            }
            const stampLayer = document.getElementById('canvas-stamp-layer');
            if (stampLayer) {
                stampLayer.hidden = !doodlesEnabled;
                stampLayer.style.zIndex = '4';
            }
            const hasCanvasTool = currentColor !== null || textAnnotationMode;
            const canvasInteractive = hasCanvasTool && doodlesEnabled && !isPagedBack;
            canvas.classList.toggle('drawing-disabled', !canvasInteractive);
            canvas.style.pointerEvents = canvasInteractive ? 'auto' : 'none';
            canvas.style.cursor = canvasInteractive
                ? (textAnnotationMode ? 'text' : 'crosshair')
                : 'default';
            narrationPane.setDrawingColorSelected(canvasInteractive);
        }

        function applyCanvasCursorState(state) {
            // These values are projections for drawing code; only this function updates them.
            currentColor = state.type === 'marker' ? state.color : null;
            textAnnotationMode = state.type === 'text';
            imgContainer.dataset.cursorMode = state.type;
            isDrawing = false;
            currentStrokeId = null;
            flushDrawBatch();
            activeTextEditor?.remove();
            activeTextEditor = null;
            if (state.type === 'marker') lastSelectedTool = { type: 'marker', color: state.color };
            if (textAnnotationMode) lastSelectedTool = { type: 'text' };
            doodleRenderer.cancelInteraction();
            colorBtns.forEach(button => {
                const active = state.type === 'marker' && button.dataset.color === state.color;
                button.classList.toggle('active', active);
                button.setAttribute('aria-pressed', String(active));
            });
            textAnnotationToggle?.classList.toggle('active', textAnnotationMode);
            textAnnotationToggle?.setAttribute('aria-pressed', String(textAnnotationMode));
            setTextAnnotationMenuOpen(false);
            pagingControls?.classList.remove('canvas-selected');
            refreshCanvasToolInteraction();
        }

        function setTextAnnotationMode(active, openMenu = active) {
            if (active && !canAddTextAnnotation()) return;
            canvasCursorState.select({ type: active ? 'text' : 'select' });
            setTextAnnotationMenuOpen(Boolean(openMenu && textAnnotationMode));
        }

        function updateTextAnnotationAvailability() {
            if (!textAnnotationToggle) return;
            const available = canAddTextAnnotation();
            textAnnotationToggle.disabled = !available;
            textAnnotationToggle.title = available
                ? 'Add text annotation'
                : 'Text annotations require collaboration mode for viewers';
            if (!available && textAnnotationMode) {
                activeTextEditor?.remove();
                activeTextEditor = null;
                setTextAnnotationMode(false, false);
            }
        }

        function setDrawingColor(color) {
            canvasCursorState.select(color ? { type: 'marker', color } : { type: 'select' });
        }

        function cycleCanvasCursor() {
            const current = canvasCursorState.current.type;
            if (current === 'marker' || current === 'text') {
                canvasCursorState.select({ type: 'select' });
            } else if (current === 'select') {
                canvasCursorState.select({ type: 'navigate' });
            } else if (!doodlesEnabled) {
                canvasCursorState.select({ type: 'select' });
            } else if (lastSelectedTool.type === 'text' && canAddTextAnnotation()) {
                setTextAnnotationMode(true, false);
            } else {
                const color = lastSelectedTool.type === 'marker' ? lastSelectedTool.color : '#ef4444';
                setDrawingColor(color);
            }
        }

        document.addEventListener('click', (event) => {
            if (!imgContainer) return;
            const clickedCanvas = imgContainer.contains(event.target);
            const clickedDrawingControls = doodleToolbar?.contains(event.target);
            if (!clickedCanvas && !clickedDrawingControls && (currentColor !== null || textAnnotationMode)) {
                setDrawingColor(null);
            }
            pagingControls?.classList.toggle('canvas-selected', clickedCanvas && canvasCursorState.current.type === 'select');
            if (textAnnotationMode) pagingControls?.classList.remove('canvas-selected');
        });

        document.addEventListener('contextmenu', (event) => {
            if (!imgContainer) return;
            const clickedCanvas = imgContainer === event.target || imgContainer.contains(event.target);
            if (!clickedCanvas) return;
            if (event.target.closest('button, a, select, [role="dialog"], #orator-command-overlay, #doodle-toolbar, #history-paging-controls')) {
                return;
            }
            if (isCurrentOrator() && actionWheel && typeof actionWheel.isRightClickBound === 'function' && actionWheel.isRightClickBound()) {
                return;
            }
            event.preventDefault();
            cycleCanvasCursor();
        });

        textAnnotationToggle?.addEventListener('click', () => {
            if (!canAddTextAnnotation()) return;
            setTextAnnotationMode(!textAnnotationMode, !textAnnotationMode);
        });

        textAnnotationSize?.addEventListener('input', () => {
            if (textAnnotationSizeValue) textAnnotationSizeValue.textContent = `${textAnnotationSize.value} px`;
        });

        textAnnotationColors.forEach(button => {
            button.addEventListener('click', () => {
                currentTextColor = button.dataset.color || '#ffffff';
                textAnnotationColors.forEach(option => {
                    const selected = option === button;
                    option.classList.toggle('active', selected);
                    option.setAttribute('aria-pressed', String(selected));
                });
                if (activeTextEditor) activeTextEditor.style.color = currentTextColor;
            });
        });

        if (paletteToggle) {
            paletteToggle.addEventListener('click', () => {
                setPaletteOpen(paletteToggle.getAttribute('aria-expanded') !== 'true');
            });
        }

        document.addEventListener('pointerdown', (event) => {
            if (paletteMenu && !paletteMenu.hidden && !event.target.closest('.doodle-palette')) {
                setPaletteOpen(false);
            }
            if (textAnnotationMenu && !textAnnotationMenu.hidden && !event.target.closest('.text-annotation-tool')) {
                setTextAnnotationMenuOpen(false);
            }
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && paletteMenu && !paletteMenu.hidden) {
                setPaletteOpen(false);
                paletteToggle?.focus();
            } else if (event.key === 'Escape' && textAnnotationMenu && !textAnnotationMenu.hidden) {
                setTextAnnotationMenuOpen(false);
                textAnnotationToggle?.focus();
            }
        });

        colorBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                const nextColor = btn.classList.contains('active') ? null : btn.dataset.color;
                setDrawingColor(nextColor);
                setPaletteOpen(false);
            });
        });
        canvasCursorState.subscribe(applyCanvasCursorState);
        applyCanvasCursorState(canvasCursorState.current);

        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${wsProtocol}//${window.location.host}/ws/doodle` + (currenttheaterId ? `?theater_id=${encodeURIComponent(currenttheaterId)}` : '');
        function connectDoodleSocket(initialConnection = false) {
            // The first connection must not depend on focus: on a
            // newly opened page, the script can run before the window
            // has received focus. Subsequent retries stay limited to
            // an active window by scheduleDoodleReconnect.
            if ((!initialConnection && !isActiveCanvasWindow()) || (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING))) return;
            const socketPageId = displayedDoodlePageId;
            const socketUrl = socketPageId ? wsUrl + '&page_id=' + encodeURIComponent(socketPageId) : wsUrl;
            const socket = new WebSocket(socketUrl);
            ws = socket;
            doodleSocketReady = false;

            socket.onopen = () => {
                // Reconnect to the page being browsed, which may differ from the orator.
                if (displayedDoodlePageId && displayedDoodlePageId !== socketPageId) void requestPageDoodles();
            };
            socket.onclose = () => {
                if (ws === socket) {
                    doodleSocketReady = false;
                    for (const message of inFlightDoodleMessages.values()) {
                        pendingDoodleMessages.push(message);
                    }
                    inFlightDoodleMessages.clear();
                    scheduleDoodleReconnect();
                }
            };
            socket.onerror = () => socket.close();
            socket.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (typeof handleDoodleSocketData === 'function' && handleDoodleSocketData(data)) return;
                if (data.page_id && displayedDoodlePageId && data.page_id !== displayedDoodlePageId) return;
                if (data.type === 'doodles_toggle') {
                    setDoodlesDisplay(data.enabled);
                } else if (data.type === 'clear') {
                    doodleActions = [];
                    redrawAllDoodles();
                } else if (data.type === 'doodle_snapshot') {
                    doodlePageRequestSequence += 1;
                    applyDoodleSnapshot(data);
                    doodleSocketReady = true;
                    flushQueuedDoodleMessages();
                } else if (data.type === 'doodle_ack') {
                    inFlightDoodleMessages.delete(data.client_message_id);
                    pendingTextEdits.delete(data.client_message_id);
                } else if (data.type === 'text_annotation_rejected' || data.type === 'stamp_rejected') {
                    inFlightDoodleMessages.delete(data.client_message_id);
                    const index = doodleActions.findIndex(action => action.client_message_id === data.client_message_id);
                    if (index >= 0) {
                        const previous = pendingTextEdits.get(data.client_message_id);
                        if (previous) doodleActions[index] = previous;
                        else doodleActions.splice(index, 1);
                        pendingTextEdits.delete(data.client_message_id);
                        redrawAllDoodles();
                    }
                } else if (data.type === 'draw_batch') {
                    renderDoodleBatch(data);
                } else if (data.type === 'draw') {
                    if (!data.stroke_id) {
                        data.stroke_id = data.client_message_id || ((typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : ('stroke-' + Date.now()));
                    }
                    doodleActions.push(data);
                    const rect = { width: canvas.clientWidth, height: canvas.clientHeight };
                    renderSegment(
                        data.x0 * rect.width, data.y0 * rect.height,
                        data.x1 * rect.width, data.y1 * rect.height,
                        data.color, data.size || 3
                    );
                } else if (data.type === 'text') {
                    applyTextAnnotation(data);
                } else if (data.type === 'stamp') {
                    applyStampAnnotation(data);
                } else if (data.type === 'select_stamp') {
                    bringStampToFront(data.id);
                } else if (data.type === 'remove_stamp') {
                    const idx = doodleActions.findIndex(a => a.type === 'stamp' && a.id === data.id);
                    if (idx >= 0) {
                        doodleActions.splice(idx, 1);
                        redrawAllDoodles();
                    }
                } else if (data.type === 'remove_text') {
                    const idx = doodleActions.findIndex(a => a.type === 'text' && a.id === data.id);
                    if (idx >= 0) {
                        doodleActions.splice(idx, 1);
                        redrawAllDoodles();
                    }
                } else if (data.type === 'remove_draw') {
                    const strokeId = data.stroke_id;
                    const ids = Array.isArray(data.ids) ? new Set(data.ids) : null;
                    doodleActions = doodleActions.filter(a => {
                        if (a.type !== 'draw') return true;
                        if (strokeId && a.stroke_id === strokeId) return false;
                        if (ids && a.id && ids.has(a.id)) return false;
                        return true;
                    });
                    redrawAllDoodles();
                } else if (data.type === 'toggle_mic') {
                    if (isCurrentOrator() && agentConnectBtn && !agentConnectBtn.disabled) {
                        agentConnectBtn.click();
                    }
                } else if (data.type === 'baton_state') {
                    if (typeof updateBatonStateUI === 'function') {
                        updateBatonStateUI(data.baton_state, data.active_viewers);
                    }
                }
            };
        }

        document.addEventListener('visibilitychange', () => {
            if (isActiveCanvasWindow()) scheduleDoodleReconnect(0);
        });
        window.addEventListener('focus', () => scheduleDoodleReconnect(0));
        connectDoodleSocket(true);

        function drawLine(nx0, ny0, nx1, ny1, color, size, emit) {
            const strokeId = currentStrokeId || (typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : ('stroke-' + Date.now()));
            const action = {
                type: 'draw',
                stroke_id: strokeId,
                x0: nx0,
                y0: ny0,
                x1: nx1,
                y1: ny1,
                color: color,
                size: size
            };

            doodleActions.push(action);
            const rect = { width: canvas.clientWidth, height: canvas.clientHeight };
            doodleRenderer.renderStrokeSegment(action);

            if (!emit) return;

            if (!pendingDrawBatch || pendingDrawBatch.color !== color || pendingDrawBatch.size !== size ||
                pendingDrawBatch.stroke_id !== strokeId ||
                pendingDrawBatch.points[pendingDrawBatch.points.length - 2] !== nx0 ||
                pendingDrawBatch.points[pendingDrawBatch.points.length - 1] !== ny0) {
                flushDrawBatch();
                pendingDrawBatch = { type: 'draw_batch', stroke_id: strokeId, page_id: displayedDoodlePageId, color, size, points: [nx0, ny0] };
            }
            pendingDrawBatch.points.push(nx1, ny1);
            if (!drawBatchFrame) drawBatchFrame = requestAnimationFrame(flushDrawBatch);
        }

        function getNormalizedCoords(e) {
            return doodleRenderer.normalizedPoint(e);
        }

        function beginTextAnnotation(event) {
            if (!textAnnotationMode || !canAddTextAnnotation() || event.target !== canvas) return;
            event.preventDefault();
            openTextAnnotationEditor(null, getNormalizedCoords(event));
        }

        function applyTextAnnotation(action) {
            const index = doodleActions.findIndex(existing => existing.type === 'text' && existing.id === action.id);
            if (index >= 0) doodleActions[index] = action;
            else doodleActions.push(action);
            redrawAllDoodles();
        }

        function moveTextAnnotation(action, previous, commit) {
            if (commit) {
                action = { ...action };
                delete action.client_message_id;
                sendOrQueueDoodleMessage(action);
                pendingTextEdits.set(action.client_message_id, previous);
                if (previous && (previous.x !== action.x || previous.y !== action.y)) {
                    if (typeof pushUndoAction === 'function') {
                        pushUndoAction(typeof MoveTextDoodleAction === 'function'
                            ? new MoveTextDoodleAction(previous, action)
                            : {
                                type: 'move_text',
                                before: { ...previous },
                                after: { ...action }
                            });
                    }
                }
            }
            applyTextAnnotation(action);
        }

        function bringStampToFront(stampId) {
            const index = doodleActions.findIndex(
                existing => existing.type === 'stamp' && existing.id === stampId
            );
            if (index >= 0) {
                const [selected] = doodleActions.splice(index, 1);
                const firstNonStamp = doodleActions.findIndex(existing => existing.type !== 'stamp');
                if (firstNonStamp >= 0) doodleActions.splice(firstNonStamp, 0, selected);
                else doodleActions.push(selected);
            }
            const stampLayer = document.getElementById('canvas-stamp-layer');
            if (stampLayer) {
                const el = Array.from(stampLayer.children).find(node => node.dataset.annotationId === stampId);
                if (el && stampLayer.lastElementChild !== el) {
                    stampLayer.appendChild(el);
                }
            }
        }

        function selectStampAnnotation(action) {
            bringStampToFront(action.id);
            if (action && action.stamp_id) {
                recordStampUsage(action.stamp_id);
            }
            sendOrQueueDoodleMessage({ type: 'select_stamp', id: action.id });
        }

        function isSameStampKind(item, action) {
            if (!item || !action || item.type !== 'stamp' || action.type !== 'stamp') return false;
            if (item.id && action.id && String(item.id) === String(action.id)) return true;
            if (item.user_id && action.user_id && String(item.user_id) === String(action.user_id)) {
                if (item.stamp_id && action.stamp_id && String(item.stamp_id) === String(action.stamp_id)) return true;
            }
            const itemIsTheaterOrGame = item.managed_by === 'game' ||
                (item.stamp_id && String(item.stamp_id).startsWith('theater:')) ||
                (item.canvas_asset_id && String(item.canvas_asset_id).startsWith('stamp:'));
            const actionIsTheaterOrGame = action.managed_by === 'game' ||
                (action.stamp_id && String(action.stamp_id).startsWith('theater:')) ||
                (action.canvas_asset_id && String(action.canvas_asset_id).startsWith('stamp:'));

            if (itemIsTheaterOrGame || actionIsTheaterOrGame) {
                if (item.stamp_id && action.stamp_id && String(item.stamp_id) === String(action.stamp_id)) return true;
                if (item.canvas_asset_id && action.canvas_asset_id && String(item.canvas_asset_id) === String(action.canvas_asset_id)) return true;
                const getStem = (d) => {
                    const assetId = d.canvas_asset_id ? String(d.canvas_asset_id) : '';
                    if (assetId.startsWith('stamp:')) return assetId.replace(/^stamp:/, '');
                    const sid = d.stamp_id ? String(d.stamp_id) : '';
                    if (sid.startsWith('theater:')) {
                        const parts = sid.split(':');
                        const last = parts[parts.length - 1];
                        return last.replace(/\.[^/.]+$/, '');
                    }
                    if (d.managed_by === 'game') return String(d.name || '');
                    return '';
                };
                const itemStem = getStem(item);
                const actionStem = getStem(action);
                if (itemStem && actionStem && itemStem === actionStem) return true;
            }
            return false;
        }

        function applyStampAnnotation(action) {
            const index = doodleActions.findIndex(
                existing => isSameStampKind(existing, action)
            );
            if (index >= 0) doodleActions.splice(index, 1);
            const firstNonStamp = doodleActions.findIndex(existing => existing.type !== 'stamp');
            if (firstNonStamp >= 0) doodleActions.splice(firstNonStamp, 0, action);
            else doodleActions.push(action);
            redrawAllDoodles();
        }

        function moveStampAnnotation(action, previous, commit) {
            if (commit) {
                if (action && action.stamp_id) {
                    recordStampUsage(action.stamp_id);
                }
                action = { ...action };
                delete action.client_message_id;
                sendOrQueueDoodleMessage(action);
                if (action.client_message_id) pendingTextEdits.set(action.client_message_id, previous);
                if (previous) {
                    const moved = previous.x !== action.x || previous.y !== action.y;
                    const resized = previous.size !== action.size;
                    const rotated = previous.rotation !== action.rotation;
                    if (moved || resized || rotated) {
                        if (typeof pushUndoAction === 'function') {
                            const transformType = resized && !moved && !rotated ? 'resize_stamp'
                                : rotated && !moved && !resized ? 'rotate_stamp'
                                : 'transform_stamp';
                            pushUndoAction(typeof TransformStampDoodleAction === 'function'
                                ? new TransformStampDoodleAction(previous, action, transformType)
                                : {
                                    type: transformType,
                                    before: { ...previous },
                                    after: { ...action }
                                });
                        }
                    }
                }
            }
            applyStampAnnotation(action);
        }

        function removeStampAnnotation(action) {
            const index = doodleActions.findIndex(
                existing => existing.type === 'stamp' && existing.id === action.id
            );
            if (index >= 0) {
                const [removed] = doodleActions.splice(index, 1);
                sendOrQueueDoodleMessage({ type: 'remove_stamp', id: action.id });
                if (typeof activeDeleteTransaction !== 'undefined' && activeDeleteTransaction) {
                    activeDeleteTransaction.stamps.push(removed);
                } else if (typeof pushUndoAction === 'function') {
                    pushUndoAction(typeof DeleteStampDoodleAction === 'function'
                        ? new DeleteStampDoodleAction([removed])
                        : {
                            type: 'delete_stamp',
                            items: [removed]
                        });
                }
                redrawAllDoodles();
            }
        }

        function removeTextAnnotation(action) {
            const list = Array.isArray(action) ? action : (action ? [action] : []);
            const removedItems = [];
            list.forEach(item => {
                const annotationId = item?.id || item;
                const index = doodleActions.findIndex(
                    existing => existing.type === 'text' && existing.id === annotationId
                );
                if (index >= 0) {
                    const [removed] = doodleActions.splice(index, 1);
                    removedItems.push(removed);
                    sendOrQueueDoodleMessage({ type: 'remove_text', id: annotationId });
                }
            });
            if (removedItems.length > 0) {
                if (typeof activeDeleteTransaction !== 'undefined' && activeDeleteTransaction) {
                    activeDeleteTransaction.texts.push(...removedItems);
                } else if (typeof pushUndoAction === 'function') {
                    pushUndoAction(typeof DeleteTextDoodleAction === 'function'
                        ? new DeleteTextDoodleAction(removedItems)
                        : {
                            type: 'delete_text',
                            items: removedItems
                        });
                }
            }
            redrawAllDoodles();
        }

        function openTextAnnotationEditor(existing, placement = existing) {
            if (!canAddTextAnnotation() || isPagedBack) return;
            stopSceneSpeech();
            activeTextEditor?.remove();

            const coords = placement;
            const editor = document.createElement('textarea');
            editor.rows = 1;
            editor.value = existing?.text || '';
            editor.maxLength = 240;
            editor.className = 'canvas-text-editor';
            editor.setAttribute('aria-label', 'Text annotation');
            editor.placeholder = 'Type annotation…';
            const plane = typeof imageHistory !== 'undefined' && imageHistory[currentViewIndex]?.canvas_page
                ? doodleRenderer.imagePlane(existing) : null;
            editor.style.left = plane ? `${plane.x + coords.x * plane.width}px` : `${coords.x * 100}%`;
            editor.style.top = plane ? `${plane.y + coords.y * plane.height}px` : `${coords.y * 100}%`;
            editor.style.fontFamily = `"${existing?.font || textAnnotationFont?.value || 'Outfit'}", sans-serif`;
            const editingSize = Number(existing?.size || textAnnotationSize?.value || 32);
            editor.style.fontSize = `${plane ? editingSize * plane.width / 1000 : editingSize}px`;
            editor.style.color = existing?.color || currentTextColor;
            imgContainer.appendChild(editor);
            activeTextEditor = editor;

            let finished = false;
            const finish = (commit) => {
                if (finished) return;
                finished = true;
                const text = editor.value.trim();
                if (commit && text && canAddTextAnnotation()) {
                    const action = {
                        id: existing?.id || ((typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : ('text-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7))),
                        type: 'text', x: coords.x, y: coords.y, text,
                        color: existing?.color || currentTextColor,
                        size: Number(existing?.size || textAnnotationSize?.value || 32),
                        font: existing?.font || textAnnotationFont?.value || 'Outfit',
                    };
                    sendOrQueueDoodleMessage(action);
                    if (existing) pendingTextEdits.set(action.client_message_id, existing);
                    applyTextAnnotation(action);
                    if (typeof pushUndoAction === 'function') {
                        if (existing) {
                            pushUndoAction(typeof EditTextDoodleAction === 'function'
                                ? new EditTextDoodleAction(existing, action)
                                : {
                                    type: 'edit_text',
                                    before: { ...existing },
                                    after: { ...action }
                                });
                        } else {
                            pushUndoAction(typeof PlaceTextDoodleAction === 'function'
                                ? new PlaceTextDoodleAction(action)
                                : {
                                    type: 'place_text',
                                    action: { ...action }
                                });
                        }
                    }
                }
                editor.remove();
                if (activeTextEditor === editor) activeTextEditor = null;
            };
            editor.addEventListener('keydown', (keyEvent) => {
                if (keyEvent.key === 'Enter' && !keyEvent.shiftKey) {
                    keyEvent.preventDefault();
                    finish(true);
                } else if (keyEvent.key === 'Escape') {
                    keyEvent.preventDefault();
                    finish(false);
                    canvas.focus();
                }
            });
            editor.addEventListener('blur', () => finish(true), { once: true });
            editor.focus();
            editor.select();
        }

        canvas.addEventListener('click', beginTextAnnotation);

        function endDrawingStroke() {
            if (!isDrawing && !currentStrokeId) return;
            const strokeId = currentStrokeId;
            isDrawing = false;
            currentStrokeId = null;
            flushDrawBatch();
            if (strokeId) {
                const strokeSegments = doodleActions.filter(a => a.type === 'draw' && a.stroke_id === strokeId);
                if (strokeSegments.length > 0) {
                    if (typeof pushUndoAction === 'function') {
                        pushUndoAction(typeof DrawStrokeDoodleAction === 'function'
                            ? new DrawStrokeDoodleAction(strokeId, strokeSegments)
                            : {
                                type: 'draw_stroke',
                                stroke_id: strokeId,
                                segments: [...strokeSegments]
                            });
                    }
                }
            }
        }

        canvas.addEventListener('mousedown', (e) => {
            if (e.button !== 0) return;
            if (!doodlesEnabled || !currentColor) return;
            isDrawing = true;
            currentStrokeId = (typeof crypto !== 'undefined' && crypto.randomUUID)
                ? crypto.randomUUID()
                : ('stroke-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7));
            const coords = getNormalizedCoords(e);
            lastNormX = coords.x;
            lastNormY = coords.y;
        });

        canvas.addEventListener('mousemove', (e) => {
            if (!isDrawing || !doodlesEnabled || !currentColor) return;
            const coords = getNormalizedCoords(e);
            drawLine(lastNormX, lastNormY, coords.x, coords.y, currentColor, currentBrushSize, true);
            lastNormX = coords.x;
            lastNormY = coords.y;
        });

        canvas.addEventListener('mouseup', endDrawingStroke);
        canvas.addEventListener('mouseout', endDrawingStroke);

        // Touch support for tablets/mobile
        canvas.addEventListener('touchstart', (e) => {
            if (!doodlesEnabled || !currentColor) return;
            e.preventDefault();
            isDrawing = true;
            currentStrokeId = (typeof crypto !== 'undefined' && crypto.randomUUID)
                ? crypto.randomUUID()
                : ('stroke-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7));
            const coords = getNormalizedCoords(e);
            lastNormX = coords.x;
            lastNormY = coords.y;
        }, { passive: false });

        canvas.addEventListener('touchmove', (e) => {
            if (!isDrawing || !doodlesEnabled || !currentColor) return;
            e.preventDefault();
            const coords = getNormalizedCoords(e);
            drawLine(lastNormX, lastNormY, coords.x, coords.y, currentColor, currentBrushSize, true);
            lastNormX = coords.x;
            lastNormY = coords.y;
        }, { passive: false });

        canvas.addEventListener('touchend', endDrawingStroke);

        clearBtn.addEventListener('click', () => {
            endDrawingStroke();
            activeTextEditor?.remove();
            activeTextEditor = null;
            for (let index = pendingDoodleMessages.length - 1; index >= 0; index--) {
                if (pendingDoodleMessages[index].page_id === displayedDoodlePageId) pendingDoodleMessages.splice(index, 1);
            }
            for (const [id, message] of inFlightDoodleMessages) {
                if (message.page_id === displayedDoodlePageId) inFlightDoodleMessages.delete(id);
            }
            doodleRenderer?.clearSelectedSegments();
            const previous = [...doodleActions];
            if (previous.length > 0) {
                if (typeof pushUndoAction === 'function') {
                    pushUndoAction(typeof ClearCanvasDoodleAction === 'function'
                        ? new ClearCanvasDoodleAction(previous)
                        : {
                            type: 'clear',
                            previousActions: previous
                        });
                }
            }
            doodleActions = [];
            redrawAllDoodles();
            sendOrQueueDoodleMessage({ type: 'clear' });
        });

        undoBtn?.addEventListener('click', () => {
            if (!doodlesEnabled || isPagedBack) return;
            undoLastDoodleAction();
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Delete' || event.key === 'Backspace') {
                const active = document.activeElement;
                if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) return;
                activeDeleteTransaction = {
                    type: 'compound_delete',
                    segments: [],
                    stamps: [],
                    texts: []
                };
                queueMicrotask(() => {
                    if (activeDeleteTransaction) {
                        const tx = activeDeleteTransaction;
                        activeDeleteTransaction = null;
                        if (tx.segments.length > 0 || tx.stamps.length > 0 || tx.texts.length > 0) {
                            if (typeof pushUndoAction === 'function') {
                                pushUndoAction(typeof CompoundDeleteDoodleAction === 'function'
                                    ? new CompoundDeleteDoodleAction(tx.segments, tx.stamps, tx.texts)
                                    : tx);
                            }
                        }
                    }
                });
            }
        }, true);

        window.addEventListener('keydown', (event) => {
            const isZ = event.key === 'z' || event.key === 'Z' || event.code === 'KeyZ';
            if ((event.ctrlKey || event.metaKey) && isZ && !event.shiftKey && !event.altKey) {
                const active = document.activeElement;
                if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable || (typeof active.getAttribute === 'function' && active.getAttribute('contenteditable') === 'true'))) return;
                if (typeof doodlesEnabled !== 'undefined' && !doodlesEnabled) return;
                if (typeof isPagedBack !== 'undefined' && isPagedBack) return;
                event.preventDefault();
                if (typeof undoLastDoodleAction === 'function') {
                    undoLastDoodleAction();
                }
            }
        }, true);

        resizeDoodleCanvas();

        // ========================================
        // Asset Manager & Canvas Stamp Dropping
        // ========================================
        const chatOpenStampsBtn = document.getElementById('chat-open-stamps-btn');
        const chatPanelTabBtn = document.getElementById('chat-show-chat-btn');
        const stampManagerPane = document.getElementById('stamp-manager-pane');
        const stampManagerGrid = document.getElementById('stamp-manager-grid');
        const stampManagerEmpty = document.getElementById('stamp-manager-empty');
        const stampEmptyMessage = document.getElementById('stamp-empty-message');
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
        let stampLoadError = '';
        let isFetchingUserStamps = false;
        let isTheaterStampOwner = false;
        const pendingStampGenerations = new Map();
        let nextStampGenerationId = 0;

        function updateStampGenerationStatus(message = '') {
            const count = pendingStampGenerations.size;
            const pending = count ? `${count} stamp${count === 1 ? '' : 's'} generating. You can generate another while you wait.` : '';
            document.getElementById('stamp-generation-status').textContent = [message, pending].filter(Boolean).join(' ');
        }

        function updateStampGenerationPermission() {
            updateAssetGenerationUI();
            const form = document.getElementById('stamp-generation-form');
            if (form) form.hidden = !isTheaterStampOwner || activeStampManagerTab !== 'stamps';
            document.getElementById('scene-generation-form').hidden = !isTheaterStampOwner || activeStampManagerTab !== 'scenes';
            document.getElementById('scene-generation-status').hidden = !isTheaterStampOwner || activeStampManagerTab !== 'scenes';
            document.getElementById('stamp-generation-status').hidden = !isTheaterStampOwner || activeStampManagerTab !== 'stamps';
        }

        const stampNameStopwords = new Set((
            'a an the and or but of for to in on at by with from into onto over under ' +
            'as is are was were be been being it its this that these those ' +
            'i me my we our you your he him his she her they them their ' +
            'some any each all no not so very can could should would will ' +
            'do does did have has had about up down out through between ' +
            'who whom whose what when where why how than then there here ' +
            'both few more most other such only own same too until while ' +
            'again further once during before after above below against off just now'
        ).split(' '));

        function stampNameFromPrompt(prompt) {
            const words = prompt.match(/[\p{L}\p{N}]+(?:['’\-][\p{L}\p{N}]+)*/gu) || [];
            const name = words.filter(word => !stampNameStopwords.has(word.toLowerCase())).slice(0, 3).join(' ');
            return (name || 'Stamp').slice(0, 100);
        }

        function getAvailableTheaterReferences() {
            const entries = [];
            const seen = new Set();
            const addEntry = (rawName, kind) => {
                const name = String(rawName || '').trim();
                const key = name.toLowerCase();
                if (!key || seen.has(key)) return;
                seen.add(key);
                entries.push({ name, kind });
            };
            const chars = (typeof cachedTheaterCharacters !== 'undefined' && Array.isArray(cachedTheaterCharacters))
                ? cachedTheaterCharacters
                : (Array.isArray(window.cachedTheaterCharacters) ? window.cachedTheaterCharacters : []);
            chars.forEach(item => addEntry(item && item.name, 'character'));
            const scenes = (typeof cachedTheaterScenes !== 'undefined' && Array.isArray(cachedTheaterScenes))
                ? cachedTheaterScenes
                : (Array.isArray(window.cachedTheaterScenes) ? window.cachedTheaterScenes : []);
            scenes.forEach(item => addEntry(item && item.name, 'scene'));
            const refs = (typeof cachedTheaterReferences !== 'undefined' && Array.isArray(cachedTheaterReferences))
                ? cachedTheaterReferences
                : (Array.isArray(window.cachedTheaterReferences) ? window.cachedTheaterReferences : []);
            refs.forEach(item => addEntry(item && item.name, 'reference'));
            const stamps = (typeof cachedTheaterStamps !== 'undefined' && Array.isArray(cachedTheaterStamps))
                ? cachedTheaterStamps
                : (Array.isArray(window.cachedTheaterStamps) ? window.cachedTheaterStamps : []);
            stamps.forEach(item => addEntry(item && item.name, 'stamp'));
            return entries;
        }

        function escapeCanvasReferenceRegex(str) {
            return String(str).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        }

        function extractTaggedCanvasReferences(promptText) {
            if (!promptText || !promptText.includes('@')) return [];
            const available = getAvailableTheaterReferences();
            const sorted = [...available].sort((a, b) => b.name.length - a.name.length);
            const hits = [];
            const occupied = [];
            const isOverlapping = (start, end) => occupied.some(([s, e]) => start < e && end > s);
            for (const entry of sorted) {
                const tokens = entry.name.trim().split(/[\s_-]+/).filter(Boolean).map(escapeCanvasReferenceRegex);
                if (!tokens.length) continue;
                const pattern = new RegExp(`(?:^|[\\s([{"',;:])@(${tokens.join('[\\s_-]+')})(?=$|[\\s.,!?;:)\\]}"'])`, 'gi');
                let match;
                while ((match = pattern.exec(promptText)) !== null) {
                    const matchedTag = match[1];
                    const tagStart = match.index + match[0].lastIndexOf('@');
                    const tagEnd = tagStart + 1 + matchedTag.length;
                    if (!isOverlapping(tagStart, tagEnd)) {
                        occupied.push([tagStart, tagEnd]);
                        hits.push({ index: tagStart, name: entry.name });
                    }
                }
            }
            const fallbackPattern = /(?:^|[\s([{"',;:])@([a-zA-Z0-9][\w-]*)/g;
            let fallbackMatch;
            while ((fallbackMatch = fallbackPattern.exec(promptText)) !== null) {
                const token = fallbackMatch[1];
                const tagStart = fallbackMatch.index + fallbackMatch[0].lastIndexOf('@');
                const tagEnd = tagStart + 1 + token.length;
                if (!isOverlapping(tagStart, tagEnd)) {
                    occupied.push([tagStart, tagEnd]);
                    hits.push({ index: tagStart, name: token });
                }
            }
            hits.sort((a, b) => a.index - b.index);
            const result = [];
            const seen = new Set();
            for (const hit of hits) {
                const key = hit.name.toLowerCase();
                if (!seen.has(key)) {
                    seen.add(key);
                    result.push(hit.name);
                    if (result.length >= 4) break;
                }
            }
            return result;
        }

        function attachCanvasReferenceAutocomplete(textarea) {
            if (!textarea || !textarea.parentElement || textarea.dataset.refAutocompleteAttached === 'true') return;
            textarea.dataset.refAutocompleteAttached = 'true';
            const container = textarea.parentElement;
            const menu = document.createElement('div');
            menu.className = 'reference-mention-menu';
            menu.setAttribute('role', 'listbox');
            menu.hidden = true;
            container.appendChild(menu);

            let filtered = [];
            let activeIndex = 0;
            let mentionStart = -1;

            function closeMenu() {
                menu.hidden = true;
                menu.replaceChildren();
                filtered = [];
                activeIndex = 0;
                mentionStart = -1;
            }

            function applySelection(entry) {
                if (!entry || mentionStart < 0) return;
                const caret = typeof textarea.selectionStart === 'number' ? textarea.selectionStart : textarea.value.length;
                const before = textarea.value.slice(0, mentionStart);
                const after = textarea.value.slice(caret);
                const insertion = `@${entry.name} `;
                textarea.value = before + insertion + after.replace(/^\s+/, '');
                const nextCaret = before.length + insertion.length;
                textarea.focus();
                textarea.setSelectionRange(nextCaret, nextCaret);
                closeMenu();
            }

            function renderMenu() {
                menu.replaceChildren();
                if (!filtered.length) {
                    closeMenu();
                    return;
                }
                menu.hidden = false;
                filtered.forEach((entry, idx) => {
                    const option = document.createElement('button');
                    option.type = 'button';
                    option.className = 'reference-mention-option' + (idx === activeIndex ? ' active' : '');
                    option.setAttribute('role', 'option');
                    option.setAttribute('aria-selected', idx === activeIndex ? 'true' : 'false');
                    option.dataset.referenceName = entry.name;
                    option.dataset.referenceKind = entry.kind;
                    const nameSpan = document.createElement('span');
                    nameSpan.className = 'reference-mention-name';
                    nameSpan.textContent = `@${entry.name}`;
                    const kindSpan = document.createElement('span');
                    kindSpan.className = 'reference-mention-kind';
                    kindSpan.textContent = entry.kind;
                    option.append(nameSpan, kindSpan);
                    option.addEventListener('mousedown', e => {
                        e.preventDefault();
                        applySelection(entry);
                    });
                    option.addEventListener('click', e => {
                        e.preventDefault();
                        applySelection(entry);
                    });
                    menu.appendChild(option);
                });
            }

            function updateMenu() {
                const caret = typeof textarea.selectionStart === 'number' ? textarea.selectionStart : textarea.value.length;
                const beforeCaret = textarea.value.slice(0, caret);
                const match = /(?:^|[\s([{"',;:])@([^@\r\n]*)$/.exec(beforeCaret);
                if (!match) {
                    closeMenu();
                    return;
                }
                const query = match[1].toLowerCase();
                mentionStart = caret - match[1].length - 1;
                const available = getAvailableTheaterReferences();
                const prefixMatches = [];
                const substringMatches = [];
                for (const entry of available) {
                    const lower = entry.name.toLowerCase();
                    if (lower.startsWith(query)) prefixMatches.push(entry);
                    else if (lower.includes(query)) substringMatches.push(entry);
                }
                filtered = [...prefixMatches, ...substringMatches];
                if (!filtered.length) {
                    closeMenu();
                    return;
                }
                if (activeIndex >= filtered.length) activeIndex = 0;
                renderMenu();
            }

            textarea.addEventListener('input', updateMenu);
            textarea.addEventListener('click', updateMenu);
            textarea.addEventListener('keydown', event => {
                if (menu.hidden || !filtered.length) return;
                if (event.key === 'ArrowDown') {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                    activeIndex = (activeIndex + 1) % filtered.length;
                    renderMenu();
                } else if (event.key === 'ArrowUp') {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                    activeIndex = (activeIndex - 1 + filtered.length) % filtered.length;
                    renderMenu();
                } else if (event.key === 'Tab' || event.key === 'Enter') {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                    applySelection(filtered[activeIndex]);
                } else if (event.key === 'Escape') {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                    closeMenu();
                }
            });
            textarea.addEventListener('blur', () => {
                setTimeout(closeMenu, 150);
            });
        }

        function initCanvasReferenceAutocomplete() {
            ['stamp-generation-prompt', 'scene-generation-prompt', 'character-generation-prompt', 'animation-generation-prompt'].forEach(id => {
            attachCanvasReferenceAutocomplete(document.getElementById(id));
        });
        }

        initCanvasReferenceAutocomplete();
        document.getElementById('stamp-generation-prompt')?.addEventListener('keydown', event => {
            if (event.key !== 'Enter' || event.isComposing) return;
            event.preventDefault();
            if (!event.repeat) document.getElementById('stamp-generation-form').requestSubmit();
        });

        document.getElementById('stamp-generation-form')?.addEventListener('submit', async event => {
            event.preventDefault();
            if (!isTheaterStampOwner) return;
            const promptInput = document.getElementById('stamp-generation-prompt');
            const status = document.getElementById('stamp-generation-status');
            const prompt = promptInput.value.trim();
            if (!prompt) {
                status.textContent = 'Enter an image prompt.';
                return;
            }
            const name = stampNameFromPrompt(prompt);
            const jobId = ++nextStampGenerationId;
            pendingStampGenerations.set(jobId, { name, prompt });
            promptInput.value = '';
            renderStampManagerGrid(cachedUserStamps || []);
            updateStampGenerationStatus();
            let resultMessage = '';
            try {
                const response = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/stamps/generate`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify((() => { const p = { name, prompt }; const refs = extractTaggedCanvasReferences(prompt); if (refs.length) p.references = refs; return p; })()),
                });
                const data = await response.json();
                if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Unable to generate stamp.');
                // Responses may arrive out of order, with an older stamp list.
                const mergedStamps = new Map(cachedTheaterStamps.map(stamp => [stamp.id, stamp]));
                data.stamps.forEach(stamp => mergedStamps.set(stamp.id, stamp));
                cachedTheaterStamps = [...mergedStamps.values()];
                resultMessage = `“${name}” saved to this theater and ready to use. Charged ${data.credits_charged} credits.`;
            } catch (error) {
                resultMessage = `“${name}”: ${error.message || 'Unable to generate stamp. Please try again.'}`;
                if (!promptInput.value.trim()) promptInput.value = prompt;
            } finally {
                pendingStampGenerations.delete(jobId);
                renderStampManagerGrid(cachedUserStamps || []);
                updateStampGenerationStatus(resultMessage);
            }
        });

        const scenePresetMenu = document.getElementById('scene-generation-presets');
        const pendingSceneGenerations = new Map();
        let nextSceneGenerationId = 0;

        function updateSceneGenerationStatus(message = '') {
            const count = pendingSceneGenerations.size;
            const pending = count ? `${count} scene${count === 1 ? '' : 's'} generating. You can generate another while you wait.` : '';
            document.getElementById('scene-generation-status').textContent = [message, pending].filter(Boolean).join(' ');
            document.getElementById('scene-generation-form').setAttribute('aria-busy', String(count > 0));
        }

        scenePresetMenu.querySelectorAll('[data-scene-type]').forEach(option => {
            option.addEventListener('click', () => {
                document.getElementById('scene-generation-type').value = option.dataset.sceneType;
                document.getElementById('scene-generation-type-label').textContent = option.textContent;
                scenePresetMenu.querySelectorAll('[data-scene-type]').forEach(item => {
                    item.setAttribute('aria-pressed', String(item === option));
                });
                scenePresetMenu.open = false;
                scenePresetMenu.querySelector('summary').focus();
            });
        });
        document.addEventListener('pointerdown', event => {
            if (!scenePresetMenu.contains(event.target)) scenePresetMenu.open = false;
        });
        scenePresetMenu.addEventListener('keydown', event => {
            if (event.key === 'Escape') {
                scenePresetMenu.open = false;
                scenePresetMenu.querySelector('summary').focus();
            }
        });
        document.getElementById('scene-generation-prompt').addEventListener('keydown', event => {
            if (event.key !== 'Enter' || event.isComposing) return;
            event.preventDefault();
            if (!event.repeat) document.getElementById('scene-generation-form').requestSubmit();
        });

        document.getElementById('scene-generation-form')?.addEventListener('submit', async event => {
            event.preventDefault();
            if (!isTheaterStampOwner) return;
            const promptInput = document.getElementById('scene-generation-prompt');
            const typeInput = document.getElementById('scene-generation-type');
            const prompt = promptInput.value.trim();
            if (!prompt) return;
            const sceneType = typeInput.value;
            const name = stampNameFromPrompt(prompt);
            const generationTheaterId = theaterId;
            const jobId = ++nextSceneGenerationId;
            pendingSceneGenerations.set(jobId, { name, prompt, sceneType });
            promptInput.value = '';
            renderScenesManagerGrid(cachedTheaterScenes || []);
            updateSceneGenerationStatus();
            let resultMessage = '';
            try {
                const response = await fetch(`/api/theaters/${encodeURIComponent(generationTheaterId)}/scenes/generate`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify((() => { const p = { name, prompt, scene_type: sceneType }; const refs = extractTaggedCanvasReferences(prompt); if (refs.length) p.references = refs; return p; })()),
                });
                const data = await response.json();
                if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Unable to generate scene.');
                if (theaterId !== generationTheaterId) return;
                // Concurrent requests can return snapshots in a different order.
                const mergedScenes = new Map((cachedTheaterScenes || []).map(scene => [scene.id, scene]));
                data.scenes.forEach(scene => mergedScenes.set(scene.id, scene));
                cachedTheaterScenes = [...mergedScenes.values()];
                resultMessage = `“${name}” saved and ready to push to canvas. Charged ${data.credits_charged} credits.`;
            } catch (error) {
                if (theaterId !== generationTheaterId) return;
                resultMessage = `“${name}”: ${error.message || 'Unable to generate scene. Please try again.'}`;
                if (!promptInput.value.trim()) promptInput.value = prompt;
            } finally {
                pendingSceneGenerations.delete(jobId);
                if (theaterId === generationTheaterId) {
                    if (activeStampManagerTab === 'scenes') renderScenesManagerGrid(cachedTheaterScenes || []);
                    updateSceneGenerationStatus(resultMessage);
                }
            }
        });

        const assetGenerationKinds = { characters: 'character', animations: 'animation', playlists: 'music' };
        const animationPresetMenu = document.getElementById('animation-generation-presets');
        animationPresetMenu.querySelectorAll('[data-animation-type]').forEach(option => {
            option.addEventListener('click', () => {
                document.getElementById('animation-generation-type').value = option.dataset.animationType;
                document.getElementById('animation-generation-type-label').textContent = option.textContent;
                animationPresetMenu.querySelectorAll('[data-animation-type]').forEach(item => {
                    item.setAttribute('aria-pressed', String(item === option));
                });
                animationPresetMenu.open = false;
                animationPresetMenu.querySelector('summary').focus();
            });
        });
        document.addEventListener('pointerdown', event => {
            if (!animationPresetMenu.contains(event.target)) animationPresetMenu.open = false;
        });
        animationPresetMenu.addEventListener('keydown', event => {
            if (event.key === 'Escape') {
                animationPresetMenu.open = false;
                animationPresetMenu.querySelector('summary').focus();
            }
        });
        const pendingAssetGenerations = new Map();
        const assetGenerationMessages = new Map();
        let nextAssetGenerationId = 0;

        function updateAssetGenerationUI() {
            const kind = assetGenerationKinds[activeStampManagerTab];
            for (const value of Object.values(assetGenerationKinds)) {
                document.getElementById(`${value}-generation-form`).hidden = !isTheaterStampOwner || kind !== value;
            }
            const status = document.getElementById('asset-generation-status');
            status.hidden = !isTheaterStampOwner || !kind;
            const jobs = [...pendingAssetGenerations.values()].filter(job => job.kind === kind && job.theaterId === theaterId);
            const note = kind === 'character' ? 'Uses image generation credits.' : `Uses ${kind || 'asset'} generation credits.`;
            status.textContent = [assetGenerationMessages.get(kind) || `${note} Saved to this theater.`,
                jobs.length ? `${jobs.length} generating. You can generate another while you wait.` : ''].filter(Boolean).join(' ');
            const panel = document.getElementById('asset-generation-pending');
            panel.replaceChildren();
            panel.hidden = !jobs.length;
            if (kind === 'character' && cachedTheaterCharacters !== null) {
                charactersManagerEmpty.style.display = jobs.length || cachedTheaterCharacters.length ? 'none' : 'flex';
            }
            for (const job of jobs) {
                const card = document.createElement('div');
                card.className = 'scene-card asset-card-generating';
                card.dataset.assetSearch = `${job.name} ${job.prompt}`;
                card.setAttribute('aria-busy', 'true');
                const placeholder = document.createElement('div');
                placeholder.className = 'scene-card-thumb-container stamp-generation-placeholder';
                const label = document.createElement('div');
                label.className = 'scene-card-name';
                label.textContent = job.name;
                const progress = document.createElement('div');
                progress.className = 'stamp-card-progress';
                progress.textContent = 'Generating…';
                card.append(placeholder, label, progress);
                panel.appendChild(card);
            }
            if (kind) document.getElementById(`${kind}-generation-form`).setAttribute('aria-busy', String(jobs.length > 0));
        }

        for (const [tab, kind] of Object.entries(assetGenerationKinds)) {
            const form = document.getElementById(`${kind}-generation-form`);
            const input = document.getElementById(`${kind}-generation-prompt`);
            input.addEventListener('keydown', event => {
                if (event.key !== 'Enter' || event.isComposing) return;
                event.preventDefault();
                if (!event.repeat) form.requestSubmit();
            });
            form.addEventListener('submit', async event => {
                event.preventDefault();
                const prompt = input.value.trim();
                if (!isTheaterStampOwner || !prompt) return;
                const generationTheaterId = theaterId;
                const name = stampNameFromPrompt(prompt);
                const payload = { name, prompt };
                if (kind === 'animation') payload.animation_type = document.getElementById('animation-generation-type').value;
                if (kind === 'character' || kind === 'animation') {
                    const taggedRefs = extractTaggedCanvasReferences(prompt);
                    if (taggedRefs.length) payload.references = taggedRefs;
                }
                const id = ++nextAssetGenerationId;
                pendingAssetGenerations.set(id, { kind, name, prompt, theaterId: generationTheaterId });
                assetGenerationMessages.delete(kind);
                input.value = '';
                updateAssetGenerationUI();
                try {
                    const endpoint = kind === 'music' ? 'music' : tab;
                    const response = await fetch(`/api/theaters/${encodeURIComponent(generationTheaterId)}/${endpoint}/generate`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
                    });
                    const data = await response.json();
                    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Unable to generate ${kind}.`);
                    if (theaterId !== generationTheaterId) return;
                    if (kind === 'character') {
                        const merged = new Map((cachedTheaterCharacters || []).map(asset => [asset.id || asset.name, asset]));
                        (data.characters || []).forEach(asset => merged.set(asset.id || asset.name, asset));
                        cachedTheaterCharacters = [...merged.values()];
                        if (activeStampManagerTab === tab) renderCharactersManagerGrid(cachedTheaterCharacters);
                    } else if (activeStampManagerTab === tab) {
                        await loadExtraAssets(tab);
                    }
                    assetGenerationMessages.set(kind, `“${name}” saved to this theater. Charged ${data.credits_charged} credits.`);
                } catch (error) {
                    if (theaterId !== generationTheaterId) return;
                    assetGenerationMessages.set(kind, `“${name}”: ${error.message}`);
                    if (!input.value.trim()) input.value = prompt;
                } finally {
                    pendingAssetGenerations.delete(id);
                    if (theaterId === generationTheaterId) updateAssetGenerationUI();
                }
            });
        }

        function applyAssetSearch() {
            const query = document.getElementById('asset-search').value.trim().toLocaleLowerCase();
            const matches = text => text.toLocaleLowerCase().includes(query);
            const body = document.getElementById('stamp-manager-body');
            body.querySelectorAll('[data-asset-search]').forEach(card => {
                const playlist = card.closest('.playlist-group');
                card.classList.toggle('asset-search-hidden', !matches(`${card.dataset.assetSearch} ${playlist?.dataset.playlistName || ''}`));
            });
            body.querySelectorAll('.playlist-group').forEach(group => {
                const nameMatches = matches(group.dataset.playlistName);
                const tracks = [...group.querySelectorAll('.playlist-track-list [data-asset-search]')];
                const visible = nameMatches || tracks.some(track => !track.classList.contains('asset-search-hidden'));
                group.classList.toggle('asset-search-hidden', !visible);
                group.querySelector('.playlist-header-push').classList.toggle('asset-search-hidden', !visible);
                const expanded = query ? visible : expandedPlaylists.has(group.dataset.playlistName);
                group.querySelector('.playlist-track-list').hidden = !expanded;
                group.querySelector('.playlist-header-toggle').setAttribute('aria-expanded', String(expanded));
                const chevron = group.querySelector('.playlist-chevron');
                const symbol = expanded ? '▼' : '▶';
                if (chevron.textContent !== symbol) chevron.textContent = symbol;
                group.classList.toggle('minimized', !expanded);
            });
            const panels = { stamps: 'stamp-manager-grid', scenes: 'scenes-manager-grid', characters: 'characters-manager-grid', playlists: 'playlists-manager', animations: 'animations-manager' };
            const panel = document.getElementById(panels[activeStampManagerTab]);
            const visible = [...panel.querySelectorAll('[data-asset-search]'), ...document.querySelectorAll('#asset-generation-pending [data-asset-search]')]
                .some(card => !card.classList.contains('asset-search-hidden'));
            document.getElementById('asset-search-empty').hidden = !query || visible;
        }
        document.getElementById('asset-search').addEventListener('input', applyAssetSearch);
        new MutationObserver(applyAssetSearch).observe(document.getElementById('stamp-manager-body'), { childList: true, subtree: true });

        function setStampManagerOpen(open) {
            if (open) {
                if (!currentUser || !currentUser.id) {
                    getCanvasAuthState()
                        .then(data => {
                            if (data && data.authenticated && data.user) {
                                currentUser = data.user;
                                updateStampManagerProfileLinks(currentUser);
                                setStampManagerOpen(true);
                            } else {
                                if (typeof openAuthModal === 'function') {
                                    openAuthModal('login');
                                }
                            }
                        })
                        .catch(() => {
                            if (typeof openAuthModal === 'function') {
                                openAuthModal('login');
                            }
                        });
                    return;
                }
            }
            isStampManagerOpen = Boolean(open);
            const panelBody = document.getElementById('panel-body');
            if (isStampManagerOpen && panelBody && panelBody.style.display !== 'none') {
                const chatComposer = document.getElementById('chat-form');
                const chatPrompt = document.getElementById('chat-input');
                const composerStyle = getComputedStyle(chatComposer);
                const promptStyle = getComputedStyle(chatPrompt);
                const footer = stampManagerPane.querySelector('.stamp-manager-footer');
                footer.style.padding = composerStyle.padding;
                footer.style.gap = composerStyle.gap;
                footer.style.margin = composerStyle.margin;
                footer.style.borderTop = composerStyle.borderTop;
                const stampPrompt = document.getElementById('stamp-generation-prompt');
                stampPrompt.style.height = `${chatPrompt.getBoundingClientRect().height}px`;
                stampPrompt.style.padding = promptStyle.padding;
                stampPrompt.style.font = promptStyle.font;
            }
            if (panelBody) panelBody.style.display = isStampManagerOpen ? 'none' : 'flex';
            if (stampManagerPane) stampManagerPane.style.display = isStampManagerOpen ? 'flex' : 'none';
            chatOpenStampsBtn?.classList.toggle('active', isStampManagerOpen);
            chatOpenStampsBtn?.setAttribute('aria-selected', String(isStampManagerOpen));
            chatPanelTabBtn?.setAttribute('aria-selected', String(!isStampManagerOpen));
            if (isStampManagerOpen) {
                if (typeof updateStampTabsVisibility === 'function') updateStampTabsVisibility();
                if (activeStampManagerTab === 'stamps') {
                    updateStampManagerPermissionBanner();
                    updateStampManagerProfileLinks(currentUser);
                    if (cachedUserStamps) renderStampManagerGrid(cachedUserStamps);
                    loadUserStamps();
                } else if (activeStampManagerTab === 'scenes') {
                    if (cachedTheaterScenes) renderScenesManagerGrid(cachedTheaterScenes);
                    if (typeof loadTheaterScenes === 'function') loadTheaterScenes();
                } else if (activeStampManagerTab === 'characters') {
                    if (cachedTheaterCharacters) renderCharactersManagerGrid(cachedTheaterCharacters);
                    if (typeof loadTheaterCharacters === 'function') loadTheaterCharacters();
                } else if (activeStampManagerTab === 'playlists' || activeStampManagerTab === 'animations') {
                    loadExtraAssets(activeStampManagerTab);
                }
            }
        }

        function updateStampManagerPermissionBanner() {
            if (!stampManagerPermissionBanner) return;
            const canContribute = hasContributorsPermission();
            const isOrator = typeof isCurrentOrator === 'function' && isCurrentOrator();
            stampManagerPermissionBanner.style.display = isOrator || activeStampManagerTab !== 'stamps' ? 'none' : '';
            if (isOrator) return;
            const active = canPlaceStamps();
            stampManagerPermissionBanner.className = `stamp-manager-permission-banner ${active ? 'contributor' : 'non-contributor'}`;
            if (!canContribute) {
                stampManagerPermissionBanner.innerHTML = '🔒 <strong>Viewer Mode</strong>: You need contributor permission to place stamps on the canvas.';
            } else if (!isOrator && typeof doodlesEnabled !== 'undefined' && !doodlesEnabled) {
                stampManagerPermissionBanner.innerHTML = '✏️ <strong>Doodles are Off</strong>: The Orator must turn on Doodles before contributors can place stamps.';
            } else {
                stampManagerPermissionBanner.innerHTML = '✨ <strong>Contributor Active</strong>: Drag any stamp out of this pane and onto the canvas.';
            }
        }

        chatOpenStampsBtn?.addEventListener('click', () => {
            if (!isStampManagerOpen) setStampManagerOpen(true);
        });
        chatPanelTabBtn?.addEventListener('click', () => setStampManagerOpen(false));
        document.getElementById('chat-user-bar')?.addEventListener('keydown', event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            event.stopPropagation();
            const tab = event.key === 'Home' ? chatPanelTabBtn
                : event.key === 'End' ? chatOpenStampsBtn
                    : event.target === chatPanelTabBtn ? chatOpenStampsBtn : chatPanelTabBtn;
            tab.focus();
            tab.click();
        });
        stampTabStampsBtn?.addEventListener('click', () => {
            if (typeof switchStampManagerTab === 'function') switchStampManagerTab('stamps');
        });
        stampTabScenesBtn?.addEventListener('click', () => {
            if (typeof switchStampManagerTab === 'function') switchStampManagerTab('scenes');
        });
        stampTabCharactersBtn?.addEventListener('click', () => {
            if (typeof switchStampManagerTab === 'function') switchStampManagerTab('characters');
        });
        document.getElementById('stamp-manager-empty-profile-btn')?.addEventListener('click', (e) => {
            if (!currentUser || !currentUser.id) {
                e.preventDefault();
                if (typeof openAuthModal === 'function') openAuthModal('login');
            }
        });
        document.getElementById('stamp-manager-profile-link')?.addEventListener('click', (e) => {
            if (!currentUser || !currentUser.id) {
                e.preventDefault();
                if (typeof openAuthModal === 'function') openAuthModal('login');
            }
        });

        function getStampUsageStorageKey() {
            const uid = (typeof currentUser !== 'undefined' && currentUser && currentUser.id) ? currentUser.id : null;
            return uid ? `narratron_stamp_usage_${uid}` : 'narratron_stamp_usage';
        }

        function getStampUsageMap() {
            try {
                const specificKey = getStampUsageStorageKey();
                const rawSpecific = localStorage.getItem(specificKey);
                if (rawSpecific) {
                    const parsed = JSON.parse(rawSpecific);
                    if (parsed && typeof parsed === 'object') return parsed;
                }
                const rawGlobal = localStorage.getItem('narratron_stamp_usage');
                if (rawGlobal) {
                    const parsed = JSON.parse(rawGlobal);
                    if (parsed && typeof parsed === 'object') return parsed;
                }
            } catch (_) { }
            return {};
        }

        function getStampLastUsedTimestamp(stampId) {
            const idStr = String(stampId);
            const map = getStampUsageMap();
            if (map && map[idStr]) {
                const val = Number(map[idStr]);
                if (!Number.isNaN(val)) return val;
            }

            // Fallback: check if the stamp is currently on canvas in doodleActions
            if (typeof doodleActions !== 'undefined' && Array.isArray(doodleActions)) {
                for (let i = doodleActions.length - 1; i >= 0; i--) {
                    const action = doodleActions[i];
                    if (action && action.type === 'stamp' && String(action.stamp_id) === idStr) {
                        return 1000 + i;
                    }
                }
            }
            return 0;
        }

        function recordStampUsage(stampId) {
            if (stampId === null || stampId === undefined) return;
            const idStr = String(stampId);
            const now = Date.now();
            const map = getStampUsageMap();
            map[idStr] = now;
            try {
                const json = JSON.stringify(map);
                localStorage.setItem(getStampUsageStorageKey(), json);
                localStorage.setItem('narratron_stamp_usage', json);
            } catch (_) { }
            if (cachedUserStamps) {
                renderStampManagerGrid(cachedUserStamps);
            }
        }

        async function loadUserStamps() {
            if (!stampManagerGrid || isFetchingUserStamps) return;
            isFetchingUserStamps = true;
            if (stampManagerEmpty) stampManagerEmpty.style.display = 'none';

            try {
                const results = await Promise.allSettled([
                    fetch('/api/users/me/stamps').then(async res => {
                        if (!res.ok) throw new Error(`HTTP ${res.status}`);
                        return res.json();
                    }),
                    fetch(`/api/theaters/${encodeURIComponent(theaterId)}/stamps`).then(async res => {
                        if (!res.ok) throw new Error(`HTTP ${res.status}`);
                        return res.json();
                    }),
                ]);
                cachedUserStamps = results[0].status === 'fulfilled' && Array.isArray(results[0].value) ? results[0].value : [];
                cachedTheaterStamps = results[1].status === 'fulfilled' && Array.isArray(results[1].value) ? results[1].value : [];
                stampLoadError = results.some(result => result.status === 'rejected') ? 'Some stamps could not be loaded. Reopen the manager to retry.' : '';
                renderStampManagerGrid(cachedUserStamps);
            } catch (err) {
                console.warn('Unable to load user stamps:', err);
                stampManagerGrid.innerHTML = '';
                if (stampManagerEmpty) stampManagerEmpty.style.display = 'flex';
                if (stampEmptyMessage) stampEmptyMessage.textContent = 'Unable to load stamps at this time.';
            } finally {
                isFetchingUserStamps = false;
            }
        }

        function renderStampManagerGrid(stamps) {
            if (!stampManagerGrid) return;
            stampManagerGrid.innerHTML = '';
            const isStampsActive = typeof activeStampManagerTab === 'undefined' || activeStampManagerTab === 'stamps';
            if (stampManagerCount && isStampsActive) {
                stampManagerCount.textContent = `${stamps.length}/10`;
                stampManagerCount.title = `${stamps.length} user stamps · ${cachedTheaterStamps.length} theater stamps` +
                    (pendingStampGenerations.size ? ` · ${pendingStampGenerations.size} generating` : '');
            }

            if (stamps.length === 0 && cachedTheaterStamps.length === 0 && pendingStampGenerations.size === 0) {
                if (stampManagerEmpty && isStampsActive) stampManagerEmpty.style.display = 'flex';
                if (stampEmptyMessage) stampEmptyMessage.textContent = stampLoadError || 'No stamps available yet.';
                return;
            }
            if (stampManagerEmpty && isStampsActive) stampManagerEmpty.style.display = 'none';
            if (stampLoadError) {
                const notice = document.createElement('div');
                notice.className = 'stamp-section-label';
                notice.textContent = stampLoadError;
                stampManagerGrid.appendChild(notice);
            }

            // Stamps in the stamp manager should be ordered from most recently used to least
            const sortStamps = items => [...items].sort((a, b) => {
                const timeA = getStampLastUsedTimestamp(a.id);
                const timeB = getStampLastUsedTimestamp(b.id);
                if (timeA !== timeB) {
                    return timeB - timeA;
                }
                const idA = Number(a.id) || 0;
                const idB = Number(b.id) || 0;
                return idA - idB;
            });
            const sortedStamps = [...sortStamps(stamps), ...sortStamps(cachedTheaterStamps)];
            const addSection = (label, divider = false) => {
                const heading = document.createElement('div');
                heading.className = `stamp-section-label${divider ? ' stamp-section-divider' : ''}`;
                heading.textContent = label;
                stampManagerGrid.appendChild(heading);
            };
            addSection('User stamps');
            if (stamps.length === 0) addSection('No user stamps uploaded yet.');

            const canContribute = hasContributorsPermission();
            const isOrator = typeof isCurrentOrator === 'function' && isCurrentOrator();
            const canDrag = canPlaceStamps();
            sortedStamps.forEach((stamp, index) => {
                if (index === stamps.length) addSection('Theater stamps', true);
                const card = document.createElement('div');
                card.className = `stamp-card ${canDrag ? 'can-drag' : 'locked'}`;
                card.dataset.assetSearch = JSON.stringify(stamp);
                card.draggable = canDrag;
                card.dataset.stampId = String(stamp.id);
                card.dataset.stampName = stamp.name || 'Stamp';
                card.dataset.stampUrl = typeof resolveWebpQualityUrl === 'function' ? resolveWebpQualityUrl(stamp.url) : stamp.url;
                const stampLabel = stamp.name || 'Stamp';
                card.title = canDrag
                    ? `Drag "${stampLabel}" onto canvas`
                    : !canContribute
                        ? `"${stampLabel}" (Requires Contributor permission to place)`
                        : `"${stampLabel}" (Doodles must be enabled by Orator to place stamps)`;

                const img = document.createElement('img');
                img.className = 'stamp-card-thumb';
                img.src = typeof resolveWebpQualityUrl === 'function' ? resolveWebpQualityUrl(stamp.url) : stamp.url;
                img.alt = stamp.name;
                img.draggable = false;

                const nameSpan = document.createElement('span');
                nameSpan.className = 'stamp-card-name';
                nameSpan.textContent = stamp.name;

                card.appendChild(img);
                card.appendChild(nameSpan);

                if (canDrag) {
                    const grip = document.createElement('span');
                    grip.className = 'stamp-card-grip';
                    grip.textContent = '⠿';
                    card.appendChild(grip);

                    card.addEventListener('dragstart', (e) => {
                        if (!canPlaceStamps()) {
                            e.preventDefault();
                            return;
                        }
                        const payload = {
                            stamp_id: stamp.id,
                            name: stamp.name,
                            url: stamp.url,
                        };
                        const json = JSON.stringify(payload);
                        try {
                            e.dataTransfer.setData('application/json', json);
                        } catch (_) { }
                        try {
                            e.dataTransfer.setData('text/plain', json);
                        } catch (_) { }
                        e.dataTransfer.effectAllowed = 'copyMove';
                    });

                    // Touch / Pointer drag fallback
                    card.addEventListener('pointerdown', (e) => {
                        if (e.button !== 0 || !canPlaceStamps()) return;
                        let moved = false;
                        const startX = e.clientX;
                        const startY = e.clientY;
                        let ghost = null;

                        const onPointerMove = (moveEvt) => {
                            const dist = Math.hypot(moveEvt.clientX - startX, moveEvt.clientY - startY);
                            if (!moved && dist > 6) {
                                moved = true;
                                ghost = document.createElement('div');
                                ghost.className = 'stamp-drag-ghost';
                                const ghostImg = document.createElement('img');
                                ghostImg.src = stamp.url;
                                ghost.appendChild(ghostImg);
                                document.body.appendChild(ghost);
                            }
                            if (ghost) {
                                ghost.style.left = `${moveEvt.clientX}px`;
                                ghost.style.top = `${moveEvt.clientY}px`;
                            }
                        };

                        const onPointerUp = (upEvt) => {
                            window.removeEventListener('pointermove', onPointerMove);
                            window.removeEventListener('pointerup', onPointerUp);
                            window.removeEventListener('pointercancel', onPointerUp);
                            if (ghost) {
                                ghost.remove();
                                ghost = null;
                            }
                            if (!moved) return;

                            const targetEl = (canvas && canvas.offsetWidth > 0 && canvas.offsetHeight > 0)
                                ? canvas
                                : (imgContainer || canvas);
                            const rect = targetEl ? targetEl.getBoundingClientRect() : null;
                            if (
                                rect && rect.width > 0 && rect.height > 0 &&
                                upEvt.clientX >= rect.left && upEvt.clientX <= rect.right &&
                                upEvt.clientY >= rect.top && upEvt.clientY <= rect.bottom
                            ) {
                                const projected = typeof imageHistory !== 'undefined' && imageHistory[currentViewIndex]?.canvas_page
                                    ? doodleRenderer.normalizedPoint(upEvt) : null;
                                const normX = projected?.x ?? Math.max(0, Math.min(1, (upEvt.clientX - rect.left) / rect.width));
                                const normY = projected?.y ?? Math.max(0, Math.min(1, (upEvt.clientY - rect.top) / rect.height));
                                placeStampOnCanvas({ stamp_id: stamp.id, name: stamp.name, url: stamp.url }, normX, normY);
                            }
                        };

                        window.addEventListener('pointermove', onPointerMove);
                        window.addEventListener('pointerup', onPointerUp);
                        window.addEventListener('pointercancel', onPointerUp);
                    });
                }

                stampManagerGrid.appendChild(card);
            });
            if (cachedTheaterStamps.length === 0) {
                addSection(pendingStampGenerations.size ? 'Theater stamps' : 'Theater stamps · None provided', true);
            }
            pendingStampGenerations.forEach((job, jobId) => {
                const card = document.createElement('div');
                card.className = 'stamp-card stamp-card-generating';
                card.dataset.assetSearch = `${job.name} ${job.prompt}`;
                card.dataset.generationId = String(jobId);
                card.draggable = false;
                card.setAttribute('aria-busy', 'true');
                card.setAttribute('aria-label', `${job.name}: Generating`);
                card.title = `Generating “${job.name}” — ${job.prompt}`;

                const icon = document.createElement('span');
                icon.className = 'stamp-card-thumb stamp-generation-placeholder';
                icon.setAttribute('aria-hidden', 'true');
                const nameSpan = document.createElement('span');
                nameSpan.className = 'stamp-card-name';
                nameSpan.textContent = job.name;
                const progress = document.createElement('span');
                progress.className = 'stamp-card-progress';
                progress.textContent = 'Generating…';
                card.append(icon, nameSpan, progress);
                stampManagerGrid.appendChild(card);
            });
        }

        function canPlaceStamps() {
            if (!hasContributorsPermission()) return false;
            if (typeof isCurrentOrator === 'function' && isCurrentOrator()) return true;
            return typeof doodlesEnabled !== 'undefined' ? Boolean(doodlesEnabled) : true;
        }

        function placeStampOnCanvas(stampData, normX, normY) {
            if (!canPlaceStamps() || isPagedBack) return;
            canvasCursorState.select({ type: 'select' });
            if (typeof isCurrentOrator === 'function' && isCurrentOrator() && typeof doodlesEnabled !== 'undefined' && !doodlesEnabled) {
                setDoodlesDisplay(true);
                sendOrQueueDoodleMessage({ type: 'toggle_doodles', enabled: true });
            }
            const myId = (typeof currentUser !== 'undefined' && currentUser && currentUser.id) ? currentUser.id : myUserId;
            const action = {
                type: 'stamp',
                id: (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : ('stamp-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7)),
                stamp_id: stampData.stamp_id,
                user_id: myId,
                url: stampData.url,
                name: stampData.name || 'Stamp',
                x: normX,
                y: normY,
                size: 80,
                rotation: 0,
            };

            // Enforce that a user can only have one stamp of a kind on the canvas at a time.
            // Pulling another of the same will just replace it.
            let replacedStamp = null;
            const existingIndex = doodleActions.findIndex(
                item => isSameStampKind(item, action)
            );
            if (existingIndex >= 0) {
                action.id = doodleActions[existingIndex].id || action.id;
                [replacedStamp] = doodleActions.splice(existingIndex, 1);
            }
            const firstNonStamp = doodleActions.findIndex(item => item.type !== 'stamp');
            if (firstNonStamp >= 0) doodleActions.splice(firstNonStamp, 0, action);
            else doodleActions.push(action);

            recordStampUsage(stampData.stamp_id);
            sendOrQueueDoodleMessage(action);
            redrawAllDoodles();

            if (typeof pushUndoAction === 'function') {
                pushUndoAction(typeof PlaceStampDoodleAction === 'function'
                    ? new PlaceStampDoodleAction(action, replacedStamp)
                    : {
                        type: 'place_stamp',
                        action: { ...action },
                        replaced: replacedStamp ? { ...replacedStamp } : null
                    });
            }
        }

        if (imgContainer) {
            imgContainer.addEventListener('dragover', (e) => {
                if (typeof window !== 'undefined' && window.isCanvasSelectionActive) return;
                const types = e.dataTransfer ? Array.from(e.dataTransfer.types || []) : [];
                const isStampDrag = types.includes('application/json') || types.includes('text/plain');
                if (canPlaceStamps() && isStampDrag) {
                    e.preventDefault();
                    e.dataTransfer.dropEffect = 'copy';
                    imgContainer.classList.add('stamp-drag-over');
                }
            });
            imgContainer.addEventListener('dragleave', (e) => {
                imgContainer.classList.remove('stamp-drag-over');
            });
            imgContainer.addEventListener('drop', (e) => {
                imgContainer.classList.remove('stamp-drag-over');
                if (!canPlaceStamps()) return;
                try {
                    let raw = '';
                    try {
                        raw = e.dataTransfer.getData('application/json');
                    } catch (_) { }
                    if (!raw) {
                        try {
                            raw = e.dataTransfer.getData('text/plain') || e.dataTransfer.getData('text');
                        } catch (_) { }
                    }
                    if (!raw) return;
                    const stampData = JSON.parse(raw);
                    if (!stampData || !stampData.stamp_id) return;
                    e.preventDefault();
                    const targetEl = (canvas && canvas.offsetWidth > 0 && canvas.offsetHeight > 0)
                        ? canvas
                        : (imgContainer || canvas);
                    const rect = targetEl ? targetEl.getBoundingClientRect() : null;
                    if (!rect || !rect.width || !rect.height) return;
                    const projected = typeof imageHistory !== 'undefined' && imageHistory[currentViewIndex]?.canvas_page
                        ? doodleRenderer.normalizedPoint(e) : null;
                    const normX = projected?.x ?? Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                    const normY = projected?.y ?? Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));
                    placeStampOnCanvas(stampData, normX, normY);
                } catch (err) {
                    console.error('Error dropping stamp onto canvas:', err);
                }
            });
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

        window.startCanvasStatePolling(refreshCanvasState);

        refreshCanvasState();
        connectCanvasStateSocket();

        // ========================================
        // Asset Manager Tab Switching (Scenes & Characters for Orator)
        // ========================================
        function updateStampTabsVisibility() {
            const isOrator = typeof isCurrentOrator === 'function' && isCurrentOrator();
            if (stampManagerTabs) {
                stampManagerTabs.style.display = isOrator ? 'flex' : 'none';
            }
            if (!isOrator && activeStampManagerTab !== 'stamps') {
                switchStampManagerTab('stamps');
            }
        }

        const preloadedImageUrls = new Set();
        function preloadItemImages(items) {
            if (!Array.isArray(items)) return;
            items.forEach(item => {
                const url = item && (item.url || item.image_url);
                if (url && typeof url === 'string') {
                    const effectiveUrl = typeof resolveWebpQualityUrl === 'function' ? resolveWebpQualityUrl(url) : url;
                    if (!preloadedImageUrls.has(effectiveUrl)) {
                        preloadedImageUrls.add(effectiveUrl);
                        const img = new Image();
                        img.src = effectiveUrl;
                    }
                }
            });
        }

        function handleDoodleSocketData(data) {
            if (!data || typeof data !== 'object') return false;
            if (data.type === 'theater_characters_updated') {
                cachedTheaterCharacters = data.characters || [];
                preloadItemImages(cachedTheaterCharacters);
                if (isStampManagerOpen && activeStampManagerTab === 'characters') renderCharactersManagerGrid(cachedTheaterCharacters);
                return true;
            }
            if (data.type === 'theater_playlists_updated' || data.type === 'theater_animations_updated') {
                const tab = data.type === 'theater_playlists_updated' ? 'playlists' : 'animations';
                if (isStampManagerOpen && activeStampManagerTab === tab) loadExtraAssets(tab);
                return true;
            }
            if (data.type === 'theater_scenes_updated') {
                cachedTheaterScenes = data.scenes || [];
                preloadItemImages(cachedTheaterScenes);
                if (isStampManagerOpen && activeStampManagerTab === 'scenes') renderScenesManagerGrid(cachedTheaterScenes);
                return true;
            }
            if (data.type === 'theater_stamps_updated') {
                cachedTheaterStamps = data.stamps || [];
                if (isStampManagerOpen) renderStampManagerGrid(cachedUserStamps || []);
                return true;
            }
            return false;
        }

        const extraAssetPanels = {
            playlists: document.getElementById('playlists-manager'),
            animations: document.getElementById('animations-manager'),
        };
        let assetLoadVersion = 0;
        let selectedPlaylist = '';
        let assetMusicState = null;
        const expandedPlaylists = new Set();
        const noMusicButton = createAssetButton({ id: 'no_music', name: 'No Music' }, 'playlists');
        noMusicButton.id = 'no-music-button';
        extraAssetPanels.playlists.prepend(noMusicButton);
        for (const kind of ['playlists', 'animations']) {
            document.getElementById(`stamp-tab-${kind}`)?.addEventListener('click', () => switchStampManagerTab(kind));
        }

        function revertAllOtherPushItems(currentElement) {
            document.querySelectorAll('.asset-push-button, .playlist-push-button').forEach(btn => {
                if (btn !== currentElement) {
                    btn.classList.remove('pushing', 'pushed');
                    const action = btn.querySelector('small');
                    if (action && (action.textContent === 'Pushed!' || action.textContent === 'Pushing…')) {
                        action.textContent = 'Push';
                    }
                }
            });
            document.querySelectorAll('.scene-card').forEach(card => {
                if (card !== currentElement) {
                    card.classList.remove('pushing');
                    const badge = card.querySelector('.scene-card-push-badge');
                    if (badge && (badge.textContent === 'Pushed!' || badge.textContent === 'Pushing...' || badge.textContent === 'Pushing…')) {
                        badge.textContent = 'Push';
                    }
                }
            });
            document.querySelectorAll('.character-card').forEach(card => {
                if (card !== currentElement) {
                    card.classList.remove('pushing');
                    const badge = card.querySelector('.character-card-push-badge');
                    if (badge && (badge.textContent === 'Pushed!' || badge.textContent === 'Pushing...' || badge.textContent === 'Pushing…')) {
                        badge.textContent = 'Push';
                    }
                }
            });
        }

        function updateAssetPlaybackSelection(music) {
            assetMusicState = music;
            extraAssetPanels.playlists.querySelectorAll('.asset-push-button, .playlist-push-button').forEach(updateAssetPlaybackButton);
        }

        function updateAssetPlaybackButton(button) {
            if (!assetMusicState || button.dataset.assetKind !== 'playlists') return;
            const tracks = assetMusicState.tracks || [];
            const silent = !(assetMusicState.music_id || assetMusicState.playlist) || tracks.length === 0;
            const playingTrack = typeof currentTrackSrc !== 'undefined' && tracks.includes(currentTrackSrc)
                ? currentTrackSrc : tracks[0];
            const selected = button.dataset.assetId === 'no_music' ? silent : !silent && (
                button.dataset.playlistName
                    ? button.dataset.playlistName === (assetMusicState.music_id || assetMusicState.playlist)
                    && tracks.includes(button.dataset.trackUrl)
                    : button.dataset.trackUrl === playingTrack
            );
            button.classList.toggle('currently-playing', selected);
            if (selected) button.setAttribute('aria-current', 'true');
            else button.removeAttribute('aria-current');
            const indicator = button.querySelector('.asset-playing-indicator');
            indicator.hidden = !selected;
            indicator.textContent = silent ? '✓ Selected' : assetMusicState.paused ? 'Ⅱ Paused' : '▶ Playing';
            if (button.classList.contains('playlist-push-button')) {
                const label = button.querySelector('.asset-label') || button.querySelector('span:not(.asset-playing-indicator)');
                if (label) {
                    label.hidden = selected;
                    label.textContent = selected ? '' : 'Push';
                }
            }
            if (!selected) {
                const action = button.querySelector('small');
                if (action && (action.textContent === 'Pushed!' || action.textContent === 'Pushing…')) {
                    action.textContent = 'Push';
                }
            }
        }

        async function loadExtraAssets(kind) {
            const panel = kind === 'playlists' ? document.getElementById('playlists-manager-content') : extraAssetPanels[kind];
            const version = ++assetLoadVersion;
            panel.textContent = 'Loading…';
            stampManagerCount.textContent = '…';
            try {
                const tid = (typeof theaterId !== 'undefined' && theaterId) || (typeof currenttheaterId !== 'undefined' && currenttheaterId) || new URLSearchParams(window.location.search).get('theater_id');
                const res = await fetch(`/api/theaters/${encodeURIComponent(tid)}/${kind}`);
                if (!res.ok) throw new Error('Unable to load assets.');
                const assets = await res.json();
                if (version !== assetLoadVersion || activeStampManagerTab !== kind) return;
                panel.replaceChildren();
                const count = kind === 'playlists' ? assets.reduce((n, p) => n + p.tracks.length, 0) : assets.length;
                stampManagerCount.textContent = `${count}`;
                stampManagerCount.title = `${count} ${kind === 'playlists' ? 'tracks' : 'animations'}`;
                if (!count) {
                    panel.textContent = `No ${kind} found for this theater.`;
                    return;
                }
                if (kind === 'playlists') {
                    const listContainer = document.createElement('div');
                    listContainer.className = 'playlist-accordion-list';
                    assets.forEach((playlist, index) => {
                        const initialExpanded = expandedPlaylists.has(playlist.name);
                        const group = document.createElement('div');
                        group.className = 'playlist-group';
                        group.dataset.playlistName = playlist.name;
                        if (!initialExpanded) {
                            group.classList.add('minimized');
                        }

                        const header = document.createElement('div');
                        header.className = 'playlist-header';

                        const toggleBtn = document.createElement('button');
                        toggleBtn.type = 'button';
                        toggleBtn.className = 'playlist-header-toggle';
                        toggleBtn.dataset.playlistName = playlist.name;
                        toggleBtn.setAttribute('aria-expanded', initialExpanded ? 'true' : 'false');
                        toggleBtn.setAttribute('aria-controls', `playlist-tracks-${index}`);
                        toggleBtn.setAttribute('aria-label', `Toggle ${playlist.name} playlist`);

                        const chevron = document.createElement('span');
                        chevron.className = 'playlist-chevron';
                        chevron.setAttribute('aria-hidden', 'true');
                        chevron.textContent = initialExpanded ? '▼' : '▶';

                        const title = document.createElement('span');
                        title.className = 'playlist-title';
                        title.textContent = playlist.name;

                        const countBadge = document.createElement('span');
                        countBadge.className = 'playlist-track-count';
                        const trackCount = playlist.tracks ? playlist.tracks.length : 0;
                        countBadge.textContent = `${trackCount} ${trackCount === 1 ? 'track' : 'tracks'}`;

                        toggleBtn.append(chevron, title, countBadge);

                        const pushBtn = createAssetButton(playlist, kind);
                        pushBtn.classList.add('playlist-header-push');
                        pushBtn.setAttribute('aria-label', `Push ${playlist.name}`);

                        header.append(toggleBtn, pushBtn);

                        const trackList = document.createElement('div');
                        trackList.className = 'asset-track-list playlist-track-list';
                        trackList.id = `playlist-tracks-${index}`;
                        trackList.hidden = !initialExpanded;

                        playlist.tracks.forEach(track => {
                            trackList.appendChild(createAssetButton(track, kind));
                        });

                        const setExpanded = (expanded) => {
                            toggleBtn.setAttribute('aria-expanded', `${expanded}`);
                            chevron.textContent = expanded ? '▼' : '▶';
                            trackList.hidden = !expanded;
                            group.classList.toggle('minimized', !expanded);
                            if (expanded) {
                                expandedPlaylists.add(playlist.name);
                            } else {
                                expandedPlaylists.delete(playlist.name);
                            }
                        };

                        toggleBtn.addEventListener('click', (e) => {
                            e.stopPropagation();
                            const isExpanded = toggleBtn.getAttribute('aria-expanded') === 'true';
                            setExpanded(!isExpanded);
                        });

                        header.addEventListener('click', (e) => {
                            if (e.target.closest('.playlist-header-push')) return;
                            const isExpanded = toggleBtn.getAttribute('aria-expanded') === 'true';
                            setExpanded(!isExpanded);
                        });

                        pushBtn.addEventListener('click', () => {
                            if (toggleBtn.getAttribute('aria-expanded') !== 'true') {
                                setExpanded(true);
                            }
                        });

                        group.append(header, trackList);
                        listContainer.appendChild(group);
                    });
                    panel.appendChild(listContainer);
                } else if (kind === 'animations') {
                    panel.className = 'animations-grid';
                    assets.forEach(asset => panel.appendChild(createAssetButton(asset, kind)));
                } else {
                    panel.className = 'asset-track-list';
                    assets.forEach(asset => panel.appendChild(createAssetButton(asset, kind)));
                }
            } catch (err) {
                if (version === assetLoadVersion && activeStampManagerTab === kind) {
                    panel.textContent = err.message;
                    stampManagerCount.textContent = '—';
                    const retry = document.createElement('button');
                    retry.type = 'button';
                    retry.className = 'panel-btn';
                    retry.textContent = 'Retry';
                    retry.addEventListener('click', () => loadExtraAssets(kind));
                    panel.appendChild(retry);
                }
            }
        }

        function createAssetButton(asset, kind) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = asset.tracks ? 'stamp-tab-btn playlist-push-button' : 'asset-push-button';
            button.dataset.assetSearch = JSON.stringify(asset);
            button.dataset.assetKind = kind;
            button.dataset.assetId = asset.id;
            button.dataset.playlistName = asset.tracks ? asset.name : '';
            if (asset.tracks) button.dataset.playlist = asset.name;
            button.dataset.trackUrl = asset.tracks ? asset.tracks[0]?.url || '' : asset.url || asset.id;
            button.title = asset.description || `Push ${asset.name}`;

            let action;
            if (kind === 'animations') {
                button.setAttribute('aria-label', `Push ${asset.name || 'animation'}`);
                const thumbContainer = document.createElement('div');
                thumbContainer.className = 'asset-thumb-container';

                const mediaUrl = asset.url;
                if (mediaUrl) {
                    const isVideo = mediaUrl.endsWith('.mp4') || mediaUrl.endsWith('.webm') || asset.type === 'video';
                    if (isVideo && (mediaUrl.endsWith('.mp4') || mediaUrl.endsWith('.webm'))) {
                        const video = document.createElement('video');
                        video.className = 'asset-thumb';
                        video.src = `${mediaUrl}#t=0.001`;
                        video.preload = 'metadata';
                        video.muted = true;
                        video.playsInline = true;
                        video.onerror = () => {
                            video.style.display = 'none';
                            if (!thumbContainer.querySelector('.asset-fallback-thumb')) {
                                const placeholder = document.createElement('div');
                                placeholder.className = 'asset-fallback-thumb';
                                placeholder.textContent = '🎬';
                                thumbContainer.appendChild(placeholder);
                            }
                        };
                        thumbContainer.appendChild(video);
                    } else {
                        const img = document.createElement('img');
                        img.className = 'asset-thumb';
                        img.src = mediaUrl;
                        img.alt = asset.name || '';
                        img.loading = 'lazy';
                        img.onerror = () => {
                            img.style.display = 'none';
                            if (!thumbContainer.querySelector('.asset-fallback-thumb')) {
                                const placeholder = document.createElement('div');
                                placeholder.className = 'asset-fallback-thumb';
                                placeholder.textContent = '🎬';
                                thumbContainer.appendChild(placeholder);
                            }
                        };
                        thumbContainer.appendChild(img);
                    }
                } else {
                    const placeholder = document.createElement('div');
                    placeholder.className = 'asset-fallback-thumb';
                    placeholder.textContent = '🎬';
                    thumbContainer.appendChild(placeholder);
                }

                const overlay = document.createElement('div');
                overlay.className = 'asset-overlay';
                action = document.createElement('small');
                action.textContent = 'Push';
                overlay.appendChild(action);
                thumbContainer.appendChild(overlay);
                button.appendChild(thumbContainer);

                const label = document.createElement('span');
                label.className = 'asset-label';
                label.hidden = true;
                label.style.display = 'none';
                button.appendChild(label);
            } else {
                const label = document.createElement('span');
                label.className = 'asset-label';
                label.textContent = asset.tracks ? 'Push' : (asset.description || asset.name);
                action = document.createElement('small');
                action.textContent = 'Push';
                action.hidden = Boolean(asset.tracks);
                const indicator = document.createElement('span');
                indicator.className = 'asset-playing-indicator';
                indicator.hidden = true;
                button.append(indicator, label, action);
                updateAssetPlaybackButton(button);
            }

            button.addEventListener('click', async () => {
                if (!isCurrentOrator()) return;
                revertAllOtherPushItems(button);
                button.disabled = true;
                action.textContent = 'Pushing…';
                button.classList.add('pushing');
                try {
                    const res = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/${kind}/push`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ id: asset.id }),
                    });
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.detail || 'Unable to push asset.');
                    showShareToast(data.message);
                    if (asset.tracks) {
                        updateAssetPlaybackSelection({
                            music_id: asset.name,
                            playlist: asset.name,
                            tracks: asset.tracks.map(t => t.url || t.id),
                            paused: false
                        });
                    } else if (asset.id === 'no_music') {
                        updateAssetPlaybackSelection({
                            music_id: '',
                            playlist: '',
                            tracks: [],
                            paused: false
                        });
                    } else if (kind === 'playlists') {
                        const parentGroup = button.closest('.playlist-group');
                        const plName = parentGroup ? parentGroup.dataset.playlistName : '';
                        const trackUrl = button.dataset.trackUrl || asset.url || asset.id;
                        currentTrackSrc = trackUrl;
                        updateAssetPlaybackSelection({
                            music_id: plName ? `${plName} / ${asset.name}` : asset.name,
                            playlist: plName,
                            tracks: [trackUrl],
                            paused: false
                        });
                    }
                    refreshCanvasState(['latest']);
                    revertAllOtherPushItems(button);
                    button.classList.add('pushed');
                    action.textContent = 'Pushed!';
                    setTimeout(() => {
                        button.classList.remove('pushed');
                        if (action.textContent === 'Pushed!') {
                            action.textContent = 'Push';
                        }
                    }, 2000);
                } catch (err) {
                    showShareToast(err.message);
                    action.textContent = 'Retry';
                } finally {
                    button.disabled = false;
                    button.classList.remove('pushing');
                }
            });
            return button;
        }

        function switchStampManagerTab(tab) {
            const isOrator = typeof isCurrentOrator === 'function' && isCurrentOrator();
            if (!isOrator && tab !== 'stamps') {
                tab = 'stamps';
            }
            activeStampManagerTab = tab;
            updateAssetGenerationUI();
            queueMicrotask(applyAssetSearch);
            ++assetLoadVersion;
            const stampManagerBodyEl = document.getElementById('stamp-manager-body');
            if (stampManagerBodyEl) {
                stampManagerBodyEl.scrollTop = 0;
            }
            for (const kind of ['playlists', 'animations']) {
                extraAssetPanels[kind].hidden = tab !== kind;
                document.getElementById(`stamp-tab-${kind}`)?.classList.toggle('active', tab === kind);
            }


            stampTabStampsBtn?.classList.toggle('active', tab === 'stamps');
            stampTabScenesBtn?.classList.toggle('active', tab === 'scenes');
            stampTabCharactersBtn?.classList.toggle('active', tab === 'characters');

            const stampGenForm = document.getElementById('stamp-generation-form');
            const stampGenStatus = document.getElementById('stamp-generation-status');
            const canGenerateScene = typeof isTheaterStampOwner !== 'undefined' && isTheaterStampOwner && tab === 'scenes';
            document.getElementById('scene-generation-form').hidden = !canGenerateScene;
            document.getElementById('scene-generation-status').hidden = !canGenerateScene;

            if (tab === 'playlists' || tab === 'animations') {
                for (const el of [stampManagerPermissionBanner, stampManagerGrid, stampManagerEmpty,
                    scenesManagerGrid, scenesManagerEmpty, charactersManagerGrid, charactersManagerEmpty]) {
                    if (el) el.style.display = 'none';
                }
                for (const el of [stampGenForm, stampGenStatus]) {
                    if (el) el.hidden = true;
                }
                loadExtraAssets(tab);
            } else if (tab === 'stamps') {
                if (stampManagerPermissionBanner) stampManagerPermissionBanner.style.display = '';
                if (stampManagerGrid) stampManagerGrid.style.display = '';
                if (scenesManagerGrid) scenesManagerGrid.style.display = 'none';
                if (scenesManagerEmpty) scenesManagerEmpty.style.display = 'none';
                if (charactersManagerGrid) charactersManagerGrid.style.display = 'none';
                if (charactersManagerEmpty) charactersManagerEmpty.style.display = 'none';

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
            } else if (tab === 'scenes') {
                if (stampManagerPermissionBanner) stampManagerPermissionBanner.style.display = 'none';
                if (stampManagerGrid) stampManagerGrid.style.display = 'none';
                if (stampManagerEmpty) stampManagerEmpty.style.display = 'none';
                if (charactersManagerGrid) charactersManagerGrid.style.display = 'none';
                if (charactersManagerEmpty) charactersManagerEmpty.style.display = 'none';
                if (scenesManagerGrid) scenesManagerGrid.style.display = 'grid';

                if (stampGenForm) stampGenForm.hidden = true;
                if (stampGenStatus) stampGenStatus.hidden = true;

                if (stampManagerCount) {
                    const count = cachedTheaterScenes ? cachedTheaterScenes.length : 0;
                    stampManagerCount.textContent = `${count}`;
                    stampManagerCount.title = `${count} adventure scenes`;
                }

                if (cachedTheaterScenes) {
                    renderScenesManagerGrid(cachedTheaterScenes);
                    preloadItemImages(cachedTheaterScenes);
                } else {
                    loadTheaterScenes();
                }
            } else if (tab === 'characters') {
                if (stampManagerPermissionBanner) stampManagerPermissionBanner.style.display = 'none';
                if (stampManagerGrid) stampManagerGrid.style.display = 'none';
                if (stampManagerEmpty) stampManagerEmpty.style.display = 'none';
                if (scenesManagerGrid) scenesManagerGrid.style.display = 'none';
                if (scenesManagerEmpty) scenesManagerEmpty.style.display = 'none';
                if (charactersManagerGrid) charactersManagerGrid.style.display = 'grid';

                if (stampGenForm) stampGenForm.hidden = true;
                if (stampGenStatus) stampGenStatus.hidden = true;

                if (stampManagerCount) {
                    const count = cachedTheaterCharacters ? cachedTheaterCharacters.length : 0;
                    stampManagerCount.textContent = `${count}`;
                    stampManagerCount.title = `${count} adventure characters`;
                }

                if (cachedTheaterCharacters) {
                    renderCharactersManagerGrid(cachedTheaterCharacters);
                    preloadItemImages(cachedTheaterCharacters);
                } else {
                    loadTheaterCharacters();
                }
            }
        }

        async function loadTheaterScenes() {
            const tid = (typeof theaterId !== 'undefined' && theaterId) || (typeof currenttheaterId !== 'undefined' && currenttheaterId) || new URLSearchParams(window.location.search).get('theater_id');
            if (!tid || isFetchingTheaterScenes) return;
            isFetchingTheaterScenes = true;
            try {
                const res = await fetch(`/api/theaters/${encodeURIComponent(tid)}/scenes`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                cachedTheaterScenes = Array.isArray(data) ? data : [];
                preloadItemImages(cachedTheaterScenes);
                if (activeStampManagerTab === 'scenes') {
                    renderScenesManagerGrid(cachedTheaterScenes);
                }
            } catch (err) {
                console.warn('Unable to load theater scenes:', err);
                if (activeStampManagerTab === 'scenes') {
                    if (scenesManagerGrid) scenesManagerGrid.innerHTML = '';
                    if (scenesManagerEmpty) {
                        scenesManagerEmpty.style.display = 'flex';
                        if (scenesEmptyMessage) scenesEmptyMessage.textContent = 'Unable to load scenes at this time.';
                    }
                }
            } finally {
                isFetchingTheaterScenes = false;
            }
        }

        function renderScenesManagerGrid(scenes) {
            if (!scenesManagerGrid) return;
            scenes = scenes || [];
            scenesManagerGrid.innerHTML = '';
            pendingSceneGenerations.forEach((job, jobId) => {
                const card = document.createElement('div');
                card.className = 'scene-card scene-card-generating';
                card.dataset.assetSearch = `${job.name} ${job.prompt}`;
                card.dataset.generationId = String(jobId);
                card.setAttribute('aria-busy', 'true');
                card.setAttribute('aria-label', `${job.name}: Generating`);
                card.title = `Generating “${job.name}” — ${job.prompt}`;
                const thumb = document.createElement('div');
                thumb.className = 'scene-card-thumb-container stamp-generation-placeholder';
                thumb.setAttribute('aria-hidden', 'true');
                const name = document.createElement('div');
                name.className = 'scene-card-name';
                name.textContent = job.name;
                const progress = document.createElement('div');
                progress.className = 'stamp-card-progress';
                progress.textContent = 'Generating…';
                card.append(thumb, name, progress);
                scenesManagerGrid.appendChild(card);
            });
            if (stampManagerCount && activeStampManagerTab === 'scenes') {
                stampManagerCount.textContent = `${scenes.length}` + (pendingSceneGenerations.size ? ` · ${pendingSceneGenerations.size} generating` : '');
                stampManagerCount.title = `${scenes.length} adventure scenes` + (pendingSceneGenerations.size ? ` · ${pendingSceneGenerations.size} generating` : '');
            }
            if (scenes.length === 0 && pendingSceneGenerations.size === 0) {
                if (scenesManagerEmpty) {
                    scenesManagerEmpty.style.display = 'flex';
                    if (scenesEmptyMessage) scenesEmptyMessage.textContent = 'No scenes found for this adventure.';
                }
                return;
            }
            if (scenesManagerEmpty) scenesManagerEmpty.style.display = 'none';

            scenes.forEach(scene => {
                const card = document.createElement('div');
                card.className = 'scene-card';
                card.dataset.assetSearch = JSON.stringify(scene);
                card.setAttribute('role', 'button');
                card.setAttribute('tabindex', '0');
                card.setAttribute('title', `Click to push "${scene.name}"`);
                card.dataset.sceneName = scene.name;
                if (scene.id) card.dataset.sceneId = scene.id;

                const thumbContainer = document.createElement('div');
                thumbContainer.className = 'scene-card-thumb-container';

                const imgUrl = scene.url || scene.image_url;
                if (imgUrl) {
                    const img = document.createElement('img');
                    img.className = 'scene-card-thumb';
                    img.src = imgUrl;
                    img.alt = scene.name;
                    img.loading = 'lazy';
                    img.onerror = () => {
                        img.style.display = 'none';
                        if (!thumbContainer.querySelector('.scene-card-fallback-thumb')) {
                            const placeholder = document.createElement('div');
                            placeholder.className = 'scene-card-fallback-thumb';
                            placeholder.style.cssText = 'width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:2rem;color:#64748b;';
                            placeholder.textContent = '🎬';
                            thumbContainer.appendChild(placeholder);
                        }
                    };
                    thumbContainer.appendChild(img);
                } else {
                    const placeholder = document.createElement('div');
                    placeholder.className = 'scene-card-fallback-thumb';
                    placeholder.style.cssText = 'width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:2rem;color:#64748b;';
                    placeholder.textContent = '🎬';
                    thumbContainer.appendChild(placeholder);
                }

                const overlay = document.createElement('div');
                overlay.className = 'scene-card-overlay';
                const badge = document.createElement('span');
                badge.className = 'scene-card-push-badge';
                badge.textContent = 'Push';
                overlay.appendChild(badge);
                thumbContainer.appendChild(overlay);

                card.appendChild(thumbContainer);

                const nameEl = document.createElement('div');
                nameEl.className = 'scene-card-name';
                nameEl.textContent = scene.name;
                card.appendChild(nameEl);

                if (scene.description) {
                    const descEl = document.createElement('div');
                    descEl.className = 'scene-card-desc';
                    descEl.textContent = scene.description;
                    card.appendChild(descEl);
                }

                card.addEventListener('click', () => pushSceneToCanvas(scene, card));
                card.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        pushSceneToCanvas(scene, card);
                    }
                });

                scenesManagerGrid.appendChild(card);
            });
        }

        async function pushSceneToCanvas(scene, cardEl) {
            const tid = (typeof theaterId !== 'undefined' && theaterId) || (typeof currenttheaterId !== 'undefined' && currenttheaterId) || new URLSearchParams(window.location.search).get('theater_id');
            if (!tid || isPushingScene) return;
            const isOrator = typeof isCurrentOrator === 'function' && isCurrentOrator();
            if (!isOrator) {
                if (typeof showShareToast === 'function') {
                    showShareToast('Only the active orator can push scenes.');
                }
                return;
            }

            revertAllOtherPushItems(cardEl);
            isPushingScene = true;
            if (cardEl) {
                cardEl.classList.add('pushing');
                const badge = cardEl.querySelector('.scene-card-push-badge');
                if (badge) badge.textContent = 'Pushing...';
            }

            try {
                const res = await fetch(`/api/theaters/${encodeURIComponent(tid)}/scenes/push`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: scene.name, scene_id: scene.id || '' })
                });
                const data = await res.json();
                if (!res.ok) {
                    throw new Error(typeof data.detail === 'string' ? data.detail : 'Failed to push scene.');
                }
                revertAllOtherPushItems(cardEl);
                if (cardEl) {
                    const badge = cardEl.querySelector('.scene-card-push-badge');
                    if (badge) badge.textContent = 'Pushed!';
                    setTimeout(() => {
                        if (badge && badge.textContent === 'Pushed!') badge.textContent = 'Push';
                    }, 2000);
                }
                if (typeof showShareToast === 'function') {
                    showShareToast(`Pushed "${scene.name}".`);
                }
                if (typeof fetchLatestImage === 'function') {
                    fetchLatestImage();
                } else if (typeof refreshCanvasState === 'function') {
                    refreshCanvasState(['latest']);
                }
            } catch (err) {
                console.error('Error pushing scene to canvas:', err);
                if (typeof showShareToast === 'function') {
                    showShareToast(err.message || 'Error pushing scene.');
                }
                if (cardEl) {
                    const badge = cardEl.querySelector('.scene-card-push-badge');
                    if (badge) badge.textContent = 'Failed';
                    setTimeout(() => {
                        if (badge) badge.textContent = 'Push';
                    }, 2000);
                }
            } finally {
                isPushingScene = false;
                if (cardEl) {
                    cardEl.classList.remove('pushing');
                }
            }
        }

        async function loadTheaterCharacters() {
            const tid = (typeof theaterId !== 'undefined' && theaterId) || (typeof currenttheaterId !== 'undefined' && currenttheaterId) || new URLSearchParams(window.location.search).get('theater_id');
            if (!tid || isFetchingTheaterCharacters) return;
            isFetchingTheaterCharacters = true;
            try {
                const res = await fetch(`/api/theaters/${encodeURIComponent(tid)}/characters`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                cachedTheaterCharacters = Array.isArray(data) ? data : [];
                preloadItemImages(cachedTheaterCharacters);
                if (activeStampManagerTab === 'characters') {
                    renderCharactersManagerGrid(cachedTheaterCharacters);
                }
            } catch (err) {
                console.warn('Unable to load theater characters:', err);
                if (activeStampManagerTab === 'characters') {
                    if (charactersManagerGrid) charactersManagerGrid.innerHTML = '';
                    if (charactersManagerEmpty) {
                        charactersManagerEmpty.style.display = 'flex';
                        if (charactersEmptyMessage) charactersEmptyMessage.textContent = 'Unable to load characters at this time.';
                    }
                }
            } finally {
                isFetchingTheaterCharacters = false;
            }
        }

        function renderCharactersManagerGrid(characters) {
            if (!charactersManagerGrid) return;
            charactersManagerGrid.innerHTML = '';
            if (stampManagerCount && activeStampManagerTab === 'characters') {
                stampManagerCount.textContent = `${characters.length}`;
                stampManagerCount.title = `${characters.length} adventure characters`;
            }
            if (!characters || characters.length === 0) {
                if (charactersManagerEmpty) {
                    charactersManagerEmpty.style.display = 'flex';
                    if (charactersEmptyMessage) charactersEmptyMessage.textContent = 'No characters found for this adventure.';
                }
                return;
            }
            if (charactersManagerEmpty) charactersManagerEmpty.style.display = 'none';

            characters.forEach(char => {
                const card = document.createElement('div');
                card.className = 'character-card';
                card.dataset.assetSearch = JSON.stringify(char);
                card.setAttribute('role', 'button');
                card.setAttribute('tabindex', '0');
                card.setAttribute('title', `Click to push "${char.name}"`);
                card.dataset.characterName = char.name;
                if (char.id) card.dataset.characterId = char.id;

                const thumbContainer = document.createElement('div');
                thumbContainer.className = 'character-card-thumb-container';

                const imgUrl = char.url || char.image_url;
                if (imgUrl) {
                    const img = document.createElement('img');
                    img.className = 'character-card-thumb';
                    img.src = imgUrl;
                    img.alt = char.name;
                    img.loading = 'lazy';
                    img.onerror = () => {
                        img.style.display = 'none';
                        if (!thumbContainer.querySelector('.character-card-fallback-thumb')) {
                            const placeholder = document.createElement('div');
                            placeholder.className = 'character-card-fallback-thumb';
                            placeholder.style.cssText = 'width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:2rem;color:#64748b;';
                            placeholder.textContent = '👤';
                            thumbContainer.appendChild(placeholder);
                        }
                    };
                    thumbContainer.appendChild(img);
                } else {
                    const placeholder = document.createElement('div');
                    placeholder.className = 'character-card-fallback-thumb';
                    placeholder.style.cssText = 'width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:2rem;color:#64748b;';
                    placeholder.textContent = '👤';
                    thumbContainer.appendChild(placeholder);
                }

                const overlay = document.createElement('div');
                overlay.className = 'character-card-overlay';
                const badge = document.createElement('span');
                badge.className = 'character-card-push-badge';
                badge.textContent = 'Push';
                overlay.appendChild(badge);
                thumbContainer.appendChild(overlay);

                card.appendChild(thumbContainer);

                const nameEl = document.createElement('div');
                nameEl.className = 'character-card-name';
                nameEl.textContent = char.name;
                card.appendChild(nameEl);

                const textToShow = char.description || char.personality || char.quirk || '';
                if (textToShow) {
                    const descEl = document.createElement('div');
                    descEl.className = 'character-card-desc';
                    descEl.textContent = textToShow;
                    card.appendChild(descEl);
                }

                card.addEventListener('click', () => pushCharacterToCanvas(char, card));
                card.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        pushCharacterToCanvas(char, card);
                    }
                });

                charactersManagerGrid.appendChild(card);
            });
        }

        async function pushCharacterToCanvas(character, cardEl) {
            const tid = (typeof theaterId !== 'undefined' && theaterId) || (typeof currenttheaterId !== 'undefined' && currenttheaterId) || new URLSearchParams(window.location.search).get('theater_id');
            if (!tid || isPushingCharacter) return;
            const isOrator = typeof isCurrentOrator === 'function' && isCurrentOrator();
            if (!isOrator) {
                if (typeof showShareToast === 'function') {
                    showShareToast('Only the active orator can push characters.');
                }
                return;
            }

            revertAllOtherPushItems(cardEl);
            isPushingCharacter = true;
            if (cardEl) {
                cardEl.classList.add('pushing');
                const badge = cardEl.querySelector('.character-card-push-badge');
                if (badge) badge.textContent = 'Pushing...';
            }

            try {
                const res = await fetch(`/api/theaters/${encodeURIComponent(tid)}/characters/push`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: character.name, character_id: character.id || '' })
                });
                const data = await res.json();
                if (!res.ok) {
                    throw new Error(typeof data.detail === 'string' ? data.detail : 'Failed to push character.');
                }
                revertAllOtherPushItems(cardEl);
                if (cardEl) {
                    const badge = cardEl.querySelector('.character-card-push-badge');
                    if (badge) badge.textContent = 'Pushed!';
                    setTimeout(() => {
                        if (badge && badge.textContent === 'Pushed!') badge.textContent = 'Push';
                    }, 2000);
                }
                if (typeof showShareToast === 'function') {
                    showShareToast(`Pushed "${character.name}".`);
                }
                if (typeof fetchLatestImage === 'function') {
                    fetchLatestImage();
                } else if (typeof refreshCanvasState === 'function') {
                    refreshCanvasState(['latest']);
                }
            } catch (err) {
                console.error('Error pushing character to canvas:', err);
                if (typeof showShareToast === 'function') {
                    showShareToast(err.message || 'Error pushing character.');
                }
                if (cardEl) {
                    const badge = cardEl.querySelector('.character-card-push-badge');
                    if (badge) badge.textContent = 'Failed';
                    setTimeout(() => {
                        if (badge) badge.textContent = 'Push';
                    }, 2000);
                }
            } finally {
                isPushingCharacter = false;
                if (cardEl) {
                    cardEl.classList.remove('pushing');
                }
            }
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
        const viewerCollabGuideBtn = document.getElementById('viewer-collab-guide-btn');
        const viewerCollabBar = document.getElementById('viewer-collab-bar');
        const viewerCollabBarStatus = document.getElementById('viewer-collab-bar-status');
        let viewerCollabEnabled = false;
        updateTextAnnotationAvailability();

        function updateViewerCollabBar(enabled) {
            viewerCollabBar.style.display = isCurrentOrator() ? 'none' : 'flex';
            if (typeof gameRenderer !== 'undefined' && gameRenderer && typeof gameRenderer.render === 'function') {
                gameRenderer.render();
            }
            viewerCollabBar.classList.toggle('is-enabled', Boolean(enabled));
            viewerCollabBarStatus.textContent = enabled ? 'Collaboration: On' : 'Collaboration: Off';
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
        if (viewerCollabGuideBtn) {
            viewerCollabGuideBtn.addEventListener('click', openViewerCollabGuide);
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
                isNavigating = false;
                isPagedBack = false;
                hasNewHeadWhilePagedBack = false;
                if (oratorCursorIndex >= 0 && oratorCursorIndex < imageHistory.length) {
                    displayHistoryImage(oratorCursorIndex, 'none');
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
                    setTimeout(() => openOratorHowtoModal({ openMicConfigOnClose: true }), 400);
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
        let activeViewersList = [];
        let batonTimerInterval = null;

        const batonStatusBadge = document.getElementById('baton-status-badge');
        const menuTakeBackBatonBtn = document.getElementById('menu-take-back-baton-btn');
        const oratorsModal = document.getElementById('orators-modal');
        const oratorsCloseBtn = document.getElementById('orators-close-btn');
        const acceptBatonBtn = document.getElementById('accept-baton-btn');
        const declineBatonBtn = document.getElementById('decline-baton-btn');

        // Fetch current user details
        getCanvasAuthState()
            .then(data => {
                if (data.authenticated && data.user) {
                    currentUser = data.user;
                    if (currentBatonState) {
                        updateBatonStateUI(currentBatonState, activeViewersList);
                    }
                    checkBatonState();
                }
            })
            .catch(() => { });

        function checkBatonState() {
            if (!theaterId) return;
            fetch(`/api/theaters/${encodeURIComponent(theaterId)}/baton`)
                .then(res => res.ok ? res.json() : null)
                .then(data => {
                    if (data) {
                        updateBatonStateUI(data, data.active_viewers || []);
                    }
                })
                .catch(err => console.error("Error fetching baton state:", err));
        }

        function updateBatonStateUI(batonState, activeViewers) {
            currentBatonState = batonState;
            activeViewersList = activeViewers || [];

            const isOwner = currentUser && batonState.owner && currentUser.id === batonState.owner.id;
            isTheaterStampOwner = Boolean(isOwner);
            updateStampGenerationPermission();
            const isActiveOrator = currentUser && batonState.active_orator && currentUser.id === batonState.active_orator.id;
            const isContributorInList = currentUser && (batonState.contributors || []).some(u => u.id === currentUser.id);
            window._isContributor = Boolean(isOwner || isActiveOrator || isContributorInList);
            if (typeof gameRenderer !== 'undefined' && gameRenderer && typeof gameRenderer.render === 'function') {
                gameRenderer.render();
            }

            if (typeof isStampManagerOpen !== 'undefined' && isStampManagerOpen) {
                updateStampManagerPermissionBanner();
                if (cachedUserStamps) renderStampManagerGrid(cachedUserStamps);
            }

            // This is the single baton control: it identifies the current
            // holder and opens the owner's assignment menu.
            if (batonStatusBadge) {
                const holder = batonState.active_orator || batonState.owner;
                const holderName = holder && (holder.username || holder.name) || 'Unknown';
                batonStatusBadge.textContent = `👑 @${holderName}`;
                batonStatusBadge.disabled = !isOwner;
                batonStatusBadge.style.cursor = isOwner ? 'pointer' : 'default';
                batonStatusBadge.title = isOwner ? 'Open baton menu' : `Baton is held by @${holderName}`;
                batonStatusBadge.style.background = isOwner ? '#ec4899' : 'rgba(236, 72, 153, 0.15)';
                batonStatusBadge.style.color = isOwner ? '#fff' : '#f9a8d4';
                batonStatusBadge.style.borderColor = isOwner ? '#fbcfe8' : 'rgba(236, 72, 153, 0.3)';
                batonStatusBadge.style.boxShadow = isOwner ? '0 0 12px rgba(236, 72, 153, 0.45)' : 'none';
            }

            if (menuTakeBackBatonBtn) {
                menuTakeBackBatonBtn.style.display = (isOwner && !isActiveOrator) ? 'inline-flex' : 'none';
            }

            const isNowOrator = Boolean(isActiveOrator || (isOwner && !batonState.active_orator));
            applyRoleUI(isNowOrator);

            // Check pending request targeting local user
            if (batonState.baton_request && currentUser && batonState.baton_request.target_user_id === currentUser.id) {
                showBatonRequestModal(batonState.baton_request);
            } else {
                hideBatonRequestModal();
            }

            // Refresh modal contents if open
            if (oratorsModal && oratorsModal.classList.contains('active')) {
                renderOratorManagementModalContent();
            }
        }

        function showBatonRequestModal(batonReq) {
            const modal = document.getElementById('baton-request-modal');
            if (!modal) return;
            if (modal.classList.contains('active')) return;

            modal.classList.add('active');
            modal.setAttribute('aria-hidden', 'false');

            const timerBar = document.getElementById('baton-timer-bar');
            const totalMs = 30000;
            const startTime = Date.now();

            if (batonTimerInterval) clearInterval(batonTimerInterval);

            batonTimerInterval = setInterval(() => {
                const elapsed = Date.now() - startTime;
                const remaining = Math.max(0, totalMs - elapsed);
                const pct = (remaining / totalMs) * 100;
                if (timerBar) timerBar.style.width = `${pct}%`;

                if (remaining <= 0) {
                    clearInterval(batonTimerInterval);
                    declineBatonPass();
                }
            }, 100);
        }

        function hideBatonRequestModal() {
            const modal = document.getElementById('baton-request-modal');
            if (modal) {
                modal.classList.remove('active');
                modal.setAttribute('aria-hidden', 'true');
            }
            if (batonTimerInterval) {
                clearInterval(batonTimerInterval);
                batonTimerInterval = null;
            }
        }

        function acceptBatonPass() {
            if (!theaterId) return;
            fetch(`/api/theaters/${encodeURIComponent(theaterId)}/baton/accept`, { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    hideBatonRequestModal();
                    if (data && !data.detail) {
                        updateBatonStateUI(data, data.active_viewers);
                    }
                })
                .catch(err => console.error("Error accepting baton:", err));
        }

        function declineBatonPass() {
            if (!theaterId) return;
            fetch(`/api/theaters/${encodeURIComponent(theaterId)}/baton/decline`, { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    hideBatonRequestModal();
                    if (data && !data.detail) {
                        updateBatonStateUI(data, data.active_viewers);
                    }
                })
                .catch(err => console.error("Error declining baton:", err));
        }

        function takeBackBaton() {
            if (!theaterId) return;
            fetch(`/api/theaters/${encodeURIComponent(theaterId)}/baton/takeback`, { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    if (data && !data.detail) {
                        updateBatonStateUI(data, data.active_viewers);
                    }
                })
                .catch(err => console.error("Error taking back baton:", err));
        }

        function renderOratorManagementModalContent() {
            if (!currentBatonState) return;

            const activeViewersContainer = document.getElementById('active-viewers-list');
            const contributorsContainer = document.getElementById('contributors-list');
            const allowedIds = (currentBatonState.contributors || []).map(u => u.id);

            // Render Active Connected Viewers
            if (activeViewersContainer) {
                const viewers = activeViewersList.filter(u => u.id !== (currentBatonState.owner ? currentBatonState.owner.id : null));
                if (viewers.length === 0) {
                    activeViewersContainer.innerHTML = '<div style="font-size:0.85rem; color:#64748b; padding:6px;">No other connected viewers online.</div>';
                } else {
                    activeViewersContainer.innerHTML = viewers.map(v => {
                        const isAllowed = allowedIds.includes(v.id);
                        return `
                            <div style="display:flex; align-items:center; justify-content:space-between; padding:6px 10px; background:rgba(255,255,255,0.05); border-radius:6px;">
                                <span style="font-size:0.9rem; color:#fff;">👤 @${v.username}</span>
                                ${isAllowed
                                ? `<span style="font-size:0.75rem; color:#4ade80; font-weight:600;">Allowed</span>`
                                : `<button onclick="addContributor(${v.id})" style="background:#6366f1; border:none; color:#fff; padding:4px 10px; border-radius:12px; font-size:0.75rem; cursor:pointer;">+ Contributor</button>`
                            }
                            </div>
                        `;
                    }).join('');
                }
            }

            // Render Contributor List
            if (contributorsContainer) {
                const allowedList = currentBatonState.contributors || [];
                if (allowedList.length === 0) {
                    contributorsContainer.innerHTML = '<div style="font-size:0.85rem; color:#64748b; padding:6px;">No contributor added yet.</div>';
                } else {
                    contributorsContainer.innerHTML = allowedList.map(u => {
                        const isTargeting = currentBatonState.baton_request && currentBatonState.baton_request.target_user_id === u.id;
                        return `
                            <div style="display:flex; align-items:center; justify-content:space-between; padding:6px 10px; background:rgba(255,255,255,0.05); border-radius:6px;">
                                <span style="font-size:0.9rem; color:#fff;">🎙️ @${u.username}</span>
                                <div style="display:flex; gap:6px;">
                                    ${isTargeting
                                ? `<span style="font-size:0.75rem; color:#fbbf24; padding:3px 8px; font-weight:600;">Pending 30s...</span>`
                                : `<button onclick="requestBatonPass(${u.id})" style="background:#f59e0b; border:none; color:#fff; padding:4px 10px; border-radius:12px; font-size:0.75rem; cursor:pointer;">Pass Baton 🎙️</button>`
                            }
                                    <button onclick="removeContributor(${u.id})" style="background:rgba(239,68,68,0.2); border:1px solid rgba(239,68,68,0.4); color:#f87171; padding:4px 8px; border-radius:12px; font-size:0.75rem; cursor:pointer;">Remove</button>
                                </div>
                            </div>
                        `;
                    }).join('');
                }
            }
        }

        window.addContributor = function (targetUserId) {
            if (!theaterId) return;
            fetch(`/api/theaters/${encodeURIComponent(theaterId)}/baton/contributors`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target_user_id: targetUserId })
            }).then(r => r.json()).then(data => updateBatonStateUI(data, data.active_viewers));
        };

        window.removeContributor = function (targetUserId) {
            if (!theaterId) return;
            fetch(`/api/theaters/${encodeURIComponent(theaterId)}/baton/contributors/${targetUserId}`, {
                method: 'DELETE'
            }).then(r => r.json()).then(data => updateBatonStateUI(data, data.active_viewers));
        };

        window.requestBatonPass = function (targetUserId) {
            if (!theaterId) return;
            fetch(`/api/theaters/${encodeURIComponent(theaterId)}/baton/request`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target_user_id: targetUserId, timeout_seconds: 30 })
            }).then(r => r.json()).then(data => updateBatonStateUI(data, data.active_viewers));
        };

        if (batonStatusBadge) {
            batonStatusBadge.addEventListener('click', () => {
                if (batonStatusBadge.disabled) return;
                if (oratorsModal) {
                    oratorsModal.classList.add('active');
                    oratorsModal.setAttribute('aria-hidden', 'false');
                    renderOratorManagementModalContent();
                }
            });
        }
        if (oratorsCloseBtn) {
            oratorsCloseBtn.addEventListener('click', () => {
                if (oratorsModal) {
                    oratorsModal.classList.remove('active');
                    oratorsModal.setAttribute('aria-hidden', 'true');
                }
            });
        }
        if (menuTakeBackBatonBtn) {
            menuTakeBackBatonBtn.addEventListener('click', takeBackBaton);
        }
        if (acceptBatonBtn) {
            acceptBatonBtn.addEventListener('click', acceptBatonPass);
        }
        if (declineBatonBtn) {
            declineBatonBtn.addEventListener('click', declineBatonPass);
        }



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

            await refreshMicrophoneDevices(true);
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

        // Panel drawer collapse/expand logic
        const popoutExpandBtn = document.getElementById('popout-expand-btn');

        const SMALL_WINDOW_BREAKPOINT = 1280;
        let userToggledPanel = false;
        let isPanelCollapsed = window.innerWidth <= SMALL_WINDOW_BREAKPOINT;

        // Cover intermediate layouts so image effects never visibly fold during resizing.
        const canvasContainer = document.getElementById('image-container');
        let canvasSettleTimer = null;
        let canvasRevealFrame = null;
        let canvasSettleUntil = 0;

        function darkenCanvasUntilStable(delay = 120) {
            canvasContainer.classList.add('layout-resizing');
            clearTimeout(canvasSettleTimer);
            cancelAnimationFrame(canvasRevealFrame);
            const now = Date.now();
            const targetTime = now + delay;
            if (targetTime > canvasSettleUntil) {
                canvasSettleUntil = targetTime;
            }
            const effectiveDelay = Math.max(delay, canvasSettleUntil - now);
            canvasSettleTimer = setTimeout(() => {
                canvasSettleUntil = 0;
                resizeRendererCanvas();
                resizeDoodleCanvas();
                // Let animated renderers paint at the final size before revealing them.
                canvasRevealFrame = requestAnimationFrame(() => {
                    canvasRevealFrame = requestAnimationFrame(() => {
                        canvasContainer.classList.remove('layout-resizing');
                    });
                });
            }, effectiveDelay);
        }

        let canvasLayout = canvasContainer.getBoundingClientRect();
        const canvasResizeObserver = new ResizeObserver(() => {
            const nextLayout = canvasContainer.getBoundingClientRect();
            if (nextLayout.width !== canvasLayout.width || nextLayout.height !== canvasLayout.height) {
                darkenCanvasUntilStable();
                canvasLayout = nextLayout;
            }
        });
        canvasResizeObserver.observe(canvasContainer);

        function setPanelCollapsed(collapsed, manual = false) {
            if (manual) userToggledPanel = true;
            isPanelCollapsed = collapsed;
            const sidebar = document.getElementById('chat-sidebar');
            if (sidebar) {
                if (sidebar.classList.contains('collapsed') !== collapsed) {
                    darkenCanvasUntilStable(400);
                }
                if (collapsed) {
                    sidebar.classList.add('collapsed');
                } else {
                    sidebar.classList.remove('collapsed');
                }
            }
            if (popoutExpandBtn) {
                popoutExpandBtn.style.display = collapsed ? 'flex' : 'none';
            }
            if (document.body.classList.contains('fullscreen-cinematic')) {
                showCinematicUI();
            }
        }

        if (popoutExpandBtn) {
            popoutExpandBtn.addEventListener('click', () => setPanelCollapsed(false, true));
        }

        // Default-hide chat panel if the window is small
        if (isPanelCollapsed) {
            const sidebar = document.getElementById('chat-sidebar');
            if (sidebar) sidebar.classList.add('collapsed');
            if (popoutExpandBtn) popoutExpandBtn.style.display = 'flex';
        }

        window.addEventListener('resize', () => {
            if (!userToggledPanel && !isFullscreenActive()) {
                const shouldCollapse = window.innerWidth <= SMALL_WINDOW_BREAKPOINT;
                if (shouldCollapse !== isPanelCollapsed) {
                    setPanelCollapsed(shouldCollapse, false);
                }
            }
        });

        window.toggleFullScreen = toggleFullScreen;
        window.togglePopoutPanel = () => setPanelCollapsed(!isPanelCollapsed, true);

        // Chat side arrow toggle tab listener
        const chatSideToggleBtn = document.getElementById('chat-side-toggle-btn');
        if (chatSideToggleBtn) {
            chatSideToggleBtn.addEventListener('click', () => setPanelCollapsed(!isPanelCollapsed, true));
        }

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
        const minimalModeInput = document.getElementById('minimal-mode-input');

        if (minimalModeInput) {
            minimalModeInput.checked = minimalMode;
            minimalModeInput.addEventListener('change', () => {
                setMinimalMode(minimalModeInput.checked);
            });
        }

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
                openOratorHowtoModal();
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

        // Microphone device selection
        const micModeToggle = document.getElementById('menu-mic-mode-toggle');
        if (micModeToggle) {
            micModeToggle.checked = micMode === 'push-to-talk';
            micModeToggle.addEventListener('change', () => {
                cancelMicCapture();
                micMode = micModeToggle.checked ? 'push-to-talk' : 'toggle';
                localStorage.setItem('narratron_mic_mode', micMode);
                updateMicToggleUI();
            });
        }
        updateMicToggleUI();
        const micDeviceSelect = document.getElementById('menu-mic-device-select');
        const micDeviceRefresh = document.getElementById('menu-mic-device-refresh');
        const micDeviceHint = document.getElementById('menu-mic-device-hint');
        async function refreshMicrophoneDevices(requestPermission = false) {
            if (!navigator.mediaDevices?.enumerateDevices || !micDeviceSelect) return;
            try {
                if (requestPermission) {
                    const permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    permissionStream.getTracks().forEach(track => track.stop());
                }
                const selected = localStorage.getItem('narratron_mic_device_id') || '';
                const devices = (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === 'audioinput');
                micDeviceSelect.replaceChildren(new Option('System default microphone', ''));
                devices.forEach((device, index) => micDeviceSelect.add(new Option(device.label || `Microphone ${index + 1} (allow access to identify)`, device.deviceId)));
                micDeviceSelect.value = [...micDeviceSelect.options].some(option => option.value === selected) ? selected : '';
                window.NARRATRON_MIC_DEVICE_ID = micDeviceSelect.value;
                if (micDeviceHint) micDeviceHint.textContent = devices.some(device => device.label) ? 'Your selection is used when you connect.' : 'Refresh and allow microphone access to identify devices.';
            } catch (error) { if (micDeviceHint) micDeviceHint.textContent = 'Unable to list microphones. Check browser permissions.'; }
        }
        if (micDeviceRefresh) micDeviceRefresh.addEventListener('click', () => refreshMicrophoneDevices(true));
        if (micDeviceSelect) micDeviceSelect.addEventListener('change', () => {
            window.NARRATRON_MIC_DEVICE_ID = micDeviceSelect.value;
            localStorage.setItem('narratron_mic_device_id', micDeviceSelect.value);
            if (micDeviceHint) micDeviceHint.textContent = 'Saved in this browser. Reconnect to use this microphone.';
        });
        navigator.mediaDevices?.addEventListener('devicechange', () => refreshMicrophoneDevices());
        refreshMicrophoneDevices();

        // Microphone test: capture selected-device audio with the same 16 kHz,
        // signed 16-bit PCM pipeline used for live narration, then play it locally.
        const micTestRecordBtn = document.getElementById('menu-mic-test-record');
        const micTestPlayBtn = document.getElementById('menu-mic-test-play');
        const micTestStatus = document.getElementById('menu-mic-test-status');
        const MIC_TEST_SAMPLE_RATE = 16000;
        const MIC_TEST_MAX_MS = 10000;
        let stopMicTestCapture = null;
        let micTestStarting = false;
        let micTestChunks = [];
        let micTestBytes = 0;
        let micTestTimer = null;
        let micTestPlayback = null;
        let micTestPlaybackContext = null;

        function setMicTestStatus(message) {
            if (micTestStatus) micTestStatus.textContent = message;
        }

        function setMicTestRecordingUI(recording) {
            if (!micTestRecordBtn) return;
            micTestRecordBtn.textContent = recording ? 'Stop recording' : 'Record test';
            micTestRecordBtn.setAttribute('aria-pressed', String(recording));
        }

        function stopMicTestRecording(message = 'Recording ready. Play it back to verify your microphone.') {
            if (micTestTimer) window.clearTimeout(micTestTimer);
            micTestTimer = null;
            const stop = stopMicTestCapture;
            stopMicTestCapture = null;
            if (typeof stop === 'function') stop();
            setMicTestRecordingUI(false);
            if (micTestPlayBtn) micTestPlayBtn.disabled = micTestBytes === 0;
            setMicTestStatus(micTestBytes ? message : 'No audio was captured. Check browser microphone permission and try again.');
        }

        async function startMicTestRecording() {
            if (micTestStarting) return;
            if (stopMicTestCapture) {
                stopMicTestRecording();
                return;
            }
            if (isRecording) {
                setMicTestStatus('Disconnect the live microphone before running a local mic test.');
                return;
            }
            if (typeof listenForSpeech !== 'function') {
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
                    onError: (error) => setMicTestStatus(`Microphone test failed: ${error.message}`),
                });
                stopMicTestCapture = stop;
                micTestTimer = window.setTimeout(
                    () => stopMicTestRecording('10-second recording ready. Play it back to verify your microphone.'),
                    MIC_TEST_MAX_MS,
                );
            } catch (error) {
                setMicTestRecordingUI(false);
                setMicTestStatus(`Could not start microphone test: ${error.message}`);
            } finally {
                micTestStarting = false;
            }
        }

        async function playMicTestRecording() {
            if (!micTestBytes) return;
            if (micTestPlayback) micTestPlayback.stop();
            if (micTestPlaybackContext && micTestPlaybackContext.state !== 'closed') {
                await micTestPlaybackContext.close();
            }

            const pcm = new Uint8Array(micTestBytes);
            let offset = 0;
            micTestChunks.forEach((chunk) => { pcm.set(chunk, offset); offset += chunk.byteLength; });
            const context = new AudioContext({ sampleRate: MIC_TEST_SAMPLE_RATE });
            const audioBuffer = context.createBuffer(1, pcm.byteLength / 2, MIC_TEST_SAMPLE_RATE);
            const samples = audioBuffer.getChannelData(0);
            const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);
            for (let index = 0; index < samples.length; index += 1) samples[index] = view.getInt16(index * 2, true) / 32768;
            const source = context.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(context.destination);
            source.onended = () => {
                if (micTestPlayback === source) {
                    micTestPlayback = null;
                    setMicTestStatus('Playback finished. If you heard yourself clearly, your mic and PCM path are working.');
                }
                if (context.state !== 'closed') context.close();
            };
            micTestPlaybackContext = context;
            micTestPlayback = source;
            await context.resume();
            source.start();
            setMicTestStatus('Playing your PCM microphone test…');
        }

        if (micTestRecordBtn) micTestRecordBtn.addEventListener('click', startMicTestRecording);
        if (micTestPlayBtn) micTestPlayBtn.addEventListener('click', playMicTestRecording);

        // Microphone Sensitivity Slider Logic
        const micSensSlider = document.getElementById('menu-mic-sensitivity-slider');
        const micSensValDisplay = document.getElementById('menu-mic-sensitivity-val');

        function setMicSensitivity(percentVal, saveDb = false) {
            const val = Math.max(0, Math.min(100, parseInt(percentVal, 10) || 50));
            if (micSensSlider) micSensSlider.value = val;
            if (micSensValDisplay) micSensValDisplay.textContent = `${val}%`;

            const s = val / 100.0;
            // record-pcm uses RMS VAD.  Higher sensitivity lowers the threshold.
            window.MIC_DETECT_THRESHOLD = 0.01 * Math.pow(10, (0.5 - s) * 2);
            window.MIC_SENSITIVITY = s;

            try {
                localStorage.setItem('narratron_mic_sensitivity', val.toString());
            } catch (e) { }

            if (saveDb) {
                fetch('/api/auth/mic-sensitivity', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mic_sensitivity: s })
                }).then(res => {
                    if (res.ok) canvasAuthStatePromise = null;
                }).catch(err => console.debug('Failed to sync mic_sensitivity to server:', err));
            }
        }

        if (micSensSlider) {
            micSensSlider.addEventListener('input', (e) => {
                setMicSensitivity(e.target.value, false);
            });
            micSensSlider.addEventListener('change', (e) => {
                setMicSensitivity(e.target.value, true);
            });
        }

        (function initMicSensitivity() {
            let initialVal = 50;
            try {
                const stored = localStorage.getItem('narratron_mic_sensitivity');
                if (stored !== null && !isNaN(parseInt(stored, 10))) {
                    initialVal = parseInt(stored, 10);
                }
            } catch (e) { }

            getCanvasAuthState()
                .then(data => {
                    if (data && data.authenticated && data.user && data.user.mic_sensitivity !== undefined && data.user.mic_sensitivity !== null) {
                        const userVal = Math.round(data.user.mic_sensitivity * 100);
                        setMicSensitivity(userVal, false);
                    } else {
                        setMicSensitivity(initialVal, false);
                    }
                })
                .catch(() => setMicSensitivity(initialVal, false));
        })();

        // ========================================
        // Orator How-To Modal Logic
        // ========================================
        const micConfigModal = document.getElementById('mic-config-modal');
        const menuItemMicConfig = document.getElementById('menu-item-mic-config');
        const micConfigCloseBtn = document.getElementById('mic-config-close-btn');
        const micConfigDoneBtn = document.getElementById('mic-config-done-btn');

        function openMicConfigModal() {
            if (!micConfigModal) return;
            if (oratorHowtoModal) {
                delete oratorHowtoModal.dataset.openMicConfigOnClose;
                oratorHowtoModal.classList.remove('active');
                oratorHowtoModal.setAttribute('aria-hidden', 'true');
            }
            closePulloutMenu();
            refreshMicrophoneDevices();
            micConfigModal.classList.add('active');
            micConfigModal.setAttribute('aria-hidden', 'false');
        }

        function closeMicConfigModal() {
            actionWheel.cancelRebinding();
            if (!micConfigModal) return;
            if (stopMicTestCapture) stopMicTestRecording();
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
            micConfigModal.classList.remove('active');
            micConfigModal.setAttribute('aria-hidden', 'true');
        }

        if (menuItemMicConfig) menuItemMicConfig.addEventListener('click', openMicConfigModal);
        if (micConfigCloseBtn) micConfigCloseBtn.addEventListener('click', closeMicConfigModal);
        if (micConfigDoneBtn) micConfigDoneBtn.addEventListener('click', closeMicConfigModal);
        if (micConfigModal) {
            micConfigModal.addEventListener('click', (event) => {
                if (event.target === micConfigModal) closeMicConfigModal();
            });
        }

        const oratorHowtoModal = document.getElementById('orator-howto-modal');
        const howtoCloseBtn = document.getElementById('howto-close-btn');
        const howtoAckBtn = document.getElementById('howto-ack-btn');

        function openOratorHowtoModal({ openMicConfigOnClose = false } = {}) {
            if (!oratorHowtoModal) return;
            oratorHowtoModal.dataset.openMicConfigOnClose = String(openMicConfigOnClose);
            oratorHowtoModal.classList.add('active');
            oratorHowtoModal.setAttribute('aria-hidden', 'false');
        }

        function closeOratorHowtoModal() {
            if (!oratorHowtoModal) return;
            const openMicConfigOnClose = oratorHowtoModal.dataset.openMicConfigOnClose === 'true';
            delete oratorHowtoModal.dataset.openMicConfigOnClose;
            oratorHowtoModal.classList.remove('active');
            oratorHowtoModal.setAttribute('aria-hidden', 'true');
            try {
                localStorage.setItem('narratron_orator_howto_seen', 'true');
            } catch (e) { }
            if (openMicConfigOnClose) openMicConfigModal();
        }

        if (howtoCloseBtn) howtoCloseBtn.addEventListener('click', closeOratorHowtoModal);
        if (howtoAckBtn) howtoAckBtn.addEventListener('click', closeOratorHowtoModal);

        if (oratorHowtoModal) {
            oratorHowtoModal.addEventListener('click', (e) => {
                if (e.target === oratorHowtoModal) {
                    closeOratorHowtoModal();
                }
            });
        }

        // ========================================
        // Viewer Collaboration Guide Modal Logic
        // ========================================
        const viewerCollabGuideModal = document.getElementById('viewer-collab-guide-modal');
        const viewerCollabGuideCloseBtn = document.getElementById('viewer-collab-guide-close-btn');
        const viewerCollabGuideAckBtn = document.getElementById('viewer-collab-guide-ack-btn');
        const viewerCollabStatus = document.getElementById('viewer-collab-status');

        function updateViewerCollabStatus(enabled) {
            updateViewerCollabBar(enabled);
            if (!viewerCollabStatus) return;
            if (enabled) {
                viewerCollabStatus.textContent = '🟢 Collaboration Mode is on. Narratron will receive the top viewer suggestion and the doodled canvas on its next update.';
                viewerCollabStatus.style.background = 'rgba(34, 197, 94, 0.12)';
                viewerCollabStatus.style.borderColor = 'rgba(74, 222, 128, 0.4)';
                viewerCollabStatus.style.color = '#bbf7d0';
            } else {
                viewerCollabStatus.textContent = '⚪ Collaboration Mode is currently off. You can still doodle, suggest, and vote so everything is ready when the orator turns it on.';
                viewerCollabStatus.style.background = 'rgba(148, 163, 184, 0.1)';
                viewerCollabStatus.style.borderColor = 'rgba(148, 163, 184, 0.35)';
                viewerCollabStatus.style.color = '#cbd5e1';
            }
        }

        function openViewerCollabGuide() {
            if (!viewerCollabGuideModal) return;
            updateViewerCollabStatus(viewerCollabEnabled);
            viewerCollabGuideModal.classList.add('active');
            viewerCollabGuideModal.setAttribute('aria-hidden', 'false');
        }

        function closeViewerCollabGuide() {
            if (!viewerCollabGuideModal) return;
            viewerCollabGuideModal.classList.remove('active');
            viewerCollabGuideModal.setAttribute('aria-hidden', 'true');
            localStorage.setItem(`narratron_viewer_collab_guide_seen:${theaterId}`, 'true');
        }

        if (viewerCollabGuideCloseBtn) viewerCollabGuideCloseBtn.addEventListener('click', closeViewerCollabGuide);
        if (viewerCollabGuideAckBtn) viewerCollabGuideAckBtn.addEventListener('click', closeViewerCollabGuide);
        if (viewerCollabGuideModal) {
            viewerCollabGuideModal.addEventListener('click', (e) => {
                if (e.target === viewerCollabGuideModal) closeViewerCollabGuide();
            });
        }

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                if (isOratorCommandOpen()) {
                    closeOratorCommand();
                } else if (pulloutOverlay && pulloutOverlay.classList.contains('active')) {
                    closePulloutMenu();
                } else if (micConfigModal && micConfigModal.classList.contains('active')) {
                    closeMicConfigModal();
                } else if (oratorHowtoModal && oratorHowtoModal.classList.contains('active')) {
                    closeOratorHowtoModal();
                } else if (viewerCollabGuideModal && viewerCollabGuideModal.classList.contains('active')) {
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
        window.openOratorHowtoModal = openOratorHowtoModal;
        window.closeOratorHowtoModal = closeOratorHowtoModal;
        window.openMicConfigModal = openMicConfigModal;
        window.closeMicConfigModal = closeMicConfigModal;
        window.openTheaterConfigModal = openTheaterConfigModal;
        window.closeTheaterConfigModal = closeTheaterConfigModal;
        setupTheaterReport(theaterId, closePulloutMenu);
