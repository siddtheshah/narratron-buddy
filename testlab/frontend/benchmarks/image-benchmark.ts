/** Image Provider Bench page controller: catalog, routing badges, gallery slicing, lightbox, polling, and notes. */

export interface ImageProviderSpec {
  readonly id: string;
  readonly name: string;
  readonly model: string;
  readonly status: string;
  readonly notes: string;
  readonly estimated_cost_usd_1mp?: number | null;
  readonly model_options?: readonly string[];
  readonly classifier_model_options?: readonly string[];
  readonly quality_options?: readonly string[];
}

export interface ImagePromptSpec {
  readonly id: string;
  readonly title: string;
  readonly dimension: string;
  readonly prompt: string;
  readonly reference_count?: number;
}

export interface ImageCatalogResponse {
  readonly providers: readonly ImageProviderSpec[];
  readonly prompts: readonly ImagePromptSpec[];
}

export interface ImageCustomPromptInput {
  readonly prompt: string;
  readonly title: string;
  readonly dimension: string;
}

export interface ImageRoutingMetadata {
  readonly route_to_fallback?: boolean;
  readonly selected_provider: string;
  readonly reasons?: readonly string[];
  readonly ambiguous_terms?: readonly string[];
}

export interface ImageItemUsage {
  readonly routing?: ImageRoutingMetadata;
}

export interface ImageRunItem {
  readonly id: string;
  readonly provider_id: string;
  readonly prompt_id: string;
  readonly repetition: number;
  readonly status: string;
  readonly latency_ms: number;
  readonly image_url?: string;
  readonly model?: string;
  readonly estimated_output_cost_usd?: number | null;
  readonly usage?: ImageItemUsage;
  readonly error?: string;
  note?: string;
}

export interface ImageRunPayload {
  readonly id: string;
  readonly status: string;
  readonly total: number;
  readonly prompts?: readonly ImagePromptSpec[];
  readonly items: readonly ImageRunItem[];
  readonly detail?: string;
}

export interface ImageNoteResponse {
  readonly id?: string;
  readonly note?: string;
  readonly detail?: string;
}

