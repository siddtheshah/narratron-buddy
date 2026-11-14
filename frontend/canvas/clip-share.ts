export interface CreateClipShareControllerOptions {
  readonly theaterId?: string | null;
  readonly getTheaterId?: () => string | null;
  readonly shareTheaterBtn?: HTMLElement | null;
  readonly clipShareBtn?: HTMLElement | null;
  readonly clipShareModal?: HTMLElement | null;
  readonly clipRecordBtn?: HTMLElement | null;
  readonly clipCancelBtn?: HTMLElement | null;
  readonly obsTheaterBtn?: HTMLElement | null;
  readonly toastContainer?: HTMLElement | null;
  readonly toastDurationMs?: number;
  readonly getLocationHref?: () => string;
  readonly getOrigin?: () => string;
  readonly writeClipboard?: (text: string) => Promise<void>;
  readonly openWindow?: (url: string, target: string, features: string) => Window | null;
}

export interface ClipShareController {
  showShareToast(message: string): void;
  openClipShareModal(): void;
  closeClipShareModal(): void;
  isClipShareOpen(): boolean;
  shareTheaterLink(): Promise<void>;
  copyObsLink(): Promise<void>;
  openClipStage(): void;
  destroy(): void;
}

interface TheaterMetadataPayload {
  readonly join_key?: string | null;
}

interface TheaterDetailsResponse {
  readonly metadata?: TheaterMetadataPayload | null;
  readonly detail?: string;
}

const DEFAULT_TOAST_DURATION_MS = 2200;
const CLIP_WINDOW_NAME = 'narratron-tiktok-clip';
const CLIP_WINDOW_FEATURES = 'popup=yes,width=960,height=640';

function readErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

/**
 * Manage theater share link copying, OBS Browser Source link copying,
 * toast notifications, and the TikTok clip stage modal.
 */
