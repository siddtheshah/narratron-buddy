// Generic, host-rendered declarative views. No theater HTML, CSS or JavaScript executes.
const TAGS = { column: 'div', row: 'div', grid: 'div', text: 'p', button: 'button', svg: 'svg', rect: 'rect', circle: 'circle', svg_text: 'text' };
const SVG = new Set(['svg', 'rect', 'circle', 'svg_text']);
const STYLES = new Set(['gap', 'padding', 'color', 'background', 'fontSize', 'fontWeight', 'borderRadius', 'outline', 'aspectRatio', 'textAlign']);
const ATTRIBUTES = new Set(['viewBox', 'x', 'y', 'width', 'height', 'cx', 'cy', 'r', 'rx', 'fill', 'stroke', 'stroke-width', 'text-anchor', 'dominant-baseline']);

export function createGameRenderer({ container, toggleButton, actionUrl, canEdit, refresh }) {
    const stylesheet = document.createElement('link');
    stylesheet.rel = 'stylesheet';
    stylesheet.href = '/static/css/game-renderer.css?v=3';
    document.head.append(stylesheet);
    let game = null;
    let pending = false;
    let error = '';
    let signature = '';
    let open = false;

    toggleButton.addEventListener('click', event => {
        event.stopPropagation();
        if (!game) return;
        open = !open;
        draw();
    });
    toggleButton.addEventListener('pointerdown', event => event.stopPropagation());

    async function send(action) {
        if (pending || !canEdit() || !game?.view) return;
        if (action.confirmation && !window.confirm(action.confirmation)) return;
        pending = true;
        error = '';
        const revision = game.revision;
        const definitionHash = game.definition_hash;
        draw();
        try {
            const response = await fetch(actionUrl(), {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: action.name, arguments: action.arguments, revision }),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Action rejected.');
            if (game?.definition_hash === definitionHash && result.revision >= game.revision) game = result;
        } catch (failure) {
            error = failure.message || 'Could not submit action.';
        } finally {
            pending = false;
            draw();
            refresh();
        }
    }

    function nodeView(node, depth = 0) {
        if (!Object.hasOwn(TAGS, node.kind) || depth > 12) return document.createTextNode('');
        const element = SVG.has(node.kind)
            ? document.createElementNS('http://www.w3.org/2000/svg', TAGS[node.kind])
            : document.createElement(TAGS[node.kind]);
        element.setAttribute('class', `game-node game-${node.kind}`);
        const props = node.props || {};
        if (props.id) element.dataset.gameId = String(props.id);
        if (props.label) element.setAttribute('aria-label', String(props.label));
        if (props.text !== undefined) element.textContent = String(props.text);
        if (props.pressed !== undefined) element.setAttribute('aria-pressed', String(Boolean(props.pressed)));
        for (const [name, value] of Object.entries(props)) {
            if (STYLES.has(name) && typeof value === 'string' && value.length <= 100
                && !/[\\;{}<>@]|url\s*\(|expression\s*\(/i.test(value)) {
                // Background accepts colors only: image-set() can fetch URLs
                // even without a url() token.
                if (!['background', 'color'].includes(name) || (CSS.supports('color', value) && !/var\s*\(/i.test(value))) {
                    element.style[name] = value;
                }
            }
            if (SVG.has(node.kind) && ATTRIBUTES.has(name) && String(value).length <= 100
                && !/url\s*\(|[<>{}\\]/i.test(String(value))) element.setAttribute(name, String(value));
        }
        if (node.kind === 'grid') {
            const columns = Math.max(1, Math.min(32, Number(props.columns) || 1));
            element.style.gridTemplateColumns = `repeat(${Math.floor(columns)}, minmax(0, 1fr))`;
        }
        if (node.kind === 'button') {
            element.type = 'button';
            element.disabled = pending || !canEdit() || Boolean(props.disabled) || !node.action;
            if (node.action) element.addEventListener('click', () => send(node.action));
        }
        for (const child of node.children || []) element.append(nodeView(child, depth + 1));
        return element;
    }

    function message(text, className) {
        const element = document.createElement('p');
        element.className = className;
        element.textContent = text;
        return element;
    }

    function draw() {
        if (!game) open = false;
        toggleButton.hidden = !game;
        toggleButton.setAttribute('aria-expanded', String(open));
        toggleButton.title = open ? 'Close game' : 'Open game';
        container.hidden = !game || !open;
        container.replaceChildren();
        if (!game) return;
        const panel = document.createElement('section');
        panel.className = 'game-panel';
        panel.setAttribute('aria-busy', String(pending));
        for (const event of ['pointerdown', 'pointermove', 'pointerup', 'click', 'dblclick', 'wheel']) {
            panel.addEventListener(event, e => e.stopPropagation());
        }
        for (const node of game.view || []) panel.append(nodeView(node));
        if (pending) {
            const status = message('Resolving action…', 'game-message');
            status.setAttribute('role', 'status');
            panel.append(status);
        }
        if (!canEdit()) panel.append(message('Read-only · the active orator controls the game.', 'game-message'));
        if (error || game.error) {
            const alert = message(error || game.error, 'game-error');
            alert.setAttribute('role', 'alert');
            panel.append(alert);
        }
        container.append(panel);
    }

    return {
        render(next) {
            if (next?.definition_hash === game?.definition_hash && next?.revision < game?.revision) return;
            const nextSignature = JSON.stringify([next, canEdit()]);
            if (nextSignature === signature) return;
            signature = nextSignature;
            game = next;
            draw();
        },
    };
}
