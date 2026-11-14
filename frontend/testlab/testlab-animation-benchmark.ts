/** Animation Benchmark Lab page controller: catalog loading, run polling, layer filters, and CSS motion loop. */

import { layerCssTransform } from '../shared/animation-effects.js';

export interface AnimationLayerSpec {
  readonly order: number;
  readonly name: string;
  readonly effect: string;
  readonly description: string;
  readonly url: string;
}

export interface AnimationManifest {
  readonly base_url: string;
  readonly layers?: readonly AnimationLayerSpec[];
}

export interface AnimationProviderSpec {
  readonly id: string;
  readonly name: string;
  readonly status: string;
}

export interface AnimationPromptSpec {
  readonly id: string;
  readonly title: string;
  readonly prompt: string;
}

export interface AnimationCatalogResponse {
  readonly providers: readonly AnimationProviderSpec[];
  readonly prompts: readonly AnimationPromptSpec[];
}

export interface AnimationRunPayload {
  readonly id: string;
  readonly status: string;
  readonly latency_ms?: number | null;
  readonly manifest?: AnimationManifest;
  readonly detail?: string;
}

export interface AnimationBenchmarkController {
  load(): Promise<void>;
  render(manifest: AnimationManifest): void;
  destroy(): void;
}

interface VisibleLayerEntry {
  readonly layer: AnimationLayerSpec;
  readonly image: HTMLImageElement;
}

function getEl(id: string): HTMLElement | null {
  return document.getElementById(id);
}

