/** Text Effects & Beautifier Demo page controller: preset loader, scene beautifier, typewriter playback, and sandbox. */

export interface TextSpanSpec {
  readonly text: string;
  readonly effect?: string;
  readonly font?: string;
  readonly color?: string | null;
}

export interface TextPresetDialogueLine {
  readonly speaker: string;
  readonly text: string;
}

export interface TextPreset {
  readonly id: string;
  readonly title: string;
  readonly badge: string;
  readonly narration?: string;
  readonly dialogue?: readonly TextPresetDialogueLine[];
}

export interface PresetsResponse {
  readonly presets?: readonly TextPreset[];
}

export interface BeautifiedDialogueLine {
  readonly speaker: string;
  readonly text: string;
  readonly kind?: string;
  readonly spans?: readonly TextSpanSpec[];
}

export interface BeautifySceneResponse {
  readonly narration?: string;
  readonly narration_spans?: readonly TextSpanSpec[];
  readonly dialogue?: readonly BeautifiedDialogueLine[];
  readonly latency_seconds?: number;
  readonly model?: string;
}

export interface TextEffectsLabController {
  switchTab(tab: 'ai' | 'catalog'): void;
  loadPresets(): Promise<void>;
  selectPreset(presetId: string): void;
  addDialogueRow(speaker?: string, text?: string): void;
  submitBeautification(): Promise<void>;
  replayCurrentScene(): void;
  updateSandbox(): void;
  destroy(): void;
}

interface TextEffectsWindow extends Window {
  Splitting?: (options?: { readonly target?: Element; readonly by?: string }) => unknown;
  switchTab?: (tab: 'ai' | 'catalog') => void;
  addDialogueRow?: (speaker?: string, text?: string) => void;
  submitBeautification?: () => Promise<void>;
  replayCurrentScene?: () => void;
  updateSandbox?: () => void;
}

