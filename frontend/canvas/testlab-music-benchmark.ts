/** Music Provider Bench page controller: catalog, adapter options, SSE progress, polling, gallery, and notes. */

export interface MusicProviderSpec {
  readonly id: string;
  readonly name: string;
  readonly model: string;
  readonly status: string;
  readonly notes: string;
  readonly estimated_cost_usd_per_generation?: number | null;
  readonly estimated_cost_usd_30s?: number | null;
  readonly model_options?: readonly string[];
}

export interface MusicAdapterSpec {
  readonly id: string;
  readonly name?: string;
  readonly status: string;
  readonly estimated_cost_usd_per_generation?: number | null;
}

export interface MusicPromptSpec {
  readonly id: string;
  readonly title: string;
  readonly dimension: string;
  readonly prompt: string;
  readonly duration_seconds: number;
  readonly genre?: string | null;
  readonly tempo?: string | null;
}

export interface MusicCatalogResponse {
  readonly providers: readonly MusicProviderSpec[];
  readonly adapters: readonly MusicAdapterSpec[];
  readonly prompts: readonly MusicPromptSpec[];
}

export interface MusicCustomPromptInput {
  readonly prompt: string;
  readonly title: string;
  readonly dimension: string;
  readonly duration_seconds: number;
  readonly genre: string | null;
  readonly tempo: string | null;
}

export interface MusicProviderOptionValues {
  model?: string;
  base_provider?: string;
  adapter?: string;
  adapter_options?: Record<string, number>;
  [key: string]: string | Record<string, number> | undefined;
}

export interface MusicRunItem {
  readonly id: string;
  readonly provider_id: string;
  readonly prompt_id: string;
  readonly repetition: number;
  readonly status: string;
  readonly latency_ms: number;
  readonly audio_url?: string;
  readonly base_audio_url?: string | null;
  readonly base_model?: string | null;
  readonly base_latency_ms?: number | null;
  readonly adapter_latency_ms?: number | null;
  readonly model?: string;
  readonly estimated_output_cost_usd?: number | null;
  readonly error?: string;
  note?: string;
}

export interface MusicRunPayload {
  readonly id: string;
  readonly status: string;
  readonly total: number;
  readonly prompts?: readonly MusicPromptSpec[];
  readonly items: readonly MusicRunItem[];
  readonly detail?: string;
}

export interface MusicProgressEvent {
  readonly stage: string;
  readonly prompt_id: string;
  readonly repetition: number;
  readonly details?: {
    readonly latency_ms?: number;
  };
}

export interface MusicNoteResponse {
  readonly id?: string;
  readonly note?: string;
  readonly detail?: string;
}

