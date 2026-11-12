export interface CreateCollabBarControllerOptions {
  readonly theaterId: string | null;
  readonly bar: HTMLElement | null;
  readonly barStatus: HTMLElement | null;
  readonly guideBtn: HTMLElement | null;
  readonly guideModal: HTMLElement | null;
  readonly guideCloseBtn: HTMLElement | null;
  readonly guideAckBtn: HTMLElement | null;
  readonly guideStatus: HTMLElement | null;
  readonly isOrator: () => boolean;
  readonly isCollabEnabled: () => boolean;
  readonly onBarUpdated?: () => void;
}

export interface CollabBarController {
  updateBar(enabled: boolean): void;
  updateStatus(enabled: boolean): void;
  openGuide(): void;
  closeGuide(): void;
  isGuideOpen(): boolean;
}

/** Manage the viewer collaboration status bar and its explanatory modal. */
export function createCollabBarController(
  options: CreateCollabBarControllerOptions,
): CollabBarController {
  const {
    theaterId,
    bar,
    barStatus,
    guideBtn,
    guideModal,
    guideCloseBtn,
    guideAckBtn,
    guideStatus,
    isOrator,
    isCollabEnabled,
    onBarUpdated,
  } = options;

  function updateBar(enabled: boolean): void {
    if (bar) {
      bar.style.display = isOrator() ? 'none' : 'flex';
      bar.classList.toggle('is-enabled', Boolean(enabled));
    }
    onBarUpdated?.();
    if (barStatus) {
      barStatus.textContent = enabled ? 'Collaboration: On' : 'Collaboration: Off';
    }
  }

  function updateStatus(enabled: boolean): void {
    updateBar(enabled);
    if (!guideStatus) return;
    if (enabled) {
      guideStatus.textContent =
        '🟢 Collaboration Mode is on. Narratron will receive the top viewer suggestion and the doodled canvas on its next update.';
      guideStatus.style.background = 'rgba(34, 197, 94, 0.12)';
      guideStatus.style.borderColor = 'rgba(74, 222, 128, 0.4)';
      guideStatus.style.color = '#bbf7d0';
    } else {
      guideStatus.textContent =
        '⚪ Collaboration Mode is currently off. You can still doodle, suggest, and vote so everything is ready when the orator turns it on.';
      guideStatus.style.background = 'rgba(148, 163, 184, 0.1)';
      guideStatus.style.borderColor = 'rgba(148, 163, 184, 0.35)';
      guideStatus.style.color = '#cbd5e1';
    }
  }

  function openGuide(): void {
    if (!guideModal) return;
    updateStatus(isCollabEnabled());
    guideModal.classList.add('active');
    guideModal.setAttribute('aria-hidden', 'false');
  }

  function closeGuide(): void {
    if (!guideModal) return;
    guideModal.classList.remove('active');
    guideModal.setAttribute('aria-hidden', 'true');
    if (theaterId) {
      localStorage.setItem(`narratron_viewer_collab_guide_seen:${theaterId}`, 'true');
    }
  }

  guideBtn?.addEventListener('click', openGuide);
  guideCloseBtn?.addEventListener('click', closeGuide);
  guideAckBtn?.addEventListener('click', closeGuide);
  guideModal?.addEventListener('click', (event) => {
    if (event.target === guideModal) {
      closeGuide();
    }
  });

  return {
    updateBar,
    updateStatus,
    openGuide,
    closeGuide,
    isGuideOpen: () => Boolean(guideModal?.classList.contains('active')),
  };
}
