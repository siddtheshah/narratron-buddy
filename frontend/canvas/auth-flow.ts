/**
 * Shared Narratron Auth Flow Module.
 * Encapsulates authentication state, user account header bar, and the Auth Modal across pages.
 */

export type AuthTab = 'login' | 'register' | 'forgot' | 'reset';

export interface AuthUser {
  readonly id?: number;
  readonly username: string;
  readonly email?: string;
  credits?: number;
  readonly profile_color?: string | null;
}

export interface AuthStateResponse {
  readonly authenticated: boolean;
  readonly user?: AuthUser | null;
  readonly testing_use_local?: boolean;
  readonly server_run_id?: string;
}

export interface AuthRefreshOptions {
  readonly refresh?: boolean;
}

export interface AuthValidationErrorItem {
  readonly msg?: string;
}

export interface AuthApiResponse {
  readonly status?: string;
  readonly message?: string;
  readonly detail?: string | readonly AuthValidationErrorItem[];
  readonly user?: AuthUser | null;
  readonly reset_link?: string;
  readonly valid?: boolean;
  readonly username?: string;
}

declare global {
  interface Window {
    currentUser?: AuthUser | null;
    openAuthModal?: (tab?: string) => void;
    closeAuthModal?: () => void;
    switchAuthTab?: (tab: string) => void;
    submitLogin?: () => Promise<void>;
    submitRegister?: () => Promise<void>;
    submitForgotPassword?: () => Promise<void>;
    submitResetPassword?: () => Promise<void>;
    submitLogout?: () => Promise<void>;
    checkAuthStatus?: (options?: AuthRefreshOptions) => Promise<AuthStateResponse | undefined>;
    invalidateAuthState?: () => void;
    onAuthSuccess?: (action?: string, data?: unknown) => void;
    openBuyCreditsModal?: (reason?: string | null) => void;
  }
}

const SERVER_RUN_STORAGE_KEY = 'narratron_server_run_id';

let authStatePromise: Promise<AuthStateResponse> | null = null;
let keydownListenerAttached = false;

window.currentUser = null;

function showModalError(message: string): void {
  const err = document.getElementById('modalError');
  if (err) {
    err.textContent = message;
    err.style.display = 'block';
  }
}

function hideModalAlerts(): void {
  const err = document.getElementById('modalError');
  const succ = document.getElementById('modalSuccess');
  if (err) err.style.display = 'none';
  if (succ) succ.style.display = 'none';
}

function getInputValue(id: string): HTMLInputElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLInputElement ? el : null;
}

function extractDetailMessage(detail: AuthApiResponse['detail'], fallback: string): string {
  if (typeof detail === 'string' && detail) {
    return detail;
  }
  if (Array.isArray(detail) && detail.length > 0 && typeof detail[0]?.msg === 'string' && detail[0].msg) {
    return detail[0].msg;
  }
  return fallback;
}

/** Return one shared auth-state request for the lifetime of the current page. */
export function getAuthState({ refresh = false }: AuthRefreshOptions = {}): Promise<AuthStateResponse> {
  if (refresh) authStatePromise = null;
  if (!authStatePromise) {
    authStatePromise = fetch('/api/auth/me')
      .then((res) => res.json() as Promise<AuthStateResponse>)
      .then(async (data) => {
        if (data && data.testing_use_local && data.server_run_id) {
          const serverRunId = data.server_run_id;
          try {
            const storedRunId = localStorage.getItem(SERVER_RUN_STORAGE_KEY);
            if (storedRunId !== serverRunId) {
              if (!data.authenticated) {
                const autoRes = await fetch('/api/auth/auto-login', { method: 'POST' });
                if (autoRes.ok) {
                  const autoData = (await autoRes.json()) as { user?: AuthUser | null };
                  data = { ...data, authenticated: true, user: autoData.user ?? null };
                }
              }
              localStorage.setItem(SERVER_RUN_STORAGE_KEY, serverRunId);
            }
          } catch (storageErr: unknown) {
            console.warn('Testing auto-login check failed:', storageErr);
          }
        }
        return data;
      })
      .catch((error: unknown) => {
        // Do not make a transient network failure stick for the whole page.
        authStatePromise = null;
        throw error;
      });
  }
  return authStatePromise;
}

