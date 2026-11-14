/** Shared navigation topbar mobile menu controller. */

export interface SharedTopbarController {
  closeMenu(): void;
  destroy(): void;
}

const DESKTOP_BREAKPOINT_QUERY = '(min-width: 761px)';

let activeCleanup: (() => void) | null = null;

export function initSharedTopbar(): SharedTopbarController | null {
  const topbar = document.querySelector('header.narratron-topbar');
  if (!(topbar instanceof HTMLElement)) {
    return null;
  }
  const toggle = topbar.querySelector('.mobile-menu-toggle');
  const menu = topbar.querySelector('.nav-links');
  const scrim = topbar.querySelector('.mobile-menu-scrim');
  if (
    !(toggle instanceof HTMLElement) ||
    !(menu instanceof HTMLElement) ||
    !(scrim instanceof HTMLElement)
  ) {
    return null;
  }

  activeCleanup?.();

  const desktopMedia = window.matchMedia(DESKTOP_BREAKPOINT_QUERY);

  const closeMenu = (): void => {
    topbar.classList.remove('mobile-menu-open');
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-label', 'Open navigation menu');
  };

  const onToggleClick = (): void => {
    const isOpen = topbar.classList.toggle('mobile-menu-open');
    toggle.setAttribute('aria-expanded', String(isOpen));
    toggle.setAttribute('aria-label', isOpen ? 'Close navigation menu' : 'Open navigation menu');
  };

  const onMenuClick = (event: MouseEvent): void => {
    if (event.target instanceof Element && event.target.closest('a, button')) {
      closeMenu();
    }
  };

  const onKeyDown = (event: KeyboardEvent): void => {
    if (event.key === 'Escape') {
      closeMenu();
    }
  };

  const onMediaChange = (): void => {
    closeMenu();
  };

  toggle.addEventListener('click', onToggleClick);
  scrim.addEventListener('click', closeMenu);
  menu.addEventListener('click', onMenuClick);
  document.addEventListener('keydown', onKeyDown);
  desktopMedia.addEventListener('change', onMediaChange);

  const destroy = (): void => {
    toggle.removeEventListener('click', onToggleClick);
    scrim.removeEventListener('click', closeMenu);
    menu.removeEventListener('click', onMenuClick);
    document.removeEventListener('keydown', onKeyDown);
    desktopMedia.removeEventListener('change', onMediaChange);
    if (activeCleanup === destroy) {
      activeCleanup = null;
    }
  };
  activeCleanup = destroy;

  return { closeMenu, destroy };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initSharedTopbar();
  });
} else {
  initSharedTopbar();
}
