export interface ViewerCollabGuideAdapter {
  updateStatus(enabled: boolean): void;
  openGuide(): void;
  closeGuide(): void;
  isGuideOpen(): boolean;
}

export interface CreateTheaterModalsControllerOptions {
  readonly theaterId?: string | null;
  readonly getTheaterId?: () => string | null;
  // Pullout menu elements
  readonly pulloutOverlay?: HTMLElement | null;
  readonly menuLeftToggleBtn?: HTMLElement | null;
  readonly pulloutCloseBtn?: HTMLElement | null;
  readonly menuItemHowto?: HTMLElement | null;
  readonly menuItemShare?: HTMLElement | null;
  readonly menuItemClip?: HTMLElement | null;
  readonly menuItemObs?: HTMLElement | null;
  readonly menuItemTheaterBuilder?: HTMLAnchorElement | null;
  // Corner controls role visibility (optional)
  readonly suggestionsToggleBtn?: HTMLElement | null;
  readonly stickyNotesToggleBtn?: HTMLElement | null;
  // Pullout menu action targets/callbacks
  readonly shareTheaterBtn?: HTMLElement | null;
  readonly clipShareBtn?: HTMLElement | null;
  readonly obsTheaterBtn?: HTMLElement | null;
  readonly onOpenOratorHowto?: () => void;
  readonly onShareTheater?: () => void;
  readonly onOpenClipShare?: () => void;
  readonly onCopyObsLink?: () => void;
  // Viewer collaboration guide adapter (e.g., CollabBarController)
  readonly collabGuide?: ViewerCollabGuideAdapter | null;
  // Adventure mode guide modal elements
  readonly adventureGuideModal?: HTMLElement | null;
  readonly menuItemAdventureGuide?: HTMLElement | null;
  readonly adventureGuideCloseBtn?: HTMLElement | null;
  readonly adventureGuideAckBtn?: HTMLElement | null;
  readonly adventureGuideAutoOpenDelayMs?: number;
  // Theater YAML configuration modal elements
  readonly theaterConfigModal?: HTMLElement | null;
  readonly menuItemTheaterConfig?: HTMLElement | null;
  readonly configModalCloseBtn?: HTMLElement | null;
  readonly configModalCancelBtn?: HTMLElement | null;
  readonly configModalFormatBtn?: HTMLElement | null;
  readonly configModalSaveBtn?: HTMLButtonElement | null;
  readonly theaterConfigEditor?: HTMLTextAreaElement | null;
  readonly theaterConfigStatus?: HTMLElement | null;
  // External modal Escape-priority hooks
  readonly isOratorCommandOpen?: () => boolean;
  readonly closeOratorCommand?: () => void;
  readonly isMicConfigOpen?: () => boolean;
  readonly closeMicConfigModal?: () => void;
  readonly isOratorHowtoOpen?: () => boolean;
  readonly closeOratorHowtoModal?: () => void;
  readonly isClipShareOpen?: () => boolean;
  readonly closeClipShareModal?: () => void;
}

export interface TheaterModalsController {
  openPulloutMenu(): void;
  closePulloutMenu(): void;
  isPulloutMenuOpen(): boolean;
  applyOwnerMenuVisibility(isOwner: boolean, theaterIdOverride?: string | null): void;
  applyCornerControlsRoleVisibility(isOrator: boolean): void;
  updateViewerCollabStatus(enabled: boolean): void;
  openViewerCollabGuide(): void;
  closeViewerCollabGuide(): void;
  isViewerCollabGuideOpen(): boolean;
  maybeAutoOpenViewerCollabGuide(
    currentTheaterId?: string | null,
    delayMs?: number,
  ): boolean;
  openAdventureGuideModal(): void;
  closeAdventureGuideModal(): void;
  isAdventureGuideOpen(): boolean;
  initAdventureGuide(isAdventureMode: boolean, currentTheaterId?: string | null): void;
  openTheaterConfigModal(): Promise<void>;
  closeTheaterConfigModal(): void;
  isTheaterConfigOpen(): boolean;
  formatTheaterConfig(quiet?: boolean): Promise<boolean>;
  saveTheaterConfig(): Promise<void>;
  destroy(): void;
}

