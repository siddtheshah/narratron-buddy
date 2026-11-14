/** Text Response Provider Bench page controller: catalog, run polling, stats, gallery slicing/pagination, and review notes. */

export interface TextProviderSpec {
  readonly id: string;
  readonly name: string;
  readonly model: string;
  readonly status: string;
  readonly notes: string;
  readonly model_options?: readonly string[];
}

export interface TextPromptSpec {
  readonly id: string;
  readonly title: string;
  readonly dimension: string;
  readonly prompt: string;
  readonly temperature: number;
  readonly system_instruction?: string | null;
  readonly max_output_tokens?: number;
}

export interface TextCatalogResponse {
  readonly providers: readonly TextProviderSpec[];
  readonly prompts: readonly TextPromptSpec[];
}

export interface TextCustomPromptInput {
  readonly prompt: string;
  readonly title: string;
  readonly dimension: string;
  readonly system_instruction: string | null;
  readonly temperature: number;
  readonly max_output_tokens: number;
}

export interface TextRunItem {
  readonly id: string;
  readonly provider_id: string;
  readonly prompt_id: string;
  readonly repetition: number;
  readonly status: string;
  readonly latency_ms: number;
  readonly text?: string;
  readonly model?: string;
  readonly finish_reason?: string | null;
  readonly error?: string;
  note?: string;
}

export interface TextRunPayload {
  readonly id: string;
  readonly status: string;
  readonly total: number;
  readonly prompts?: readonly TextPromptSpec[];
  readonly items: readonly TextRunItem[];
  readonly detail?: string;
}

export interface TextNoteResponse {
  readonly id?: string;
  readonly note?: string;
  readonly detail?: string;
}

export interface TextResponseBenchmarkController {
  loadCatalog(): Promise<void>;
  destroy(): void;
}

type GalleryViewMode = 'provider' | 'prompt';

type ElementAttrs<K extends keyof HTMLElementTagNameMap> = Partial<HTMLElementTagNameMap[K]>;
type ChildItem = Node | string | null | undefined;

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs?: ElementAttrs<K>,
  children: readonly ChildItem[] = [],
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (attrs !== undefined) {
    Object.assign(node, attrs);
  }
  for (const child of children) {
    if (child !== null && child !== undefined) {
      node.append(child);
    }
  }
  return node;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function percentile(values: readonly number[], p: number): number | null {
  if (values.length === 0) {
    return null;
  }
  const sorted = [...values].sort((a: number, b: number): number => a - b);
  const index = (sorted.length - 1) * p;
  const low = Math.floor(index);
  const high = Math.ceil(index);
  return sorted[low] + (sorted[high] - sorted[low]) * (index - low);
}

function formatStat(label: string, value: string): HTMLSpanElement {
  return el('span', {}, [`${label} `, el('strong', { textContent: value })]);
}

