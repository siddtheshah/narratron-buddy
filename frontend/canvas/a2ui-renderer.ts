export interface A2UIDynamicPath {
  readonly path: string;
}

export type A2UIDynamicString = string | A2UIDynamicPath;
export type A2UIDynamicNumber = number | A2UIDynamicPath;
export type A2UIDataModel = Record<string, unknown>;

export interface A2UIActionEvent {
  readonly name: string;
  readonly context?: Readonly<Record<string, unknown>>;
}

export interface A2UIActionDefinition {
  readonly event?: A2UIActionEvent;
}

export interface A2UICardComponent {
  readonly id: string;
  readonly component: 'Card';
  readonly child: string;
  readonly weight?: number;
}

export interface A2UIColumnComponent {
  readonly id: string;
  readonly component: 'Column';
  readonly children?: readonly string[];
  readonly justify?: string;
  readonly align?: string;
  readonly weight?: number;
}

export interface A2UIRowComponent {
  readonly id: string;
  readonly component: 'Row';
  readonly children?: readonly string[];
  readonly justify?: string;
  readonly align?: string;
  readonly weight?: number;
}

export interface A2UIGridComponent {
  readonly id: string;
  readonly component: 'Grid';
  readonly children?: readonly string[];
  readonly columns?: number;
  readonly gap?: string;
  readonly weight?: number;
}

export interface A2UITextComponent {
  readonly id: string;
  readonly component: 'Text';
  readonly text?: A2UIDynamicString;
  readonly variant?: string;
  readonly weight?: number;
}

export interface A2UIProgressComponent {
  readonly id: string;
  readonly component: 'Progress';
  readonly value: A2UIDynamicNumber;
  readonly max?: A2UIDynamicNumber;
  readonly label?: A2UIDynamicString;
  readonly variant?: string;
  readonly weight?: number;
}

export interface A2UIButtonComponent {
  readonly id: string;
  readonly component: 'Button';
  readonly child: string;
  readonly variant?: string;
  readonly action?: A2UIActionDefinition;
  readonly weight?: number;
}

export interface A2UIDividerComponent {
  readonly id: string;
  readonly component: 'Divider';
  readonly axis?: string;
  readonly weight?: number;
}

export type A2UIIconName =
  | 'sword'
  | 'key'
  | 'shield'
  | 'warning'
  | 'star'
  | 'heart'
  | 'bag'
  | 'coin'
  | 'potion'
  | (string & {});

export interface A2UIIconComponent {
  readonly id: string;
  readonly component: 'Icon';
  readonly name: A2UIIconName;
  readonly weight?: number;
}

export type A2UIComponent =
  | A2UICardComponent
  | A2UIColumnComponent
  | A2UIRowComponent
  | A2UIGridComponent
  | A2UITextComponent
  | A2UIProgressComponent
  | A2UIButtonComponent
  | A2UIDividerComponent
  | A2UIIconComponent;

export interface A2UIPlacement {
  left_pct?: number;
  top_pct?: number;
  width_pct?: number;
}

export interface A2UIMessage {
  readonly deleteSurface?: unknown;
  readonly createSurface?: {
    readonly surfaceId: string;
    readonly components?: readonly A2UIComponent[];
    readonly dataModel?: unknown;
  };
  readonly updateComponents?: {
    readonly components?: readonly A2UIComponent[];
  };
  readonly updateDataModel?: {
    readonly path?: string;
    readonly value?: unknown;
  };
}

export interface A2UISurface {
  readonly surface_id: string;
  readonly placement?: A2UIPlacement;
  readonly persistent?: boolean;
  readonly messages?: readonly A2UIMessage[];
}

export interface A2UIActionPayload {
  readonly version: 'v1.0';
  readonly action: {
    readonly name: string;
    readonly surfaceId: string;
    readonly sourceComponentId: string;
    readonly timestamp: string;
    readonly context: Readonly<Record<string, unknown>>;
    readonly wantResponse: false;
  };
}

export interface CreateA2UICanvasRendererOptions {
  readonly container: HTMLElement;
  readonly actionUrl: () => string;
  readonly surfaceUrl: (surfaceId: string) => string;
  readonly canEdit?: () => boolean;
}

export interface A2UICanvasRenderer {
  render(surfaces?: readonly A2UISurface[] | null): void;
}

interface MaterializedSurface {
  readonly surfaceId: string | undefined;
  readonly components: ReadonlyMap<string, A2UIComponent>;
  readonly dataModel: unknown;
}

