import type { MicMode } from './mic-config.js';

export interface HotkeySpec {
  readonly code: string;
  readonly key: string;
  readonly altKey: boolean;
  readonly ctrlKey: boolean;
  readonly shiftKey: boolean;
}

export type MicPressSource = 'pointer' | 'hotkey' | 'button-key' | null;

export interface CreateHotkeyControlsOptions {
  readonly isCurrentOrator: () => boolean;
  readonly getMicMode?: () => MicMode;
  readonly getMicPressSource?: () => MicPressSource;
  readonly beginMicPress?: (source: 'hotkey' | 'button-key') => void;
  readonly releaseMicPress?: () => void;
  readonly agentStartStopBtn?: HTMLElement | null;
  readonly agentConnectBtn?: HTMLButtonElement | null;
  readonly menuItemHotkey?: HTMLElement | null;
  readonly menuHotkeyDisplay?: HTMLElement | null;
  readonly howtoMicDisplay?: HTMLElement | null;
  readonly micHotkeyBtn?: HTMLElement | null;
  readonly hotkeyDisplay?: HTMLElement | null;
  readonly menuItemTextHotkey?: HTMLElement | null;
  readonly menuTextHotkeyDisplay?: HTMLElement | null;
  readonly howtoTextDisplay?: HTMLElement | null;
  readonly dismissHotkeyDisplay?: HTMLElement | null;
  readonly oratorCommandOverlay?: HTMLElement | null;
  readonly oratorCommandForm?: HTMLFormElement | null;
  readonly oratorCommandInput?: HTMLInputElement | null;
  readonly oratorCommandStatus?: HTMLElement | null;
  readonly getTheaterId?: () => string | null;
  readonly stopSceneSpeech?: () => void;
  readonly isOratorCommandOpen?: () => boolean;
  readonly openOratorCommand?: () => void;
  readonly closeOratorCommand?: () => void;
  readonly onRebindStart?: () => void;
  readonly capturePlainSpace?: boolean;
}

export interface HotkeyControlsController {
  getMicHotkey(): HotkeySpec;
  getTextHotkey(): HotkeySpec;
  setMicHotkey(hk: string | Partial<HotkeySpec>): void;
  setTextHotkey(hk: string | Partial<HotkeySpec>): void;
  isRebindingMicHotkey(): boolean;
  isRebindingTextHotkey(): boolean;
  cancelRebinding(): void;
  updateHotkeyUI(): void;
  updateTextHotkeyUI(): void;
  isTextHotkeyEvent(event: KeyboardEvent): boolean;
  isOratorCommandOpen(): boolean;
  openOratorCommand(): void;
  closeOratorCommand(): void;
  destroy(): void;
}

export const MIC_HOTKEY_STORAGE_KEY = 'narratron_mic_hotkey';
export const TEXT_HOTKEY_STORAGE_KEY = 'narratron_text_hotkey';

export const DEFAULT_MIC_HOTKEY: HotkeySpec = {
  code: 'BracketLeft',
  key: '[',
  altKey: false,
  ctrlKey: true,
  shiftKey: true,
};

export const DEFAULT_TEXT_HOTKEY: HotkeySpec = {
  code: 'Space',
  key: ' ',
  altKey: false,
  ctrlKey: false,
  shiftKey: true,
};

const CODE_DISPLAY_LABELS: Readonly<Record<string, string>> = {
  BracketLeft: '[',
  BracketRight: ']',
  Backquote: '`',
  Semicolon: ';',
  Quote: "'",
  Comma: ',',
  Period: '.',
  Slash: '/',
  Backslash: '\\',
  Minus: '-',
  Equal: '=',
  Space: 'Space',
};

const STANDALONE_MODIFIER_CODES: ReadonlySet<string> = new Set([
  'ControlLeft',
  'ControlRight',
  'AltLeft',
  'AltRight',
  'ShiftLeft',
  'ShiftRight',
  'MetaLeft',
  'MetaRight',
]);

const REBIND_PROMPT_TEXT = 'Press key combo to rebind...';
const REBIND_ACTIVE_BG = 'rgba(234, 179, 8, 0.25)';
const REBIND_ACTIVE_COLOR = '#fef08a';
const REBIND_SUCCESS_BG = 'rgba(16, 185, 129, 0.25)';
const REBIND_SUCCESS_COLOR = '#6ee7b7';
const LEGACY_BTN_IDLE_BG = 'rgba(255,255,255,0.08)';
const LEGACY_BTN_IDLE_COLOR = '#a5b4fc';

