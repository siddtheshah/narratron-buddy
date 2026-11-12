const COLORS: readonly string[] = [
  '#fbbf24',
  '#60a5fa',
  '#f472b6',
  '#34d399',
  '#c4b5fd',
  '#fb923c',
  '#22d3ee',
  '#f87171',
];

export interface BookmarkAnimationInfo {
  readonly scene_prompt?: string | null;
}

export interface BookmarkHistoryItem {
  readonly path?: string | null;
  readonly url?: string | null;
  readonly name?: string | null;
  readonly prompt?: string | null;
  readonly time?: number | null;
  readonly animation?: BookmarkAnimationInfo | string | null;
}

export interface InitializeCanvasBookmarksOptions {
  readonly theaterId: string;
  readonly isOrator: () => boolean;
  readonly getHistory: () => readonly BookmarkHistoryItem[];
  readonly getIndex: () => number;
  readonly navigate: (index: number) => Promise<void>;
}

export interface CanvasBookmarksController {
  refresh(): void;
}

interface StoredBookmark {
  readonly key: string;
  readonly number: number;
}

interface ResolvedBookmark extends StoredBookmark {
  readonly index: number;
}

// Use media identity and creation time: repeated presentations of the same image
// are separate history entries, while appending history does not move bookmarks.
function historyKey(item: BookmarkHistoryItem): string {
  return JSON.stringify([item.path || item.url || item.animation, item.time || 0]);
}

