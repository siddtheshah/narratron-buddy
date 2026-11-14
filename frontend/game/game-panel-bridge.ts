import type { GameRecord } from './game-runtime.js';

export interface GamePanelBridgePayload {
  readonly parameters: GameRecord;
  readonly actions: Readonly<Record<string, boolean>>;
}

type DisableablePanelElement = HTMLElement & { disabled: boolean };

/**
 * Self-contained bridge serialized into sandboxed game-panel iframes.
 * Keep zero runtime imports or outer-scope references so `toString()` stays standalone.
 */
export function runGamePanelBridge(payloadJson: string): void {
  const { parameters, actions } = JSON.parse(payloadJson) as GamePanelBridgePayload;
  document.documentElement.style.maxWidth = '100%';
  if (document.body) {
    document.body.style.width = '100%';
    document.body.style.maxWidth = '100%';
    document.body.style.height = 'auto';
    document.body.style.minHeight = '100vh';
    document.body.style.boxSizing = 'border-box';
  }
  const value = (path: string): unknown => {
    let result: unknown = parameters;
    for (const key of path.split('.')) {
      if (result === null || typeof result !== 'object' || !Object.hasOwn(result, key)) {
        return null;
      }
      result = (result as Record<string, unknown>)[key];
    }
    return result;
  };
  for (const el of document.querySelectorAll<HTMLElement>('[data-panel-text]')) {
    const path = el.dataset.panelText ?? '';
    const result = value(path);
    el.textContent =
      result === null
        ? ''
        : typeof result === 'object'
          ? JSON.stringify(result)
          : String(result);
  }
  for (const el of document.querySelectorAll<HTMLElement>('[data-panel-hidden]')) {
    const path = el.dataset.panelHidden ?? '';
    el.hidden = Boolean(value(path));
  }
  for (const el of document.querySelectorAll<DisableablePanelElement>('[data-panel-action]')) {
    const id = el.dataset.panelAction ?? '';
    el.disabled =
      !Object.hasOwn(actions, id) ||
      !actions[id] ||
      Boolean(el.dataset.panelDisabled && value(el.dataset.panelDisabled));
    el.addEventListener('click', () => {
      if (!el.disabled) {
        parent.postMessage({ type: 'game-panel-action', id }, '*');
      }
    });
  }
  const forwardKey = (event: KeyboardEvent): void => {
    if (event.isComposing) return;
    const active = document.activeElement;
    if (
      active instanceof HTMLElement &&
      (['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName) || active.isContentEditable)
    ) {
      return;
    }
    parent.postMessage(
      {
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
      },
      '*',
    );
  };
  window.addEventListener('keydown', forwardKey, true);
  window.addEventListener('keyup', forwardKey, true);
  window.addEventListener('blur', () => {
    parent.postMessage({ type: 'game-panel-blur' }, '*');
  });
}
