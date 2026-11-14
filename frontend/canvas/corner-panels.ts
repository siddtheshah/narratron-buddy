export interface SuggestionItem {
  readonly title?: string;
  readonly category?: string;
  readonly description?: string;
}

export interface SuggestionsApiResponse {
  readonly elements_fingerprint?: string;
  readonly suggestions?: readonly SuggestionItem[];
}

export interface StickyNoteItem {
  readonly topic?: string;
  readonly name?: string;
  readonly info?: string;
  readonly content?: string;
  readonly hidden?: boolean;
  readonly is_hidden?: boolean;
}

export type HiddenStickyEntry = string | { readonly topic?: string; readonly name?: string };

export interface StickyNotesApiResponse {
  readonly sticky_notes?: readonly StickyNoteItem[];
  readonly hidden_stickies?: readonly HiddenStickyEntry[];
}

export interface CreateCornerPanelsOptions {
  readonly getTheaterId?: () => string | null;
  readonly suggestionsButton?: HTMLElement | null;
  readonly suggestionsList?: HTMLElement | null;
  readonly suggestionsLoading?: HTMLElement | null;
  readonly stickyNotesButton?: HTMLElement | null;
  readonly stickyNotesList?: HTMLElement | null;
  readonly stickyNotesLoading?: HTMLElement | null;
  readonly stickyNotesBadge?: HTMLElement | null;
  readonly hideCanvasTextButton?: HTMLButtonElement | null;
  readonly imageContainer?: HTMLElement | null;
  readonly initialFetchStickyNotes?: boolean;
}

export interface CornerPanelsController {
  readonly STICKIES_REVEAL_CACHE_TIMEOUT_MS: number;
  isStickiesRevealedInCache(tid: string | null | undefined): boolean;
  setStickiesRevealedInCache(tid: string | null | undefined, revealed: boolean): void;
  updateCanvasStickyNotes(
    notes?: readonly StickyNoteItem[] | null,
    hidden?: readonly HiddenStickyEntry[] | null,
  ): void;
  triggerStickyNotesFetch(): Promise<void>;
  triggerSuggestionsFetch(): Promise<void>;
  setCanvasTextHidden(hidden: boolean, persist?: boolean): void;
  destroy(): void;
}

interface CanvasTheaterGlobals extends Window {
  currenttheaterId?: string | null;
  theaterId?: string | null;
}

interface StickiesRevealCacheEntry {
  readonly revealed?: boolean;
  readonly revealedAt?: number;
  readonly expiresAt?: number;
}

export const STICKIES_REVEAL_CACHE_TIMEOUT_MS = 60 * 60 * 1000;
export const HIDE_CANVAS_TEXT_STORAGE_KEY = 'narratron-hide-canvas-text';

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function getStickiesRevealedCacheKey(tid: string | null | undefined): string {
  return `narratron_stickies_revealed:${tid || 'default'}`;
}

function isStickiesRevealedInCache(tid: string | null | undefined): boolean {
  try {
    const key = getStickiesRevealedCacheKey(tid);
    const raw = localStorage.getItem(key);
    if (!raw) return false;
    const parsed: unknown = JSON.parse(raw);
    if (parsed !== null && typeof parsed === 'object') {
      const entry = parsed as StickiesRevealCacheEntry;
      if (typeof entry.expiresAt === 'number' && Date.now() > entry.expiresAt) {
        localStorage.removeItem(key);
        return false;
      }
      return Boolean(entry.revealed);
    }
    if (typeof parsed === 'number') {
      if (Date.now() > parsed) {
        localStorage.removeItem(key);
        return false;
      }
      return true;
    }
  } catch {
    // Ignore malformed or restricted storage entries.
  }
  return false;
}