export interface MusicBenchmarkController {
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

function formatMoney(provider: MusicProviderSpec): string {
  if (provider.estimated_cost_usd_per_generation != null) {
    return `~$${Number(provider.estimated_cost_usd_per_generation).toFixed(2)} / gen`;
  }
  if (provider.estimated_cost_usd_30s != null) {
    return `~$${Number(provider.estimated_cost_usd_30s).toFixed(3)} / 30s`;
  }
  return 'Estimate pending';
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

function runCost(done: readonly MusicRunItem[]): string {
  const costs = done
    .map((item: MusicRunItem): number | null | undefined => item.estimated_output_cost_usd)
    .filter((cost: number | null | undefined): cost is number => cost != null);
  return costs.length > 0
    ? `$${costs.reduce((sum: number, cost: number): number => sum + cost, 0).toFixed(3)}`
    : '—';
}

function formatStat(label: string, value: string): HTMLSpanElement {
  return el('span', {}, [`${label} `, el('strong', { textContent: value })]);
}

function progressText(event: MusicProgressEvent): string {
  const names: Record<string, string> = {
    submitted: 'Queued',
    base_generating: 'Generating base track',
    base_completed: 'Base track ready',
    adapter_generating: 'Adapting track',
    adapter_completed: 'Adapted track ready',
    completed: 'Completed',
    failed: 'Failed',
  };
  const latency =
    event.details?.latency_ms != null
      ? ` (${(event.details.latency_ms / 1000).toFixed(2)} s)`
      : '';
  return `${names[event.stage] || event.stage}: ${event.prompt_id}, run ${event.repetition}${latency}`;
}

function makeOptionSelect(
  values: readonly string[],
  current: string | undefined,
  providerId: string,
  optionKey: string,
): HTMLSelectElement {
  const select = el('select');
  select.setAttribute('data-provider-option', optionKey);
  select.setAttribute('data-provider-id', providerId);
  for (const value of values) {
    select.append(
      el('option', { value, textContent: value, selected: value === current }),
    );
  }
  select.addEventListener('click', (event: MouseEvent): void => {
    event.stopPropagation();
  });
  return select;
}

/** Initializes the Music Provider Bench DOM controls, SSE progress, polling, and gallery. */
export function initMusicBenchmark(): MusicBenchmarkController | null {
  const providersEl = document.getElementById('providers');
  const promptsEl = document.getElementById('prompts');
  const selectAllBtn = document.getElementById('selectAll');
  const deselectAllBtn = document.getElementById('deselectAll');
  const repetitionsInput = document.getElementById('repetitions');
  const useCustomPromptCheckbox = document.getElementById('useCustomPrompt');
  const customPromptInput = document.getElementById('customPromptText');
  const customDurationInput = document.getElementById('customDuration');
  const customGenreInput = document.getElementById('customGenre');
  const customTempoInput = document.getElementById('customTempo');
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
    !(customDurationInput instanceof HTMLInputElement) ||
    !(customGenreInput instanceof HTMLInputElement) ||
    !(customTempoInput instanceof HTMLInputElement) ||
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

  let catalog: MusicCatalogResponse | null = null;
  let currentRun: MusicRunPayload | null = null;
  let pollTimer: number | null = null;
  let stream: EventSource | null = null;
  let galleryPage = 0;
  let galleryProvider = 'all';
  let galleryPrompt = 'all';
  let galleryView: GalleryViewMode = 'provider';
  let destroyed = false;

  const getPromptMap = (run: MusicRunPayload | null): Record<string, MusicPromptSpec> => {
    const map: Record<string, MusicPromptSpec> = {};
    for (const prompt of catalog?.prompts ?? []) {
      map[prompt.id] = prompt;
    }
    for (const prompt of run?.prompts ?? []) {
      map[prompt.id] = prompt;
    }
    return map;
  };

  const getProviderMap = (): Record<string, MusicProviderSpec> => {
    const map: Record<string, MusicProviderSpec> = {};
    for (const provider of catalog?.providers ?? []) {
      map[provider.id] = provider;
    }
    return map;
  };

  const renderCatalog = (loadedCatalog: MusicCatalogResponse): void => {
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
        el('span', { className: 'pill', textContent: formatMoney(provider) }),
        el('span', { className: 'pill', textContent: provider.status }),
        el('small', { textContent: provider.notes }),
      ];

      if (provider.model_options && provider.model_options.length > 0) {
        children.push(
          el('small', { textContent: 'Model' }),
          makeOptionSelect(provider.model_options, provider.model, provider.id, 'model'),
        );
      }

      if (provider.id === 'test-base-plus-adapter') {
        const bases = loadedCatalog.providers.filter(
          (base: MusicProviderSpec): boolean =>
            base.id !== 'test-base-plus-adapter' && base.status === 'available',
        );
        const adapters = loadedCatalog.adapters.filter(
          (adapter: MusicAdapterSpec): boolean => adapter.status === 'available',
        );
        const noiseInput = el('input', {
          type: 'number',
          min: '0',
          max: '1',
          step: '0.05',
          value: '0.35',
        });
        noiseInput.setAttribute('data-provider-id', provider.id);
        noiseInput.setAttribute('data-adapter-option', 'init_noise_level');
        noiseInput.addEventListener('click', (event: MouseEvent): void => {
          event.stopPropagation();
        });

        children.push(
          el('small', { textContent: 'Base provider' }),
          makeOptionSelect(
            bases.map((base: MusicProviderSpec): string => base.id),
            bases[0]?.id,
            provider.id,
            'base_provider',
          ),
          el('small', { textContent: 'Audio adapter' }),
          makeOptionSelect(
            adapters.map((adapter: MusicAdapterSpec): string => adapter.id),
            adapters[0]?.id,
            provider.id,
            'adapter',
          ),
          el('small', { textContent: 'Source preservation (noise 0–1)' }),
          noiseInput,
        );
      }

      children.push(stats);
      providersEl.append(el('label', { className: 'choice' }, children));
    }

    loadedCatalog.prompts.forEach((prompt: MusicPromptSpec, index: number): void => {
      const input = el('input', {
        type: 'checkbox',
        value: prompt.id,
        checked: index === 0,
      });
      promptsEl.append(
        el('label', { className: 'choice' }, [
          input,
          el('strong', { textContent: `${index + 1}. ${prompt.title}` }),
          el('small', {
            textContent: `${prompt.dimension} · ${prompt.genre || ''} · ${prompt.tempo || ''}`,
          }),
          el('small', { textContent: prompt.prompt }),
          el('span', { className: 'pill', textContent: `${prompt.duration_seconds}s` }),
        ]),
      );
    });
  };

