// Generic, host-rendered declarative views. No theater HTML, CSS or JavaScript executes.
const TAGS = {
    column: 'div', row: 'div', grid: 'div', text: 'p', button: 'button',
    svg: 'svg', g: 'g', path: 'path', rect: 'rect', circle: 'circle', ellipse: 'ellipse',
    line: 'line', polyline: 'polyline', polygon: 'polygon', svg_text: 'text', tspan: 'tspan',
    defs: 'defs', linearGradient: 'linearGradient', radialGradient: 'radialGradient',
    stop: 'stop', clipPath: 'clipPath', mask: 'mask', symbol: 'symbol',
};
const SVG = new Set([
    'svg', 'g', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
    'svg_text', 'tspan', 'defs', 'linearGradient', 'radialGradient', 'stop',
    'clipPath', 'mask', 'symbol',
]);
const STYLES = new Set(['gap', 'padding', 'color', 'background', 'fontSize', 'fontWeight', 'borderRadius', 'outline', 'aspectRatio', 'textAlign']);
const ATTRIBUTES = new Set([
    'viewBox', 'preserveAspectRatio', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'dx', 'dy',
    'width', 'height', 'cx', 'cy', 'r', 'rx', 'ry', 'fx', 'fy', 'fr',
    'd', 'points', 'fill', 'fill-opacity', 'fill-rule', 'stroke', 'stroke-width',
    'stroke-opacity', 'stroke-linecap', 'stroke-linejoin', 'stroke-dasharray',
    'stroke-dashoffset', 'stroke-miterlimit', 'opacity', 'transform',
    'text-anchor', 'dominant-baseline', 'font-size', 'font-weight', 'font-family',
    'letter-spacing', 'offset', 'stop-color', 'stop-opacity', 'gradientUnits',
    'gradientTransform', 'spreadMethod', 'clipPathUnits', 'maskUnits', 'maskContentUnits',
]);