interface RenderedSurface {
  readonly host: HTMLDivElement;
  readonly updateControls?: (surface: A2UISurface) => void;
  contentSignature: string | null;
  content: ChildNode | null;
}

const SUPPORTED = new Set<string>([
  'Card',
  'Column',
  'Row',
  'Grid',
  'Text',
  'Progress',
  'Button',
  'Divider',
  'Icon',
]);

const ICON_GLYPHS: Readonly<Record<string, string>> = {
  sword: '⚔',
  key: '🗝',
  shield: '🛡',
  warning: '⚠',
  star: '✦',
  heart: '♥',
  bag: '🎒',
  coin: '●',
  potion: '⚗',
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object';
}

function valueAtPath(model: unknown, path: string | undefined): unknown {
  if (!path || path === '/') return model;
  return String(path)
    .split('/')
    .slice(1)
    .reduce<unknown>((value, part) => {
      const key = part.replace(/~1/g, '/').replace(/~0/g, '~');
      return isRecord(value) ? value[key] : undefined;
    }, model);
}

function dynamicValue(value: unknown, model: unknown): unknown {
  if (isRecord(value) && typeof value.path === 'string') {
    return valueAtPath(model, value.path);
  }
  return value;
}

function applyDataModelUpdate(
  model: unknown,
  path: string | undefined,
  value: unknown,
): unknown {
  if (!path || path === '/') return value ?? {};
  const next: Record<string, unknown> = isRecord(model)
    ? (structuredClone(model) as Record<string, unknown>)
    : {};
  const parts = String(path)
    .split('/')
    .slice(1)
    .map((part) => part.replace(/~1/g, '/').replace(/~0/g, '~'));
  let cursor = next;
  for (let index = 0; index < parts.length - 1; index += 1) {
    const key = parts[index];
    const child = cursor[key];
    if (!isRecord(child)) {
      const created: Record<string, unknown> = {};
      cursor[key] = created;
      cursor = created;
    } else {
      cursor = child;
    }
  }
  const leaf = parts.at(-1);
  if (leaf !== undefined) {
    if (value === null) delete cursor[leaf];
    else cursor[leaf] = value;
  }
  return next;
}

