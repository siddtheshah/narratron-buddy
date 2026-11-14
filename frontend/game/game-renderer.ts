// Generic declarative views with optional data-only local exploration.
import {
  createLocalGame,
  type GameClientProgram,
  type GamePermission,
  type GameRecord,
  type LocalGameRuntime,
  type LocalGameSnapshot,
  type RenderedGameAction,
  type RenderedGameNode,
} from './game-runtime.js';
import { createPanelFrame } from './game-panel.js';

export interface GamePlacement {
  readonly left_pct?: number;
  readonly top_pct?: number;
}

export interface GamePayload {
  readonly definition_hash: string;
  readonly revision: number;
  readonly state?: GameRecord;
  readonly view?: readonly RenderedGameNode[];
  readonly panel_html?: string;
  readonly client?: GameClientProgram | null;
  readonly error?: string;
  readonly placement?: GamePlacement | null;
  readonly canvas_revision?: number;
}

export interface GameRendererOptions {
  readonly container: HTMLElement;
  readonly toggleButton: HTMLElement;
  readonly actionUrl: () => string;
  readonly canEdit: () => boolean;
  readonly canContribute?: () => boolean;
  readonly refresh: () => void | Promise<void>;
  readonly canMove?: () => boolean;
  readonly stylesheetUrl?: string;
  readonly initiallyOpen?: boolean;
  readonly embedded?: boolean;
  readonly beforeOpen?: () => void | Promise<void>;
  readonly onPendingChange?: (pending: boolean) => void;
  readonly onMove?: (placement: { left_pct: number; top_pct: number }) => void | Promise<void>;
}

export interface GameRenderer {
  close(): void;
  render(next?: GamePayload | null): void;
}

interface DraftEntry {
  readonly name: string;
  readonly arguments: GameRecord;
}

const TAGS: Readonly<Record<string, string>> = {
  column: 'div',
  row: 'div',
  grid: 'div',
  tabs: 'div',
  text: 'p',
  button: 'button',
  form: 'form',
  input: 'input',
  svg: 'svg',
  g: 'g',
  path: 'path',
  rect: 'rect',
  circle: 'circle',
  ellipse: 'ellipse',
  line: 'line',
  polyline: 'polyline',
  polygon: 'polygon',
  svg_text: 'text',
  tspan: 'tspan',
  defs: 'defs',
  linearGradient: 'linearGradient',
  radialGradient: 'radialGradient',
  stop: 'stop',
  clipPath: 'clipPath',
  mask: 'mask',
  symbol: 'symbol',
};

const SVG = new Set([
  'svg',
  'g',
  'path',
  'rect',
  'circle',
  'ellipse',
  'line',
  'polyline',
  'polygon',
  'svg_text',
  'tspan',
  'defs',
  'linearGradient',
  'radialGradient',
  'stop',
  'clipPath',
  'mask',
  'symbol',
]);

type StylePropName =
  | 'gap'
  | 'padding'
  | 'color'
  | 'background'
  | 'fontSize'
  | 'fontWeight'
  | 'borderRadius'
  | 'outline'
  | 'aspectRatio'
  | 'textAlign';

const STYLE_SETTERS: Readonly<Record<StylePropName, (style: CSSStyleDeclaration, val: string) => void>> = {
  gap: (style, val) => {
    style.gap = val;
  },
  padding: (style, val) => {
    style.padding = val;
  },
  color: (style, val) => {
    style.color = val;
  },
  background: (style, val) => {
    style.background = val;
  },
  fontSize: (style, val) => {
    style.fontSize = val;
  },
  fontWeight: (style, val) => {
    style.fontWeight = val;
  },
  borderRadius: (style, val) => {
    style.borderRadius = val;
  },
  outline: (style, val) => {
    style.outline = val;
  },
  aspectRatio: (style, val) => {
    style.aspectRatio = val;
  },
  textAlign: (style, val) => {
    style.textAlign = val;
  },
};

