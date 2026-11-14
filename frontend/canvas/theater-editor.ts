/** Theater Builder workspace, file tree, live preview, AI assistant, and asset generation. */

export type TheaterFileKind = 'text' | 'image' | 'audio' | 'video';

export type GenerationKind =
  | 'reference'
  | 'stamp'
  | 'character'
  | 'scene'
  | 'playlist'
  | 'animation';

export interface TheaterDraftFile {
  readonly path: string;
  readonly kind: TheaterFileKind;
}

export interface TheaterDraftInfo {
  readonly theater_id: string;
  readonly name: string;
  readonly revision: number;
  readonly saved_revision: number;
}

export interface TheaterEditorRates {
  readonly image_credit_rate: number;
  readonly music_credit_rate: number;
  readonly layered_animation_credit_rate: number;
  readonly theater_editor_assistant_credit_rate: number;
}

export interface TheaterEditorState {
  readonly draft: TheaterDraftInfo;
  readonly files: readonly TheaterDraftFile[];
  readonly rates: TheaterEditorRates;
}

export interface ImportDraftState extends TheaterEditorState {
  readonly imported_files?: number;
  readonly discarded_files?: readonly string[];
}

export interface ProposalWrite {
  readonly path: string;
  readonly content: string;
}

export interface ProposalMove {
  readonly source: string;
  readonly destination: string;
}

export interface ProposalGeneration {
  readonly kind: GenerationKind;
  readonly name: string;
  readonly prompt: string;
  readonly playlist?: string;
  readonly animation_type?: string;
  references: string[];
  generated_path?: string;
}

export interface BuilderProposal {
  readonly message: string;
  readonly writes: readonly ProposalWrite[];
  readonly moves: readonly ProposalMove[];
  readonly deletions?: readonly string[];
  readonly generations: readonly ProposalGeneration[];
}

export interface AssistantResponse {
  readonly state?: TheaterEditorState;
  readonly proposal: BuilderProposal;
  readonly revision: number;
  readonly credits_charged: number;
}

export interface GenerateRequestPayload {
  readonly kind: GenerationKind;
  readonly name: string;
  readonly prompt: string;
  readonly playlist?: string;
  readonly animation_type?: string;
  readonly references?: readonly string[];
}

export interface GenerateResponse {
  readonly state: TheaterEditorState;
  readonly path: string;
  readonly credits_charged: number;
}

export interface DeployResponse {
  readonly canvas_url: string;
}

export interface GoogleLinkResponse {
  readonly path?: string;
  readonly state?: TheaterEditorState;
  readonly harvested?: boolean;
  readonly proposal?: BuilderProposal;
  readonly revision?: number;
  readonly credits_charged?: number;
  readonly message?: string;
}

export interface GameLibraryEntry {
  readonly id: string;
  readonly title?: string;
  readonly description?: string;
  readonly genre?: string;
  readonly author?: string;
  readonly tags?: readonly string[];
  readonly declaration_file?: string;
  readonly has_panel?: boolean;
  readonly svg_count: number;
  readonly stamp_count: number;
  readonly cover_image?: string;
}

export interface TheaterAuthUser {
  readonly id: number | string;
  readonly username?: string;
  readonly credits?: number;
}

export interface TheaterAuthState {
  readonly authenticated: boolean;
  readonly user?: TheaterAuthUser | null;
}

interface AssistantHistoryEntry {
  readonly role: 'user' | 'assistant';
  readonly content: string;
}

interface FileTreeNode {
  readonly folders: Map<string, FileTreeNode>;
  readonly files: TheaterDraftFile[];
  count: number;
}

interface ReferenceEntry {
  readonly name: string;
  readonly kind: 'character' | 'scene' | 'reference';
  readonly path: string;
}

interface ReferenceHit {
  readonly index: number;
  readonly path: string;
}

interface PngUploadPreference {
  readonly action: 'convert' | 'skip';
  readonly format: string | null;
}

interface PendingUpload {
  readonly files: File[];
  readonly isFolder: boolean;
}

interface ApiErrorPayload {
  readonly detail?: unknown;
}

interface AuthChangedDetail {
  readonly authenticated?: boolean;
}

export interface TheaterEditorController {
  initialize(): Promise<void>;
}

declare global {
  interface Window {
    checkAuthStatus?: (options?: { refresh?: boolean }) => Promise<unknown>;
    onAuthSuccess?: (mode?: string, data?: unknown) => void;
    beforeCreditPurchase?: () => Promise<void>;
  }
}

function extractUserId(user: unknown): string | number | null {
  if (typeof user === 'object' && user !== null && 'id' in user) {
    const id = (user as { readonly id?: unknown }).id;
    if (typeof id === 'string' || typeof id === 'number') {
      return id;
    }
  }
  return null;
}

type RateKey =
  | 'layered_animation_credit_rate'
  | 'music_credit_rate'
  | 'image_credit_rate';

const PNG_PREFERENCE_KEY = 'narratron.builder.png_upload_preference';
const customRelativePaths = new WeakMap<File, string>();

const GENERATION_KIND_LABELS: Readonly<Record<GenerationKind, string>> = {
  reference: 'Reference',
  stamp: 'Stamp token',
  character: 'Character portrait',
  scene: 'Scene visual',
  playlist: 'Playlist track',
  animation: 'Animation',
};

function getEl(id: string): HTMLElement | null {
  return document.getElementById(id);
}

function requireEl(id: string): HTMLElement {
  const node = document.getElementById(id);
  if (!node) {
    throw new Error(`Missing required element #${id}`);
  }
  return node;
}

function requireInput(id: string): HTMLInputElement {
  const node = requireEl(id);
  if (!(node instanceof HTMLInputElement)) {
    throw new Error(`Expected #${id} to be an HTMLInputElement`);
  }
  return node;
}

function requireTextArea(id: string): HTMLTextAreaElement {
  const node = requireEl(id);
  if (!(node instanceof HTMLTextAreaElement)) {
    throw new Error(`Expected #${id} to be an HTMLTextAreaElement`);
  }
  return node;
}

function requireSelect(id: string): HTMLSelectElement {
  const node = requireEl(id);
  if (!(node instanceof HTMLSelectElement)) {
    throw new Error(`Expected #${id} to be an HTMLSelectElement`);
  }
  return node;
}

function requireButton(id: string): HTMLButtonElement {
  const node = requireEl(id);
  if (!(node instanceof HTMLButtonElement)) {
    throw new Error(`Expected #${id} to be an HTMLButtonElement`);
  }
  return node;
}

function requireDialog(id: string): HTMLDialogElement {
  const node = requireEl(id);
  if (!(node instanceof HTMLDialogElement)) {
    throw new Error(`Expected #${id} to be an HTMLDialogElement`);
  }
  return node;
}

function requireAnchor(id: string): HTMLAnchorElement {
  const node = requireEl(id);
  if (!(node instanceof HTMLAnchorElement)) {
    throw new Error(`Expected #${id} to be an HTMLAnchorElement`);
  }
  return node;
}

function requireForm(id: string): HTMLFormElement {
  const node = requireEl(id);
  if (!(node instanceof HTMLFormElement)) {
    throw new Error(`Expected #${id} to be an HTMLFormElement`);
  }
  return node;
}

function getErrorMessage(error: unknown, fallback = 'The request could not be completed.'): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

