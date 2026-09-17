(() => {
  const input = document.getElementById('docsSearch');
  const results = document.getElementById('docsSearchResults');
  if (!input || !results) return;

  let debounceTimer;
  let requestController;

  function showMessage(message) {
    results.replaceChildren();
    const status = document.createElement('span');
    status.className = 'docs-search-empty';
    status.textContent = message;
    results.append(status);
  }

  function renderResults(matches) {
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

  async function updateResults() {
    const query = input.value.trim();
    if (!query) { results.replaceChildren(); return; }
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
      const payload = await response.json();
      if (input.value.trim() === query) renderResults(payload.results || []);
    } catch (error) {
      if (error.name !== 'AbortError') showMessage('Search is temporarily unavailable');
    }
  }

  input.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    if (!input.value.trim()) {
      requestController?.abort();
      results.replaceChildren();
      return;
    }
    debounceTimer = setTimeout(updateResults, 140);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === '/' && !event.metaKey && !event.ctrlKey && document.activeElement !== input && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
      event.preventDefault();
      input.focus();
    }
  });
})();
