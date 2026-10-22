// Generic declarative views with optional data-only local exploration.
import { createLocalGame } from './game-runtime.js?v=1';
const TAGS = {
    column: 'div', row: 'div', grid: 'div', tabs: 'div', text: 'p', button: 'button',
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
let rendererSequence = 0;

export function createGameRenderer({ container, toggleButton, actionUrl, canEdit,
    canContribute = () => false, refresh,
    canMove = () => canEdit() || canContribute(),
    stylesheetUrl = '/static/css/game-renderer.css?v=10', initiallyOpen = false,
    beforeOpen = async () => {}, onPendingChange = () => {}, onMove = () => {} }) {
    const stylesheet = document.createElement('link');
    stylesheet.rel = 'stylesheet';
    stylesheet.href = stylesheetUrl;
    document.head.append(stylesheet);
    let game = null;
    let pending = false;
    let error = '';
    let signature = '';
    let open = initiallyOpen;
    let opening = false;
    let openRequest = 0;
    let unseenUpdate = false;
    let highlightUntil = 0;
    let highlightTimer = null;
    let latestLeft = 50;
    let latestTop = 50;
    let localPlacement = null;
    let activePointerId = null;
    let startX = 0;
    let startY = 0;
    let startLeft = 0;
    let startTop = 0;
    let local = null;
    let draft = [];
    let draftViews = [];
    let draftPermission = '';
    const selectedTabs = new Map();
    const rendererId = `game-renderer-${++rendererSequence}`;

    function clearDraft() {
        draft = [];
        draftViews = [];
        draftPermission = '';
    }

    function currentView() { return draftViews.length ? draftViews[draftViews.length - 1].view : game.view; }

    function explore(action) {
        if (draft.length >= 64) throw new Error('Commit or discard these changes before continuing.');
        const state = draftViews.length ? draftViews[draftViews.length - 1].state : game.state;
        const result = local.apply(state, action);
        draft.push({ name: action.name, arguments: action.arguments });
        draftViews.push(result);
        draftPermission = JSON.stringify([canEdit(), Boolean(canContribute())]);
    }

    function canAct(action) {
        if (!action) return false;
        if (canEdit()) return true;
        return action.permission === 'contributor' && Boolean(canContribute());
    }

    function highlightUpdate() {
        highlightUntil = Date.now() + 2400;
        clearTimeout(highlightTimer);
        highlightTimer = setTimeout(() => {
            highlightUntil = 0;
            highlightTimer = null;
            container.querySelector('.game-panel')?.classList.remove('game-panel-updated');
        }, 2400);
    }

    function close() {
        openRequest++;
        opening = false;
        open = false;
        draw();
    }

    toggleButton.addEventListener('click', async event => {
        event.stopPropagation();
        if (!game) return;
        if (open || opening) {
            close();
            return;
        }
        const request = ++openRequest;
        opening = true;
        try {
            await beforeOpen();
            if (request !== openRequest || !game) return;
            open = true;
            if (unseenUpdate) {
                unseenUpdate = false;
                highlightUpdate();
            }
        } catch (failure) {
            if (request === openRequest) error = failure.message || 'Could not open game.';
        } finally {
            if (request === openRequest) {
                opening = false;
                draw();
            }
        }
    });
    toggleButton.addEventListener('pointerdown', event => event.stopPropagation());

    async function send(action, committing = false) {
        if (pending || !canAct(action) || !game?.view) return;
        if (action.confirmation && !window.confirm(action.confirmation)) return;
        const transition = game.client?.transitions[action.name];
        if (!committing && local && transition && (!transition.triggering || transition.preview)) {
            try { explore(action); error = ''; } catch (failure) { error = failure.message; }
            draw();
            return;
        }
        pending = true;
        onPendingChange(true);
        error = '';
        const revision = game.revision;
        const definitionHash = game.definition_hash;
        draw();
        try {
            const response = await fetch(actionUrl(), {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: action.name, arguments: action.arguments, revision,
                    draft: committing ? draft.slice(0, -1) : draft }),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Action rejected.');
            if (game?.definition_hash === definitionHash && result.revision >= game.revision) {
                game = result;
                clearDraft();
            }
        } catch (failure) {
            error = failure.message || 'Could not submit action.';
            if (game?.definition_hash === definitionHash) clearDraft();
        } finally {
            pending = false;
            onPendingChange(false);
            draw();
            refresh();
        }
    }

    function nodeView(node, depth = 0, path = '0') {
        if (!Object.hasOwn(TAGS, node.kind) || depth > 12) return document.createTextNode('');
        const element = SVG.has(node.kind)
            ? document.createElementNS('http://www.w3.org/2000/svg', TAGS[node.kind])
            : document.createElement(TAGS[node.kind]);
        element.setAttribute('class', `game-node game-${node.kind}`);
        const props = node.props || {};
        if (props.id) element.dataset.gameId = String(props.id);
        if (node.kind === 'row' && props.layout === 'sidebar') element.dataset.gameLayout = 'sidebar';
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
            const awaitingCommit = draft.length && game.client.transitions[draft[draft.length - 1].name].triggering;
            element.disabled = pending || Boolean(awaitingCommit) || !canAct(node.action) || Boolean(props.disabled) || !node.action;
            if (node.action?.permission) element.dataset.gamePermission = String(node.action.permission);
            if (node.action) element.addEventListener('click', () => send(node.action));
        }
        if (node.kind === 'tabs') {
            const key = String(props.id || path);
            const children = node.children || [];
            const tablist = document.createElement('div');
            tablist.className = 'game-tab-list';
            tablist.setAttribute('role', 'tablist');
            tablist.setAttribute('aria-label', String(props.label || 'Game information'));
            const buttons = [];
            const panels = [];
            const activate = index => {
                selectedTabs.set(key, index);
                buttons.forEach((button, i) => {
                    button.setAttribute('aria-selected', String(i === index));
                    button.tabIndex = i === index ? 0 : -1;
                    panels[i].hidden = i !== index;
                });
            };
            children.forEach((child, index) => {
                const id = `${rendererId}-${path}-${index}`;
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'game-button game-tab';
                button.id = `${id}-tab`;
                button.textContent = String(child.props?.label || `Tab ${index + 1}`);
                button.setAttribute('role', 'tab');
                button.setAttribute('aria-controls', `${id}-panel`);
                button.addEventListener('click', () => activate(index));
                button.addEventListener('keydown', event => {
                    let next;
                    if (event.key === 'ArrowRight') next = (index + 1) % children.length;
                    else if (event.key === 'ArrowLeft') next = (index + children.length - 1) % children.length;
                    else if (event.key === 'Home') next = 0;
                    else if (event.key === 'End') next = children.length - 1;
                    else return;
                    event.preventDefault();
                    activate(next);
                    buttons[next].focus();
                });
                const panel = document.createElement('div');
                panel.className = 'game-node game-tab-panel';
                panel.append(nodeView(child, depth + 1, `${path}-${index}`));
                panel.id = `${id}-panel`;
                panel.setAttribute('role', 'tabpanel');
                panel.setAttribute('aria-labelledby', button.id);
                panel.tabIndex = 0;
                buttons.push(button);
                panels.push(panel);
                tablist.append(button);
            });
            element.append(tablist, ...panels);
            activate(Math.min(selectedTabs.get(key) || 0, Math.max(0, children.length - 1)));
        } else {
            (node.children || []).forEach((child, index) => element.append(nodeView(child, depth + 1, `${path}-${index}`)));
        }
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
        toggleButton.dataset.gameUpdated = String(unseenUpdate && !open);
        const label = hasError ? 'Toggle game — game error' : 'Toggle game';
        toggleButton.setAttribute('aria-label', unseenUpdate ? `${label} — new update` : label);
        toggleButton.title = hasError ? 'Game error — click to view details'
            : unseenUpdate ? 'Game updated — click to view' : (open ? 'Close game' : 'Open game');
        container.hidden = !game || !open;
        container.replaceChildren();
        if (!game) return;
        const panel = document.createElement('section');
        panel.className = 'game-panel';
        panel.classList.toggle('game-panel-updated', open && Date.now() < highlightUntil);
        panel.setAttribute('aria-busy', String(pending));
        if (!panel.classList.contains('dragging')) {
            panel.style.left = `${latestLeft}%`;
            panel.style.top = `${latestTop}%`;
        }
        for (const event of ['pointerdown', 'pointermove', 'pointerup', 'click', 'dblclick', 'wheel']) {
            panel.addEventListener(event, e => e.stopPropagation());
        }

        let move = null;
        if (canMove()) {
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
                if (event.button !== 0 || activePointerId !== null || !canMove()) return;
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
            if (!canEdit()) {
                localPlacement = { left_pct: latestLeft, top_pct: latestTop };
                return;
            }
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

        (currentView() || []).forEach((node, index) => panel.append(nodeView(node, 0, String(index))));
        const feedback = panel.querySelector('[data-game-layout="sidebar"] > .game-column') || panel;
        if (draft.length) {
            const controls = document.createElement('div');
            controls.className = 'game-node game-column game-draft-controls';
            const last = draft[draft.length - 1];
            const ready = game.client.transitions[last.name].triggering;
            const status = message(ready ? 'Move previewed · submit to confirm.' : 'Exploring · changes are private until submitted.', 'game-message');
            status.setAttribute('role', 'status');
            controls.append(status);
            const actions = document.createElement('div');
            actions.className = 'game-node game-row game-draft-actions';
            const button = (label, callback) => {
                const element = document.createElement('button');
                element.className = 'game-node game-button';
                element.textContent = label;
                element.disabled = pending;
                element.addEventListener('click', callback);
                actions.append(element);
                return element;
            };
            const submit = button('Submit', () => send({ ...last, permission: draftPermission === '[false,true]' ? 'contributor' : 'orator' }, true));
            submit.disabled = pending || !ready;
            submit.classList.toggle('game-submit-ready', Boolean(ready));
            button('Undo', () => { draft.pop(); draftViews.pop(); error = ''; draw(); });
            button('Discard changes', () => { clearDraft(); error = ''; draw(); });
            controls.append(actions);
            feedback.append(controls);
        }
        if (!canEdit()) {
            const note = canContribute()
                ? 'Contributor mode · collaboration-enabled actions are unlocked.'
                : 'Read-only · the active orator controls the game.';
            feedback.append(message(note, 'game-message'));
        }
        if (error || game.error) {
            const alert = message(error || game.error, 'game-error');
            alert.setAttribute('role', 'alert');
            feedback.append(alert);
        }
        container.append(panel);
    }

    return {
        close,
        render(next) {
            const target = next === undefined ? game : next;
            if (next !== undefined && next?.definition_hash === game?.definition_hash && next?.revision < game?.revision) return;
            const nextSignature = JSON.stringify([target, canEdit(), Boolean(canContribute()), Boolean(canMove())]);
            if (nextSignature === signature) return;
            signature = nextSignature;
            const changed = target?.definition_hash !== game?.definition_hash || target?.revision !== game?.revision;
            // Polling, permission changes and placement updates are not new game actions.
            // The first payload establishes the baseline; subsequent revisions signal updates.
            if (!target) {
                unseenUpdate = false;
                highlightUntil = 0;
                clearTimeout(highlightTimer);
                highlightTimer = null;
            } else if (game && changed) {
                if (open) highlightUpdate();
                else unseenUpdate = true;
            }
            const permissionChanged = draft.length && draftPermission !== JSON.stringify([canEdit(), Boolean(canContribute())]);
            if (changed || permissionChanged) {
                if (draft.length && !pending) error = 'The game or your permissions changed. Local exploration was discarded.';
                clearDraft();
            }
            if (target?.client !== game?.client) local = target?.client ? createLocalGame(target.client) : null;
            if (target?.definition_hash !== game?.definition_hash) {
                localPlacement = null;
                selectedTabs.clear();
            }
            game = target;
            if (activePointerId === null) {
                const placement = localPlacement || target?.placement;
                if (placement?.left_pct !== undefined) latestLeft = Number(placement.left_pct) || 50;
                if (placement?.top_pct !== undefined) latestTop = Number(placement.top_pct) || 50;
            }
            draw();
        },
    };
}
