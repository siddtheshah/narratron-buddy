// Passive authored HTML runs in an opaque-origin frame. Only this bridge runs JS.
export function createPanelFrame(html, parameters, actions) {
    const frame = document.createElement('iframe');
    frame.className = 'game-html-panel';
    frame.title = 'Game panel';
    frame.setAttribute('sandbox', 'allow-scripts');
    frame.setAttribute('referrerpolicy', 'no-referrer');
    frame.style.cssText = 'width:100%;height:360px;border:0;display:block';
    const nonce = Array.from(crypto.getRandomValues(new Uint8Array(24)), n => n.toString(16).padStart(2, '0')).join('');
    const data = JSON.stringify(JSON.stringify({parameters, actions})).replaceAll('<', '\\u003c');
    // Never interpolate CEL values into HTML or evaluate authored JavaScript.
    const bridge = `
        const {parameters, actions} = JSON.parse(${data});
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
    `;
    const policy = `default-src 'none'; script-src 'nonce-${nonce}'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'`;
    frame.srcdoc = `<!doctype html><meta http-equiv="Content-Security-Policy" content="${policy}"><meta charset="utf-8">${html}<script nonce="${nonce}">${bridge}</script>`;
    return frame;
}