/** Format a HotkeySpec into its human-readable shortcut label. */
export function formatHotkeyLabel(hk: HotkeySpec | null | undefined): string {
  if (!hk) return '';
  const parts: string[] = [];
  if (hk.ctrlKey) parts.push('Ctrl');
  if (hk.altKey) parts.push('Alt');
  if (hk.shiftKey) parts.push('Shift');

  let codeLabel = hk.code || hk.key || '[';
  if (codeLabel.startsWith('Key')) {
    codeLabel = codeLabel.slice(3);
  } else if (codeLabel.startsWith('Digit')) {
    codeLabel = codeLabel.slice(5);
  } else if (codeLabel in CODE_DISPLAY_LABELS) {
    codeLabel = CODE_DISPLAY_LABELS[codeLabel];
  }
  if (codeLabel !== 'Space') {
    codeLabel = codeLabel.toUpperCase();
  }
  parts.push(codeLabel);
  return parts.join(' + ');
}

/** Check whether a KeyboardEvent matches a configured HotkeySpec. */
export function hotkeyMatchesEvent(
  event: KeyboardEvent,
  hk: HotkeySpec | null | undefined,
): boolean {
  if (!hk) return false;
  return (
    event.code === hk.code &&
    Boolean(event.altKey) === Boolean(hk.altKey) &&
    Boolean(event.ctrlKey) === Boolean(hk.ctrlKey) &&
    Boolean(event.shiftKey) === Boolean(hk.shiftKey)
  );
}

function parseStoredHotkey(raw: string | null, fallback: HotkeySpec): HotkeySpec {
  if (!raw) return { ...fallback };
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed === 'object' && parsed !== null) {
      const candidate = parsed as Partial<Record<keyof HotkeySpec, unknown>>;
      if (typeof candidate.code === 'string' && candidate.code.length > 0) {
        return {
          code: candidate.code,
          key: typeof candidate.key === 'string' ? candidate.key : candidate.code,
          altKey: Boolean(candidate.altKey),
          ctrlKey: Boolean(candidate.ctrlKey),
          shiftKey: Boolean(candidate.shiftKey),
        };
      }
    }
  } catch {
    // Ignore malformed JSON in localStorage and keep the default hotkey.
  }
  return { ...fallback };
}

function loadStoredHotkey(storageKey: string, fallback: HotkeySpec): HotkeySpec {
  try {
    return parseStoredHotkey(localStorage.getItem(storageKey), fallback);
  } catch {
    return { ...fallback };
  }
}

function saveStoredHotkey(storageKey: string, hk: HotkeySpec): void {
  try {
    localStorage.setItem(storageKey, JSON.stringify(hk));
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
}

function isEditableElement(el: Element | null): boolean {
  if (!el) return false;
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) return true;
  return el instanceof HTMLElement && el.isContentEditable;
}

function isSpaceEvent(event: KeyboardEvent): boolean {
  return ['Space', ' '].includes(event.code) || ['Space', ' ', 'Spacebar'].includes(event.key);
}

function readErrorDetail(payload: unknown): string | null {
  if (typeof payload !== 'object' || payload === null) return null;
  const detail = (payload as { readonly detail?: unknown }).detail;
  return typeof detail === 'string' && detail.length > 0 ? detail : null;
}

/**
 * Manage configurable mic/text hotkeys, rebinding UI state, global keyboard
 * shortcut dispatch, and the orator text command overlay.
 */
