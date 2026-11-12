export interface CreateLayoutResizeControllerOptions {
  readonly container: HTMLElement;
  readonly sidebar: HTMLElement | null;
  readonly expandButton?: HTMLElement | null;
  readonly sideToggleButton?: HTMLElement | null;
  readonly breakpoint?: number;
  readonly onSettle: () => void;
  readonly isFullscreenActive?: () => boolean;
  readonly onCinematicShow?: () => void;
}

export interface LayoutResizeController {
  darkenUntilStable(delay?: number): void;
  setPanelCollapsed(collapsed: boolean, manual?: boolean): void;
  isPanelCollapsed(): boolean;
  togglePanel(): void;
}

const DEFAULT_SMALL_WINDOW_BREAKPOINT = 1280;

/**
 * Coordinate chat-sidebar collapse state and cover intermediate canvas
 * layouts so image effects never visibly fold while resizing.
 */
export function createLayoutResizeController(
  options: CreateLayoutResizeControllerOptions,
): LayoutResizeController {
  const {
    container,
    sidebar,
    expandButton = null,
    sideToggleButton = null,
    breakpoint = DEFAULT_SMALL_WINDOW_BREAKPOINT,
    onSettle,
    isFullscreenActive = () => false,
    onCinematicShow,
  } = options;

  let userToggledPanel = false;
  let panelCollapsed = window.innerWidth <= breakpoint;
  let settleTimer: ReturnType<typeof setTimeout> | null = null;
  let revealFrame: number | null = null;
  let settleUntil = 0;

  function darkenUntilStable(delay = 120): void {
    container.classList.add('layout-resizing');
    if (settleTimer !== null) {
      clearTimeout(settleTimer);
      settleTimer = null;
    }
    if (revealFrame !== null) {
      cancelAnimationFrame(revealFrame);
      revealFrame = null;
    }
    const now = Date.now();
    const targetTime = now + delay;
    if (targetTime > settleUntil) {
      settleUntil = targetTime;
    }
    const effectiveDelay = Math.max(delay, settleUntil - now);
    settleTimer = setTimeout(() => {
      settleTimer = null;
      settleUntil = 0;
      onSettle();
      // Let animated renderers paint at the final size before revealing them.
      revealFrame = requestAnimationFrame(() => {
        revealFrame = requestAnimationFrame(() => {
          revealFrame = null;
          container.classList.remove('layout-resizing');
        });
      });
    }, effectiveDelay);
  }

  let canvasLayout = container.getBoundingClientRect();
  const canvasResizeObserver = new ResizeObserver(() => {
    const nextLayout = container.getBoundingClientRect();
    if (nextLayout.width !== canvasLayout.width || nextLayout.height !== canvasLayout.height) {
      darkenUntilStable();
      canvasLayout = nextLayout;
    }
  });
  canvasResizeObserver.observe(container);

  function setPanelCollapsed(collapsed: boolean, manual = false): void {
    if (manual) userToggledPanel = true;
    panelCollapsed = collapsed;
    if (sidebar) {
      if (sidebar.classList.contains('collapsed') !== collapsed) {
        darkenUntilStable(400);
      }
      sidebar.classList.toggle('collapsed', collapsed);
    }
    if (expandButton) {
      expandButton.style.display = collapsed ? 'flex' : 'none';
    }
    if (document.body.classList.contains('fullscreen-cinematic')) {
      onCinematicShow?.();
    }
  }

  if (expandButton) {
    expandButton.addEventListener('click', () => setPanelCollapsed(false, true));
  }

  if (panelCollapsed) {
    sidebar?.classList.add('collapsed');
    if (expandButton) {
      expandButton.style.display = 'flex';
    }
  }

  window.addEventListener('resize', () => {
    if (!userToggledPanel && !isFullscreenActive()) {
      const shouldCollapse = window.innerWidth <= breakpoint;
      if (shouldCollapse !== panelCollapsed) {
        setPanelCollapsed(shouldCollapse, false);
      }
    }
  });

  if (sideToggleButton) {
    sideToggleButton.addEventListener('click', () => setPanelCollapsed(!panelCollapsed, true));
  }

  return {
    darkenUntilStable,
    setPanelCollapsed,
    isPanelCollapsed: () => panelCollapsed,
    togglePanel: () => setPanelCollapsed(!panelCollapsed, true),
  };
}