export function createGameRenderer({ container, toggleButton, actionUrl, canEdit,
    canContribute = () => false, refresh,
    stylesheetUrl = '/static/css/game-renderer.css?v=6', initiallyOpen = false,
    onPendingChange = () => {}, onMove = () => {} }) {
    const stylesheet = document.createElement('link');
    stylesheet.rel = 'stylesheet';
    stylesheet.href = stylesheetUrl;
    document.head.append(stylesheet);
    let game = null;
    let pending = false;
    let error = '';
    let signature = '';
    let open = initiallyOpen;
    let latestLeft = 50;
    let latestTop = 50;
    let activePointerId = null;
    let startX = 0;
    let startY = 0;
    let startLeft = 0;
    let startTop = 0;

    function canAct(action) {
        if (!action) return false;
        if (canEdit()) return true;
        return action.permission === 'contributor' && Boolean(canContribute());
    }

    toggleButton.addEventListener('click', event => {
        event.stopPropagation();
        if (!game) return;
        open = !open;
        draw();
    });
    toggleButton.addEventListener('pointerdown', event => event.stopPropagation());

    async function send(action) {
        if (pending || !canAct(action) || !game?.view) return;
        if (action.confirmation && !window.confirm(action.confirmation)) return;
        pending = true;
        onPendingChange(true);
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
            onPendingChange(false);
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
        if (props.src && node.kind === 'svg') element.dataset.gameSvg = String(props.src);
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
            const maxAttrLength = (name === 'd' || name === 'points') ? 10000 : 200;
            if (SVG.has(node.kind) && ATTRIBUTES.has(name) && String(value).length <= maxAttrLength
                && !/url\s*\(|[<>{}\\]/i.test(String(value))) element.setAttribute(name, String(value));
        }
        if (node.kind === 'grid') {
            const columns = Math.max(1, Math.min(32, Number(props.columns) || 1));
            element.style.gridTemplateColumns = `repeat(${Math.floor(columns)}, minmax(0, 1fr))`;
        }
        if (node.kind === 'button') {
            element.type = 'button';
            element.disabled = pending || !canAct(node.action) || Boolean(props.disabled) || !node.action;
            if (node.action?.permission) element.dataset.gamePermission = String(node.action.permission);
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
        const hasError = Boolean(error || game?.error);
        toggleButton.dataset.gameError = String(hasError);
        toggleButton.setAttribute('aria-label', hasError ? 'Toggle game — game error' : 'Toggle game');
        toggleButton.title = hasError ? 'Game error — click to view details' : (open ? 'Close game' : 'Open game');
        container.hidden = !game || !open;
        container.replaceChildren();
        if (!game) return;
        const panel = document.createElement('section');
        panel.className = 'game-panel';
        panel.setAttribute('aria-busy', String(pending));
        if (!panel.classList.contains('dragging')) {
            panel.style.left = `${latestLeft}%`;
            panel.style.top = `${latestTop}%`;
        }
        for (const event of ['pointerdown', 'pointermove', 'pointerup', 'click', 'dblclick', 'wheel']) {
            panel.addEventListener(event, e => e.stopPropagation());
        }

        let move = null;
        if (canEdit()) {
            panel.classList.add('game-panel-editable', 'a2ui-surface-editable');
            const controls = document.createElement('div');
            controls.className = 'game-panel-controls a2ui-surface-controls';

            move = document.createElement('button');
            move.type = 'button';
            move.className = 'game-panel-control game-move a2ui-surface-control a2ui-move';
            move.title = 'Drag to reposition';
            move.setAttribute('aria-label', 'Drag to reposition this game');
            move.textContent = '⠿';
            move.style.touchAction = 'none';

            panel.addEventListener('pointerdown', event => {
                // Only the outer padding and move control are drag handles.
                if (event.target !== panel && !move.contains(event.target)) return;
                if (event.button !== 0 || activePointerId !== null || !canEdit()) return;
                activePointerId = event.pointerId;
                startX = event.clientX;
                startY = event.clientY;
                startLeft = latestLeft;
                startTop = latestTop;
                move.classList.add('dragging');
                panel.classList.add('dragging');
                panel.setPointerCapture(event.pointerId);
                event.preventDefault();
                event.stopPropagation();
            });

            controls.append(move);
            panel.append(controls);
        }

        panel.addEventListener('pointermove', event => {
            if (event.pointerId !== activePointerId) return;
            const rect = container.getBoundingClientRect();
            if (!rect.width || !rect.height) return;
            latestLeft = Math.max(2, Math.min(98, startLeft + ((event.clientX - startX) / rect.width) * 100));
            latestTop = Math.max(2, Math.min(98, startTop + ((event.clientY - startY) / rect.height) * 100));
            panel.style.left = `${latestLeft}%`;
            panel.style.top = `${latestTop}%`;
            event.stopPropagation();
        });

        const finishDrag = async event => {
            if (event.pointerId !== activePointerId) return;
            activePointerId = null;
            if (move) move.classList.remove('dragging');
            panel.classList.remove('dragging');
            if (panel.hasPointerCapture(event.pointerId)) {
                panel.releasePointerCapture(event.pointerId);
            }
            event.stopPropagation();
            if (event.type !== 'pointerup') {
                latestLeft = startLeft;
                latestTop = startTop;
                panel.style.left = `${latestLeft}%`;
                panel.style.top = `${latestTop}%`;
                return;
            }
            if (latestLeft === startLeft && latestTop === startTop) return;
            try {
                if (typeof onMove === 'function') {
                    await onMove({ left_pct: latestLeft, top_pct: latestTop });
                }
            } catch (failure) {
                console.error('Could not persist game panel position:', failure);
            }
        };

        panel.addEventListener('pointerup', finishDrag);
        panel.addEventListener('pointercancel', finishDrag);
        panel.addEventListener('lostpointercapture', finishDrag);

        for (const node of game.view || []) panel.append(nodeView(node));
        if (pending) {
            const status = message('Resolving action…', 'game-message');
            status.setAttribute('role', 'status');
            panel.append(status);
        }
        if (!canEdit()) {
            const note = canContribute()
                ? 'Contributor mode · collaboration-enabled actions are unlocked.'
                : 'Read-only · the active orator controls the game.';
            panel.append(message(note, 'game-message'));
        }
        if (error || game.error) {
            const alert = message(error || game.error, 'game-error');
            alert.setAttribute('role', 'alert');
            panel.append(alert);
        }
        container.append(panel);
    }

    return {
        render(next) {
            const target = next === undefined ? game : next;
            if (next !== undefined && next?.definition_hash === game?.definition_hash && next?.revision < game?.revision) return;
            const nextSignature = JSON.stringify([target, canEdit(), Boolean(canContribute())]);
            if (nextSignature === signature) return;
            signature = nextSignature;
            game = target;
            if (activePointerId === null) {
                if (target?.placement?.left_pct !== undefined) latestLeft = Number(target.placement.left_pct) || 50;
                if (target?.placement?.top_pct !== undefined) latestTop = Number(target.placement.top_pct) || 50;
            }
            draw();
        },
    };
}
