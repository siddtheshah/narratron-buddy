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
    const remoteControls = document.getElementById('remote-controls');
    const previewView = document.getElementById('canvas-preview');
    const openPreview = document.getElementById('open-preview');
    const closePreview = document.getElementById('close-preview');
    const theaterId = new URLSearchParams(location.search).get('theater_id');
    let state = null;
    let busy = false;
    let generation = 0;
    let refreshSequence = 0;
    let pollTimer;
    let previewUrl = null;
    let previewLoaded = false;
    let openedPreview = false;

    function renderView() {
        const showPreview = location.hash === '#preview';
        remoteControls.hidden = showPreview;
        previewView.hidden = !showPreview;
        document.title = showPreview ? 'Canvas preview · Narratron Remote' : 'Narratron Remote';
        if (showPreview) {
            updatePreview();
            closePreview.focus();
        } else {
            openPreview.focus();
        }
    }

    openPreview.addEventListener('click', () => {
        openedPreview = true;
        location.hash = 'preview';
    });
    function dismissPreview() {
        if (openedPreview) {
            history.back();
        } else {
            history.replaceState(null, '', location.pathname + location.search);
            renderView();
        }
    }
    closePreview.addEventListener('click', dismissPreview);
    window.addEventListener('hashchange', renderView);

    const previewFrame = previewView.querySelector('.preview-frame');
    let swipeStart = null;
    previewFrame.addEventListener('touchstart', event => {
        swipeStart = event.touches.length === 1
            ? {x: event.touches[0].clientX, y: event.touches[0].clientY} : null;
    }, {passive: true});
    previewFrame.addEventListener('touchmove', event => {
        if (event.touches.length !== 1) swipeStart = null;
    }, {passive: true});
    previewFrame.addEventListener('touchcancel', () => { swipeStart = null; }, {passive: true});
    previewFrame.addEventListener('touchend', event => {
        const start = swipeStart;
        swipeStart = null;
        if (!start || event.touches.length || previewView.hidden) return;
        const touch = event.changedTouches[0];
        const distanceX = touch.clientX - start.x;
        const distanceY = touch.clientY - start.y;
        if (distanceY >= 80 && distanceY > Math.abs(distanceX) * 1.5) dismissPreview();
    }, {passive: true});

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

    function message(text, error = false) {
        feedback.textContent = text;
        feedback.dataset.error = String(error);
    }

    function render() {
        controls.disabled = !state || busy;
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
            button.textContent = `${pinned ? 'Unpin' : 'Pin'} ${label}`;
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
            updatePreview();
            status.dataset.connected = 'true';
            status.textContent = next.agent_running ? 'Connected · Agent on' : 'Connected · Agent off';
        } catch (error) {
            if (version !== generation || sequence !== refreshSequence) return;
            state = null;
            updatePreview();
            status.dataset.connected = 'false';
            status.textContent = error.name === 'TypeError' || error.name === 'TimeoutError'
                ? 'Connection lost. Retrying…' : error.message;
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
        controls.querySelectorAll('[data-error]').forEach(control => {
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
        } catch (error) {
            if (version !== generation) return;
            const detail = error.name === 'TypeError' || error.name === 'TimeoutError'
                ? 'Delivery could not be confirmed. Check the canvas before trying again.' : error.message;
            button.dataset.error = detail;
            button.closest('.bar').dataset.error = detail;
            button.setAttribute('aria-describedby', 'feedback');
            message(detail, true);
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
        if (!text) { message('Enter an instruction first.', true); return; }
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
        status.textContent = 'Open the remote from your theater.';
    }
})();
