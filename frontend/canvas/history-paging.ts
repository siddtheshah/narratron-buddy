import { getAnimationSignature, type AnimationRenderer, type CanvasAnimation } from './media.js';

export const HISTORY_WINDOW_RADIUS = 5;
export const MINIMAL_MODE_STORAGE_KEY = 'narratron.canvas.performance.minimalMode';
export const LEGACY_IMAGE_EFFECTS_STORAGE_KEY = 'narratron.canvas.performance.disableImageEffects';

export interface HistoryCanvasPage {
  readonly width: number;
  readonly height: number;
}

export type HistoryAnimation = CanvasAnimation & { readonly scene_prompt?: string };

export interface HistoryItem {
  readonly url?: string | null;
  readonly prompt?: string | null;
  readonly time?: number | null;
  readonly transition?: string | null;
  readonly effect?: string | null;
  readonly path?: string | null;
  readonly page_id?: string | null;
  readonly doodles_enabled?: boolean | null;
  readonly canvas_page?: HistoryCanvasPage | null;
  readonly animation?: HistoryAnimation | null;
}

export interface LatestHistoryPayload {
  readonly latest?: string | null;
  readonly prompt?: string | null;
  readonly time?: number | null;
  readonly transition?: string | null;
  readonly effect?: string | null;
  readonly animation?: HistoryAnimation | null;
  readonly history?: readonly HistoryItem[] | null;
  readonly orator_cursor?: number | null;
}

export type HistoryResource =
  | {
      type: 'image';
      img: HTMLImageElement | null;
      frames?: undefined;
      video?: undefined;
      animation?: undefined;
    }
  | {
      type: 'animation';
      animation: CanvasAnimation;
      img?: undefined;
      frames?: HTMLImageElement[];
      video?: HTMLVideoElement | null;
    };

export interface HistoryResourceManager {
  readonly loadedHistoryResources: Map<number, HistoryResource>;
  pruneHistoryResources(centerIndex: number): void;
  unloadHistoryResource(resource: HistoryResource | null | undefined): void;
  readonly HISTORY_WINDOW_RADIUS: number;
}

export interface HistoryImageRenderer {
  applyTransition(url: string, transition?: string, effect?: string): Promise<void> | void;
  playLayeredAnimation: AnimationRenderer['playLayeredAnimation'];
  playVideoAnimation: AnimationRenderer['playVideoAnimation'];
  playSequence: AnimationRenderer['playSequence'];
  stopSequence?: () => void;
  setImageEffectsEnabled?: (enabled: boolean) => void;
}

export interface CreateHistoryPagingOptions {
  readonly theaterId: string | null;
  readonly isOrator: () => boolean;
  readonly imageRenderer: HistoryImageRenderer;
  readonly playCanvasAnimation: (
    renderer: AnimationRenderer,
    animation: CanvasAnimation,
    resolveUrl?: (url: string) => string,
  ) => void;
  readonly promptButton: HTMLElement | null;
  readonly promptTextSpan: HTMLElement | null;
  readonly currentImage: HTMLImageElement | null;
  readonly ghostImage: HTMLImageElement | null;
  readonly backgroundLayer: HTMLElement | null;
  readonly pagingControls: HTMLElement | null;
  readonly prevButton: HTMLButtonElement | null;
  readonly nextButton: HTMLButtonElement | null;
  readonly headButton: HTMLElement | null;
  readonly syncButton: HTMLElement | null;
  readonly pageIndicator: HTMLElement | null;
  readonly newHeadBadge: HTMLElement | null;
  readonly imageContainer: HTMLElement | null;
  readonly minimalModeInput?: HTMLInputElement | null;
  readonly hasDoodleCanvas?: () => boolean;
  readonly isSelectionActive?: () => boolean;
  readonly onRefreshBookmarks?: () => void;
  readonly onRefreshToolInteraction?: () => void;
  readonly onDisplayPageDoodles?: (item: HistoryItem) => void;
}

