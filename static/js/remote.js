(() => {
    'use strict';
    const controls = document.getElementById('controls');
    const status = document.getElementById('connection-status');
    const feedback = document.getElementById('feedback');
    const command = document.getElementById('command');
    const canvasLink = document.getElementById('canvas-link');
    const previewImage = document.getElementById('preview-image');
    const previewStatus = document.getElementById('preview-status');
    const previewPlaceholder = document.getElementById('preview-placeholder');
    const previewView = document.getElementById('canvas-preview');
    const tabs = [...document.querySelectorAll('[data-view]')];
    const views = tabs.map(tab => document.getElementById(tab.getAttribute('aria-controls')));
    const viewContainer = document.getElementById('remote-views');
    const scenesCarousel = document.getElementById('scenes-carousel');
    const musicCarousel = document.getElementById('music-carousel');
    const assetStatus = document.getElementById('asset-status');
    const retryAssets = document.getElementById('retry-assets');
    const toast = document.getElementById('toast');
    const toastMessage = document.getElementById('toast-message');
    const toastDismiss = document.getElementById('toast-dismiss');
    let toastTimer = null;
    let lastRefreshError = null;
    let assetSequence = 0;
    let assetsLoading = false;
    const theaterId = new URLSearchParams(location.search).get('theater_id');
    let state = null;
    let busy = false;
    let generation = 0;
    let refreshSequence = 0;
    let pollTimer;
    let previewUrl = null;
    let previewLoaded = false;

    function activeIndex() {
        const index = tabs.findIndex(tab => location.hash === `#${tab.dataset.view}`);
        return index < 0 ? 1 : index;
    }

    function renderView(focus = false) {
        const selected = activeIndex();
        tabs.forEach((tab, index) => {
            tab.setAttribute('aria-selected', String(index === selected));
            tab.tabIndex = index === selected ? 0 : -1;
            views[index].hidden = index !== selected;
        });
        document.title = `${tabs[selected].textContent} · Narratron Remote`;
        if (focus) tabs[selected].focus();
        if (selected === 2) updatePreview();
        if (selected === 0) void loadAssets();
    }

    function selectView(index) {
        if (index < 0 || index >= tabs.length || index === activeIndex()) return;
        location.hash = tabs[index].dataset.view;
    }

    tabs.forEach((tab, index) => {
        tab.addEventListener('click', () => selectView(index));
        tab.addEventListener('keydown', event => {
            const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length
                : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length
                : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
            if (next === null) return;
            event.preventDefault();
            selectView(next);
        });
    });
    window.addEventListener('hashchange', () => renderView(true));

    let swipeStart = null;
    viewContainer.addEventListener('touchstart', event => {
        const nested = event.target.closest('.asset-carousel, .asset-card, input, button');
        swipeStart = event.touches.length === 1 && !nested
            ? {x: event.touches[0].clientX, y: event.touches[0].clientY} : null;
    }, {passive: true});
    viewContainer.addEventListener('touchmove', event => {
        if (event.touches.length !== 1) swipeStart = null;
    }, {passive: true});
    viewContainer.addEventListener('touchcancel', () => { swipeStart = null; }, {passive: true});
    viewContainer.addEventListener('touchend', event => {
        const start = swipeStart;
        swipeStart = null;
        if (!start || event.touches.length || !event.changedTouches.length) return;
        const touch = event.changedTouches[0];
        const dx = touch.clientX - start.x;
        const dy = touch.clientY - start.y;
        if (Math.abs(dx) >= 60 && Math.abs(dx) > Math.abs(dy) * 1.5) {
            selectView(activeIndex() + (dx < 0 ? 1 : -1));
        }
    }, {passive: true});

    function createAssetCard(item, type) {
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

        function trigger() {
            if (!state || busy) return;
            const path = `${basePath()}/${type === 'scene' ? 'scenes' : 'playlists'}/push`;
            const payload = type === 'scene' ? {scene_id: item.id} : {id: item.id};
            void send(card, path, payload);
        }

        let pointerStartX = 0;
        let pointerStartY = 0;
        let moved = false;
        let lastTap = 0;
        let lastTriggerTime = 0;

        card.addEventListener('pointerdown', event => {
            pointerStartX = event.clientX;
            pointerStartY = event.clientY;
            moved = false;
        });

        card.addEventListener('pointermove', event => {
            if (Math.hypot(event.clientX - pointerStartX, event.clientY - pointerStartY) > 10) {
                moved = true;
            }
        });

        card.addEventListener('pointerup', event => {
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

        card.addEventListener('dblclick', event => {
            event.preventDefault();
            const now = Date.now();
            if (now - lastTriggerTime >= 500) {
                lastTriggerTime = now;
                trigger();
            }
        });

        card.addEventListener('keydown', event => {
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

    async function loadAssets() {
        const sequence = ++assetSequence;
        scenesCarousel.replaceChildren();
        musicCarousel.replaceChildren();
        retryAssets.hidden = true;
        assetsLoading = true;
        assetStatus.textContent = state ? 'Loading assets…' : 'Reconnect to browse assets.';
        if (!state) { assetsLoading = false; return; }
        try {
            const [scenes, playlists] = await Promise.all([
                request(`${basePath()}/scenes`),
                request(`${basePath()}/playlists`),
            ]);
            if (sequence !== assetSequence || !state) return;

            const musicEntries = (playlists || []).flatMap(group => [
                {...group, isGroup: true},
                ...(group.tracks || []).map(track => ({...track, playlistName: group.name, isTrack: true})),
            ]);

            if (!scenes.length && !musicEntries.length) {
                assetStatus.textContent = 'No scenes available in this theater yet.';
            } else if (!scenes.length) {
                assetStatus.textContent = 'No scenes available in this theater yet.';
            } else {
                assetStatus.textContent = '';
            }

            if (scenes.length) {
                for (const scene of scenes) {
                    scenesCarousel.append(createAssetCard(scene, 'scene'));
                }
            } else {
                const empty = document.createElement('div');
                empty.className = 'asset-empty';
                empty.textContent = 'No scenes available.';
                scenesCarousel.append(empty);
            }

            if (musicEntries.length) {
                for (const music of musicEntries) {
                    musicCarousel.append(createAssetCard(music, 'music'));
                }
            } else {
                const empty = document.createElement('div');
                empty.className = 'asset-empty';
                empty.textContent = 'No music available.';
                musicCarousel.append(empty);
            }
        } catch (error) {
            if (sequence !== assetSequence) return;
            assetStatus.textContent = error.message;
            retryAssets.hidden = false;
            showToast(error.message, 'error');
        } finally {
            if (sequence === assetSequence) assetsLoading = false;
        }
    }

    retryAssets.addEventListener('click', () => { void loadAssets(); });

    function renderPreview() {
        previewImage.hidden = !state || !previewLoaded;
        previewPlaceholder.hidden = Boolean(state && previewLoaded);
        previewStatus.textContent = !state ? 'Unavailable' : !previewUrl ? 'No scene' : previewLoaded ? 'Current scene' : 'Loading…';
        previewPlaceholder.textContent = !state ? 'Reconnect to see the canvas.' : !previewUrl ? 'No scene on the canvas yet.' : 'Loading scene…';
    }

    function updatePreview() {
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

    function dismissToast() {
        if (!toast || toast.hidden) return;
        clearTimeout(toastTimer);
        toast.classList.remove('show');
        const onEnd = () => {
            toast.removeEventListener('transitionend', onEnd);
            if (!toast.classList.contains('show')) {
                toast.hidden = true;
            }
        };
        toast.addEventListener('transitionend', onEnd);
        setTimeout(() => {
            if (!toast.classList.contains('show')) {
                toast.hidden = true;
            }
        }, 300);
    }

    function showToast(text, type = 'error') {
        if (!toast || !toastMessage) return;
        clearTimeout(toastTimer);
        toastMessage.textContent = text;
        toast.dataset.type = type;
        toast.hidden = false;
        void toast.offsetWidth;
        toast.classList.add('show');
        toastTimer = setTimeout(() => {
            dismissToast();
        }, 5000);
    }

    if (toastDismiss) {
        toastDismiss.addEventListener('click', () => {
            dismissToast();
        });
    }

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && toast && !toast.hidden && toast.classList.contains('show')) {
            dismissToast();
        }
    });

    function message(text, error = false) {
        feedback.textContent = text;
        feedback.dataset.error = String(error);
    }

    function render() {
        controls.disabled = !state || busy;
        if (!state) {
            assetSequence += 1;
            assetsLoading = false;
            scenesCarousel.replaceChildren();
            musicCarousel.replaceChildren();
            assetStatus.textContent = 'Reconnect to browse assets.';
        } else if (!views[0].hidden && !assetsLoading && !scenesCarousel.children.length && !musicCarousel.children.length && retryAssets.hidden
            && assetStatus.textContent === 'Reconnect to browse assets.') {
            void loadAssets();
        }
        controls.querySelectorAll('[data-live]').forEach(button => {
            button.disabled = !state?.agent_running;
        });
        command.disabled = !state?.agent_running;
        for (const [id, pinned, label] of [
            ['scene-pin', state?.pinned, 'scene'],
            ['music-pin', state?.music_pinned, 'music'],
        ]) {
            const button = document.getElementById(id);
            button.setAttribute('aria-pressed', String(Boolean(pinned)));
            const pinLabel = `${pinned ? 'Unpin' : 'Pin'} ${label}`;
            button.setAttribute('aria-label', pinLabel);
            button.title = pinLabel;
        }
        const collaboration = document.getElementById('collaboration');
        collaboration.setAttribute('aria-pressed', String(Boolean(state?.viewer_collab_enabled)));
        const collaborationLabel = `Turn collaboration ${state?.viewer_collab_enabled ? 'off' : 'on'}`;
        collaboration.setAttribute('aria-label', collaborationLabel);
        collaboration.title = collaborationLabel;
        document.getElementById('collaboration-status').textContent = `Collaboration ${state?.viewer_collab_enabled ? 'on' : 'off'}`;
        const agentToggle = document.getElementById('agent-toggle');
        agentToggle.setAttribute('aria-pressed', String(Boolean(state?.agent_running)));
        const agentLabel = state?.agent_running ? 'Turn agent off' : 'Turn agent on';
        agentToggle.setAttribute('aria-label', agentLabel);
        agentToggle.title = agentLabel;
    }

    async function request(path, body) {
        const response = await fetch(path, {
            method: body === undefined ? 'GET' : 'POST',
            credentials: 'same-origin',
            cache: 'no-store',
            headers: body === undefined ? {} : {'Content-Type': 'application/json'},
            body: body === undefined ? undefined : JSON.stringify(body),
            signal: AbortSignal.timeout(10000),
        });
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status}).`);
        }
        return response.json();
    }

    function basePath() {
        return `/api/theaters/${encodeURIComponent(theaterId)}`;
    }

    async function refresh(version) {
        const sequence = ++refreshSequence;
        clearTimeout(pollTimer);
        try {
            const next = await request(`${basePath()}/remote`);
            if (version !== generation || sequence !== refreshSequence) return;
            state = next;
            lastRefreshError = null;
            updatePreview();
            status.dataset.connected = 'true';
            status.textContent = next.agent_running ? 'Connected · Agent on' : 'Connected · Agent off';
        } catch (error) {
            if (version !== generation || sequence !== refreshSequence) return;
            state = null;
            updatePreview();
            status.dataset.connected = 'false';
            status.textContent = 'Disconnected';
            const detail = error.name === 'TypeError' || error.name === 'TimeoutError'
                ? 'Connection lost. Retrying…' : error.message;
            if (lastRefreshError !== detail) {
                lastRefreshError = detail;
                showToast(detail, 'error');
            }
        } finally {
            if (version === generation && sequence === refreshSequence) {
                render();
                if (!document.hidden) pollTimer = setTimeout(() => refresh(version), 3000);
            }
        }
    }

    async function send(button, path, payload, onSuccess = () => {}) {
        if (!state || busy) return;
        const version = generation;
        busy = true;
        refreshSequence += 1;
        clearTimeout(pollTimer);
        viewContainer.querySelectorAll('[data-error]').forEach(control => {
            delete control.dataset.error;
            control.removeAttribute('aria-describedby');
        });
        button.setAttribute('aria-busy', 'true');
        render();
        message('Sending…');
        try {
            await request(path, payload);
            if (version !== generation) return;
            onSuccess();
            message('Accepted.');
            if (button.closest('.asset-card')) assetStatus.textContent = 'Sent to canvas.';
        } catch (error) {
            if (version !== generation) return;
            const detail = error.name === 'TypeError' || error.name === 'TimeoutError'
                ? 'Delivery could not be confirmed. Check the canvas before trying again.' : error.message;
            button.dataset.error = detail;
            button.closest('.bar, .asset-card').dataset.error = detail;
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

    document.getElementById('agent-toggle').addEventListener('click', event => {
        const action = state?.agent_running ? 'stop' : 'start';
        void send(event.currentTarget, `${basePath()}/agent/${action}`, {});
    });
    controls.querySelectorAll('[data-action]').forEach(button => {
        button.addEventListener('click', () => {
            void send(button, `${basePath()}/orator-action`, {action: button.dataset.action});
        });
    });
    document.getElementById('collaboration').addEventListener('click', event => {
        void send(event.currentTarget, `${basePath()}/collab`, {enabled: !state?.viewer_collab_enabled});
    });
    document.getElementById('command-form').addEventListener('submit', event => {
        event.preventDefault();
        const text = command.value.trim();
        if (!text) {
            message('', true);
            showToast('Enter an instruction first.', 'error');
            return;
        }
        void send(event.currentTarget.querySelector('button[type="submit"]'), `/api/orator/command?${new URLSearchParams({theater_id: theaterId})}`, {text}, () => {
            if (command.value.trim() === text) command.value = '';
        });
    });
    document.addEventListener('visibilitychange', () => {
        clearTimeout(pollTimer);
        if (!document.hidden && theaterId && !busy) void refresh(generation);
    });
    render();
    renderView();
    if (theaterId && /^[a-zA-Z0-9_-]+$/.test(theaterId)) {
        document.getElementById('theater-id').textContent = theaterId;
        document.getElementById('theater-id').title = theaterId;
        canvasLink.href = `/canvas?${new URLSearchParams({theater_id: theaterId})}`;
        canvasLink.hidden = false;
        void refresh(generation);
    } else {
        const msg = 'Open the remote from your theater.';
        status.textContent = msg;
        showToast(msg, 'error');
    }
})();
