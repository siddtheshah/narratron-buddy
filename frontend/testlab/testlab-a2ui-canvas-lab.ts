/** A2UI Canvas Lab page controller: defaults loading, run polling, and surface preview rendering. */

export type A2uiStringValue =
  | string
  | { readonly literalString?: string; readonly value?: string };

export type A2uiNumberValue =
  | number
  | { readonly literalNumber?: number; readonly value?: number };

export interface A2uiComponent {
  readonly id: string;
  readonly component?: string;
  readonly child?: string;
  readonly children?: readonly string[];
  readonly columns?: number;
  readonly variant?: string;
  readonly text?: A2uiStringValue;
  readonly label?: A2uiStringValue;
  readonly value?: A2uiNumberValue;
  readonly max?: A2uiNumberValue;
  readonly name?: string;
}

export interface A2uiSurfacePlacement {
  readonly left_pct?: number;
  readonly top_pct?: number;
  readonly width_pct?: number;
}

export interface A2uiCreateSurfaceMessage {
  readonly createSurface?: {
    readonly components?: readonly A2uiComponent[];
  };
}

export interface A2uiSurface {
  readonly placement?: A2uiSurfacePlacement;
  readonly messages?: readonly A2uiCreateSurfaceMessage[];
}

export interface A2uiResultEnvelope {
  readonly status?: string;
  readonly result?: { readonly status?: string; readonly surfaces?: readonly A2uiSurface[] };
  readonly surfaces?: readonly A2uiSurface[];
}

export interface A2uiRunPayload {
  readonly id?: string;
  readonly status?: string;
  readonly detail?: string;
  readonly result?: A2uiResultEnvelope;
  readonly surfaces?: readonly A2uiSurface[];
}

export interface A2uiDefaultConfig {
  readonly model: string;
  readonly request: string;
  readonly expected_surface_count: number;
}

export interface A2uiImageOption {
  readonly path: string;
  readonly label: string;
}

export interface A2uiImagesResponse {
  readonly images: readonly A2uiImageOption[];
}

export interface A2uiCanvasLabController {
  loadDefaults(): Promise<void>;
  previewImage(): void;
  render(run: A2uiRunPayload): void;
  destroy(): void;
}

const ICONS: Readonly<Record<string, string>> = {
  sword: '⚔',
  key: '🗝',
  shield: '🛡',
  warning: '⚠',
  star: '★',
  heart: '♥',
  bag: '▣',
  coin: '●',
  potion: '⚗',
};

function readText(value: A2uiStringValue | undefined): string {
  if (typeof value === 'string') {
    return value;
  }
  return value?.literalString ?? value?.value ?? '';
}

function readNumber(value: A2uiNumberValue | undefined): number {
  if (typeof value === 'number') {
    return value;
  }
  return Number(value?.literalNumber ?? value?.value ?? 0);
}

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  return node;
}

function renderComponent(
  component: A2uiComponent | undefined,
  byId: ReadonlyMap<string, A2uiComponent>,
  visiting: Set<string> = new Set(),
): HTMLElement {
  if (!component || visiting.has(component.id)) {
    return element('div');
  }
  visiting.add(component.id);
  const child = (id: string | undefined): HTMLElement =>
    renderComponent(id ? byId.get(id) : undefined, byId, new Set(visiting));

  let node: HTMLElement;
  if (component.component === 'Card') {
    node = element('div', 'a2ui-card');
    node.append(child(component.child));
  } else if (component.component === 'Column' || component.component === 'Row') {
    node = element('div', `a2ui-${component.component.toLowerCase()}`);
    (component.children || []).forEach((id: string): void => {
      node.append(child(id));
    });
  } else if (component.component === 'Grid') {
    node = element('div', 'a2ui-grid');
    node.style.gridTemplateColumns = `repeat(${component.columns || 2},minmax(0,1fr))`;
    (component.children || []).forEach((id: string): void => {
      node.append(child(id));
    });
  } else if (component.component === 'Text') {
    node = element('div', `a2ui-text ${component.variant || 'body'}`);
    node.textContent = readText(component.text);
  } else if (component.component === 'Progress') {
    const value = readNumber(component.value);
    const maximum = readNumber(component.max) || 100;
    const percent = Math.max(0, Math.min(100, (value / maximum) * 100));
    node = element('div', `a2ui-progress ${component.variant || ''}`);
    const label = element('div', 'progress-label');
    const name = element('span');
    const amount = element('span');
    name.textContent = readText(component.label);
    amount.textContent = `${value}/${maximum}`;
    label.append(name, amount);
    const track = element('div', 'progress-track');
    const fill = element('div', 'progress-fill');
    fill.style.width = `${percent}%`;
    track.append(fill);
    node.append(label, track);
  } else if (component.component === 'Button') {
    const button = element('button', 'a2ui-button');
    button.type = 'button';
    button.append(child(component.child));
    node = button;
  } else if (component.component === 'Divider') {
    node = element('div', 'a2ui-divider');
  } else if (component.component === 'Icon') {
    node = element('span', 'a2ui-icon');
    node.textContent = (component.name && ICONS[component.name]) || '◇';
  } else {
    node = element('div');
    node.textContent = `Unsupported component: ${component.component || 'unknown'}`;
  }
  return node;
}