export interface HistoryPagingController {
  readonly resourceManager: HistoryResourceManager;
  resolveWebpQualityUrl(rawUrl: string): string;
  isMinimalMode(): boolean;
  setMinimalMode(enabled: boolean): void;
  getHistory(): readonly HistoryItem[];
  getCurrentViewIndex(): number;
  getCurrentItem(): HistoryItem | null;
  getOratorCursorIndex(): number;
  setOratorCursorIndex(index: number): void;
  isNavigating(): boolean;
  isPagedBack(): boolean;
  stepOratorPreviousImage(): void;
  triggerOratorPreviousImage(): Promise<void>;
  stepOratorNextImage(): void;
  triggerOratorNextImage(): Promise<void>;
  syncToOratorCursor(): void;
  displayHistoryImage(index: number, transition?: string): void;
  updatePagingControlsUI(): void;
  applyLatestState(data: LatestHistoryPayload): void;
  destroy(): void;
}

/**
 * Append `quality=low` to `.webp` asset URLs when Minimal Mode is active unless
 * an explicit `quality` query parameter is already present.
 */
export function resolveWebpQualityUrlForMode(rawUrl: string, minimalMode: boolean): string {
  if (!rawUrl || typeof rawUrl !== 'string') return rawUrl;
  if (!minimalMode) return rawUrl;
  const pathPart = rawUrl.split('?')[0].split('#')[0].toLowerCase();
  if (!pathPart.endsWith('.webp')) return rawUrl;
  try {
    const urlObj = new URL(rawUrl, window.location.origin);
    if (!urlObj.searchParams.has('quality')) {
      urlObj.searchParams.set('quality', 'low');
    }
    if (rawUrl.startsWith('/') || !rawUrl.includes('://')) {
      return urlObj.pathname + urlObj.search + urlObj.hash;
    }
    return urlObj.toString();
  } catch {
    const sep = rawUrl.includes('?') ? '&' : '?';
    return `${rawUrl}${sep}quality=low`;
  }
}

function readInitialMinimalMode(): boolean {
  try {
    // Preserve the old performance preference when upgrading to
    // Minimal Mode, which includes disabling image effects.
    return (
      localStorage.getItem(MINIMAL_MODE_STORAGE_KEY) === 'true' ||
      localStorage.getItem(LEGACY_IMAGE_EFFECTS_STORAGE_KEY) === 'true'
    );
  } catch {
    return false;
  }
}

/**
 * Manage canvas image history navigation, orator cursor stepping, sliding-window
 * media preloading/unloading, Minimal Mode quality/animation fallbacks, and
 * prompt badge expansion.
 */