export function invalidateAuthState(): void {
  authStatePromise = null;
}

export function ensureAuthModalDOM(): void {
  if (document.getElementById('authModal')) return;

  const modalContainer = document.createElement('div');
  modalContainer.className = 'modal-overlay';
  modalContainer.id = 'authModal';
  modalContainer.innerHTML = `
    <div class="modal-card">
      <button class="modal-close-btn" type="button" data-auth-action="close" aria-label="Close modal">&times;</button>

      <div class="modal-tabs" id="authModalTabs">
        <div class="modal-tab active" id="tabLogin">Log In</div>
        <div class="modal-tab" id="tabRegister">Sign Up</div>
      </div>

      <div id="modalError" class="modal-alert-error" style="display: none;"></div>
      <div id="modalSuccess" class="modal-alert-success" style="display: none;"></div>

      <!-- Login Form -->
      <div id="formLogin">
        <div class="modal-form-group">
          <label for="loginUserEmail">Username or Email</label>
          <input type="text" id="loginUserEmail" class="modal-input" placeholder="username or email">
        </div>
        <div class="modal-form-group">
          <label for="loginPassword">Password</label>
          <input type="password" id="loginPassword" class="modal-input" placeholder="••••••••">
        </div>
        <div style="text-align: right; margin-bottom: 1.25rem;">
          <a href="javascript:void(0)" data-auth-action="forgot"
            style="font-size: 0.85rem; color: var(--primary, #8b5cf6); text-decoration: none;">Forgot password?</a>
        </div>
        <button class="modal-btn-submit" type="button" data-auth-action="submit-login">Log In</button>
      </div>

      <!-- Register Form -->
      <div id="formRegister" style="display: none;">
        <div class="modal-form-group">
          <label for="regUsername">Username</label>
          <input type="text" id="regUsername" class="modal-input" placeholder="e.g. narrator1" minlength="3" maxlength="50">
        </div>
        <div class="modal-form-group">
          <label for="regEmail">Email</label>
          <input type="email" id="regEmail" class="modal-input" placeholder="you@example.com">
        </div>
        <div class="modal-form-group" style="margin-bottom: 1.5rem;">
          <label for="regPassword">Password</label>
          <input type="password" id="regPassword" class="modal-input" placeholder="••••••••" minlength="6" maxlength="128">
        </div>
        <label class="modal-age-attestation" for="regAgeAttested">
          <input type="checkbox" id="regAgeAttested" required>
          <span>I confirm that I am at least 13 years old.</span>
        </label>
        <button class="modal-btn-submit" type="button" data-auth-action="submit-register">Sign Up</button>
        <p style="margin-top: 0.9rem; font-size: 0.78rem; color: var(--text-muted, #94a3b8); text-align: center; line-height: 1.4;">
          By signing up, you agree to our <a href="/terms" target="_blank" style="color: var(--primary, #8b5cf6); text-decoration: underline;">Terms of Service</a> and <a href="/privacy" target="_blank" style="color: var(--primary, #8b5cf6); text-decoration: underline;">Privacy Policy</a>.
        </p>
      </div>

      <!-- Forgot Password Form -->
      <div id="formForgotPassword" style="display: none;">
        <h3 style="font-size: 1.2rem; font-weight: 600; margin-bottom: 0.5rem; color: #ffffff;">Reset Your Password</h3>
        <p style="color: var(--text-muted, #94a3b8); font-size: 0.88rem; margin-bottom: 1.25rem; line-height: 1.4;">
          Enter your email address or username and we'll send you a password reset link.
        </p>
        <div class="modal-form-group" style="margin-bottom: 1.25rem;">
          <label for="forgotUserEmail">Username or Email</label>
          <input type="text" id="forgotUserEmail" class="modal-input" placeholder="username or email">
        </div>
        <button class="modal-btn-submit" type="button" data-auth-action="submit-forgot">Send Reset Link</button>
        <div style="text-align: center; margin-top: 1rem;">
          <a href="javascript:void(0)" data-auth-action="back-to-login"
            style="font-size: 0.85rem; color: var(--text-muted, #94a3b8); text-decoration: none;">← Back to Log In</a>
        </div>
      </div>

      <!-- Reset Password Form -->
      <div id="formResetPassword" style="display: none;">
        <h3 style="font-size: 1.2rem; font-weight: 600; margin-bottom: 0.5rem; color: #ffffff;">Set New Password</h3>
        <p style="color: var(--text-muted, #94a3b8); font-size: 0.88rem; margin-bottom: 1.25rem; line-height: 1.4;" id="resetPasswordSubtext">
          Enter a new password for your account.
        </p>
        <input type="hidden" id="resetTokenInput">
        <div class="modal-form-group">
          <label for="resetNewPassword">New Password</label>
          <input type="password" id="resetNewPassword" class="modal-input" placeholder="••••••••">
        </div>
        <div class="modal-form-group" style="margin-bottom: 1.5rem;">
          <label for="resetConfirmPassword">Confirm New Password</label>
          <input type="password" id="resetConfirmPassword" class="modal-input" placeholder="••••••••">
        </div>
        <button class="modal-btn-submit" type="button" data-auth-action="submit-reset">Set New Password</button>
        <div style="text-align: center; margin-top: 1rem;">
          <a href="javascript:void(0)" data-auth-action="back-to-login"
            style="font-size: 0.85rem; color: var(--text-muted, #94a3b8); text-decoration: none;">← Back to Log In</a>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modalContainer);

  modalContainer.addEventListener('click', (e: MouseEvent) => {
    if (e.target === modalContainer) closeAuthModal();
  });

  modalContainer.querySelector('[data-auth-action="close"]')?.addEventListener('click', closeAuthModal);
  modalContainer.querySelector('#tabLogin')?.addEventListener('click', () => switchAuthTab('login'));
  modalContainer.querySelector('#tabRegister')?.addEventListener('click', () => switchAuthTab('register'));
  modalContainer.querySelector('[data-auth-action="forgot"]')?.addEventListener('click', () => switchAuthTab('forgot'));
  modalContainer.querySelectorAll('[data-auth-action="back-to-login"]').forEach((el) => {
    el.addEventListener('click', () => switchAuthTab('login'));
  });

  modalContainer.querySelector('[data-auth-action="submit-login"]')?.addEventListener('click', () => {
    void submitLogin();
  });
  modalContainer.querySelector('[data-auth-action="submit-register"]')?.addEventListener('click', () => {
    void submitRegister();
  });
  modalContainer.querySelector('[data-auth-action="submit-forgot"]')?.addEventListener('click', () => {
    void submitForgotPassword();
  });
  modalContainer.querySelector('[data-auth-action="submit-reset"]')?.addEventListener('click', () => {
    void submitResetPassword();
  });

  const bindEnter = (inputId: string, handler: () => Promise<void>): void => {
    modalContainer.querySelector(`#${inputId}`)?.addEventListener('keydown', (event: Event) => {
      if (event instanceof KeyboardEvent && !event.isComposing && event.key === 'Enter') {
        void handler();
      }
    });
  };
  bindEnter('loginUserEmail', submitLogin);
  bindEnter('loginPassword', submitLogin);
  bindEnter('regPassword', submitRegister);
  bindEnter('forgotUserEmail', submitForgotPassword);
  bindEnter('resetConfirmPassword', submitResetPassword);
}

