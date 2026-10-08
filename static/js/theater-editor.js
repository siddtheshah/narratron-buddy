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
  let activeGenerations = 0;
  const generatingKeys = new Set();
  let asking = false;
  const history = [];
  const pendingWrites = new Map();
  const expandedFolders = new Map();
  let fileTreeDraftId = null;
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
    if (busy || activeGenerations > 0) {
      if (activeGenerations > 0) status('Please wait for asset generation to finish.');
      return;
    }
    busy = true;
    document.querySelectorAll('main button').forEach(button => { button.disabled = true; });
    document.querySelectorAll('main input:not([type=file]), main textarea, main select').forEach(input => { input.disabled = true; });
    status(progress);
    try { await action(); } catch (error) { status(error.message, true); }
    finally {
      busy = false;
      document.querySelectorAll('main button').forEach(button => { button.disabled = button.dataset.done === 'true' || button.dataset.generating === 'true'; });
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
    el('assistant-send').textContent = `Send · ${rates.theater_editor_assistant_credit_rate} Cr →`;
    el('assistant-cost').textContent = `${rates.theater_editor_assistant_credit_rate} Cr per assistant turn, including shortcuts. Charged when a reply is ready.`;
    el('generation-rates').textContent = 'Image and playlist pricing follows standard live pricing.';
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
    if (fileTreeDraftId !== state.draft.theater_id) {
      expandedFolders.clear();
      fileTreeDraftId = state.draft.theater_id;
    } else {
      list.querySelectorAll('.file-folder').forEach(folder => expandedFolders.set(folder.dataset.path, folder.open));
    }
    list.replaceChildren();
    const root = { folders: new Map(), files: [], count: 0 };
    for (const file of state.files) {
      const parts = file.path.split('/');
      let node = root;
      node.count++;
      for (const name of parts.slice(0, -1)) {
        if (!node.folders.has(name)) node.folders.set(name, { folders: new Map(), files: [], count: 0 });
        node = node.folders.get(name);
        node.count++;
      }
      node.files.push(file);
    }
    if (!root.folders.has('references')) {
      root.folders.set('references', { folders: new Map(), files: [], count: 0 });
    }
    const references = root.folders.get('references');
    if (!references.folders.has('characters')) {
      references.folders.set('characters', { folders: new Map(), files: [], count: 0 });
    }
    function appendFiles(parent, files) {
      for (const file of [...files].sort((a, b) => a.path.localeCompare(b.path))) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'file-button';
        button.dataset.path = file.path;
        button.classList.toggle('active', selected?.path === file.path);
        if (selected?.path === file.path) button.setAttribute('aria-current', 'true');
        button.textContent = `${file.path.startsWith('stamps/') ? '🏷️' : (file.path.startsWith('references/characters/') ? '👤' : (file.kind === 'image' ? '▧' : file.kind === 'audio' ? '♫' : '≡'))} ${file.path.split('/').pop()}`;
        button.title = file.path; button.addEventListener('click', () => selectFile(file)); parent.append(button);
      }
    }
    function appendFolders(parent, node, prefix = '') {
      for (const [name, child] of [...node.folders].sort(([a], [b]) => a.localeCompare(b))) {
        const path = prefix ? `${prefix}/${name}` : name;
        const folder = document.createElement('details'); folder.className = 'file-folder';
        folder.dataset.path = path; folder.open = expandedFolders.get(path) || false;
        const summary = document.createElement('summary'); summary.className = 'folder-button'; summary.title = path;
        const label = document.createElement('span'); label.className = 'folder-name'; label.textContent = name;
        const count = document.createElement('span'); count.className = 'folder-count';
        count.textContent = child.count; count.setAttribute('aria-label', `${child.count} files`);
        summary.append(label, count);
        const contents = document.createElement('div'); contents.className = 'file-children';
        appendFolders(contents, child, path); appendFiles(contents, child.files);
        if (child.files.length === 0 && child.folders.size === 0) {
          const emptyHint = document.createElement('span');
          emptyHint.className = 'empty-folder-hint';
          emptyHint.textContent = path === 'references/characters' ? 'No character portraits yet (references/characters/<Name>/1.png)' : 'Empty folder';
          contents.append(emptyHint);
        }
        folder.append(summary, contents); parent.append(folder);
      }
    }
    if (root.files.length) {
      const section = document.createElement('div'); section.className = 'file-group';
      const heading = document.createElement('h3'); heading.textContent = 'Configuration'; section.append(heading);
      appendFiles(section, root.files);
      list.append(section);
    }
    appendFolders(list, root);
  }
  async function selectFile(file) {
    if (busy) return;
    captureText();
    selected = file;
    const sequence = ++previewSequence;
    el('file-list').querySelectorAll('.file-button').forEach(button => {
      const active = button.dataset.path === file.path;
      button.classList.toggle('active', active);
      if (active) {
        button.setAttribute('aria-current', 'true');
        let parent = button.closest('.file-folder');
        while (parent) {
          parent.open = true;
          expandedFolders.set(parent.dataset.path, true);
          parent = parent.parentElement ? parent.parentElement.closest('.file-folder') : null;
        }
        button.scrollIntoView({ block: 'nearest' });
      } else {
        button.removeAttribute('aria-current');
      }
    });
    el('selected-path').textContent = file.path;
    el('empty-preview').hidden = true;
    el('file-editor').hidden = true;
    el('media-preview').hidden = true;
    const url = `${endpoint('/file')}?path=${encodeURIComponent(file.path)}`;
    el('download-file').href = url; el('download-file').hidden = false;
    const isProtected = file.path === 'theater.yaml';
    el('delete-file').hidden = isProtected;
    el('delete-file').disabled = isProtected || (activeGenerations > 0);
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
    el('delete-file').hidden = true;
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
  function makeOpenLink(path, text = 'Open asset →') {
    const link = document.createElement('a');
    link.className = 'open-asset-link';
    link.href = '#';
    link.textContent = text;
    link.setAttribute('role', 'button');
    link.setAttribute('aria-label', `Open ${path}`);
    const handler = e => {
      e.preventDefault();
      openAsset(path);
    };
    link.addEventListener('click', handler);
    link.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') handler(e);
    });
    return link;
  }
  async function openAsset(path) {
    if (!state || !state.files) return;
    const file = state.files.find(f => f.path === path);
    if (file) {
      await selectFile(file);
    } else {
      status(`Could not find ${path} in this draft.`, true);
    }
  }
  function message(role, text, assetPath = null) {
    const node = document.createElement('div'); node.className = `message ${role}`; node.textContent = text;
    if (assetPath) {
      node.append(' ', makeOpenLink(assetPath));
    }
    el('assistant-messages').append(node); el('assistant-messages').scrollTop = el('assistant-messages').scrollHeight;
  }
  async function ask(prompt) {
    if (!state || !prompt.trim() || busy || asking || activeGenerations > 0) return;
    asking = true;
    el('assistant-send').disabled = true;
    el('assistant-input').disabled = true;
    status('Your assistant is shaping a proposal…');
    try {
      captureText();
      if (dirty()) await save();
      message('user', prompt);
      const result = await post('/assistant', { prompt, history: history.slice(-12) });
      if (result.state) render(result.state);
      history.push({ role: 'user', content: prompt }, { role: 'assistant', content: result.proposal.message });
      proposal = result.proposal; proposalRevision = result.revision;
      message('assistant', proposal.message);
      el('assistant-input').value = '';
      renderProposal();
      await checkAuthStatus({ refresh: true });
      status(`Proposal ready. Charged ${result.credits_charged} credits. Review file changes or generate the suggested assets below.`);
    } catch (error) {
      status(error.message, true);
    } finally {
      asking = false;
      el('assistant-send').disabled = false;
      el('assistant-input').disabled = false;
    }
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
    for (const deletion of proposal.deletions || []) {
      const text = document.createElement('p'); text.className = 'proposal-deletion';
      text.textContent = `🗑 Delete ${deletion}`; container.append(text);
    }
    if (proposal.writes.length || proposal.moves.length || (proposal.deletions && proposal.deletions.length)) {
      const button = document.createElement('button'); button.textContent = 'Apply file changes to draft'; button.type = 'button';
      button.addEventListener('click', () => {
        if (activeGenerations > 0) { status('Please wait for asset generation to finish before applying changes.', true); return; }
        run(async () => {
          await save();
          render(await post('/apply', { revision: proposalRevision, proposal }));
          for (const generation of proposal.generations) {
            generation.references = generation.references.map(path => proposal.moves.find(move => move.source === path)?.destination || path);
          }
          clearPreview(); button.disabled = true; button.dataset.done = 'true';
          status('File changes applied and saved to your draft.');
        }, 'Applying changes…');
      });
      container.append(button);
    }
    for (const generation of proposal.generations) {
      const card = document.createElement('div'); card.className = 'generation-card';
      const kindLabel = generation.kind === 'reference' ? 'Reference' : (generation.kind === 'stamp' ? 'Stamp token' : (generation.kind === 'character' ? 'Character portrait' : 'Playlist track'));
      const label = document.createElement('strong'); label.textContent = `${kindLabel}: ${generation.name}`;
      const description = document.createElement('p'); description.textContent = generation.prompt;
      const button = document.createElement('button'); button.type = 'button';
      const isMusic = generation.kind === 'playlist';
      const key = `${generation.kind}:${generation.name}:${generation.playlist || ''}`;
      if (generation.generated_path) {
        button.textContent = 'Generated';
        button.dataset.done = 'true';
        button.disabled = true;
        const openLink = makeOpenLink(generation.generated_path);
        card.append(label, description, button, openLink);
      } else if (generatingKeys.has(key)) {
        button.textContent = 'Generating…';
        button.dataset.generating = 'true';
        button.disabled = true;
        card.append(label, description, button);
      } else {
        button.textContent = `Generate · ${state.rates[isMusic ? 'music_credit_rate' : 'image_credit_rate']} Cr`;
        button.addEventListener('click', () => generate(generation, button));
        card.append(label, description, button);
      }
      container.append(card);
    }
    if (!proposal.writes.length && !proposal.moves.length && !(proposal.deletions && proposal.deletions.length) && !proposal.generations.length) container.hidden = true;
  }
  async function generate(request, button) {
    if (busy) return;
    const key = `${request.kind}:${request.name}:${request.playlist || ''}`;
    if (generatingKeys.has(key)) return;
    if (button && (button.disabled || button.dataset.generating === 'true')) return;

    generatingKeys.add(key);
    activeGenerations++;
    const isManualSubmit = button && button.id === 'generation-submit';
    if (button) {
      button.disabled = true;
      button.dataset.generating = 'true';
      button.textContent = 'Generating…';
    }

    captureText();
    if (dirty()) {
      try { await save(); } catch (e) { /* ignore */ }
    }
    const revision = state.draft.revision;
    status(activeGenerations > 1 ? `Generating asset (${activeGenerations} in progress)…` : 'Generating your asset. This can take a few minutes…');

    try {
      const result = await post('/generate', { ...request, revision });
      render(result.state);
      // A generated asset adds a new file, so the same proposal remains applicable.
      if (proposalRevision !== null) proposalRevision = state.draft.revision;
      if (request.kind && request.name && proposal?.generations) {
        const match = proposal.generations.find(g => g.name === request.name && g.kind === request.kind);
        if (match) match.generated_path = result.path;
      }
      if (button) {
        delete button.dataset.generating;
        if (isManualSubmit) {
          button.disabled = false;
          updateGenerationCost();
        } else {
          button.textContent = 'Generated';
          button.dataset.done = 'true';
          button.disabled = true;
          const openLink = makeOpenLink(result.path);
          button.insertAdjacentElement('afterend', openLink);
        }
      }
      await checkAuthStatus({ refresh: true });
      message('assistant', `Created ${result.path}. Charged ${result.credits_charged} credits.`, result.path);
      history.push({ role: 'assistant', content: `Created asset: ${result.path}` });
      status(`Saved ${result.path} to your draft.`);
    } catch (error) {
      status(error.message, true);
      if (button) {
        delete button.dataset.generating;
        button.disabled = false;
        if (isManualSubmit) {
          updateGenerationCost();
        } else {
          const isMusic = request.kind === 'playlist';
          button.textContent = `Generate · ${state.rates[isMusic ? 'music_credit_rate' : 'image_credit_rate']} Cr`;
        }
      }
    } finally {
      generatingKeys.delete(key);
      activeGenerations = Math.max(0, activeGenerations - 1);
      if (selected) {
        const isProtected = selected.path === 'theater.yaml';
        el('delete-file').disabled = isProtected || (activeGenerations > 0);
      }
    }
  }
  function updateGenerationCost() {
    if (state && el('generation-submit') && el('generation-submit').dataset.generating !== 'true') {
      const isMusic = el('generation-kind').value === 'playlist';
      el('generation-submit').textContent = `Generate · ${state.rates[isMusic ? 'music_credit_rate' : 'image_credit_rate']} Cr`;
    }
  }
  function openGoogleDialog(focusDoc = false) {
    el('google-link-url').value = '';
    el('google-link-name').value = '';
    el('google-harvest-prompt').value = '';
    el('google-doc-harvest').checked = true;
    el('google-doc-options').hidden = !focusDoc;
    el('google-link-submit').textContent = focusDoc ? 'Harvest with AI' : 'Import Link';
    el('google-link-dialog').showModal();
    el('google-link-url').focus();
  }
  function updateGoogleDialogState() {
    const url = el('google-link-url').value.trim();
    const isDoc = url.includes('docs.google.com/document');
    el('google-doc-options').hidden = !isDoc;
    if (isDoc) {
      el('google-link-submit').textContent = el('google-doc-harvest').checked ? 'Harvest with AI' : 'Import Lore File';
    } else {
      el('google-link-submit').textContent = 'Import from Drive';
    }
  }
  async function submitGoogleLink() {
    const url = el('google-link-url').value.trim();
    if (!url) return;
    const targetName = el('google-link-name').value.trim() || undefined;
    const isDoc = url.includes('docs.google.com/document');
    const harvest = isDoc && el('google-doc-harvest').checked;
    const harvestPrompt = el('google-harvest-prompt').value.trim() || undefined;

    el('google-link-dialog').close();
    let importedFile = null;
    await run(async () => {
      await save();
      const res = await post('/google-link', {
        revision: state.draft.revision,
        url,
        target_name: targetName,
        harvest,
        harvest_prompt: harvestPrompt,
      });

      if (res.path) {
        const parts = res.path.split('/');
        let cur = '';
        for (const part of parts.slice(0, -1)) {
          cur = cur ? `${cur}/${part}` : part;
          expandedFolders.set(cur, true);
        }
      }

      if (res.state) render(res.state);

      if (res.harvested && res.proposal) {
        proposal = res.proposal;
        proposalRevision = res.revision;
        message('assistant', proposal.message);
        renderProposal();
        await checkAuthStatus({ refresh: true });
        status(`Harvested Google Doc into your theater draft. Charged ${res.credits_charged} credits.`);
      } else {
        importedFile = state.files.find(f => f.path === res.path);
        status(res.message || `Imported ${res.path} from Google.`);
      }
    }, harvest ? 'Harvesting Google Doc with AI co-creator…' : 'Downloading and importing from Google…');
    if (importedFile) {
      await selectFile(importedFile);
    }
  }
  async function initialize() {
    if (opening) return;
    opening = true;
    try {
      const auth = await getAuthState();
      if (!auth.authenticated) { el('login-gate').hidden = false; el('workspace').hidden = true; el('start-panel').hidden = true; return; }
      el('login-gate').hidden = true;
      lastDraftKey = `narratron.builder.lastDraft:${auth.user.id}`;
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
  el('download-draft').addEventListener('click', () => run(async () => {
    await save();
    const link = document.createElement('a');
    link.href = endpoint('/download');
    link.download = `${state.draft.name || state.draft.theater_id}.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    status('Draft theater downloaded.');
  }, 'Preparing draft theater download…'));
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
  el('organize-assets').addEventListener('click', () => ask('Organize my uploaded assets into meaningful reference subfolders, references/characters/ folders, stamps/ tokens, lore documents, and named playlists. Update all file references where necessary.'));
  el('suggest-world').addEventListener('click', () => ask('Develop this theater into a coherent world using its existing assets and lore. Propose an opening scene, characters (with lore dossiers and references/characters/ portraits), reference images, stamp tokens for tactical play, and atmospheric playlist tracks.'));
  el('harvest-doc-shortcut').addEventListener('click', () => openGoogleDialog(true));
  el('import-google-link').addEventListener('click', () => openGoogleDialog(false));
  el('google-link-cancel').addEventListener('click', () => el('google-link-dialog').close());
  el('google-link-url').addEventListener('input', updateGoogleDialogState);
  el('google-doc-harvest').addEventListener('change', updateGoogleDialogState);
  el('google-link-form').addEventListener('submit', event => { event.preventDefault(); submitGoogleLink(); });
  el('generation-kind').addEventListener('change', updateGenerationCost);
  el('generation-form').addEventListener('submit', event => {
    event.preventDefault();
    if (el('generation-submit').disabled || el('generation-submit').dataset.generating === 'true') return;
    generate({
      kind: el('generation-kind').value,
      name: el('generation-name').value,
      playlist: el('generation-playlist').value || 'ambient',
      prompt: el('generation-prompt').value,
      references: []
    }, el('generation-submit'));
  });
  el('new-file').addEventListener('click', () => {
    el('new-file-path').value = '';
    el('new-file-dialog').showModal();
    el('new-file-path').focus();
  });
  el('new-file-cancel').addEventListener('click', () => el('new-file-dialog').close('cancel'));
  el('new-file-dialog').addEventListener('click', event => {
    if (event.target === el('new-file-dialog')) el('new-file-dialog').close('cancel');
  });
  el('new-file-dialog').addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      el('new-file-dialog').close('cancel');
    }
  });
  el('new-file-dialog').addEventListener('close', () => {
    if (el('new-file-dialog').returnValue !== 'create') return;
    const path = el('new-file-path').value.trim();
    if (state.files.some(file => file.path === path)) { status('That file already exists. Select it to edit.', true); return; }
    run(async () => {
      await save(); render(await post('/save', { revision: state.draft.revision, name: state.draft.name, writes: [{ path, content: path.endsWith('.yaml') || path.endsWith('.json') ? '{}\n' : '' }] }));
      status(`Created ${path}. Select it to begin writing.`);
    }, 'Creating file…');
  });
  async function performDelete(targetPath) {
    if (!targetPath || targetPath === 'theater.yaml') return;
    await run(async () => {
      pendingWrites.delete(targetPath);
      if (selected?.path === targetPath) textDirty = false;
      await save();
      const next = await post('/delete', { revision: state.draft.revision, path: targetPath });
      clearPreview();
      render(next);
      status(`Deleted ${targetPath}.`);
    }, `Deleting ${targetPath}…`);
  }
  el('delete-file').addEventListener('click', () => {
    if (!selected || selected.path === 'theater.yaml') return;
    el('delete-file-prompt').textContent = `Are you sure you want to delete "${selected.path}" from this draft? This cannot be undone.`;
    el('delete-file-dialog').showModal();
  });
  el('delete-file-cancel').addEventListener('click', () => el('delete-file-dialog').close());
  el('delete-file-dialog').querySelector('form').addEventListener('submit', event => {
    event.preventDefault();
    el('delete-file-dialog').close();
    if (selected && selected.path !== 'theater.yaml') {
      performDelete(selected.path);
    }
  });
  async function performResetTheater() {
    if (!state) return;
    await run(async () => {
      const next = await post('/reset', { revision: state.draft.revision });
      render(next);
      status('Theater reset.');
    }, 'Resetting theater…');
  }
  const resetBtn = el('reset-theater');
  const resetDialog = el('reset-theater-dialog');
  const resetCancel = el('reset-theater-cancel');
  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      if (!state) return;
      if (resetDialog && typeof resetDialog.showModal === 'function') {
        resetDialog.showModal();
      } else if (confirm('Are you sure you want to reset this theater? All generated output and theater state will be permanently deleted.')) {
        performResetTheater();
      }
    });
  }
  if (resetCancel && resetDialog) {
    resetCancel.addEventListener('click', () => resetDialog.close());
  }
  if (resetDialog) {
    resetDialog.addEventListener('click', event => {
      if (event.target === resetDialog) resetDialog.close();
    });
    resetDialog.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        resetDialog.close();
      }
    });
    const form = resetDialog.querySelector('form');
    if (form) {
      form.addEventListener('submit', event => {
        event.preventDefault();
        resetDialog.close();
        performResetTheater();
      });
    }
  }
  window.addEventListener('beforeunload', event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('narratron:auth-changed', event => {
    if (!event.detail.authenticated) { state = null; lastDraftKey = null; activeGenerations = 0; generatingKeys.clear(); asking = false; pendingWrites.clear(); textDirty = false; nameDirty = false; clearPreview(); history.length = 0; el('assistant-messages').replaceChildren(); el('assistant-proposal').replaceChildren(); el('workspace').hidden = true; el('start-panel').hidden = true; el('login-gate').hidden = false; }
  });
  window.onAuthSuccess = mode => { if (mode !== 'logout') initialize(); };
  window.beforeCreditPurchase = async () => {
    if (busy || activeGenerations > 0) throw new Error('Please wait for the current action to finish before purchasing credits.');
    await save();
  };
  initialize();
})();
