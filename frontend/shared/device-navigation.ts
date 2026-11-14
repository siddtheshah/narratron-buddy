/** Shared device classification and mobile/desktop theater URL navigation. */

export interface NarratronNavigationApi {
  isMobile(): boolean;
  theaterUrl(theaterId: string): string;
}

declare global {
  interface Window {
    NarratronNavigation?: NarratronNavigationApi;
  }
}

const SMALL_SCREEN_QUERY = '(max-width: 700px)';
const MOBILE_UA_PATTERN = /Android|iPhone|iPad|iPod|Mobile/i;

let activeCleanup: (() => void) | null = null;

export function isMobile(): boolean {
  const smallScreen = window.matchMedia(SMALL_SCREEN_QUERY);
  const mobileDevice = MOBILE_UA_PATTERN.test(navigator.userAgent);
  return mobileDevice || smallScreen.matches;
}

export function theaterUrl(theaterId: string): string {
  const path = isMobile() ? '/remote' : '/canvas';
  return `${path}?${new URLSearchParams({ theater_id: theaterId })}`;
}

export function updateDeviceNavigation(): void {
  document.documentElement.classList.toggle('mobile-device', isMobile());
  if (isMobile() && location.pathname === '/canvas') {
    const theaterId = new URLSearchParams(location.search).get('theater_id');
    location.replace(theaterId ? theaterUrl(theaterId) : '/deploy');
  }
}

export function initDeviceNavigation(): () => void {
  activeCleanup?.();
  const smallScreen = window.matchMedia(SMALL_SCREEN_QUERY);
  const onChange = (): void => {
    updateDeviceNavigation();
  };
  window.NarratronNavigation = { isMobile, theaterUrl };
  smallScreen.addEventListener('change', onChange);
  updateDeviceNavigation();

  const cleanup = (): void => {
    smallScreen.removeEventListener('change', onChange);
    if (activeCleanup === cleanup) {
      activeCleanup = null;
    }
  };
  activeCleanup = cleanup;
  return cleanup;
}

initDeviceNavigation();
