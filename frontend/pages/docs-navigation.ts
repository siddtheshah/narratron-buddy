/** Documentation sidebar search and `/` keyboard shortcut. */

export interface DocsSearchMatch {
  readonly href: string;
  readonly title: string;
  readonly page_title: string;
  readonly excerpt: string;
}

interface DocsSearchResponse {
  readonly results?: readonly DocsSearchMatch[];
}

export interface DocsNavigationController {
  destroy(): void;
}

export function initializeDocsNavigation(): DocsNavigationController | null {
  const rawInput = document.getElementById('docsSearch');
  const rawResults = document.getElementById('docsSearchResults');
  if (!(rawInput instanceof HTMLInputElement) || !rawResults) {
    return null;
  }

  const input = rawInput;
  const results = rawResults;
  let debounceTimer: number | undefined;
  let requestController: AbortController | undefined;

  function showMessage(message: string): void {
    results.replaceChildren();
    const status = document.createElement('span');
    status.className = 'docs-search-empty';
    status.textContent = message;
    results.append(status);
  }

  function renderResults(matches: readonly DocsSearchMatch[]): void {
    results.replaceChildren();
    if (!matches.length) {
      showMessage('No matching docs');
      return;
    }
    matches.forEach((match) => {
      const link = document.createElement('a');
      link.href = match.href;

      const title = document.createElement('strong');
      title.textContent = match.title;
      link.append(title);

      if (match.page_title !== match.title) {
        const page = document.createElement('small');
        page.textContent = match.page_title;
        link.append(page);
      }

      const excerpt = document.createElement('span');
      excerpt.textContent = match.excerpt;
      link.append(excerpt);
      results.append(link);
    });
  }

  async function updateResults(): Promise<void> {
    const query = input.value.trim();
    if (!query) {
      results.replaceChildren();
      return;
    }
    requestController?.abort();
    requestController = new AbortController();
    showMessage('Searching…');
    try {
      const params = new URLSearchParams({ q: query, limit: '8' });
      const response = await fetch(`/api/docs/search?${params}`, {
        headers: { Accept: 'application/json' },
        signal: requestController.signal,
      });
      if (!response.ok) throw new Error(`Search returned ${response.status}`);
      const payload = (await response.json()) as DocsSearchResponse;
      if (input.value.trim() === query) {
        renderResults(payload.results ?? []);
      }
    } catch (error: unknown) {
      const isAbort =
        (error instanceof Error && error.name === 'AbortError') ||
        (typeof DOMException !== 'undefined' &&
          error instanceof DOMException &&
          error.name === 'AbortError');
      if (!isAbort) {
        showMessage('Search is temporarily unavailable');
      }
    }
  }

  const onInput = (): void => {
    window.clearTimeout(debounceTimer);
    if (!input.value.trim()) {
      requestController?.abort();
      results.replaceChildren();
      return;
    }
    debounceTimer = window.setTimeout(() => {
      void updateResults();
    }, 140);
  };

  const onKeyDown = (event: KeyboardEvent): void => {
    const active = document.activeElement;
    if (
      event.key === '/' &&
      !event.metaKey &&
      !event.ctrlKey &&
      active !== input &&
      !(active && ['INPUT', 'TEXTAREA'].includes(active.tagName))
    ) {
      event.preventDefault();
      input.focus();
    }
  };

  input.addEventListener('input', onInput);
  document.addEventListener('keydown', onKeyDown);

  return {
    destroy(): void {
      window.clearTimeout(debounceTimer);
      requestController?.abort();
      input.removeEventListener('input', onInput);
      document.removeEventListener('keydown', onKeyDown);
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initializeDocsNavigation();
  });
} else {
  initializeDocsNavigation();
}