function materializeSurface(surface: A2UISurface | null | undefined): MaterializedSurface | null {
  let surfaceId = surface?.surface_id;
  let components = new Map<string, A2UIComponent>();
  let dataModel: unknown = {};
  for (const message of surface?.messages || []) {
    if (message.deleteSurface) return null;
    if (message.createSurface) {
      surfaceId = message.createSurface.surfaceId;
      components = new Map(
        (message.createSurface.components || []).map((item) => [item.id, item]),
      );
      dataModel = message.createSurface.dataModel || {};
    }
    if (message.updateComponents) {
      (message.updateComponents.components || []).forEach((item) =>
        components.set(item.id, item),
      );
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

function renderComponent(
  componentId: string,
  byId: ReadonlyMap<string, A2UIComponent>,
  model: unknown,
  surfaceId: string,
  onAction: (payload: A2UIActionPayload, button: HTMLButtonElement) => void,
  stack: ReadonlySet<string> = new Set(),
): ChildNode {
  if (stack.has(componentId)) return document.createTextNode('');
  const component = byId.get(componentId);
  if (!component || !SUPPORTED.has(component.component)) {
    return document.createTextNode('');
  }
  const nextStack = new Set(stack).add(componentId);
  switch (component.component) {
    case 'Card': {
      const element = document.createElement('section');
      element.className = 'a2ui-card';
      element.append(
        renderComponent(component.child, byId, model, surfaceId, onAction, nextStack),
      );
      return element;
    }
    case 'Column':
    case 'Row': {
      const element = document.createElement('div');
      element.className = `a2ui-${component.component.toLowerCase()}`;
      (component.children || []).forEach((child) =>
        element.append(
          renderComponent(child, byId, model, surfaceId, onAction, nextStack),
        ),
      );
      return element;
    }
    case 'Grid': {
      const element = document.createElement('div');
      element.className = `a2ui-grid gap-${component.gap || 'medium'}`;
      const columns = Math.max(1, Math.min(6, Number(component.columns) || 1));
      element.style.setProperty('--a2ui-grid-columns', String(columns));
      (component.children || []).forEach((child) =>
        element.append(
          renderComponent(child, byId, model, surfaceId, onAction, nextStack),
        ),
      );
      return element;
    }
    case 'Text': {
      const element = document.createElement(
        component.variant === 'heading' ? 'h3' : 'p',
      );
      element.className = `a2ui-text ${component.variant || ''}`;
      element.textContent = String(dynamicValue(component.text, model) ?? '');
      return element;
    }
    case 'Progress': {
      const element = document.createElement('div');
      element.className = `a2ui-progress ${component.variant || 'default'}`;
      const rawValue = Number(dynamicValue(component.value, model));
      const rawMax = Number(dynamicValue(component.max ?? 100, model));
      const maximum = Number.isFinite(rawMax) && rawMax > 0 ? rawMax : 100;
      const value = Number.isFinite(rawValue)
        ? Math.max(0, Math.min(maximum, rawValue))
        : 0;
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
      return element;
    }
    case 'Button': {
      const element = document.createElement('button');
      element.type = 'button';
      element.className = `a2ui-button ${component.variant || 'primary'}`;
      element.append(
        renderComponent(component.child, byId, model, surfaceId, onAction, nextStack),
      );
      const event = component.action?.event;
      if (event?.name) {
        element.addEventListener('click', () =>
          onAction(
            {
              version: 'v1.0',
              action: {
                name: event.name,
                surfaceId,
                sourceComponentId: component.id,
                timestamp: new Date().toISOString(),
                context: event.context || {},
                wantResponse: false,
              },
            },
            element,
          ),
        );
      } else {
        element.disabled = true;
      }
      return element;
    }
    case 'Divider': {
      const element = document.createElement('hr');
      element.className = 'a2ui-divider';
      return element;
    }
    case 'Icon': {
      const element = document.createElement('span');
      element.className = 'a2ui-icon';
      element.setAttribute('aria-hidden', 'true');
      element.textContent = ICON_GLYPHS[component.name] || '✦';
      return element;
    }
    default:
      return document.createTextNode('');
  }
}

/**
 * Render and manage interactive A2UI surfaces layered over the canvas,
 * supporting action dispatch, pointer dragging, and incremental DOM updates.
 */
export function createA2UICanvasRenderer(
  options: CreateA2UICanvasRendererOptions,
): A2UICanvasRenderer {
  const { container, actionUrl, surfaceUrl, canEdit = () => true } = options;
  let signature = '';
  const renderedSurfaces = new Map<string, RenderedSurface>();

  async function sendAction(
    payload: A2UIActionPayload,
    button: HTMLButtonElement,
  ): Promise<void> {
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

  async function moveSurface(
    surfaceId: string,
    leftPct: number,
    topPct: number,
  ): Promise<void> {
    const response = await fetch(surfaceUrl(surfaceId), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ left_pct: leftPct, top_pct: topPct }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  }

  function addSurfaceControls(
    host: HTMLDivElement,
    surface: A2UISurface,
  ): ((nextSurface: A2UISurface) => void) | undefined {
    if (!canEdit()) return undefined;
    host.classList.add('a2ui-surface-editable');
    const controls = document.createElement('div');
    controls.className = 'a2ui-surface-controls';

    const move = document.createElement('button');
    move.type = 'button';
    move.className = 'a2ui-surface-control a2ui-move';
    move.title = 'Drag to reposition';
    move.setAttribute('aria-label', 'Drag to reposition this UI');
    move.textContent = surface.persistent ? '📌' : '⠿';
    move.style.touchAction = 'none';

    let activePointerId: number | null = null;
    let startX = 0;
    let startY = 0;
    let startLeft = 0;
    let startTop = 0;
    let latestLeft = Number(surface.placement?.left_pct) || 50;
    let latestTop = Number(surface.placement?.top_pct) || 50;

    host.addEventListener('pointerdown', (event: PointerEvent) => {
      // Only the outer padding and move control are drag handles.
      const target = event.target;
      const inMove = target instanceof Node && move.contains(target);
      if (target !== host && !inMove) return;
      if (event.button !== 0 || activePointerId !== null || !canEdit()) return;
      activePointerId = event.pointerId;
      startX = event.clientX;
      startY = event.clientY;
      startLeft = latestLeft;
      startTop = latestTop;
      move.classList.add('dragging');
      host.classList.add('dragging');
      host.setPointerCapture(event.pointerId);
      event.preventDefault();
      event.stopPropagation();
    });

    host.addEventListener('pointermove', (event: PointerEvent) => {
      if (event.pointerId !== activePointerId) return;
      const rect = container.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      latestLeft = Math.max(
        2,
        Math.min(98, startLeft + ((event.clientX - startX) / rect.width) * 100),
      );
      latestTop = Math.max(
        2,
        Math.min(98, startTop + ((event.clientY - startY) / rect.height) * 100),
      );
      host.style.left = `${latestLeft}%`;
      host.style.top = `${latestTop}%`;
      event.stopPropagation();
    });

    const finishDrag = async (event: PointerEvent): Promise<void> => {
      if (event.pointerId !== activePointerId) return;
      activePointerId = null;
      move.classList.remove('dragging');
      host.classList.remove('dragging');
      if (host.hasPointerCapture(event.pointerId)) {
        host.releasePointerCapture(event.pointerId);
      }
      event.stopPropagation();
      if (event.type !== 'pointerup') {
        latestLeft = startLeft;
        latestTop = startTop;
        host.style.left = `${latestLeft}%`;
        host.style.top = `${latestTop}%`;
        return;
      }
      if (latestLeft === startLeft && latestTop === startTop) return;
      try {
        await moveSurface(surface.surface_id, latestLeft, latestTop);
      } catch (error) {
        console.error('Could not persist A2UI surface position:', error);
      }
    };

    host.addEventListener('pointerup', finishDrag);
    host.addEventListener('pointercancel', finishDrag);
    host.addEventListener('lostpointercapture', finishDrag);

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'a2ui-surface-control a2ui-delete';
    remove.title = 'Remove';
    remove.setAttribute('aria-label', 'Remove this UI');
    remove.textContent = '×';
    remove.addEventListener('click', async () => {
      remove.disabled = true;
      try {
        const response = await fetch(surfaceUrl(surface.surface_id), {
          method: 'DELETE',
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        host.remove();
      } catch (error) {
        console.error('Could not delete A2UI surface:', error);
        remove.disabled = false;
      }
    });

    controls.append(move, remove);
    host.append(controls);
    return (nextSurface: A2UISurface): void => {
      move.textContent = nextSurface.persistent ? '📌' : '⠿';
      if (activePointerId !== null) return;
      latestLeft = Number(nextSurface.placement?.left_pct) || 50;
      latestTop = Number(nextSurface.placement?.top_pct) || 50;
    };
  }

  function render(surfaces?: readonly A2UISurface[] | null): void {
    const nextSignature = JSON.stringify(surfaces || []);
    if (nextSignature === signature) return;
    signature = nextSignature;
    const activeSurfaceIds = new Set<string>();
    (Array.isArray(surfaces) ? surfaces : []).forEach((surface) => {
      const materialized = materializeSurface(surface);
      if (!materialized?.surfaceId || !materialized.components.size) return;
      activeSurfaceIds.add(materialized.surfaceId);
      const byId = materialized.components;
      const placement = surface.placement || {};
      let rendered = renderedSurfaces.get(materialized.surfaceId);
      if (!rendered) {
        const host = document.createElement('div');
        host.className = 'a2ui-surface';
        host.dataset.surfaceId = materialized.surfaceId;
        rendered = {
          host,
          updateControls: addSurfaceControls(host, surface),
          contentSignature: null,
          content: null,
        };
        renderedSurfaces.set(materialized.surfaceId, rendered);
      }
      const { host } = rendered;
      if (!host.classList.contains('dragging')) {
        host.style.left = `${Number(placement.left_pct) || 50}%`;
        host.style.top = `${Number(placement.top_pct) || 50}%`;
      }
      host.style.width = `${Number(placement.width_pct) || 28}%`;
      host.classList.toggle('persistent', Boolean(surface.persistent));
      rendered.updateControls?.(surface);
      const contentSignature = JSON.stringify(surface.messages || []);
      if (contentSignature !== rendered.contentSignature) {
        const content = renderComponent(
          'root',
          byId,
          materialized.dataModel,
          materialized.surfaceId,
          sendAction,
        );
        if (rendered.content) rendered.content.replaceWith(content);
        else host.append(content);
        rendered.content = content;
        rendered.contentSignature = contentSignature;
      }
      // Keep existing hosts attached so their arrival animation and UI state survive updates.
      if (host.parentNode !== container) container.append(host);
    });
    for (const [surfaceId, rendered] of renderedSurfaces) {
      if (activeSurfaceIds.has(surfaceId)) continue;
      rendered.host.remove();
      renderedSurfaces.delete(surfaceId);
    }
  }

  return { render };
}