/** Wires the Animation Benchmark Lab controls, API polling, and animated layer stage. */
export function initAnimationBenchmark(): AnimationBenchmarkController | null {
  const providerElRaw = getEl('provider');
  const promptsEl = getEl('prompts');
  const statusEl = getEl('status');
  const customPromptTextRaw = getEl('customPromptText');
  const useCustomPromptRaw = getEl('useCustomPrompt');
  const runBtnRaw = getEl('run');
  const debugEl = getEl('debug');
  const stageEl = getEl('stage');
  const sideSelectorEl = getEl('sideSelector');
  const layerCountBadgeEl = getEl('layerCountBadge');
  const layersSummaryEl = getEl('layersSummary');
  const layerFilterListEl = getEl('layerFilterList');
  const activeFilterBadgeEl = getEl('activeFilterBadge');
  const btnAllLayersRaw = getEl('btnAllLayers');
  const btnBaseImageRaw = getEl('btnBaseImage');

  if (
    !(providerElRaw instanceof HTMLSelectElement) ||
    !promptsEl ||
    !statusEl ||
    !(customPromptTextRaw instanceof HTMLTextAreaElement ||
      customPromptTextRaw instanceof HTMLInputElement) ||
    !(useCustomPromptRaw instanceof HTMLInputElement) ||
    !(runBtnRaw instanceof HTMLButtonElement) ||
    !debugEl ||
    !stageEl ||
    !sideSelectorEl ||
    !layerCountBadgeEl ||
    !layersSummaryEl ||
    !layerFilterListEl ||
    !activeFilterBadgeEl ||
    !(btnAllLayersRaw instanceof HTMLButtonElement) ||
    !(btnBaseImageRaw instanceof HTMLButtonElement)
  ) {
    return null;
  }

  const providerEl = providerElRaw;
  const customPromptText = customPromptTextRaw;
  const useCustomPrompt = useCustomPromptRaw;
  const runBtn = runBtnRaw;
  const btnAllLayers = btnAllLayersRaw;
  const btnBaseImage = btnBaseImageRaw;

  let raf: number | null = null;
  let pollTimer: number | null = null;
  let currentManifest: AnimationManifest | null = null;
  let mode: 'all' | 'base' | 'custom' = 'all';
  let activeIndices = new Set<number>();
  let soloIndex: number | null = null;

  const updateStageView = (): void => {
    if (raf !== null) {
      cancelAnimationFrame(raf);
      raf = null;
    }
    stageEl.innerHTML = '';

    if (!currentManifest) {
      stageEl.innerHTML =
        '<div class="stage-placeholder">Run an animation decomposition to preview layers</div>';
      activeFilterBadgeEl.textContent = 'No run loaded';
      activeFilterBadgeEl.classList.remove('active');
      return;
    }

    activeFilterBadgeEl.classList.add('active');

    if (mode === 'base') {
      const baseImg = new Image();
      baseImg.src = currentManifest.base_url;
      baseImg.alt = 'Base Image';
      stageEl.append(baseImg);
      activeFilterBadgeEl.textContent = 'Base Image (Original)';
      return;
    }

    const layers = currentManifest.layers || [];
    const visibleLayers = layers.filter((_: AnimationLayerSpec, idx: number): boolean =>
      activeIndices.has(idx),
    );

    if (visibleLayers.length === 0) {
      stageEl.innerHTML = '<div class="stage-placeholder">No layers selected in filter</div>';
      activeFilterBadgeEl.textContent = 'Hidden (0 layers)';
      return;
    }

    if (soloIndex !== null && layers[soloIndex]) {
      activeFilterBadgeEl.textContent = `Solo: Layer ${soloIndex + 1} (${layers[soloIndex].name})`;
    } else if (mode === 'all') {
      activeFilterBadgeEl.textContent = `All Layers (${layers.length})`;
    } else {
      activeFilterBadgeEl.textContent = `Filtered (${visibleLayers.length}/${layers.length} layers)`;
    }

    const renderedEntries: VisibleLayerEntry[] = visibleLayers.map(
      (layer: AnimationLayerSpec): VisibleLayerEntry => {
        const image = new Image();
        image.src = layer.url;
        image.alt = layer.name;
        stageEl.append(image);
        return { layer, image };
      },
    );

    const started = performance.now();
    const tick = (now: number): void => {
      for (const entry of renderedEntries) {
        entry.image.style.transform = layerCssTransform(
          entry.layer.effect,
          (now - started) / 1000,
        );
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
  };

  const syncFilterControls = (): void => {
    btnAllLayers.classList.toggle('active', mode === 'all');
    btnBaseImage.classList.toggle('active', mode === 'base');

    const cards = document.querySelectorAll('.layer-card');
    cards.forEach((card: Element, idx: number): void => {
      const isChecked = activeIndices.has(idx) && mode !== 'base';
      const isSolo = soloIndex === idx;
      card.classList.toggle('active', isChecked);
      card.classList.toggle('soloed', isSolo);

      const cb = card.querySelector('.layer-checkbox');
      if (cb instanceof HTMLInputElement) {
        cb.checked = isChecked;
      }

      const soloBtn = card.querySelector('.solo-btn');
      if (soloBtn) {
        soloBtn.classList.toggle('active', isSolo);
        soloBtn.textContent = isSolo ? 'Soloed' : 'Solo';
      }
    });
  };

  const toggleLayer = (index: number, isChecked: boolean): void => {
    if (soloIndex !== null) {
      soloIndex = null;
    }
    if (isChecked) {
      activeIndices.add(index);
    } else {
      activeIndices.delete(index);
    }
    const totalLayers = currentManifest?.layers?.length || 0;
    mode = activeIndices.size === totalLayers ? 'all' : 'custom';
    syncFilterControls();
    updateStageView();
  };

  const soloLayer = (index: number): void => {
    if (soloIndex === index) {
      soloIndex = null;
      mode = 'all';
      activeIndices = new Set((currentManifest?.layers || []).map((_: AnimationLayerSpec, i: number): number => i));
    } else {
      soloIndex = index;
      mode = 'custom';
      activeIndices = new Set([index]);
    }
    syncFilterControls();
    updateStageView();
  };

  const buildLayerFilterUI = (layers: readonly AnimationLayerSpec[]): void => {
    layerFilterListEl.innerHTML = '';

    layers.forEach((layer: AnimationLayerSpec, index: number): void => {
      const card = document.createElement('div');
      card.className = `layer-card ${activeIndices.has(index) ? 'active' : ''}`;
      card.dataset.index = String(index);

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'layer-checkbox';
      checkbox.checked = activeIndices.has(index);
      checkbox.onclick = (e: MouseEvent): void => {
        e.stopPropagation();
        toggleLayer(index, checkbox.checked);
      };

      const thumbBox = document.createElement('div');
      thumbBox.className = 'layer-thumb-box';
      const thumbImg = document.createElement('img');
      thumbImg.src = layer.url;
      thumbImg.alt = layer.name;
      thumbBox.append(thumbImg);

      const meta = document.createElement('div');
      meta.className = 'layer-meta';

      const topRow = document.createElement('div');
      topRow.className = 'layer-meta-top';
      const nameSpan = document.createElement('span');
      nameSpan.className = 'layer-name';
      nameSpan.textContent = `${index + 1}. ${layer.name}`;

      const effectSpan = document.createElement('span');
      effectSpan.className = `effect-chip effect-${layer.effect || 'none'}`;
      effectSpan.textContent = layer.effect || 'none';

      topRow.append(nameSpan, effectSpan);

      const descDiv = document.createElement('div');
      descDiv.className = 'layer-desc-text';
      descDiv.textContent = layer.description;

      meta.append(topRow, descDiv);

      const soloBtn = document.createElement('button');
      soloBtn.className = 'solo-btn';
      soloBtn.textContent = 'Solo';
      soloBtn.onclick = (e: MouseEvent): void => {
        e.stopPropagation();
        soloLayer(index);
      };

      card.append(checkbox, thumbBox, meta, soloBtn);
      card.onclick = (): void => {
        toggleLayer(index, !activeIndices.has(index));
      };

      layerFilterListEl.append(card);
    });

    btnAllLayers.onclick = (): void => {
      mode = 'all';
      soloIndex = null;
      activeIndices = new Set(layers.map((_: AnimationLayerSpec, i: number): number => i));
      syncFilterControls();
      updateStageView();
    };

    btnBaseImage.onclick = (): void => {
      mode = 'base';
      soloIndex = null;
      syncFilterControls();
      updateStageView();
    };
  };

  const render = (manifest: AnimationManifest): void => {
    currentManifest = manifest;
    const layers = manifest.layers || [];
    activeIndices = new Set(layers.map((_: AnimationLayerSpec, i: number): number => i));
    soloIndex = null;
    mode = 'all';

    sideSelectorEl.classList.remove('hidden');
    layerCountBadgeEl.textContent = `${layers.length} layers`;

    buildLayerFilterUI(layers);
    updateStageView();

    layersSummaryEl.textContent = layers
      .map(
        (x: AnimationLayerSpec): string =>
          `${x.order + 1}. ${x.name} — ${x.effect}: ${x.description}`,
      )
      .join(' | ');
  };

  const load = async (): Promise<void> => {
    const data = (await (await fetch('/api/animation-benchmark/catalog')).json()) as AnimationCatalogResponse;
    data.providers
      .filter((p: AnimationProviderSpec): boolean => p.status === 'available')
      .forEach((p: AnimationProviderSpec): void => providerEl.add(new Option(p.name, p.id)));
    if (!providerEl.options.length) {
      data.providers.forEach((p: AnimationProviderSpec): void =>
        providerEl.add(new Option(p.name, p.id)),
      );
    }

    data.prompts.forEach((p: AnimationPromptSpec, index: number): void => {
      const input = document.createElement('input');
      input.type = 'radio';
      input.name = 'prompt-choice';
      input.value = p.id;
      input.checked = index === 0;

      const label = document.createElement('label');
      label.className = 'choice';
      label.append(
        input,
        Object.assign(document.createElement('strong'), { textContent: p.title }),
        Object.assign(document.createElement('small'), { textContent: p.prompt }),
      );
      promptsEl.append(label);
    });

    statusEl.textContent = 'Ready.';
  };

  const onCustomPromptInput = (): void => {
    useCustomPrompt.checked = true;
  };

  const onRunClick = async (): Promise<void> => {
    const custom = customPromptText.value.trim();
    const selected = document.querySelector<HTMLInputElement>('input[name="prompt-choice"]:checked');
    const body: {
      provider_id: string;
      prompt?: string;
      prompt_id?: string;
    } = { provider_id: providerEl.value };

    if (useCustomPrompt.checked) {
      if (!custom) {
        statusEl.textContent = 'Enter a write-in prompt.';
        return;
      }
      body.prompt = custom;
    } else {
      body.prompt_id = selected?.value;
    }

    runBtn.disabled = true;
    statusEl.textContent = 'Running layer plan and base generation…';

    const response = await fetch('/api/animation-benchmark/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    let run = (await response.json()) as AnimationRunPayload;
    if (!response.ok) {
      debugEl.textContent = JSON.stringify(run, null, 2);
      runBtn.disabled = false;
      return;
    }

    const poll = async (): Promise<void> => {
      run = (await (await fetch(`/api/animation-benchmark/runs/${run.id}`)).json()) as AnimationRunPayload;
      const seconds = run.latency_ms == null ? '' : ` in ${(run.latency_ms / 1000).toFixed(2)} s`;
      statusEl.textContent =
        run.status === 'completed'
          ? `Completed${seconds}.`
          : run.status === 'failed'
            ? `Failed${seconds}.`
            : run.status;
      debugEl.textContent = JSON.stringify(run, null, 2);

      if (run.status === 'running') {
        pollTimer = window.setTimeout((): void => {
          void poll();
        }, 500);
      } else {
        runBtn.disabled = false;
        if (run.status === 'completed' && run.manifest) {
          render(run.manifest);
        }
      }
    };
    void poll();
  };

  const onRunHandler = (): void => {
    void onRunClick();
  };

  customPromptText.addEventListener('input', onCustomPromptInput);
  runBtn.addEventListener('click', onRunHandler);

  load().catch((e: unknown): void => {
    statusEl.textContent = String(e);
  });

  return {
    load,
    render,
    destroy(): void {
      if (raf !== null) {
        cancelAnimationFrame(raf);
        raf = null;
      }
      if (pollTimer !== null) {
        window.clearTimeout(pollTimer);
        pollTimer = null;
      }
      customPromptText.removeEventListener('input', onCustomPromptInput);
      runBtn.removeEventListener('click', onRunHandler);
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', (): void => {
    initAnimationBenchmark();
  });
} else {
  initAnimationBenchmark();
}