const ATTRIBUTES = new Set([
  'viewBox',
  'preserveAspectRatio',
  'x',
  'y',
  'x1',
  'y1',
  'x2',
  'y2',
  'dx',
  'dy',
  'width',
  'height',
  'cx',
  'cy',
  'r',
  'rx',
  'ry',
  'fx',
  'fy',
  'fr',
  'd',
  'points',
  'fill',
  'fill-opacity',
  'fill-rule',
  'stroke',
  'stroke-width',
  'stroke-opacity',
  'stroke-linecap',
  'stroke-linejoin',
  'stroke-dasharray',
  'stroke-dashoffset',
  'stroke-miterlimit',
  'opacity',
  'transform',
  'text-anchor',
  'dominant-baseline',
  'font-size',
  'font-weight',
  'font-family',
  'letter-spacing',
  'offset',
  'stop-color',
  'stop-opacity',
  'gradientUnits',
  'gradientTransform',
  'spreadMethod',
  'clipPathUnits',
  'maskUnits',
  'maskContentUnits',
]);

let rendererSequence = 0;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isGamePayload(value: unknown): value is GamePayload {
  return (
    isRecord(value) &&
    typeof value.definition_hash === 'string' &&
    typeof value.revision === 'number'
  );
}

function errorMessage(failure: unknown, fallback: string): string {
  if (failure instanceof Error && failure.message) {
    return failure.message;
  }
  return fallback;
}