function escapeHtml(str: string): string {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function getSpeakerColor(speaker: string): string {
  const colors = ['#38bdf8', '#a78bfa', '#f472b6', '#34d399', '#fbbf24', '#f87171'];
  let hash = 0;
  for (let i = 0; i < speaker.length; i++) {
    hash = (hash * 31 + speaker.charCodeAt(i)) >>> 0;
  }
  return colors[hash % colors.length];
}

function createSpanElement(span: TextSpanSpec): HTMLSpanElement {
  const el = document.createElement('span');
  el.className = 'text-span';
  if (span.effect && span.effect !== 'none') {
    el.classList.add(`text-effect-${span.effect}`);
  }
  if (span.font && span.font !== 'default') {
    el.classList.add(`text-font-${span.font}`);
  }
  if (span.color) {
    el.style.color = span.color;
  }
  return el;
}

function applySplittingIfAvailable(el: Element): void {
  const win = window as TextEffectsWindow;
  if (typeof win.Splitting === 'function') {
    try {
      win.Splitting({ target: el, by: 'chars' });
    } catch {
      // Ignore optional Splitting.js errors
    }
  }
}

function getEl(id: string): HTMLElement | null {
  return document.getElementById(id);
}

/** Wires the Text Effects Lab presets, beautification API, sandbox preview, and global onclick handlers. */
export function initTextEffectsLab(): TextEffectsLabController | null {
  const viewAi = getEl('viewAi');
  const viewCatalog = getEl('viewCatalog');
  const tabAiBtn = getEl('tabAiBtn');
  const tabCatalogBtn = getEl('tabCatalogBtn');
  const presetsRow = getEl('presetsRow');
  const narrationInputRaw = getEl('narrationInput');
  const dialogueContainer = getEl('dialogueContainer');
  const modelSelectRaw = getEl('modelSelect');
  const beautifyBtnRaw = getEl('beautifyBtn');
  const btnIcon = getEl('btnIcon');
  const btnText = getEl('btnText');
  const latencyBadge = getEl('latencyBadge');
  const jsonContainer = getEl('jsonContainer');
  const jsonViewer = getEl('jsonViewer');
  const sceneNarrationEl = getEl('sceneNarrationEl');
  const sceneDialogueEl = getEl('sceneDialogueEl');
  const sandboxTextRaw = getEl('sandboxText');
  const sandboxEffectRaw = getEl('sandboxEffect');
  const sandboxFontRaw = getEl('sandboxFont');
  const sandboxColorRaw = getEl('sandboxColor');
  const sandboxPreview = getEl('sandboxPreview');

  if (
    !viewAi ||
    !viewCatalog ||
    !tabAiBtn ||
    !tabCatalogBtn ||
    !presetsRow ||
    !(narrationInputRaw instanceof HTMLTextAreaElement) ||
    !dialogueContainer ||
    !(modelSelectRaw instanceof HTMLSelectElement) ||
    !(beautifyBtnRaw instanceof HTMLButtonElement) ||
    !btnIcon ||
    !btnText ||
    !latencyBadge ||
    !jsonContainer ||
    !jsonViewer ||
    !sceneNarrationEl ||
    !sceneDialogueEl ||
    !(sandboxTextRaw instanceof HTMLInputElement) ||
    !(sandboxEffectRaw instanceof HTMLSelectElement) ||
    !(sandboxFontRaw instanceof HTMLSelectElement) ||
    !(sandboxColorRaw instanceof HTMLInputElement) ||
    !sandboxPreview
  ) {
    return null;
  }

  const narrationInput = narrationInputRaw;
  const modelSelect = modelSelectRaw;
  const beautifyBtn = beautifyBtnRaw;
  const sandboxText = sandboxTextRaw;
  const sandboxEffect = sandboxEffectRaw;
  const sandboxFont = sandboxFontRaw;
  const sandboxColor = sandboxColorRaw;

  let currentPresets: readonly TextPreset[] = [];
  let lastRenderPayload: BeautifySceneResponse | null = null;
  let renderId = 0;

  const updateSandbox = (): void => {
    const text = sandboxText.value || 'TEST EFFECT';
    const effect = sandboxEffect.value;
    const font = sandboxFont.value;
    const color = sandboxColor.value;

    sandboxPreview.replaceChildren();
    const span = createSpanElement({ text, effect, font, color });
    span.textContent = text;
    sandboxPreview.appendChild(span);
    if (effect && effect !== 'none') {
      applySplittingIfAvailable(span);
    }
  };

  const switchTab = (tab: 'ai' | 'catalog'): void => {
    viewAi.style.display = tab === 'ai' ? 'grid' : 'none';
    viewCatalog.style.display = tab === 'catalog' ? 'block' : 'none';
    tabAiBtn.classList.toggle('active', tab === 'ai');
    tabCatalogBtn.classList.toggle('active', tab === 'catalog');
    if (tab === 'catalog') {
      updateSandbox();
      const win = window as TextEffectsWindow;
      if (typeof win.Splitting === 'function') {
        win.Splitting();
      }
    }
  };

  const addDialogueRow = (speaker = '', text = ''): void => {
    const row = document.createElement('div');
    row.className = 'dialogue-item';
    row.innerHTML = `
      <input type="text" placeholder="Speaker" value="${escapeHtml(speaker)}" class="diag-speaker">
      <input type="text" placeholder="Spoken line..." value="${escapeHtml(text)}" class="diag-text">
      <button type="button" class="del-btn" title="Remove line" onclick="this.parentElement.remove()">✕</button>
    `;
    dialogueContainer.appendChild(row);
  };

  const selectPreset = (presetId: string): void => {
    const preset = currentPresets.find((p: TextPreset): boolean => p.id === presetId);
    if (!preset) {
      return;
    }
    document.querySelectorAll('.preset-chip').forEach((c: Element): void => {
      c.classList.remove('active');
    });
    const activeChip = getEl(`preset-${presetId}`);
    if (activeChip) {
      activeChip.classList.add('active');
    }

    narrationInput.value = preset.narration || '';
    dialogueContainer.replaceChildren();
    for (const line of preset.dialogue || []) {
      addDialogueRow(line.speaker, line.text);
    }
  };

  const renderPresetChips = (): void => {
    presetsRow.replaceChildren();
    for (const p of currentPresets) {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'preset-chip';
      chip.id = `preset-${p.id}`;
      chip.innerHTML = `<span>${escapeHtml(p.title)}</span><small style="opacity:0.7;">(${escapeHtml(p.badge)})</small>`;
      chip.onclick = (): void => selectPreset(p.id);
      presetsRow.appendChild(chip);
    }
  };

  const loadPresets = async (): Promise<void> => {
    try {
      const res = await fetch('/api/text-effects/presets');
      const data = (await res.json()) as PresetsResponse;
      currentPresets = data.presets || [];
      renderPresetChips();
      if (currentPresets.length > 0) {
        selectPreset(currentPresets[0].id);
      }
    } catch (err: unknown) {
      console.error('Failed to load presets:', err);
    }
  };

  const renderScene = async (data: BeautifySceneResponse): Promise<void> => {
    const activeRenderId = ++renderId;

    sceneNarrationEl.replaceChildren();
    sceneDialogueEl.replaceChildren();

    if (data.narration) {
      sceneNarrationEl.style.display = 'block';
      const spans: readonly TextSpanSpec[] =
        data.narration_spans && data.narration_spans.length > 0
          ? data.narration_spans
          : [{ text: data.narration, effect: 'none', font: 'default' }];

      for (const s of spans) {
        if (activeRenderId !== renderId) return;
        const spanEl = createSpanElement(s);
        sceneNarrationEl.appendChild(spanEl);
        for (const char of Array.from(s.text)) {
          if (activeRenderId !== renderId) return;
          spanEl.textContent = (spanEl.textContent || '') + char;
          await new Promise<void>((resolve): void => {
            window.setTimeout(resolve, 14);
          });
        }
        if (s.effect && s.effect !== 'none') {
          applySplittingIfAvailable(spanEl);
        }
      }
    } else {
      sceneNarrationEl.style.display = 'none';
    }

    for (const line of data.dialogue || []) {
      if (activeRenderId !== renderId) return;
      const bubble = document.createElement('div');
      bubble.className = 'dialogue-bubble';

      const speaker = document.createElement('span');
      speaker.className = 'speaker-tag';
      speaker.textContent = line.speaker;
      speaker.style.color = getSpeakerColor(line.speaker);

      const textContainer = document.createElement('div');
      textContainer.className = 'dialogue-text';

      bubble.append(speaker, textContainer);
      sceneDialogueEl.appendChild(bubble);

      const lineSpans: readonly TextSpanSpec[] =
        line.spans && line.spans.length > 0
          ? line.spans
          : [{ text: line.text, effect: 'none', font: 'default' }];

      for (const s of lineSpans) {
        if (activeRenderId !== renderId) return;
        const spanEl = createSpanElement(s);
        textContainer.appendChild(spanEl);
        for (const char of Array.from(s.text)) {
          if (activeRenderId !== renderId) return;
          spanEl.textContent = (spanEl.textContent || '') + char;
          await new Promise<void>((resolve): void => {
            window.setTimeout(resolve, 16);
          });
        }
        if (s.effect && s.effect !== 'none') {
          applySplittingIfAvailable(spanEl);
        }
      }
    }
  };

  const submitBeautification = async (): Promise<void> => {
    const narration = narrationInput.value.trim();
    const dialogueRows = document.querySelectorAll('.dialogue-item');
    const dialogue: Array<{ speaker: string; text: string; kind: string }> = [];
    for (const row of dialogueRows) {
      const spkInput = row.querySelector('.diag-speaker');
      const txtInput = row.querySelector('.diag-text');
      const spk = spkInput instanceof HTMLInputElement ? spkInput.value.trim() : '';
      const txt = txtInput instanceof HTMLInputElement ? txtInput.value.trim() : '';
      if (txt) {
        dialogue.push({ speaker: spk || 'Narrator', text: txt, kind: 'speech' });
      }
    }

    if (!narration && dialogue.length === 0) {
      window.alert('Please provide scene narration or at least one dialogue line.');
      return;
    }

    const model = modelSelect.value;

    beautifyBtn.disabled = true;
    btnIcon.innerHTML = '<div class="spinner"></div>';
    btnText.textContent = 'Calling Gemini...';
    latencyBadge.textContent = 'Generating...';

    try {
      const res = await fetch('/api/text-effects/beautify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ narration, dialogue, model }),
      });
      if (!res.ok) {
        throw new Error(await res.text());
      }
      const data = (await res.json()) as BeautifySceneResponse;

      lastRenderPayload = data;
      latencyBadge.textContent = `⚡ ${data.latency_seconds}s (${data.model})`;

      jsonContainer.style.display = 'block';
      jsonViewer.textContent = JSON.stringify(data, null, 2);

      await renderScene(data);
    } catch (err: unknown) {
      console.error('Beautification error:', err);
      const msg = err instanceof Error ? err.message : String(err);
      window.alert(`Error: ${msg}`);
      latencyBadge.textContent = 'Failed';
    } finally {
      beautifyBtn.disabled = false;
      btnIcon.textContent = '✨';
      btnText.textContent = 'Beautify & Animate Scene';
    }
  };

  const replayCurrentScene = (): void => {
    if (lastRenderPayload) {
      void renderScene(lastRenderPayload);
    }
  };

  sandboxText.addEventListener('input', updateSandbox);

  const win = window as TextEffectsWindow;
  win.switchTab = switchTab;
  win.addDialogueRow = addDialogueRow;
  win.submitBeautification = submitBeautification;
  win.replayCurrentScene = replayCurrentScene;
  win.updateSandbox = updateSandbox;

  void loadPresets();

  return {
    switchTab,
    loadPresets,
    selectPreset,
    addDialogueRow,
    submitBeautification,
    replayCurrentScene,
    updateSandbox,
    destroy(): void {
      renderId += 1;
      sandboxText.removeEventListener('input', updateSandbox);
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', (): void => {
    initTextEffectsLab();
  });
} else {
  initTextEffectsLab();
}
