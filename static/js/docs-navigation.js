(() => {
  const documents = [
    { title: 'Docs home', href: '/docs', terms: 'documentation home index overview' },
    { title: 'About Narratron', href: '/docs/about', terms: 'overview project purpose live canvas storytelling' },
    { title: 'Ideas & recipes', href: '/docs/ideas', terms: 'inspiration stories performances shared worlds music imagery effects' },
    { title: 'theater.yaml reference', href: '/docs/theater-yaml', terms: 'configuration narrator tools animation music image generation story planning yaml' },
    { title: 'Writing adventures', href: '/docs/writing-adventures', terms: 'author custom narrative adventure create test distribute featured' },
    { title: 'Beyond20 dice rolls', href: '/docs/beyond20', terms: 'dnd beyond integration canvas chat dice rolls' },
    { title: 'Terms of Service', href: '/docs/terms', terms: 'terms use content generation credits policies' },
    { title: 'Privacy Policy', href: '/docs/privacy', terms: 'privacy account data canvas clips permissions' },
  ];

  const input = document.getElementById('docsSearch');
  const results = document.getElementById('docsSearchResults');
  if (!input || !results) return;

  function updateResults() {
    const query = input.value.trim().toLowerCase();
    if (!query) { results.replaceChildren(); return; }
    const matches = documents.filter((doc) => `${doc.title} ${doc.terms}`.toLowerCase().includes(query));
    results.replaceChildren();
    if (!matches.length) {
      const empty = document.createElement('span');
      empty.className = 'docs-search-empty';
      empty.textContent = 'No matching docs';
      results.append(empty);
      return;
    }
    matches.forEach((doc) => {
      const link = document.createElement('a');
      link.href = doc.href;
      link.textContent = doc.title;
      results.append(link);
    });
  }

  input.addEventListener('input', updateResults);
  document.addEventListener('keydown', (event) => {
    if (event.key === '/' && !event.metaKey && !event.ctrlKey && document.activeElement !== input && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
      event.preventDefault();
      input.focus();
    }
  });
})();
