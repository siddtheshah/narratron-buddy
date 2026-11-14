/** Speech Provider Bench page controller: provider/dialogue selection, voice options, polling, and audio playback. */

export interface SpeechProviderSpec {
  readonly id: string;
  readonly name: string;
  readonly model: string;
  readonly notes: string;
  readonly status: string;
  readonly estimated_cost_usd_1k_chars?: number | null;
  readonly model_options?: readonly string[];
}

export interface SpeechPromptSpec {
  readonly id: string;
  readonly title: string;
  readonly dimension: string;
  readonly text: string;
  readonly voice_instruction: string;
}

export interface SpeechCatalogResponse {
  readonly providers: readonly SpeechProviderSpec[];
  readonly prompts: readonly SpeechPromptSpec[];
}

export interface SpeechCustomPromptInput {
  readonly text: string;
  readonly voice_instruction: string;
  readonly title: string;
  readonly dimension: string;
}

export interface SpeechRunItem {
  readonly id: string;
  readonly provider_id: string;
  readonly prompt_id: string;
  readonly started_at: number;
  readonly status: string;
  readonly latency_ms: number;
  readonly model?: string;
  readonly selected_voice?: string | null;
  readonly voice_tags?: readonly string[];
  readonly audio_url?: string;
  readonly estimated_output_cost_usd?: number | null;
  readonly error?: string;
}

export interface SpeechRunPayload {
  readonly id: string;
  readonly status: string;
  readonly total: number;
  readonly prompts?: readonly SpeechPromptSpec[];
  readonly items: readonly SpeechRunItem[];
  readonly detail?: string;
}

export interface SpeechBenchmarkController {
  loadCatalog(): Promise<void>;
  destroy(): void;
}

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

function makeOptionInput(id: string, key: string, value: string): HTMLInputElement {
  const input = el('input', { value });
  input.dataset.provider = id;
  input.dataset.option = key;
  return input;
}

function makeModelSelect(id: string, values: readonly string[], current: string): HTMLSelectElement {
  const select = el('select');
  select.dataset.provider = id;
  select.dataset.option = 'model';
  for (const value of values) {
    select.append(el('option', { value, textContent: value, selected: value === current }));
  }
  return select;
}