function setStickiesRevealedInCache(tid: string | null | undefined, revealed: boolean): void {
  try {
    const key = getStickiesRevealedCacheKey(tid);
    if (!revealed) {
      localStorage.removeItem(key);
      return;
    }
    const now = Date.now();
    const payload = {
      revealed: true,
      revealedAt: now,
      expiresAt: now + STICKIES_REVEAL_CACHE_TIMEOUT_MS,
    };
    localStorage.setItem(key, JSON.stringify(payload));
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
}

function resolveTheaterId(getTheaterId?: () => string | null): string | null {
  try {
    const sp = new URLSearchParams(window.location.search);
    const tid = sp.get('theater_id');
    if (tid) return tid;
  } catch {
    // Ignore malformed search params.
  }
  const provided = getTheaterId?.();
  if (provided) return provided;
  if (typeof window !== 'undefined') {
    const globals = window as CanvasTheaterGlobals;
    if (globals.currenttheaterId) return globals.currenttheaterId;
    if (globals.theaterId) return globals.theaterId;
  }
  try {
    const pathParts = window.location.pathname.split('/').filter(Boolean);
    if (pathParts.length >= 2 && pathParts[0] === 'theaters') {
      return pathParts[1];
    }
  } catch {
    // Ignore malformed location pathname.
  }
  return null;
}

function hiddenEntryName(entry: HiddenStickyEntry): string {
  if (typeof entry === 'object' && entry !== null) {
    return String(entry.topic || entry.name || '');
  }
  return String(entry);
}

function isHiddenStickyItem(item: StickyNoteItem | null | undefined, hiddenSet: ReadonlySet<string>): boolean {
  if (!item) return false;
  if (item.hidden === true || item.is_hidden === true) return true;
  const rawTopic = String(item.topic || item.name || '').trim().toLowerCase();
  if (!rawTopic) return false;
  if (hiddenSet.has(rawTopic)) return true;
  const normalized = rawTopic.replace(/[_\s-]+/g, ' ');
  for (const h of hiddenSet) {
    if (h.replace(/[_\s-]+/g, ' ') === normalized) return true;
  }
  return false;
}

function areNotesEqual(
  a: readonly StickyNoteItem[] | null,
  b: readonly StickyNoteItem[] | null,
): boolean {
  if (a === b) return true;
  if (!Array.isArray(a) || !Array.isArray(b)) return false;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = b[i];
    if (!x || !y) return false;
    if ((x.topic || x.name) !== (y.topic || y.name)) return false;
    if ((x.info || x.content) !== (y.info || y.content)) return false;
    if (Boolean(x.hidden || x.is_hidden) !== Boolean(y.hidden || y.is_hidden)) return false;
  }
  return true;
}

function areHiddenEqual(
  a: readonly HiddenStickyEntry[] | null,
  b: readonly HiddenStickyEntry[] | null,
): boolean {
  if (a === b) return true;
  if (!Array.isArray(a) || !Array.isArray(b)) return false;
  if (a.length !== b.length) return false;
  const sA = new Set(a.map((x) => hiddenEntryName(x).trim().toLowerCase()));
  const sB = new Set(b.map((x) => hiddenEntryName(x).trim().toLowerCase()));
  if (sA.size !== sB.size) return false;
  for (const item of sA) {
    if (!sB.has(item)) return false;
  }
  return true;
}

/**
 * Initialize the canvas corner overlay panels: Scene Suggestions, Story Sticky Notes,
 * and the Hide Canvas Text toggle button.
 */
