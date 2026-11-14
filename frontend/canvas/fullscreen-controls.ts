import {
  createLayoutResizeController,
  type LayoutResizeController,
} from './layout-resize.js';

export interface CreateFullscreenControlsOptions {
  readonly fullscreenBtn?: HTMLElement | null;
  readonly fullscreenIcon?: Element | null;
  readonly container?: HTMLElement | null;
  readonly sidebar?: HTMLElement | null;
  readonly expandButton?: HTMLElement | null;
  readonly sideToggleButton?: HTMLElement | null;
  readonly breakpoint?: number;
  readonly layoutResize?: LayoutResizeController;
  readonly onResizeCanvases?: () => void;
  readonly cinematicInactivityMs?: number;
  readonly resizeDelayMs?: number;
}

export interface FullscreenControlsController {
  isFullscreenActive(): boolean;
  showCinematicUI(): void;
  updateFullscreenUI(): void;
  toggleFullScreen(): void;
  setPanelCollapsed(collapsed: boolean, manual?: boolean): void;
  isPanelCollapsed(): boolean;
  togglePanel(): void;
  darkenUntilStable(delay?: number): void;
  destroy(): void;
}

interface VendorFullscreenDocument extends Document {
  readonly webkitFullscreenElement?: Element | null;
  readonly mozFullScreenElement?: Element | null;
  readonly msFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => Promise<void> | void;
  mozCancelFullScreen?: () => Promise<void> | void;
  msExitFullscreen?: () => Promise<void> | void;
}

interface VendorFullscreenElement extends HTMLElement {
  webkitRequestFullscreen?: () => Promise<void> | void;
  mozRequestFullScreen?: () => Promise<void> | void;
  msRequestFullscreen?: () => Promise<void> | void;
}

const FULLSCREEN_CHANGE_EVENTS: readonly string[] = [
  'fullscreenchange',
  'webkitfullscreenchange',
  'mozfullscreenchange',
  'MSFullscreenChange',
];

const ENTER_FULLSCREEN_ICON_PATH =
  '<path d="M8 3H5a2 2 0 0 1-2 2v3m18 0V5a2 2 0 0 1-2-2h-3m0 18h3a2 2 0 0 1 2-2v-3M3 16v3a2 2 0 0 1 2 2h3"/>';
const EXIT_FULLSCREEN_ICON_PATH =
  '<path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3"/>';

const DEFAULT_CINEMATIC_INACTIVITY_MS = 2500;
const DEFAULT_RESIZE_DELAY_MS = 100;

/**
 * Coordinate browser fullscreen transitions, cinematic UI auto-hide timers,
 * and chat-sidebar panel collapse behavior.
 */