/** Wires the A2UI Canvas Lab controls, polling, and surface preview renderer. */
export function initA2uiCanvasLab(): A2uiCanvasLabController | null {
  const output = document.querySelector<HTMLElement>('#output');
  const submit = document.querySelector<HTMLButtonElement>('#submit');
  const stage = document.querySelector<HTMLElement>('#stage');
  const emptyStage = document.querySelector<HTMLElement>('#empty-stage');
  const imageSelect = document.querySelector<HTMLSelectElement>('#image_path');
  const form = document.querySelector<HTMLFormElement>('#run-form');
  const modelInput = document.querySelector<HTMLInputElement>('#model');
  const requestInput = document.querySelector<HTMLTextAreaElement>('#request');
  const surfaceCountInput = document.querySelector<HTMLInputElement>('#surface_count');
  const persistentSelect = document.querySelector<HTMLSelectElement>('#persistent');

  if (
    !output ||
    !submit ||
    !stage ||
    !emptyStage ||
    !imageSelect ||
    !form ||
    !modelInput ||
    !requestInput ||
    !surfaceCountInput ||
    !persistentSelect
  ) {
    return null;
  }

  let pollTimer: number | null = null;

  const show = (value: unknown): void => {
    output.textContent = JSON.stringify(value, null, 2);
  };

  const render = (run: A2uiRunPayload): void => {
    stage.querySelectorAll('.surface').forEach((node: Element): void => node.remove());
    const result = run?.result?.surfaces ? run.result : run;
    const status = result?.result?.status ?? '';
    const surfaces = ['displayed', 'updated', 'applied'].includes(status)
      ? result.surfaces || []
      : [];
    emptyStage.hidden = Boolean(surfaces.length);
    surfaces.forEach((surface: A2uiSurface): void => {
      const create = surface.messages?.find(
        (message: A2uiCreateSurfaceMessage): boolean => Boolean(message.createSurface),
      )?.createSurface;
      if (!create) {
        return;
      }
      const components = create.components || [];
      const byId = new Map<string, A2uiComponent>(
        components.map((component: A2uiComponent): [string, A2uiComponent] => [
          component.id,
          component,
        ]),
      );
      const referenced = new Set<string>(
        components
          .flatMap((component: A2uiComponent): readonly (string | undefined)[] => [
            component.child,
            ...(component.children || []),
          ])
          .filter((id): id is string => Boolean(id)),
      );
      const frame = element('section', 'surface');
      const placement = surface.placement || {};
      frame.style.left = `${placement.left_pct ?? 50}%`;
      frame.style.top = `${placement.top_pct ?? 55}%`;
      frame.style.width = `${placement.width_pct ?? 28}%`;
      frame.style.transform = 'translate(-50%, -50%)';
      components
        .filter((component: A2uiComponent): boolean => component.id === 'root')
        .forEach((component: A2uiComponent): void => {
          frame.append(renderComponent(component, byId));
        });
      const orphans = components.filter(
        (component: A2uiComponent): boolean =>
          component.id !== 'root' && !referenced.has(component.id),
      );
      if (orphans.length) {
        const list = element('div', 'orphan-list');
        orphans.forEach((component: A2uiComponent): void => {
          list.append(renderComponent(component, byId));
        });
        frame.append(list);
      }
      stage.append(frame);
    });
  };

  const previewImage = (): void => {
    const path = imageSelect.value;
    stage.style.backgroundImage = path
      ? `url('/api/a2ui-canvas/image?path=${encodeURIComponent(path)}')`
      : '';
  };

  const loadDefaults = async (): Promise<void> => {
    const [configResponse, imagesResponse] = await Promise.all([
      fetch('/api/a2ui-canvas/default-config'),
      fetch('/api/a2ui-canvas/images'),
    ]);
    const config = (await configResponse.json()) as A2uiDefaultConfig;
    const { images } = (await imagesResponse.json()) as A2uiImagesResponse;
    modelInput.value = config.model;
    requestInput.value = config.request;
    surfaceCountInput.value = String(config.expected_surface_count);
    images.forEach((image: A2uiImageOption): void => {
      const option = document.createElement('option');
      option.value = image.path;
      option.textContent = image.label;
      imageSelect.append(option);
    });
    show(config);
  };

  const onSubmit = async (event: SubmitEvent): Promise<void> => {
    event.preventDefault();
    submit.disabled = true;
    try {
      const persistent = persistentSelect.value;
      const body = {
        model: modelInput.value,
        request: requestInput.value,
        image_path: imageSelect.value || null,
        expected_surface_count: Number(surfaceCountInput.value),
        expected_persistent: persistent ? persistent === 'true' : null,
      };
      const response = await fetch('/api/a2ui-canvas/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const run = (await response.json()) as A2uiRunPayload;
      if (!response.ok) {
        throw new Error(run.detail || JSON.stringify(run));
      }
      show(run);
      const poll = async (): Promise<void> => {
        const current = (await (await fetch(`/api/a2ui-canvas/runs/${run.id}`)).json()) as A2uiRunPayload;
        show(current);
        render(current);
        if (current.status === 'running') {
          pollTimer = window.setTimeout((): void => {
            void poll();
          }, 500);
        } else {
          submit.disabled = false;
        }
      };
      void poll();
    } catch (error: unknown) {
      show({ error: String(error) });
      submit.disabled = false;
    }
  };

  const onFormSubmit = (event: SubmitEvent): void => {
    void onSubmit(event);
  };

  imageSelect.addEventListener('change', previewImage);
  form.addEventListener('submit', onFormSubmit);

  loadDefaults().catch((error: unknown): void => show({ error: String(error) }));

  return {
    loadDefaults,
    previewImage,
    render,
    destroy(): void {
      if (pollTimer !== null) {
        window.clearTimeout(pollTimer);
        pollTimer = null;
      }
      imageSelect.removeEventListener('change', previewImage);
      form.removeEventListener('submit', onFormSubmit);
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', (): void => {
    initA2uiCanvasLab();
  });
} else {
  initA2uiCanvasLab();
}