export async function checkAuthStatus({
  refresh = false,
}: AuthRefreshOptions = {}): Promise<AuthStateResponse | undefined> {
  try {
    const data = await getAuthState({ refresh });
    const bar = document.getElementById('userAccountBar');

    if (data.authenticated && data.user) {
      window.currentUser = data.user;
      if (bar) {
        bar.replaceChildren();
        const hasBuyModal = typeof window.openBuyCreditsModal === 'function';
        const creditBadge = document.createElement(hasBuyModal ? 'button' : 'div');
        if (creditBadge instanceof HTMLButtonElement) {
          creditBadge.type = 'button';
          creditBadge.addEventListener('click', () => {
            window.openBuyCreditsModal?.();
          });
        }
        creditBadge.className = 'credit-badge';
        creditBadge.title = 'Account credits balance';
        creditBadge.textContent = `⚡ ${(data.user.credits ?? 0).toFixed(1)}`;
        if (hasBuyModal) {
          const plusSpan = document.createElement('span');
          plusSpan.className = 'buy-credits-plus-btn';
          plusSpan.textContent = '+ Buy';
          creditBadge.append(' ', plusSpan);
        }

        const userMenu = document.createElement('div');
        userMenu.className = 'user-menu';

        const userPill = document.createElement('a');
        userPill.className = 'user-pill';
        userPill.href = `/users/${encodeURIComponent(data.user.username)}`;
        userPill.textContent = `👤 ${data.user.username}`;

        const dropdown = document.createElement('div');
        dropdown.className = 'user-menu-dropdown';
        dropdown.setAttribute('role', 'menu');
        dropdown.setAttribute('aria-label', 'Account menu');

        const logoutBtn = document.createElement('button');
        logoutBtn.className = 'user-menu-item';
        logoutBtn.type = 'button';
        logoutBtn.setAttribute('role', 'menuitem');
        logoutBtn.textContent = 'Log out';
        logoutBtn.addEventListener('click', () => {
          void submitLogout();
        });

        dropdown.appendChild(logoutBtn);
        userMenu.append(userPill, dropdown);
        bar.append(creditBadge, userMenu);
      }
    } else {
      window.currentUser = null;
      if (bar) {
        bar.replaceChildren();
        const loginBtn = document.createElement('button');
        loginBtn.className = 'auth-nav-btn';
        loginBtn.textContent = 'Log In';
        loginBtn.addEventListener('click', () => openAuthModal('login'));

        const registerBtn = document.createElement('button');
        registerBtn.className = 'auth-nav-btn auth-nav-btn-primary';
        registerBtn.textContent = 'Sign Up';
        registerBtn.addEventListener('click', () => openAuthModal('register'));

        bar.append(loginBtn, registerBtn);
      }
    }

    window.dispatchEvent(new CustomEvent<AuthStateResponse>('narratron:auth-changed', { detail: data }));
    return data;
  } catch (e: unknown) {
    console.error('Failed to check auth status:', e);
    return undefined;
  }
}

