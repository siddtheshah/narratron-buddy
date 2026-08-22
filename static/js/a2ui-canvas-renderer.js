const SUPPORTED = new Set([
    'Card', 'Column', 'Row', 'Grid', 'Text', 'Progress', 'Button', 'Divider', 'Icon'
]);

function valueAtPath(model, path) {
    if (!path || path === '/') return model;
    return String(path).split('/').slice(1).reduce((value, part) => {
        const key = part.replace(/~1/g, '/').replace(/~0/g, '~');
        return value && typeof value === 'object' ? value[key] : undefined;
    }, model);
}

function dynamicValue(value, model) {
    if (value && typeof value === 'object' && typeof value.path === 'string') {
        return valueAtPath(model, value.path);
    }
    return value;
}

function applyDataModelUpdate(model, path, value) {
    if (!path || path === '/') return value ?? {};
    const next = model && typeof model === 'object' ? structuredClone(model) : {};
    const parts = String(path).split('/').slice(1)
        .map(part => part.replace(/~1/g, '/').replace(/~0/g, '~'));
    let cursor = next;
    for (let index = 0; index < parts.length - 1; index += 1) {
        const key = parts[index];
        if (!cursor[key] || typeof cursor[key] !== 'object') cursor[key] = {};
        cursor = cursor[key];
    }
    const leaf = parts.at(-1);
    if (value === null) delete cursor[leaf];
    else cursor[leaf] = value;
    return next;
}

function materializeSurface(surface) {
    let surfaceId = surface?.surface_id;
    let components = new Map();
    let dataModel = {};
    for (const message of surface?.messages || []) {
        if (message.deleteSurface) return null;
        if (message.createSurface) {
            surfaceId = message.createSurface.surfaceId;
            components = new Map((message.createSurface.components || []).map(item => [item.id, item]));
            dataModel = message.createSurface.dataModel || {};
        }
        if (message.updateComponents) {
            (message.updateComponents.components || []).forEach(item => components.set(item.id, item));
        }
        if (message.updateDataModel) {
            dataModel = applyDataModelUpdate(
                dataModel,
                message.updateDataModel.path || '/',
                message.updateDataModel.value,
            );
        }
    }
    return { surfaceId, components, dataModel };
}

function renderComponent(componentId, byId, model, surfaceId, onAction, stack = new Set()) {
    if (stack.has(componentId)) return document.createTextNode('');
    const component = byId.get(componentId);
    if (!component || !SUPPORTED.has(component.component)) return document.createTextNode('');
    const nextStack = new Set(stack).add(componentId);
    let element;
    switch (component.component) {
        case 'Card':
            element = document.createElement('section');
            element.className = 'a2ui-card';
            element.append(renderComponent(component.child, byId, model, surfaceId, onAction, nextStack));
            break;
        case 'Column':
        case 'Row':
            element = document.createElement('div');
            element.className = `a2ui-${component.component.toLowerCase()}`;
            (component.children || []).forEach(child =>
                element.append(renderComponent(child, byId, model, surfaceId, onAction, nextStack)));
            break;
        case 'Grid': {
            element = document.createElement('div');
            element.className = `a2ui-grid gap-${component.gap || 'medium'}`;
            const columns = Math.max(1, Math.min(6, Number(component.columns) || 1));
            element.style.setProperty('--a2ui-grid-columns', String(columns));
            (component.children || []).forEach(child =>
                element.append(renderComponent(child, byId, model, surfaceId, onAction, nextStack)));
            break;
        }
        case 'Text':
            element = document.createElement(component.variant === 'heading' ? 'h3' : 'p');
            element.className = `a2ui-text ${component.variant || ''}`;
            element.textContent = String(dynamicValue(component.text, model) ?? '');
            break;
        case 'Progress': {
            element = document.createElement('div');
            element.className = `a2ui-progress ${component.variant || 'default'}`;
            const rawValue = Number(dynamicValue(component.value, model));
            const rawMax = Number(dynamicValue(component.max ?? 100, model));
            const maximum = Number.isFinite(rawMax) && rawMax > 0 ? rawMax : 100;
            const value = Number.isFinite(rawValue) ? Math.max(0, Math.min(maximum, rawValue)) : 0;
            const labelText = dynamicValue(component.label, model);
            if (labelText !== undefined && labelText !== null && String(labelText)) {
                const label = document.createElement('div');
                label.className = 'a2ui-progress-label';
                label.textContent = String(labelText);
                element.append(label);
            }
            const track = document.createElement('div');
            track.className = 'a2ui-progress-track';
            track.setAttribute('role', 'progressbar');
            track.setAttribute('aria-valuemin', '0');
            track.setAttribute('aria-valuemax', String(maximum));
            track.setAttribute('aria-valuenow', String(value));
            if (labelText) track.setAttribute('aria-label', String(labelText));
            const fill = document.createElement('div');
            fill.className = 'a2ui-progress-fill';
            fill.style.width = `${(value / maximum) * 100}%`;
            track.append(fill);
            const reading = document.createElement('span');
            reading.className = 'a2ui-progress-reading';
            reading.textContent = `${value} / ${maximum}`;
            element.append(track, reading);
            break;
        }
        case 'Button': {
            element = document.createElement('button');
            element.type = 'button';
            element.className = `a2ui-button ${component.variant || 'primary'}`;
            element.append(renderComponent(component.child, byId, model, surfaceId, onAction, nextStack));
            const event = component.action?.event;
            if (event?.name) element.addEventListener('click', () => onAction({
                version: 'v1.0',
                action: {
                    name: event.name,
                    surfaceId,
                    sourceComponentId: component.id,
                    timestamp: new Date().toISOString(),
                    context: event.context || {},
                    wantResponse: false,
                },
            }, element));
            else element.disabled = true;
            break;
        }
        case 'Divider':
            element = document.createElement('hr');
            element.className = 'a2ui-divider';
            break;
        case 'Icon':
            element = document.createElement('span');
            element.className = 'a2ui-icon';
            element.setAttribute('aria-hidden', 'true');
            element.textContent = ({
                sword: '⚔', key: '🗝', shield: '🛡', warning: '⚠', star: '✦',
                heart: '♥', bag: '🎒', coin: '●', potion: '⚗'
            })[component.name] || '✦';
            break;
    }
    return element;
}