export function createFullscreenControlsController(
  options: CreateFullscreenControlsOptions = {},
): FullscreenControlsController {
  const {
    fullscreenBtn = null,
    fullscreenIcon = null,
    container = null,
    sidebar = null,
    expandButton = null,
    sideToggleButton = null,
    breakpoint,
    layoutResize: providedLayoutResize,
    onResizeCanvases,
    cinematicInactivityMs = DEFAULT_CINEMATIC_INACTIVITY_MS,
    resizeDelayMs = DEFAULT_RESIZE_DELAY_MS,
  } = options;

  let mouseInactivityTimer: ReturnType<typeof setTimeout> | null = null;
  let resizeTimer: ReturnType<typeof setTimeout> | null = null;
  let fallbackCollapsed = false;
  let fallbackTeardown: (() => void) | null = null;

  function isFullscreenActive(): boolean {
    const doc = document as VendorFullscreenDocument;
    return Boolean(
      doc.fullscreenElement ||
        doc.webkitFullscreenElement ||
        doc.mozFullScreenElement ||
        doc.msFullscreenElement,
    );
  }

  function showCinematicUI(): void {
    if (!document.body.classList.contains('fullscreen-cinematic')) return;
    document.body.classList.add('ui-visible');
    if (mouseInactivityTimer !== null) {
      clearTimeout(mouseInactivityTimer);
    }
    mouseInactivityTimer = setTimeout(() => {
      mouseInactivityTimer = null;
      document.body.classList.remove('ui-visible');
    }, cinematicInactivityMs);
  }

  const handleCinematicMouseMove = (): void => {
    showCinematicUI();
  };

  const createFallbackLayoutResize = (): LayoutResizeController => {
    const applyCollapsed = (collapsed: boolean): void => {
      fallbackCollapsed = collapsed;
      sidebar?.classList.toggle('collapsed', collapsed);
      if (expandButton) {
        expandButton.style.display = collapsed ? 'flex' : 'none';
      }
      if (document.body.classList.contains('fullscreen-cinematic')) {
        showCinematicUI();
      }
    };
    const onExpandClick = (): void => applyCollapsed(false);
    const onSideToggleClick = (): void => applyCollapsed(!fallbackCollapsed);
    expandButton?.addEventListener('click', onExpandClick);
    sideToggleButton?.addEventListener('click', onSideToggleClick);
    fallbackTeardown = () => {
      expandButton?.removeEventListener('click', onExpandClick);
      sideToggleButton?.removeEventListener('click', onSideToggleClick);
    };
    return {
      darkenUntilStable: () => {},
      setPanelCollapsed: (collapsed: boolean) => applyCollapsed(collapsed),
      isPanelCollapsed: () => fallbackCollapsed,
      togglePanel: () => applyCollapsed(!fallbackCollapsed),
    };
  };

  const layoutResize: LayoutResizeController =
    providedLayoutResize ??
    (container
      ? createLayoutResizeController({
          container,
          sidebar,
          expandButton,
          sideToggleButton,
          breakpoint,
          onSettle: () => {
            onResizeCanvases?.();
          },
          isFullscreenActive,
          onCinematicShow: showCinematicUI,
        })
      : createFallbackLayoutResize());

  function setPanelCollapsed(collapsed: boolean, manual = false): void {
    layoutResize.setPanelCollapsed(collapsed, manual);
  }

  function updateFullscreenUI(): void {
    const active = isFullscreenActive();
    if (fullscreenBtn) {
      fullscreenBtn.title = active ? 'Exit Full Screen (Esc)' : 'Full Screen (F11)';
    }
    if (fullscreenIcon) {
      fullscreenIcon.innerHTML = active ? EXIT_FULLSCREEN_ICON_PATH : ENTER_FULLSCREEN_ICON_PATH;
    }

    if (active) {
      if (!document.body.classList.contains('fullscreen-cinematic')) {
        setPanelCollapsed(true);
      }
      document.body.classList.add('fullscreen-cinematic');
      window.addEventListener('mousemove', handleCinematicMouseMove);
      showCinematicUI();
    } else {
      document.body.classList.remove('fullscreen-cinematic', 'ui-visible');
      window.removeEventListener('mousemove', handleCinematicMouseMove);
      if (mouseInactivityTimer !== null) {
        clearTimeout(mouseInactivityTimer);
        mouseInactivityTimer = null;
      }
    }

    if (resizeTimer !== null) {
      clearTimeout(resizeTimer);
    }
    resizeTimer = setTimeout(() => {
      resizeTimer = null;
      onResizeCanvases?.();
    }, resizeDelayMs);
  }

  function toggleFullScreen(): void {
    if (!isFullscreenActive()) {
      const docEl = document.documentElement as VendorFullscreenElement;
      if (docEl.requestFullscreen) void docEl.requestFullscreen();
      else if (docEl.webkitRequestFullscreen) void docEl.webkitRequestFullscreen();
      else if (docEl.mozRequestFullScreen) void docEl.mozRequestFullScreen();
      else if (docEl.msRequestFullscreen) void docEl.msRequestFullscreen();
    } else {
      const doc = document as VendorFullscreenDocument;
      if (doc.exitFullscreen) void doc.exitFullscreen();
      else if (doc.webkitExitFullscreen) void doc.webkitExitFullscreen();
      else if (doc.mozCancelFullScreen) void doc.mozCancelFullScreen();
      else if (doc.msExitFullscreen) void doc.msExitFullscreen();
    }
  }

  fullscreenBtn?.addEventListener('click', toggleFullScreen);
  for (const evt of FULLSCREEN_CHANGE_EVENTS) {
    document.addEventListener(evt, updateFullscreenUI);
  }

  return {
    isFullscreenActive,
    showCinematicUI,
    updateFullscreenUI,
    toggleFullScreen,
    setPanelCollapsed,
    isPanelCollapsed: () => layoutResize.isPanelCollapsed(),
    togglePanel: () => layoutResize.togglePanel(),
    darkenUntilStable: (delay?: number) => layoutResize.darkenUntilStable(delay),
    destroy: () => {
      fullscreenBtn?.removeEventListener('click', toggleFullScreen);
      for (const evt of FULLSCREEN_CHANGE_EVENTS) {
        document.removeEventListener(evt, updateFullscreenUI);
      }
      window.removeEventListener('mousemove', handleCinematicMouseMove);
      fallbackTeardown?.();
      fallbackTeardown = null;
      if (mouseInactivityTimer !== null) {
        clearTimeout(mouseInactivityTimer);
        mouseInactivityTimer = null;
      }
      if (resizeTimer !== null) {
        clearTimeout(resizeTimer);
        resizeTimer = null;
      }
    },
  };
}
