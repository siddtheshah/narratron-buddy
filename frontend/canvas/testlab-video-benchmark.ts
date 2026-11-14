/** Video Benchmark Lab page controller: catalog loading, run submission, polling, and gallery output. */

export interface VideoProviderSpec {
  readonly id: string;
  readonly name: string;
  readonly model: string;
  readonly status: string;
  readonly notes: string;
}

export interface VideoPromptSpec {
  readonly id: string;
  readonly title: string;
  readonly category: string;
  readonly prompt: string;
}

export interface VideoBenchmarkCatalog {
  readonly providers: readonly VideoProviderSpec[];
  readonly prompts: readonly VideoPromptSpec[];
}

export interface VideoRunResult {
  readonly id: string;
  readonly status: string;
  readonly title?: string;
  readonly prompt?: string;
  readonly video_url?: string;
  readonly latency_ms?: number | null;
  readonly error?: string;
  readonly detail?: string;
}

export interface VideoBenchmarkController {
  loadCatalog(): Promise<void>;
  destroy(): void;
}

type ElementAttrs<K extends keyof HTMLElementTagNameMap> = Omit<
  Partial<HTMLElementTagNameMap[K]>,
  'style'
> & {
  readonly style?: string;
};

type ChildItem = Node | string | null | undefined;

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs?: ElementAttrs<K>,
  children: readonly ChildItem[] = [],
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (attrs !== undefined) {
    const { style, ...rest } = attrs;
    if (style !== undefined) {
      node.setAttribute('style', style);
    }
    Object.assign(node, rest);
  }
  for (const child of children) {
    if (typeof child === 'string') {
      node.appendChild(document.createTextNode(child));
    } else if (child) {
      node.appendChild(child);
    }
  }
  return node;
}

function getEl(id: string): HTMLElement | null {
  return document.getElementById(id);
}