export function createCornerPanels(
  options: CreateCornerPanelsOptions = {},
): CornerPanelsController {
  const {
    getTheaterId,
    suggestionsButton,
    suggestionsList,
    suggestionsLoading,
    stickyNotesButton,
    stickyNotesList,
    stickyNotesLoading,
    stickyNotesBadge,
    hideCanvasTextButton,
    imageContainer,
    initialFetchStickyNotes = true,
  } = options;

  const cleanups: Array<() => void> = [];

  // 1. Scene Suggestions panel
  let cachedSuggestions: readonly SuggestionItem[] | null = null;
  let cachedFingerprint: string | null = null;
  let isFetchingSuggestions = false;

  function renderSuggestions(items: readonly SuggestionItem[]): void {
    if (!suggestionsList) return;
    if (items.length === 0) {
      suggestionsList.innerHTML =
        '<div style="font-size: 0.78rem; color: #94a3b8;">No suggestions available right now.</div>';
      return;
    }
    suggestionsList.innerHTML = items
      .map(
        (item) => `
          <div class="suggestion-card">
              <div class="suggestion-card-header">
                  <span class="suggestion-card-title">${escapeHtml(item.title || '')}</span>
                  <span class="suggestion-card-badge">${escapeHtml(item.category || 'Idea')}</span>
              </div>
              <div class="suggestion-card-desc">${escapeHtml(item.description || '')}</div>
          </div>
      `,
      )
      .join('');
  }

  async function triggerSuggestionsFetch(): Promise<void> {
    if (!suggestionsButton || !suggestionsList || !suggestionsLoading) return;
    if (isFetchingSuggestions) return;
    isFetchingSuggestions = true;

    if (!cachedSuggestions) {
      suggestionsLoading.style.display = 'flex';
      suggestionsList.style.display = 'none';
    }

    try {
      const currentTheaterId = resolveTheaterId(getTheaterId);
      if (!currentTheaterId) {
        console.warn('Cannot fetch suggestions: theater_id missing.');
        if (!cachedSuggestions) {
          suggestionsList.innerHTML =
            '<div style="font-size: 0.78rem; color: #cbd5e1;">Launch a theater to view suggestions.</div>';
        }
        return;
      }

      const response = await fetch(`/theaters/${encodeURIComponent(currentTheaterId)}/suggestions`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = (await response.json()) as SuggestionsApiResponse;

      const newFingerprint = data.elements_fingerprint || '';
      const newSuggestions = Array.isArray(data.suggestions) ? data.suggestions : [];

      // Keep suggestions cached until elements update (fingerprint changes)
      if (!cachedSuggestions || newFingerprint !== cachedFingerprint) {
        cachedSuggestions = newSuggestions;
        cachedFingerprint = newFingerprint;
      }
      renderSuggestions(cachedSuggestions);
    } catch (err) {
      console.error('Failed to load suggestions:', err);
      if (!cachedSuggestions) {
        suggestionsList.innerHTML =
          '<div style="font-size: 0.78rem; color: #ef4444;">Unable to generate suggestions at this time.</div>';
      }
    } finally {
      isFetchingSuggestions = false;
      suggestionsLoading.style.display = 'none';
      suggestionsList.style.display = 'flex';
    }
  }

  if (suggestionsButton && suggestionsList && suggestionsLoading) {
    const stopPointer = (e: Event): void => e.stopPropagation();
    const onEnter = (): void => {
      suggestionsButton.classList.add('expanded');
      void triggerSuggestionsFetch();
    };
    const onLeave = (): void => {
      suggestionsButton.classList.remove('expanded');
    };
    suggestionsButton.addEventListener('pointerdown', stopPointer);
    suggestionsButton.addEventListener('mousedown', stopPointer);
    suggestionsButton.addEventListener('mouseenter', onEnter);
    suggestionsButton.addEventListener('mouseleave', onLeave);
    cleanups.push(() => {
      suggestionsButton.removeEventListener('pointerdown', stopPointer);
      suggestionsButton.removeEventListener('mousedown', stopPointer);
      suggestionsButton.removeEventListener('mouseenter', onEnter);
      suggestionsButton.removeEventListener('mouseleave', onLeave);
    });
  }

  // 2. Story Planning Sticky Notes panel
  let cachedNotes: readonly StickyNoteItem[] | null = null;
  let cachedHidden: readonly HiddenStickyEntry[] = [];
  let hiddenRevealed = false;
  let isFetchingStickies = false;
  let isStickiesPinned = false;
  let leaveTimeout: number | null = null;

  function isHiddenRevealed(): boolean {
    const tid = resolveTheaterId(getTheaterId);
    if (isStickiesRevealedInCache(tid)) {
      return true;
    }
    return Boolean(hiddenRevealed);
  }

  function renderStickyNotes(
    items?: readonly StickyNoteItem[] | null,
    hiddenList?: readonly HiddenStickyEntry[] | null,
  ): void {
    if (!stickyNotesList) return;
    if (Array.isArray(items)) {
      cachedNotes = items;
    }
    if (Array.isArray(hiddenList) && hiddenList.length > 0) {
      cachedHidden = hiddenList;
    }

    const notes = cachedNotes || [];
    const hiddenSet = new Set(
      (cachedHidden || [])
        .map((h) => hiddenEntryName(h).trim().toLowerCase())
        .filter(Boolean),
    );

    const visibleItems: StickyNoteItem[] = [];
    const hiddenItems: StickyNoteItem[] = [];

    for (const item of notes) {
      if (isHiddenStickyItem(item, hiddenSet)) {
        hiddenItems.push(item);
      } else {
        visibleItems.push(item);
      }
    }

    const isRevealed = isHiddenRevealed();

    if (stickyNotesBadge) {
      if (notes.length > 0) {
        const countText = isRevealed
          ? String(notes.length)
          : visibleItems.length > 0
            ? String(visibleItems.length)
            : hiddenItems.length > 0
              ? `${hiddenItems.length} 🔒`
              : '0';
        stickyNotesBadge.textContent = countText;
        stickyNotesBadge.style.display = 'inline-block';
      } else {
        stickyNotesBadge.style.display = 'none';
      }
    }

    if (notes.length === 0) {
      stickyNotesList.innerHTML =
        '<div style="font-size: 0.78rem; color: #94a3b8;">No sticky notes active for this scene.</div>';
      return;
    }

    let html = '';

    if (visibleItems.length === 0 && hiddenItems.length > 0) {
      html +=
        '<div style="font-size: 0.78rem; color: #94a3b8; font-style: italic;">No standard sticky notes active.</div>';
    } else {
      html += visibleItems
        .map(
          (item) => `
          <div class="sticky-note-card">
              <div class="sticky-note-card-header">
                  <span class="sticky-note-card-topic">${escapeHtml(item.topic || item.name || '')}</span>
              </div>
              <div class="sticky-note-card-info">${escapeHtml(item.info || item.content || '')}</div>
          </div>
      `,
        )
        .join('');
    }

    if (hiddenItems.length > 0) {
      const count = hiddenItems.length;
      const label = isRevealed
        ? `🙈 Hide ${count} hidden sticky${count > 1 ? ' notes' : ' note'}`
        : `👁️ Show ${count} hidden sticky${count > 1 ? ' notes' : ' note'}`;
      const btnClass = isRevealed
        ? 'sticky-notes-hidden-toggle-btn revealed'
        : 'sticky-notes-hidden-toggle-btn';
      const containerDisplay = isRevealed ? 'flex' : 'none';

      html += `
          <div class="sticky-notes-hidden-section">
              <button type="button" id="sticky-notes-hidden-toggle-btn" class="${btnClass}" title="Toggle reveal of hidden stickies">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      ${
                        isRevealed
                          ? '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>'
                          : '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>'
                      }
                  </svg>
                  <span class="sticky-notes-hidden-toggle-text">${label}</span>
              </button>
              <div id="sticky-notes-hidden-container" class="sticky-notes-hidden-container" style="display: ${containerDisplay};">
                  ${hiddenItems
                    .map(
                      (item) => `
                      <div class="sticky-note-card is-hidden-sticky">
                          <div class="sticky-note-card-header">
                              <span class="sticky-note-card-topic">
                                  ${escapeHtml(item.topic || item.name || '')}
                                  <span class="sticky-note-hidden-badge">Hidden</span>
                              </span>
                          </div>
                          <div class="sticky-note-card-info">${escapeHtml(item.info || item.content || '')}</div>
                      </div>
                  `,
                    )
                    .join('')}
              </div>
          </div>
      `;
    }

    stickyNotesList.innerHTML = html;

    const toggleBtn = stickyNotesList.querySelector('#sticky-notes-hidden-toggle-btn');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        e.preventDefault();
        const nextRevealed = !isHiddenRevealed();
        hiddenRevealed = nextRevealed;
        setStickiesRevealedInCache(resolveTheaterId(getTheaterId), nextRevealed);
        renderStickyNotes(cachedNotes, cachedHidden);
        if (nextRevealed) {
          const hiddenContainer = stickyNotesList.querySelector('#sticky-notes-hidden-container');
          if (hiddenContainer) {
            hiddenContainer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          }
        }
      });
    }
  }

  async function triggerStickyNotesFetch(): Promise<void> {
    if (!stickyNotesButton || !stickyNotesList || !stickyNotesLoading) return;
    if (isFetchingStickies) return;
    isFetchingStickies = true;

    if (!cachedNotes) {
      stickyNotesLoading.style.display = 'flex';
      stickyNotesList.style.display = 'none';
    }

    try {
      const currentTheaterId = resolveTheaterId(getTheaterId);
      if (!currentTheaterId) {
        return;
      }

      let joinKey = '';
      try {
        joinKey = new URLSearchParams(window.location.search).get('join_key') || '';
      } catch {
        // Ignore malformed search params.
      }
      const query = joinKey ? `?join_key=${encodeURIComponent(joinKey)}` : '';
      let response: Response | null = null;
      try {
        response = await fetch(`/theaters/${encodeURIComponent(currentTheaterId)}/sticky-notes${query}`);
      } catch {
        response = null;
      }

      if (!response || !response.ok) {
        try {
          const apiQuery = `theater_id=${encodeURIComponent(currentTheaterId)}${joinKey ? `&join_key=${encodeURIComponent(joinKey)}` : ''}`;
          response = await fetch(`/api/sticky-notes?${apiQuery}`);
        } catch {
          response = null;
        }
      }

      if (response && response.ok) {
        const data = (await response.json()) as StickyNotesApiResponse;
        let notesChanged = false;
        let hiddenChanged = false;
        if (Array.isArray(data.sticky_notes)) {
          if (!areNotesEqual(cachedNotes, data.sticky_notes)) {
            cachedNotes = data.sticky_notes;
            notesChanged = true;
          }
        }
        if (Array.isArray(data.hidden_stickies) && data.hidden_stickies.length > 0) {
          if (!areHiddenEqual(cachedHidden, data.hidden_stickies)) {
            cachedHidden = data.hidden_stickies;
            hiddenChanged = true;
          }
        }
        if (notesChanged || hiddenChanged || !stickyNotesList.hasChildNodes()) {
          renderStickyNotes(cachedNotes, cachedHidden);
        }
      }
    } catch (err) {
      console.error('Failed to load sticky notes:', err);
      if (!cachedNotes) {
        stickyNotesList.innerHTML =
          '<div style="font-size: 0.78rem; color: #ef4444;">Unable to load sticky notes at this time.</div>';
      }
    } finally {
      isFetchingStickies = false;
      stickyNotesLoading.style.display = 'none';
      stickyNotesList.style.display = 'flex';
    }
  }

  function updateCanvasStickyNotes(
    notes?: readonly StickyNoteItem[] | null,
    hidden?: readonly HiddenStickyEntry[] | null,
  ): void {
    if (!stickyNotesList) return;
    let notesChanged = false;
    let hiddenChanged = false;

    if (Array.isArray(notes)) {
      if (!areNotesEqual(cachedNotes, notes)) {
        cachedNotes = notes;
        notesChanged = true;
      }
    }
    if (Array.isArray(hidden) && hidden.length > 0) {
      if (!areHiddenEqual(cachedHidden, hidden)) {
        cachedHidden = hidden;
        hiddenChanged = true;
      }
    }

    if (notesChanged || hiddenChanged || !stickyNotesList.hasChildNodes()) {
      renderStickyNotes(cachedNotes, cachedHidden);
    }
  }

  if (stickyNotesButton && stickyNotesList && stickyNotesLoading) {
    const stopPointer = (e: Event): void => e.stopPropagation();
    const onEnter = (): void => {
      if (leaveTimeout !== null) {
        window.clearTimeout(leaveTimeout);
        leaveTimeout = null;
      }
      stickyNotesButton.classList.add('expanded');
      void triggerStickyNotesFetch();
    };
    const onLeave = (): void => {
      if (!isStickiesPinned) {
        if (leaveTimeout !== null) window.clearTimeout(leaveTimeout);
        leaveTimeout = window.setTimeout(() => {
          if (!isStickiesPinned) {
            stickyNotesButton.classList.remove('expanded');
          }
          leaveTimeout = null;
        }, 250);
      }
    };
    const onClick = (e: MouseEvent): void => {
      if (
        e.target instanceof Element &&
        (e.target.closest('#sticky-notes-hidden-toggle-btn') ||
          e.target.closest('.sticky-note-card'))
      ) {
        return;
      }
      isStickiesPinned = !isStickiesPinned;
      stickyNotesButton.classList.toggle('expanded', isStickiesPinned);
      if (isStickiesPinned) {
        void triggerStickyNotesFetch();
      }
    };

    stickyNotesButton.addEventListener('pointerdown', stopPointer);
    stickyNotesButton.addEventListener('mousedown', stopPointer);
    stickyNotesButton.addEventListener('mouseenter', onEnter);
    stickyNotesButton.addEventListener('mouseleave', onLeave);
    stickyNotesButton.addEventListener('click', onClick);
    cleanups.push(() => {
      if (leaveTimeout !== null) {
        window.clearTimeout(leaveTimeout);
        leaveTimeout = null;
      }
      stickyNotesButton.removeEventListener('pointerdown', stopPointer);
      stickyNotesButton.removeEventListener('mousedown', stopPointer);
      stickyNotesButton.removeEventListener('mouseenter', onEnter);
      stickyNotesButton.removeEventListener('mouseleave', onLeave);
      stickyNotesButton.removeEventListener('click', onClick);
    });

    if (initialFetchStickyNotes) {
      void triggerStickyNotesFetch();
    }
  }

  // 3. Hide All Text On Canvas button
  let isCanvasTextHidden = false;

  function setCanvasTextHidden(hidden: boolean, persist = true): void {
    if (!hideCanvasTextButton || !imageContainer) return;
    isCanvasTextHidden = Boolean(hidden);
    imageContainer.classList.toggle('hide-canvas-text', isCanvasTextHidden);
    hideCanvasTextButton.classList.toggle('active', isCanvasTextHidden);
    hideCanvasTextButton.setAttribute('aria-pressed', String(isCanvasTextHidden));
    const label = isCanvasTextHidden ? 'Show all text on canvas' : 'Hide all text on canvas';
    hideCanvasTextButton.title = label;
    hideCanvasTextButton.setAttribute('aria-label', label);

    const visibleIcon = hideCanvasTextButton.querySelector('.hide-text-icon-visible');
    const hiddenIcon = hideCanvasTextButton.querySelector('.hide-text-icon-hidden');
    if (visibleIcon instanceof HTMLElement || visibleIcon instanceof SVGElement) {
      visibleIcon.style.display = isCanvasTextHidden ? 'none' : 'block';
    }
    if (hiddenIcon instanceof HTMLElement || hiddenIcon instanceof SVGElement) {
      hiddenIcon.style.display = isCanvasTextHidden ? 'block' : 'none';
    }

    if (persist) {
      try {
        localStorage.setItem(HIDE_CANVAS_TEXT_STORAGE_KEY, String(isCanvasTextHidden));
      } catch {
        // Storage can be unavailable in private or restricted browser contexts.
      }
    }
  }

  if (hideCanvasTextButton && imageContainer) {
    const stopPointer = (e: Event): void => e.stopPropagation();
    const onClick = (e: MouseEvent): void => {
      e.stopPropagation();
      setCanvasTextHidden(!isCanvasTextHidden);
    };
    hideCanvasTextButton.addEventListener('pointerdown', stopPointer);
    hideCanvasTextButton.addEventListener('mousedown', stopPointer);
    hideCanvasTextButton.addEventListener('click', onClick);
    cleanups.push(() => {
      hideCanvasTextButton.removeEventListener('pointerdown', stopPointer);
      hideCanvasTextButton.removeEventListener('mousedown', stopPointer);
      hideCanvasTextButton.removeEventListener('click', onClick);
    });

    try {
      if (localStorage.getItem(HIDE_CANVAS_TEXT_STORAGE_KEY) === 'true') {
        setCanvasTextHidden(true, false);
      }
    } catch {
      // Ignore storage access errors.
    }
  }

  return {
    STICKIES_REVEAL_CACHE_TIMEOUT_MS,
    isStickiesRevealedInCache,
    setStickiesRevealedInCache,
    updateCanvasStickyNotes,
    triggerStickyNotesFetch,
    triggerSuggestionsFetch,
    setCanvasTextHidden,
    destroy: () => {
      cleanups.forEach((cleanup) => cleanup());
      cleanups.length = 0;
    },
  };
}