export function openAuthModal(tab = 'login'): void {
  ensureAuthModalDOM();
  const modal = document.getElementById('authModal');
  if (modal) {
    modal.classList.add('active');
    switchAuthTab(tab);
  }
}

export function closeAuthModal(): void {
  const modal = document.getElementById('authModal');
  if (modal) {
    modal.classList.remove('active');
  }
  hideModalAlerts();
}

export function switchAuthTab(tab: string): void {
  ensureAuthModalDOM();
  const tabsContainer = document.getElementById('authModalTabs');
  const formLogin = document.getElementById('formLogin');
  const formRegister = document.getElementById('formRegister');
  const formForgot = document.getElementById('formForgotPassword');
  const formReset = document.getElementById('formResetPassword');

  hideModalAlerts();

  if (tabsContainer) {
    tabsContainer.style.display = tab === 'login' || tab === 'register' ? 'flex' : 'none';
  }

  const tabLogin = document.getElementById('tabLogin');
  const tabRegister = document.getElementById('tabRegister');
  if (tabLogin) tabLogin.classList.toggle('active', tab === 'login');
  if (tabRegister) tabRegister.classList.toggle('active', tab === 'register');

  if (formLogin) formLogin.style.display = tab === 'login' ? 'block' : 'none';
  if (formRegister) formRegister.style.display = tab === 'register' ? 'block' : 'none';
  if (formForgot) formForgot.style.display = tab === 'forgot' ? 'block' : 'none';
  if (formReset) formReset.style.display = tab === 'reset' ? 'block' : 'none';
}