export function createHistoryPagingController(
  options: CreateHistoryPagingOptions,
): HistoryPagingController {
  const {
    theaterId,
    isOrator,
    imageRenderer,
    playCanvasAnimation,
    promptButton,
    promptTextSpan,
    currentImage,
    ghostImage,
    backgroundLayer,
    pagingControls,
    prevButton,
    nextButton,
    headButton,
    syncButton,
    pageIndicator,
    newHeadBadge,
    imageContainer,
    minimalModeInput = null,
    hasDoodleCanvas = () => Boolean(document.getElementById('doodle-canvas')),
    isSelectionActive = () => Boolean((window as Window & { isCanvasSelectionActive?: boolean }).isCanvasSelectionActive),
    onRefreshBookmarks,
    onRefreshToolInteraction,
    onDisplayPageDoodles,
  } = options;

  let minimalMode = readInitialMinimalMode();
  document.documentElement.classList.toggle('minimal-mode', minimalMode);

  let promptPinned = false;
  let lastTime = -1;
  let lastImageRawUrl: string | null = null;
  let lastImageEffect: string | null = null;
  let lastAnimationSignature = '';
  let isFirstCanvasLoad = true;

  let imageHistory: HistoryItem[] = [];
  let currentViewIndex = -1;
  let oratorCursorIndex = -1;
  let navigating = false;
  let pagedBack = false;
  let hasNewHeadWhilePagedBack = false;

  const loadedHistoryResources = new Map<number, HistoryResource>();
  let pagingHoverTimeout: ReturnType<typeof setTimeout> | null = null;

  function resolveWebpQualityUrl(rawUrl: string): string {
    return resolveWebpQualityUrlForMode(rawUrl, minimalMode);
  }

  function unloadHistoryResource(resource: HistoryResource | null | undefined): void {
    if (!resource) return;
    if (resource.img) {
      try {
        resource.img.onload = null;
        resource.img.onerror = null;
        resource.img.src = '';
      } catch {
        // Ignore DOM teardown errors.
      }
      resource.img = null;
    }
    if (Array.isArray(resource.frames)) {
      resource.frames.forEach((frame) => {
        if (frame) {
          try {
            frame.onload = null;
            frame.onerror = null;
            frame.src = '';
          } catch {
            // Ignore DOM teardown errors.
          }
        }
      });
      resource.frames = [];
    }
    if (resource.video) {
      try {
        resource.video.pause();
        resource.video.removeAttribute('src');
        resource.video.load();
      } catch {
        // Ignore DOM teardown errors.
      }
      resource.video = null;
    }
  }

  function pruneHistoryResources(centerIndex: number): void {
    if (typeof centerIndex !== 'number' || centerIndex < 0) return;
    const minIndex = centerIndex - HISTORY_WINDOW_RADIUS;
    const maxIndex = centerIndex + HISTORY_WINDOW_RADIUS;
    for (const [index, resource] of loadedHistoryResources.entries()) {
      if (index < minIndex || index > maxIndex) {
        unloadHistoryResource(resource);
        loadedHistoryResources.delete(index);
      }
    }
  }

  function trackHistoryResource(index: number, resource: HistoryResource): void {
    if (index < 0) return;
    if (loadedHistoryResources.has(index)) {
      const prev = loadedHistoryResources.get(index);
      if (prev !== resource) {
        unloadHistoryResource(prev);
      }
    }
    loadedHistoryResources.set(index, resource);
    pruneHistoryResources(currentViewIndex >= 0 ? currentViewIndex : index);
  }

  function preloadHistoryNeighbor(index: number): void {
    if (index < 0 || index >= imageHistory.length || loadedHistoryResources.has(index)) return;
    const item = imageHistory[index];
    if (!item || !item.url || item.animation) return;
    const img = new Image();
    img.src = resolveWebpQualityUrl(item.url);
    trackHistoryResource(index, { type: 'image', img });
  }

  function playAnimation(animation: CanvasAnimation): void {
    if (minimalMode) {
      // Animated media defeats the point of Minimal Mode. Present a
      // still source where one exists, without starting a decoder or
      // a requestAnimationFrame loop.
      const stillUrl =
        animation.type === 'triframe'
          ? animation.frames?.[0]
          : animation.type === 'layered'
            ? animation.layers?.[0]?.url
            : animation.poster_url;
      if (stillUrl) {
        void imageRenderer.applyTransition(resolveWebpQualityUrl(stillUrl), 'none', 'none');
      }
      return;
    }
    playCanvasAnimation(imageRenderer, animation, (url: string) => {
      const effective = resolveWebpQualityUrl(url);
      return animation.type === 'triframe'
        ? `${effective}${effective.includes('?') ? '&' : '?'}t=${Date.now()}`
        : effective;
    });
  }

  function updatePagingControlsUI(): void {
    onRefreshBookmarks?.();
    if (!pagingControls) return;

    const orator = isOrator();
    if (orator) {
      navigating = false;
      pagedBack = false;
      hasNewHeadWhilePagedBack = false;
    }

    const total = Math.max(1, imageHistory.length || 1);
    const currentNum = currentViewIndex >= 0 ? currentViewIndex + 1 : total;

    if (pageIndicator) {
      pageIndicator.textContent = `${currentNum} / ${total}`;
    }

    if (prevButton) {
      prevButton.disabled = currentViewIndex <= 0 || total <= 1;
      prevButton.style.opacity = prevButton.disabled ? '0.35' : '1';
      prevButton.style.cursor = prevButton.disabled ? 'default' : 'pointer';
    }

    if (nextButton) {
      nextButton.disabled = currentViewIndex >= total - 1 || total <= 1;
      nextButton.style.opacity = nextButton.disabled ? '0.35' : '1';
      nextButton.style.cursor = nextButton.disabled ? 'default' : 'pointer';
    }

    if (headButton) {
      headButton.style.display = !orator && navigating ? 'flex' : 'none';
    }

    if (newHeadBadge) {
      newHeadBadge.style.display = !orator && navigating && hasNewHeadWhilePagedBack ? 'inline-block' : 'none';
    }

    if (hasDoodleCanvas()) {
      onRefreshToolInteraction?.();
    }
  }

  function displayHistoryImage(index: number, transition = 'none'): void {
    if (index < 0 || index >= imageHistory.length) return;
    if (isOrator()) {
      navigating = false;
      pagedBack = false;
      hasNewHeadWhilePagedBack = false;
    }
    currentViewIndex = index;
    pagedBack = navigating;
    if (!navigating) {
      hasNewHeadWhilePagedBack = false;
    }

    pruneHistoryResources(currentViewIndex);

    const item = imageHistory[currentViewIndex];
    if (item) {
      onDisplayPageDoodles?.(item);
    }
    if (!item || (!item.url && !item.animation)) return;

    if (promptButton) {
      promptButton.classList.remove('expanded');
      promptButton.style.display = 'none';
    }
    promptPinned = false;
    const promptText = item.prompt || item.animation?.scene_prompt || '';
    if (promptText && promptTextSpan && promptButton) {
      promptTextSpan.textContent = promptText;
      promptButton.style.display = 'flex';
    } else if (promptButton) {
      promptButton.style.display = 'none';
    }

    const animSignature = getAnimationSignature(item.animation);
    if (animSignature && item.animation) {
      playAnimation(item.animation);
      trackHistoryResource(currentViewIndex, {
        type: 'animation',
        animation: item.animation,
      });
    } else if (item.url) {
      const effectiveUrl = resolveWebpQualityUrl(item.url);
      const imgUrl = `${effectiveUrl}${effectiveUrl.includes('?') ? '&' : '?'}t=${Date.now()}`;
      void imageRenderer.applyTransition(imgUrl, transition, item.effect || 'gleam3');
      const cachedImg = new Image();
      cachedImg.src = imgUrl;
      trackHistoryResource(currentViewIndex, {
        type: 'image',
        img: cachedImg,
      });
    }

    preloadHistoryNeighbor(currentViewIndex - 1);
    preloadHistoryNeighbor(currentViewIndex + 1);

    updatePagingControlsUI();
  }

  function setMinimalMode(enabled: boolean): void {
    const previousMode = minimalMode;
    minimalMode = Boolean(enabled);
    document.documentElement.classList.toggle('minimal-mode', minimalMode);
    try {
      localStorage.setItem(MINIMAL_MODE_STORAGE_KEY, String(minimalMode));
      localStorage.removeItem(LEGACY_IMAGE_EFFECTS_STORAGE_KEY);
    } catch {
      // Storage can be unavailable in private or restricted browser contexts.
    }
    if (minimalMode) {
      imageRenderer.stopSequence?.();
    }
    imageRenderer.setImageEffectsEnabled?.(!minimalMode);
    if (previousMode !== minimalMode && currentViewIndex >= 0 && currentViewIndex < imageHistory.length) {
      loadedHistoryResources.forEach((res) => unloadHistoryResource(res));
      loadedHistoryResources.clear();
      displayHistoryImage(currentViewIndex, 'none');
    }
  }

  function stepOratorPreviousImage(): void {
    if (oratorCursorIndex > 0) {
      oratorCursorIndex -= 1;
    } else if (oratorCursorIndex < 0 && imageHistory.length > 1) {
      oratorCursorIndex = imageHistory.length - 2;
    } else if (currentViewIndex > 0) {
      oratorCursorIndex = currentViewIndex - 1;
    }
    navigating = false;
    pagedBack = false;
    hasNewHeadWhilePagedBack = false;
    currentViewIndex = oratorCursorIndex;
    if (oratorCursorIndex >= 0 && oratorCursorIndex < imageHistory.length) {
      displayHistoryImage(oratorCursorIndex, 'none');
    } else {
      updatePagingControlsUI();
    }
  }

  async function triggerOratorPreviousImage(): Promise<void> {
    stepOratorPreviousImage();
    if (!theaterId) return;
    try {
      const response = await fetch(
        `/api/theaters/${encodeURIComponent(theaterId)}/orator-action`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'previous_image' }),
        },
      );
      const result = (await response.json()) as { orator_cursor?: number | null };
      if (response.ok && result.orator_cursor !== undefined && result.orator_cursor !== null) {
        oratorCursorIndex = Number(result.orator_cursor);
        currentViewIndex = oratorCursorIndex;
        updatePagingControlsUI();
      }
    } catch (err) {
      console.error('Failed to trigger previous image action:', err);
    }
  }

  function stepOratorNextImage(): void {
    if (oratorCursorIndex >= 0 && oratorCursorIndex < imageHistory.length - 1) {
      oratorCursorIndex += 1;
    } else if (currentViewIndex >= 0 && currentViewIndex < imageHistory.length - 1) {
      oratorCursorIndex = currentViewIndex + 1;
    }
    navigating = false;
    pagedBack = false;
    hasNewHeadWhilePagedBack = false;
    currentViewIndex = oratorCursorIndex;
    if (oratorCursorIndex >= 0 && oratorCursorIndex < imageHistory.length) {
      displayHistoryImage(oratorCursorIndex, 'none');
    } else {
      updatePagingControlsUI();
    }
  }

  async function triggerOratorNextImage(): Promise<void> {
    stepOratorNextImage();
    if (!theaterId) return;
    try {
      const response = await fetch(
        `/api/theaters/${encodeURIComponent(theaterId)}/orator-action`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'next_image' }),
        },
      );
      const result = (await response.json()) as { orator_cursor?: number | null };
      if (response.ok && result.orator_cursor !== undefined && result.orator_cursor !== null) {
        oratorCursorIndex = Number(result.orator_cursor);
        currentViewIndex = oratorCursorIndex;
        updatePagingControlsUI();
      }
    } catch (err) {
      console.error('Failed to trigger next image action:', err);
    }
  }

  function syncToOratorCursor(): void {
    if (isOrator()) return;
    navigating = false;
    pagedBack = false;
    hasNewHeadWhilePagedBack = false;
    const target =
      oratorCursorIndex >= 0 && oratorCursorIndex < imageHistory.length
        ? oratorCursorIndex
        : Math.max(0, imageHistory.length - 1);
    if (target >= 0 && target < imageHistory.length) {
      displayHistoryImage(target, 'crossfade');
    } else {
      updatePagingControlsUI();
    }
  }

  function applyLatestState(data: LatestHistoryPayload): void {
    if (data.history && Array.isArray(data.history) && data.history.length > 0) {
      imageHistory = [...data.history];
    } else if (data.latest) {
      const fallbackPrompt = data.prompt || data.animation?.scene_prompt || '';
      imageHistory = [
        {
          url: data.latest,
          prompt: fallbackPrompt,
          time: data.time || 0,
          transition: data.transition || 'fade',
          effect: data.effect || 'gleam3',
          path: data.latest,
          animation: data.animation || null,
        },
      ];
    }

    const orator = isOrator();
    if (!orator || isFirstCanvasLoad || oratorCursorIndex < 0) {
      if (data.orator_cursor !== undefined && data.orator_cursor !== null) {
        oratorCursorIndex = Math.max(
          0,
          Math.min(Number(data.orator_cursor), imageHistory.length - 1),
        );
      } else if (imageHistory.length > 0) {
        oratorCursorIndex = imageHistory.length - 1;
      }
    }
    pruneHistoryResources(
      currentViewIndex >= 0
        ? currentViewIndex
        : oratorCursorIndex >= 0
          ? oratorCursorIndex
          : imageHistory.length - 1,
    );

    const currentImageEffect = data.effect || 'gleam3';
    const animationSignature = getAnimationSignature(data.animation);
    const dataTime = data.time ?? 0;
    // A show_image call may change only presentation (for example,
    // applying haze to an image the fallback already displayed).
    // Treat that as an update even when the URL has not changed.
    const isNewImage =
      Boolean(data.latest || animationSignature) &&
      (dataTime > lastTime ||
        data.latest !== lastImageRawUrl ||
        currentImageEffect !== lastImageEffect ||
        animationSignature !== lastAnimationSignature);

    if (isFirstCanvasLoad) {
      isFirstCanvasLoad = false;
      currentViewIndex =
        oratorCursorIndex >= 0 && oratorCursorIndex < imageHistory.length
          ? oratorCursorIndex
          : Math.max(0, imageHistory.length - 1);
      navigating = false;
      pagedBack = false;
      hasNewHeadWhilePagedBack = false;
      pruneHistoryResources(currentViewIndex);
      if (data.latest || animationSignature) {
        lastTime = dataTime;
        lastImageRawUrl = data.latest ?? null;
        lastImageEffect = currentImageEffect;
        lastAnimationSignature = animationSignature;
      }
      displayHistoryImage(currentViewIndex, 'none');
    } else if (
      isNewImage ||
      (!orator && !navigating && currentViewIndex !== oratorCursorIndex)
    ) {
      lastTime = dataTime;
      lastImageRawUrl = data.latest ?? null;
      lastImageEffect = currentImageEffect;
      lastAnimationSignature = animationSignature;
      const transition = (data.transition || 'crossfade').toLowerCase().trim();

      if (orator) {
        navigating = false;
        pagedBack = false;
        hasNewHeadWhilePagedBack = false;
        if (data.orator_cursor !== undefined && data.orator_cursor !== null) {
          oratorCursorIndex = Math.max(
            0,
            Math.min(Number(data.orator_cursor), imageHistory.length - 1),
          );
        } else {
          oratorCursorIndex = Math.max(0, imageHistory.length - 1);
        }
      }

      if (!navigating) {
        currentViewIndex =
          oratorCursorIndex >= 0 && oratorCursorIndex < imageHistory.length
            ? oratorCursorIndex
            : Math.max(0, imageHistory.length - 1);
        pagedBack = false;
        hasNewHeadWhilePagedBack = false;
        pruneHistoryResources(currentViewIndex);
        displayHistoryImage(currentViewIndex, transition);
      } else {
        hasNewHeadWhilePagedBack = true;
        updatePagingControlsUI();
      }
    } else if (!data.latest && !data.animation && imageHistory.length === 0) {
      if (backgroundLayer) {
        backgroundLayer.style.backgroundImage = 'none';
      }
      if (currentImage) {
        currentImage.classList.remove('loaded');
      }
      if (ghostImage) {
        ghostImage.src = '';
        ghostImage.style.opacity = '0';
      }
      if (promptButton) {
        promptButton.style.display = 'none';
      }
      loadedHistoryResources.forEach((res) => unloadHistoryResource(res));
      loadedHistoryResources.clear();
      updatePagingControlsUI();
    } else {
      updatePagingControlsUI();
    }
  }

  const handlePromptMouseEnter = (): void => {
    promptButton?.classList.add('expanded');
  };
  const handlePromptMouseLeave = (): void => {
    if (!promptPinned) {
      promptButton?.classList.remove('expanded');
    }
  };
  const handlePromptClick = (): void => {
    promptPinned = !promptPinned;
    promptButton?.classList.toggle('expanded', promptPinned);
  };

  if (promptButton) {
    promptButton.addEventListener('mouseenter', handlePromptMouseEnter);
    promptButton.addEventListener('mouseleave', handlePromptMouseLeave);
    promptButton.addEventListener('click', handlePromptClick);
  }

  const handleMinimalModeChange = (): void => {
    if (minimalModeInput) {
      setMinimalMode(minimalModeInput.checked);
    }
  };
  if (minimalModeInput) {
    minimalModeInput.checked = minimalMode;
    minimalModeInput.addEventListener('change', handleMinimalModeChange);
  }

  const handlePrevClick = (): void => {
    if (isOrator()) {
      if (currentViewIndex > 0) {
        void triggerOratorPreviousImage();
      }
    } else if (currentViewIndex > 0) {
      navigating = true;
      pagedBack = true;
      displayHistoryImage(currentViewIndex - 1, 'none');
    }
  };

  const handleNextClick = (): void => {
    if (isOrator()) {
      if (currentViewIndex < imageHistory.length - 1) {
        void triggerOratorNextImage();
      }
    } else if (currentViewIndex < imageHistory.length - 1) {
      navigating = true;
      pagedBack = true;
      displayHistoryImage(currentViewIndex + 1, 'none');
    }
  };

  prevButton?.addEventListener('click', handlePrevClick);
  nextButton?.addEventListener('click', handleNextClick);
  headButton?.addEventListener('click', syncToOratorCursor);
  syncButton?.addEventListener('click', syncToOratorCursor);

  const handleContainerPointerMove = (e: PointerEvent): void => {
    if (!pagingControls || !imageContainer) return;
    if (isSelectionActive()) return;
    if (pagingControls.classList.contains('inspector-open')) return;
    const rect = imageContainer.getBoundingClientRect();
    const relY = e.clientY - rect.top;
    const relX = e.clientX - rect.left;
    const centerX = rect.width / 2;
    // Discovery zone: within 70px of the container top and within 180px of center
    const inZone = relY >= 0 && relY <= 70 && Math.abs(relX - centerX) <= 180;
    if (inZone) {
      if (pagingHoverTimeout !== null) {
        clearTimeout(pagingHoverTimeout);
        pagingHoverTimeout = null;
      }
      pagingControls.classList.add('hover-discovered');
    } else if (pagingControls.classList.contains('hover-discovered')) {
      if (pagingHoverTimeout === null) {
        pagingHoverTimeout = setTimeout(() => {
          pagingControls.classList.remove('hover-discovered');
          pagingHoverTimeout = null;
        }, 350);
      }
    }
  };

  const handleContainerPointerLeave = (): void => {
    if (pagingHoverTimeout !== null) {
      clearTimeout(pagingHoverTimeout);
      pagingHoverTimeout = null;
    }
    pagingControls?.classList.remove('hover-discovered');
  };

  if (pagingControls && imageContainer) {
    imageContainer.addEventListener('pointermove', handleContainerPointerMove);
    imageContainer.addEventListener('pointerleave', handleContainerPointerLeave);
  }

  const handleWindowKeyDown = (e: KeyboardEvent): void => {
    const active = document.activeElement as HTMLElement | null;
    if (
      active &&
      (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)
    ) {
      return;
    }
    if (e.key === 'ArrowLeft') {
      handlePrevClick();
    } else if (e.key === 'ArrowRight') {
      handleNextClick();
    }
  };
  window.addEventListener('keydown', handleWindowKeyDown);

  return {
    resourceManager: {
      loadedHistoryResources,
      pruneHistoryResources,
      unloadHistoryResource,
      HISTORY_WINDOW_RADIUS,
    },
    resolveWebpQualityUrl,
    isMinimalMode: () => minimalMode,
    setMinimalMode,
    getHistory: () => imageHistory,
    getCurrentViewIndex: () => currentViewIndex,
    getCurrentItem: () =>
      currentViewIndex >= 0 && currentViewIndex < imageHistory.length
        ? imageHistory[currentViewIndex]
        : null,
    getOratorCursorIndex: () => oratorCursorIndex,
    setOratorCursorIndex: (index: number) => {
      oratorCursorIndex = index;
    },
    isNavigating: () => navigating,
    isPagedBack: () => pagedBack,
    stepOratorPreviousImage,
    triggerOratorPreviousImage,
    stepOratorNextImage,
    triggerOratorNextImage,
    syncToOratorCursor,
    displayHistoryImage,
    updatePagingControlsUI,
    applyLatestState,
    destroy: () => {
      if (pagingHoverTimeout !== null) {
        clearTimeout(pagingHoverTimeout);
        pagingHoverTimeout = null;
      }
      promptButton?.removeEventListener('mouseenter', handlePromptMouseEnter);
      promptButton?.removeEventListener('mouseleave', handlePromptMouseLeave);
      promptButton?.removeEventListener('click', handlePromptClick);
      minimalModeInput?.removeEventListener('change', handleMinimalModeChange);
      prevButton?.removeEventListener('click', handlePrevClick);
      nextButton?.removeEventListener('click', handleNextClick);
      headButton?.removeEventListener('click', syncToOratorCursor);
      syncButton?.removeEventListener('click', syncToOratorCursor);
      if (pagingControls && imageContainer) {
        imageContainer.removeEventListener('pointermove', handleContainerPointerMove);
        imageContainer.removeEventListener('pointerleave', handleContainerPointerLeave);
      }
      window.removeEventListener('keydown', handleWindowKeyDown);
      loadedHistoryResources.forEach((res) => unloadHistoryResource(res));
      loadedHistoryResources.clear();
    },
  };
}
