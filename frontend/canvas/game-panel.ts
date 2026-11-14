// Passive authored HTML runs in an opaque-origin frame. Only this bridge runs JS.
import { runGamePanelBridge } from './game-panel-bridge.js';
import type { GameRecord } from './game-runtime.js';

export const DEFAULT_PANEL_WIDTH = 510;
export const DEFAULT_PANEL_HEIGHT = 430;
export const MIN_PANEL_WIDTH = 280;
export const MAX_PANEL_WIDTH = 960;
export const MIN_PANEL_HEIGHT = 220;
export const MAX_PANEL_HEIGHT = 780;

export interface PanelSize {
  readonly width: number | null;
  readonly height: number | null;
}

const PANEL_SIZE_PROPS = [
  'width',
  'min-width',
  'max-width',
  'height',
  'min-height',
  'max-height',
  '--panel-width',
  '--panel-height',
] as const;

function parsePanelLength(value: string | undefined, min: number, max: number): number | null {
  if (typeof value !== 'string') return null;
  const match = value.trim().match(/^(\d+(?:\.\d+)?)(px|rem|em|vh|vw|vmin|vmax)$/i);
  if (!match) return null;
  const amount = Number(match[1]);
  const unit = match[2].toLowerCase();
  const vw = window.innerWidth || 1024;
  const vh = window.innerHeight || 768;
  const pixels =
    unit === 'px'
      ? amount
      : unit === 'rem' || unit === 'em'
        ? amount * 16
        : unit === 'vh'
          ? (amount * vh) / 100
          : unit === 'vw'
            ? (amount * vw) / 100
            : unit === 'vmin'
              ? (amount * Math.min(vw, vh)) / 100
              : (amount * Math.max(vw, vh)) / 100;
  return Math.round(Math.max(min, Math.min(max, pixels)));
}

export function extractPanelSize(html: string): PanelSize {
  const declared: Record<string, string> = {};
  const doc = new DOMParser().parseFromString(String(html || ''), 'text/html');
  for (const styleEl of doc.querySelectorAll('style')) {
    const sheet = new CSSStyleSheet();
    try {
      sheet.replaceSync(styleEl.textContent || '');
    } catch {
      continue;
    }
    for (const rule of sheet.cssRules) {
      if (
        rule instanceof CSSStyleRule &&
        rule.selectorText &&
        /(^|,)\s*(:root|html|body)\s*(,|$)/i.test(rule.selectorText)
      ) {
        for (const prop of PANEL_SIZE_PROPS) {
          const val = rule.style.getPropertyValue(prop).trim();
          if (val) declared[prop] = val;
        }
      }
    }
  }
  for (const el of [doc.documentElement, doc.body]) {
    if (!el) continue;
    for (const prop of PANEL_SIZE_PROPS) {
      const val = el.style.getPropertyValue(prop).trim();
      if (val) declared[prop] = val;
    }
  }
  const width = parsePanelLength(
    declared['--panel-width'] || declared.width || declared['max-width'] || declared['min-width'],
    MIN_PANEL_WIDTH,
    MAX_PANEL_WIDTH,
  );
  const height = parsePanelLength(
    declared['--panel-height'] || declared.height || declared['min-height'] || declared['max-height'],
    MIN_PANEL_HEIGHT,
    MAX_PANEL_HEIGHT,
  );
  return { width, height };
}

export function createPanelFrame(
  html: string,
  parameters: GameRecord,
  actions: Readonly<Record<string, boolean>>,
): HTMLIFrameElement {
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
  const nonce = Array.from(crypto.getRandomValues(new Uint8Array(24)), (n) =>
    n.toString(16).padStart(2, '0'),
  ).join('');
  const data = JSON.stringify(JSON.stringify({ parameters, actions })).replaceAll('<', '\\u003c');
  // Never interpolate CEL values into HTML or evaluate authored JavaScript.
  const bridge = `(${runGamePanelBridge.toString()})(${data});`;
  const policy = `default-src 'none'; img-src data:; script-src 'nonce-${nonce}'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'`;
  frame.srcdoc = `<!doctype html><meta http-equiv="Content-Security-Policy" content="${policy}"><meta charset="utf-8">${html}<script nonce="${nonce}">${bridge}</script>`;
  return frame;
}