export async function submitLogin(): Promise<void> {
  const valInput = getInputValue('loginUserEmail');
  const pwdInput = getInputValue('loginPassword');
  if (!valInput || !pwdInput) return;

  const val = valInput.value.trim();
  const pwd = pwdInput.value;
  hideModalAlerts();

  if (!val || !pwd) {
    showModalError('Please enter username/email and password.');
    return;
  }

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username_or_email: val, password: pwd }),
    });
    const data = (await res.json()) as AuthApiResponse;
    if (res.ok) {
      closeAuthModal();
      invalidateAuthState();
      await checkAuthStatus();
      if (typeof window.onAuthSuccess === 'function') {
        window.onAuthSuccess('login', data);
      }
    } else {
      showModalError(extractDetailMessage(data.detail, 'Login failed.'));
    }
  } catch {
    showModalError('Network error. Please try again.');
  }
}

export async function submitRegister(): Promise<void> {
  const userEl = getInputValue('regUsername');
  const emailEl = getInputValue('regEmail');
  const passEl = getInputValue('regPassword');
  const ageEl = getInputValue('regAgeAttested');
  if (!userEl || !emailEl || !passEl || !ageEl) return;

  const username = userEl.value.trim();
  const email = emailEl.value.trim();
  const password = passEl.value;
  hideModalAlerts();

  if (!username || !email || !password) {
    showModalError('Please fill out all fields.');
    return;
  }

  if (username.length < 3 || username.length > 50) {
    showModalError('Username must be between 3 and 50 characters.');
    userEl.focus();
    return;
  }

  if (password.length < 6 || password.length > 128) {
    showModalError('Password must be between 6 and 128 characters.');
    passEl.focus();
    return;
  }

  if (!ageEl.checked) {
    showModalError('Please confirm that you are at least 13 years old.');
    ageEl.focus();
    return;
  }

  try {
    const res = await fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, email, password, age_attested: ageEl.checked }),
    });
    const data = (await res.json()) as AuthApiResponse;
    if (res.ok) {
      ageEl.checked = false;
      closeAuthModal();
      invalidateAuthState();
      await checkAuthStatus();
      if (typeof window.onAuthSuccess === 'function') {
        window.onAuthSuccess('register', data);
      }
    } else {
      showModalError(extractDetailMessage(data.detail, 'Registration failed.'));
    }
  } catch {
    showModalError('Network error. Please try again.');
  }
}

export async function submitForgotPassword(): Promise<void> {
  const inputEl = getInputValue('forgotUserEmail');
  if (!inputEl) return;

  const input = inputEl.value.trim();
  const success = document.getElementById('modalSuccess');
  hideModalAlerts();

  if (!input) {
    showModalError('Please enter your username or email address.');
    return;
  }

  try {
    const res = await fetch('/api/auth/forgot-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username_or_email: input }),
    });
    if (!res.ok) {
      const data = (await res.json().catch(() => ({}))) as AuthApiResponse;
      showModalError(extractDetailMessage(data.detail, 'Password reset request failed. Please try again later.'));
      return;
    }
    const data = (await res.json()) as AuthApiResponse;
    if (success) {
      success.replaceChildren();
      success.appendChild(document.createTextNode(data.message || 'Password reset link sent!'));
      if (data.reset_link) {
        success.appendChild(document.createElement('br'));
        success.appendChild(document.createElement('br'));
        const label = document.createElement('strong');
        label.textContent = 'Dev Mode Reset Link:';
        success.appendChild(label);
        success.appendChild(document.createElement('br'));
        const link = document.createElement('a');
        link.href = data.reset_link;
        link.style.color = '#c084fc';
        link.style.wordBreak = 'break-all';
        link.textContent = data.reset_link;
        success.appendChild(link);
      }
      success.style.display = 'block';
    }
  } catch {
    showModalError('Network error. Please try again.');
  }
}