/** Initializes the Text Response Provider Bench DOM controls, polling, gallery, and note saving. */
export function initTextResponseBenchmark(): TextResponseBenchmarkController | null {
  const providersEl = document.getElementById('providers');
  const promptsEl = document.getElementById('prompts');
  const selectAllBtn = document.getElementById('selectAll');
  const deselectAllBtn = document.getElementById('deselectAll');
  const repetitionsInput = document.getElementById('repetitions');
  const useCustomPromptCheckbox = document.getElementById('useCustomPrompt');
  const customPromptInput = document.getElementById('customPromptText');
  const customSystemInstructionInput = document.getElementById('customSystemInstruction');
  const customTemperatureInput = document.getElementById('customTemperature');
  const customMaxTokensInput = document.getElementById('customMaxTokens');
  const runBtn = document.getElementById('run');
  const statusEl = document.getElementById('status');
  const galleryEl = document.getElementById('gallery');
  const galleryViewSelect = document.getElementById('galleryView');
  const galleryFilterLabel = document.getElementById('galleryFilterLabel');
  const galleryProviderSelect = document.getElementById('galleryProvider');
  const previousPageBtn = document.getElementById('previousPage');
  const nextPageBtn = document.getElementById('nextPage');
  const galleryPageEl = document.getElementById('galleryPage');
  const galleryPromptDetailEl = document.getElementById('galleryPromptDetail');
  const galleryResultsEl = document.getElementById('galleryResults');

  if (
    !(providersEl instanceof HTMLElement) ||
    !(promptsEl instanceof HTMLElement) ||
    !(selectAllBtn instanceof HTMLButtonElement) ||
    !(deselectAllBtn instanceof HTMLButtonElement) ||
    !(repetitionsInput instanceof HTMLInputElement) ||
    !(useCustomPromptCheckbox instanceof HTMLInputElement) ||
    !(customPromptInput instanceof HTMLTextAreaElement) ||
    !(customSystemInstructionInput instanceof HTMLInputElement) ||
    !(customTemperatureInput instanceof HTMLInputElement) ||
    !(customMaxTokensInput instanceof HTMLInputElement) ||
    !(runBtn instanceof HTMLButtonElement) ||
    !(statusEl instanceof HTMLElement) ||
    !(galleryEl instanceof HTMLElement) ||
    !(galleryViewSelect instanceof HTMLSelectElement) ||
    !(galleryFilterLabel instanceof HTMLElement) ||
    !(galleryProviderSelect instanceof HTMLSelectElement) ||
    !(previousPageBtn instanceof HTMLButtonElement) ||
    !(nextPageBtn instanceof HTMLButtonElement) ||
    !(galleryPageEl instanceof HTMLElement) ||
    !(galleryPromptDetailEl instanceof HTMLElement) ||
    !(galleryResultsEl instanceof HTMLElement)
  ) {
    return null;
  }

  let catalog: TextCatalogResponse | null = null;
  let currentRun: TextRunPayload | null = null;
  let pollInterval: number | null = null;
  let galleryPage = 0;
  let galleryProvider = 'all';
  let galleryPrompt = 'all';
  let galleryView: GalleryViewMode = 'provider';
  let destroyed = false;

  const getPromptMap = (run: TextRunPayload | null): Record<string, TextPromptSpec> => {
    const map: Record<string, TextPromptSpec> = {};
    for (const prompt of catalog?.prompts ?? []) {
      map[prompt.id] = prompt;
    }
    for (const prompt of run?.prompts ?? []) {
      map[prompt.id] = prompt;
    }
    return map;
  };

  const getProviderMap = (): Record<string, TextProviderSpec> => {
    const map: Record<string, TextProviderSpec> = {};
    for (const provider of catalog?.providers ?? []) {
      map[provider.id] = provider;
    }
    return map;
  };

  const renderCatalog = (loadedCatalog: TextCatalogResponse): void => {
    providersEl.innerHTML = '';
    promptsEl.innerHTML = '';

    for (const provider of loadedCatalog.providers) {
      const input = el('input', {
        type: 'checkbox',
        value: provider.id,
        checked: provider.status === 'available',
        disabled: provider.status !== 'available',
      });
      const stats = el('div', { className: 'provider-stats hidden' });
      stats.setAttribute('data-provider-stats', provider.id);

      const children: ChildItem[] = [
        input,
        el('strong', { textContent: provider.name }),
        el('small', { textContent: provider.model }),
        el('span', { className: 'pill', textContent: provider.status }),
        el('small', { textContent: provider.notes }),
      ];

      if (provider.model_options && provider.model_options.length > 0) {
        const modelSelect = el('select');
        modelSelect.setAttribute('data-provider-option', 'model');
        modelSelect.setAttribute('data-provider-id', provider.id);
        for (const name of provider.model_options) {
          modelSelect.append(
            el('option', {
              value: name,
              textContent: name,
              selected: name === provider.model,
            }),
          );
        }
        modelSelect.addEventListener('click', (event: MouseEvent): void => {
          event.stopPropagation();
        });
        children.push(el('small', { textContent: 'Model' }), modelSelect);
      }

      children.push(stats);
      providersEl.append(el('label', { className: 'choice' }, children));
    }

    loadedCatalog.prompts.forEach((prompt: TextPromptSpec, index: number): void => {
      const input = el('input', {
        type: 'checkbox',
        value: prompt.id,
        checked: index === 0,
      });
      promptsEl.append(
        el('label', { className: 'choice' }, [
          input,
          el('strong', { textContent: `${index + 1}. ${prompt.title}` }),
          el('small', { textContent: `${prompt.dimension} · temp ${prompt.temperature}` }),
          el('small', { textContent: prompt.prompt }),
        ]),
      );
    });
  };

  const selectedIds = (containerId: string): string[] => {
    const checked = document.querySelectorAll<HTMLInputElement>(`#${containerId} input:checked`);
    return Array.from(checked, (node: HTMLInputElement): string => node.value);
  };

  const collectProviderOptions = (): Record<string, Record<string, string>> => {
    const options: Record<string, Record<string, string>> = {};
    const nodes = document.querySelectorAll<HTMLSelectElement | HTMLInputElement>(
      '[data-provider-option]',
    );
    nodes.forEach((input: HTMLSelectElement | HTMLInputElement): void => {
      const id = input.dataset.providerId;
      const optionKey = input.dataset.providerOption;
      if (!id || !optionKey) {
        return;
      }
      options[id] ??= {};
      options[id][optionKey] = input.value;
    });
    return options;
  };

  const saveGenerationNote = async (item: TextRunItem, noteText: string): Promise<void> => {
    if (!currentRun) {
      throw new Error('No active run.');
    }
    const response = await fetch(
      `/api/text-benchmark/runs/${currentRun.id}/items/${item.id}/note`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note: noteText }),
      },
    );
    const data = (await response.json()) as TextNoteResponse;
    if (!response.ok) {
      throw new Error(data.detail || 'Could not save note.');
    }
    item.note = data.note ?? noteText;
  };

  const makeResultCard = (
    item: TextRunItem,
    byId: Record<string, TextPromptSpec> = getPromptMap(currentRun),
    specs: Record<string, TextProviderSpec> = getProviderMap(),
  ): HTMLElement => {
    const prompt: Pick<TextPromptSpec, 'title' | 'dimension' | 'prompt'> =
      byId[item.prompt_id] ?? {
        title: 'Custom Text Prompt',
        dimension: 'Manual Prompt',
        prompt: '',
      };
    const provider: Pick<TextProviderSpec, 'name' | 'model'> = specs[item.provider_id] ?? {
      name: item.provider_id,
      model: '',
    };

    const card = el('article', {
      className: `result ${item.status === 'failed' ? 'error' : ''}`,
    });
    if (item.text) {
      card.append(el('div', { className: 'text-wrapper', textContent: item.text }));
    }

    const body = el('div', { className: 'result-body' });
    const finishSuffix = item.finish_reason ? ` · ${item.finish_reason}` : '';
    body.append(
      el('h3', { textContent: `${provider.name} · ${prompt.title} · run ${item.repetition}` }),
      el('p', {
        textContent: `${(item.latency_ms / 1000).toFixed(2)} s · ${item.model || provider.model}${finishSuffix}`,
      }),
    );
    if (item.error) {
      body.append(el('p', { className: 'error-text', textContent: item.error }));
    }

    const noteInput = el('textarea', {
      className: 'generation-note',
      placeholder: 'Add text review note…',
      value: item.note ?? '',
      maxLength: 2000,
      ariaLabel: 'Review note',
    });
    noteInput.addEventListener('change', (): void => {
      void (async (): Promise<void> => {
        noteInput.disabled = true;
        try {
          await saveGenerationNote(item, noteInput.value);
        } catch (error) {
          window.alert(errorMessage(error));
        } finally {
          noteInput.disabled = false;
        }
      })();
    });
    body.append(noteInput);
    card.append(body);
    return card;
  };

  const renderGallery = (
    run: TextRunPayload,
    byId: Record<string, TextPromptSpec> = getPromptMap(run),
    specs: Record<string, TextProviderSpec> = getProviderMap(),
  ): void => {
    galleryEl.classList.remove('hidden');
    const providerIds = [...new Set(run.items.map((item: TextRunItem): string => item.provider_id))];
    const existingOptions = Array.from(
      galleryProviderSelect.options,
      (opt: HTMLOptionElement): string => opt.value,
    ).join('|');
    const expectedOptions = ['all', ...providerIds].join('|');
    if (existingOptions !== expectedOptions) {
      galleryProviderSelect.innerHTML = '';
      galleryProviderSelect.append(el('option', { value: 'all', textContent: 'All providers' }));
      for (const id of providerIds) {
        galleryProviderSelect.append(
          el('option', { value: id, textContent: specs[id]?.name || id }),
        );
      }
    }
    galleryProviderSelect.value = galleryProvider;

    const items = run.items
      .filter(
        (item: TextRunItem): boolean =>
          galleryProvider === 'all' || item.provider_id === galleryProvider,
      )
      .sort(
        (a: TextRunItem, b: TextRunItem): number =>
          a.provider_id.localeCompare(b.provider_id) ||
          a.prompt_id.localeCompare(b.prompt_id) ||
          a.repetition - b.repetition,
      );

    const pageSize = 12;
    const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
    galleryPage = Math.min(galleryPage, totalPages - 1);
    const pageItems = items.slice(galleryPage * pageSize, (galleryPage + 1) * pageSize);

    galleryResultsEl.innerHTML = '';
    for (const item of pageItems) {
      galleryResultsEl.append(makeResultCard(item, byId, specs));
    }
    galleryPageEl.textContent = `Page ${galleryPage + 1} of ${totalPages} · ${items.length} response${items.length === 1 ? '' : 's'}`;
    previousPageBtn.disabled = galleryPage === 0;
    nextPageBtn.disabled = galleryPage >= totalPages - 1;
  };

  const renderPromptGallery = (
    run: TextRunPayload,
    byId: Record<string, TextPromptSpec> = getPromptMap(run),
    specs: Record<string, TextProviderSpec> = getProviderMap(),
  ): void => {
    if (galleryFilterLabel.firstChild !== null) {
      galleryFilterLabel.firstChild.textContent = 'Prompt ';
    }
    galleryEl.classList.remove('hidden');

    const promptIds = [...new Set(run.items.map((item: TextRunItem): string => item.prompt_id))];
    const existingOptions = Array.from(
      galleryProviderSelect.options,
      (opt: HTMLOptionElement): string => opt.value,
    ).join('|');
    const expectedOptions = ['all', ...promptIds].join('|');
    if (existingOptions !== expectedOptions) {
      galleryProviderSelect.innerHTML = '';
      galleryProviderSelect.append(el('option', { value: 'all', textContent: 'All prompts' }));
      for (const id of promptIds) {
        galleryProviderSelect.append(
          el('option', { value: id, textContent: byId[id]?.title || id }),
        );
      }
    }
    galleryProviderSelect.value = galleryPrompt;

    const prompt: Pick<TextPromptSpec, 'title' | 'dimension' | 'prompt'> | null =
      byId[galleryPrompt] ??
      (galleryPrompt === 'all'
        ? null
        : { title: 'Custom Text Prompt', dimension: 'Manual Prompt', prompt: '' });
    if (prompt) {
      galleryPromptDetailEl.innerHTML = '';
      galleryPromptDetailEl.append(
        el('strong', { textContent: prompt.title }),
        el('small', { textContent: prompt.dimension }),
        el('small', { textContent: prompt.prompt }),
      );
      galleryPromptDetailEl.classList.remove('hidden');
    } else {
      galleryPromptDetailEl.classList.add('hidden');
    }

    const items = run.items
      .filter(
        (item: TextRunItem): boolean =>
          galleryPrompt === 'all' || item.prompt_id === galleryPrompt,
      )
      .sort(
        (a: TextRunItem, b: TextRunItem): number =>
          a.prompt_id.localeCompare(b.prompt_id) ||
          a.provider_id.localeCompare(b.provider_id) ||
          a.repetition - b.repetition,
      );

    const pageSize = 12;
    const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
    galleryPage = Math.min(galleryPage, totalPages - 1);

    galleryResultsEl.innerHTML = '';
    for (const item of items.slice(galleryPage * pageSize, (galleryPage + 1) * pageSize)) {
      galleryResultsEl.append(makeResultCard(item, byId, specs));
    }
    galleryPageEl.textContent = `Page ${galleryPage + 1} of ${totalPages} · ${items.length} response${items.length === 1 ? '' : 's'}`;
    previousPageBtn.disabled = galleryPage === 0;
    nextPageBtn.disabled = galleryPage >= totalPages - 1;
  };

  const renderRun = (run: TextRunPayload): void => {
    const byId = getPromptMap(run);
    const specs = getProviderMap();
    for (const id of Object.keys(specs)) {
      const statsEl = document.querySelector(`[data-provider-stats="${id}"]`);
      const items = run.items.filter((item: TextRunItem): boolean => item.provider_id === id);
      if (!(statsEl instanceof HTMLElement)) {
        continue;
      }
      if (items.length === 0) {
        statsEl.classList.add('hidden');
        continue;
      }
      const done = items.filter((item: TextRunItem): boolean => item.status === 'completed');
      const times = done.map((item: TextRunItem): number => item.latency_ms);
      const mean =
        times.length > 0
          ? `${(times.reduce((a: number, b: number): number => a + b, 0) / times.length / 1000).toFixed(2)} s`
          : '—';
      const p50Value = percentile(times, 0.5);
      const p95Value = percentile(times, 0.95);
      const p50 = p50Value !== null ? `${(p50Value / 1000).toFixed(2)} s` : '—';
      const p95 = p95Value !== null ? `${(p95Value / 1000).toFixed(2)} s` : '—';

      statsEl.innerHTML = '';
      statsEl.append(
        formatStat('Done', `${done.length}/${items.length}`),
        formatStat('Failed', `${items.length - done.length}`),
        formatStat('Mean', mean),
        formatStat('p50', p50),
        formatStat('p95', p95),
      );
      statsEl.classList.remove('hidden');
    }

    if (galleryView === 'prompt') {
      renderPromptGallery(run, byId, specs);
    } else {
      renderGallery(run, byId, specs);
    }
  };

  const renderSelectedGallery = (): void => {
    if (!currentRun) {
      return;
    }
    if (galleryView === 'prompt') {
      renderPromptGallery(currentRun);
    } else {
      if (galleryFilterLabel.firstChild !== null) {
        galleryFilterLabel.firstChild.textContent = 'Provider ';
      }
      galleryPromptDetailEl.classList.add('hidden');
      renderGallery(currentRun);
    }
  };

  const onGalleryViewChange = (): void => {
    galleryView = galleryViewSelect.value === 'prompt' ? 'prompt' : 'provider';
    if (galleryView === 'prompt' && galleryPrompt === 'all' && currentRun) {
      galleryPrompt = currentRun.items[0]?.prompt_id || 'all';
    }
    galleryPage = 0;
    renderSelectedGallery();
  };

  const onGalleryProviderChange = (): void => {
    if (galleryView === 'prompt') {
      galleryPrompt = galleryProviderSelect.value;
    } else {
      galleryProvider = galleryProviderSelect.value;
    }
    galleryPage = 0;
    renderSelectedGallery();
  };

  const onPreviousPage = (): void => {
    galleryPage -= 1;
    renderSelectedGallery();
  };

  const onNextPage = (): void => {
    galleryPage += 1;
    renderSelectedGallery();
  };

  const onSelectAll = (): void => {
    promptsEl.querySelectorAll<HTMLInputElement>('input').forEach((box: HTMLInputElement): void => {
      box.checked = true;
    });
  };

  const onDeselectAll = (): void => {
    promptsEl.querySelectorAll<HTMLInputElement>('input').forEach((box: HTMLInputElement): void => {
      box.checked = false;
    });
  };

  const onCustomPromptInput = (): void => {
    if (customPromptInput.value.trim()) {
      useCustomPromptCheckbox.checked = true;
    }
  };

  const stopPolling = (): void => {
    if (pollInterval !== null) {
      window.clearInterval(pollInterval);
      pollInterval = null;
    }
  };

  const onRunClick = async (): Promise<void> => {
    const providerIds = selectedIds('providers');
    const promptIds = selectedIds('prompts');
    const repetitions = Number(repetitionsInput.value);
    const options = collectProviderOptions();
    const useCustom = useCustomPromptCheckbox.checked;
    const customText = customPromptInput.value.trim();
    const customPrompts: TextCustomPromptInput[] = [];
    if (useCustom && customText) {
      customPrompts.push({
        prompt: customText,
        title: 'Custom Text Prompt',
        dimension: 'Manual Prompt',
        system_instruction: customSystemInstructionInput.value.trim() || null,
        temperature: Number(customTemperatureInput.value || 0.7),
        max_output_tokens: Number(customMaxTokensInput.value || 1000),
      });
    }
    if (!providerIds.length || (!promptIds.length && !customPrompts.length)) {
      window.alert('Select at least one provider and one prompt or enter a custom prompt.');
      return;
    }

    runBtn.disabled = true;
    statusEl.textContent = 'Starting benchmark run…';
    try {
      const response = await fetch('/api/text-benchmark/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider_ids: providerIds,
          prompt_ids: promptIds,
          custom_prompts: customPrompts,
          repetitions,
          provider_options: options,
        }),
      });
      const payload = (await response.json()) as TextRunPayload;
      if (!response.ok) {
        throw new Error(payload.detail || 'Failed to start run.');
      }
      currentRun = payload;
      galleryPage = 0;
      stopPolling();
      pollInterval = window.setInterval((): void => {
        void (async (): Promise<void> => {
          if (destroyed || !currentRun) {
            return;
          }
          const statusRes = await fetch(`/api/text-benchmark/runs/${currentRun.id}`);
          if (!statusRes.ok || destroyed) {
            return;
          }
          currentRun = (await statusRes.json()) as TextRunPayload;
          renderRun(currentRun);
          statusEl.textContent = `Run ${currentRun.status}: ${currentRun.items.length}/${currentRun.total} responses generated`;
          if (currentRun.status === 'completed') {
            stopPolling();
            runBtn.disabled = false;
          }
        })();
      }, 1000);
    } catch (error) {
      window.alert(errorMessage(error));
      runBtn.disabled = false;
      statusEl.textContent = 'Run failed to start.';
    }
  };

  const handleRunClick = (): void => {
    void onRunClick();
  };

  galleryViewSelect.addEventListener('change', onGalleryViewChange);
  galleryProviderSelect.addEventListener('change', onGalleryProviderChange);
  previousPageBtn.addEventListener('click', onPreviousPage);
  nextPageBtn.addEventListener('click', onNextPage);
  selectAllBtn.addEventListener('click', onSelectAll);
  deselectAllBtn.addEventListener('click', onDeselectAll);
  customPromptInput.addEventListener('input', onCustomPromptInput);
  runBtn.addEventListener('click', handleRunClick);

  const loadCatalog = async (): Promise<void> => {
    const response = await fetch('/api/text-benchmark/catalog');
    const loaded = (await response.json()) as TextCatalogResponse;
    if (destroyed) {
      return;
    }
    catalog = loaded;
    renderCatalog(loaded);
  };

  void loadCatalog();

  return {
    loadCatalog,
    destroy(): void {
      destroyed = true;
      stopPolling();
      galleryViewSelect.removeEventListener('change', onGalleryViewChange);
      galleryProviderSelect.removeEventListener('change', onGalleryProviderChange);
      previousPageBtn.removeEventListener('click', onPreviousPage);
      nextPageBtn.removeEventListener('click', onNextPage);
      selectAllBtn.removeEventListener('click', onSelectAll);
      deselectAllBtn.removeEventListener('click', onDeselectAll);
      customPromptInput.removeEventListener('input', onCustomPromptInput);
      runBtn.removeEventListener('click', handleRunClick);
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', (): void => {
    initTextResponseBenchmark();
  });
} else {
  initTextResponseBenchmark();
}
