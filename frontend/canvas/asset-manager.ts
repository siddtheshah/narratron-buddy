export type StampManagerTab = 'stamps' | 'scenes' | 'characters' | 'playlists' | 'animations';
export type ExtraAssetKind = 'playlists' | 'animations';
export type AssetGenerationKind = 'character' | 'animation' | 'music';

export interface TheaterScene {
  readonly id?: string;
  readonly name: string;
  readonly description?: string;
  readonly url?: string | null;
  readonly image_url?: string | null;
}

export interface TheaterCharacter {
  readonly id?: string;
  readonly name: string;
  readonly description?: string;
  readonly personality?: string;
  readonly quirk?: string;
  readonly gender?: string;
  readonly voice_tags?: readonly string[];
  readonly url?: string | null;
  readonly image_url?: string | null;
}

export interface AssetTrack {
  readonly id: string;
  readonly name: string;
  readonly description?: string;
  readonly url?: string;
  readonly type?: string;
}

export interface TheaterPlaylist {
  readonly id: string;
  readonly name: string;
  readonly description?: string;
  readonly tracks: readonly AssetTrack[];
}

export interface TheaterAnimation {
  readonly id: string;
  readonly name?: string;
  readonly description?: string;
  readonly url?: string;
  readonly type?: string;
}

export interface MusicSelectionState {
  readonly music_id?: string;
  readonly playlist?: string;
  readonly tracks?: readonly string[];
  readonly paused?: boolean;
}

export interface TheaterStampSummary {
  readonly id: string | number;
  readonly name?: string;
  readonly url?: string;
  readonly is_theater_stamp?: boolean;
}

export interface AssetManagerElements {
  readonly stampManagerTabs: HTMLElement | null;
  readonly stampTabStampsBtn: HTMLElement | null;
  readonly stampTabScenesBtn: HTMLElement | null;
  readonly stampTabCharactersBtn: HTMLElement | null;
  readonly stampTabPlaylistsBtn: HTMLElement | null;
  readonly stampTabAnimationsBtn: HTMLElement | null;
  readonly stampManagerBody: HTMLElement | null;
  readonly stampManagerCount: HTMLElement | null;
  readonly stampManagerPermissionBanner: HTMLElement | null;
  readonly stampManagerGrid: HTMLElement | null;
  readonly stampManagerEmpty: HTMLElement | null;
  readonly stampGenerationForm: HTMLElement | null;
  readonly stampGenerationStatus: HTMLElement | null;
  readonly scenesManagerGrid: HTMLElement | null;
  readonly scenesManagerEmpty: HTMLElement | null;
  readonly scenesEmptyMessage: HTMLElement | null;
  readonly charactersManagerGrid: HTMLElement | null;
  readonly charactersManagerEmpty: HTMLElement | null;
  readonly charactersEmptyMessage: HTMLElement | null;
  readonly playlistsManager: HTMLElement | null;
  readonly playlistsManagerContent: HTMLElement | null;
  readonly animationsManager: HTMLElement | null;
  readonly sceneGenerationForm: HTMLFormElement | null;
  readonly sceneGenerationPrompt: HTMLTextAreaElement | null;
  readonly sceneGenerationType: HTMLInputElement | null;
  readonly sceneGenerationTypeLabel: HTMLElement | null;
  readonly sceneGenerationStatus: HTMLElement | null;
  readonly scenePresetMenu: HTMLDetailsElement | null;
  readonly animationPresetMenu: HTMLDetailsElement | null;
  readonly animationGenerationType: HTMLInputElement | null;
  readonly animationGenerationTypeLabel: HTMLElement | null;
  readonly assetGenerationStatus: HTMLElement | null;
  readonly assetGenerationPending: HTMLElement | null;
  readonly characterGenerationForm: HTMLFormElement | null;
  readonly characterGenerationPrompt: HTMLTextAreaElement | null;
  readonly animationGenerationForm: HTMLFormElement | null;
  readonly animationGenerationPrompt: HTMLTextAreaElement | null;
  readonly musicGenerationForm: HTMLFormElement | null;
  readonly musicGenerationPrompt: HTMLTextAreaElement | null;
}

export interface CreateCanvasAssetManagerOptions {
  readonly elements?: Partial<AssetManagerElements>;
  readonly getTheaterId: () => string | null;
  readonly isOrator: () => boolean;
  readonly isStampOwner: () => boolean;
  readonly isManagerOpen: () => boolean;
  readonly getActiveTab: () => StampManagerTab;
  readonly setActiveTab: (tab: StampManagerTab) => void;
  readonly getCachedScenes: () => TheaterScene[] | null;
  readonly setCachedScenes: (scenes: TheaterScene[] | null) => void;
  readonly getCachedCharacters: () => TheaterCharacter[] | null;
  readonly setCachedCharacters: (characters: TheaterCharacter[] | null) => void;
  readonly getCurrentTrackSrc?: () => string | null;
  readonly setCurrentTrackSrc?: (src: string | null) => void;
  readonly nameFromPrompt?: (prompt: string) => string;
  readonly extractTaggedReferences?: (prompt: string) => string[];
  readonly applyAssetSearch?: () => void;
  readonly resolveWebpQualityUrl?: (url: string) => string;
  readonly showShareToast?: (message: string) => void;
  readonly fetchLatestImage?: () => void;
  readonly refreshCanvasState?: (domains?: readonly string[]) => void;
  readonly onShowStampsTab?: () => void;
  readonly onTheaterStampsUpdated?: (stamps: TheaterStampSummary[]) => void;
  readonly bindPrimaryTabButtons?: boolean;
  readonly expandedPlaylists?: Set<string>;
}

export interface CanvasAssetManager {
  updateStampTabsVisibility(): void;
  switchStampManagerTab(tab: StampManagerTab): void;
  updateAssetGenerationUI(): void;
  preloadItemImages(items: readonly { readonly url?: string | null; readonly image_url?: string | null }[] | null | undefined): void;
  getPreloadedImageUrls(): ReadonlySet<string>;
  handleDoodleSocketData(data: unknown): boolean;
  updateAssetPlaybackSelection(music: MusicSelectionState | null): void;
  loadExtraAssets(kind: ExtraAssetKind): Promise<void>;
  loadTheaterScenes(): Promise<void>;
  renderScenesManagerGrid(scenes: readonly TheaterScene[] | null | undefined): void;
  pushSceneToCanvas(scene: TheaterScene, cardEl: HTMLElement | null): Promise<void>;
  loadTheaterCharacters(): Promise<void>;
  renderCharactersManagerGrid(characters: readonly TheaterCharacter[] | null | undefined): void;
  pushCharacterToCanvas(character: TheaterCharacter, cardEl: HTMLElement | null): Promise<void>;
  revertAllOtherPushItems(currentElement: HTMLElement | null): void;
  dispose(): void;
}

interface PendingSceneGeneration {
  readonly name: string;
  readonly prompt: string;
  readonly sceneType: string;
}

interface PendingAssetGeneration {
  readonly kind: AssetGenerationKind;
  readonly name: string;
  readonly prompt: string;
  readonly theaterId: string;
}

interface GenerateApiResponse {
  readonly detail?: string;
  readonly credits_charged?: number;
  readonly scenes?: TheaterScene[];
  readonly characters?: TheaterCharacter[];
}

interface PushApiResponse {
  readonly detail?: string;
  readonly message?: string;
}

interface AssetButtonInput {
  readonly id: string;
  readonly name?: string;
  readonly description?: string;
  readonly url?: string;
  readonly type?: string;
  readonly tracks?: readonly AssetTrack[];
}

const ASSET_GENERATION_KINDS: Readonly<Record<'characters' | 'animations' | 'playlists', AssetGenerationKind>> = {
  characters: 'character',
  animations: 'animation',
  playlists: 'music',
};

const DEFAULT_STOPWORDS = new Set(
  (
    'a an the and or but of for to in on at by with from into onto over under ' +
    'as is are was were be been being it its this that these those ' +
    'i me my we our you your he him his she her they them their ' +
    'some any each all no not so very can could should would will ' +
    'do does did have has had about up down out through between ' +
    'who whom whose what when where why how than then there here ' +
    'both few more most other such only own same too until while ' +
    'again further once during before after above below against off just now'
  ).split(' '),
);