function imageName(item: BookmarkHistoryItem): string {
  const pathStr = (item.path || item.url || '').split(/[?#]/)[0] ?? '';
  const rawFilename = pathStr.split(/[\\/]/).pop() ?? '';
  const filename = rawFilename.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ');
  const animationPrompt =
    typeof item.animation === 'object' && item.animation !== null
      ? item.animation.scene_prompt
      : undefined;
  const name = (item.name || filename || item.prompt || animationPrompt || 'Untitled image').trim();
  return name.length > 36 ? `${name.slice(0, 33).trimEnd()}…` : name;
}

function isStoredBookmark(entry: unknown): entry is StoredBookmark {
  if (typeof entry !== 'object' || entry === null) {
    return false;
  }
  const candidate = entry as { key?: unknown; number?: unknown };
  return (
    typeof candidate.key === 'string' &&
    typeof candidate.number === 'number' &&
    Number.isSafeInteger(candidate.number) &&
    candidate.number > 0
  );
}

function parseStoredBookmarks(raw: string | null): StoredBookmark[] | null {
  const saved: unknown = JSON.parse(raw || '[]');
  if (!Array.isArray(saved)) {
    return null;
  }
  return saved.filter(isStoredBookmark);
}

function bookmarkColor(number: number): string {
  return COLORS[(number - 1) % COLORS.length] ?? '#cbd5e1';
}

/** Initialize the orator canvas bookmark controls and localStorage persistence. */
export function initializeCanvasBookmarks({
  theaterId,
  isOrator,
  getHistory,
  getIndex,
  navigate,
}: InitializeCanvasBookmarksOptions): CanvasBookmarksController {
  const root = document.getElementById('canvas-bookmarks');
  if (!root) {
    return { refresh() {} };
  }

  root.innerHTML = `
        <button id="canvas-bookmark-toggle" type="button" aria-label="Bookmark current page"
            aria-pressed="false" aria-expanded="false" aria-controls="canvas-bookmark-panel">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M6 4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v17l-6-4-6 4z"/>
            </svg><span class="bookmark-current-number" aria-hidden="true"></span>
        </button>
        <div id="canvas-bookmark-panel" aria-label="Bookmarked pages" hidden>
            <div class="bookmark-panel-title">Bookmarks</div><div class="bookmark-list"></div>
        </div>
        <span class="bookmark-flash" aria-hidden="true"></span>
        <span class="bookmark-status" role="status" aria-live="polite"></span>`;

  const toggleEl = root.querySelector<HTMLButtonElement>('button');
  const panelEl = root.querySelector<HTMLDivElement>('#canvas-bookmark-panel');
  const listEl = root.querySelector<HTMLDivElement>('.bookmark-list');
  const flashEl = root.querySelector<HTMLSpanElement>('.bookmark-flash');
  const statusEl = root.querySelector<HTMLSpanElement>('.bookmark-status');
  const currentNumberEl = root.querySelector<HTMLSpanElement>('.bookmark-current-number');

  if (!toggleEl || !panelEl || !listEl || !flashEl || !statusEl || !currentNumberEl) {
    throw new Error('Canvas bookmarks DOM failed to initialize');
  }

  const toggle = toggleEl;
  const panel = panelEl;
  const list = listEl;
  const flash = flashEl;
  const status = statusEl;
  const currentNumber = currentNumberEl;

  const storageKey = `narratron-canvas-bookmarks:${theaterId || 'default'}`;
  let bookmarks: StoredBookmark[] = [];
  try {
    const parsed = parseStoredBookmarks(localStorage.getItem(storageKey));
    if (parsed) {
      bookmarks = parsed;
    }
  } catch (error) {
    console.warn('Could not load canvas bookmarks', error);
  }

  let signature = '';
  let busy = false;
  let flashTimer: number | undefined;

  function expand(open: boolean): void {
    panel.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
  }

  function refresh(): void {
    root!.hidden = !isOrator();
    if (root!.hidden) {
      expand(false);
    }
    const history = getHistory();
    const currentIndex = getIndex();
    const current = history[currentIndex];
    const selected = current
      ? bookmarks.find((entry) => entry.key === historyKey(current))
      : undefined;

    toggle.disabled = !current || busy;
    toggle.setAttribute('aria-pressed', String(Boolean(selected)));
    toggle.setAttribute(
      'aria-label',
      selected ? `Remove bookmark ${selected.number}` : 'Bookmark current page',
    );
    toggle.title = selected
      ? `Remove bookmark ${selected.number} · Hover to browse`
      : 'Bookmark current page · Hover to browse';
    toggle.style.setProperty(
      '--bookmark-color',
      selected ? bookmarkColor(selected.number) : '#cbd5e1',
    );
    currentNumber.textContent = selected ? String(selected.number) : '';

    const entries: ResolvedBookmark[] = bookmarks
      .map((entry) => ({
        ...entry,
        index: history.findIndex((item) => historyKey(item) === entry.key),
      }))
      .filter((entry) => entry.index >= 0);

    const nextSignature = JSON.stringify([
      entries,
      currentIndex,
      busy,
      entries.map((entry) => imageName(history[entry.index]!)),
    ]);
    if (signature === nextSignature) {
      return;
    }
    signature = nextSignature;

    list.replaceChildren();
    if (!entries.length) {
      const empty = document.createElement('p');
      empty.className = 'bookmark-empty';
      empty.textContent = 'Click the bookmark to save this page.';
      list.append(empty);
    }

    for (const entry of entries) {
      const historyItem = history[entry.index]!;
      const labelText = imageName(historyItem);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'bookmark-item';
      button.disabled = busy;
      button.setAttribute('aria-current', String(entry.index === currentIndex));
      button.title = labelText;

      const numberEl = document.createElement('span');
      numberEl.className = 'bookmark-number';
      numberEl.style.setProperty('--bookmark-color', bookmarkColor(entry.number));
      numberEl.textContent = String(entry.number);

      const labelEl = document.createElement('span');
      labelEl.className = 'bookmark-name';
      labelEl.textContent = labelText;

      button.append(numberEl, labelEl);
      button.addEventListener('click', async () => {
        if (!isOrator() || busy) {
          return;
        }
        busy = true;
        let navigated = false;
        refresh();
        try {
          // Resolve against the latest history, including any pages added since hover.
          const index = getHistory().findIndex((item) => historyKey(item) === entry.key);
          if (index < 0) {
            throw new Error('This bookmarked page is no longer available.');
          }
          await navigate(index);
          navigated = true;
        } catch (error) {
          status.textContent = 'Could not open bookmark. Try again.';
          console.error('Could not navigate to bookmark', error);
        } finally {
          busy = false;
          refresh();
          if (navigated) {
            toggle.focus();
            expand(false);
          }
        }
      });
      list.append(button);
    }
  }

  toggle.addEventListener('click', () => {
    const item = getHistory()[getIndex()];
    if (!isOrator() || !item || busy) {
      return;
    }
    const key = historyKey(item);
    const existing = bookmarks.find((entry) => entry.key === key);
    let number = existing?.number ?? 1;
    if (existing) {
      bookmarks = bookmarks.filter((entry) => entry.key !== key);
    } else {
      while (bookmarks.some((entry) => entry.number === number)) {
        number++;
      }
      bookmarks.push({ key, number });
      bookmarks.sort((a, b) => a.number - b.number);
    }

    try {
      localStorage.setItem(storageKey, JSON.stringify(bookmarks));
    } catch (error) {
      console.warn('Could not save canvas bookmarks', error);
    }

    refresh();
    status.textContent = `Bookmark ${number} ${existing ? 'removed' : 'added'}.`;
    flash.textContent = String(number);
    flash.style.setProperty('--bookmark-color', bookmarkColor(number));
    flash.classList.remove('flashing');
    void flash.offsetWidth;
    flash.classList.add('flashing');
    window.clearTimeout(flashTimer);
    flashTimer = window.setTimeout(() => flash.classList.remove('flashing'), 1000);
  });

  root.addEventListener('mouseenter', () => expand(true));
  root.addEventListener('mouseleave', () => expand(false));
  root.addEventListener('focusin', () => expand(true));
  root.addEventListener('focusout', (event: FocusEvent) => {
    const related = event.relatedTarget;
    if (!(related instanceof Node) || !root.contains(related)) {
      expand(false);
    }
  });
  root.addEventListener('keydown', (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      toggle.focus();
      expand(false);
      event.stopPropagation();
    }
    if (event.key === 'ArrowDown' && event.target === toggle) {
      event.preventDefault();
      expand(true);
      list.querySelector<HTMLButtonElement>('button')?.focus();
    }
  });

  window.addEventListener('storage', (event: StorageEvent) => {
    if (event.key !== storageKey) {
      return;
    }
    try {
      const parsed = parseStoredBookmarks(event.newValue);
      if (parsed) {
        bookmarks = parsed;
      }
      refresh();
    } catch (error) {
      console.warn('Could not sync canvas bookmarks', error);
    }
  });

  refresh();
  return { refresh };
}
