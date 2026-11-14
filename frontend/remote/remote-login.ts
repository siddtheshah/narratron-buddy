/** Remote mobile login gate controller. */

import { getAuthState, openAuthModal } from '../shared/auth-flow.js';

export function initRemoteLogin(): void {
  window.onAuthSuccess = (action?: string): void => {
    if (action === 'login' || action === 'register') {
      window.location.reload();
    }
  };
  const signInButton = document.getElementById('remote-sign-in');
  signInButton?.addEventListener('click', () => openAuthModal('login'));
  getAuthState()
    .then((auth) => {
      if (auth.authenticated) {
        window.location.reload();
      } else {
        openAuthModal('login');
      }
    })
    .catch(() => openAuthModal('login'));
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initRemoteLogin);
} else if (document.getElementById('remote-sign-in')) {
  initRemoteLogin();
}
