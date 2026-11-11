// Passive authored HTML runs in an opaque-origin frame. Only this bridge runs JS.
export const DEFAULT_PANEL_WIDTH = 510;
export const DEFAULT_PANEL_HEIGHT = 430;
export const MIN_PANEL_WIDTH = 280;
export const MAX_PANEL_WIDTH = 960;
export const MIN_PANEL_HEIGHT = 220;
export const MAX_PANEL_HEIGHT = 780;

function parsePanelLength(value, min, max) {
    if (typeof value !== 'string') return null;
    const match = value.trim().match(/^(\d+(?:\.\d+)?)(px|rem|em|vh|vw|vmin|vmax)$/i);
    if (!match) return null;
    const amount = Number(match[1]);
    const unit = match[2].toLowerCase();
    const vw = window.innerWidth || 1024;
    const vh = window.innerHeight || 768;
    const pixels = unit === 'px' ? amount
        : (unit === 'rem' || unit === 'em') ? amount * 16
        : unit === 'vh' ? (amount * vh) / 100
        : unit === 'vw' ? (amount * vw) / 100
        : unit === 'vmin' ? (amount * Math.min(vw, vh)) / 100
        : (amount * Math.max(vw, vh)) / 100;
    return Math.round(Math.max(min, Math.min(max, pixels)));
}

export function extractPanelSize(html) {
    const declared = {};
    const props = ['width', 'min-width', 'max-width', 'height', 'min-height', 'max-height', '--panel-width', '--panel-height'];
    const doc = new DOMParser().parseFromString(String(html || ''), 'text/html');
    for (const styleEl of doc.querySelectorAll('style')) {
        const sheet = new CSSStyleSheet();
        try { sheet.replaceSync(styleEl.textContent || ''); } catch { continue; }
        for (const rule of sheet.cssRules || []) {
            if (rule.selectorText && /(^|,)\s*(:root|html|body)\s*(,|$)/i.test(rule.selectorText)) {
                for (const prop of props) {
                    const val = rule.style.getPropertyValue(prop).trim();
                    if (val) declared[prop] = val;
                }
            }
        }
    }
    for (const el of [doc.documentElement, doc.body]) {
        if (!el) continue;
        for (const prop of props) {
            const val = el.style.getPropertyValue(prop).trim();
            if (val) declared[prop] = val;
        }
    }
    const width = parsePanelLength(
        declared['--panel-width'] || declared['width'] || declared['max-width'] || declared['min-width'],
        MIN_PANEL_WIDTH, MAX_PANEL_WIDTH,
    );
    const height = parsePanelLength(
        declared['--panel-height'] || declared['height'] || declared['min-height'] || declared['max-height'],
        MIN_PANEL_HEIGHT, MAX_PANEL_HEIGHT,
    );
    return { width, height };
}

export function createPanelFrame(html, parameters, actions) {
    const { width, height } = extractPanelSize(html);
    const resolvedHeight = height ?? DEFAULT_PANEL_HEIGHT;
    const frame = document.createElement('iframe');
    frame.className = 'game-html-panel';
    frame.title = 'Game panel';
    frame.setAttribute('sandbox', 'allow-scripts');
    frame.setAttribute('referrerpolicy', 'no-referrer');
    if (width !== null) frame.dataset.panelWidth = `${width}px`;
    frame.dataset.panelHeight = `${resolvedHeight}px`;
    frame.style.cssText = `width:100%;height:clamp(${MIN_PANEL_HEIGHT}px,${resolvedHeight}px,calc(88vh - 40px));min-height:${MIN_PANEL_HEIGHT}px;max-height:calc(88vh - 40px);border:0;display:block`;
    const nonce = Array.from(crypto.getRandomValues(new Uint8Array(24)), n => n.toString(16).padStart(2, '0')).join('');
    const data = JSON.stringify(JSON.stringify({parameters, actions})).replaceAll('<', '\\u003c');
    // Never interpolate CEL values into HTML or evaluate authored JavaScript.
    const bridge = `
        const {parameters, actions} = JSON.parse(${data});
        document.documentElement.style.maxWidth = '100%';
        if (document.body) {
            document.body.style.width = '100%';
            document.body.style.maxWidth = '100%';
            document.body.style.height = 'auto';
            document.body.style.minHeight = '100vh';
            document.body.style.boxSizing = 'border-box';
        }
        const value = path => {
            let result = parameters;
            for (const key of path.split('.')) {
                if (result === null || typeof result !== 'object' || !Object.hasOwn(result, key)) return null;
                result = result[key];
            }
            return result;
        };
        for (const el of document.querySelectorAll('[data-panel-text]')) {
            const result = value(el.dataset.panelText);
            el.textContent = result === null ? '' : typeof result === 'object' ? JSON.stringify(result) : String(result);
        }
        for (const el of document.querySelectorAll('[data-panel-hidden]')) el.hidden = Boolean(value(el.dataset.panelHidden));
        for (const el of document.querySelectorAll('[data-panel-action]')) {
            const id = el.dataset.panelAction;
            el.disabled = !Object.hasOwn(actions, id) || !actions[id] || Boolean(el.dataset.panelDisabled && value(el.dataset.panelDisabled));
            el.addEventListener('click', () => {
                if (!el.disabled) parent.postMessage({type: 'game-panel-action', id}, '*');
            });
        }
        const forwardKey = event => {
            if (event.isComposing) return;
            const active = document.activeElement;
            if (active && (['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName) || active.isContentEditable)) return;
            parent.postMessage({
                type: 'game-panel-key',
                eventType: event.type,
                key: String(event.key || ''),
                code: String(event.code || ''),
                location: Number(event.location) || 0,
                altKey: Boolean(event.altKey),
                ctrlKey: Boolean(event.ctrlKey),
                shiftKey: Boolean(event.shiftKey),
                metaKey: Boolean(event.metaKey),
                repeat: Boolean(event.repeat),
            }, '*');
        };
        window.addEventListener('keydown', forwardKey, true);
        window.addEventListener('keyup', forwardKey, true);
        window.addEventListener('blur', () => {
            parent.postMessage({type: 'game-panel-blur'}, '*');
        });
    `;
    const policy = `default-src 'none'; img-src data:; script-src 'nonce-${nonce}'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'`;
    frame.srcdoc = `<!doctype html><meta http-equiv="Content-Security-Policy" content="${policy}"><meta charset="utf-8">${html}<script nonce="${nonce}">${bridge}</script>`;
    return frame;
}