export function createA2UICanvasRenderer({ container, actionUrl, surfaceUrl, canEdit = () => true }) {
    let signature = '';

    async function sendAction(payload, button) {
        button.disabled = true;
        try {
            const response = await fetch(actionUrl(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            button.closest('.a2ui-surface')?.classList.add('a2ui-surface-used');
        } catch (error) {
            console.error('A2UI action failed:', error);
            button.disabled = false;
        }
    }

    async function moveSurface(surfaceId, leftPct, topPct) {
        const response = await fetch(surfaceUrl(surfaceId), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ left_pct: leftPct, top_pct: topPct }),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
    }

    function addSurfaceControls(host, surface) {
        if (!canEdit()) return;
        const controls = document.createElement('div');
        controls.className = 'a2ui-surface-controls';

        const move = document.createElement('button');
        move.type = 'button';
        move.className = 'a2ui-surface-control a2ui-move';
        move.title = 'Drag to reposition';
        move.setAttribute('aria-label', 'Drag to reposition this UI');
        move.textContent = surface.persistent ? '📌' : '⠿';
        move.style.touchAction = 'none';

        let dragging = false;
        let latestLeft = Number(surface.placement?.left_pct) || 50;
        let latestTop = Number(surface.placement?.top_pct) || 50;
        move.addEventListener('pointerdown', event => {
            if (event.button !== 0) return;
            dragging = true;
            move.classList.add('dragging');
            move.setPointerCapture(event.pointerId);
            event.preventDefault();
        });
        move.addEventListener('pointermove', event => {
            if (!dragging) return;
            const rect = container.getBoundingClientRect();
            latestLeft = Math.max(2, Math.min(98, ((event.clientX - rect.left) / rect.width) * 100));
            latestTop = Math.max(2, Math.min(98, ((event.clientY - rect.top) / rect.height) * 100));
            host.style.left = `${latestLeft}%`;
            host.style.top = `${latestTop}%`;
        });
        const finishDrag = async event => {
            if (!dragging) return;
            dragging = false;
            move.classList.remove('dragging');
            try { move.releasePointerCapture(event.pointerId); } catch (_) {}
            try {
                await moveSurface(surface.surface_id, latestLeft, latestTop);
            } catch (error) {
                console.error('Could not persist A2UI surface position:', error);
            }
        };
        move.addEventListener('pointerup', finishDrag);
        move.addEventListener('pointercancel', finishDrag);

        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'a2ui-surface-control a2ui-delete';
        remove.title = 'Remove';
        remove.setAttribute('aria-label', 'Remove this UI');
        remove.textContent = '×';
        remove.addEventListener('click', async () => {
            remove.disabled = true;
            try {
                const response = await fetch(surfaceUrl(surface.surface_id), { method: 'DELETE' });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                host.remove();
            } catch (error) {
                console.error('Could not delete A2UI surface:', error);
                remove.disabled = false;
            }
        });

        controls.append(move, remove);
        host.append(controls);
    }

    function render(surfaces) {
        const nextSignature = JSON.stringify(surfaces || []);
        if (nextSignature === signature) return;
        signature = nextSignature;
        container.replaceChildren();
        (Array.isArray(surfaces) ? surfaces : []).forEach(surface => {
            const materialized = materializeSurface(surface);
            if (!materialized?.surfaceId || !materialized.components.size) return;
            const byId = materialized.components;
            const placement = surface.placement || {};
            const host = document.createElement('div');
            host.className = 'a2ui-surface';
            host.dataset.surfaceId = materialized.surfaceId;
            host.style.left = `${Number(placement.left_pct) || 50}%`;
            host.style.top = `${Number(placement.top_pct) || 50}%`;
            host.style.width = `${Number(placement.width_pct) || 28}%`;
            host.classList.toggle('persistent', Boolean(surface.persistent));
            addSurfaceControls(host, surface);
            host.append(renderComponent(
                'root', byId, materialized.dataModel, materialized.surfaceId, sendAction
            ));
            container.append(host);
        });
    }

    return { render };
}