function defaultNameFromPrompt(prompt: string): string {
  const words = prompt.match(/[\p{L}\p{N}]+(?:['’\-][\p{L}\p{N}]+)*/gu) ?? [];
  const name = words
    .filter((word) => !DEFAULT_STOPWORDS.has(word.toLowerCase()))
    .slice(0, 3)
    .join(' ');
  return (name || 'Stamp').slice(0, 100);
}

function queryElements(overrides: Partial<AssetManagerElements> | undefined): AssetManagerElements {
  const byId = (id: string): HTMLElement | null => document.getElementById(id);
  return {
    stampManagerTabs: overrides?.stampManagerTabs ?? byId('stamp-manager-tabs'),
    stampTabStampsBtn: overrides?.stampTabStampsBtn ?? byId('stamp-tab-stamps'),
    stampTabScenesBtn: overrides?.stampTabScenesBtn ?? byId('stamp-tab-scenes'),
    stampTabCharactersBtn: overrides?.stampTabCharactersBtn ?? byId('stamp-tab-characters'),
    stampTabPlaylistsBtn: overrides?.stampTabPlaylistsBtn ?? byId('stamp-tab-playlists'),
    stampTabAnimationsBtn: overrides?.stampTabAnimationsBtn ?? byId('stamp-tab-animations'),
    stampManagerBody: overrides?.stampManagerBody ?? byId('stamp-manager-body'),
    stampManagerCount: overrides?.stampManagerCount ?? byId('stamp-manager-count'),
    stampManagerPermissionBanner:
      overrides?.stampManagerPermissionBanner ?? byId('stamp-manager-permission-banner'),
    stampManagerGrid: overrides?.stampManagerGrid ?? byId('stamp-manager-grid'),
    stampManagerEmpty: overrides?.stampManagerEmpty ?? byId('stamp-manager-empty'),
    stampGenerationForm: overrides?.stampGenerationForm ?? byId('stamp-generation-form'),
    stampGenerationStatus: overrides?.stampGenerationStatus ?? byId('stamp-generation-status'),
    scenesManagerGrid: overrides?.scenesManagerGrid ?? byId('scenes-manager-grid'),
    scenesManagerEmpty: overrides?.scenesManagerEmpty ?? byId('scenes-manager-empty'),
    scenesEmptyMessage: overrides?.scenesEmptyMessage ?? byId('scenes-empty-message'),
    charactersManagerGrid: overrides?.charactersManagerGrid ?? byId('characters-manager-grid'),
    charactersManagerEmpty: overrides?.charactersManagerEmpty ?? byId('characters-manager-empty'),
    charactersEmptyMessage: overrides?.charactersEmptyMessage ?? byId('characters-empty-message'),
    playlistsManager: overrides?.playlistsManager ?? byId('playlists-manager'),
    playlistsManagerContent: overrides?.playlistsManagerContent ?? byId('playlists-manager-content'),
    animationsManager: overrides?.animationsManager ?? byId('animations-manager'),
    sceneGenerationForm:
      overrides?.sceneGenerationForm ??
      (byId('scene-generation-form') as HTMLFormElement | null),
    sceneGenerationPrompt:
      overrides?.sceneGenerationPrompt ??
      (byId('scene-generation-prompt') as HTMLTextAreaElement | null),
    sceneGenerationType:
      overrides?.sceneGenerationType ??
      (byId('scene-generation-type') as HTMLInputElement | null),
    sceneGenerationTypeLabel:
      overrides?.sceneGenerationTypeLabel ?? byId('scene-generation-type-label'),
    sceneGenerationStatus: overrides?.sceneGenerationStatus ?? byId('scene-generation-status'),
    scenePresetMenu:
      overrides?.scenePresetMenu ??
      (byId('scene-generation-presets') as HTMLDetailsElement | null),
    animationPresetMenu:
      overrides?.animationPresetMenu ??
      (byId('animation-generation-presets') as HTMLDetailsElement | null),
    animationGenerationType:
      overrides?.animationGenerationType ??
      (byId('animation-generation-type') as HTMLInputElement | null),
    animationGenerationTypeLabel:
      overrides?.animationGenerationTypeLabel ?? byId('animation-generation-type-label'),
    assetGenerationStatus: overrides?.assetGenerationStatus ?? byId('asset-generation-status'),
    assetGenerationPending: overrides?.assetGenerationPending ?? byId('asset-generation-pending'),
    characterGenerationForm:
      overrides?.characterGenerationForm ??
      (byId('character-generation-form') as HTMLFormElement | null),
    characterGenerationPrompt:
      overrides?.characterGenerationPrompt ??
      (byId('character-generation-prompt') as HTMLTextAreaElement | null),
    animationGenerationForm:
      overrides?.animationGenerationForm ??
      (byId('animation-generation-form') as HTMLFormElement | null),
    animationGenerationPrompt:
      overrides?.animationGenerationPrompt ??
      (byId('animation-generation-prompt') as HTMLTextAreaElement | null),
    musicGenerationForm:
      overrides?.musicGenerationForm ??
      (byId('music-generation-form') as HTMLFormElement | null),
    musicGenerationPrompt:
      overrides?.musicGenerationPrompt ??
      (byId('music-generation-prompt') as HTMLTextAreaElement | null),
  };
}

function hasTypeField(value: unknown): value is { readonly type: string } {
  if (!value || typeof value !== 'object') return false;
  const record = value as { readonly type?: unknown };
  return typeof record.type === 'string';
}

/** Manage theater scenes, characters, playlists, animations, and asset generation tabs. */
export function createCanvasAssetManager(
  options: CreateCanvasAssetManagerOptions,
): CanvasAssetManager {
  const {
    getTheaterId,
    isOrator,
    isStampOwner,
    isManagerOpen,
    getActiveTab,
    setActiveTab,
    getCachedScenes,
    setCachedScenes,
    getCachedCharacters,
    setCachedCharacters,
    getCurrentTrackSrc = () => null,
    setCurrentTrackSrc = () => {},
    nameFromPrompt = defaultNameFromPrompt,
    extractTaggedReferences = () => [],
    applyAssetSearch = () => {},
    resolveWebpQualityUrl = (url: string) => url,
    showShareToast = () => {},
    fetchLatestImage,
    refreshCanvasState,
    onShowStampsTab = () => {},
    onTheaterStampsUpdated = () => {},
    bindPrimaryTabButtons = false,
    expandedPlaylists = new Set<string>(),
  } = options;

  const el = queryElements(options.elements);
  const cleanupCallbacks: Array<() => void> = [];

  const preloadedImageUrls = new Set<string>();
  const pendingSceneGenerations = new Map<number, PendingSceneGeneration>();
  let nextSceneGenerationId = 0;

  const pendingAssetGenerations = new Map<number, PendingAssetGeneration>();
  const assetGenerationMessages = new Map<AssetGenerationKind, string>();
  let nextAssetGenerationId = 0;

  interface TheaterCollectionState<
    T extends { readonly url?: string | null; readonly image_url?: string | null },
  > {
    epoch: number;
    activeRequestId: number;
    inFlightTheaterId: string | null;
    readonly tab: 'scenes' | 'characters';
    readonly endpoint: 'scenes' | 'characters';
    readonly getCached: () => T[] | null;
    readonly setCached: (items: T[] | null) => void;
    readonly renderGrid: (items: readonly T[] | null | undefined) => void;
    readonly gridEl: HTMLElement | null;
    readonly emptyEl: HTMLElement | null;
    readonly emptyMessageEl: HTMLElement | null;
    readonly errorText: string;
  }

  let assetLoadVersion = 0;
  let assetMusicState: MusicSelectionState | null = null;
  let isPushingScene = false;
  let isPushingCharacter = false;

  const scenesCollectionState: TheaterCollectionState<TheaterScene> = {
    epoch: 0,
    activeRequestId: 0,
    inFlightTheaterId: null,
    tab: 'scenes',
    endpoint: 'scenes',
    getCached: getCachedScenes,
    setCached: setCachedScenes,
    renderGrid: (items) => renderScenesManagerGrid(items),
    gridEl: el.scenesManagerGrid,
    emptyEl: el.scenesManagerEmpty,
    emptyMessageEl: el.scenesEmptyMessage,
    errorText: 'Unable to load scenes at this time.',
  };

  const charactersCollectionState: TheaterCollectionState<TheaterCharacter> = {
    epoch: 0,
    activeRequestId: 0,
    inFlightTheaterId: null,
    tab: 'characters',
    endpoint: 'characters',
    getCached: getCachedCharacters,
    setCached: setCachedCharacters,
    renderGrid: (items) => renderCharactersManagerGrid(items),
    gridEl: el.charactersManagerGrid,
    emptyEl: el.charactersManagerEmpty,
    emptyMessageEl: el.charactersEmptyMessage,
    errorText: 'Unable to load characters at this time.',
  };

  function writeCollectionCache<
    T extends { readonly url?: string | null; readonly image_url?: string | null },
  >(state: TheaterCollectionState<T>, items: T[] | null): void {
    state.epoch += 1;
    state.setCached(items);
  }

  const extraAssetPanels: Readonly<Record<ExtraAssetKind, HTMLElement | null>> = {
    playlists: el.playlistsManager,
    animations: el.animationsManager,
  };

  function listen(
    target: EventTarget | null | undefined,
    type: string,
    handler: EventListener,
  ): void {
    if (!target) return;
    target.addEventListener(type, handler);
    cleanupCallbacks.push(() => target.removeEventListener(type, handler));
  }

  function updateSceneGenerationStatus(message = ''): void {
    const count = pendingSceneGenerations.size;
    const pending = count
      ? `${count} scene${count === 1 ? '' : 's'} generating. You can generate another while you wait.`
      : '';
    if (el.sceneGenerationStatus) {
      el.sceneGenerationStatus.textContent = [message, pending].filter(Boolean).join(' ');
    }
    if (el.sceneGenerationForm) {
      el.sceneGenerationForm.setAttribute('aria-busy', String(count > 0));
    }
  }

  function wirePresetMenu(
    menu: HTMLDetailsElement | null,
    attributeSelector: string,
    datasetKey: 'sceneType' | 'animationType',
    typeInput: HTMLInputElement | null,
    typeLabel: HTMLElement | null,
  ): void {
    if (!menu) return;
    const optionsList = Array.from(menu.querySelectorAll<HTMLElement>(attributeSelector));
    for (const option of optionsList) {
      listen(option, 'click', () => {
        const value = option.dataset[datasetKey] ?? '';
        if (typeInput) typeInput.value = value;
        if (typeLabel) typeLabel.textContent = option.textContent;
        for (const item of optionsList) {
          item.setAttribute('aria-pressed', String(item === option));
        }
        menu.open = false;
        menu.querySelector('summary')?.focus();
      });
    }
    listen(document, 'pointerdown', (event: Event) => {
      const target = event.target as Node | null;
      if (target && !menu.contains(target)) {
        menu.open = false;
      }
    });
    listen(menu, 'keydown', (event: Event) => {
      const keyEvent = event as KeyboardEvent;
      if (keyEvent.key === 'Escape') {
        menu.open = false;
        menu.querySelector('summary')?.focus();
      }
    });
  }

  wirePresetMenu(
    el.scenePresetMenu,
    '[data-scene-type]',
    'sceneType',
    el.sceneGenerationType,
    el.sceneGenerationTypeLabel,
  );
  wirePresetMenu(
    el.animationPresetMenu,
    '[data-animation-type]',
    'animationType',
    el.animationGenerationType,
    el.animationGenerationTypeLabel,
  );

  function getAssetGenerationKind(tab: StampManagerTab): AssetGenerationKind | null {
    if (tab === 'characters' || tab === 'animations' || tab === 'playlists') {
      return ASSET_GENERATION_KINDS[tab];
    }
    return null;
  }

  function getAssetFormAndPrompt(
    kind: AssetGenerationKind,
  ): { form: HTMLFormElement | null; prompt: HTMLTextAreaElement | null } {
    if (kind === 'character') {
      return { form: el.characterGenerationForm, prompt: el.characterGenerationPrompt };
    }
    if (kind === 'animation') {
      return { form: el.animationGenerationForm, prompt: el.animationGenerationPrompt };
    }
    return { form: el.musicGenerationForm, prompt: el.musicGenerationPrompt };
  }

  function updateAssetGenerationUI(): void {
    const activeTab = getActiveTab();
    const kind = getAssetGenerationKind(activeTab);
    const stampOwner = isStampOwner();
    const theaterId = getTheaterId();

    for (const value of Object.values(ASSET_GENERATION_KINDS)) {
      const { form } = getAssetFormAndPrompt(value);
      if (form) {
        form.hidden = !stampOwner || kind !== value;
      }
    }
    if (el.assetGenerationStatus) {
      el.assetGenerationStatus.hidden = !stampOwner || !kind;
    }

    const jobs = Array.from(pendingAssetGenerations.values()).filter(
      (job) => job.kind === kind && job.theaterId === theaterId,
    );
    const note =
      kind === 'character'
        ? 'Uses image generation credits.'
        : `Uses ${kind || 'asset'} generation credits.`;
    if (el.assetGenerationStatus) {
      el.assetGenerationStatus.textContent = [
        (kind ? assetGenerationMessages.get(kind) : undefined) || `${note} Saved to this theater.`,
        jobs.length ? `${jobs.length} generating. You can generate another while you wait.` : '',
      ]
        .filter(Boolean)
        .join(' ');
    }

    if (el.assetGenerationPending) {
      el.assetGenerationPending.replaceChildren();
      el.assetGenerationPending.hidden = !jobs.length;
      for (const job of jobs) {
        const card = document.createElement('div');
        card.className = 'scene-card asset-card-generating';
        card.dataset.assetSearch = `${job.name} ${job.prompt}`;
        card.setAttribute('aria-busy', 'true');
        const placeholder = document.createElement('div');
        placeholder.className = 'scene-card-thumb-container stamp-generation-placeholder';
        const label = document.createElement('div');
        label.className = 'scene-card-name';
        label.textContent = job.name;
        const progress = document.createElement('div');
        progress.className = 'stamp-card-progress';
        progress.textContent = 'Generating…';
        card.append(placeholder, label, progress);
        el.assetGenerationPending.appendChild(card);
      }
    }

    const cachedChars = getCachedCharacters();
    if (kind === 'character' && cachedChars !== null && el.charactersManagerEmpty) {
      el.charactersManagerEmpty.style.display = jobs.length || cachedChars.length ? 'none' : 'flex';
    }

    if (kind) {
      const { form } = getAssetFormAndPrompt(kind);
      form?.setAttribute('aria-busy', String(jobs.length > 0));
    }
  }

  listen(el.sceneGenerationPrompt, 'keydown', (event: Event) => {
    const keyEvent = event as KeyboardEvent;
    if (keyEvent.key !== 'Enter' || keyEvent.isComposing) return;
    keyEvent.preventDefault();
    if (!keyEvent.repeat) {
      el.sceneGenerationForm?.requestSubmit();
    }
  });

  listen(el.sceneGenerationForm, 'submit', async (event: Event) => {
    event.preventDefault();
    if (!isStampOwner() || !el.sceneGenerationPrompt) return;
    const prompt = el.sceneGenerationPrompt.value.trim();
    if (!prompt) return;
    const sceneType = el.sceneGenerationType?.value ?? 'none';
    const name = nameFromPrompt(prompt);
    const generationTheaterId = getTheaterId();
    if (!generationTheaterId) return;

    const jobId = ++nextSceneGenerationId;
    pendingSceneGenerations.set(jobId, { name, prompt, sceneType });
    el.sceneGenerationPrompt.value = '';
    renderScenesManagerGrid(getCachedScenes() || []);
    updateSceneGenerationStatus();

    let resultMessage = '';
    try {
      const payload: { name: string; prompt: string; scene_type: string; references?: string[] } = {
        name,
        prompt,
        scene_type: sceneType,
      };
      const refs = extractTaggedReferences(prompt);
      if (refs.length) {
        payload.references = refs;
      }
      const response = await fetch(
        `/api/theaters/${encodeURIComponent(generationTheaterId)}/scenes/generate`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        },
      );
      const data = (await response.json()) as GenerateApiResponse;
      if (!response.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Unable to generate scene.');
      }
      if (getTheaterId() !== generationTheaterId) return;
      // Concurrent requests can return snapshots in a different order.
      const mergedScenes = new Map<string, TheaterScene>();
      for (const scene of getCachedScenes() || []) {
        mergedScenes.set(scene.id ?? scene.name, scene);
      }
      for (const scene of data.scenes || []) {
        mergedScenes.set(scene.id ?? scene.name, scene);
      }
      writeCollectionCache(scenesCollectionState, Array.from(mergedScenes.values()));
      resultMessage = `“${name}” saved and ready to push to canvas. Charged ${data.credits_charged} credits.`;
    } catch (error) {
      if (getTheaterId() !== generationTheaterId) return;
      const errMsg = error instanceof Error ? error.message : '';
      resultMessage = `“${name}”: ${errMsg || 'Unable to generate scene. Please try again.'}`;
      if (!el.sceneGenerationPrompt.value.trim()) {
        el.sceneGenerationPrompt.value = prompt;
      }
    } finally {
      pendingSceneGenerations.delete(jobId);
      if (getTheaterId() === generationTheaterId) {
        if (getActiveTab() === 'scenes') {
          renderScenesManagerGrid(getCachedScenes() || []);
        }
        updateSceneGenerationStatus(resultMessage);
      }
    }
  });

  for (const [tab, kind] of Object.entries(ASSET_GENERATION_KINDS) as Array<
    ['characters' | 'animations' | 'playlists', AssetGenerationKind]
  >) {
    const { form, prompt: input } = getAssetFormAndPrompt(kind);
    listen(input, 'keydown', (event: Event) => {
      const keyEvent = event as KeyboardEvent;
      if (keyEvent.key !== 'Enter' || keyEvent.isComposing) return;
      keyEvent.preventDefault();
      if (!keyEvent.repeat) {
        form?.requestSubmit();
      }
    });
    listen(form, 'submit', async (event: Event) => {
      event.preventDefault();
      if (!input) return;
      const prompt = input.value.trim();
      const generationTheaterId = getTheaterId();
      if (!isStampOwner() || !prompt || !generationTheaterId) return;

      const name = nameFromPrompt(prompt);
      const payload: {
        name: string;
        prompt: string;
        animation_type?: string;
        references?: string[];
      } = { name, prompt };
      if (kind === 'animation' && el.animationGenerationType) {
        payload.animation_type = el.animationGenerationType.value;
      }
      if (kind === 'character' || kind === 'animation') {
        const taggedRefs = extractTaggedReferences(prompt);
        if (taggedRefs.length) {
          payload.references = taggedRefs;
        }
      }
      const id = ++nextAssetGenerationId;
      pendingAssetGenerations.set(id, { kind, name, prompt, theaterId: generationTheaterId });
      assetGenerationMessages.delete(kind);
      input.value = '';
      updateAssetGenerationUI();

      try {
        const endpoint = kind === 'music' ? 'music' : tab;
        const response = await fetch(
          `/api/theaters/${encodeURIComponent(generationTheaterId)}/${endpoint}/generate`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          },
        );
        const data = (await response.json()) as GenerateApiResponse;
        if (!response.ok) {
          throw new Error(
            typeof data.detail === 'string' ? data.detail : `Unable to generate ${kind}.`,
          );
        }
        if (getTheaterId() !== generationTheaterId) return;
        if (kind === 'character') {
          const merged = new Map<string, TheaterCharacter>();
          for (const asset of getCachedCharacters() || []) {
            merged.set(asset.id || asset.name, asset);
          }
          for (const asset of data.characters || []) {
            merged.set(asset.id || asset.name, asset);
          }
          const mergedList = Array.from(merged.values());
          writeCollectionCache(charactersCollectionState, mergedList);
          if (getActiveTab() === tab) {
            renderCharactersManagerGrid(mergedList);
          }
        } else if (tab !== 'characters' && getActiveTab() === tab) {
          await loadExtraAssets(tab);
        }
        assetGenerationMessages.set(
          kind,
          `“${name}” saved to this theater. Charged ${data.credits_charged} credits.`,
        );
      } catch (error) {
        if (getTheaterId() !== generationTheaterId) return;
        const errMsg = error instanceof Error ? error.message : String(error);
        assetGenerationMessages.set(kind, `“${name}”: ${errMsg}`);
        if (!input.value.trim()) {
          input.value = prompt;
        }
      } finally {
        pendingAssetGenerations.delete(id);
        if (getTheaterId() === generationTheaterId) {
          updateAssetGenerationUI();
        }
      }
    });
  }

  function updateStampTabsVisibility(): void {
    const orator = isOrator();
    if (el.stampManagerTabs) {
      el.stampManagerTabs.style.display = orator ? 'flex' : 'none';
    }
    if (!orator && getActiveTab() !== 'stamps') {
      switchStampManagerTab('stamps');
    }
  }

  function preloadItemImages(
    items: readonly { readonly url?: string | null; readonly image_url?: string | null }[] | null | undefined,
  ): void {
    if (!Array.isArray(items)) return;
    for (const item of items) {
      const url = item && (item.url || item.image_url);
      if (url && typeof url === 'string') {
        const effectiveUrl = resolveWebpQualityUrl(url);
        if (!preloadedImageUrls.has(effectiveUrl)) {
          preloadedImageUrls.add(effectiveUrl);
          const img = new Image();
          img.src = effectiveUrl;
        }
      }
    }
  }

  function revertAllOtherPushItems(currentElement: HTMLElement | null): void {
    document.querySelectorAll('.asset-push-button, .playlist-push-button').forEach((btn) => {
      if (btn !== currentElement) {
        btn.classList.remove('pushing', 'pushed');
        const action = btn.querySelector('small');
        if (action && (action.textContent === 'Pushed!' || action.textContent === 'Pushing…')) {
          action.textContent = 'Push';
        }
      }
    });
    document.querySelectorAll('.scene-card').forEach((card) => {
      if (card !== currentElement) {
        card.classList.remove('pushing');
        const badge = card.querySelector('.scene-card-push-badge');
        if (
          badge &&
          (badge.textContent === 'Pushed!' ||
            badge.textContent === 'Pushing...' ||
            badge.textContent === 'Pushing…')
        ) {
          badge.textContent = 'Push';
        }
      }
    });
    document.querySelectorAll('.character-card').forEach((card) => {
      if (card !== currentElement) {
        card.classList.remove('pushing');
        const badge = card.querySelector('.character-card-push-badge');
        if (
          badge &&
          (badge.textContent === 'Pushed!' ||
            badge.textContent === 'Pushing...' ||
            badge.textContent === 'Pushing…')
        ) {
          badge.textContent = 'Push';
        }
      }
    });
  }

  function updateAssetPlaybackButton(button: HTMLElement): void {
    if (!assetMusicState || button.dataset.assetKind !== 'playlists') return;
    const tracks = assetMusicState.tracks || [];
    const silent = !(assetMusicState.music_id || assetMusicState.playlist) || tracks.length === 0;
    const trackSrc = getCurrentTrackSrc();
    const playingTrack = trackSrc && tracks.includes(trackSrc) ? trackSrc : tracks[0];
    const selected =
      button.dataset.assetId === 'no_music'
        ? silent
        : !silent &&
          (button.dataset.playlistName
            ? button.dataset.playlistName ===
                (assetMusicState.music_id || assetMusicState.playlist) &&
              tracks.includes(button.dataset.trackUrl ?? '')
            : button.dataset.trackUrl === playingTrack);
    button.classList.toggle('currently-playing', selected);
    if (selected) {
      button.setAttribute('aria-current', 'true');
    } else {
      button.removeAttribute('aria-current');
    }
    const indicator = button.querySelector<HTMLElement>('.asset-playing-indicator');
    if (indicator) {
      indicator.hidden = !selected;
      indicator.textContent = silent
        ? '✓ Selected'
        : assetMusicState.paused
          ? 'Ⅱ Paused'
          : '▶ Playing';
    }
    if (button.classList.contains('playlist-push-button')) {
      const label =
        button.querySelector<HTMLElement>('.asset-label') ||
        button.querySelector<HTMLElement>('span:not(.asset-playing-indicator)');
      if (label) {
        label.hidden = selected;
        label.textContent = selected ? '' : 'Push';
      }
    }
    if (!selected) {
      const action = button.querySelector('small');
      if (action && (action.textContent === 'Pushed!' || action.textContent === 'Pushing…')) {
        action.textContent = 'Push';
      }
    }
  }

  function updateAssetPlaybackSelection(music: MusicSelectionState | null): void {
    assetMusicState = music;
    el.playlistsManager
      ?.querySelectorAll<HTMLElement>('.asset-push-button, .playlist-push-button')
      .forEach(updateAssetPlaybackButton);
  }

  function createAssetButton(asset: AssetButtonInput, kind: ExtraAssetKind): HTMLButtonElement {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = asset.tracks ? 'stamp-tab-btn playlist-push-button' : 'asset-push-button';
    button.dataset.assetSearch = JSON.stringify(asset);
    button.dataset.assetKind = kind;
    button.dataset.assetId = asset.id;
    button.dataset.playlistName = asset.tracks ? asset.name || '' : '';
    if (asset.tracks && asset.name) {
      button.dataset.playlist = asset.name;
    }
    button.dataset.trackUrl = asset.tracks
      ? asset.tracks[0]?.url || ''
      : asset.url || asset.id;
    button.title = asset.description || `Push ${asset.name || ''}`;

    let action: HTMLElement;
    if (kind === 'animations') {
      button.setAttribute('aria-label', `Push ${asset.name || 'animation'}`);
      const thumbContainer = document.createElement('div');
      thumbContainer.className = 'asset-thumb-container';

      const mediaUrl = asset.url;
      if (mediaUrl) {
        const isVideo =
          mediaUrl.endsWith('.mp4') || mediaUrl.endsWith('.webm') || asset.type === 'video';
        if (isVideo && (mediaUrl.endsWith('.mp4') || mediaUrl.endsWith('.webm'))) {
          const video = document.createElement('video');
          video.className = 'asset-thumb';
          video.src = `${mediaUrl}#t=0.001`;
          video.preload = 'metadata';
          video.muted = true;
          video.playsInline = true;
          video.onerror = () => {
            video.style.display = 'none';
            if (!thumbContainer.querySelector('.asset-fallback-thumb')) {
              const placeholder = document.createElement('div');
              placeholder.className = 'asset-fallback-thumb';
              placeholder.textContent = '🎬';
              thumbContainer.appendChild(placeholder);
            }
          };
          thumbContainer.appendChild(video);
        } else {
          const img = document.createElement('img');
          img.className = 'asset-thumb';
          img.src = mediaUrl;
          img.alt = asset.name || '';
          img.loading = 'lazy';
          img.onerror = () => {
            img.style.display = 'none';
            if (!thumbContainer.querySelector('.asset-fallback-thumb')) {
              const placeholder = document.createElement('div');
              placeholder.className = 'asset-fallback-thumb';
              placeholder.textContent = '🎬';
              thumbContainer.appendChild(placeholder);
            }
          };
          thumbContainer.appendChild(img);
        }
      } else {
        const placeholder = document.createElement('div');
        placeholder.className = 'asset-fallback-thumb';
        placeholder.textContent = '🎬';
        thumbContainer.appendChild(placeholder);
      }

      const overlay = document.createElement('div');
      overlay.className = 'asset-overlay';
      action = document.createElement('small');
      action.textContent = 'Push';
      overlay.appendChild(action);
      thumbContainer.appendChild(overlay);
      button.appendChild(thumbContainer);

      const label = document.createElement('span');
      label.className = 'asset-label';
      label.hidden = true;
      label.style.display = 'none';
      button.appendChild(label);
    } else {
      const label = document.createElement('span');
      label.className = 'asset-label';
      label.textContent = asset.tracks ? 'Push' : asset.description || asset.name || '';
      action = document.createElement('small');
      action.textContent = 'Push';
      action.hidden = Boolean(asset.tracks);
      const indicator = document.createElement('span');
      indicator.className = 'asset-playing-indicator';
      indicator.hidden = true;
      button.append(indicator, label, action);
      updateAssetPlaybackButton(button);
    }

    button.addEventListener('click', async () => {
      const tid = getTheaterId();
      if (!isOrator() || !tid) return;
      revertAllOtherPushItems(button);
      button.disabled = true;
      action.textContent = 'Pushing…';
      button.classList.add('pushing');
      try {
        const res = await fetch(`/api/theaters/${encodeURIComponent(tid)}/${kind}/push`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: asset.id }),
        });
        const data = (await res.json()) as PushApiResponse;
        if (!res.ok) {
          throw new Error(data.detail || 'Unable to push asset.');
        }
        if (data.message) {
          showShareToast(data.message);
        }
        if (asset.tracks) {
          updateAssetPlaybackSelection({
            music_id: asset.name,
            playlist: asset.name,
            tracks: asset.tracks.map((track) => track.url || track.id),
            paused: false,
          });
        } else if (asset.id === 'no_music') {
          updateAssetPlaybackSelection({
            music_id: '',
            playlist: '',
            tracks: [],
            paused: false,
          });
        } else if (kind === 'playlists') {
          const parentGroup = button.closest<HTMLElement>('.playlist-group');
          const plName = parentGroup ? parentGroup.dataset.playlistName || '' : '';
          const trackUrl = button.dataset.trackUrl || asset.url || asset.id;
          setCurrentTrackSrc(trackUrl);
          updateAssetPlaybackSelection({
            music_id: plName ? `${plName} / ${asset.name || ''}` : asset.name,
            playlist: plName,
            tracks: [trackUrl],
            paused: false,
          });
        }
        refreshCanvasState?.(['latest']);
        revertAllOtherPushItems(button);
        button.classList.add('pushed');
        action.textContent = 'Pushed!';
        window.setTimeout(() => {
          button.classList.remove('pushed');
          if (action.textContent === 'Pushed!') {
            action.textContent = 'Push';
          }
        }, 2000);
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Unable to push asset.';
        showShareToast(msg);
        action.textContent = 'Retry';
      } finally {
        button.disabled = false;
        button.classList.remove('pushing');
      }
    });

    return button;
  }

  if (el.playlistsManager && !el.playlistsManager.querySelector('#no-music-button')) {
    const noMusicButton = createAssetButton({ id: 'no_music', name: 'No Music' }, 'playlists');
    noMusicButton.id = 'no-music-button';
    el.playlistsManager.prepend(noMusicButton);
  }

  listen(el.stampTabPlaylistsBtn, 'click', () => switchStampManagerTab('playlists'));
  listen(el.stampTabAnimationsBtn, 'click', () => switchStampManagerTab('animations'));
  if (bindPrimaryTabButtons) {
    listen(el.stampTabStampsBtn, 'click', () => switchStampManagerTab('stamps'));
    listen(el.stampTabScenesBtn, 'click', () => switchStampManagerTab('scenes'));
    listen(el.stampTabCharactersBtn, 'click', () => switchStampManagerTab('characters'));
  }

  async function loadExtraAssets(kind: ExtraAssetKind): Promise<void> {
    const panel = kind === 'playlists' ? el.playlistsManagerContent : extraAssetPanels[kind];
    if (!panel) return;
    const version = ++assetLoadVersion;
    const tid = getTheaterId();
    panel.textContent = 'Loading…';
    if (el.stampManagerCount) {
      el.stampManagerCount.textContent = '…';
    }
    try {
      if (!tid) throw new Error('Unable to load assets.');
      const res = await fetch(`/api/theaters/${encodeURIComponent(tid)}/${kind}`);
      if (!res.ok) throw new Error('Unable to load assets.');
      if (kind === 'playlists') {
        const assets = (await res.json()) as TheaterPlaylist[];
        if (version !== assetLoadVersion || getActiveTab() !== kind || getTheaterId() !== tid) {
          return;
        }
        panel.replaceChildren();
        const count = assets.reduce((n, playlist) => n + playlist.tracks.length, 0);
        if (el.stampManagerCount) {
          el.stampManagerCount.textContent = `${count}`;
          el.stampManagerCount.title = `${count} tracks`;
        }
        if (!count) {
          panel.textContent = 'No playlists found for this theater.';
          return;
        }
        const listContainer = document.createElement('div');
        listContainer.className = 'playlist-accordion-list';
        assets.forEach((playlist, index) => {
          const initialExpanded = expandedPlaylists.has(playlist.name);
          const group = document.createElement('div');
          group.className = 'playlist-group';
          group.dataset.playlistName = playlist.name;
          if (!initialExpanded) {
            group.classList.add('minimized');
          }

          const header = document.createElement('div');
          header.className = 'playlist-header';

          const toggleBtn = document.createElement('button');
          toggleBtn.type = 'button';
          toggleBtn.className = 'playlist-header-toggle';
          toggleBtn.dataset.playlistName = playlist.name;
          toggleBtn.setAttribute('aria-expanded', initialExpanded ? 'true' : 'false');
          toggleBtn.setAttribute('aria-controls', `playlist-tracks-${index}`);
          toggleBtn.setAttribute('aria-label', `Toggle ${playlist.name} playlist`);

          const chevron = document.createElement('span');
          chevron.className = 'playlist-chevron';
          chevron.setAttribute('aria-hidden', 'true');
          chevron.textContent = initialExpanded ? '▼' : '▶';

          const title = document.createElement('span');
          title.className = 'playlist-title';
          title.textContent = playlist.name;

          const countBadge = document.createElement('span');
          countBadge.className = 'playlist-track-count';
          const trackCount = playlist.tracks ? playlist.tracks.length : 0;
          countBadge.textContent = `${trackCount} ${trackCount === 1 ? 'track' : 'tracks'}`;

          toggleBtn.append(chevron, title, countBadge);

          const pushBtn = createAssetButton(playlist, kind);
          pushBtn.classList.add('playlist-header-push');
          pushBtn.setAttribute('aria-label', `Push ${playlist.name}`);

          header.append(toggleBtn, pushBtn);

          const trackList = document.createElement('div');
          trackList.className = 'asset-track-list playlist-track-list';
          trackList.id = `playlist-tracks-${index}`;
          trackList.hidden = !initialExpanded;

          playlist.tracks.forEach((track) => {
            trackList.appendChild(createAssetButton(track, kind));
          });

          const setExpanded = (expanded: boolean): void => {
            toggleBtn.setAttribute('aria-expanded', `${expanded}`);
            chevron.textContent = expanded ? '▼' : '▶';
            trackList.hidden = !expanded;
            group.classList.toggle('minimized', !expanded);
            if (expanded) {
              expandedPlaylists.add(playlist.name);
            } else {
              expandedPlaylists.delete(playlist.name);
            }
          };

          toggleBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const isExpanded = toggleBtn.getAttribute('aria-expanded') === 'true';
            setExpanded(!isExpanded);
          });

          header.addEventListener('click', (e) => {
            const target = e.target as HTMLElement | null;
            if (target?.closest('.playlist-header-push')) return;
            const isExpanded = toggleBtn.getAttribute('aria-expanded') === 'true';
            setExpanded(!isExpanded);
          });

          pushBtn.addEventListener('click', () => {
            if (toggleBtn.getAttribute('aria-expanded') !== 'true') {
              setExpanded(true);
            }
          });

          group.append(header, trackList);
          listContainer.appendChild(group);
        });
        panel.appendChild(listContainer);
      } else {
        const assets = (await res.json()) as TheaterAnimation[];
        if (version !== assetLoadVersion || getActiveTab() !== kind || getTheaterId() !== tid) {
          return;
        }
        panel.replaceChildren();
        const count = assets.length;
        if (el.stampManagerCount) {
          el.stampManagerCount.textContent = `${count}`;
          el.stampManagerCount.title = `${count} animations`;
        }
        if (!count) {
          panel.textContent = 'No animations found for this theater.';
          return;
        }
        panel.className = 'animations-grid';
        for (const asset of assets) {
          panel.appendChild(createAssetButton(asset, kind));
        }
      }
    } catch (err) {
      if (version === assetLoadVersion && getActiveTab() === kind && getTheaterId() === tid) {
        panel.textContent = err instanceof Error ? err.message : 'Unable to load assets.';
        if (el.stampManagerCount) {
          el.stampManagerCount.textContent = '—';
        }
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'panel-btn';
        retry.textContent = 'Retry';
        retry.addEventListener('click', () => {
          void loadExtraAssets(kind);
        });
        panel.appendChild(retry);
      }
    }
  }

  function switchStampManagerTab(requestedTab: StampManagerTab): void {
    const orator = isOrator();
    const tab: StampManagerTab = !orator && requestedTab !== 'stamps' ? 'stamps' : requestedTab;
    setActiveTab(tab);
    updateAssetGenerationUI();
    queueMicrotask(applyAssetSearch);
    ++assetLoadVersion;
    if (el.stampManagerBody) {
      el.stampManagerBody.scrollTop = 0;
    }

    if (el.playlistsManager) el.playlistsManager.hidden = tab !== 'playlists';
    if (el.animationsManager) el.animationsManager.hidden = tab !== 'animations';
    el.stampTabPlaylistsBtn?.classList.toggle('active', tab === 'playlists');
    el.stampTabAnimationsBtn?.classList.toggle('active', tab === 'animations');
    el.stampTabStampsBtn?.classList.toggle('active', tab === 'stamps');
    el.stampTabScenesBtn?.classList.toggle('active', tab === 'scenes');
    el.stampTabCharactersBtn?.classList.toggle('active', tab === 'characters');

    const canGenerateScene = isStampOwner() && tab === 'scenes';
    if (el.sceneGenerationForm) el.sceneGenerationForm.hidden = !canGenerateScene;
    if (el.sceneGenerationStatus) el.sceneGenerationStatus.hidden = !canGenerateScene;

    if (tab === 'playlists' || tab === 'animations') {
      for (const node of [
        el.stampManagerPermissionBanner,
        el.stampManagerGrid,
        el.stampManagerEmpty,
        el.scenesManagerGrid,
        el.scenesManagerEmpty,
        el.charactersManagerGrid,
        el.charactersManagerEmpty,
      ]) {
        if (node) node.style.display = 'none';
      }
      if (el.stampGenerationForm) el.stampGenerationForm.hidden = true;
      if (el.stampGenerationStatus) el.stampGenerationStatus.hidden = true;
      void loadExtraAssets(tab);
    } else if (tab === 'stamps') {
      if (el.stampManagerPermissionBanner) el.stampManagerPermissionBanner.style.display = '';
      if (el.stampManagerGrid) el.stampManagerGrid.style.display = '';
      if (el.scenesManagerGrid) el.scenesManagerGrid.style.display = 'none';
      if (el.scenesManagerEmpty) el.scenesManagerEmpty.style.display = 'none';
      if (el.charactersManagerGrid) el.charactersManagerGrid.style.display = 'none';
      if (el.charactersManagerEmpty) el.charactersManagerEmpty.style.display = 'none';
      onShowStampsTab();
    } else if (tab === 'scenes') {
      if (el.stampManagerPermissionBanner) el.stampManagerPermissionBanner.style.display = 'none';
      if (el.stampManagerGrid) el.stampManagerGrid.style.display = 'none';
      if (el.stampManagerEmpty) el.stampManagerEmpty.style.display = 'none';
      if (el.charactersManagerGrid) el.charactersManagerGrid.style.display = 'none';
      if (el.charactersManagerEmpty) el.charactersManagerEmpty.style.display = 'none';
      if (el.scenesManagerGrid) el.scenesManagerGrid.style.display = 'grid';
      if (el.stampGenerationForm) el.stampGenerationForm.hidden = true;
      if (el.stampGenerationStatus) el.stampGenerationStatus.hidden = true;

      const cachedScenes = getCachedScenes();
      if (el.stampManagerCount) {
        const count = cachedScenes ? cachedScenes.length : 0;
        el.stampManagerCount.textContent = `${count}`;
        el.stampManagerCount.title = `${count} adventure scenes`;
      }
      if (cachedScenes) {
        renderScenesManagerGrid(cachedScenes);
        preloadItemImages(cachedScenes);
      } else {
        void loadTheaterScenes();
      }
    } else if (tab === 'characters') {
      if (el.stampManagerPermissionBanner) el.stampManagerPermissionBanner.style.display = 'none';
      if (el.stampManagerGrid) el.stampManagerGrid.style.display = 'none';
      if (el.stampManagerEmpty) el.stampManagerEmpty.style.display = 'none';
      if (el.scenesManagerGrid) el.scenesManagerGrid.style.display = 'none';
      if (el.scenesManagerEmpty) el.scenesManagerEmpty.style.display = 'none';
      if (el.charactersManagerGrid) el.charactersManagerGrid.style.display = 'grid';
      if (el.stampGenerationForm) el.stampGenerationForm.hidden = true;
      if (el.stampGenerationStatus) el.stampGenerationStatus.hidden = true;

      const cachedChars = getCachedCharacters();
      if (el.stampManagerCount) {
        const count = cachedChars ? cachedChars.length : 0;
        el.stampManagerCount.textContent = `${count}`;
        el.stampManagerCount.title = `${count} adventure characters`;
      }
      if (cachedChars) {
        renderCharactersManagerGrid(cachedChars);
        preloadItemImages(cachedChars);
      } else {
        void loadTheaterCharacters();
      }
    }
  }

  async function loadTheaterCollection<
    T extends { readonly url?: string | null; readonly image_url?: string | null },
  >(state: TheaterCollectionState<T>): Promise<void> {
    const tid = getTheaterId();
    if (!tid) return;
    if (state.inFlightTheaterId !== null && state.inFlightTheaterId === tid) return;
    const requestId = ++state.activeRequestId;
    state.inFlightTheaterId = tid;
    const startEpoch = state.epoch;
    const startCachedRef = state.getCached();
    try {
      const res = await fetch(`/api/theaters/${encodeURIComponent(tid)}/${state.endpoint}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: unknown = await res.json();
      if (requestId !== state.activeRequestId || getTheaterId() !== tid) return;
      if (state.epoch !== startEpoch || state.getCached() !== startCachedRef) return;
      const items = Array.isArray(data) ? (data as T[]) : [];
      writeCollectionCache(state, items);
      preloadItemImages(items);
      if (getActiveTab() === state.tab) {
        state.renderGrid(items);
      }
    } catch (err) {
      if (requestId !== state.activeRequestId || getTheaterId() !== tid) return;
      if (state.epoch !== startEpoch || state.getCached() !== startCachedRef) return;
      console.warn(`Unable to load theater ${state.endpoint}:`, err);
      if (getActiveTab() === state.tab) {
        if (state.gridEl) state.gridEl.innerHTML = '';
        if (state.emptyEl) {
          state.emptyEl.style.display = 'flex';
          if (state.emptyMessageEl) {
            state.emptyMessageEl.textContent = state.errorText;
          }
        }
      }
    } finally {
      if (requestId === state.activeRequestId) {
        state.inFlightTheaterId = null;
      }
    }
  }

  async function loadTheaterScenes(): Promise<void> {
    return loadTheaterCollection(scenesCollectionState);
  }

  function renderScenesManagerGrid(scenesInput: readonly TheaterScene[] | null | undefined): void {
    if (!el.scenesManagerGrid) return;
    const scenes = scenesInput || [];
    el.scenesManagerGrid.innerHTML = '';

    pendingSceneGenerations.forEach((job, jobId) => {
      const card = document.createElement('div');
      card.className = 'scene-card scene-card-generating';
      card.dataset.assetSearch = `${job.name} ${job.prompt}`;
      card.dataset.generationId = String(jobId);
      card.setAttribute('aria-busy', 'true');
      card.setAttribute('aria-label', `${job.name}: Generating`);
      card.title = `Generating “${job.name}” — ${job.prompt}`;
      const thumb = document.createElement('div');
      thumb.className = 'scene-card-thumb-container stamp-generation-placeholder';
      thumb.setAttribute('aria-hidden', 'true');
      const name = document.createElement('div');
      name.className = 'scene-card-name';
      name.textContent = job.name;
      const progress = document.createElement('div');
      progress.className = 'stamp-card-progress';
      progress.textContent = 'Generating…';
      card.append(thumb, name, progress);
      el.scenesManagerGrid?.appendChild(card);
    });

    if (el.stampManagerCount && getActiveTab() === 'scenes') {
      const suffix = pendingSceneGenerations.size ? ` · ${pendingSceneGenerations.size} generating` : '';
      el.stampManagerCount.textContent = `${scenes.length}${suffix}`;
      el.stampManagerCount.title = `${scenes.length} adventure scenes${suffix}`;
    }

    if (scenes.length === 0 && pendingSceneGenerations.size === 0) {
      if (el.scenesManagerEmpty) {
        el.scenesManagerEmpty.style.display = 'flex';
        if (el.scenesEmptyMessage) {
          el.scenesEmptyMessage.textContent = 'No scenes found for this adventure.';
        }
      }
      return;
    }
    if (el.scenesManagerEmpty) el.scenesManagerEmpty.style.display = 'none';

    for (const scene of scenes) {
      const card = document.createElement('div');
      card.className = 'scene-card';
      card.dataset.assetSearch = JSON.stringify(scene);
      card.setAttribute('role', 'button');
      card.setAttribute('tabindex', '0');
      card.setAttribute('title', `Click to push "${scene.name}"`);
      card.dataset.sceneName = scene.name;
      if (scene.id) card.dataset.sceneId = scene.id;

      const thumbContainer = document.createElement('div');
      thumbContainer.className = 'scene-card-thumb-container';

      const imgUrl = scene.url || scene.image_url;
      if (imgUrl) {
        const img = document.createElement('img');
        img.className = 'scene-card-thumb';
        img.src = imgUrl;
        img.alt = scene.name;
        img.loading = 'lazy';
        img.onerror = () => {
          img.style.display = 'none';
          if (!thumbContainer.querySelector('.scene-card-fallback-thumb')) {
            const placeholder = document.createElement('div');
            placeholder.className = 'scene-card-fallback-thumb';
            placeholder.style.cssText =
              'width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:2rem;color:#64748b;';
            placeholder.textContent = '🎬';
            thumbContainer.appendChild(placeholder);
          }
        };
        thumbContainer.appendChild(img);
      } else {
        const placeholder = document.createElement('div');
        placeholder.className = 'scene-card-fallback-thumb';
        placeholder.style.cssText =
          'width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:2rem;color:#64748b;';
        placeholder.textContent = '🎬';
        thumbContainer.appendChild(placeholder);
      }

      const overlay = document.createElement('div');
      overlay.className = 'scene-card-overlay';
      const badge = document.createElement('span');
      badge.className = 'scene-card-push-badge';
      badge.textContent = 'Push';
      overlay.appendChild(badge);
      thumbContainer.appendChild(overlay);
      card.appendChild(thumbContainer);

      const nameEl = document.createElement('div');
      nameEl.className = 'scene-card-name';
      nameEl.textContent = scene.name;
      card.appendChild(nameEl);

      if (scene.description) {
        const descEl = document.createElement('div');
        descEl.className = 'scene-card-desc';
        descEl.textContent = scene.description;
        card.appendChild(descEl);
      }

      card.addEventListener('click', () => {
        void pushSceneToCanvas(scene, card);
      });
      card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          void pushSceneToCanvas(scene, card);
        }
      });

      el.scenesManagerGrid.appendChild(card);
    }
  }

  async function pushSceneToCanvas(scene: TheaterScene, cardEl: HTMLElement | null): Promise<void> {
    const tid = getTheaterId();
    if (!tid || isPushingScene) return;
    if (!isOrator()) {
      showShareToast('Only the active orator can push scenes.');
      return;
    }

    revertAllOtherPushItems(cardEl);
    isPushingScene = true;
    if (cardEl) {
      cardEl.classList.add('pushing');
      const badge = cardEl.querySelector('.scene-card-push-badge');
      if (badge) badge.textContent = 'Pushing...';
    }

    try {
      const res = await fetch(`/api/theaters/${encodeURIComponent(tid)}/scenes/push`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: scene.name, scene_id: scene.id || '' }),
      });
      const data = (await res.json()) as PushApiResponse;
      if (!res.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Failed to push scene.');
      }
      revertAllOtherPushItems(cardEl);
      if (cardEl) {
        const badge = cardEl.querySelector('.scene-card-push-badge');
        if (badge) badge.textContent = 'Pushed!';
        window.setTimeout(() => {
          if (badge && badge.textContent === 'Pushed!') badge.textContent = 'Push';
        }, 2000);
      }
      showShareToast(`Pushed "${scene.name}".`);
      if (fetchLatestImage) {
        fetchLatestImage();
      } else {
        refreshCanvasState?.(['latest']);
      }
    } catch (err) {
      console.error('Error pushing scene to canvas:', err);
      const msg = err instanceof Error ? err.message : '';
      showShareToast(msg || 'Error pushing scene.');
      if (cardEl) {
        const badge = cardEl.querySelector('.scene-card-push-badge');
        if (badge) badge.textContent = 'Failed';
        window.setTimeout(() => {
          if (badge) badge.textContent = 'Push';
        }, 2000);
      }
    } finally {
      isPushingScene = false;
      if (cardEl) {
        cardEl.classList.remove('pushing');
      }
    }
  }

  async function loadTheaterCharacters(): Promise<void> {
    return loadTheaterCollection(charactersCollectionState);
  }

  function renderCharactersManagerGrid(
    charactersInput: readonly TheaterCharacter[] | null | undefined,
  ): void {
    if (!el.charactersManagerGrid) return;
    const characters = charactersInput || [];
    el.charactersManagerGrid.innerHTML = '';
    if (el.stampManagerCount && getActiveTab() === 'characters') {
      el.stampManagerCount.textContent = `${characters.length}`;
      el.stampManagerCount.title = `${characters.length} adventure characters`;
    }
    if (characters.length === 0) {
      if (el.charactersManagerEmpty) {
        el.charactersManagerEmpty.style.display = 'flex';
        if (el.charactersEmptyMessage) {
          el.charactersEmptyMessage.textContent = 'No characters found for this adventure.';
        }
      }
      return;
    }
    if (el.charactersManagerEmpty) el.charactersManagerEmpty.style.display = 'none';

    for (const char of characters) {
      const card = document.createElement('div');
      card.className = 'character-card';
      card.dataset.assetSearch = JSON.stringify(char);
      card.setAttribute('role', 'button');
      card.setAttribute('tabindex', '0');
      card.setAttribute('title', `Click to push "${char.name}"`);
      card.dataset.characterName = char.name;
      if (char.id) card.dataset.characterId = char.id;

      const thumbContainer = document.createElement('div');
      thumbContainer.className = 'character-card-thumb-container';

      const imgUrl = char.url || char.image_url;
      if (imgUrl) {
        const img = document.createElement('img');
        img.className = 'character-card-thumb';
        img.src = imgUrl;
        img.alt = char.name;
        img.loading = 'lazy';
        img.onerror = () => {
          img.style.display = 'none';
          if (!thumbContainer.querySelector('.character-card-fallback-thumb')) {
            const placeholder = document.createElement('div');
            placeholder.className = 'character-card-fallback-thumb';
            placeholder.style.cssText =
              'width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:2rem;color:#64748b;';
            placeholder.textContent = '👤';
            thumbContainer.appendChild(placeholder);
          }
        };
        thumbContainer.appendChild(img);
      } else {
        const placeholder = document.createElement('div');
        placeholder.className = 'character-card-fallback-thumb';
        placeholder.style.cssText =
          'width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:2rem;color:#64748b;';
        placeholder.textContent = '👤';
        thumbContainer.appendChild(placeholder);
      }

      const overlay = document.createElement('div');
      overlay.className = 'character-card-overlay';
      const badge = document.createElement('span');
      badge.className = 'character-card-push-badge';
      badge.textContent = 'Push';
      overlay.appendChild(badge);
      thumbContainer.appendChild(overlay);
      card.appendChild(thumbContainer);

      const nameEl = document.createElement('div');
      nameEl.className = 'character-card-name';
      nameEl.textContent = char.name;
      card.appendChild(nameEl);

      const textToShow = char.description || char.personality || char.quirk || '';
      if (textToShow) {
        const descEl = document.createElement('div');
        descEl.className = 'character-card-desc';
        descEl.textContent = textToShow;
        card.appendChild(descEl);
      }

      card.addEventListener('click', () => {
        void pushCharacterToCanvas(char, card);
      });
      card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          void pushCharacterToCanvas(char, card);
        }
      });

      el.charactersManagerGrid.appendChild(card);
    }
  }

  async function pushCharacterToCanvas(
    character: TheaterCharacter,
    cardEl: HTMLElement | null,
  ): Promise<void> {
    const tid = getTheaterId();
    if (!tid || isPushingCharacter) return;
    if (!isOrator()) {
      showShareToast('Only the active orator can push characters.');
      return;
    }

    revertAllOtherPushItems(cardEl);
    isPushingCharacter = true;
    if (cardEl) {
      cardEl.classList.add('pushing');
      const badge = cardEl.querySelector('.character-card-push-badge');
      if (badge) badge.textContent = 'Pushing...';
    }

    try {
      const res = await fetch(`/api/theaters/${encodeURIComponent(tid)}/characters/push`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: character.name, character_id: character.id || '' }),
      });
      const data = (await res.json()) as PushApiResponse;
      if (!res.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Failed to push character.');
      }
      revertAllOtherPushItems(cardEl);
      if (cardEl) {
        const badge = cardEl.querySelector('.character-card-push-badge');
        if (badge) badge.textContent = 'Pushed!';
        window.setTimeout(() => {
          if (badge && badge.textContent === 'Pushed!') badge.textContent = 'Push';
        }, 2000);
      }
      showShareToast(`Pushed "${character.name}".`);
      if (fetchLatestImage) {
        fetchLatestImage();
      } else {
        refreshCanvasState?.(['latest']);
      }
    } catch (err) {
      console.error('Error pushing character to canvas:', err);
      const msg = err instanceof Error ? err.message : '';
      showShareToast(msg || 'Error pushing character.');
      if (cardEl) {
        const badge = cardEl.querySelector('.character-card-push-badge');
        if (badge) badge.textContent = 'Failed';
        window.setTimeout(() => {
          if (badge) badge.textContent = 'Push';
        }, 2000);
      }
    } finally {
      isPushingCharacter = false;
      if (cardEl) {
        cardEl.classList.remove('pushing');
      }
    }
  }

  function handleDoodleSocketData(data: unknown): boolean {
    if (!hasTypeField(data)) return false;
    if (data.type === 'theater_characters_updated') {
      const payload = data as { readonly characters?: unknown };
      const characters = Array.isArray(payload.characters)
        ? (payload.characters as TheaterCharacter[])
        : [];
      writeCollectionCache(charactersCollectionState, characters);
      preloadItemImages(characters);
      if (isManagerOpen() && getActiveTab() === 'characters') {
        renderCharactersManagerGrid(characters);
      }
      return true;
    }
    if (data.type === 'theater_playlists_updated' || data.type === 'theater_animations_updated') {
      const tab: ExtraAssetKind =
        data.type === 'theater_playlists_updated' ? 'playlists' : 'animations';
      if (isManagerOpen() && getActiveTab() === tab) {
        void loadExtraAssets(tab);
      }
      return true;
    }
    if (data.type === 'theater_scenes_updated') {
      const payload = data as { readonly scenes?: unknown };
      const scenes = Array.isArray(payload.scenes) ? (payload.scenes as TheaterScene[]) : [];
      writeCollectionCache(scenesCollectionState, scenes);
      preloadItemImages(scenes);
      if (isManagerOpen() && getActiveTab() === 'scenes') {
        renderScenesManagerGrid(scenes);
      }
      return true;
    }
    if (data.type === 'theater_stamps_updated') {
      const payload = data as { readonly stamps?: unknown };
      const stamps = Array.isArray(payload.stamps) ? (payload.stamps as TheaterStampSummary[]) : [];
      onTheaterStampsUpdated(stamps);
      return true;
    }
    return false;
  }

  function dispose(): void {
    while (cleanupCallbacks.length > 0) {
      const fn = cleanupCallbacks.pop();
      fn?.();
    }
  }

  return {
    updateStampTabsVisibility,
    switchStampManagerTab,
    updateAssetGenerationUI,
    preloadItemImages,
    getPreloadedImageUrls: () => preloadedImageUrls,
    handleDoodleSocketData,
    updateAssetPlaybackSelection,
    loadExtraAssets,
    loadTheaterScenes,
    renderScenesManagerGrid,
    pushSceneToCanvas,
    loadTheaterCharacters,
    renderCharactersManagerGrid,
    pushCharacterToCanvas,
    revertAllOtherPushItems,
    dispose,
  };
}