/** Initializes the Speech Provider Bench DOM controls, catalog loading, and run polling. */
export function initSpeechBenchmark(): SpeechBenchmarkController | null {
  const providersEl = document.getElementById('providers');
  const promptsEl = document.getElementById('prompts');
  const selectAllBtn = document.getElementById('selectAll');
  const deselectAllBtn = document.getElementById('deselectAll');
  const useCustomPromptCheckbox = document.getElementById('useCustomPrompt');
  const customDialogueInput = document.getElementById('customDialogueText');
  const customVoiceInstructionInput = document.getElementById('customVoiceInstruction');
  const runBtn = document.getElementById('run');
  const statusEl = document.getElementById('status');
  const resultsPanel = document.getElementById('results-panel');
  const resultsEl = document.getElementById('results');

  if (
    !(providersEl instanceof HTMLElement) ||
    !(promptsEl instanceof HTMLElement) ||
    !(selectAllBtn instanceof HTMLButtonElement) ||
    !(deselectAllBtn instanceof HTMLButtonElement) ||
    !(useCustomPromptCheckbox instanceof HTMLInputElement) ||
    !(customDialogueInput instanceof HTMLTextAreaElement) ||
    !(customVoiceInstructionInput instanceof HTMLInputElement) ||
    !(runBtn instanceof HTMLButtonElement) ||
    !(statusEl instanceof HTMLElement) ||
    !(resultsPanel instanceof HTMLElement) ||
    !(resultsEl instanceof HTMLElement)
  ) {
    return null;
  }

  let catalog: SpeechCatalogResponse | null = null;
  let currentRun: SpeechRunPayload | null = null;
  let pollTimer: number | null = null;
  let destroyed = false;

  const getPromptMap = (run: SpeechRunPayload | null): Record<string, SpeechPromptSpec> => {
    const map: Record<string, SpeechPromptSpec> = {};
    for (const prompt of catalog?.prompts ?? []) {
      map[prompt.id] = prompt;
    }
    for (const prompt of run?.prompts ?? []) {
      map[prompt.id] = prompt;
    }
    return map;
  };

  const renderCatalog = (loadedCatalog: SpeechCatalogResponse): void => {
    providersEl.innerHTML = '';
    promptsEl.innerHTML = '';

    for (const provider of loadedCatalog.providers) {
      const checked = provider.status === 'available';
      const box = el('input', {
        type: 'checkbox',
        value: provider.id,
        checked,
        disabled: !checked,
      });
      const kids: ChildItem[] = [
        box,
        el('strong', { textContent: provider.name }),
        el('small', { textContent: provider.model }),
        el('small', { textContent: provider.notes }),
        el('small', {
          textContent:
            provider.estimated_cost_usd_1k_chars == null
              ? 'Cost estimate pending'
              : `~$${provider.estimated_cost_usd_1k_chars.toFixed(3)} / 1k chars`,
        }),
        el('span', { className: 'muted', textContent: provider.status }),
      ];
      if (provider.model_options && provider.model_options.length > 0) {
        kids.push(
          el('small', { textContent: 'Model' }),
          makeModelSelect(provider.id, provider.model_options, provider.model),
        );
      }
      const tags = makeOptionInput(provider.id, 'voice_tags', '');
      tags.placeholder = 'Voice filters (e.g. female, accent=General American)';
      kids.push(el('small', { textContent: 'Voice tags / filters (selects a voice)' }), tags);
      providersEl.append(el('label', { className: 'choice' }, kids));
    }

    loadedCatalog.prompts.forEach((prompt: SpeechPromptSpec, index: number): void => {
      promptsEl.append(
        el('label', { className: 'choice' }, [
          el('input', { type: 'checkbox', value: prompt.id, checked: index === 0 }),
          el('strong', { textContent: `${prompt.title}` }),
          el('small', { textContent: prompt.dimension }),
          el('small', { textContent: `“${prompt.text}”` }),
          el('small', { textContent: prompt.voice_instruction }),
        ]),
      );
    });
  };

  const selectedIds = (containerId: string): string[] => {
    const inputs = document.querySelectorAll<HTMLInputElement>(
      `#${containerId} input[type=checkbox]:checked`,
    );
    return Array.from(inputs, (input: HTMLInputElement): string => input.value);
  };

  const collectProviderOptions = (): Record<string, Record<string, string>> => {
    const values: Record<string, Record<string, string>> = {};
    const optionNodes = document.querySelectorAll<HTMLInputElement | HTMLSelectElement>('[data-provider]');
    optionNodes.forEach((node: HTMLInputElement | HTMLSelectElement): void => {
      const providerId = node.dataset.provider;
      const optionKey = node.dataset.option;
      const trimmed = node.value.trim();
      if (!providerId || !optionKey || !trimmed) {
        return;
      }
      values[providerId] ??= {};
      values[providerId][optionKey] = trimmed;
    });
    return values;
  };

  const showResults = (run: SpeechRunPayload): void => {
    resultsPanel.hidden = false;
    resultsEl.innerHTML = '';
    const promptMap = getPromptMap(run);
    const providerMap = new Map<string, SpeechProviderSpec>(
      (catalog?.providers ?? []).map((provider: SpeechProviderSpec): [string, SpeechProviderSpec] => [
        provider.id,
        provider,
      ]),
    );
    const sortedItems = [...run.items].sort(
      (a: SpeechRunItem, b: SpeechRunItem): number => a.started_at - b.started_at,
    );

    for (const item of sortedItems) {
      const card = el('article', {
        className: `result ${item.status === 'failed' ? 'error' : ''}`,
      });
      const promptInfo: Pick<SpeechPromptSpec, 'title' | 'dimension' | 'text'> =
        promptMap[item.prompt_id] ?? {
          title: 'Custom Dialogue',
          dimension: 'Manual Dialogue',
          text: '',
        };
      const provInfo = providerMap.get(item.provider_id) ?? {
        name: item.provider_id,
        model: '',
      };

      card.append(
        el('strong', { textContent: `${provInfo.name} · ${promptInfo.title}` }),
        el('p', {
          className: 'muted',
          textContent: `${(item.latency_ms / 1000).toFixed(2)} s · ${item.model || provInfo.model || ''}`,
        }),
      );
      if (item.selected_voice) {
        const tagSuffix =
          item.voice_tags && item.voice_tags.length > 0
            ? ` · tags: ${item.voice_tags.join(', ')}`
            : '';
        card.append(
          el('small', {
            textContent: `Selected voice: ${item.selected_voice}${tagSuffix}`,
          }),
        );
      }
      if (item.audio_url) {
        card.append(el('audio', { controls: true, src: item.audio_url }));
      }
      if (item.estimated_output_cost_usd != null) {
        card.append(
          el('small', {
            textContent: `Estimated output cost: $${item.estimated_output_cost_usd.toFixed(4)}`,
          }),
        );
      }
      if (item.error) {
        card.append(el('p', { className: 'error-text', textContent: item.error }));
      }
      resultsEl.append(card);
    }
  };

  const pollRun = async (runId: string): Promise<void> => {
    if (destroyed) {
      return;
    }
    const response = await fetch(`/api/speech-benchmark/runs/${runId}`);
    const run = (await response.json()) as SpeechRunPayload;
    if (destroyed) {
      return;
    }
    currentRun = run;
    showResults(run);
    if (run.status === 'completed') {
      runBtn.disabled = false;
      statusEl.textContent = `Completed ${run.total} speech comparisons.`;
    } else {
      statusEl.textContent = `Running ${run.items.length}/${run.total} comparisons…`;
      pollTimer = window.setTimeout((): void => {
        void pollRun(runId);
      }, 300);
    }
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

  const onCustomDialogueInput = (): void => {
    if (customDialogueInput.value.trim()) {
      useCustomPromptCheckbox.checked = true;
    }
  };

  const onRunClick = async (): Promise<void> => {
    const providerIds = selectedIds('providers');
    const promptIds = selectedIds('prompts');
    const useCustom = useCustomPromptCheckbox.checked;
    const customText = customDialogueInput.value.trim();
    const customPrompts: SpeechCustomPromptInput[] = [];
    if (useCustom && customText) {
      customPrompts.push({
        text: customText,
        voice_instruction: customVoiceInstructionInput.value.trim(),
        title: 'Custom Dialogue',
        dimension: 'Manual Dialogue',
      });
    }
    if (!providerIds.length || (!promptIds.length && !customPrompts.length)) {
      statusEl.textContent =
        'Select at least one available provider and dialogue line or enter a custom prompt.';
      return;
    }
    runBtn.disabled = true;
    const response = await fetch('/api/speech-benchmark/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        provider_ids: providerIds,
        prompt_ids: promptIds,
        custom_prompts: customPrompts,
        provider_options: collectProviderOptions(),
      }),
    });
    const payload = (await response.json()) as SpeechRunPayload;
    if (!response.ok) {
      statusEl.textContent = payload.detail || 'Could not start the comparison.';
      runBtn.disabled = false;
      return;
    }
    void pollRun(payload.id);
  };

  const handleRunClick = (): void => {
    void onRunClick();
  };

  selectAllBtn.addEventListener('click', onSelectAll);
  deselectAllBtn.addEventListener('click', onDeselectAll);
  customDialogueInput.addEventListener('input', onCustomDialogueInput);
  runBtn.addEventListener('click', handleRunClick);

  const loadCatalog = async (): Promise<void> => {
    try {
      const response = await fetch('/api/speech-benchmark/catalog');
      const loaded = (await response.json()) as SpeechCatalogResponse;
      if (destroyed) {
        return;
      }
      catalog = loaded;
      renderCatalog(loaded);
    } catch {
      if (!destroyed) {
        statusEl.textContent = 'Could not load speech providers.';
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
      selectAllBtn.removeEventListener('click', onSelectAll);
      deselectAllBtn.removeEventListener('click', onDeselectAll);
      customDialogueInput.removeEventListener('input', onCustomDialogueInput);
      runBtn.removeEventListener('click', handleRunClick);
      void currentRun;
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', (): void => {
    initSpeechBenchmark();
  });
} else {
  initSpeechBenchmark();
}
