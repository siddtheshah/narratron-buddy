/* Theater drafts are server owned. Keep previews and paid generation explicit. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  let state = null;
  let selected = null;
  let busy = false;
  let textDirty = false;
  let nameDirty = false;
  let opening = false;
  let previewSequence = 0;
  let proposal = null;
  let proposalRevision = null;
  const history = [];
  const pendingWrites = new Map();
  let lastDraftKey = null;

  function status(message, error = false) {
    el('builder-status').textContent = message;
    el('builder-status').classList.toggle('error', error);
  }
  function dirty() { return textDirty || nameDirty || pendingWrites.size > 0; }
  function updateSaveState() {
    el('draft-state').textContent = dirty() ? 'Unsaved changes' : 'Saved draft';
    el('file-save-state').textContent = dirty() ? 'Save your edits to keep them in this draft.' : 'Changes stay in your draft until you deploy.';
  }
  function captureText() {
    if (selected?.kind === 'text' && textDirty) pendingWrites.set(selected.path, el('file-editor').value);
    textDirty = false;
  }
  async function api(path, options = {}) {
    const res = await fetch(path, options);
    const data = await res.json();
    if (!res.ok) {
      if (res.status === 401) { el('workspace').hidden = true; el('login-gate').hidden = false; }
      throw new Error(typeof data.detail === 'string' ? data.detail : 'The request could not be completed. Check your inputs.');
    }
    return data;
  }
  function endpoint(action = '') { return `/api/theater-editor/${encodeURIComponent(state.draft.theater_id)}${action}`; }
  function post(action, body) { return api(endpoint(action), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); }
  async function run(action, progress) {
    if (busy) return;
    busy = true;
    document.querySelectorAll('main button').forEach(button => { button.disabled = true; });
    document.querySelectorAll('main input:not([type=file]), main textarea, main select').forEach(input => { input.disabled = true; });
    status(progress);
    try { await action(); } catch (error) { status(error.message, true); }
    finally {
      busy = false;
      document.querySelectorAll('main button').forEach(button => { button.disabled = button.dataset.done === 'true'; });
      document.querySelectorAll('main input:not([type=file]), main textarea, main select').forEach(input => { input.disabled = false; });
    }
  }
  function render(next) {
    state = next;
    el('start-panel').hidden = true;
    el('login-gate').hidden = true;
    el('workspace').hidden = false;
    el('draft-name').value = state.draft.name;
    el('file-count').textContent = `${state.files.length} files`;
    const rates = state.rates;
    el('generation-rates').textContent = `References: ${rates.image_credit_rate} Cr / image · Playlists: ${rates.music_credit_rate} Cr / track. Same rates as live generation.`;
    updateGenerationCost();
    if (lastDraftKey) localStorage.setItem(lastDraftKey, state.draft.theater_id);
    const url = new URL(location.href);
    url.searchParams.set('theater_id', state.draft.theater_id);
    window.history.replaceState(null, '', url);
    renderFiles();
    updateSaveState();
  }
  function renderFiles() {
    const list = el('file-list');
    list.replaceChildren();
    const groups = ['Configuration', 'lore', 'references', 'playlists'];
    for (const group of groups) {
      const files = state.files.filter(file => group === 'Configuration' ? !file.path.includes('/') : file.path.startsWith(`${group}/`));
      if (!files.length) continue;
      const section = document.createElement('div'); section.className = 'file-group';
      const heading = document.createElement('h3'); heading.textContent = group; section.append(heading);
      for (const file of files) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'file-button';
        button.classList.toggle('active', selected?.path === file.path);
        button.textContent = `${file.kind === 'image' ? '▧' : file.kind === 'audio' ? '♫' : '≡'} ${group === 'Configuration' ? file.path : file.path.slice(group.length + 1)}`;
        button.title = file.path; button.addEventListener('click', () => selectFile(file)); section.append(button);
      }
      list.append(section);
    }
  }
  async function selectFile(file) {
    if (busy) return;
    captureText();
    selected = file;
    const sequence = ++previewSequence;
    renderFiles();
    el('selected-path').textContent = file.path;
    el('empty-preview').hidden = true;
    el('file-editor').hidden = true;
    el('media-preview').hidden = true;
    const url = `${endpoint('/file')}?path=${encodeURIComponent(file.path)}`;
    el('download-file').href = url; el('download-file').hidden = false;
    if (file.kind === 'text') {
      try {
        let content = pendingWrites.get(file.path);
        if (content === undefined) {
          const res = await fetch(url);
          if (!res.ok) throw new Error('Could not load this file.');
          content = await res.text();
        }
        if (sequence !== previewSequence) return;
        el('file-editor').value = content; el('file-editor').hidden = false;
      } catch (error) { status(error.message, true); }
    } else {
      const media = document.createElement(file.kind === 'image' ? 'img' : 'audio');
      media.src = url;
      if (file.kind === 'image') media.alt = file.path;
      else media.controls = true;
      el('media-preview').replaceChildren(media); el('media-preview').hidden = false;
    }
    updateSaveState();
  }
  async function save() {
    captureText();
    if (!dirty()) return;
    const next = await post('/save', { revision: state.draft.revision, name: el('draft-name').value.trim(), writes: [...pendingWrites].map(([path, content]) => ({ path, content })) });
    pendingWrites.clear(); nameDirty = false; textDirty = false;
    render(next);
  }
  function clearPreview() {
    selected = null; ++previewSequence;
    el('selected-path').textContent = 'Explore your theater'; el('empty-preview').hidden = false;
    el('file-editor').hidden = true; el('media-preview').hidden = true; el('download-file').hidden = true;
    el('media-preview').replaceChildren();
  }
  async function load(identifier) {
    const next = await api(`/api/theater-editor/${encodeURIComponent(identifier)}`);
    pendingWrites.clear(); textDirty = false; nameDirty = false; proposal = null;
    el('assistant-proposal').hidden = true; clearPreview(); render(next);
    status('Draft loaded. Select a file or ask your assistant to begin.');
  }
  async function create(populateDefault = true) {
    clearPreview(); history.length = 0; proposal = null;
    el('assistant-proposal').hidden = true;
    render(await api('/api/theater-editor/drafts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: el('new-name').value.trim() || 'My Theater', populate_default: populateDefault }) }));
  }
  async function upload(files, folder) {
    if (!files.length) return;
    await run(async () => {
      if (!state) await create(!folder);
      await save();
      const form = new FormData();
      form.append('revision', state.draft.revision); form.append('folder', folder);
      for (const file of files) form.append('files', file, folder ? file.webkitRelativePath : file.name);
      render(await api(endpoint('/upload'), { method: 'POST', body: form }));
      clearPreview();
      status(`Uploaded ${files.length} files. Ask the assistant to organize them or develop your world.`);
    }, 'Uploading theater assets…');
  }
  function message(role, text) {
    const node = document.createElement('div'); node.className = `message ${role}`; node.textContent = text;
    el('assistant-messages').append(node); el('assistant-messages').scrollTop = el('assistant-messages').scrollHeight;
  }
  async function ask(prompt) {
    if (!state || !prompt.trim()) return;
    await run(async () => {
      await save();
      message('user', prompt);
      const result = await post('/assistant', { prompt, history: history.slice(-12) });
      history.push({ role: 'user', content: prompt }, { role: 'assistant', content: result.proposal.message });
      proposal = result.proposal; proposalRevision = result.revision;
      message('assistant', proposal.message);
      el('assistant-input').value = '';
      renderProposal();
      status('Proposal ready. Review file changes or generate the suggested assets below.');
    }, 'Your assistant is shaping a proposal…');
  }
  function renderProposal() {
    const container = el('assistant-proposal'); container.replaceChildren();
    container.hidden = false;
    const title = document.createElement('strong'); title.textContent = 'Review proposed changes'; container.append(title);
    for (const write of proposal.writes) {
      const details = document.createElement('details'); const summary = document.createElement('summary');
      summary.textContent = `Edit ${write.path}`; const pre = document.createElement('pre'); pre.textContent = write.content;
      details.append(summary, pre); container.append(details);
    }
    for (const move of proposal.moves) {
      const text = document.createElement('p'); text.textContent = `${move.source} → ${move.destination}`; container.append(text);
    }
    if (proposal.writes.length || proposal.moves.length) {
      const button = document.createElement('button'); button.textContent = 'Apply file changes to draft'; button.type = 'button';
      button.addEventListener('click', () => run(async () => {
        await save();
        render(await post('/apply', { revision: proposalRevision, proposal }));
        for (const generation of proposal.generations) {
          generation.references = generation.references.map(path => proposal.moves.find(move => move.source === path)?.destination || path);
        }
        clearPreview(); button.disabled = true; button.dataset.done = 'true';
        status('File changes applied and saved to your draft.');
      }, 'Applying changes…'));
      container.append(button);
    }
    for (const generation of proposal.generations) {
      const card = document.createElement('div'); card.className = 'generation-card';
      const label = document.createElement('strong'); label.textContent = `${generation.kind === 'reference' ? 'Reference' : 'Playlist track'}: ${generation.name}`;
      const description = document.createElement('p'); description.textContent = generation.prompt;
      const button = document.createElement('button'); button.type = 'button';
      button.textContent = `Generate · ${state.rates[generation.kind === 'reference' ? 'image_credit_rate' : 'music_credit_rate']} Cr`;
      button.addEventListener('click', () => generate(generation, button)); card.append(label, description, button); container.append(card);
    }
    if (!proposal.writes.length && !proposal.moves.length && !proposal.generations.length) container.hidden = true;
  }
  async function generate(request, button) {
    await run(async () => {
      await save();
      const revision = state.draft.revision;
      const result = await post('/generate', { ...request, revision }); render(result.state);
      // A generated asset adds a new file, so the same proposal remains applicable.
      if (proposalRevision === revision) proposalRevision = state.draft.revision;
      if (button) { button.textContent = 'Generated'; button.dataset.done = 'true'; }
      el('credit-balance').textContent = `${result.credits.toFixed(2)} credits · Top up →`;
      await checkAuthStatus({ refresh: true });
      message('assistant', `Created ${result.path}. Charged ${result.credits_charged} credits.`);
      history.push({ role: 'assistant', content: `Created asset: ${result.path}` });
      status(`Saved ${result.path} to your draft.`);
    }, 'Generating your asset. This can take a few minutes…');
  }
  function updateGenerationCost() {
    if (state) el('generation-submit').textContent = `Generate · ${state.rates[el('generation-kind').value === 'reference' ? 'image_credit_rate' : 'music_credit_rate']} Cr`;
  }
  async function initialize() {
    if (opening) return;
    opening = true;
    try {
      const auth = await getAuthState();
      if (!auth.authenticated) { el('login-gate').hidden = false; el('workspace').hidden = true; el('start-panel').hidden = true; return; }
      el('login-gate').hidden = true;
      lastDraftKey = `narratron.builder.lastDraft:${auth.user.id}`;
      el('credit-balance').textContent = `${Number(auth.user.credits || 0).toFixed(2)} credits · Top up →`;
      const identifier = new URLSearchParams(location.search).get('theater_id');
      if (identifier) await run(() => load(identifier), 'Loading your theater…');
      else {
        el('start-panel').hidden = false;
        const previous = localStorage.getItem(lastDraftKey);
        el('resume-draft').hidden = !previous;
        el('resume-draft').dataset.identifier = previous || '';
      }
    } catch (error) { status(error.message, true); }
    finally { opening = false; }
  }
  el('start-default').addEventListener('click', () => run(async () => { await create(); status('Default theater ready. Make it your own.'); }, 'Preparing your theater…'));
  el('resume-draft').addEventListener('click', () => run(() => load(el('resume-draft').dataset.identifier), 'Opening your last saved draft…'));
  el('start-folder').addEventListener('click', () => el('folder-input').click());
  el('upload-folder').addEventListener('click', () => el('folder-input').click());
  el('upload-assets').addEventListener('click', () => el('assets-input').click());
  el('folder-input').addEventListener('change', event => { const files = [...event.target.files]; event.target.value = ''; upload(files, true); });
  el('assets-input').addEventListener('change', event => { const files = [...event.target.files]; event.target.value = ''; upload(files, false); });
  el('file-editor').addEventListener('input', () => { textDirty = true; updateSaveState(); });
  el('draft-name').addEventListener('input', () => { nameDirty = true; updateSaveState(); });
  el('save-draft').addEventListener('click', () => run(async () => { await save(); status('Draft saved.'); }, 'Saving draft…'));
  el('reload-draft').addEventListener('click', () => { if (!dirty() || confirm('Discard unsaved edits and reload the saved draft?')) run(() => load(state.draft.theater_id), 'Reloading draft…'); });
  el('deploy-draft').addEventListener('click', () => run(async () => {
    await save(); const result = await post('/deploy', { revision: state.draft.revision });
    status('Deployed. Opening your canvas…'); location.assign(result.canvas_url);
  }, 'Deploying your theater…'));
  el('new-draft').addEventListener('click', () => {
    if (dirty() && !confirm('Leave unsaved edits and start another theater?')) return;
    state = null; pendingWrites.clear(); textDirty = false; nameDirty = false;
    el('workspace').hidden = true; el('start-panel').hidden = false;
    window.history.replaceState(null, '', '/theater-editor'); status('');
  });
  el('assistant-form').addEventListener('submit', event => { event.preventDefault(); ask(el('assistant-input').value.trim()); });
  el('organize-assets').addEventListener('click', () => ask('Organize my uploaded assets into meaningful reference subfolders, lore documents, and named playlists. Update all file references where necessary.'));
  el('suggest-world').addEventListener('click', () => ask('Develop this theater into a coherent world using its existing assets and lore. Propose an opening scene, characters, reference images, and atmospheric playlist tracks.'));
  el('generation-kind').addEventListener('change', updateGenerationCost);
  el('generation-form').addEventListener('submit', event => {
    event.preventDefault(); generate({ kind: el('generation-kind').value, name: el('generation-name').value, playlist: el('generation-playlist').value || 'ambient', prompt: el('generation-prompt').value, references: [] });
  });
  el('new-file').addEventListener('click', () => el('new-file-dialog').showModal());
  el('new-file-dialog').addEventListener('close', () => {
    if (el('new-file-dialog').returnValue !== 'create') return;
    const path = el('new-file-path').value.trim();
    if (state.files.some(file => file.path === path)) { status('That file already exists. Select it to edit.', true); return; }
    run(async () => {
      await save(); render(await post('/save', { revision: state.draft.revision, name: state.draft.name, writes: [{ path, content: path.endsWith('.yaml') || path.endsWith('.json') ? '{}\n' : '' }] }));
      status(`Created ${path}. Select it to begin writing.`);
    }, 'Creating file…');
  });
  window.addEventListener('beforeunload', event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('narratron:auth-changed', event => {
    if (!event.detail.authenticated) { state = null; lastDraftKey = null; pendingWrites.clear(); textDirty = false; nameDirty = false; clearPreview(); history.length = 0; el('assistant-messages').replaceChildren(); el('assistant-proposal').replaceChildren(); el('workspace').hidden = true; el('start-panel').hidden = true; el('login-gate').hidden = false; }
  });
  window.onAuthSuccess = mode => { if (mode !== 'logout') initialize(); };
  initialize();
})();