interface TheaterConfigGetResponse {
  readonly config_yaml?: string;
  readonly detail?: string;
}

interface TheaterYamlFormatResponse {
  readonly formatted_yaml?: string;
  readonly detail?: string;
}

interface TheaterConfigSaveResponse {
  readonly message?: string;
  readonly detail?: string;
}

type StatusBannerVariant = 'hidden' | 'info' | 'success' | 'error';

export const ADVENTURE_GUIDE_SEEN_STORAGE_KEY = 'narratron_adventure_guide_seen';

const DEFAULT_ADVENTURE_GUIDE_DELAY_MS = 450;
const DEFAULT_VIEWER_COLLAB_GUIDE_DELAY_MS = 400;

export function getAdventureGuideTheaterSeenKey(theaterId: string): string {
  return `narratron_adventure_guide_seen:${theaterId}`;
}

export function getViewerCollabGuideSeenKey(theaterId: string): string {
  return `narratron_viewer_collab_guide_seen:${theaterId}`;
}

function readErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

/**
 * Coordinate the canvas pullout drawer menu, viewer collaboration guide wrappers,
 * Adventure Mode guide modal, Theater YAML configuration modal, and Escape key handling.
 */
export function createTheaterModalsController(
  options: CreateTheaterModalsControllerOptions = {},
): TheaterModalsController {
  const {
    theaterId = null,
    getTheaterId,
    pulloutOverlay = null,
    menuLeftToggleBtn = null,
    pulloutCloseBtn = null,
    menuItemHowto = null,
    menuItemShare = null,
    menuItemClip = null,
    menuItemObs = null,
    menuItemTheaterBuilder = null,
    suggestionsToggleBtn = null,
    stickyNotesToggleBtn = null,
    shareTheaterBtn = null,
    clipShareBtn = null,
    obsTheaterBtn = null,
    onOpenOratorHowto,
    onShareTheater,
    onOpenClipShare,
    onCopyObsLink,
    collabGuide = null,
    adventureGuideModal = null,
    menuItemAdventureGuide = null,
    adventureGuideCloseBtn = null,
    adventureGuideAckBtn = null,
    adventureGuideAutoOpenDelayMs = DEFAULT_ADVENTURE_GUIDE_DELAY_MS,
    theaterConfigModal = null,
    menuItemTheaterConfig = null,
    configModalCloseBtn = null,
    configModalCancelBtn = null,
    configModalFormatBtn = null,
    configModalSaveBtn = null,
    theaterConfigEditor = null,
    theaterConfigStatus = null,
    isOratorCommandOpen,
    closeOratorCommand,
    isMicConfigOpen,
    closeMicConfigModal,
    isOratorHowtoOpen,
    closeOratorHowtoModal,
    isClipShareOpen,
    closeClipShareModal,
  } = options;

  let activeAdventureTheaterId: string | null = null;
  let adventureGuideAutoOpenTimer: number | null = null;
  let viewerCollabAutoOpenTimer: number | null = null;

  function resolveTheaterId(): string | null {
    if (getTheaterId) {
      return getTheaterId();
    }
    return theaterId;
  }

  // ========================================
  // Pullout Drawer Menu
  // ========================================
  function openPulloutMenu(): void {
    if (!pulloutOverlay) return;
    pulloutOverlay.classList.add('active');
    pulloutOverlay.setAttribute('aria-hidden', 'false');
  }

  function closePulloutMenu(): void {
    if (!pulloutOverlay) return;
    pulloutOverlay.classList.remove('active');
    pulloutOverlay.setAttribute('aria-hidden', 'true');
  }

  function isPulloutMenuOpen(): boolean {
    return Boolean(pulloutOverlay?.classList.contains('active'));
  }

  function applyOwnerMenuVisibility(isOwner: boolean, theaterIdOverride?: string | null): void {
    if (!menuItemTheaterBuilder) return;
    const tid = theaterIdOverride ?? resolveTheaterId();
    if (isOwner && tid) {
      menuItemTheaterBuilder.href = `/theater-editor?theater_id=${encodeURIComponent(tid)}`;
      menuItemTheaterBuilder.style.display = 'flex';
    } else {
      menuItemTheaterBuilder.style.display = 'none';
    }
  }

  function applyCornerControlsRoleVisibility(isOrator: boolean): void {
    const displayValue = isOrator ? 'flex' : 'none';
    if (suggestionsToggleBtn) {
      suggestionsToggleBtn.style.display = displayValue;
    }
    if (stickyNotesToggleBtn) {
      stickyNotesToggleBtn.style.display = displayValue;
    }
  }

  const onPulloutBackdropClick = (event: MouseEvent): void => {
    if (event.target === pulloutOverlay) {
      closePulloutMenu();
    }
  };

  const onMenuItemHowtoClick = (): void => {
    closePulloutMenu();
    onOpenOratorHowto?.();
  };

  const onMenuItemShareClick = (): void => {
    closePulloutMenu();
    if (onShareTheater) {
      onShareTheater();
    } else {
      shareTheaterBtn?.click();
    }
  };

  const onMenuItemClipClick = (): void => {
    closePulloutMenu();
    if (onOpenClipShare) {
      onOpenClipShare();
    } else {
      clipShareBtn?.click();
    }
  };

  const onMenuItemObsClick = (): void => {
    closePulloutMenu();
    if (onCopyObsLink) {
      onCopyObsLink();
    } else {
      obsTheaterBtn?.click();
    }
  };

  menuLeftToggleBtn?.addEventListener('click', openPulloutMenu);
  pulloutCloseBtn?.addEventListener('click', closePulloutMenu);
  pulloutOverlay?.addEventListener('click', onPulloutBackdropClick);
  menuItemHowto?.addEventListener('click', onMenuItemHowtoClick);
  menuItemShare?.addEventListener('click', onMenuItemShareClick);
  menuItemClip?.addEventListener('click', onMenuItemClipClick);
  menuItemObs?.addEventListener('click', onMenuItemObsClick);

  // ========================================
  // Viewer Collaboration Guide Modal
  // ========================================
  function updateViewerCollabStatus(enabled: boolean): void {
    collabGuide?.updateStatus(enabled);
  }

  function openViewerCollabGuide(): void {
    collabGuide?.openGuide();
  }

  function closeViewerCollabGuide(): void {
    collabGuide?.closeGuide();
  }

  function isViewerCollabGuideOpen(): boolean {
    return Boolean(collabGuide?.isGuideOpen());
  }

  function maybeAutoOpenViewerCollabGuide(
    currentTheaterId?: string | null,
    delayMs = DEFAULT_VIEWER_COLLAB_GUIDE_DELAY_MS,
  ): boolean {
    const tid = currentTheaterId ?? resolveTheaterId();
    if (!tid) return false;
    const storageKey = getViewerCollabGuideSeenKey(tid);
    if (localStorage.getItem(storageKey)) {
      return false;
    }
    if (viewerCollabAutoOpenTimer !== null) {
      window.clearTimeout(viewerCollabAutoOpenTimer);
    }
    viewerCollabAutoOpenTimer = window.setTimeout(() => {
      viewerCollabAutoOpenTimer = null;
      openViewerCollabGuide();
    }, delayMs);
    return true;
  }

  // ========================================
  // Adventure Mode Guide Modal
  // ========================================
  function openAdventureGuideModal(): void {
    if (!adventureGuideModal) return;
    closePulloutMenu();
    adventureGuideModal.classList.add('active');
    adventureGuideModal.setAttribute('aria-hidden', 'false');
  }

  function closeAdventureGuideModal(): void {
    if (!adventureGuideModal) return;
    adventureGuideModal.classList.remove('active');
    adventureGuideModal.setAttribute('aria-hidden', 'true');
    const tid = resolveTheaterId() || activeAdventureTheaterId || 'default';
    localStorage.setItem(ADVENTURE_GUIDE_SEEN_STORAGE_KEY, 'true');
    localStorage.setItem(getAdventureGuideTheaterSeenKey(tid), 'true');
  }

  function isAdventureGuideOpen(): boolean {
    return Boolean(adventureGuideModal?.classList.contains('active'));
  }

  function initAdventureGuide(isAdventureMode: boolean, currentTheaterId?: string | null): void {
    if (menuItemAdventureGuide) {
      menuItemAdventureGuide.style.display = isAdventureMode ? 'flex' : 'none';
    }

    if (!isAdventureMode) return;

    const tid = currentTheaterId || resolveTheaterId() || 'default';
    activeAdventureTheaterId = tid;
    const hasSeen =
      localStorage.getItem(ADVENTURE_GUIDE_SEEN_STORAGE_KEY) === 'true' ||
      localStorage.getItem(getAdventureGuideTheaterSeenKey(tid)) === 'true';

    if (!hasSeen) {
      if (adventureGuideAutoOpenTimer !== null) {
        window.clearTimeout(adventureGuideAutoOpenTimer);
      }
      adventureGuideAutoOpenTimer = window.setTimeout(() => {
        adventureGuideAutoOpenTimer = null;
        openAdventureGuideModal();
      }, adventureGuideAutoOpenDelayMs);
    }
  }

  const onAdventureGuideBackdropClick = (event: MouseEvent): void => {
    if (event.target === adventureGuideModal) {
      closeAdventureGuideModal();
    }
  };

  menuItemAdventureGuide?.addEventListener('click', openAdventureGuideModal);
  adventureGuideCloseBtn?.addEventListener('click', closeAdventureGuideModal);
  adventureGuideAckBtn?.addEventListener('click', closeAdventureGuideModal);
  adventureGuideModal?.addEventListener('click', onAdventureGuideBackdropClick);

  // ========================================
  // Theater Config Modal (theater.yaml)
  // ========================================
  function setTheaterConfigStatus(variant: StatusBannerVariant, message = ''): void {
    if (!theaterConfigStatus) return;
    if (variant === 'hidden') {
      theaterConfigStatus.style.display = 'none';
      theaterConfigStatus.textContent = '';
      return;
    }

    theaterConfigStatus.style.display = 'block';
    if (variant === 'info') {
      theaterConfigStatus.style.background = 'rgba(99, 102, 241, 0.15)';
      theaterConfigStatus.style.border = '1px solid rgba(99, 102, 241, 0.4)';
      theaterConfigStatus.style.color = '#a5b4fc';
    } else if (variant === 'success') {
      theaterConfigStatus.style.background = 'rgba(34, 197, 94, 0.15)';
      theaterConfigStatus.style.border = '1px solid rgba(34, 197, 94, 0.4)';
      theaterConfigStatus.style.color = '#4ade80';
    } else {
      theaterConfigStatus.style.background = 'rgba(239, 68, 68, 0.15)';
      theaterConfigStatus.style.border = '1px solid rgba(239, 68, 68, 0.4)';
      theaterConfigStatus.style.color = '#f87171';
    }
    theaterConfigStatus.textContent = message;
  }

  async function openTheaterConfigModal(): Promise<void> {
    if (!theaterConfigModal) return;
    closePulloutMenu();
    theaterConfigModal.classList.add('active');
    theaterConfigModal.setAttribute('aria-hidden', 'false');
    setTheaterConfigStatus('hidden');

    if (theaterConfigEditor) {
      theaterConfigEditor.value = 'Loading theater.yaml...';
      theaterConfigEditor.disabled = true;
      theaterConfigEditor.style.borderColor = 'rgba(255, 255, 255, 0.15)';
    }

    const activeTheaterId = resolveTheaterId() ?? '';
    try {
      const res = await fetch(`/api/theaters/${encodeURIComponent(activeTheaterId)}/config`);
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: Failed to load theater.yaml`);
      }
      const data = (await res.json()) as TheaterConfigGetResponse;
      if (theaterConfigEditor) {
        theaterConfigEditor.value = data.config_yaml || '';
        theaterConfigEditor.disabled = false;
      }
    } catch (error: unknown) {
      setTheaterConfigStatus('error', readErrorMessage(error, 'Error loading theater.yaml'));
      if (theaterConfigEditor) {
        theaterConfigEditor.disabled = false;
      }
    }
  }

  function closeTheaterConfigModal(): void {
    if (!theaterConfigModal) return;
    theaterConfigModal.classList.remove('active');
    theaterConfigModal.setAttribute('aria-hidden', 'true');
  }

  function isTheaterConfigOpen(): boolean {
    return Boolean(theaterConfigModal?.classList.contains('active'));
  }

  async function formatTheaterConfig(quiet = false): Promise<boolean> {
    if (!theaterConfigEditor) return false;
    const yamlText = theaterConfigEditor.value;

    try {
      const res = await fetch('/api/theaters/format-yaml', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config_yaml: yamlText }),
      });
      const data = (await res.json()) as TheaterYamlFormatResponse;

      if (!res.ok) {
        throw new Error(data.detail || 'Invalid YAML syntax');
      }

      if (data.formatted_yaml !== undefined) {
        theaterConfigEditor.value = data.formatted_yaml;
      }
      theaterConfigEditor.style.borderColor = 'rgba(34, 197, 94, 0.5)';

      if (!quiet) {
        setTheaterConfigStatus('success', '✨ YAML formatted & syntax validated successfully!');
      } else {
        setTheaterConfigStatus('hidden');
      }
      return true;
    } catch (error: unknown) {
      theaterConfigEditor.style.borderColor = 'rgba(239, 68, 68, 0.6)';
      setTheaterConfigStatus('error', `❌ ${readErrorMessage(error, 'YAML Syntax Error')}`);
      return false;
    }
  }

  async function saveTheaterConfig(): Promise<void> {
    if (!theaterConfigEditor || !configModalSaveBtn) return;

    const isValid = await formatTheaterConfig(true);
    if (!isValid) {
      return;
    }

    const yamlText = theaterConfigEditor.value;
    setTheaterConfigStatus('info', 'Saving theater.yaml directly to current theater...');
    configModalSaveBtn.disabled = true;

    const activeTheaterId = resolveTheaterId() ?? '';
    try {
      const res = await fetch(`/api/theaters/${encodeURIComponent(activeTheaterId)}/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config_yaml: yamlText }),
      });

      const data = (await res.json()) as TheaterConfigSaveResponse;
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to save theater.yaml');
      }

      const successMessage =
        data.message ||
        'Saved directly to current theater. Restart to apply configs, and ensure you save the theater state to keep these settings.';
      setTheaterConfigStatus('success', `✅ ${successMessage}`);
    } catch (error: unknown) {
      setTheaterConfigStatus('error', `❌ ${readErrorMessage(error, 'Error saving configuration')}`);
    } finally {
      configModalSaveBtn.disabled = false;
    }
  }

  const onMenuItemTheaterConfigClick = (): void => {
    void openTheaterConfigModal();
  };

  const onFormatClick = (): void => {
    void formatTheaterConfig(false);
  };

  const onSaveClick = (): void => {
    void saveTheaterConfig();
  };

  const onTheaterConfigBackdropClick = (event: MouseEvent): void => {
    if (event.target === theaterConfigModal) {
      closeTheaterConfigModal();
    }
  };

  menuItemTheaterConfig?.addEventListener('click', onMenuItemTheaterConfigClick);
  configModalCloseBtn?.addEventListener('click', closeTheaterConfigModal);
  configModalCancelBtn?.addEventListener('click', closeTheaterConfigModal);
  configModalFormatBtn?.addEventListener('click', onFormatClick);
  configModalSaveBtn?.addEventListener('click', onSaveClick);
  theaterConfigModal?.addEventListener('click', onTheaterConfigBackdropClick);

  // ========================================
  // Global Escape Key Handling
  // ========================================
  const onDocumentKeyDown = (event: KeyboardEvent): void => {
    if (event.key !== 'Escape') return;

    if (isOratorCommandOpen?.()) {
      closeOratorCommand?.();
    } else if (isPulloutMenuOpen()) {
      closePulloutMenu();
    } else if (isMicConfigOpen?.()) {
      closeMicConfigModal?.();
    } else if (isOratorHowtoOpen?.()) {
      closeOratorHowtoModal?.();
    } else if (isViewerCollabGuideOpen()) {
      closeViewerCollabGuide();
    } else if (isAdventureGuideOpen()) {
      closeAdventureGuideModal();
    } else if (isTheaterConfigOpen()) {
      closeTheaterConfigModal();
    } else if (isClipShareOpen?.()) {
      closeClipShareModal?.();
    }
  };

  document.addEventListener('keydown', onDocumentKeyDown);

  return {
    openPulloutMenu,
    closePulloutMenu,
    isPulloutMenuOpen,
    applyOwnerMenuVisibility,
    applyCornerControlsRoleVisibility,
    updateViewerCollabStatus,
    openViewerCollabGuide,
    closeViewerCollabGuide,
    isViewerCollabGuideOpen,
    maybeAutoOpenViewerCollabGuide,
    openAdventureGuideModal,
    closeAdventureGuideModal,
    isAdventureGuideOpen,
    initAdventureGuide,
    openTheaterConfigModal,
    closeTheaterConfigModal,
    isTheaterConfigOpen,
    formatTheaterConfig,
    saveTheaterConfig,
    destroy: () => {
      if (adventureGuideAutoOpenTimer !== null) {
        window.clearTimeout(adventureGuideAutoOpenTimer);
        adventureGuideAutoOpenTimer = null;
      }
      if (viewerCollabAutoOpenTimer !== null) {
        window.clearTimeout(viewerCollabAutoOpenTimer);
        viewerCollabAutoOpenTimer = null;
      }
      menuLeftToggleBtn?.removeEventListener('click', openPulloutMenu);
      pulloutCloseBtn?.removeEventListener('click', closePulloutMenu);
      pulloutOverlay?.removeEventListener('click', onPulloutBackdropClick);
      menuItemHowto?.removeEventListener('click', onMenuItemHowtoClick);
      menuItemShare?.removeEventListener('click', onMenuItemShareClick);
      menuItemClip?.removeEventListener('click', onMenuItemClipClick);
      menuItemObs?.removeEventListener('click', onMenuItemObsClick);
      menuItemAdventureGuide?.removeEventListener('click', openAdventureGuideModal);
      adventureGuideCloseBtn?.removeEventListener('click', closeAdventureGuideModal);
      adventureGuideAckBtn?.removeEventListener('click', closeAdventureGuideModal);
      adventureGuideModal?.removeEventListener('click', onAdventureGuideBackdropClick);
      menuItemTheaterConfig?.removeEventListener('click', onMenuItemTheaterConfigClick);
      configModalCloseBtn?.removeEventListener('click', closeTheaterConfigModal);
      configModalCancelBtn?.removeEventListener('click', closeTheaterConfigModal);
      configModalFormatBtn?.removeEventListener('click', onFormatClick);
      configModalSaveBtn?.removeEventListener('click', onSaveClick);
      theaterConfigModal?.removeEventListener('click', onTheaterConfigBackdropClick);
      document.removeEventListener('keydown', onDocumentKeyDown);
    },
  };
}