function escapeRegex(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function isGenerationKind(value: string): value is GenerationKind {
  return (
    value === 'reference' ||
    value === 'stamp' ||
    value === 'character' ||
    value === 'scene' ||
    value === 'playlist' ||
    value === 'animation'
  );
}

function generationRate(kind: GenerationKind): RateKey {
  if (kind === 'animation') return 'layered_animation_credit_rate';
  if (kind === 'playlist') return 'music_credit_rate';
  return 'image_credit_rate';
}

function fileIcon(file: TheaterDraftFile): string {
  if (file.path.startsWith('stamps/')) return '🏷️';
  if (file.path.startsWith('references/characters/')) return '👤';
  if (file.path.startsWith('references/scenes/')) return '🏞️';
  if (file.kind === 'image' || file.path.toLowerCase().endsWith('.svg')) return '▧';
  if (file.kind === 'audio') return '♫';
  return '≡';
}

function isPngFile(file: File): boolean {
  return Boolean(
    (file.name && file.name.toLowerCase().endsWith('.png')) || file.type === 'image/png',
  );
}

function convertPngFile(file: File, targetType = 'image/webp', quality = 0.85): Promise<File> {
  return new Promise((resolve) => {
    if (!isPngFile(file)) {
      resolve(file);
      return;
    }
    const ext = targetType === 'image/jpeg' ? '.jpg' : '.webp';
    const newName = file.name.replace(/\.png$/i, ext);
    const newRelPath = file.webkitRelativePath
      ? file.webkitRelativePath.replace(/\.png$/i, ext)
      : '';

    const finishWithConverted = (blob: Blob): void => {
      const converted = new File([blob], newName, {
        type: targetType,
        lastModified: Date.now(),
      });
      if (newRelPath) {
        try {
          Object.defineProperty(converted, 'webkitRelativePath', {
            value: newRelPath,
            writable: false,
            configurable: true,
          });
        } catch {
          customRelativePaths.set(converted, newRelPath);
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
        if (!ctx) {
          finishWithConverted(file);
          return;
        }
        if (targetType === 'image/jpeg') {
          ctx.fillStyle = '#ffffff';
          ctx.fillRect(0, 0, canvas.width, canvas.height);
        }
        ctx.drawImage(img, 0, 0);
        canvas.toBlob(
          (blob) => {
            if (!blob) {
              finishWithConverted(file);
              return;
            }
            finishWithConverted(blob);
          },
          targetType,
          quality,
        );
      } catch {
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

async function convertPngFiles(files: readonly File[], targetType = 'image/webp'): Promise<File[]> {
  return Promise.all(files.map((file) => convertPngFile(file, targetType)));
}

function getPngPreference(): PngUploadPreference | null {
  try {
    const item = localStorage.getItem(PNG_PREFERENCE_KEY);
    if (!item) return null;
    const parsed: unknown = JSON.parse(item);
    if (typeof parsed !== 'object' || parsed === null) return null;
    const candidate = parsed as Partial<PngUploadPreference>;
    if (candidate.action !== 'convert' && candidate.action !== 'skip') return null;
    return {
      action: candidate.action,
      format: typeof candidate.format === 'string' ? candidate.format : null,
    };
  } catch {
    return null;
  }
}

function savePngPreference(action: 'convert' | 'skip', format: string | null = null): void {
  try {
    const checkbox = getEl('png-remember-choice');
    if (checkbox instanceof HTMLInputElement && checkbox.checked) {
      localStorage.setItem(PNG_PREFERENCE_KEY, JSON.stringify({ action, format }));
    }
  } catch {
    /* ignore storage errors */
  }
}

function isAuthChangedDetail(detail: unknown): detail is AuthChangedDetail {
  return typeof detail === 'object' && detail !== null;
}

/** Wire up the Theater Builder UI, state management, file editor, assistant, and dialogs. */
export function initializeTheaterEditor(): TheaterEditorController | null {
  if (
    !document.getElementById('workspace') ||
    !document.getElementById('start-panel') ||
    !document.getElementById('login-gate')
  ) {
    return null;
  }

  let state: TheaterEditorState | null = null;
  let selected: TheaterDraftFile | null = null;
  let busy = false;
  let textDirty = false;
  let nameDirty = false;
  let opening = false;
  let previewSequence = 0;
  let proposal: BuilderProposal | null = null;
  let proposalRevision: number | null = null;
  let activeGenerations = 0;
  const generatingKeys = new Set<string>();
  let asking = false;
  const history: AssistantHistoryEntry[] = [];
  const pendingWrites = new Map<string, string>();
  const expandedFolders = new Map<string, boolean>();
  let fileTreeDraftId: string | null = null;
  let lastDraftKey: string | null = null;
  let draggedFilePath: string | null = null;
  let dragHoverTimer: number | null = null;
  let pendingUpload: PendingUpload | null = null;
  let availableGames: readonly GameLibraryEntry[] = [];
  let selectedGameId: string | null = null;

  function status(messageText: string, error = false): void {
    const statusEl = requireEl('builder-status');
    statusEl.textContent = messageText;
    statusEl.classList.toggle('error', error);
  }

  function dirty(): boolean {
    return textDirty || nameDirty || pendingWrites.size > 0;
  }

  function unsaved(): boolean {
    return dirty() || Boolean(state && state.draft.revision !== state.draft.saved_revision);
  }

  function updateSaveState(): void {
    requireEl('draft-state').textContent = unsaved() ? 'Unsaved changes' : 'Saved theater';
    requireEl('file-save-state').textContent = dirty()
      ? 'Save your edits before leaving.'
      : unsaved()
        ? 'Changes are in your working theater. Save theater to keep them.'
        : 'Theater saved. Editor and canvas share the same working copy.';
  }

  function captureText(): void {
    if (
      (selected?.kind === 'text' || selected?.path.toLowerCase().endsWith('.svg')) &&
      textDirty
    ) {
      pendingWrites.set(selected.path, requireTextArea('file-editor').value);
    }
    textDirty = false;
  }

  async function api<TResponse>(path: string, options: RequestInit = {}): Promise<TResponse> {
    const res = await fetch(path, options);
    const data = (await res.json()) as TResponse & ApiErrorPayload;
    if (!res.ok) {
      if (res.status === 401) {
        requireEl('workspace').hidden = true;
        requireEl('login-gate').hidden = false;
      }
      throw new Error(
        typeof data.detail === 'string'
          ? data.detail
          : 'The request could not be completed. Check your inputs.',
      );
    }
    return data;
  }

  function endpoint(action = ''): string {
    if (!state) {
      throw new Error('No theater draft is currently loaded.');
    }
    return `/api/theater-editor/${encodeURIComponent(state.draft.theater_id)}${action}`;
  }

  function post<TResponse>(action: string, body: unknown): Promise<TResponse> {
    return api<TResponse>(endpoint(action), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }

  async function run(action: () => Promise<void>, progress: string): Promise<void> {
    if (busy || activeGenerations > 0) {
      if (activeGenerations > 0) status('Please wait for asset generation to finish.');
      return;
    }
    busy = true;
    document.querySelectorAll<HTMLButtonElement>('main button').forEach((button) => {
      button.disabled = true;
    });
    document
      .querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(
        'main input:not([type=file]), main textarea, main select',
      )
      .forEach((input) => {
        input.disabled = true;
      });
    status(progress);
    try {
      await action();
    } catch (error: unknown) {
      status(getErrorMessage(error), true);
    } finally {
      busy = false;
      document.querySelectorAll<HTMLButtonElement>('main button').forEach((button) => {
        button.disabled =
          button.dataset.done === 'true' || button.dataset.generating === 'true';
      });
      document
        .querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(
          'main input:not([type=file]), main textarea, main select',
        )
        .forEach((input) => {
          input.disabled = false;
        });
    }
  }

  function updateGenerationCost(): void {
    const kindSelect = requireSelect('generation-kind');
    const isAnimation = kindSelect.value === 'animation';
    requireEl('generation-animation-type').hidden = !isAnimation;
    const animLabel = document.querySelector<HTMLElement>('label[for="generation-animation-type"]');
    if (animLabel) {
      animLabel.hidden = !isAnimation;
    }
    const submitBtn = getEl('generation-submit');
    if (
      state &&
      submitBtn instanceof HTMLButtonElement &&
      submitBtn.dataset.generating !== 'true' &&
      isGenerationKind(kindSelect.value)
    ) {
      submitBtn.textContent = `Generate · ${state.rates[generationRate(kindSelect.value)]} Cr`;
    }
  }

  function renderSvgPreview(
    file: TheaterDraftFile,
    content: string | null,
    url: string,
  ): void {
    const media = document.createElement('img');
    media.className = 'svg-graphic';
    media.alt = file.path;
    const currentContent = content ?? pendingWrites.get(file.path);
    if (
      currentContent &&
      typeof currentContent === 'string' &&
      currentContent.trim().startsWith('<')
    ) {
      media.src = `data:image/svg+xml;utf8,${encodeURIComponent(currentContent)}`;
    } else {
      media.src = url;
    }
    const mediaPreview = requireEl('media-preview');
    mediaPreview.replaceChildren(media);
    mediaPreview.hidden = false;
    requireEl('file-editor').hidden = true;
  }

  async function selectFile(file: TheaterDraftFile): Promise<void> {
    if (busy || !state) return;
    captureText();
    selected = file;
    const sequence = ++previewSequence;
    requireEl('file-list')
      .querySelectorAll<HTMLButtonElement>('.file-button')
      .forEach((button) => {
        const active = button.dataset.path === file.path;
        button.classList.toggle('active', active);
        if (active) {
          button.setAttribute('aria-current', 'true');
          let parent = button.closest('.file-folder');
          while (parent instanceof HTMLDetailsElement) {
            parent.open = true;
            const folderPath = parent.dataset.path;
            if (folderPath) {
              expandedFolders.set(folderPath, true);
            }
            parent = parent.parentElement ? parent.parentElement.closest('.file-folder') : null;
          }
          button.scrollIntoView({ block: 'nearest' });
        } else {
          button.removeAttribute('aria-current');
        }
      });

    requireEl('selected-path').textContent = file.path;
    requireEl('empty-preview').hidden = true;
    const fileEditor = requireTextArea('file-editor');
    fileEditor.hidden = true;
    const mediaPreview = requireEl('media-preview');
    mediaPreview.hidden = true;

    const isSvg = file.path.toLowerCase().endsWith('.svg');
    const toggleSource = getEl('toggle-source');
    if (toggleSource) {
      toggleSource.hidden = !isSvg;
      toggleSource.textContent = 'Source';
    }

    // Regeneration can replace bytes at the same path. Give each draft revision
    // a distinct URL so previews cannot reuse the previous image from memory.
    const url = `${endpoint('/file')}?path=${encodeURIComponent(file.path)}&revision=${state.draft.revision}`;
    const downloadFile = requireAnchor('download-file');
    downloadFile.href = url;
    downloadFile.hidden = false;
    const isProtected = file.path === 'theater.yaml';
    const deleteFileBtn = requireButton('delete-file');
    deleteFileBtn.hidden = isProtected;
    deleteFileBtn.disabled = isProtected || activeGenerations > 0;

    if (isSvg) {
      try {
        let content = pendingWrites.get(file.path);
        if (content === undefined) {
          const res = await fetch(url);
          if (!res.ok) throw new Error('Could not load this file.');
          content = await res.text();
        }
        if (sequence !== previewSequence) return;
        fileEditor.value = content;
        renderSvgPreview(file, content, url);
      } catch {
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
        fileEditor.value = content;
        fileEditor.hidden = false;
      } catch (error: unknown) {
        status(getErrorMessage(error, 'Could not load this file.'), true);
      }
    } else {
      let media: HTMLImageElement | HTMLVideoElement | HTMLAudioElement;
      if (file.kind === 'image') {
        const img = document.createElement('img');
        img.src = url;
        img.alt = file.path;
        media = img;
      } else if (file.kind === 'video') {
        const video = document.createElement('video');
        video.src = url;
        video.controls = true;
        media = video;
      } else {
        const audio = document.createElement('audio');
        audio.src = url;
        audio.controls = true;
        media = audio;
      }
      mediaPreview.replaceChildren(media);
      mediaPreview.hidden = false;
    }
    updateSaveState();
  }

  async function save(persist = false): Promise<void> {
    if (!state) return;
    captureText();
    if (!dirty() && !persist) return;
    const next = await post<TheaterEditorState>('/save', {
      revision: state.draft.revision,
      name: requireInput('draft-name').value.trim(),
      writes: [...pendingWrites].map(([path, content]) => ({ path, content })),
      persist,
    });
    pendingWrites.clear();
    nameDirty = false;
    textDirty = false;
    render(next);
  }

  async function moveFile(source: string, targetFolder: string): Promise<void> {
    if (!source || !targetFolder || !state) return;
    if (busy || activeGenerations > 0) {
      status('Please wait for current operation to finish.', true);
      return;
    }
    if (source === 'theater.yaml') {
      status('theater.yaml cannot be moved.', true);
      return;
    }
    const filename = source.split('/').pop() ?? '';
    const sourceFolder = source.split('/').slice(0, -1).join('/');
    if (sourceFolder === targetFolder) return;
    const destination = `${targetFolder}/${filename}`;
    if (state.files.some((file) => file.path.toLowerCase() === destination.toLowerCase())) {
      status(`A file named "${filename}" already exists in ${targetFolder}.`, true);
      return;
    }
    await run(async () => {
      if (!state) return;
      captureText();
      await save();
      const nextDraft = await post<TheaterEditorState>('/move', {
        revision: state.draft.revision,
        source,
        destination,
      });
      expandedFolders.set(targetFolder, true);
      selected = nextDraft.files.find((f) => f.path === destination) ?? null;
      render(nextDraft);
      status(`Moved ${filename} to ${targetFolder}.`);
    }, `Moving ${filename} to ${targetFolder}…`);
    if (selected) {
      await selectFile(selected);
    }
  }

  function renderFiles(): void {
    if (!state) return;
    const list = requireEl('file-list');
    if (fileTreeDraftId !== state.draft.theater_id) {
      expandedFolders.clear();
      fileTreeDraftId = state.draft.theater_id;
    } else {
      list.querySelectorAll<HTMLDetailsElement>('.file-folder').forEach((folder) => {
        const folderPath = folder.dataset.path;
        if (folderPath) {
          expandedFolders.set(folderPath, folder.open);
        }
      });
    }
    list.replaceChildren();
    const root: FileTreeNode = { folders: new Map(), files: [], count: 0 };
    for (const file of state.files) {
      const parts = file.path.split('/');
      let node = root;
      node.count++;
      for (const name of parts.slice(0, -1)) {
        let child = node.folders.get(name);
        if (!child) {
          child = { folders: new Map(), files: [], count: 0 };
          node.folders.set(name, child);
        }
        node = child;
        node.count++;
      }
      node.files.push(file);
    }
    let references = root.folders.get('references');
    if (!references) {
      references = { folders: new Map(), files: [], count: 0 };
      root.folders.set('references', references);
    }
    if (!references.folders.has('characters')) {
      references.folders.set('characters', { folders: new Map(), files: [], count: 0 });
    }
    if (!references.folders.has('scenes')) {
      references.folders.set('scenes', { folders: new Map(), files: [], count: 0 });
    }

    function appendFiles(parent: HTMLElement, files: readonly TheaterDraftFile[]): void {
      for (const file of [...files].sort((a, b) => a.path.localeCompare(b.path))) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'file-button';
        button.dataset.path = file.path;
        button.classList.toggle('active', selected?.path === file.path);
        if (selected?.path === file.path) button.setAttribute('aria-current', 'true');
        button.textContent = `${fileIcon(file)} ${file.path.split('/').pop() ?? file.path}`;
        button.title = file.path;
        button.addEventListener('click', () => {
          void selectFile(file);
        });
        if (file.path !== 'theater.yaml') {
          button.draggable = true;
          button.addEventListener('dragstart', (event: DragEvent) => {
            if (busy || activeGenerations > 0) {
              event.preventDefault();
              return;
            }
            draggedFilePath = file.path;
            if (event.dataTransfer) {
              event.dataTransfer.setData('text/plain', file.path);
              event.dataTransfer.effectAllowed = 'move';
            }
            button.classList.add('dragging');
          });
          button.addEventListener('dragend', () => {
            draggedFilePath = null;
            button.classList.remove('dragging');
            document.querySelectorAll('.drag-over').forEach((node) => {
              node.classList.remove('drag-over');
            });
            if (dragHoverTimer !== null) {
              window.clearTimeout(dragHoverTimer);
              dragHoverTimer = null;
            }
          });
        }
        parent.append(button);
      }
    }

    function appendFolders(parent: HTMLElement, node: FileTreeNode, prefix = ''): void {
      for (const [name, child] of [...node.folders].sort(([a], [b]) => a.localeCompare(b))) {
        const folderPath = prefix ? `${prefix}/${name}` : name;
        const folder = document.createElement('details');
        folder.className = 'file-folder';
        folder.dataset.path = folderPath;
        folder.open = expandedFolders.get(folderPath) ?? false;

        const summary = document.createElement('summary');
        summary.className = 'folder-button';
        summary.title = folderPath;
        const label = document.createElement('span');
        label.className = 'folder-name';
        label.textContent = name;
        const count = document.createElement('span');
        count.className = 'folder-count';
        count.textContent = String(child.count);
        count.setAttribute('aria-label', `${child.count} files`);
        summary.append(label, count);

        summary.addEventListener('dragenter', (event: DragEvent) => {
          if (!draggedFilePath && !event.dataTransfer?.types.includes('text/plain')) return;
          event.preventDefault();
          summary.classList.add('drag-over');
          if (!folder.open && dragHoverTimer === null) {
            dragHoverTimer = window.setTimeout(() => {
              folder.open = true;
              expandedFolders.set(folderPath, true);
            }, 500);
          }
        });
        summary.addEventListener('dragover', (event: DragEvent) => {
          if (!draggedFilePath && !event.dataTransfer?.types.includes('text/plain')) return;
          event.preventDefault();
          if (event.dataTransfer) {
            event.dataTransfer.dropEffect = 'move';
          }
          if (!summary.classList.contains('drag-over')) {
            summary.classList.add('drag-over');
          }
        });
        summary.addEventListener('dragleave', (event: DragEvent) => {
          if (event.relatedTarget instanceof Node && summary.contains(event.relatedTarget)) return;
          summary.classList.remove('drag-over');
          if (dragHoverTimer !== null) {
            window.clearTimeout(dragHoverTimer);
            dragHoverTimer = null;
          }
        });
        summary.addEventListener('drop', (event: DragEvent) => {
          event.preventDefault();
          summary.classList.remove('drag-over');
          if (dragHoverTimer !== null) {
            window.clearTimeout(dragHoverTimer);
            dragHoverTimer = null;
          }
          const source = event.dataTransfer?.getData('text/plain') || draggedFilePath;
          if (!source) return;
          void moveFile(source, folderPath);
        });

        const contents = document.createElement('div');
        contents.className = 'file-children';
        contents.addEventListener('dragover', (event: DragEvent) => {
          if (event.target === contents) {
            event.preventDefault();
            if (event.dataTransfer) {
              event.dataTransfer.dropEffect = 'move';
            }
          }
        });
        contents.addEventListener('drop', (event: DragEvent) => {
          if (event.target === contents) {
            event.preventDefault();
            const source = event.dataTransfer?.getData('text/plain') || draggedFilePath;
            if (!source) return;
            void moveFile(source, folderPath);
          }
        });

        appendFolders(contents, child, folderPath);
        appendFiles(contents, child.files);
        if (child.files.length === 0 && child.folders.size === 0) {
          const emptyHint = document.createElement('span');
          emptyHint.className = 'empty-folder-hint';
          emptyHint.textContent =
            folderPath === 'references/characters'
              ? 'No character portraits yet (references/characters/<Name>/1.webp)'
              : folderPath === 'references/scenes'
                ? 'No scene images yet (references/scenes/<Scene>/1.webp)'
                : 'Empty folder';
          emptyHint.addEventListener('dragenter', (event: DragEvent) => {
            event.preventDefault();
            emptyHint.classList.add('drag-over');
          });
          emptyHint.addEventListener('dragover', (event: DragEvent) => {
            event.preventDefault();
            if (event.dataTransfer) {
              event.dataTransfer.dropEffect = 'move';
            }
            emptyHint.classList.add('drag-over');
          });
          emptyHint.addEventListener('dragleave', () => {
            emptyHint.classList.remove('drag-over');
          });
          emptyHint.addEventListener('drop', (event: DragEvent) => {
            event.preventDefault();
            emptyHint.classList.remove('drag-over');
            const source = event.dataTransfer?.getData('text/plain') || draggedFilePath;
            if (!source) return;
            void moveFile(source, folderPath);
          });
          contents.append(emptyHint);
        }
        folder.append(summary, contents);
        parent.append(folder);
      }
    }

    if (root.files.length) {
      const section = document.createElement('div');
      section.className = 'file-group';
      const heading = document.createElement('h3');
      heading.textContent = 'Configuration';
      section.append(heading);
      appendFiles(section, root.files);
      list.append(section);
    }
    appendFolders(list, root);
  }

  function render(next: TheaterEditorState): void {
    state = next;
    requireEl('start-panel').hidden = true;
    requireEl('login-gate').hidden = true;
    requireEl('workspace').hidden = false;
    requireInput('draft-name').value = state.draft.name;
    requireEl('file-count').textContent = `${state.files.length} files`;
    const rates = state.rates;
    requireEl('assistant-send').textContent =
      `Send · ${rates.theater_editor_assistant_credit_rate} Cr →`;
    requireEl('assistant-cost').textContent =
      `${rates.theater_editor_assistant_credit_rate} Cr per assistant turn, including shortcuts. Charged when a reply is ready.`;
    requireEl('generation-rates').textContent =
      'Image, playlist, and animation pricing follows standard live pricing.';
    updateGenerationCost();
    if (lastDraftKey) localStorage.setItem(lastDraftKey, state.draft.theater_id);
    const url = new URL(location.href);
    url.searchParams.set('theater_id', state.draft.theater_id);
    window.history.replaceState(null, '', url);
    renderFiles();
    updateSaveState();
  }

  function clearPreview(): void {
    selected = null;
    ++previewSequence;
    requireEl('selected-path').textContent = 'Explore your theater';
    requireEl('empty-preview').hidden = false;
    requireEl('file-editor').hidden = true;
    requireEl('media-preview').hidden = true;
    requireEl('download-file').hidden = true;
    requireEl('delete-file').hidden = true;
    const toggleSource = getEl('toggle-source');
    if (toggleSource) toggleSource.hidden = true;
    requireEl('media-preview').replaceChildren();
  }

  async function load(identifier: string): Promise<void> {
    const next = await api<TheaterEditorState>(
      `/api/theater-editor/${encodeURIComponent(identifier)}`,
    );
    pendingWrites.clear();
    textDirty = false;
    nameDirty = false;
    proposal = null;
    requireEl('assistant-proposal').hidden = true;
    clearPreview();
    render(next);
    status('Theater loaded. Select a file or ask your assistant to begin.');
  }

  async function create(populateDefault = true): Promise<void> {
    clearPreview();
    history.length = 0;
    proposal = null;
    requireEl('assistant-proposal').hidden = true;
    const next = await api<TheaterEditorState>('/api/theater-editor/drafts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: requireInput('new-name').value.trim() || 'My Theater',
        populate_default: populateDefault,
      }),
    });
    render(next);
  }

  async function upload(files: readonly File[], folder: boolean): Promise<void> {
    if (!files.length) return;
    await run(async () => {
      const form = new FormData();
      form.append('folder', String(folder));
      for (const file of files) {
        const relativeName = folder
          ? file.webkitRelativePath || customRelativePaths.get(file) || file.name
          : file.name;
        form.append('files', file, relativeName);
      }
      let result: ImportDraftState;
      if (state) {
        await save();
        form.append('revision', String(state.draft.revision));
        result = await api<ImportDraftState>(endpoint('/upload'), { method: 'POST', body: form });
      } else {
        form.append('name', requireInput('new-name').value.trim() || 'My Theater');
        result = await api<ImportDraftState>('/api/theater-editor/drafts/import', {
          method: 'POST',
          body: form,
        });
        history.length = 0;
        proposal = null;
        requireEl('assistant-proposal').hidden = true;
      }
      render(result);
      clearPreview();
      const discarded = result.discarded_files ?? [];
      const summary = discarded.length
        ? ` Discarded ${discarded.length} ineligible files: ${discarded.join(', ')}.`
        : '';
      status(
        `Uploaded ${result.imported_files ?? files.length} files.${summary} Ask the assistant to organize them or develop your world.`,
      );
    }, 'Uploading theater assets…');
  }

  async function handleUploadRequest(files: File[], isFolder: boolean): Promise<void> {
    if (!files.length) return;
    const pngFiles = files.filter(isPngFile);
    if (pngFiles.length === 0) {
      await upload(files, isFolder);
      return;
    }

    const savedPreference = getPngPreference();
    if (savedPreference) {
      if (savedPreference.action === 'convert') {
        status('Converting PNG images before uploading…');
        const processedFiles = await convertPngFiles(
          files,
          savedPreference.format || 'image/webp',
        );
        await upload(processedFiles, isFolder);
        return;
      }
      if (savedPreference.action === 'skip') {
        await upload(files, isFolder);
        return;
      }
    }

    const pngDialog = getEl('png-warning-dialog');
    if (pngDialog instanceof HTMLDialogElement && typeof pngDialog.showModal === 'function') {
      pendingUpload = { files, isFolder };
      const summaryEl = getEl('png-warning-files');
      if (summaryEl) {
        summaryEl.textContent = `${pngFiles.length} PNG image${pngFiles.length > 1 ? 's' : ''} detected: ${pngFiles.map((f) => f.name).join(', ')}`;
      }
      const rememberCheckbox = getEl('png-remember-choice');
      if (rememberCheckbox instanceof HTMLInputElement) {
        rememberCheckbox.checked = false;
      }
      pngDialog.showModal();
    } else {
      await upload(files, isFolder);
    }
  }

  async function openAsset(path: string): Promise<void> {
    if (!state) return;
    const file = state.files.find((f) => f.path === path);
    if (file) {
      await selectFile(file);
    } else {
      status(`Could not find ${path} in this theater.`, true);
    }
  }

  function makeOpenLink(path: string, text = 'Open asset →'): HTMLAnchorElement {
    const link = document.createElement('a');
    link.className = 'open-asset-link';
    link.href = '#';
    link.textContent = text;
    link.setAttribute('role', 'button');
    link.setAttribute('aria-label', `Open ${path}`);
    const handler = (event: Event): void => {
      event.preventDefault();
      void openAsset(path);
    };
    link.addEventListener('click', handler);
    link.addEventListener('keydown', (event: KeyboardEvent) => {
      if (event.key === 'Enter' || event.key === ' ') handler(event);
    });
    return link;
  }

  function message(role: 'user' | 'assistant', text: string, assetPath: string | null = null): void {
    const node = document.createElement('div');
    node.className = `message ${role}`;
    node.textContent = text;
    if (assetPath) {
      node.append(' ', makeOpenLink(assetPath));
    }
    const messagesEl = requireEl('assistant-messages');
    messagesEl.append(node);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  async function generate(
    request: GenerateRequestPayload,
    button: HTMLButtonElement | null,
  ): Promise<void> {
    if (busy || !state) return;
    const key = `${request.kind}:${request.name}:${request.playlist || ''}`;
    if (generatingKeys.has(key)) return;
    if (button && (button.disabled || button.dataset.generating === 'true')) return;

    generatingKeys.add(key);
    activeGenerations++;
    const isManualSubmit = button?.id === 'generation-submit';
    if (button) {
      button.disabled = true;
      button.dataset.generating = 'true';
      button.textContent = 'Generating…';
    }

    captureText();
    if (dirty()) {
      try {
        await save();
      } catch {
        /* ignore pre-generation auto-save failure */
      }
    }
    const revision = state.draft.revision;
    status(
      activeGenerations > 1
        ? `Generating asset (${activeGenerations} in progress)…`
        : 'Generating your asset. This can take a few minutes…',
    );

    try {
      const result = await post<GenerateResponse>('/generate', { ...request, revision });
      render(result.state);
      if (selected?.path === result.path) await openAsset(result.path);
      // A generated asset adds a new file, so the same proposal remains applicable.
      if (proposalRevision !== null && state) proposalRevision = state.draft.revision;
      if (request.kind && request.name && proposal?.generations) {
        const match = proposal.generations.find(
          (g) => g.name === request.name && g.kind === request.kind,
        );
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
      await window.checkAuthStatus?.({ refresh: true });
      message(
        'assistant',
        `Created ${result.path}. Charged ${result.credits_charged} credits.`,
        result.path,
      );
      history.push({ role: 'assistant', content: `Created asset: ${result.path}` });
      status(`Added ${result.path} to your working theater.`);
    } catch (error: unknown) {
      status(getErrorMessage(error), true);
      if (button) {
        delete button.dataset.generating;
        button.disabled = false;
        if (isManualSubmit) {
          updateGenerationCost();
        } else if (state) {
          button.textContent = `Generate · ${state.rates[generationRate(request.kind)]} Cr`;
        }
      }
    } finally {
      generatingKeys.delete(key);
      activeGenerations = Math.max(0, activeGenerations - 1);
      if (selected) {
        const isProtected = selected.path === 'theater.yaml';
        requireButton('delete-file').disabled = isProtected || activeGenerations > 0;
      }
    }
  }

  function renderProposal(): void {
    const container = requireEl('assistant-proposal');
    container.replaceChildren();
    if (!proposal || !state) {
      container.hidden = true;
      return;
    }
    const activeProposal = proposal;
    container.hidden = false;
    const title = document.createElement('strong');
    title.textContent = 'Review proposed changes';
    container.append(title);

    for (const write of activeProposal.writes) {
      const details = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = `Edit ${write.path}`;
      const pre = document.createElement('pre');
      pre.textContent = write.content;
      details.append(summary, pre);
      container.append(details);
    }
    for (const move of activeProposal.moves) {
      const text = document.createElement('p');
      text.textContent = `${move.source} → ${move.destination}`;
      container.append(text);
    }
    for (const deletion of activeProposal.deletions ?? []) {
      const text = document.createElement('p');
      text.className = 'proposal-deletion';
      text.textContent = `🗑 Delete ${deletion}`;
      container.append(text);
    }
    if (
      activeProposal.writes.length ||
      activeProposal.moves.length ||
      (activeProposal.deletions && activeProposal.deletions.length)
    ) {
      const button = document.createElement('button');
      button.textContent = 'Apply file changes';
      button.type = 'button';
      button.addEventListener('click', () => {
        if (activeGenerations > 0) {
          status('Please wait for asset generation to finish before applying changes.', true);
          return;
        }
        void run(async () => {
          await save();
          render(
            await post<TheaterEditorState>('/apply', {
              revision: proposalRevision,
              proposal: activeProposal,
            }),
          );
          for (const generation of activeProposal.generations) {
            generation.references = generation.references.map(
              (path) => activeProposal.moves.find((move) => move.source === path)?.destination || path,
            );
          }
          clearPreview();
          button.disabled = true;
          button.dataset.done = 'true';
          status('File changes applied to your working theater.');
        }, 'Applying changes…');
      });
      container.append(button);
    }
    for (const generation of activeProposal.generations) {
      const card = document.createElement('div');
      card.className = 'generation-card';
      const kindLabel = GENERATION_KIND_LABELS[generation.kind] ?? generation.kind;
      const label = document.createElement('strong');
      label.textContent = `${kindLabel}: ${generation.name}`;
      const description = document.createElement('p');
      description.textContent = generation.prompt;
      const button = document.createElement('button');
      button.type = 'button';
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
        button.addEventListener('click', () => {
          void generate(generation, button);
        });
        card.append(label, description, button);
      }
      container.append(card);
    }
    if (
      !activeProposal.writes.length &&
      !activeProposal.moves.length &&
      !(activeProposal.deletions && activeProposal.deletions.length) &&
      !activeProposal.generations.length
    ) {
      container.hidden = true;
    }
  }

  async function ask(prompt: string): Promise<void> {
    if (!state || !prompt.trim() || busy || asking || activeGenerations > 0) return;
    asking = true;
    const sendBtn = requireButton('assistant-send');
    const inputEl = requireTextArea('assistant-input');
    sendBtn.disabled = true;
    inputEl.disabled = true;
    status('Your assistant is shaping a proposal…');
    try {
      captureText();
      if (dirty()) await save();
      message('user', prompt);
      const result = await post<AssistantResponse>('/assistant', {
        prompt,
        history: history.slice(-12),
      });
      if (result.state) render(result.state);
      history.push(
        { role: 'user', content: prompt },
        { role: 'assistant', content: result.proposal.message },
      );
      proposal = result.proposal;
      proposalRevision = result.revision;
      message('assistant', proposal.message);
      inputEl.value = '';
      renderProposal();
      await window.checkAuthStatus?.({ refresh: true });
      status(
        `Proposal ready. Charged ${result.credits_charged} credits. Review file changes or generate the suggested assets below.`,
      );
    } catch (error: unknown) {
      status(getErrorMessage(error), true);
    } finally {
      asking = false;
      sendBtn.disabled = false;
      inputEl.disabled = false;
    }
  }

  function openGoogleDialog(focusDoc = false): void {
    const urlInput = requireInput('google-link-url');
    urlInput.value = '';
    requireInput('google-link-name').value = '';
    requireTextArea('google-harvest-prompt').value = '';
    requireInput('google-doc-harvest').checked = true;
    requireEl('google-doc-options').hidden = !focusDoc;
    requireButton('google-link-submit').textContent = focusDoc ? 'Harvest with AI' : 'Import Link';
    requireDialog('google-link-dialog').showModal();
    urlInput.focus();
  }

  function updateGoogleDialogState(): void {
    const url = requireInput('google-link-url').value.trim();
    const isDoc = url.includes('docs.google.com/document');
    requireEl('google-doc-options').hidden = !isDoc;
    const submitBtn = requireButton('google-link-submit');
    if (isDoc) {
      submitBtn.textContent = requireInput('google-doc-harvest').checked
        ? 'Harvest with AI'
        : 'Import Lore File';
    } else {
      submitBtn.textContent = 'Import from Drive';
    }
  }

  async function submitGoogleLink(): Promise<void> {
    if (!state) return;
    const url = requireInput('google-link-url').value.trim();
    if (!url) return;
    const targetName = requireInput('google-link-name').value.trim() || undefined;
    const isDoc = url.includes('docs.google.com/document');
    const harvest = isDoc && requireInput('google-doc-harvest').checked;
    const harvestPrompt = requireTextArea('google-harvest-prompt').value.trim() || undefined;

    requireDialog('google-link-dialog').close();
    let importedFile: TheaterDraftFile | undefined;
    await run(async () => {
      if (!state) return;
      await save();
      const res = await post<GoogleLinkResponse>('/google-link', {
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
        proposalRevision = res.revision ?? null;
        message('assistant', proposal.message);
        renderProposal();
        await window.checkAuthStatus?.({ refresh: true });
        status(
          `Prepared Google Doc changes for review. Charged ${res.credits_charged ?? 0} credits.`,
        );
      } else {
        importedFile = state.files.find((f) => f.path === res.path);
        status(res.message || `Imported ${res.path ?? 'file'} from Google.`);
      }
    }, harvest ? 'Harvesting Google Doc with AI co-creator…' : 'Downloading and importing from Google…');
    if (importedFile) {
      await selectFile(importedFile);
    }
  }

  function renderGameLibraryList(): void {
    const listEl = getEl('game-library-list');
    if (!listEl) return;
    listEl.replaceChildren();
    const searchEl = getEl('game-library-search');
    const query = (searchEl instanceof HTMLInputElement ? searchEl.value : '').trim().toLowerCase();
    const filtered = availableGames.filter((game) => {
      if (!query) return true;
      const haystack = [
        game.title,
        game.id,
        game.description,
        game.genre,
        game.author,
        ...(Array.isArray(game.tags) ? game.tags : []),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return haystack.includes(query);
    });
    const submitBtn = getEl('game-library-submit');
    if (!filtered.length) {
      const empty = document.createElement('div');
      empty.className = 'game-library-empty';
      empty.textContent = availableGames.length
        ? 'No games match your search.'
        : 'No pre-defined games found in the repository.';
      listEl.append(empty);
      if (submitBtn instanceof HTMLButtonElement) submitBtn.disabled = true;
      return;
    }
    if (!selectedGameId || !filtered.some((g) => g.id === selectedGameId)) {
      selectedGameId = filtered[0].id;
    }
    if (submitBtn instanceof HTMLButtonElement) submitBtn.disabled = false;

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

      const selectCard = (): void => {
        selectedGameId = game.id;
        renderGameLibraryList();
      };
      card.addEventListener('click', selectCard);
      card.addEventListener('keydown', (event: KeyboardEvent) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          selectCard();
        }
      });
      listEl.append(card);
    }
  }

  async function openGameLibraryDialog(): Promise<void> {
    if (!state || busy || activeGenerations > 0) return;
    const dialog = getEl('game-library-dialog');
    if (!(dialog instanceof HTMLDialogElement)) return;
    const searchEl = getEl('game-library-search');
    if (searchEl instanceof HTMLInputElement) searchEl.value = '';
    const enableToolEl = getEl('game-enable-tool');
    if (enableToolEl instanceof HTMLInputElement) enableToolEl.checked = false;

    const listEl = getEl('game-library-list');
    if (listEl) {
      const loading = document.createElement('div');
      loading.className = 'game-library-empty';
      loading.textContent = 'Loading games from repository…';
      listEl.replaceChildren(loading);
    }
    const submitBtn = getEl('game-library-submit');
    if (submitBtn instanceof HTMLButtonElement) submitBtn.disabled = true;

    dialog.showModal();
    try {
      const games = await api<GameLibraryEntry[]>('/api/games');
      availableGames = Array.isArray(games) ? games : [];
      selectedGameId = availableGames[0]?.id ?? null;
      renderGameLibraryList();
      if (searchEl instanceof HTMLInputElement) searchEl.focus();
    } catch (error: unknown) {
      if (listEl) {
        const errNode = document.createElement('div');
        errNode.className = 'game-library-empty';
        errNode.textContent = getErrorMessage(error, 'Could not load games.');
        listEl.replaceChildren(errNode);
      }
    }
  }

  async function submitAttachGame(): Promise<void> {
    if (!state || !selectedGameId) return;
    const chosen = availableGames.find((g) => g.id === selectedGameId);
    const enableToolEl = getEl('game-enable-tool');
    const enableGameTool = enableToolEl instanceof HTMLInputElement && enableToolEl.checked;
    const dialog = getEl('game-library-dialog');
    if (dialog instanceof HTMLDialogElement) dialog.close();
    let targetFile: TheaterDraftFile | undefined;
    await run(async () => {
      if (!state) return;
      await save();
      const next = await post<TheaterEditorState>('/game', {
        revision: state.draft.revision,
        game_id: selectedGameId,
        enable_game_tool: enableGameTool,
      });
      expandedFolders.set('game', true);
      if (chosen && chosen.stamp_count > 0) expandedFolders.set('stamps', true);
      render(next);
      const declPath = chosen?.declaration_file || 'game/game.yaml';
      targetFile =
        state.files.find((f) => f.path === declPath) ??
        state.files.find((f) => f.path.startsWith('game/'));
      status(`Attached "${chosen?.title || selectedGameId}" to your working theater.`);
    }, `Attaching ${chosen?.title || selectedGameId}…`);
    if (targetFile) {
      await selectFile(targetFile);
    }
  }

  function getAvailableReferences(): ReferenceEntry[] {
    if (!state || !Array.isArray(state.files)) return [];
    const imageFiles = state.files.filter(
      (f) => f.kind === 'image' && !f.path.toLowerCase().endsWith('.svg'),
    );
    const charGroups = new Map<string, TheaterDraftFile>();
    const sceneGroups = new Map<string, TheaterDraftFile>();
    const flatScenes: ReferenceEntry[] = [];
    const generalRefs: ReferenceEntry[] = [];
    const iterScore = (path: string): number => {
      const base = (path.split('/').pop() ?? '').replace(/\.[^.]+$/, '');
      return /^\d+$/.test(base) ? parseInt(base, 10) : -1;
    };
    for (const file of imageFiles) {
      const parts = file.path.split('/');
      if (parts[0] !== 'references') continue;
      if (parts[1] === 'characters' && parts.length >= 4) {
        const charName = parts[2];
        const prev = charGroups.get(charName);
        if (
          !prev ||
          iterScore(file.path) > iterScore(prev.path) ||
          (iterScore(file.path) === iterScore(prev.path) && file.path > prev.path)
        ) {
          charGroups.set(charName, file);
        }
      } else if (parts[1] === 'scenes') {
        if (parts.length >= 4) {
          const sceneName = parts[2];
          const prev = sceneGroups.get(sceneName);
          if (
            !prev ||
            iterScore(file.path) > iterScore(prev.path) ||
            (iterScore(file.path) === iterScore(prev.path) && file.path > prev.path)
          ) {
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
    const entries: ReferenceEntry[] = [];
    const seen = new Set<string>();
    const pushEntry = (entry: ReferenceEntry): void => {
      const key = entry.name.toLowerCase();
      if (!key || seen.has(key)) return;
      seen.add(key);
      entries.push(entry);
    };
    for (const [name, file] of [...charGroups.entries()].sort((a, b) =>
      a[0].localeCompare(b[0]),
    )) {
      pushEntry({ name, kind: 'character', path: file.path });
    }
    for (const [name, file] of [...sceneGroups.entries()].sort((a, b) =>
      a[0].localeCompare(b[0]),
    )) {
      pushEntry({ name, kind: 'scene', path: file.path });
    }
    for (const item of flatScenes.sort((a, b) => a.name.localeCompare(b.name))) pushEntry(item);
    for (const item of generalRefs.sort((a, b) => a.name.localeCompare(b.name))) pushEntry(item);
    return entries;
  }

  function extractPromptReferences(promptText: string): string[] {
    if (!promptText || !promptText.includes('@')) return [];
    const available = getAvailableReferences();
    if (!available.length) return [];
    const sorted = [...available].sort((a, b) => b.name.length - a.name.length);
    const hits: ReferenceHit[] = [];
    for (const entry of sorted) {
      const tokens = entry.name.trim().split(/[\s_-]+/).filter(Boolean).map(escapeRegex);
      if (!tokens.length) continue;
      const pattern = new RegExp(
        `(?:^|[\\s([{"',;:])@(${tokens.join('[\\s_-]+')})(?=$|[\\s.,!?;:)\\]}"'])`,
        'gi',
      );
      let match: RegExpExecArray | null;
      while ((match = pattern.exec(promptText)) !== null) {
        hits.push({ index: match.index, path: entry.path });
      }
    }
    hits.sort((a, b) => a.index - b.index);
    const result: string[] = [];
    for (const hit of hits) {
      if (!result.includes(hit.path)) {
        result.push(hit.path);
        if (result.length >= 4) break;
      }
    }
    return result;
  }

  function attachReferenceAutocomplete(textarea: HTMLTextAreaElement): void {
    const parent = textarea.parentNode;
    if (!parent) return;
    const wrapper = document.createElement('div');
    wrapper.className = 'reference-mention-wrapper';
    parent.insertBefore(wrapper, textarea);
    wrapper.appendChild(textarea);
    const menu = document.createElement('div');
    menu.className = 'reference-mention-menu';
    menu.setAttribute('role', 'listbox');
    menu.hidden = true;
    wrapper.appendChild(menu);

    let filtered: ReferenceEntry[] = [];
    let activeIndex = 0;
    let mentionStart = -1;

    function closeMenu(): void {
      menu.hidden = true;
      menu.replaceChildren();
      filtered = [];
      activeIndex = 0;
      mentionStart = -1;
    }

    function applySelection(entry: ReferenceEntry | undefined): void {
      if (!entry || mentionStart < 0) return;
      const caret =
        typeof textarea.selectionStart === 'number'
          ? textarea.selectionStart
          : textarea.value.length;
      const before = textarea.value.slice(0, mentionStart);
      const after = textarea.value.slice(caret);
      const insertion = `@${entry.name} `;
      textarea.value = before + insertion + after.replace(/^\s+/, '');
      const nextCaret = before.length + insertion.length;
      textarea.focus();
      textarea.setSelectionRange(nextCaret, nextCaret);
      closeMenu();
    }

    function renderMenu(): void {
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
        option.addEventListener('mousedown', (event: MouseEvent) => {
          event.preventDefault();
          applySelection(entry);
        });
        option.addEventListener('click', (event: MouseEvent) => {
          event.preventDefault();
          applySelection(entry);
        });
        menu.appendChild(option);
      });
    }

    function updateMenu(): void {
      const caret =
        typeof textarea.selectionStart === 'number'
          ? textarea.selectionStart
          : textarea.value.length;
      const beforeCaret = textarea.value.slice(0, caret);
      const match = /(?:^|[\s([{"',;:])@([^@\r\n]*)$/.exec(beforeCaret);
      if (!match) {
        closeMenu();
        return;
      }
      const query = match[1].toLowerCase();
      mentionStart = caret - match[1].length - 1;
      const available = getAvailableReferences();
      const prefixMatches: ReferenceEntry[] = [];
      const substringMatches: ReferenceEntry[] = [];
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
    textarea.addEventListener('keydown', (event: KeyboardEvent) => {
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
      window.setTimeout(closeMenu, 150);
    });
  }

  async function performDelete(targetPath: string): Promise<void> {
    if (!targetPath || targetPath === 'theater.yaml' || !state) return;
    await run(async () => {
      if (!state) return;
      pendingWrites.delete(targetPath);
      if (selected?.path === targetPath) textDirty = false;
      await save();
      const next = await post<TheaterEditorState>('/delete', {
        revision: state.draft.revision,
        path: targetPath,
      });
      clearPreview();
      render(next);
      status(`Deleted ${targetPath}.`);
    }, `Deleting ${targetPath}…`);
  }

  async function performResetTheater(): Promise<void> {
    if (!state) return;
    await run(async () => {
      if (!state) return;
      const next = await post<TheaterEditorState>('/reset', {
        revision: state.draft.revision,
      });
      render(next);
      status('Theater reset.');
    }, 'Resetting theater…');
  }

  async function initialize(): Promise<void> {
    if (opening) return;
    opening = true;
    try {
      if (typeof window.getAuthState !== 'function') {
        throw new Error('Auth helper getAuthState is not loaded.');
      }
      const auth = await window.getAuthState();
      const userId = extractUserId(auth.user);
      if (!auth.authenticated || userId === null) {
        requireEl('login-gate').hidden = false;
        requireEl('workspace').hidden = true;
        requireEl('start-panel').hidden = true;
        return;
      }
      requireEl('login-gate').hidden = true;
      lastDraftKey = `narratron.builder.lastDraft:${userId}`;
      const identifier = new URLSearchParams(location.search).get('theater_id');
      if (identifier) {
        await run(() => load(identifier), 'Loading your theater…');
      } else {
        requireEl('start-panel').hidden = false;
        const previous = localStorage.getItem(lastDraftKey);
        const resumeBtn = requireButton('resume-draft');
        resumeBtn.hidden = !previous;
        resumeBtn.dataset.identifier = previous || '';
      }
    } catch (error: unknown) {
      status(getErrorMessage(error), true);
    } finally {
      opening = false;
    }
  }

  const authoringDialog = getEl('external-authoring-dialog');
  const authoringCopyFeedback = getEl('authoring-copy-feedback');
  const openAuthoringBtn = getEl('open-external-authoring');
  if (openAuthoringBtn && authoringDialog instanceof HTMLDialogElement) {
    openAuthoringBtn.addEventListener('click', () => {
      if (authoringCopyFeedback) authoringCopyFeedback.textContent = '';
      authoringDialog.showModal();
    });
  }
  const closeAuthoringBtn = getEl('external-authoring-close');
  if (closeAuthoringBtn && authoringDialog instanceof HTMLDialogElement) {
    closeAuthoringBtn.addEventListener('click', () => authoringDialog.close());
  }
  const doneAuthoringBtn = getEl('external-authoring-done');
  if (doneAuthoringBtn && authoringDialog instanceof HTMLDialogElement) {
    doneAuthoringBtn.addEventListener('click', () => authoringDialog.close());
  }
  if (authoringDialog instanceof HTMLDialogElement) {
    authoringDialog.addEventListener('click', (event: MouseEvent) => {
      if (event.target === authoringDialog) authoringDialog.close();
    });
    authoringDialog.addEventListener('keydown', (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        authoringDialog.close();
      }
    });
  }

  requireButton('copy-authoring-prompt').addEventListener('click', async () => {
    const prompt = requireTextArea('authoring-prompt');
    try {
      await navigator.clipboard.writeText(prompt.value);
      if (authoringCopyFeedback) authoringCopyFeedback.textContent = 'Copied to clipboard.';
      status('Starter prompt copied. Paste it into your AI app with the authoring kit.');
    } catch {
      prompt.focus();
      prompt.select();
      if (authoringCopyFeedback) {
        authoringCopyFeedback.textContent = 'Selected — copy and paste into your AI app.';
      }
      status('Copy the selected starter prompt and paste it into your AI app.');
    }
  });

  requireButton('start-default').addEventListener('click', () => {
    void run(async () => {
      await create();
      status('Default theater ready. Make it your own.');
    }, 'Preparing your theater…');
  });

  requireButton('resume-draft').addEventListener('click', () => {
    const identifier = requireButton('resume-draft').dataset.identifier ?? '';
    if (!identifier) return;
    void run(() => load(identifier), 'Opening your last theater…');
  });

  const folderInput = requireInput('folder-input');
  const assetsInput = requireInput('assets-input');

  requireButton('start-folder').addEventListener('click', () => folderInput.click());
  requireButton('upload-folder').addEventListener('click', () => folderInput.click());
  requireButton('upload-assets').addEventListener('click', () => assetsInput.click());

  folderInput.addEventListener('change', (event: Event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || !input.files) return;
    const files = [...input.files];
    input.value = '';
    void handleUploadRequest(files, true);
  });

  assetsInput.addEventListener('change', (event: Event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || !input.files) return;
    const files = [...input.files];
    input.value = '';
    void handleUploadRequest(files, false);
  });

  const fileEditor = requireTextArea('file-editor');
  fileEditor.addEventListener('input', () => {
    textDirty = true;
    updateSaveState();
  });

  const toggleSourceBtn = getEl('toggle-source');
  if (toggleSourceBtn instanceof HTMLButtonElement) {
    toggleSourceBtn.addEventListener('click', async () => {
      if (!selected || !selected.path.toLowerCase().endsWith('.svg')) return;
      const viewingSource = !fileEditor.hidden;
      if (viewingSource) {
        captureText();
        const url = `${endpoint('/file')}?path=${encodeURIComponent(selected.path)}`;
        renderSvgPreview(selected, fileEditor.value, url);
        toggleSourceBtn.textContent = 'Source';
      } else {
        let content = pendingWrites.get(selected.path);
        if (content === undefined) {
          const url = `${endpoint('/file')}?path=${encodeURIComponent(selected.path)}`;
          try {
            const res = await fetch(url);
            if (!res.ok) throw new Error('Could not load this file.');
            content = await res.text();
          } catch (error: unknown) {
            status(getErrorMessage(error, 'Could not load this file.'), true);
            return;
          }
        }
        fileEditor.value = content;
        fileEditor.hidden = false;
        requireEl('media-preview').hidden = true;
        toggleSourceBtn.textContent = 'Preview';
      }
    });
  }

  requireInput('draft-name').addEventListener('input', () => {
    nameDirty = true;
    updateSaveState();
  });

  requireButton('save-draft').addEventListener('click', () => {
    void run(async () => {
      await save(true);
      status('Theater saved.');
    }, 'Saving theater…');
  });

  requireButton('download-draft').addEventListener('click', () => {
    void run(async () => {
      if (!state) return;
      await save();
      const link = document.createElement('a');
      link.href = endpoint('/download');
      link.download = `${state.draft.name || state.draft.theater_id}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      status('Theater downloaded.');
    }, 'Preparing theater download…');
  });

  requireButton('reload-draft').addEventListener('click', () => {
    if (!state) return;
    const theaterId = state.draft.theater_id;
    if (!dirty() || confirm('Discard unsubmitted edits and reload the working theater?')) {
      void run(() => load(theaterId), 'Reloading theater…');
    }
  });

  requireButton('deploy-draft').addEventListener('click', () => {
    void run(async () => {
      if (!state) return;
      await save(true);
      const result = await post<DeployResponse>('/deploy', { revision: state.draft.revision });
      status('Deployed. Opening your canvas…');
      location.assign(result.canvas_url);
    }, 'Deploying your theater…');
  });

  requireButton('new-draft').addEventListener('click', () => {
    if (unsaved() && !confirm('Leave without saving this theater and start another?')) return;
    state = null;
    pendingWrites.clear();
    textDirty = false;
    nameDirty = false;
    requireEl('workspace').hidden = true;
    requireEl('start-panel').hidden = false;
    window.history.replaceState(null, '', '/theater-editor');
    status('');
  });

  requireForm('assistant-form').addEventListener('submit', (event: SubmitEvent) => {
    event.preventDefault();
    void ask(requireTextArea('assistant-input').value.trim());
  });

  requireButton('organize-assets').addEventListener('click', () => {
    void ask(
      'Organize my uploaded assets into meaningful reference subfolders, references/characters/ folders, references/scenes/ folders, stamps/ tokens, lore documents, and named playlists. Update all file references where necessary.',
    );
  });

  requireButton('suggest-world').addEventListener('click', () => {
    void ask(
      'Develop this theater into a coherent world using its existing assets and lore. Propose scenes (with lore dossiers, references/scenes/ imagery, and scene.yaml), characters (with lore dossiers and references/characters/ portraits), reference images, stamp tokens for tactical play, and atmospheric playlist tracks.',
    );
  });

  requireButton('harvest-doc-shortcut').addEventListener('click', () => openGoogleDialog(true));
  requireButton('import-google-link').addEventListener('click', () => openGoogleDialog(false));

  const attachGameBtn = getEl('attach-game');
  if (attachGameBtn) {
    attachGameBtn.addEventListener('click', () => {
      void openGameLibraryDialog();
    });
  }
  const gameCancelBtn = getEl('game-library-cancel');
  const gameDialog = getEl('game-library-dialog');
  if (gameCancelBtn && gameDialog instanceof HTMLDialogElement) {
    gameCancelBtn.addEventListener('click', () => gameDialog.close());
  }
  const gameSearchInput = getEl('game-library-search');
  if (gameSearchInput) {
    gameSearchInput.addEventListener('input', renderGameLibraryList);
  }
  const gameForm = getEl('game-library-form');
  if (gameForm instanceof HTMLFormElement) {
    gameForm.addEventListener('submit', (event: SubmitEvent) => {
      event.preventDefault();
      void submitAttachGame();
    });
  }
  if (gameDialog instanceof HTMLDialogElement) {
    gameDialog.addEventListener('click', (event: MouseEvent) => {
      if (event.target === gameDialog) gameDialog.close();
    });
  }

  requireButton('google-link-cancel').addEventListener('click', () => {
    requireDialog('google-link-dialog').close();
  });
  requireInput('google-link-url').addEventListener('input', updateGoogleDialogState);
  requireInput('google-doc-harvest').addEventListener('change', updateGoogleDialogState);
  requireForm('google-link-form').addEventListener('submit', (event: SubmitEvent) => {
    event.preventDefault();
    void submitGoogleLink();
  });

  attachReferenceAutocomplete(requireTextArea('generation-prompt'));
  attachReferenceAutocomplete(requireTextArea('assistant-input'));

  requireSelect('generation-kind').addEventListener('change', updateGenerationCost);
  requireForm('generation-form').addEventListener('submit', (event: SubmitEvent) => {
    event.preventDefault();
    const submitBtn = requireButton('generation-submit');
    if (submitBtn.disabled || submitBtn.dataset.generating === 'true') return;
    const rawKind = requireSelect('generation-kind').value;
    if (!isGenerationKind(rawKind)) {
      throw new Error(`Unsupported generation kind: ${rawKind}`);
    }
    const promptValue = requireTextArea('generation-prompt').value;
    void generate(
      {
        kind: rawKind,
        name: requireInput('generation-name').value,
        playlist: requireInput('generation-playlist').value || 'ambient',
        animation_type: requireSelect('generation-animation-type').value,
        prompt: promptValue,
        references: extractPromptReferences(promptValue),
      },
      submitBtn,
    );
  });

  const newFileDialog = requireDialog('new-file-dialog');
  const newFilePathInput = requireInput('new-file-path');
  requireButton('new-file').addEventListener('click', () => {
    newFilePathInput.value = '';
    newFileDialog.showModal();
    newFilePathInput.focus();
  });
  requireButton('new-file-cancel').addEventListener('click', () => newFileDialog.close('cancel'));
  newFileDialog.addEventListener('click', (event: MouseEvent) => {
    if (event.target === newFileDialog) newFileDialog.close('cancel');
  });
  newFileDialog.addEventListener('keydown', (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      newFileDialog.close('cancel');
    }
  });
  newFileDialog.addEventListener('close', () => {
    if (newFileDialog.returnValue !== 'create' || !state) return;
    const path = newFilePathInput.value.trim();
    if (state.files.some((file) => file.path === path)) {
      status('That file already exists. Select it to edit.', true);
      return;
    }
    void run(async () => {
      if (!state) return;
      await save();
      const next = await post<TheaterEditorState>('/save', {
        revision: state.draft.revision,
        name: state.draft.name,
        writes: [{ path, content: /\.(yaml|yml|json)$/.test(path) ? '{}\n' : '' }],
      });
      render(next);
      status(`Created ${path}. Select it to begin writing.`);
    }, 'Creating file…');
  });

  const deleteFileDialog = requireDialog('delete-file-dialog');
  requireButton('delete-file').addEventListener('click', () => {
    if (!selected || selected.path === 'theater.yaml') return;
    requireEl('delete-file-prompt').textContent =
      `Are you sure you want to delete "${selected.path}" from this theater? This cannot be undone.`;
    deleteFileDialog.showModal();
  });
  requireButton('delete-file-cancel').addEventListener('click', () => deleteFileDialog.close());
  deleteFileDialog.querySelector('form')?.addEventListener('submit', (event: SubmitEvent) => {
    event.preventDefault();
    deleteFileDialog.close();
    if (selected && selected.path !== 'theater.yaml') {
      void performDelete(selected.path);
    }
  });

  const resetBtn = getEl('reset-theater');
  const resetDialog = getEl('reset-theater-dialog');
  const resetCancel = getEl('reset-theater-cancel');
  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      if (!state) return;
      if (resetDialog instanceof HTMLDialogElement && typeof resetDialog.showModal === 'function') {
        resetDialog.showModal();
      } else if (
        confirm(
          'Are you sure you want to reset this theater? All generated output, canvas history, theater state, and reference and scene image versions after the first image (1.webp) will be permanently deleted.',
        )
      ) {
        void performResetTheater();
      }
    });
  }
  if (resetCancel && resetDialog instanceof HTMLDialogElement) {
    resetCancel.addEventListener('click', () => resetDialog.close());
  }
  if (resetDialog instanceof HTMLDialogElement) {
    resetDialog.addEventListener('click', (event: MouseEvent) => {
      if (event.target === resetDialog) resetDialog.close();
    });
    resetDialog.addEventListener('keydown', (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        resetDialog.close();
      }
    });
    const form = resetDialog.querySelector('form');
    if (form) {
      form.addEventListener('submit', (event: SubmitEvent) => {
        event.preventDefault();
        resetDialog.close();
        void performResetTheater();
      });
    }
  }

  const pngDialog = getEl('png-warning-dialog');
  const pngCancel = getEl('png-warning-cancel');
  const pngSkip = getEl('png-warning-skip');
  const pngForm = getEl('png-warning-form');
  if (pngCancel && pngDialog instanceof HTMLDialogElement) {
    pngCancel.addEventListener('click', () => {
      pendingUpload = null;
      pngDialog.close('cancel');
    });
  }
  if (pngDialog instanceof HTMLDialogElement) {
    pngDialog.addEventListener('click', (event: MouseEvent) => {
      if (event.target === pngDialog) {
        pendingUpload = null;
        pngDialog.close('cancel');
      }
    });
    pngDialog.addEventListener('keydown', (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        pendingUpload = null;
        pngDialog.close('cancel');
      }
    });
  }
  if (pngSkip && pngDialog instanceof HTMLDialogElement) {
    pngSkip.addEventListener('click', () => {
      if (!pendingUpload) return;
      const { files, isFolder } = pendingUpload;
      savePngPreference('skip');
      pendingUpload = null;
      pngDialog.close('skip');
      void upload(files, isFolder);
    });
  }
  if (pngForm instanceof HTMLFormElement && pngDialog instanceof HTMLDialogElement) {
    pngForm.addEventListener('submit', async (event: SubmitEvent) => {
      event.preventDefault();
      if (!pendingUpload) return;
      const { files, isFolder } = pendingUpload;
      const formatEl = getEl('png-convert-format');
      const targetFormat =
        formatEl instanceof HTMLSelectElement ? formatEl.value : 'image/webp';
      savePngPreference('convert', targetFormat);
      pendingUpload = null;
      pngDialog.close('convert');
      status('Converting PNG images before uploading…');
      const processedFiles = await convertPngFiles(files, targetFormat);
      await upload(processedFiles, isFolder);
    });
  }

  window.addEventListener('beforeunload', (event: BeforeUnloadEvent) => {
    if (unsaved() || activeGenerations > 0) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  window.addEventListener('narratron:auth-changed', (event: Event) => {
    if (!(event instanceof CustomEvent) || !isAuthChangedDetail(event.detail)) return;
    if (!event.detail.authenticated) {
      state = null;
      lastDraftKey = null;
      activeGenerations = 0;
      generatingKeys.clear();
      asking = false;
      pendingWrites.clear();
      textDirty = false;
      nameDirty = false;
      clearPreview();
      history.length = 0;
      requireEl('assistant-messages').replaceChildren();
      requireEl('assistant-proposal').replaceChildren();
      requireEl('workspace').hidden = true;
      requireEl('start-panel').hidden = true;
      requireEl('login-gate').hidden = false;
    }
  });

  window.onAuthSuccess = (mode?: string) => {
    if (mode !== 'logout') {
      void initialize();
    }
  };

  window.beforeCreditPurchase = async () => {
    if (busy || activeGenerations > 0) {
      throw new Error('Please wait for the current action to finish before purchasing credits.');
    }
    await save();
  };

  document.getElementById('login-gate-button')?.addEventListener('click', () => {
    window.openAuthModal?.('login');
  });

  void initialize();
  return { initialize };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initializeTheaterEditor();
  });
} else {
  initializeTheaterEditor();
}
