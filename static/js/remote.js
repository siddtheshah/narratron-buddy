(() => {
    'use strict';
    const controls = document.getElementById('controls');
    const status = document.getElementById('connection-status');
    const feedback = document.getElementById('feedback');
    const command = document.getElementById('command');
    const canvasLink = document.getElementById('canvas-link');
    const theaterId = new URLSearchParams(location.search).get('theater_id');
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
            status.dataset.connected = 'true';
            status.textContent = next.agent_running ? 'Connected · Agent on' : 'Connected · Agent off';
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