export function createHotkeyControlsController(
  options: CreateHotkeyControlsOptions,
): HotkeyControlsController {
  const {
    isCurrentOrator,
    getMicMode = () => 'toggle',
    getMicPressSource = () => null,
    beginMicPress,
    releaseMicPress,
    agentStartStopBtn = null,
    agentConnectBtn = null,
    menuItemHotkey = null,
    menuHotkeyDisplay = null,
    howtoMicDisplay = null,
    micHotkeyBtn = null,
    hotkeyDisplay = null,
    menuItemTextHotkey = null,
    menuTextHotkeyDisplay = null,
    howtoTextDisplay = null,
    dismissHotkeyDisplay = null,
    oratorCommandOverlay = null,
    oratorCommandForm = null,
    oratorCommandInput = null,
    oratorCommandStatus = null,
    getTheaterId = () => null,
    stopSceneSpeech,
    isOratorCommandOpen: customIsOratorCommandOpen,
    openOratorCommand: customOpenOratorCommand,
    closeOratorCommand: customCloseOratorCommand,
    onRebindStart,
    capturePlainSpace = true,
  } = options;

  let isRebindingHotkey = false;
  let isRebindingTextHotkey = false;
  let micFlashTimer: ReturnType<typeof setTimeout> | null = null;
  let textFlashTimer: ReturnType<typeof setTimeout> | null = null;

  let currentHotkey: HotkeySpec = loadStoredHotkey(MIC_HOTKEY_STORAGE_KEY, DEFAULT_MIC_HOTKEY);
  let currentTextHotkey: HotkeySpec = loadStoredHotkey(
    TEXT_HOTKEY_STORAGE_KEY,
    DEFAULT_TEXT_HOTKEY,
  );

  function isOratorCommandOpen(): boolean {
    if (customIsOratorCommandOpen) return customIsOratorCommandOpen();
    return Boolean(oratorCommandOverlay?.classList.contains('active'));
  }

  function closeOratorCommand(): void {
    if (customCloseOratorCommand) {
      customCloseOratorCommand();
      return;
    }
    if (!oratorCommandOverlay) return;
    oratorCommandOverlay.classList.remove('active');
    oratorCommandOverlay.setAttribute('aria-hidden', 'true');
    if (oratorCommandInput) oratorCommandInput.value = '';
    if (oratorCommandStatus) oratorCommandStatus.textContent = '';
  }

  function openOratorCommand(): void {
    if (customOpenOratorCommand) {
      customOpenOratorCommand();
      return;
    }
    if (!isCurrentOrator() || !oratorCommandOverlay) return;
    stopSceneSpeech?.();
    oratorCommandOverlay.classList.add('active');
    oratorCommandOverlay.setAttribute('aria-hidden', 'false');
    if (oratorCommandStatus) oratorCommandStatus.textContent = '';
    if (oratorCommandInput) {
      requestAnimationFrame(() => oratorCommandInput.focus());
    }
  }

  function isTextHotkeyEvent(event: KeyboardEvent): boolean {
    return hotkeyMatchesEvent(event, currentTextHotkey);
  }

  function updateHotkeyUI(): void {
    const formatted = formatHotkeyLabel(currentHotkey);
    const label = `Mic Hotkey: ${formatted}`;
    if (hotkeyDisplay) {
      hotkeyDisplay.textContent = label;
    }
    if (menuHotkeyDisplay) {
      menuHotkeyDisplay.textContent = label;
    }
    if (howtoMicDisplay) {
      howtoMicDisplay.textContent = formatted;
    }
  }

  function updateTextHotkeyUI(): void {
    const formatted = formatHotkeyLabel(currentTextHotkey);
    const label = `Text Hotkey: ${formatted}`;
    if (menuTextHotkeyDisplay) {
      menuTextHotkeyDisplay.textContent = label;
    }
    if (howtoTextDisplay) {
      howtoTextDisplay.textContent = formatted;
    }
    if (dismissHotkeyDisplay) {
      dismissHotkeyDisplay.textContent = formatted;
    }
  }

  function clearMicRebindStyles(restoreLegacyBtn: boolean): void {
    if (micFlashTimer !== null) {
      clearTimeout(micFlashTimer);
      micFlashTimer = null;
    }
    if (restoreLegacyBtn && micHotkeyBtn) {
      micHotkeyBtn.style.background = LEGACY_BTN_IDLE_BG;
      micHotkeyBtn.style.color = LEGACY_BTN_IDLE_COLOR;
    }
    if (menuItemHotkey) {
      menuItemHotkey.style.background = '';
      menuItemHotkey.style.color = '';
    }
  }

  function clearTextRebindStyles(): void {
    if (textFlashTimer !== null) {
      clearTimeout(textFlashTimer);
      textFlashTimer = null;
    }
    if (menuItemTextHotkey) {
      menuItemTextHotkey.style.background = '';
      menuItemTextHotkey.style.color = '';
    }
  }

  function cancelMicHotkeyRebind(restoreLegacyBtn = false): void {
    if (!isRebindingHotkey) return;
    isRebindingHotkey = false;
    clearMicRebindStyles(restoreLegacyBtn);
    updateHotkeyUI();
  }

  function cancelTextHotkeyRebind(): void {
    if (!isRebindingTextHotkey) return;
    isRebindingTextHotkey = false;
    clearTextRebindStyles();
    updateTextHotkeyUI();
  }

  function cancelRebinding(): void {
    cancelMicHotkeyRebind(false);
    cancelTextHotkeyRebind();
  }

  function startHotkeyRebind(btnElem: HTMLElement | null, labelElem: HTMLElement | null): void {
    onRebindStart?.();
    if (isRebindingHotkey) return;
    cancelTextHotkeyRebind();
    clearMicRebindStyles(false);
    isRebindingHotkey = true;
    if (btnElem) {
      btnElem.style.background = REBIND_ACTIVE_BG;
      btnElem.style.color = REBIND_ACTIVE_COLOR;
    }
    if (labelElem) {
      labelElem.textContent = REBIND_PROMPT_TEXT;
    }
  }

  function startTextHotkeyRebind(
    btnElem: HTMLElement | null,
    labelElem: HTMLElement | null,
  ): void {
    onRebindStart?.();
    if (isRebindingTextHotkey) return;
    cancelMicHotkeyRebind(false);
    clearTextRebindStyles();
    isRebindingTextHotkey = true;
    if (btnElem) {
      btnElem.style.background = REBIND_ACTIVE_BG;
      btnElem.style.color = REBIND_ACTIVE_COLOR;
    }
    if (labelElem) {
      labelElem.textContent = REBIND_PROMPT_TEXT;
    }
  }

  function setMicHotkey(hk: string | Partial<HotkeySpec>): void {
    if (typeof hk === 'string') {
      currentHotkey = {
        code: hk,
        key: hk,
        altKey: false,
        ctrlKey: false,
        shiftKey: false,
      };
    } else {
      currentHotkey = {
        code: hk.code || DEFAULT_MIC_HOTKEY.code,
        key: hk.key || DEFAULT_MIC_HOTKEY.key,
        altKey: Boolean(hk.altKey),
        ctrlKey: Boolean(hk.ctrlKey),
        shiftKey: Boolean(hk.shiftKey),
      };
    }
    saveStoredHotkey(MIC_HOTKEY_STORAGE_KEY, currentHotkey);
    updateHotkeyUI();
  }

  function setTextHotkey(hk: string | Partial<HotkeySpec>): void {
    if (typeof hk === 'string') {
      currentTextHotkey = {
        code: hk,
        key: hk,
        altKey: false,
        ctrlKey: false,
        shiftKey: false,
      };
    } else {
      currentTextHotkey = {
        code: hk.code || 'Space',
        key: hk.key || ' ',
        altKey: Boolean(hk.altKey),
        ctrlKey: Boolean(hk.ctrlKey),
        shiftKey: Boolean(hk.shiftKey),
      };
    }
    saveStoredHotkey(TEXT_HOTKEY_STORAGE_KEY, currentTextHotkey);
    updateTextHotkeyUI();
  }

  // Plain Space is reserved for typing, never canvas shortcuts or button activation.
  const onPlainSpace = (event: KeyboardEvent): void => {
    if (
      event.code !== 'Space' ||
      event.altKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.metaKey
    ) {
      return;
    }
    const editing = isEditableElement(document.activeElement);
    if (!editing) event.preventDefault();
    event.stopImmediatePropagation();
  };

  const onOratorCommandInputKeydown = (event: KeyboardEvent): void => {
    if (event.key === 'Escape' || isTextHotkeyEvent(event)) {
      event.preventDefault();
      event.stopPropagation();
      closeOratorCommand();
    }
  };

  const onOratorCommandFormSubmit = async (event: SubmitEvent): Promise<void> => {
    event.preventDefault();
    if (!oratorCommandInput) return;
    const text = oratorCommandInput.value.trim();
    if (!text) return;

    oratorCommandInput.disabled = true;
    if (oratorCommandStatus) oratorCommandStatus.textContent = 'Sending…';
    try {
      const theaterId = getTheaterId() ?? '';
      const response = await fetch(
        `/api/orator/command?theater_id=${encodeURIComponent(theaterId)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text }),
        },
      );
      if (!response.ok) {
        const body: unknown = await response.json().catch(() => ({}));
        throw new Error(readErrorDetail(body) ?? 'Narratron could not receive the command.');
      }
      closeOratorCommand();
    } catch (error: unknown) {
      if (oratorCommandStatus) {
        oratorCommandStatus.textContent =
          error instanceof Error ? error.message : 'Narratron could not receive the command.';
      }
    } finally {
      oratorCommandInput.disabled = false;
      if (isOratorCommandOpen()) oratorCommandInput.focus();
    }
  };

  const onMenuItemHotkeyClick = (event: MouseEvent): void => {
    event.stopPropagation();
    startHotkeyRebind(menuItemHotkey, menuHotkeyDisplay);
  };

  const onMenuItemTextHotkeyClick = (event: MouseEvent): void => {
    event.stopPropagation();
    startTextHotkeyRebind(menuItemTextHotkey, menuTextHotkeyDisplay);
  };

  const onMicHotkeyBtnClick = (event: MouseEvent): void => {
    event.stopPropagation();
    startHotkeyRebind(micHotkeyBtn, hotkeyDisplay);
  };

  const onWindowKeydown = (event: KeyboardEvent): void => {
    if (!isCurrentOrator()) return;
    if (agentStartStopBtn && document.activeElement === agentStartStopBtn && isSpaceEvent(event)) {
      event.preventDefault();
      return;
    }
    if (
      getMicMode() === 'push-to-talk' &&
      agentConnectBtn &&
      document.activeElement === agentConnectBtn &&
      (event.code === 'Space' || event.code === 'Enter')
    ) {
      event.preventDefault();
      if (!event.repeat) beginMicPress?.('button-key');
      return;
    }

    if (isRebindingTextHotkey) {
      event.preventDefault();
      event.stopPropagation();

      if (event.code === 'Escape') {
        cancelTextHotkeyRebind();
        return;
      }
      if (STANDALONE_MODIFIER_CODES.has(event.code)) {
        return;
      }

      currentTextHotkey = {
        code: event.code,
        key: event.key,
        altKey: event.altKey,
        ctrlKey: event.ctrlKey,
        shiftKey: event.shiftKey,
      };
      saveStoredHotkey(TEXT_HOTKEY_STORAGE_KEY, currentTextHotkey);

      isRebindingTextHotkey = false;
      clearTextRebindStyles();
      if (menuItemTextHotkey) {
        menuItemTextHotkey.style.background = REBIND_SUCCESS_BG;
        menuItemTextHotkey.style.color = REBIND_SUCCESS_COLOR;
      }
      updateTextHotkeyUI();

      textFlashTimer = setTimeout(() => {
        textFlashTimer = null;
        if (menuItemTextHotkey) {
          menuItemTextHotkey.style.background = '';
          menuItemTextHotkey.style.color = '';
        }
      }, 600);
      return;
    }

    if (isRebindingHotkey) {
      event.preventDefault();
      event.stopPropagation();

      if (event.code === 'Escape') {
        cancelMicHotkeyRebind(true);
        return;
      }
      if (STANDALONE_MODIFIER_CODES.has(event.code)) {
        return;
      }

      currentHotkey = {
        code: event.code,
        key: event.key,
        altKey: event.altKey,
        ctrlKey: event.ctrlKey,
        shiftKey: event.shiftKey,
      };
      saveStoredHotkey(MIC_HOTKEY_STORAGE_KEY, currentHotkey);

      isRebindingHotkey = false;
      clearMicRebindStyles(false);
      if (micHotkeyBtn) {
        micHotkeyBtn.style.background = '#10b981';
        micHotkeyBtn.style.color = '#fff';
      }
      if (menuItemHotkey) {
        menuItemHotkey.style.background = REBIND_SUCCESS_BG;
        menuItemHotkey.style.color = REBIND_SUCCESS_COLOR;
      }
      updateHotkeyUI();

      micFlashTimer = setTimeout(() => {
        micFlashTimer = null;
        if (micHotkeyBtn) {
          micHotkeyBtn.style.background = LEGACY_BTN_IDLE_BG;
          micHotkeyBtn.style.color = LEGACY_BTN_IDLE_COLOR;
        }
        if (menuItemHotkey) {
          menuItemHotkey.style.background = '';
          menuItemHotkey.style.color = '';
        }
      }, 600);
      return;
    }

    if (event.repeat) return;

    if (hotkeyMatchesEvent(event, currentTextHotkey)) {
      event.preventDefault();
      if (isOratorCommandOpen()) {
        closeOratorCommand();
      } else {
        if (isEditableElement(document.activeElement)) {
          return;
        }
        openOratorCommand();
      }
      return;
    }

    if (isOratorCommandOpen()) return;
    if (isEditableElement(document.activeElement)) return;

    if (hotkeyMatchesEvent(event, currentHotkey)) {
      event.preventDefault();
      if (getMicMode() === 'push-to-talk') {
        beginMicPress?.('hotkey');
      } else if (agentConnectBtn && !agentConnectBtn.disabled) {
        agentConnectBtn.click();
      }
    }
  };

  const onWindowKeyup = (event: KeyboardEvent): void => {
    if (agentStartStopBtn && document.activeElement === agentStartStopBtn && isSpaceEvent(event)) {
      event.preventDefault();
      return;
    }
    const pressSource = getMicPressSource();
    if (pressSource === 'button-key' && (event.code === 'Space' || event.code === 'Enter')) {
      event.preventDefault();
      releaseMicPress?.();
    } else if (
      pressSource === 'hotkey' &&
      (event.code === currentHotkey.code ||
        Boolean(event.altKey) !== Boolean(currentHotkey.altKey) ||
        Boolean(event.ctrlKey) !== Boolean(currentHotkey.ctrlKey) ||
        Boolean(event.shiftKey) !== Boolean(currentHotkey.shiftKey))
    ) {
      releaseMicPress?.();
    }
  };

  updateHotkeyUI();
  updateTextHotkeyUI();

  if (capturePlainSpace) {
    window.addEventListener('keydown', onPlainSpace, true);
    window.addEventListener('keyup', onPlainSpace, true);
  }
  oratorCommandInput?.addEventListener('keydown', onOratorCommandInputKeydown);
  oratorCommandForm?.addEventListener('submit', onOratorCommandFormSubmit);
  menuItemHotkey?.addEventListener('click', onMenuItemHotkeyClick);
  menuItemTextHotkey?.addEventListener('click', onMenuItemTextHotkeyClick);
  micHotkeyBtn?.addEventListener('click', onMicHotkeyBtnClick);
  window.addEventListener('keydown', onWindowKeydown);
  window.addEventListener('keyup', onWindowKeyup);

  return {
    getMicHotkey: () => ({ ...currentHotkey }),
    getTextHotkey: () => ({ ...currentTextHotkey }),
    setMicHotkey,
    setTextHotkey,
    isRebindingMicHotkey: () => isRebindingHotkey,
    isRebindingTextHotkey: () => isRebindingTextHotkey,
    cancelRebinding,
    updateHotkeyUI,
    updateTextHotkeyUI,
    isTextHotkeyEvent,
    isOratorCommandOpen,
    openOratorCommand,
    closeOratorCommand,
    destroy: () => {
      clearMicRebindStyles(false);
      clearTextRebindStyles();
      if (capturePlainSpace) {
        window.removeEventListener('keydown', onPlainSpace, true);
        window.removeEventListener('keyup', onPlainSpace, true);
      }
      oratorCommandInput?.removeEventListener('keydown', onOratorCommandInputKeydown);
      oratorCommandForm?.removeEventListener('submit', onOratorCommandFormSubmit);
      menuItemHotkey?.removeEventListener('click', onMenuItemHotkeyClick);
      menuItemTextHotkey?.removeEventListener('click', onMenuItemTextHotkeyClick);
      micHotkeyBtn?.removeEventListener('click', onMicHotkeyBtnClick);
      window.removeEventListener('keydown', onWindowKeydown);
      window.removeEventListener('keyup', onWindowKeyup);
    },
  };
}
