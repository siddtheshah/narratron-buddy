/* Editor and canvas share a server checkout. Keep durable saves and paid generation explicit. */
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
  let draggedFilePath = null;
  let dragHoverTimer = null;

  function status(message, error = false) {
    el('builder-status').textContent = message;
    el('builder-status').classList.toggle('error', error);
  }
  function dirty() { return textDirty || nameDirty || pendingWrites.size > 0; }
  function unsaved() { return dirty() || (state && state.draft.revision !== state.draft.saved_revision); }
  function updateSaveState() {
    el('draft-state').textContent = unsaved() ? 'Unsaved changes' : 'Saved theater';
    el('file-save-state').textContent = dirty() ? 'Save your edits before leaving.' : unsaved()
      ? 'Changes are in your working theater. Save theater to keep them.' : 'Theater saved. Editor and canvas share the same working copy.';
  }
  function captureText() {
    if ((selected?.kind === 'text' || selected?.path.toLowerCase().endsWith('.svg')) && textDirty) pendingWrites.set(selected.path, el('file-editor').value);
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
    el('generation-rates').textContent = 'Image, playlist, and animation pricing follows standard live pricing.';
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
    if (!references.folders.has('scenes')) {
      references.folders.set('scenes', { folders: new Map(), files: [], count: 0 });
    }
    function appendFiles(parent, files) {
      for (const file of [...files].sort((a, b) => a.path.localeCompare(b.path))) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'file-button';
        button.dataset.path = file.path;
        button.classList.toggle('active', selected?.path === file.path);
        if (selected?.path === file.path) button.setAttribute('aria-current', 'true');
        button.textContent = `${file.path.startsWith('stamps/') ? '🏷️' : (file.path.startsWith('references/characters/') ? '👤' : (file.path.startsWith('references/scenes/') ? '🏞️' : (file.kind === 'image' || file.path.toLowerCase().endsWith('.svg') ? '▧' : file.kind === 'audio' ? '♫' : '≡')))} ${file.path.split('/').pop()}`;
        button.title = file.path; button.addEventListener('click', () => selectFile(file));
        if (file.path !== 'theater.yaml') {
          button.draggable = true;
          button.addEventListener('dragstart', event => {
            if (busy || activeGenerations > 0) {
              event.preventDefault();
              return;
            }
            draggedFilePath = file.path;
            event.dataTransfer.setData('text/plain', file.path);
            event.dataTransfer.effectAllowed = 'move';
            button.classList.add('dragging');
          });
          button.addEventListener('dragend', () => {
            draggedFilePath = null;
            button.classList.remove('dragging');
            document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
            if (dragHoverTimer) {
              clearTimeout(dragHoverTimer);
              dragHoverTimer = null;
            }
          });
        }
        parent.append(button);
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

        summary.addEventListener('dragenter', event => {
          if (!draggedFilePath && !event.dataTransfer?.types?.includes('text/plain')) return;
          event.preventDefault();
          summary.classList.add('drag-over');
          if (!folder.open && !dragHoverTimer) {
            dragHoverTimer = setTimeout(() => {
              folder.open = true;
              expandedFolders.set(path, true);
            }, 500);
          }
        });
        summary.addEventListener('dragover', event => {
          if (!draggedFilePath && !event.dataTransfer?.types?.includes('text/plain')) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = 'move';
          if (!summary.classList.contains('drag-over')) {
            summary.classList.add('drag-over');
          }
        });
        summary.addEventListener('dragleave', event => {
          if (event.relatedTarget && summary.contains(event.relatedTarget)) return;
          summary.classList.remove('drag-over');
          if (dragHoverTimer) {
            clearTimeout(dragHoverTimer);
            dragHoverTimer = null;
          }
        });
        summary.addEventListener('drop', async event => {
          event.preventDefault();
          summary.classList.remove('drag-over');
          if (dragHoverTimer) {
            clearTimeout(dragHoverTimer);
            dragHoverTimer = null;
          }
          const source = event.dataTransfer?.getData('text/plain') || draggedFilePath;
          if (!source) return;
          await moveFile(source, path);
        });

        const contents = document.createElement('div'); contents.className = 'file-children';
        contents.addEventListener('dragover', event => {
          if (event.target === contents) {
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
          }
        });
        contents.addEventListener('drop', async event => {
          if (event.target === contents) {
            event.preventDefault();
            const source = event.dataTransfer?.getData('text/plain') || draggedFilePath;
            if (!source) return;
            await moveFile(source, path);
          }
        });

        appendFolders(contents, child, path); appendFiles(contents, child.files);
        if (child.files.length === 0 && child.folders.size === 0) {
          const emptyHint = document.createElement('span');
          emptyHint.className = 'empty-folder-hint';
          emptyHint.textContent = path === 'references/characters'
            ? 'No character portraits yet (references/characters/<Name>/1.webp)'
            : (path === 'references/scenes'
              ? 'No scene images yet (references/scenes/<Scene>/1.webp)'
              : 'Empty folder');
          emptyHint.addEventListener('dragenter', event => {
            event.preventDefault();
            emptyHint.classList.add('drag-over');
          });
          emptyHint.addEventListener('dragover', event => {
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
            emptyHint.classList.add('drag-over');
          });
          emptyHint.addEventListener('dragleave', () => {
            emptyHint.classList.remove('drag-over');
          });
          emptyHint.addEventListener('drop', async event => {
            event.preventDefault();
            emptyHint.classList.remove('drag-over');
            const source = event.dataTransfer?.getData('text/plain') || draggedFilePath;
            if (!source) return;
            await moveFile(source, path);
          });
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
    const isSvg = file.path.toLowerCase().endsWith('.svg');
    const toggleSource = el('toggle-source');
    if (toggleSource) {
      toggleSource.hidden = !isSvg;
      toggleSource.textContent = 'Source';
    }
    // Regeneration can replace bytes at the same path. Give each draft revision
    // a distinct URL so previews cannot reuse the previous image from memory.
    const url = `${endpoint('/file')}?path=${encodeURIComponent(file.path)}&revision=${state.draft.revision}`;
    el('download-file').href = url; el('download-file').hidden = false;
    const isProtected = file.path === 'theater.yaml';
    el('delete-file').hidden = isProtected;
    el('delete-file').disabled = isProtected || (activeGenerations > 0);
    if (isSvg) {
      try {
        let content = pendingWrites.get(file.path);
        if (content === undefined) {
          const res = await fetch(url);
          if (!res.ok) throw new Error('Could not load this file.');
          content = await res.text();
        }
        if (sequence !== previewSequence) return;
        el('file-editor').value = content;
        renderSvgPreview(file, content, url);
      } catch (error) {
        if (sequence !== previewSequence) return;
        renderSvgPreview(file, null, url);
      }
    } else if (file.kind === 'text') {
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
      const media = document.createElement(file.kind === 'image' ? 'img' : file.kind === 'video' ? 'video' : 'audio');
      media.src = url;
      if (file.kind === 'image') media.alt = file.path;
      else media.controls = true;
      el('media-preview').replaceChildren(media); el('media-preview').hidden = false;
    }
    updateSaveState();
  }
  function renderSvgPreview(file, content, url) {
    const media = document.createElement('img');
    media.className = 'svg-graphic';
    media.alt = file.path;
    const currentContent = content ?? pendingWrites.get(file.path);
    if (currentContent && typeof currentContent === 'string' && currentContent.trim().startsWith('<')) {
      media.src = `data:image/svg+xml;utf8,${encodeURIComponent(currentContent)}`;
    } else {
      media.src = url;
    }
    el('media-preview').replaceChildren(media);
    el('media-preview').hidden = false;
    el('file-editor').hidden = true;
  }
  async function moveFile(source, targetFolder) {
    if (!source || !targetFolder) return;
    if (busy || activeGenerations > 0) {
      status('Please wait for current operation to finish.', true);
      return;
    }
    if (source === 'theater.yaml') {
      status('theater.yaml cannot be moved.', true);
      return;
    }
    const filename = source.split('/').pop();
    const sourceFolder = source.split('/').slice(0, -1).join('/');
    if (sourceFolder === targetFolder) return;
    const destination = `${targetFolder}/${filename}`;
    if (state.files.some(file => file.path.toLowerCase() === destination.toLowerCase())) {
      status(`A file named "${filename}" already exists in ${targetFolder}.`, true);
      return;
    }
    let nextDraft = null;
    await run(async () => {
      captureText();
      await save();
      nextDraft = await post('/move', {
        revision: state.draft.revision,
        source,
        destination,
      });
      expandedFolders.set(targetFolder, true);
      selected = nextDraft.files.find(f => f.path === destination) || null;
      render(nextDraft);
      status(`Moved ${filename} to ${targetFolder}.`);
    }, `Moving ${filename} to ${targetFolder}…`);
    if (selected) {
      await selectFile(selected);
    }
  }
  async function save(persist = false) {
    captureText();
    if (!dirty() && !persist) return;
    const next = await post('/save', { revision: state.draft.revision, name: el('draft-name').value.trim(), writes: [...pendingWrites].map(([path, content]) => ({ path, content })), persist });
    pendingWrites.clear(); nameDirty = false; textDirty = false;
    render(next);
  }
  function clearPreview() {
    selected = null; ++previewSequence;
    el('selected-path').textContent = 'Explore your theater'; el('empty-preview').hidden = false;
    el('file-editor').hidden = true; el('media-preview').hidden = true; el('download-file').hidden = true;
    el('delete-file').hidden = true;
    if (el('toggle-source')) el('toggle-source').hidden = true;
    el('media-preview').replaceChildren();
  }
  async function load(identifier) {
    const next = await api(`/api/theater-editor/${encodeURIComponent(identifier)}`);
    pendingWrites.clear(); textDirty = false; nameDirty = false; proposal = null;
    el('assistant-proposal').hidden = true; clearPreview(); render(next);
    status('Theater loaded. Select a file or ask your assistant to begin.');
  }
  async function create(populateDefault = true) {
    clearPreview(); history.length = 0; proposal = null;
    el('assistant-proposal').hidden = true;
    render(await api('/api/theater-editor/drafts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: el('new-name').value.trim() || 'My Theater', populate_default: populateDefault }) }));
  }
  function convertPngFile(file, targetType = 'image/webp', quality = 0.85) {
    return new Promise(resolve => {
      const isPng = (file.name && file.name.toLowerCase().endsWith('.png')) || file.type === 'image/png';
      if (!isPng) {
        resolve(file);
        return;
      }
      const ext = targetType === 'image/jpeg' ? '.jpg' : '.webp';
      const newName = file.name.replace(/\.png$/i, ext);
      const newRelPath = file.webkitRelativePath ? file.webkitRelativePath.replace(/\.png$/i, ext) : '';

      const finishWithConverted = blob => {
        const converted = new File([blob], newName, { type: targetType, lastModified: Date.now() });
        if (newRelPath) {
          try {
            Object.defineProperty(converted, 'webkitRelativePath', {
              value: newRelPath,
              writable: false,
              configurable: true,
            });
          } catch (e) {
            converted._customRelativePath = newRelPath;
          }
        }
        resolve(converted);
      };

      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        URL.revokeObjectURL(url);
        try {
          const canvas = document.createElement('canvas');
          canvas.width = img.naturalWidth || img.width || 1;
          canvas.height = img.naturalHeight || img.height || 1;
          const ctx = canvas.getContext('2d');
          if (targetType === 'image/jpeg') {
            ctx.fillStyle = '#ffffff';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
          }
          ctx.drawImage(img, 0, 0);
          canvas.toBlob(blob => {
            if (!blob) {
              finishWithConverted(file);
              return;
            }
            finishWithConverted(blob);
          }, targetType, quality);
        } catch (err) {
          finishWithConverted(file);
        }
      };
      img.onerror = () => {
        URL.revokeObjectURL(url);
        finishWithConverted(file);
      };
      img.src = url;
    });
  }

  async function convertPngFiles(files, targetType = 'image/webp') {
    return Promise.all(files.map(file => convertPngFile(file, targetType)));
  }

  const PNG_PREFERENCE_KEY = 'narratron.builder.png_upload_preference';

  function getPngPreference() {
    try {
      const item = localStorage.getItem(PNG_PREFERENCE_KEY);
      return item ? JSON.parse(item) : null;
    } catch (e) {
      return null;
    }
  }

  function savePngPreference(action, format = null) {
    try {
      if (el('png-remember-choice')?.checked) {
        localStorage.setItem(PNG_PREFERENCE_KEY, JSON.stringify({ action, format }));
      }
    } catch (e) {
      /* ignore storage errors */
    }
  }

  let pendingUpload = null;

  async function handleUploadRequest(files, isFolder) {
    if (!files || !files.length) return;
    const pngFiles = files.filter(f => (f.name && f.name.toLowerCase().endsWith('.png')) || f.type === 'image/png');
    if (pngFiles.length === 0) {
      upload(files, isFolder);
      return;
    }

    const savedPreference = getPngPreference();
    if (savedPreference) {
      if (savedPreference.action === 'convert') {
        status('Converting PNG images before uploading…');
        const processedFiles = await convertPngFiles(files, savedPreference.format || 'image/webp');
        upload(processedFiles, isFolder);
        return;
      }
      if (savedPreference.action === 'skip') {
        upload(files, isFolder);
        return;
      }
    }

    const pngDialog = el('png-warning-dialog');
    if (pngDialog && typeof pngDialog.showModal === 'function') {
      pendingUpload = { files, isFolder };
      const summaryEl = el('png-warning-files');
      if (summaryEl) {
        summaryEl.textContent = `${pngFiles.length} PNG image${pngFiles.length > 1 ? 's' : ''} detected: ${pngFiles.map(f => f.name).join(', ')}`;
      }
      if (el('png-remember-choice')) el('png-remember-choice').checked = false;
      pngDialog.showModal();
    } else {
      upload(files, isFolder);
    }
  }

  async function upload(files, folder) {
    if (!files.length) return;
    await run(async () => {
      const form = new FormData();
      form.append('folder', folder);
      for (const file of files) form.append('files', file, folder ? (file.webkitRelativePath || file._customRelativePath || file.name) : file.name);
      let result;
      if (state) {
        await save();
        form.append('revision', state.draft.revision);
        result = await api(endpoint('/upload'), { method: 'POST', body: form });
      } else {
        form.append('name', el('new-name').value.trim() || 'My Theater');
        result = await api('/api/theater-editor/drafts/import', { method: 'POST', body: form });
        history.length = 0; proposal = null;
        el('assistant-proposal').hidden = true;
      }
      render(result);
      clearPreview();
      const discarded = result.discarded_files || [];
      const summary = discarded.length ? ` Discarded ${discarded.length} ineligible files: ${discarded.join(', ')}.` : '';
      status(`Uploaded ${result.imported_files ?? files.length} files.${summary} Ask the assistant to organize them or develop your world.`);
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
      status(`Could not find ${path} in this theater.`, true);
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
      const button = document.createElement('button'); button.textContent = 'Apply file changes'; button.type = 'button';
      button.addEventListener('click', () => {
        if (activeGenerations > 0) { status('Please wait for asset generation to finish before applying changes.', true); return; }
        run(async () => {
          await save();
          render(await post('/apply', { revision: proposalRevision, proposal }));
          for (const generation of proposal.generations) {
            generation.references = generation.references.map(path => proposal.moves.find(move => move.source === path)?.destination || path);
          }
          clearPreview(); button.disabled = true; button.dataset.done = 'true';
          status('File changes applied to your working theater.');
        }, 'Applying changes…');
      });
      container.append(button);
    }
    for (const generation of proposal.generations) {
      const card = document.createElement('div'); card.className = 'generation-card';
      const kindLabel = { reference: 'Reference', stamp: 'Stamp token', character: 'Character portrait', scene: 'Scene visual', playlist: 'Playlist track', animation: 'Animation' }[generation.kind];
      const label = document.createElement('strong'); label.textContent = `${kindLabel}: ${generation.name}`;
      const description = document.createElement('p'); description.textContent = generation.prompt;
      const button = document.createElement('button'); button.type = 'button';
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
        button.textContent = `Generate · ${state.rates[generationRate(generation.kind)]} Cr`;
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
      if (selected?.path === result.path) await openAsset(result.path);
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
      status(`Added ${result.path} to your working theater.`);
    } catch (error) {
      status(error.message, true);
      if (button) {
        delete button.dataset.generating;
        button.disabled = false;
        if (isManualSubmit) {
          updateGenerationCost();
        } else {
          button.textContent = `Generate · ${state.rates[generationRate(request.kind)]} Cr`;
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
  function generationRate(kind) {
    return kind === 'animation' ? 'layered_animation_credit_rate' : kind === 'playlist' ? 'music_credit_rate' : 'image_credit_rate';
  }
  function updateGenerationCost() {
    const isAnimation = el('generation-kind').value === 'animation';
    el('generation-animation-type').hidden = !isAnimation;
    document.querySelector('label[for="generation-animation-type"]').hidden = !isAnimation;
    if (state && el('generation-submit') && el('generation-submit').dataset.generating !== 'true') {
      el('generation-submit').textContent = `Generate · ${state.rates[generationRate(el('generation-kind').value)]} Cr`;
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
        status(`Prepared Google Doc changes for review. Charged ${res.credits_charged} credits.`);
      } else {
        importedFile = state.files.find(f => f.path === res.path);
        status(res.message || `Imported ${res.path} from Google.`);
      }
    }, harvest ? 'Harvesting Google Doc with AI co-creator…' : 'Downloading and importing from Google…');
    if (importedFile) {
      await selectFile(importedFile);
    }
  }
  let availableGames = [];
  let selectedGameId = null;

  function renderGameLibraryList() {
    const listEl = el('game-library-list');
    if (!listEl) return;
    listEl.replaceChildren();
    const query = (el('game-library-search')?.value || '').trim().toLowerCase();
    const filtered = availableGames.filter(game => {
      if (!query) return true;
      const haystack = [
        game.title,
        game.id,
        game.description,
        game.genre,
        game.author,
        ...(Array.isArray(game.tags) ? game.tags : []),
      ].filter(Boolean).join(' ').toLowerCase();
      return haystack.includes(query);
    });
    if (!filtered.length) {
      const empty = document.createElement('div');
      empty.className = 'game-library-empty';
      empty.textContent = availableGames.length ? 'No games match your search.' : 'No pre-defined games found in the repository.';
      listEl.append(empty);
      if (el('game-library-submit')) el('game-library-submit').disabled = true;
      return;
    }
    if (!selectedGameId || !filtered.some(g => g.id === selectedGameId)) {
      selectedGameId = filtered[0].id;
    }
    if (el('game-library-submit')) el('game-library-submit').disabled = false;
    for (const game of filtered) {
      const card = document.createElement('div');
      card.className = 'game-library-card' + (game.id === selectedGameId ? ' selected' : '');
      card.setAttribute('role', 'option');
      card.setAttribute('tabindex', '0');
      card.setAttribute('aria-selected', game.id === selectedGameId ? 'true' : 'false');
      card.dataset.gameId = game.id;

      if (game.cover_image) {
        const img = document.createElement('img');
        img.className = 'game-card-cover';
        img.src = `/api/games/${encodeURIComponent(game.id)}/cover`;
        img.alt = game.title || game.id;
        card.append(img);
      } else {
        const icon = document.createElement('div');
        icon.className = 'game-card-cover';
        icon.textContent = '🎮';
        card.append(icon);
      }

      const body = document.createElement('div');
      body.className = 'game-card-body';

      const header = document.createElement('div');
      header.className = 'game-card-header';
      const title = document.createElement('span');
      title.className = 'game-card-title';
      title.textContent = game.title || game.id;
      header.append(title);
      if (game.genre) {
        const genre = document.createElement('span');
        genre.className = 'game-card-genre';
        genre.textContent = game.genre;
        header.append(genre);
      }
      body.append(header);

      if (game.description) {
        const desc = document.createElement('p');
        desc.className = 'game-card-desc';
        desc.textContent = game.description;
        body.append(desc);
      }

      const badges = document.createElement('div');
      badges.className = 'game-card-badges';
      if (game.declaration_file) {
        const declBadge = document.createElement('span');
        declBadge.className = 'game-card-badge';
        declBadge.textContent = game.declaration_file;
        badges.append(declBadge);
      }
      if (game.has_panel) {
        const panelBadge = document.createElement('span');
        panelBadge.className = 'game-card-badge';
        panelBadge.textContent = 'HTML Panel';
        badges.append(panelBadge);
      }
      if (game.svg_count > 0) {
        const svgBadge = document.createElement('span');
        svgBadge.className = 'game-card-badge';
        svgBadge.textContent = `${game.svg_count} SVG${game.svg_count > 1 ? 's' : ''}`;
        badges.append(svgBadge);
      }
      if (game.stamp_count > 0) {
        const stampBadge = document.createElement('span');
        stampBadge.className = 'game-card-badge';
        stampBadge.textContent = `${game.stamp_count} Stamp${game.stamp_count > 1 ? 's' : ''}`;
        badges.append(stampBadge);
      }
      body.append(badges);
      card.append(body);

      const selectCard = () => {
        selectedGameId = game.id;
        renderGameLibraryList();
      };
      card.addEventListener('click', selectCard);
      card.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          selectCard();
        }
      });
      listEl.append(card);
    }
  }

  async function openGameLibraryDialog() {
    if (!state || busy || activeGenerations > 0) return;
    const dialog = el('game-library-dialog');
    if (!dialog) return;
    if (el('game-library-search')) el('game-library-search').value = '';
    if (el('game-enable-tool')) el('game-enable-tool').checked = false;
    const listEl = el('game-library-list');
    if (listEl) {
      const loading = document.createElement('div');
      loading.className = 'game-library-empty';
      loading.textContent = 'Loading games from repository…';
      listEl.replaceChildren(loading);
    }
    if (el('game-library-submit')) el('game-library-submit').disabled = true;
    dialog.showModal();
    try {
      const games = await api('/api/games');
      availableGames = Array.isArray(games) ? games : [];
      selectedGameId = availableGames[0]?.id || null;
      renderGameLibraryList();
      el('game-library-search')?.focus();
    } catch (error) {
      if (listEl) {
        const errNode = document.createElement('div');
        errNode.className = 'game-library-empty';
        errNode.textContent = error.message || 'Could not load games.';
        listEl.replaceChildren(errNode);
      }
    }
  }

  async function submitAttachGame() {
    if (!state || !selectedGameId) return;
    const chosen = availableGames.find(g => g.id === selectedGameId);
    const enableGameTool = Boolean(el('game-enable-tool')?.checked);
    el('game-library-dialog')?.close();
    let targetFile = null;
    await run(async () => {
      await save();
      const next = await post('/game', {
        revision: state.draft.revision,
        game_id: selectedGameId,
        enable_game_tool: enableGameTool,
      });
      expandedFolders.set('game', true);
      if (chosen?.stamp_count > 0) expandedFolders.set('stamps', true);
      render(next);
      const declPath = chosen?.declaration_file || 'game/game.yaml';
      targetFile = state.files.find(f => f.path === declPath) || state.files.find(f => f.path.startsWith('game/'));
      status(`Attached "${chosen?.title || selectedGameId}" to your working theater.`);
    }, `Attaching ${chosen?.title || selectedGameId}…`);
    if (targetFile) {
      await selectFile(targetFile);
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
  el('copy-authoring-prompt').addEventListener('click', async () => {
    const prompt = el('authoring-prompt');
    try {
      await navigator.clipboard.writeText(prompt.value);
      status('Starter prompt copied. Paste it into your AI app with the authoring kit.');
    } catch {
      prompt.focus();
      prompt.select();
      status('Copy the selected starter prompt and paste it into your AI app.');
    }
  });
  el('start-default').addEventListener('click', () => run(async () => { await create(); status('Default theater ready. Make it your own.'); }, 'Preparing your theater…'));
  el('resume-draft').addEventListener('click', () => run(() => load(el('resume-draft').dataset.identifier), 'Opening your last theater…'));
  el('start-folder').addEventListener('click', () => el('folder-input').click());
  el('upload-folder').addEventListener('click', () => el('folder-input').click());
  el('upload-assets').addEventListener('click', () => el('assets-input').click());
  el('folder-input').addEventListener('change', event => { const files = [...event.target.files]; event.target.value = ''; handleUploadRequest(files, true); });
  el('assets-input').addEventListener('change', event => { const files = [...event.target.files]; event.target.value = ''; handleUploadRequest(files, false); });
  el('file-editor').addEventListener('input', () => { textDirty = true; updateSaveState(); });
  if (el('toggle-source')) {
    el('toggle-source').addEventListener('click', async () => {
      if (!selected || !selected.path.toLowerCase().endsWith('.svg')) return;
      const viewingSource = !el('file-editor').hidden;
      if (viewingSource) {
        captureText();
        const url = `${endpoint('/file')}?path=${encodeURIComponent(selected.path)}`;
        renderSvgPreview(selected, el('file-editor').value, url);
        el('toggle-source').textContent = 'Source';
      } else {
        let content = pendingWrites.get(selected.path);
        if (content === undefined) {
          const url = `${endpoint('/file')}?path=${encodeURIComponent(selected.path)}`;
          try {
            const res = await fetch(url);
            if (!res.ok) throw new Error('Could not load this file.');
            content = await res.text();
          } catch (error) {
            status(error.message, true);
            return;
          }
        }
        el('file-editor').value = content;
        el('file-editor').hidden = false;
        el('media-preview').hidden = true;
        el('toggle-source').textContent = 'Preview';
      }
    });
  }
  el('draft-name').addEventListener('input', () => { nameDirty = true; updateSaveState(); });
  el('save-draft').addEventListener('click', () => run(async () => { await save(true); status('Theater saved.'); }, 'Saving theater…'));
  el('download-draft').addEventListener('click', () => run(async () => {
    await save();
    const link = document.createElement('a');
    link.href = endpoint('/download');
    link.download = `${state.draft.name || state.draft.theater_id}.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    status('Theater downloaded.');
  }, 'Preparing theater download…'));
  el('reload-draft').addEventListener('click', () => { if (!dirty() || confirm('Discard unsubmitted edits and reload the working theater?')) run(() => load(state.draft.theater_id), 'Reloading theater…'); });
  el('deploy-draft').addEventListener('click', () => run(async () => {
    await save(true); const result = await post('/deploy', { revision: state.draft.revision });
    status('Deployed. Opening your canvas…'); location.assign(result.canvas_url);
  }, 'Deploying your theater…'));
  el('new-draft').addEventListener('click', () => {
    if (unsaved() && !confirm('Leave without saving this theater and start another?')) return;
    state = null; pendingWrites.clear(); textDirty = false; nameDirty = false;
    el('workspace').hidden = true; el('start-panel').hidden = false;
    window.history.replaceState(null, '', '/theater-editor'); status('');
  });
  el('assistant-form').addEventListener('submit', event => { event.preventDefault(); ask(el('assistant-input').value.trim()); });
  el('organize-assets').addEventListener('click', () => ask('Organize my uploaded assets into meaningful reference subfolders, references/characters/ folders, references/scenes/ folders, stamps/ tokens, lore documents, and named playlists. Update all file references where necessary.'));
  el('suggest-world').addEventListener('click', () => ask('Develop this theater into a coherent world using its existing assets and lore. Propose scenes (with lore dossiers, references/scenes/ imagery, and scene.yaml), characters (with lore dossiers and references/characters/ portraits), reference images, stamp tokens for tactical play, and atmospheric playlist tracks.'));
  el('harvest-doc-shortcut').addEventListener('click', () => openGoogleDialog(true));
  el('import-google-link').addEventListener('click', () => openGoogleDialog(false));
  if (el('attach-game')) el('attach-game').addEventListener('click', openGameLibraryDialog);
  if (el('game-library-cancel')) el('game-library-cancel').addEventListener('click', () => el('game-library-dialog').close());
  if (el('game-library-search')) el('game-library-search').addEventListener('input', renderGameLibraryList);
  if (el('game-library-form')) el('game-library-form').addEventListener('submit', event => { event.preventDefault(); submitAttachGame(); });
  if (el('game-library-dialog')) {
    el('game-library-dialog').addEventListener('click', event => {
      if (event.target === el('game-library-dialog')) el('game-library-dialog').close();
    });
  }
  el('google-link-cancel').addEventListener('click', () => el('google-link-dialog').close());
  el('google-link-url').addEventListener('input', updateGoogleDialogState);
  el('google-doc-harvest').addEventListener('change', updateGoogleDialogState);
  el('google-link-form').addEventListener('submit', event => { event.preventDefault(); submitGoogleLink(); });
  function getAvailableReferences() {
    if (!state || !Array.isArray(state.files)) return [];
    const imageFiles = state.files.filter(f => f.kind === 'image' && !f.path.toLowerCase().endsWith('.svg'));
    const charGroups = new Map();
    const sceneGroups = new Map();
    const flatScenes = [];
    const generalRefs = [];
    const iterScore = path => {
      const base = (path.split('/').pop() || '').replace(/\.[^.]+$/, '');
      return /^\d+$/.test(base) ? parseInt(base, 10) : -1;
    };
    for (const file of imageFiles) {
      const parts = file.path.split('/');
      if (parts[0] !== 'references') continue;
      if (parts[1] === 'characters' && parts.length >= 4) {
        const charName = parts[2];
        const prev = charGroups.get(charName);
        if (!prev || iterScore(file.path) > iterScore(prev.path) || (iterScore(file.path) === iterScore(prev.path) && file.path > prev.path)) {
          charGroups.set(charName, file);
        }
      } else if (parts[1] === 'scenes') {
        if (parts.length >= 4) {
          const sceneName = parts[2];
          const prev = sceneGroups.get(sceneName);
          if (!prev || iterScore(file.path) > iterScore(prev.path) || (iterScore(file.path) === iterScore(prev.path) && file.path > prev.path)) {
            sceneGroups.set(sceneName, file);
          }
        } else if (parts.length === 3) {
          const stem = parts[2].replace(/\.[^.]+$/, '');
          if (stem) flatScenes.push({ name: stem, kind: 'scene', path: file.path });
        }
      } else if (parts.length >= 2) {
        const filename = parts[parts.length - 1];
        const stem = filename.replace(/\.[^.]+$/, '');
        if (stem) generalRefs.push({ name: stem, kind: 'reference', path: file.path });
      }
    }
    const entries = [];
    const seen = new Set();
    const pushEntry = entry => {
      const key = entry.name.toLowerCase();
      if (!key || seen.has(key)) return;
      seen.add(key);
      entries.push(entry);
    };
    for (const [name, file] of [...charGroups.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
      pushEntry({ name, kind: 'character', path: file.path });
    }
    for (const [name, file] of [...sceneGroups.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
      pushEntry({ name, kind: 'scene', path: file.path });
    }
    for (const item of flatScenes.sort((a, b) => a.name.localeCompare(b.name))) pushEntry(item);
    for (const item of generalRefs.sort((a, b) => a.name.localeCompare(b.name))) pushEntry(item);
    return entries;
  }
  function escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }
  function extractPromptReferences(promptText) {
    if (!promptText || !promptText.includes('@')) return [];
    const available = getAvailableReferences();
    if (!available.length) return [];
    const sorted = [...available].sort((a, b) => b.name.length - a.name.length);
    const hits = [];
    for (const entry of sorted) {
      const tokens = entry.name.trim().split(/[\s_-]+/).filter(Boolean).map(escapeRegex);
      if (!tokens.length) continue;
      const pattern = new RegExp(`(?:^|[\\s([{"',;:])@(${tokens.join('[\\s_-]+')})(?=$|[\\s.,!?;:)\\]}"'])`, 'gi');
      let match;
      while ((match = pattern.exec(promptText)) !== null) {
        hits.push({ index: match.index, path: entry.path });
      }
    }
    hits.sort((a, b) => a.index - b.index);
    const result = [];
    for (const hit of hits) {
      if (!result.includes(hit.path)) {
        result.push(hit.path);
        if (result.length >= 4) break;
      }
    }
    return result;
  }
  function attachReferenceAutocomplete(textarea) {
    if (!textarea) return;
    const wrapper = document.createElement('div');
    wrapper.className = 'reference-mention-wrapper';
    textarea.parentNode.insertBefore(wrapper, textarea);
    wrapper.appendChild(textarea);
    const menu = document.createElement('div');
    menu.className = 'reference-mention-menu';
    menu.setAttribute('role', 'listbox');
    menu.hidden = true;
    wrapper.appendChild(menu);

    let filtered = [];
    let activeIndex = 0;
    let mentionStart = -1;

    function closeMenu() {
      menu.hidden = true;
      menu.replaceChildren();
      filtered = [];
      activeIndex = 0;
      mentionStart = -1;
    }

    function applySelection(entry) {
      if (!entry || mentionStart < 0) return;
      const caret = typeof textarea.selectionStart === 'number' ? textarea.selectionStart : textarea.value.length;
      const before = textarea.value.slice(0, mentionStart);
      const after = textarea.value.slice(caret);
      const insertion = `@${entry.name} `;
      textarea.value = before + insertion + after.replace(/^\s+/, '');
      const nextCaret = before.length + insertion.length;
      textarea.focus();
      textarea.setSelectionRange(nextCaret, nextCaret);
      closeMenu();
    }

    function renderMenu() {
      menu.replaceChildren();
      if (!filtered.length) {
        closeMenu();
        return;
      }
      menu.hidden = false;
      filtered.forEach((entry, idx) => {
        const option = document.createElement('button');
        option.type = 'button';
        option.className = 'reference-mention-option' + (idx === activeIndex ? ' active' : '');
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', idx === activeIndex ? 'true' : 'false');
        option.dataset.referenceName = entry.name;
        option.dataset.referencePath = entry.path;
        option.dataset.referenceKind = entry.kind;
        const nameSpan = document.createElement('span');
        nameSpan.className = 'reference-mention-name';
        nameSpan.textContent = `@${entry.name}`;
        const kindSpan = document.createElement('span');
        kindSpan.className = 'reference-mention-kind';
        kindSpan.textContent = entry.kind;
        option.append(nameSpan, kindSpan);
        option.addEventListener('mousedown', e => {
          e.preventDefault();
          applySelection(entry);
        });
        option.addEventListener('click', e => {
          e.preventDefault();
          applySelection(entry);
        });
        menu.appendChild(option);
      });
    }

    function updateMenu() {
      const caret = typeof textarea.selectionStart === 'number' ? textarea.selectionStart : textarea.value.length;
      const beforeCaret = textarea.value.slice(0, caret);
      const match = /(?:^|[\s([{"',;:])@([^@\r\n]*)$/.exec(beforeCaret);
      if (!match) {
        closeMenu();
        return;
      }
      const query = match[1].toLowerCase();
      mentionStart = caret - match[1].length - 1;
      const available = getAvailableReferences();
      const prefixMatches = [];
      const substringMatches = [];
      for (const entry of available) {
        const lower = entry.name.toLowerCase();
        if (lower.startsWith(query)) prefixMatches.push(entry);
        else if (lower.includes(query)) substringMatches.push(entry);
      }
      filtered = [...prefixMatches, ...substringMatches];
      if (!filtered.length) {
        closeMenu();
        return;
      }
      if (activeIndex >= filtered.length) activeIndex = 0;
      renderMenu();
    }

    textarea.addEventListener('input', updateMenu);
    textarea.addEventListener('click', updateMenu);
    textarea.addEventListener('keydown', event => {
      if (menu.hidden || !filtered.length) return;
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        activeIndex = (activeIndex + 1) % filtered.length;
        renderMenu();
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        activeIndex = (activeIndex - 1 + filtered.length) % filtered.length;
        renderMenu();
      } else if (event.key === 'Tab' || event.key === 'Enter') {
        event.preventDefault();
        applySelection(filtered[activeIndex]);
      } else if (event.key === 'Escape') {
        event.preventDefault();
        closeMenu();
      }
    });
    textarea.addEventListener('blur', () => {
      setTimeout(closeMenu, 150);
    });
  }
  attachReferenceAutocomplete(el('generation-prompt'));
  attachReferenceAutocomplete(el('assistant-input'));
  el('generation-kind').addEventListener('change', updateGenerationCost);
  el('generation-form').addEventListener('submit', event => {
    event.preventDefault();
    if (el('generation-submit').disabled || el('generation-submit').dataset.generating === 'true') return;
    generate({
      kind: el('generation-kind').value,
      name: el('generation-name').value,
      playlist: el('generation-playlist').value || 'ambient',
      animation_type: el('generation-animation-type').value,
      prompt: el('generation-prompt').value,
      references: extractPromptReferences(el('generation-prompt').value)
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
      await save(); render(await post('/save', { revision: state.draft.revision, name: state.draft.name, writes: [{ path, content: /\.(yaml|yml|json)$/.test(path) ? '{}\n' : '' }] }));
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
    el('delete-file-prompt').textContent = `Are you sure you want to delete "${selected.path}" from this theater? This cannot be undone.`;
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
      } else if (confirm('Are you sure you want to reset this theater? All generated output, canvas history, theater state, and reference and scene image versions after the first image (1.webp) will be permanently deleted.')) {
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
  const pngDialog = el('png-warning-dialog');
  const pngCancel = el('png-warning-cancel');
  const pngSkip = el('png-warning-skip');
  const pngForm = el('png-warning-form');
  if (pngCancel && pngDialog) {
    pngCancel.addEventListener('click', () => {
      pendingUpload = null;
      pngDialog.close('cancel');
    });
  }
  if (pngDialog) {
    pngDialog.addEventListener('click', event => {
      if (event.target === pngDialog) {
        pendingUpload = null;
        pngDialog.close('cancel');
      }
    });
    pngDialog.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        pendingUpload = null;
        pngDialog.close('cancel');
      }
    });
  }
  if (pngSkip && pngDialog) {
    pngSkip.addEventListener('click', () => {
      if (!pendingUpload) return;
      const { files, isFolder } = pendingUpload;
      savePngPreference('skip');
      pendingUpload = null;
      pngDialog.close('skip');
      upload(files, isFolder);
    });
  }
  if (pngForm && pngDialog) {
    pngForm.addEventListener('submit', async event => {
      event.preventDefault();
      if (!pendingUpload) return;
      const { files, isFolder } = pendingUpload;
      const targetFormat = el('png-convert-format')?.value || 'image/webp';
      savePngPreference('convert', targetFormat);
      pendingUpload = null;
      pngDialog.close('convert');
      status('Converting PNG images before uploading…');
      const processedFiles = await convertPngFiles(files, targetFormat);
      upload(processedFiles, isFolder);
    });
  }
  window.addEventListener('beforeunload', event => { if (unsaved() || activeGenerations > 0) { event.preventDefault(); event.returnValue = ''; } });
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