  const selectedIds = (containerId: string): string[] => {
    const checked = document.querySelectorAll<HTMLInputElement>(`#${containerId} input:checked`);
    return Array.from(checked, (node: HTMLInputElement): string => node.value);
  };

  const collectProviderOptions = (): Record<string, MusicProviderOptionValues> => {
    const options: Record<string, MusicProviderOptionValues> = {};
    document
      .querySelectorAll<HTMLSelectElement | HTMLInputElement>('[data-provider-option]')
      .forEach((input: HTMLSelectElement | HTMLInputElement): void => {
        const id = input.dataset.providerId;
        const optionKey = input.dataset.providerOption;
        if (!id || !optionKey) {
          return;
        }
        options[id] ??= {};
        options[id][optionKey] = input.value;
      });
    document
      .querySelectorAll<HTMLInputElement>('[data-adapter-option]')
      .forEach((input: HTMLInputElement): void => {
        const id = input.dataset.providerId;
        const adapterKey = input.dataset.adapterOption;
        if (!id || !adapterKey) {
          return;
        }
        options[id] ??= {};
        options[id].adapter_options ??= {};
        options[id].adapter_options[adapterKey] = Number(input.value);
      });
    return options;
  };

  const saveGenerationNote = async (item: MusicRunItem, noteText: string): Promise<void> => {
    if (!currentRun) {
      throw new Error('No active run.');
    }
    const response = await fetch(
      `/api/music-benchmark/runs/${currentRun.id}/items/${item.id}/note`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note: noteText }),
      },
    );
    const data = (await response.json()) as MusicNoteResponse;
    if (!response.ok) {
      throw new Error(data.detail || 'Could not save note.');
    }
    item.note = data.note ?? noteText;
  };

  const makeResultCard = (
    item: MusicRunItem,
    byId: Record<string, MusicPromptSpec> = getPromptMap(currentRun),
    specs: Record<string, MusicProviderSpec> = getProviderMap(),
  ): HTMLElement => {
    const prompt: Pick<MusicPromptSpec, 'title' | 'dimension' | 'prompt'> =
      byId[item.prompt_id] ?? {
        title: 'Custom Music',
        dimension: 'Manual Music',
        prompt: '',
      };
    const provider: Pick<MusicProviderSpec, 'name' | 'model'> = specs[item.provider_id] ?? {
      name: item.provider_id,
      model: '',
    };

    const card = el('article', {
      className: `result ${item.status === 'failed' ? 'error' : ''}`,
    });
    if (item.base_audio_url) {
      const baseWrapper = el('div', { className: 'audio-wrapper' });
      baseWrapper.append(
        el('small', {
          textContent: `Base track${item.base_model ? ` · ${item.base_model}` : ''}`,
        }),
        el('audio', { controls: true, src: item.base_audio_url }),
      );
      card.append(baseWrapper);
    }
    if (item.audio_url) {
      const wrapper = el('div', { className: 'audio-wrapper' });
      if (item.base_audio_url) {
        wrapper.append(el('small', { textContent: 'Adapted track' }));
      }
      wrapper.append(el('audio', { controls: true, src: item.audio_url }));
      card.append(wrapper);
    }

    const body = el('div', { className: 'result-body' });
    body.append(
      el('h3', { textContent: `${provider.name} · ${prompt.title} · run ${item.repetition}` }),
      el('p', {
        textContent: `${(item.latency_ms / 1000).toFixed(2)} s · ${item.model || provider.model}`,
      }),
    );
    if (item.base_latency_ms != null) {
      const adapterSuffix =
        item.adapter_latency_ms != null
          ? ` · Adapter latency: ${(item.adapter_latency_ms / 1000).toFixed(2)} s`
          : '';
      body.append(
        el('p', {
          textContent: `Base latency: ${(item.base_latency_ms / 1000).toFixed(2)} s${adapterSuffix}`,
        }),
      );
    }
    if (item.error) {
      body.append(el('p', { className: 'error-text', textContent: item.error }));
    }

    const noteInput = el('textarea', {
      className: 'generation-note',
      placeholder: 'Add music review note…',
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
    run: MusicRunPayload,
    byId: Record<string, MusicPromptSpec> = getPromptMap(run),
    specs: Record<string, MusicProviderSpec> = getProviderMap(),
  ): void => {
    galleryEl.classList.remove('hidden');
    const providerIds = [...new Set(run.items.map((item: MusicRunItem): string => item.provider_id))];
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
        (item: MusicRunItem): boolean =>
          galleryProvider === 'all' || item.provider_id === galleryProvider,
      )
      .sort(
        (a: MusicRunItem, b: MusicRunItem): number =>
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
    galleryPageEl.textContent = `Page ${galleryPage + 1} of ${totalPages} · ${items.length} track${items.length === 1 ? '' : 's'}`;
    previousPageBtn.disabled = galleryPage === 0;
    nextPageBtn.disabled = galleryPage >= totalPages - 1;
  };

  const renderPromptGallery = (
    run: MusicRunPayload,
    byId: Record<string, MusicPromptSpec> = getPromptMap(run),
    specs: Record<string, MusicProviderSpec> = getProviderMap(),
  ): void => {
    if (galleryFilterLabel.firstChild !== null) {
      galleryFilterLabel.firstChild.textContent = 'Prompt ';
    }
    galleryEl.classList.remove('hidden');

    const promptIds = [...new Set(run.items.map((item: MusicRunItem): string => item.prompt_id))];
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

    const prompt: Pick<MusicPromptSpec, 'title' | 'dimension' | 'prompt'> & {
      readonly duration_seconds?: number;
    } | null =
      byId[galleryPrompt] ??
      (galleryPrompt === 'all'
        ? null
        : { title: 'Custom Music', dimension: 'Manual Music', prompt: '' });
    if (prompt) {
      galleryPromptDetailEl.innerHTML = '';
      galleryPromptDetailEl.append(
        el('strong', { textContent: prompt.title }),
        el('small', { textContent: `${prompt.dimension} · ${prompt.duration_seconds || 30}s` }),
        el('small', { textContent: prompt.prompt }),
      );
      galleryPromptDetailEl.classList.remove('hidden');
    } else {
      galleryPromptDetailEl.classList.add('hidden');
    }

    const items = run.items
      .filter(
        (item: MusicRunItem): boolean =>
          galleryPrompt === 'all' || item.prompt_id === galleryPrompt,
      )
      .sort(
        (a: MusicRunItem, b: MusicRunItem): number =>
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
    galleryPageEl.textContent = `Page ${galleryPage + 1} of ${totalPages} - ${items.length} track${items.length === 1 ? '' : 's'}`;
    previousPageBtn.disabled = galleryPage === 0;
    nextPageBtn.disabled = galleryPage >= totalPages - 1;
  };

  const renderRun = (run: MusicRunPayload): void => {
    const byId = getPromptMap(run);
    const specs = getProviderMap();
    for (const id of Object.keys(specs)) {
      const statsEl = document.querySelector(`[data-provider-stats="${id}"]`);
      const items = run.items.filter((item: MusicRunItem): boolean => item.provider_id === id);
      if (!(statsEl instanceof HTMLElement)) {
        continue;
      }
      if (items.length === 0) {
        statsEl.classList.add('hidden');
        continue;
      }
      const done = items.filter((item: MusicRunItem): boolean => item.status === 'completed');
      const times = done.map((item: MusicRunItem): number => item.latency_ms);
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
      if (done.length > 0) {
        statsEl.append(formatStat('Est. output cost', runCost(done)));
      }
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

  const streamProgress = (runId: string): void => {
    stream?.close();
    stream = new EventSource(`/api/music-benchmark/runs/${runId}/events`);
    stream.onmessage = (event: MessageEvent<string>): void => {
      const progress = JSON.parse(event.data) as MusicProgressEvent;
      statusEl.textContent = progressText(progress);
    };
    stream.addEventListener('complete', (): void => {
      stream?.close();
    });
    stream.onerror = (): void => {
      stream?.close();
    };
  };

  const pollRun = async (runId: string): Promise<void> => {
    if (destroyed) {
      return;
    }
    const response = await fetch(`/api/music-benchmark/runs/${runId}`);
    const run = (await response.json()) as MusicRunPayload;
    if (destroyed) {
      return;
    }
    currentRun = run;
    renderRun(run);
    if (run.status === 'completed') {
      statusEl.textContent = `Completed ${run.total} provider/prompt music comparisons.`;
      stream?.close();
      runBtn.disabled = false;
    } else if (!stream) {
      statusEl.textContent = `Running ${run.items.length} of ${run.total} music comparisons…`;
    }
    if (run.status !== 'completed') {
      pollTimer = window.setTimeout((): void => {
        void pollRun(runId);
      }, 250);
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
    promptsEl.querySelectorAll<HTMLInputElement>('input').forEach((node: HTMLInputElement): void => {
      node.checked = true;
    });
  };

  const onDeselectAll = (): void => {
    promptsEl.querySelectorAll<HTMLInputElement>('input').forEach((node: HTMLInputElement): void => {
      node.checked = false;
    });
  };

  const onCustomPromptInput = (): void => {
    if (customPromptInput.value.trim()) {
      useCustomPromptCheckbox.checked = true;
    }
  };

  const onRunClick = async (): Promise<void> => {
    const providerIds = selectedIds('providers');
    const promptIds = selectedIds('prompts');
    const repetitions = Number(repetitionsInput.value);
    const useCustom = useCustomPromptCheckbox.checked;
    const customText = customPromptInput.value.trim();
    const customPrompts: MusicCustomPromptInput[] = [];
    if (useCustom && customText) {
      customPrompts.push({
        prompt: customText,
        title: 'Custom Music',
        dimension: 'Manual Music',
        duration_seconds: Number(customDurationInput.value || 30),
        genre: customGenreInput.value.trim() || null,
        tempo: customTempoInput.value.trim() || null,
      });
    }
    if (!providerIds.length || (!promptIds.length && !customPrompts.length)) {
      statusEl.textContent =
        'Select an available music provider and at least one prompt or enter a custom prompt.';
      return;
    }
    runBtn.disabled = true;
    galleryPage = 0;
    galleryProvider = 'all';
    galleryEl.classList.add('hidden');
    const response = await fetch('/api/music-benchmark/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        provider_ids: providerIds,
        prompt_ids: promptIds,
        custom_prompts: customPrompts,
        repetitions,
        provider_options: collectProviderOptions(),
      }),
    });
    const payload = (await response.json()) as MusicRunPayload;
    if (!response.ok) {
      statusEl.textContent = payload.detail || 'Could not start music benchmark.';
      runBtn.disabled = false;
      return;
    }
    streamProgress(payload.id);
    void pollRun(payload.id);
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
    try {
      const response = await fetch('/api/music-benchmark/catalog');
      const loaded = (await response.json()) as MusicCatalogResponse;
      if (destroyed) {
        return;
      }
      catalog = loaded;
      renderCatalog(loaded);
    } catch {
      if (!destroyed) {
        statusEl.textContent = 'Could not load music benchmark catalog.';
      }
    }
  };

  void loadCatalog();

  return {
    loadCatalog,
    destroy(): void {
      destroyed = true;
      if (pollTimer !== null) {
        window.clearTimeout(pollTimer);
        pollTimer = null;
      }
      stream?.close();
      stream = null;
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
    initMusicBenchmark();
  });
} else {
  initMusicBenchmark();
}
