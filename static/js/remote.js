(() => {
    'use strict';
    const connectForm = document.getElementById('connect-form');
    const theaterInput = document.getElementById('theater');
    const controls = document.getElementById('controls');
    const status = document.getElementById('connection-status');
    const feedback = document.getElementById('feedback');
    const command = document.getElementById('command');
    const canvasLink = document.getElementById('canvas-link');
    let theaterId = '';
    let state = null;
    let busy = false;
    let generation = 0;
    let refreshSequence = 0;
    let pollTimer;

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
        collaboration.textContent = `Audience collaboration ${state?.viewer_collab_enabled ? 'on' : 'off'}`;
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
            status.dataset.connected = 'true';
            status.textContent = next.agent_running ? 'Connected · Narratron ready' : 'Connected · Start Narratron on the main canvas';
        } catch (error) {
            if (version !== generation || sequence !== refreshSequence) return;
            state = null;
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

    function connect(value) {
        let id = value.trim();
        if (id.includes('://') || id.startsWith('/')) {
            try { id = new URL(id, location.origin).searchParams.get('theater_id') || ''; }
            catch { id = ''; }
        }
        if (!/^[a-zA-Z0-9_-]+$/.test(id)) {
            message('Enter a theater ID or a canvas link containing theater_id.', true);
            return;
        }
        generation += 1;
        clearTimeout(pollTimer);
        theaterId = id;
        theaterInput.value = id;
        state = null;
        busy = false;
        render();
        message('');
        status.textContent = 'Connecting…';
        status.dataset.connected = 'false';
        const url = new URL('/remote', location.origin);
        url.searchParams.set('theater_id', id);
        history.replaceState(null, '', url);
        canvasLink.href = `/canvas?${new URLSearchParams({theater_id: id})}`;
        canvasLink.hidden = false;
        void refresh(generation);
    }

    async function send(path, payload, success, onSuccess = () => {}) {
        if (!state || busy) return;
        const version = generation;
        busy = true;
        refreshSequence += 1;
        clearTimeout(pollTimer);
        render();
        message('Sending…');
        try {
            await request(path, payload);
            if (version !== generation) return;
            onSuccess();
            message(success);
        } catch (error) {
            if (version !== generation) return;
            message(error.name === 'TypeError' || error.name === 'TimeoutError'
                ? 'Delivery could not be confirmed. Check the canvas before trying again.' : error.message, true);
        } finally {
            if (version === generation) {
                await refresh(version);
                busy = false;
                render();
            }
        }
    }

    connectForm.addEventListener('submit', event => {
        event.preventDefault();
        connect(theaterInput.value);
    });
    controls.querySelectorAll('[data-action]').forEach(button => {
        button.addEventListener('click', () => {
            void send(`${basePath()}/orator-action`, {action: button.dataset.action}, `${button.textContent.trim()} — accepted.`);
        });
    });
    document.getElementById('collaboration').addEventListener('click', () => {
        void send(`${basePath()}/collab`, {enabled: !state?.viewer_collab_enabled}, 'Collaboration updated.');
    });
    document.getElementById('command-form').addEventListener('submit', event => {
        event.preventDefault();
        const text = command.value.trim();
        if (!text) { message('Enter an instruction first.', true); return; }
        void send(`/api/orator/command?${new URLSearchParams({theater_id: theaterId})}`, {text}, 'Instruction accepted.', () => {
            if (command.value.trim() === text) command.value = '';
        });
    });
    document.addEventListener('visibilitychange', () => {
        clearTimeout(pollTimer);
        if (!document.hidden && theaterId && !busy) void refresh(generation);
    });
    const initial = new URLSearchParams(location.search).get('theater_id');
    render();
    if (initial) connect(initial);
})();