export function createClipShareController(
  options: CreateClipShareControllerOptions = {},
): ClipShareController {
  const {
    theaterId = null,
    getTheaterId,
    shareTheaterBtn = null,
    clipShareBtn = null,
    clipShareModal = null,
    clipRecordBtn = null,
    clipCancelBtn = null,
    obsTheaterBtn = null,
    toastContainer = null,
    toastDurationMs = DEFAULT_TOAST_DURATION_MS,
    getLocationHref = () => window.location.href,
    getOrigin = () => window.location.origin,
    writeClipboard,
    openWindow = (url: string, target: string, features: string) =>
      window.open(url, target, features),
  } = options;

  let toastElem: HTMLDivElement | null = null;
  let toastTimer: number | null = null;

  function resolveTheaterId(): string | null {
    if (getTheaterId) {
      return getTheaterId();
    }
    return theaterId;
  }

  function showShareToast(message: string): void {
    if (!toastElem) {
      const parent = toastContainer ?? document.body;
      toastElem = document.createElement('div');
      toastElem.className = 'share-toast';
      parent.appendChild(toastElem);
    }
    toastElem.textContent = message;
    // Force reflow so repeated toasts re-trigger the CSS reveal transition.
    void toastElem.offsetWidth;
    toastElem.classList.add('show');

    if (toastTimer !== null) {
      window.clearTimeout(toastTimer);
    }
    toastTimer = window.setTimeout(() => {
      toastElem?.classList.remove('show');
      toastTimer = null;
    }, toastDurationMs);
  }

  async function copyTextWithToast(
    text: string,
    copiedMessage: string,
    fallbackMessage: string,
  ): Promise<void> {
    if (writeClipboard) {
      try {
        await writeClipboard(text);
        showShareToast(copiedMessage);
      } catch {
        showShareToast(fallbackMessage);
      }
      return;
    }

    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      try {
        await navigator.clipboard.writeText(text);
        showShareToast(copiedMessage);
      } catch {
        showShareToast(fallbackMessage);
      }
      return;
    }

    showShareToast(fallbackMessage);
  }

  async function shareTheaterLink(): Promise<void> {
    const activeTheaterId = resolveTheaterId();
    if (!activeTheaterId) return;

    try {
      const response = await fetch(`/api/theaters/${encodeURIComponent(activeTheaterId)}`);
      const data = (await response.json()) as TheaterDetailsResponse;
      const metadata = data.metadata;
      if (!response.ok || !metadata) {
        throw new Error(data.detail || 'Unable to load theater sharing details.');
      }

      const joinKey =
        metadata.join_key && !metadata.join_key.includes('Owner')
          ? metadata.join_key
          : new URL(getLocationHref()).searchParams.get('join_key');

      const shareUrl = new URL('/canvas', getOrigin());
      shareUrl.searchParams.set('theater_id', activeTheaterId);
      if (joinKey) {
        shareUrl.searchParams.set('join_key', joinKey);
      }

      await copyTextWithToast(
        shareUrl.toString(),
        '✨ Share link copied to clipboard!',
        '🔗 Link ready to share!',
      );
    } catch (error: unknown) {
      console.error('Failed to create theater share link:', error);
      showShareToast('Unable to create share link.');
    }
  }

  function openClipShareModal(): void {
    if (!clipShareModal) return;
    clipShareModal.classList.add('show');
    clipShareModal.setAttribute('aria-hidden', 'false');
  }

  function closeClipShareModal(): void {
    if (!clipShareModal) return;
    clipShareModal.classList.remove('show');
    clipShareModal.setAttribute('aria-hidden', 'true');
  }

  function isClipShareOpen(): boolean {
    return Boolean(clipShareModal?.classList.contains('show'));
  }

  function openClipStage(): void {
    closeClipShareModal();
    const clipUrl = new URL('/obs', getOrigin());
    new URL(getLocationHref()).searchParams.forEach((value, key) => {
      clipUrl.searchParams.set(key, value);
    });
    clipUrl.searchParams.set('clip', '1');
    const clipWindow = openWindow(clipUrl.toString(), CLIP_WINDOW_NAME, CLIP_WINDOW_FEATURES);
    if (!clipWindow) {
      showShareToast('Allow pop-ups to open the clean clip stage.');
    } else {
      showShareToast('🎬 The clean clip stage is ready in a new window.');
    }
  }

  async function copyObsLink(): Promise<void> {
    const currentUrl = new URL(getLocationHref());
    const obsUrl = new URL('/obs', getOrigin());
    currentUrl.searchParams.forEach((val, key) => {
      obsUrl.searchParams.set(key, val);
    });

    const activeTheaterId = resolveTheaterId();
    if (activeTheaterId && !obsUrl.searchParams.get('theater_id')) {
      obsUrl.searchParams.set('theater_id', activeTheaterId);
    }

    if (!obsUrl.searchParams.get('join_key') && activeTheaterId) {
      try {
        const response = await fetch(`/api/theaters/${encodeURIComponent(activeTheaterId)}`);
        if (response.ok) {
          const data = (await response.json()) as TheaterDetailsResponse;
          const joinKey = data.metadata?.join_key;
          if (joinKey && !joinKey.includes('Owner')) {
            obsUrl.searchParams.set('join_key', joinKey);
          }
        }
      } catch (error: unknown) {
        console.error(
          'Failed to fetch theater join key for OBS link:',
          readErrorMessage(error, String(error)),
        );
      }
    }

    await copyTextWithToast(
      obsUrl.toString(),
      '📹 OBS Browser Source link copied!',
      '📹 OBS link ready!',
    );
  }

  const onShareClick = (): void => {
    void shareTheaterLink();
  };

  const onClipShareBtnClick = (): void => {
    openClipShareModal();
  };

  const onClipCancelClick = (): void => {
    closeClipShareModal();
  };

  const onClipModalBackdropClick = (event: MouseEvent): void => {
    if (event.target === clipShareModal) {
      closeClipShareModal();
    }
  };

  const onClipRecordClick = (): void => {
    openClipStage();
  };

  const onObsClick = (): void => {
    void copyObsLink();
  };

  const onDocumentKeyDown = (event: KeyboardEvent): void => {
    if (event.key === 'Escape' && isClipShareOpen()) {
      closeClipShareModal();
    }
  };

  shareTheaterBtn?.addEventListener('click', onShareClick);
  clipShareBtn?.addEventListener('click', onClipShareBtnClick);
  clipCancelBtn?.addEventListener('click', onClipCancelClick);
  clipShareModal?.addEventListener('click', onClipModalBackdropClick);
  clipRecordBtn?.addEventListener('click', onClipRecordClick);
  obsTheaterBtn?.addEventListener('click', onObsClick);
  if (clipShareModal) {
    document.addEventListener('keydown', onDocumentKeyDown);
  }

  return {
    showShareToast,
    openClipShareModal,
    closeClipShareModal,
    isClipShareOpen,
    shareTheaterLink,
    copyObsLink,
    openClipStage,
    destroy: () => {
      if (toastTimer !== null) {
        window.clearTimeout(toastTimer);
        toastTimer = null;
      }
      if (toastElem) {
        toastElem.remove();
        toastElem = null;
      }
      shareTheaterBtn?.removeEventListener('click', onShareClick);
      clipShareBtn?.removeEventListener('click', onClipShareBtnClick);
      clipCancelBtn?.removeEventListener('click', onClipCancelClick);
      clipShareModal?.removeEventListener('click', onClipModalBackdropClick);
      clipRecordBtn?.removeEventListener('click', onClipRecordClick);
      obsTheaterBtn?.removeEventListener('click', onObsClick);
      if (clipShareModal) {
        document.removeEventListener('keydown', onDocumentKeyDown);
      }
    },
  };
}