function applySafeStyle(style: CSSStyleDeclaration, name: string, value: unknown): void {
  if (
    !Object.hasOwn(STYLE_SETTERS, name) ||
    typeof value !== 'string' ||
    value.length > 100 ||
    /[\\;{}<>@]|url\s*\(|expression\s*\(/i.test(value)
  ) {
    return;
  }
  // Background accepts colors only: image-set() can fetch URLs even without a url() token.
  if (
    (name === 'background' || name === 'color') &&
    (!CSS.supports('color', value) || /var\s*\(/i.test(value))
  ) {
    return;
  }
  STYLE_SETTERS[name as StylePropName](style, value);
}

export function createGameRenderer({
  container,
  toggleButton,
  actionUrl,
  canEdit,
  canContribute = () => false,
  refresh,
  canMove = () => canEdit() || canContribute(),
  stylesheetUrl = '/static/css/game-renderer.css?v=12',
  initiallyOpen = false,
  embedded = false,
  beforeOpen = async () => {},
  onPendingChange = () => {},
  onMove = () => {},
}: GameRendererOptions): GameRenderer {
  const stylesheet = document.createElement('link');
  stylesheet.rel = 'stylesheet';
  stylesheet.href = stylesheetUrl;
  document.head.append(stylesheet);

  let game: GamePayload | null = null;
  let pending = false;
  let error = '';
  let signature = '';
  let open = initiallyOpen;
  let opening = false;
  let openRequest = 0;
  let unseenUpdate = false;
  let highlightUntil = 0;
  let highlightTimer: number | null = null;
  let latestLeft = 50;
  let latestTop = 50;
  let localPlacement: { left_pct: number; top_pct: number } | null = null;
  let activePointerId: number | null = null;
  let startX = 0;
  let startY = 0;
  let startLeft = 0;
  let startTop = 0;
  let local: LocalGameRuntime | null = null;
  let draft: DraftEntry[] = [];
  let draftViews: LocalGameSnapshot[] = [];
  let draftPermission = '';
  const selectedTabs = new Map<string, number>();
  const inputValues = new Map<string, string>();
  const rendererId = `game-renderer-${++rendererSequence}`;
  let panelFrame: HTMLIFrameElement | null = null;
  let panelActions = new Map<string, RenderedGameAction>();

  window.addEventListener('message', (event: MessageEvent<unknown>) => {
    if (!panelFrame || event.source !== panelFrame.contentWindow) return;
    const data = event.data;
    if (!isRecord(data)) return;
    if (data.type === 'game-panel-action' && typeof data.id === 'string') {
      const action = panelActions.get(data.id);
      if (action) void send(action);
    } else if (data.type === 'game-panel-key') {
      if (data.eventType !== 'keydown' && data.eventType !== 'keyup') return;
      panelFrame.dispatchEvent(
        new KeyboardEvent(data.eventType, {
          key: typeof data.key === 'string' ? data.key : '',
          code: typeof data.code === 'string' ? data.code : '',
          location: Number(data.location) || 0,
          altKey: Boolean(data.altKey),
          ctrlKey: Boolean(data.ctrlKey),
          shiftKey: Boolean(data.shiftKey),
          metaKey: Boolean(data.metaKey),
          repeat: Boolean(data.repeat),
          bubbles: true,
          cancelable: true,
          composed: true,
        }),
      );
    } else if (data.type === 'game-panel-blur') {
      requestAnimationFrame(() => {
        if (!document.hasFocus()) window.dispatchEvent(new Event('blur'));
      });
    }
  });

  function clearDraft(): void {
    draft = [];
    draftViews = [];
    draftPermission = '';
  }

  function currentView(): readonly RenderedGameNode[] {
    if (draftViews.length) {
      return draftViews[draftViews.length - 1].view;
    }
    return game?.view ?? [];
  }

  function explore(action: RenderedGameAction): void {
    if (!game || !local) return;
    if (draft.length >= 64) {
      throw new Error('Commit or discard these changes before continuing.');
    }
    const state = draftViews.length ? draftViews[draftViews.length - 1].state : (game.state ?? {});
    const result = local.apply(state, action);
    draft.push({ name: action.name, arguments: action.arguments });
    draftViews.push(result);
    draftPermission = JSON.stringify([canEdit(), Boolean(canContribute())]);
  }

  function canAct(action: RenderedGameAction | null | undefined): boolean {
    if (!action) return false;
    if (canEdit()) return true;
    return action.permission === 'contributor' && Boolean(canContribute());
  }

  function highlightUpdate(): void {
    highlightUntil = Date.now() + 2400;
    if (highlightTimer !== null) {
      window.clearTimeout(highlightTimer);
    }
    highlightTimer = window.setTimeout(() => {
      highlightUntil = 0;
      highlightTimer = null;
      container.querySelector('.game-panel')?.classList.remove('game-panel-updated');
    }, 2400);
  }

  function close(): void {
    openRequest++;
    opening = false;
    open = false;
    draw();
  }

  toggleButton.addEventListener('click', async (event: MouseEvent) => {
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
    } catch (failure: unknown) {
      if (request === openRequest) {
        error = errorMessage(failure, 'Could not open game.');
      }
    } finally {
      if (request === openRequest) {
        opening = false;
        draw();
      }
    }
  });

  toggleButton.addEventListener('pointerdown', (event: PointerEvent) => event.stopPropagation());

  async function send(action: RenderedGameAction, committing = false): Promise<void> {
    if (pending || !canAct(action) || !game?.view) return;
    if (action.confirmation && !window.confirm(action.confirmation)) return;
    const transition = game.client?.transitions[action.name];
    if (!committing && local && transition && (!transition.triggering || transition.preview)) {
      try {
        explore(action);
        error = '';
      } catch (failure: unknown) {
        error = errorMessage(failure, '');
      }
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
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: action.name,
          arguments: action.arguments,
          revision,
          draft: committing ? draft.slice(0, -1) : draft,
          ...(game.canvas_revision !== undefined
            ? { canvas_revision: game.canvas_revision }
            : {}),
        }),
      });
      const result: unknown = await response.json();
      if (!response.ok) {
        const detail =
          isRecord(result) && typeof result.detail === 'string' ? result.detail : 'Action rejected.';
        throw new Error(detail);
      }
      if (
        game?.definition_hash === definitionHash &&
        isGamePayload(result) &&
        result.revision >= game.revision
      ) {
        game = result;
        inputValues.clear();
        clearDraft();
      }
    } catch (failure: unknown) {
      error = errorMessage(failure, 'Could not submit action.');
      if (game?.definition_hash === definitionHash) clearDraft();
    } finally {
      pending = false;
      onPendingChange(false);
      draw();
      void refresh();
    }
  }

  function nodeView(
    node: RenderedGameNode,
    depth = 0,
    path = '0',
    formAction: RenderedGameAction | null = null,
  ): Node {
    if (!Object.hasOwn(TAGS, node.kind) || depth > 12) return document.createTextNode('');
    let buttonEl: HTMLButtonElement | null = null;
    let inputEl: HTMLInputElement | null = null;
    let formEl: HTMLFormElement | null = null;
    let element: HTMLElement | SVGElement;
    if (node.kind === 'button') {
      buttonEl = document.createElement('button');
      element = buttonEl;
    } else if (node.kind === 'input') {
      inputEl = document.createElement('input');
      element = inputEl;
    } else if (node.kind === 'form') {
      formEl = document.createElement('form');
      element = formEl;
    } else if (SVG.has(node.kind)) {
      element = document.createElementNS('http://www.w3.org/2000/svg', TAGS[node.kind]);
    } else {
      element = document.createElement(TAGS[node.kind]);
    }
    element.setAttribute('class', `game-node game-${node.kind}`);
    const props = node.props;
    if (props.id) element.dataset.gameId = String(props.id);
    if (node.kind === 'row' && props.layout === 'sidebar') element.dataset.gameLayout = 'sidebar';
    if (props.src && node.kind === 'svg') element.dataset.gameSvg = String(props.src);
    if (props.label) element.setAttribute('aria-label', String(props.label));
    if (props.text !== undefined) element.textContent = String(props.text);
    if (props.pressed !== undefined) {
      element.setAttribute('aria-pressed', String(Boolean(props.pressed)));
    }
    for (const [name, value] of Object.entries(props)) {
      applySafeStyle(element.style, name, value);
      const maxAttrLength = name === 'd' || name === 'points' ? 10000 : 200;
      if (
        SVG.has(node.kind) &&
        ATTRIBUTES.has(name) &&
        String(value).length <= maxAttrLength &&
        !/url\s*\(|[<>{}\\]/i.test(String(value))
      ) {
        element.setAttribute(name, String(value));
      }
    }
    if (node.kind === 'grid') {
      const columns = Math.max(1, Math.min(32, Number(props.columns) || 1));
      element.style.gridTemplateColumns = `repeat(${Math.floor(columns)}, minmax(0, 1fr))`;
    }
    if (buttonEl) {
      const rawClearInputs = props.clearInputs;
      const clearInputs: GameRecord | null =
        rawClearInputs !== null &&
        typeof rawClearInputs === 'object' &&
        !Array.isArray(rawClearInputs)
          ? rawClearInputs
          : null;
      buttonEl.type = formAction && !node.action && !clearInputs ? 'submit' : 'button';
      const lastDraft = draft.length ? draft[draft.length - 1] : null;
      const awaitingCommit =
        lastDraft !== null && Boolean(game?.client?.transitions[lastDraft.name]?.triggering);
      buttonEl.disabled =
        pending ||
        awaitingCommit ||
        !canAct(node.action || formAction) ||
        Boolean(props.disabled);
      if (node.action?.permission) {
        buttonEl.dataset.gamePermission = String(node.action.permission);
      }
      const nodeAction = node.action;
      if (nodeAction) {
        buttonEl.addEventListener('click', () => {
          void send(nodeAction);
        });
      } else if (clearInputs) {
        const currentButton = buttonEl;
        currentButton.addEventListener('click', () => {
          if (pending || !canAct(formAction)) return;
          const form = currentButton.closest('form');
          let first: HTMLInputElement | null = null;
          for (const input of form?.querySelectorAll('input') || []) {
            if (
              input.closest('form') !== form ||
              input.disabled ||
              !Object.hasOwn(clearInputs, input.name)
            ) {
              continue;
            }
            const value = clearInputs[input.name];
            if (typeof value !== 'string' && typeof value !== 'number') continue;
            input.value = String(value);
            input.dispatchEvent(new Event('input', { bubbles: true }));
            first ||= input;
          }
          first?.focus();
        });
      }
    }
    if (inputEl) {
      const currentInput = inputEl;
      currentInput.type = props.type === 'number' ? 'number' : 'text';
      currentInput.name = String(props.name || '');
      currentInput.value = inputValues.get(path) ?? String(props.value ?? '');
      currentInput.addEventListener('input', () => inputValues.set(path, currentInput.value));
      currentInput.placeholder = String(props.placeholder || '');
      currentInput.required = Boolean(props.required);
      if (props.min !== undefined) currentInput.min = String(props.min);
      if (props.max !== undefined) currentInput.max = String(props.max);
      if (props.step !== undefined) currentInput.step = String(props.step);
      const maxLength = Number(props.maxLength ?? 1000);
      currentInput.maxLength = Number.isFinite(maxLength)
        ? Math.max(0, Math.min(1000, Math.floor(maxLength)))
        : 1000;
      currentInput.disabled = pending || !canAct(formAction) || Boolean(props.disabled);
    }
    if (formEl) {
      const currentForm = formEl;
      const nodeAction = node.action;
      formAction = nodeAction;
      currentForm.setAttribute('aria-label', String(props.label || 'Game action'));
      currentForm.addEventListener('submit', (event: SubmitEvent) => {
        event.preventDefault();
        event.stopPropagation();
        if (!nodeAction || !canAct(nodeAction) || !currentForm.reportValidity()) return;
        const argumentsMap: GameRecord = { ...nodeAction.arguments };
        for (const input of currentForm.querySelectorAll('input')) {
          if (input.closest('form') !== currentForm || input.disabled || !input.name) continue;
          const value = input.type === 'number' ? input.valueAsNumber : input.value;
          if (typeof value === 'string' && value.length > Math.min(1000, input.maxLength)) {
            input.setCustomValidity('Text exceeds the input length limit.');
            input.reportValidity();
            input.addEventListener('input', () => input.setCustomValidity(''), { once: true });
            return;
          }
          if (input.type === 'number' && !Number.isFinite(value)) return;
          Object.defineProperty(argumentsMap, input.name, {
            value,
            enumerable: true,
            configurable: true,
          });
        }
        void send({ ...nodeAction, arguments: argumentsMap });
      });
    }
    if (node.kind === 'tabs') {
      const key = String(props.id || path);
      const children = node.children || [];
      const tablist = document.createElement('div');
      tablist.className = 'game-tab-list';
      tablist.setAttribute('role', 'tablist');
      tablist.setAttribute('aria-label', String(props.label || 'Game information'));
      const buttons: HTMLButtonElement[] = [];
      const panels: HTMLDivElement[] = [];
      const activate = (index: number): void => {
        selectedTabs.set(key, index);
        buttons.forEach((tabBtn, i) => {
          tabBtn.setAttribute('aria-selected', String(i === index));
          tabBtn.tabIndex = i === index ? 0 : -1;
          panels[i].hidden = i !== index;
        });
      };
      children.forEach((child, index) => {
        const id = `${rendererId}-${path}-${index}`;
        const tabBtn = document.createElement('button');
        tabBtn.type = 'button';
        tabBtn.className = 'game-button game-tab';
        tabBtn.id = `${id}-tab`;
        tabBtn.textContent = String(child.props?.label || `Tab ${index + 1}`);
        tabBtn.setAttribute('role', 'tab');
        tabBtn.setAttribute('aria-controls', `${id}-panel`);
        tabBtn.addEventListener('click', () => activate(index));
        tabBtn.addEventListener('keydown', (event: KeyboardEvent) => {
          let next: number;
          if (event.key === 'ArrowRight') next = (index + 1) % children.length;
          else if (event.key === 'ArrowLeft')
            next = (index + children.length - 1) % children.length;
          else if (event.key === 'Home') next = 0;
          else if (event.key === 'End') next = children.length - 1;
          else return;
          event.preventDefault();
          activate(next);
          buttons[next].focus();
        });
        const panel = document.createElement('div');
        panel.className = 'game-node game-tab-panel';
        if (typeof props.panelHeight === 'number' && Number.isFinite(props.panelHeight)) {
          panel.style.height = `min(${Math.max(160, Math.min(560, Math.floor(props.panelHeight)))}px, 55vh)`;
        }
        panel.append(nodeView(child, depth + 1, `${path}-${index}`, formAction));
        panel.id = `${id}-panel`;
        panel.setAttribute('role', 'tabpanel');
        panel.setAttribute('aria-labelledby', tabBtn.id);
        panel.tabIndex = 0;
        buttons.push(tabBtn);
        panels.push(panel);
        tablist.append(tabBtn);
      });
      element.append(tablist, ...panels);
      activate(Math.min(selectedTabs.get(key) || 0, Math.max(0, children.length - 1)));
    } else {
      (node.children || []).forEach((child, index) =>
        element.append(nodeView(child, depth + 1, `${path}-${index}`, formAction)),
      );
    }
    if (node.kind === 'input' && props.label && !props.labelHidden) {
      const label = document.createElement('label');
      label.className = 'game-input-field';
      const caption = document.createElement('span');
      caption.textContent = String(props.label);
      label.append(caption, element);
      return label;
    }
    return element;
  }

  function message(text: string, className: string): HTMLParagraphElement {
    const element = document.createElement('p');
    element.className = className;
    element.textContent = text;
    return element;
  }

  function draw(): void {
    panelFrame = null;
    panelActions = new Map<string, RenderedGameAction>();
    if (embedded) open = Boolean(game);
    if (!game) open = false;
    toggleButton.hidden = !game;
    toggleButton.setAttribute('aria-expanded', String(open));
    const hasError = Boolean(error || game?.error);
    toggleButton.dataset.gameError = String(hasError);
    toggleButton.dataset.gameUpdated = String(unseenUpdate && !open);
    const label = hasError ? 'Toggle game — game error' : 'Toggle game';
    toggleButton.setAttribute('aria-label', unseenUpdate ? `${label} — new update` : label);
    toggleButton.title = hasError
      ? 'Game error — click to view details'
      : unseenUpdate
        ? 'Game updated — click to view'
        : open
          ? 'Close game'
          : 'Open game';
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
    for (const eventName of ['pointerdown', 'pointermove', 'pointerup', 'click', 'dblclick', 'wheel']) {
      panel.addEventListener(eventName, (e: Event) => e.stopPropagation());
    }

    let move: HTMLButtonElement | null = null;
    if (canMove()) {
      panel.classList.add('game-panel-editable', 'a2ui-surface-editable');
      const controls = document.createElement('div');
      controls.className = 'game-panel-controls a2ui-surface-controls';

      const moveBtn = document.createElement('button');
      move = moveBtn;
      moveBtn.type = 'button';
      moveBtn.className = 'game-panel-control game-move a2ui-surface-control a2ui-move';
      moveBtn.title = 'Drag to reposition';
      moveBtn.setAttribute('aria-label', 'Drag to reposition this game');
      moveBtn.textContent = '⠿';
      moveBtn.style.touchAction = 'none';

      panel.addEventListener('pointerdown', (event: PointerEvent) => {
        // Only the outer padding and move control are drag handles.
        if (
          event.target !== panel &&
          (!(event.target instanceof Node) || !moveBtn.contains(event.target))
        ) {
          return;
        }
        if (event.button !== 0 || activePointerId !== null || !canMove()) return;
        activePointerId = event.pointerId;
        startX = event.clientX;
        startY = event.clientY;
        startLeft = latestLeft;
        startTop = latestTop;
        moveBtn.classList.add('dragging');
        panel.classList.add('dragging');
        panel.setPointerCapture(event.pointerId);
        event.preventDefault();
        event.stopPropagation();
      });

      controls.append(moveBtn);
      panel.append(controls);
    }

    panel.addEventListener('pointermove', (event: PointerEvent) => {
      if (event.pointerId !== activePointerId) return;
      const rect = container.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      latestLeft = Math.max(2, Math.min(98, startLeft + ((event.clientX - startX) / rect.width) * 100));
      latestTop = Math.max(2, Math.min(98, startTop + ((event.clientY - startY) / rect.height) * 100));
      panel.style.left = `${latestLeft}%`;
      panel.style.top = `${latestTop}%`;
      event.stopPropagation();
    });

    const finishDrag = async (event: PointerEvent): Promise<void> => {
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
        await onMove({ left_pct: latestLeft, top_pct: latestTop });
      } catch (failure: unknown) {
        console.error('Could not persist game panel position:', failure);
      }
    };

    panel.addEventListener('pointerup', finishDrag);
    panel.addEventListener('pointercancel', finishDrag);
    panel.addEventListener('lostpointercapture', finishDrag);

    if (typeof game.panel_html === 'string') {
      const root = currentView()[0];
      for (const node of root?.children || []) {
        if (node.action && typeof node.props?.id === 'string') {
          panelActions.set(node.props.id, node.action);
        }
      }
      panelFrame = createPanelFrame(
        game.panel_html,
        root?.props || {},
        Object.fromEntries([...panelActions].map(([id, action]) => [id, !pending && canAct(action)])),
      );
      if (panelFrame.dataset.panelWidth) {
        panel.style.setProperty('--game-panel-width', panelFrame.dataset.panelWidth);
      }
      if (panelFrame.dataset.panelHeight) {
        panel.style.setProperty('--game-panel-height', panelFrame.dataset.panelHeight);
      }
      panel.append(panelFrame);
    } else {
      currentView().forEach((node, index) => panel.append(nodeView(node, 0, String(index))));
    }

    const feedback = panel.querySelector('[data-game-layout="sidebar"] > .game-column') || panel;
    if (draft.length) {
      const controls = document.createElement('div');
      controls.className = 'game-node game-column game-draft-controls';
      const last = draft[draft.length - 1];
      const ready = Boolean(game.client?.transitions[last.name]?.triggering);
      const status = message(
        ready
          ? 'Move previewed · submit to confirm.'
          : 'Exploring · changes are private until submitted.',
        'game-message',
      );
      status.setAttribute('role', 'status');
      controls.append(status);
      const actions = document.createElement('div');
      actions.className = 'game-node game-row game-draft-actions';
      const createDraftButton = (
        btnLabel: string,
        callback: () => void,
      ): HTMLButtonElement => {
        const element = document.createElement('button');
        element.className = 'game-node game-button';
        element.textContent = btnLabel;
        element.disabled = pending;
        element.addEventListener('click', callback);
        actions.append(element);
        return element;
      };
      const permission: GamePermission =
        draftPermission === '[false,true]' ? 'contributor' : 'orator';
      const submit = createDraftButton('Submit', () => {
        void send({ ...last, permission }, true);
      });
      submit.disabled = pending || !ready;
      submit.classList.toggle('game-submit-ready', ready);
      createDraftButton('Undo', () => {
        draft.pop();
        draftViews.pop();
        error = '';
        draw();
      });
      createDraftButton('Discard changes', () => {
        clearDraft();
        error = '';
        draw();
      });
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
      const alert = message(error || game.error || '', 'game-error');
      alert.setAttribute('role', 'alert');
      feedback.append(alert);
    }
    container.append(panel);
  }

  return {
    close,
    render(next?: GamePayload | null): void {
      const target = next === undefined ? game : next;
      if (
        next !== undefined &&
        next !== null &&
        game !== null &&
        next.definition_hash === game.definition_hash &&
        next.revision < game.revision
      ) {
        return;
      }
      const nextSignature = JSON.stringify([
        target,
        canEdit(),
        Boolean(canContribute()),
        Boolean(canMove()),
      ]);
      if (nextSignature === signature) return;
      signature = nextSignature;
      const changed =
        target?.definition_hash !== game?.definition_hash || target?.revision !== game?.revision;
      if (changed) inputValues.clear();
      // Polling, permission changes and placement updates are not new game actions.
      // The first payload establishes the baseline; subsequent revisions signal updates.
      if (!target) {
        unseenUpdate = false;
        highlightUntil = 0;
        if (highlightTimer !== null) {
          window.clearTimeout(highlightTimer);
          highlightTimer = null;
        }
      } else if (game && changed) {
        if (open) highlightUpdate();
        else unseenUpdate = true;
      }
      const permissionChanged =
        draft.length > 0 &&
        draftPermission !== JSON.stringify([canEdit(), Boolean(canContribute())]);
      if (changed || permissionChanged) {
        if (draft.length && !pending) {
          error = 'The game or your permissions changed. Local exploration was discarded.';
        }
        clearDraft();
      }
      if (target?.client !== game?.client) {
        local = target?.client ? createLocalGame(target.client) : null;
      }
      if (target?.definition_hash !== game?.definition_hash) {
        localPlacement = null;
        selectedTabs.clear();
      }
      game = target ?? null;
      if (activePointerId === null) {
        const placement = localPlacement || target?.placement;
        if (placement?.left_pct !== undefined) latestLeft = Number(placement.left_pct) || 50;
        if (placement?.top_pct !== undefined) latestTop = Number(placement.top_pct) || 50;
      }
      draw();
    },
  };
}