export async function submitResetPassword(): Promise<void> {
  const token = getInputValue('resetTokenInput')?.value ?? '';
  const newPassword = getInputValue('resetNewPassword')?.value ?? '';
  const confirmPassword = getInputValue('resetConfirmPassword')?.value ?? '';
  hideModalAlerts();

  if (!newPassword || !confirmPassword) {
    showModalError('Please enter and confirm your new password.');
    return;
  }

  if (newPassword !== confirmPassword) {
    showModalError('Passwords do not match.');
    return;
  }

  try {
    const res = await fetch('/api/auth/reset-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, new_password: newPassword }),
    });
    if (!res.ok) {
      const data = (await res.json().catch(() => ({}))) as AuthApiResponse;
      showModalError(extractDetailMessage(data.detail, 'Password reset failed. Please try again later.'));
      return;
    }
    const data = (await res.json()) as AuthApiResponse;
    invalidateAuthState();
    switchAuthTab('login');
    const loginSuccess = document.getElementById('modalSuccess');
    if (loginSuccess) {
      loginSuccess.textContent = data.message || 'Password updated successfully! Please log in.';
      loginSuccess.style.display = 'block';
    }
  } catch {
    showModalError('Network error. Please try again.');
  }
}

export async function checkResetTokenInURL(): Promise<void> {
  const urlParams = new URLSearchParams(window.location.search);
  const resetToken = urlParams.get('reset_token');
  if (resetToken) {
    try {
      const res = await fetch(`/api/auth/reset-password/validate?token=${encodeURIComponent(resetToken)}`);
      const data = (await res.json()) as AuthApiResponse;
      if (data.valid) {
        ensureAuthModalDOM();
        const tokenInput = getInputValue('resetTokenInput');
        if (tokenInput) {
          tokenInput.value = resetToken;
        }
        const sub = document.getElementById('resetPasswordSubtext');
        if (sub) sub.textContent = `Set a new password for ${data.username ?? ''}.`;
        openAuthModal('reset');
      } else {
        openAuthModal('forgot');
        showModalError(
          extractDetailMessage(data.detail, 'The reset link is invalid or expired. Please request a new link.'),
        );
      }
    } catch (e: unknown) {
      console.error('Error validating reset token:', e);
    }
  }
}

export async function submitLogout(): Promise<void> {
  await fetch('/api/auth/logout', { method: 'POST' });
  invalidateAuthState();
  await checkAuthStatus();
  if (typeof window.onAuthSuccess === 'function') {
    window.onAuthSuccess('logout');
  }
}

export function initAuthFlow(): void {
  ensureAuthModalDOM();
  void checkAuthStatus();
  void checkResetTokenInURL();

  if (!keydownListenerAttached) {
    keydownListenerAttached = true;
    document.addEventListener('keydown', (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeAuthModal();
    });
  }
}

Reflect.set(window, 'getAuthState', getAuthState);
window.invalidateAuthState = invalidateAuthState;
window.checkAuthStatus = checkAuthStatus;
window.openAuthModal = openAuthModal;
window.closeAuthModal = closeAuthModal;
window.switchAuthTab = switchAuthTab;
window.submitLogin = submitLogin;
window.submitRegister = submitRegister;
window.submitForgotPassword = submitForgotPassword;
window.submitResetPassword = submitResetPassword;
window.submitLogout = submitLogout;

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAuthFlow);
} else {
  initAuthFlow();
}