/** Wires the Video Benchmark Lab catalog, prompt builder, polling loop, and result gallery. */
export function initVideoBenchmark(): VideoBenchmarkController | null {
  const providersContainer = getEl('providers');
  const promptsContainer = getEl('prompts');
  const customInputRaw = getEl('customPromptText');
  const useCustomPromptRaw = getEl('useCustomPrompt');
  const runBtnRaw = getEl('runBtn');
  const statusEl = getEl('status');
  const gallery = getEl('gallery');
  const gallerySection = getEl('gallerySection');

  if (
    !providersContainer ||
    !promptsContainer ||
    !(customInputRaw instanceof HTMLInputElement ||
      customInputRaw instanceof HTMLTextAreaElement) ||
    !(useCustomPromptRaw instanceof HTMLInputElement) ||
    !(runBtnRaw instanceof HTMLButtonElement) ||
    !statusEl ||
    !gallery ||
    !gallerySection
  ) {
    return null;
  }

  const customInput = customInputRaw;
  const useCustomPrompt = useCustomPromptRaw;
  const runBtn = runBtnRaw;

  let pollTimer: number | null = null;

  const renderCatalog = (catalog: VideoBenchmarkCatalog): void => {
    providersContainer.innerHTML = '';
    catalog.providers.forEach((p: VideoProviderSpec, index: number): void => {
      const isAvailable = p.status === 'available';
      const radio = el('input', {
        type: 'radio',
        name: 'providerSelect',
        value: p.id,
        checked: index === 0 && isAvailable,
        disabled: !isAvailable,
      });

      const statusPill = el('span', {
        className: `pill ${isAvailable ? 'pill-available' : 'pill-unconfigured'}`,
        textContent: isAvailable ? '● Ready' : '● Unconfigured (Requires FAL_API_KEY)',
      });

      const card = el('label', { className: 'choice' }, [
        radio,
        el('strong', {}, [p.name]),
        el('div', {}, [statusPill]),
        el('small', { textContent: p.model }),
        el('small', { textContent: p.notes }),
      ]);

      providersContainer.appendChild(card);
    });

    promptsContainer.innerHTML = '';
    catalog.prompts.forEach((p: VideoPromptSpec, index: number): void => {
      const radio = el('input', {
        type: 'radio',
        name: 'promptSelect',
        value: p.id,
        checked: index === 0,
      });

      const catPill = el('span', {
        className: 'pill pill-category',
        textContent: p.category,
      });

      const card = el('label', { className: 'choice' }, [
        radio,
        el('strong', {}, [p.title]),
        el('div', {}, [catPill]),
        el('small', { textContent: p.prompt }),
      ]);

      promptsContainer.appendChild(card);
    });
  };

  const getSelectedProvider = (): string | null => {
    const checked = document.querySelector<HTMLInputElement>('input[name="providerSelect"]:checked');
    return checked ? checked.value : null;
  };

  const getPromptPayload = (): { prompt?: string; prompt_id?: string | null } => {
    const selectedPrompt = document.querySelector<HTMLInputElement>(
      'input[name="promptSelect"]:checked',
    );
    if (!selectedPrompt) {
      return { prompt: '' };
    }
    if (selectedPrompt.value === 'custom') {
      return { prompt: customInput.value.trim(), prompt_id: null };
    }
    return { prompt_id: selectedPrompt.value };
  };

  const addVideoResult = (item: VideoRunResult): void => {
    gallerySection.classList.remove('hidden');

    const card = el('div', {
      className: `video-card ${item.status === 'failed' ? 'error' : ''}`,
    });

    if (item.video_url) {
      const wrapper = el('div', { className: 'video-wrapper' });
      const video = el('video', {
        src: item.video_url,
        controls: true,
        autoplay: true,
        loop: true,
        muted: true,
        playsInline: true,
      });
      wrapper.appendChild(video);
      card.appendChild(wrapper);
    }

    const body = el('div', { className: 'video-body' });
    const title = el('h3', {
      className: 'video-title',
      textContent: item.title || 'Generated Video',
    });
    const promptText = el('p', { className: 'video-prompt', textContent: item.prompt || '' });
    body.appendChild(title);
    body.appendChild(promptText);

    if (item.error) {
      const err = el('p', {
        style: 'color: #fb7185; font-size: 0.85rem; font-weight: 500; margin: 0 0 8px;',
        textContent: `Error: ${item.error}`,
      });
      body.appendChild(err);
    }

    const meta = el('div', { className: 'video-meta' });
    const latency = el('span', {
      className: 'latency-badge',
      textContent: item.latency_ms ? `${(item.latency_ms / 1000).toFixed(2)}s` : 'Failed',
    });
    meta.appendChild(latency);

    if (item.video_url) {
      const dl = el('a', {
        className: 'download-btn',
        href: item.video_url,
        download: 'video.mp4',
        textContent: '⬇ Download MP4',
      });
      meta.appendChild(dl);
    }

    body.appendChild(meta);
    card.appendChild(body);
    gallery.prepend(card);
  };

  const pollRun = async (runId: string): Promise<void> => {
    try {
      const res = await fetch(`/api/video-benchmark/runs/${runId}`);
      if (!res.ok) {
        throw new Error('Polling error');
      }
      const data = (await res.json()) as VideoRunResult;

      if (data.status === 'completed') {
        const secs = ((data.latency_ms || 0) / 1000).toFixed(2);
        statusEl.innerHTML = `<span style="color:#10b981">✓ Video generated in ${secs}s</span>`;
        runBtn.disabled = false;
        addVideoResult(data);
      } else if (data.status === 'failed') {
        statusEl.innerHTML = `<span style="color:#f43f5e">✗ Generation failed: ${data.error || 'Unknown error'}</span>`;
        runBtn.disabled = false;
        addVideoResult(data);
      } else {
        statusEl.innerHTML =
          '<span class="status-running"><span class="spinner"></span> Generating video on FAL MiniMax H3 Max Turbo…</span>';
        pollTimer = window.setTimeout((): void => {
          void pollRun(runId);
        }, 800);
      }
    } catch {
      statusEl.innerHTML =
        '<span style="color:#f43f5e">Connection error polling run.</span>';
      runBtn.disabled = false;
    }
  };

  const onCustomInput = (): void => {
    if (customInput.value.trim()) {
      useCustomPrompt.checked = true;
    }
  };

  const onRunClick = async (): Promise<void> => {
    const providerId = getSelectedProvider();
    if (!providerId) {
      window.alert('Please select an available video model.');
      return;
    }

    const promptData = getPromptPayload();
    if (!promptData.prompt && !promptData.prompt_id) {
      window.alert('Please select or enter a prompt.');
      return;
    }

    runBtn.disabled = true;
    statusEl.innerHTML =
      '<span class="status-running"><span class="spinner"></span> Dispatching request to FAL…</span>';

    try {
      const res = await fetch('/api/video-benchmark/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider_id: providerId,
          ...promptData,
        }),
      });

      const data = (await res.json()) as VideoRunResult;
      if (!res.ok) {
        statusEl.innerHTML = `<span style="color:#f43f5e">${data.detail || 'Failed to start run'}</span>`;
        runBtn.disabled = false;
        return;
      }

      void pollRun(data.id);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      statusEl.innerHTML = `<span style="color:#f43f5e">Request error: ${msg}</span>`;
      runBtn.disabled = false;
    }
  };

  const onRunHandler = (): void => {
    void onRunClick();
  };

  customInput.addEventListener('input', onCustomInput);
  runBtn.addEventListener('click', onRunHandler);

  const loadCatalog = async (): Promise<void> => {
    try {
      const res = await fetch('/api/video-benchmark/catalog');
      const catalog = (await res.json()) as VideoBenchmarkCatalog;
      renderCatalog(catalog);
    } catch {
      statusEl.textContent = 'Error loading video benchmark catalog.';
    }
  };

  void loadCatalog();

  return {
    loadCatalog,
    destroy(): void {
      if (pollTimer !== null) {
        window.clearTimeout(pollTimer);
        pollTimer = null;
      }
      customInput.removeEventListener('input', onCustomInput);
      runBtn.removeEventListener('click', onRunHandler);
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', (): void => {
    initVideoBenchmark();
  });
} else {
  initVideoBenchmark();
}