export interface ImageBenchmarkController {
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

function formatMoney(value: number | null | undefined): string {
  return value == null ? 'Estimate pending' : `~$${Number(value).toFixed(3)} / 1 MP`;
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

function runCost(done: readonly ImageRunItem[]): string {
  const costs = done
    .map((item: ImageRunItem): number | null | undefined => item.estimated_output_cost_usd)
    .filter((cost: number | null | undefined): cost is number => cost != null);
  return costs.length > 0
    ? `$${costs.reduce((sum: number, cost: number): number => sum + cost, 0).toFixed(3)}`
    : '—';
}

function formatStat(label: string, value: string): HTMLSpanElement {
  return el('span', {}, [`${label} `, el('strong', { textContent: value })]);
}

function makeOptionSelect(
  values: readonly string[],
  selectedValue: string,
  providerId: string,
  optionKey: string,
): HTMLSelectElement {
  const select = el('select');
  select.setAttribute('data-provider-option', optionKey);
  select.setAttribute('data-provider-id', providerId);
  for (const name of values) {
    select.append(
      el('option', { value: name, textContent: name, selected: name === selectedValue }),
    );
  }
  select.addEventListener('click', (event: MouseEvent): void => {
    event.stopPropagation();
  });
  return select;
}

/** Initializes the Image Provider Bench DOM controls, lightbox, polling, and gallery. */
export function initImageBenchmark(): ImageBenchmarkController | null {
  const providersEl = document.getElementById('providers');
  const promptsEl = document.getElementById('prompts');
  const selectAllBtn = document.getElementById('selectAll');
  const deselectAllBtn = document.getElementById('deselectAll');
  const repetitionsInput = document.getElementById('repetitions');
  const refCountStatusEl = document.getElementById('referenceCountStatus');
  const useCustomPromptCheckbox = document.getElementById('useCustomPrompt');
  const customPromptInput = document.getElementById('customPromptText');
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
  const lightboxEl = document.getElementById('lightbox');
  const closeLightboxBtn = document.getElementById('closeLightbox');
  const lightboxImageEl = document.getElementById('lightboxImage');

  if (
    !(providersEl instanceof HTMLElement) ||
    !(promptsEl instanceof HTMLElement) ||
    !(selectAllBtn instanceof HTMLButtonElement) ||
    !(deselectAllBtn instanceof HTMLButtonElement) ||
    !(repetitionsInput instanceof HTMLInputElement) ||
    !(refCountStatusEl instanceof HTMLElement) ||
    !(useCustomPromptCheckbox instanceof HTMLInputElement) ||
    !(customPromptInput instanceof HTMLTextAreaElement) ||
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
    !(galleryResultsEl instanceof HTMLElement) ||
    !(lightboxEl instanceof HTMLElement) ||
    !(closeLightboxBtn instanceof HTMLButtonElement) ||
    !(lightboxImageEl instanceof HTMLImageElement)
  ) {
    return null;
  }

  let catalog: ImageCatalogResponse | null = null;
  let currentRun: ImageRunPayload | null = null;
  let pollTimer: number | null = null;
  let galleryPage = 0;
  let galleryProvider = 'all';
  let galleryPrompt = 'all';
  let galleryView: GalleryViewMode = 'provider';
  let destroyed = false;

  const getPromptMap = (run: ImageRunPayload | null): Record<string, ImagePromptSpec> => {
    const map: Record<string, ImagePromptSpec> = {};
    for (const prompt of catalog?.prompts ?? []) {
      map[prompt.id] = prompt;
    }
    for (const prompt of run?.prompts ?? []) {
      map[prompt.id] = prompt;
    }
    return map;
  };

  const getProviderMap = (): Record<string, ImageProviderSpec> => {
    const map: Record<string, ImageProviderSpec> = {};
    for (const provider of catalog?.providers ?? []) {
      map[provider.id] = provider;
    }
    return map;
  };

  const openLightbox = (src: string, alt: string): void => {
    lightboxImageEl.src = src;
    lightboxImageEl.alt = alt;
    lightboxEl.classList.remove('hidden');
    lightboxEl.setAttribute('aria-hidden', 'false');
  };

  const closeLightbox = (): void => {
    lightboxEl.classList.add('hidden');
    lightboxEl.setAttribute('aria-hidden', 'true');
    lightboxImageEl.removeAttribute('src');
  };

  const renderCatalog = (loadedCatalog: ImageCatalogResponse): void => {
    providersEl.innerHTML = '';
    promptsEl.innerHTML = '';
    selectAllBtn.textContent = `Select all ${loadedCatalog.prompts.length}`;
    const refCount = loadedCatalog.prompts.filter(
      (prompt: ImagePromptSpec): boolean => Boolean(prompt.reference_count),
    ).length;
    refCountStatusEl.textContent = `Includes ${refCount} reference-aware prompts.`;

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
        el('span', { className: 'pill', textContent: formatMoney(provider.estimated_cost_usd_1mp) }),
        el('span', { className: 'pill', textContent: provider.status }),
        el('small', { textContent: provider.notes }),
      ];

      if (provider.model_options && provider.model_options.length > 0) {
        children.push(
          el('small', { textContent: 'Model' }),
          makeOptionSelect(provider.model_options, provider.model, provider.id, 'model'),
        );
      }
      if (provider.classifier_model_options && provider.classifier_model_options.length > 0) {
        children.push(
          el('small', { textContent: 'Classifier' }),
          makeOptionSelect(
            provider.classifier_model_options,
            'gemini-2.5-flash-lite',
            provider.id,
            'classifier_model',
          ),
        );
      }
      if (provider.quality_options && provider.quality_options.length > 0) {
        const defaultQuality = provider.id.includes('flare') ? 'high' : 'medium';
        children.push(
          el('small', { textContent: 'Quality' }),
          makeOptionSelect(provider.quality_options, defaultQuality, provider.id, 'quality'),
        );
      }

      children.push(stats);
      providersEl.append(el('label', { className: 'choice' }, children));
    }

    loadedCatalog.prompts.forEach((prompt: ImagePromptSpec, index: number): void => {
      const input = el('input', {
        type: 'checkbox',
        value: prompt.id,
        checked: index === 0,
      });
      const refBadge: ChildItem = prompt.reference_count
        ? el('span', {
            className: 'pill',
            textContent: `${prompt.reference_count} reference${prompt.reference_count > 1 ? 's' : ''}`,
          })
        : document.createTextNode('');
      promptsEl.append(
        el('label', { className: 'choice' }, [
          input,
          el('strong', { textContent: `${index + 1}. ${prompt.title}` }),
          el('small', { textContent: prompt.dimension }),
          el('small', { textContent: prompt.prompt }),
          refBadge,
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
    return options;
  };

  const saveGenerationNote = async (item: ImageRunItem, noteText: string): Promise<void> => {
    if (!currentRun) {
      throw new Error('No active run.');
    }
    const response = await fetch(
      `/api/image-benchmark/runs/${currentRun.id}/items/${item.id}/note`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note: noteText }),
      },
    );
    const data = (await response.json()) as ImageNoteResponse;
    if (!response.ok) {
      throw new Error(data.detail || 'Could not save note.');
    }
    item.note = data.note ?? noteText;
  };

  const makeResultCard = (
    item: ImageRunItem,
    byId: Record<string, ImagePromptSpec> = getPromptMap(currentRun),
    specs: Record<string, ImageProviderSpec> = getProviderMap(),
  ): HTMLElement => {
    const prompt: Pick<ImagePromptSpec, 'title' | 'dimension' | 'prompt'> =
      byId[item.prompt_id] ?? {
        title: 'Custom Prompt',
        dimension: 'Manual Prompt',
        prompt: '',
      };
    const provider: Pick<ImageProviderSpec, 'name' | 'model'> = specs[item.provider_id] ?? {
      name: item.provider_id,
      model: '',
    };

    const card = el('article', {
      className: `result ${item.status === 'failed' ? 'error' : ''}`,
    });
    if (item.image_url) {
      const image = el('img', {
        src: item.image_url,
        alt: `${provider.name}: ${prompt.title}`,
      });
      image.addEventListener('click', (): void => {
        openLightbox(image.src, image.alt);
      });
      card.append(image);
    }

    const body = el('div', { className: 'result-body' });
    body.append(
      el('h3', { textContent: `${provider.name} · ${prompt.title} · run ${item.repetition}` }),
      el('p', {
        textContent: `${(item.latency_ms / 1000).toFixed(2)} s · ${item.model || provider.model}`,
      }),
    );
    if (item.error) {
      body.append(el('p', { className: 'error-text', textContent: item.error }));
    }

    const routing = item.usage?.routing;
    if (routing) {
      const rerouted = Boolean(routing.route_to_fallback);
      body.append(
        el('span', {
          className: `pill routing-badge ${rerouted ? 'rerouted' : ''}`,
          textContent: rerouted
            ? `Rerouted → ${routing.selected_provider}`
            : `Stayed → ${routing.selected_provider}`,
        }),
      );
      if (routing.reasons && routing.reasons.length > 0) {
        const ambiguousSuffix =
          routing.ambiguous_terms && routing.ambiguous_terms.length > 0
            ? ` (${routing.ambiguous_terms.join(', ')})`
            : '';
        body.append(
          el('small', {
            textContent: `Reason: ${routing.reasons.join(', ')}${ambiguousSuffix}`,
          }),
        );
      }
    }

    const noteInput = el('textarea', {
      className: 'generation-note',
      placeholder: 'Add review note…',
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

  const computeLatencySummary = (
    items: readonly ImageRunItem[],
  ): {
    readonly done: readonly ImageRunItem[];
    readonly mean: string;
    readonly p50: string;
    readonly p95: string;
  } => {
    const done = items.filter((item: ImageRunItem): boolean => item.status === 'completed');
    const times = done.map((item: ImageRunItem): number => item.latency_ms);
    const mean =
      times.length > 0
        ? `${(times.reduce((a: number, b: number): number => a + b, 0) / times.length / 1000).toFixed(2)} s`
        : '—';
    const p50Value = percentile(times, 0.5);
    const p95Value = percentile(times, 0.95);
    return {
      done,
      mean,
      p50: p50Value !== null ? `${(p50Value / 1000).toFixed(2)} s` : '—',
      p95: p95Value !== null ? `${(p95Value / 1000).toFixed(2)} s` : '—',
    };
  };

  const renderGallery = (
    run: ImageRunPayload,
    byId: Record<string, ImagePromptSpec> = getPromptMap(run),
    specs: Record<string, ImageProviderSpec> = getProviderMap(),
  ): void => {
    galleryEl.classList.remove('hidden');
    const providerIds = [...new Set(run.items.map((item: ImageRunItem): string => item.provider_id))];
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
        (item: ImageRunItem): boolean =>
          galleryProvider === 'all' || item.provider_id === galleryProvider,
      )
      .sort(
        (a: ImageRunItem, b: ImageRunItem): number =>
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
    galleryPageEl.textContent = `Page ${galleryPage + 1} of ${totalPages} · ${items.length} generation${items.length === 1 ? '' : 's'}`;
    previousPageBtn.disabled = galleryPage === 0;
    nextPageBtn.disabled = galleryPage >= totalPages - 1;

    if (galleryProvider === 'all') {
      galleryPromptDetailEl.classList.add('hidden');
      return;
    }
    const providerItems = run.items.filter(
      (item: ImageRunItem): boolean => item.provider_id === galleryProvider,
    );
    const summary = computeLatencySummary(providerItems);
    const providerSpec = specs[galleryProvider] ?? { name: galleryProvider, model: '' };
    galleryPromptDetailEl.innerHTML = '';
    galleryPromptDetailEl.append(
      el('strong', { textContent: providerSpec.name }),
      el('small', { textContent: providerSpec.model }),
      el('div', { className: 'provider-stats' }, [
        formatStat('Done', `${summary.done.length}/${providerItems.length}`),
        formatStat('Est. output cost', runCost(summary.done)),
        formatStat('Mean', summary.mean),
        formatStat('p50', summary.p50),
        formatStat('p95', summary.p95),
      ]),
    );
    galleryPromptDetailEl.classList.remove('hidden');
  };

  const renderPromptGallery = (
    run: ImageRunPayload,
    byId: Record<string, ImagePromptSpec> = getPromptMap(run),
    specs: Record<string, ImageProviderSpec> = getProviderMap(),
  ): void => {
    if (galleryFilterLabel.firstChild !== null) {
      galleryFilterLabel.firstChild.textContent = 'Prompt ';
    }
    galleryEl.classList.remove('hidden');

    const promptIds = [...new Set(run.items.map((item: ImageRunItem): string => item.prompt_id))];
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

    const prompt: Pick<ImagePromptSpec, 'title' | 'dimension' | 'prompt'> | null =
      byId[galleryPrompt] ??
      (galleryPrompt === 'all'
        ? null
        : { title: 'Custom Prompt', dimension: 'Manual Prompt', prompt: '' });
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
        (item: ImageRunItem): boolean =>
          galleryPrompt === 'all' || item.prompt_id === galleryPrompt,
      )
      .sort(
        (a: ImageRunItem, b: ImageRunItem): number =>
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
    galleryPageEl.textContent = `Page ${galleryPage + 1} of ${totalPages} - ${items.length} generation${items.length === 1 ? '' : 's'}`;
    previousPageBtn.disabled = galleryPage === 0;
    nextPageBtn.disabled = galleryPage >= totalPages - 1;
  };

  const renderRun = (run: ImageRunPayload): void => {
    const byId = getPromptMap(run);
    const specs = getProviderMap();
    for (const id of Object.keys(specs)) {
      const statsEl = document.querySelector(`[data-provider-stats="${id}"]`);
      const items = run.items.filter((item: ImageRunItem): boolean => item.provider_id === id);
      if (!(statsEl instanceof HTMLElement)) {
        continue;
      }
      if (items.length === 0) {
        statsEl.classList.add('hidden');
        continue;
      }
      const summary = computeLatencySummary(items);
      statsEl.innerHTML = '';
      statsEl.append(
        formatStat('Done', `${summary.done.length}/${items.length}`),
        formatStat('Failed', `${items.length - summary.done.length}`),
        formatStat('Mean', summary.mean),
        formatStat('p50', summary.p50),
        formatStat('p95', summary.p95),
      );
      if (summary.done.length > 0) {
        statsEl.append(formatStat('Est. output cost', runCost(summary.done)));
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

  const pollRun = async (runId: string): Promise<void> => {
    if (destroyed) {
      return;
    }
    const response = await fetch(`/api/image-benchmark/runs/${runId}`);
    const run = (await response.json()) as ImageRunPayload;
    if (destroyed) {
      return;
    }
    currentRun = run;
    renderRun(run);
    statusEl.textContent =
      run.status === 'completed'
        ? `Completed ${run.total} provider/prompt comparisons.`
        : `Running ${run.items.length} of ${run.total} comparisons…`;
    if (run.status !== 'completed') {
      pollTimer = window.setTimeout((): void => {
        void pollRun(runId);
      }, 250);
    } else {
      runBtn.disabled = false;
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

  const onLightboxBackdropClick = (event: MouseEvent): void => {
    if (event.target === lightboxEl) {
      closeLightbox();
    }
  };

  const onDocumentKeydown = (event: KeyboardEvent): void => {
    if (event.key === 'Escape') {
      closeLightbox();
    }
  };

  const onRunClick = async (): Promise<void> => {
    const providerIds = selectedIds('providers');
    const promptIds = selectedIds('prompts');
    const repetitions = Number(repetitionsInput.value);
    const useCustom = useCustomPromptCheckbox.checked;
    const customText = customPromptInput.value.trim();
    const customPrompts: ImageCustomPromptInput[] = [];
    if (useCustom && customText) {
      customPrompts.push({
        prompt: customText,
        title: 'Custom Prompt',
        dimension: 'Manual Prompt',
      });
    }
    if (!providerIds.length || (!promptIds.length && !customPrompts.length)) {
      statusEl.textContent =
        'Select an available provider and at least one fixed prompt or enter a custom prompt.';
      return;
    }
    runBtn.disabled = true;
    galleryPage = 0;
    galleryProvider = 'all';
    galleryEl.classList.add('hidden');
    const response = await fetch('/api/image-benchmark/runs', {
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
    const payload = (await response.json()) as ImageRunPayload;
    if (!response.ok) {
      statusEl.textContent = payload.detail || 'Could not start benchmark.';
      runBtn.disabled = false;
      return;
    }
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
  closeLightboxBtn.addEventListener('click', closeLightbox);
  lightboxEl.addEventListener('click', onLightboxBackdropClick);
  document.addEventListener('keydown', onDocumentKeydown);
  runBtn.addEventListener('click', handleRunClick);

  const loadCatalog = async (): Promise<void> => {
    try {
      const response = await fetch('/api/image-benchmark/catalog');
      const loaded = (await response.json()) as ImageCatalogResponse;
      if (destroyed) {
        return;
      }
      catalog = loaded;
      renderCatalog(loaded);
    } catch {
      if (!destroyed) {
        statusEl.textContent = 'Could not load benchmark catalog.';
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
      galleryViewSelect.removeEventListener('change', onGalleryViewChange);
      galleryProviderSelect.removeEventListener('change', onGalleryProviderChange);
      previousPageBtn.removeEventListener('click', onPreviousPage);
      nextPageBtn.removeEventListener('click', onNextPage);
      selectAllBtn.removeEventListener('click', onSelectAll);
      deselectAllBtn.removeEventListener('click', onDeselectAll);
      customPromptInput.removeEventListener('input', onCustomPromptInput);
      closeLightboxBtn.removeEventListener('click', closeLightbox);
      lightboxEl.removeEventListener('click', onLightboxBackdropClick);
      document.removeEventListener('keydown', onDocumentKeydown);
      runBtn.removeEventListener('click', handleRunClick);
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', (): void => {
    initImageBenchmark();
  });
} else {
  initImageBenchmark();
}
