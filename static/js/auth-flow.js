/**
 * Shared Narratron Auth Flow Module
 * Encapsulates authentication state, user account header bar, and the Auth Modal across pages.
 */

window.currentUser = null;
let authStatePromise = null;

/** Return one shared auth-state request for the lifetime of the current page. */
function getAuthState({ refresh = false } = {}) {
  if (refresh) authStatePromise = null;
  if (!authStatePromise) {
    authStatePromise = fetch('/api/auth/me')
      .then(res => res.json())
      .catch(error => {
        // Do not make a transient network failure stick for the whole page.
        authStatePromise = null;
        throw error;
      });
  }
  return authStatePromise;
}

function invalidateAuthState() {
  authStatePromise = null;
}

function ensureAuthModalDOM() {
  if (document.getElementById('authModal')) return;

  const modalContainer = document.createElement('div');
  modalContainer.className = 'modal-overlay';
  modalContainer.id = 'authModal';
  modalContainer.innerHTML = `
    <div class="modal-card">
      <button class="modal-close-btn" onclick="closeAuthModal()" aria-label="Close modal">&times;</button>
      
      <div class="modal-tabs" id="authModalTabs">
        <div class="modal-tab active" id="tabLogin" onclick="switchAuthTab('login')">Log In</div>
        <div class="modal-tab" id="tabRegister" onclick="switchAuthTab('register')">Sign Up</div>
      </div>

      <div id="modalError" class="modal-alert-error" style="display: none;"></div>
      <div id="modalSuccess" class="modal-alert-success" style="display: none;"></div>

      <!-- Login Form -->
      <div id="formLogin">
        <div class="modal-form-group">
          <label for="loginUserEmail">Username or Email</label>
          <input type="text" id="loginUserEmail" class="modal-input" placeholder="username or email"
            onkeypress="if(event.key==='Enter') submitLogin()">
        </div>
        <div class="modal-form-group">
          <label for="loginPassword">Password</label>
          <input type="password" id="loginPassword" class="modal-input" placeholder="••••••••"
            onkeypress="if(event.key==='Enter') submitLogin()">
        </div>
        <div style="text-align: right; margin-bottom: 1.25rem;">
          <a href="javascript:void(0)" onclick="switchAuthTab('forgot')"
            style="font-size: 0.85rem; color: var(--primary, #8b5cf6); text-decoration: none;">Forgot password?</a>
        </div>
        <button class="modal-btn-submit" onclick="submitLogin()">Log In</button>
      </div>

      <!-- Register Form -->
      <div id="formRegister" style="display: none;">
        <div class="modal-form-group">
          <label for="regUsername">Username</label>
          <input type="text" id="regUsername" class="modal-input" placeholder="e.g. narrator1">
        </div>
        <div class="modal-form-group">
          <label for="regEmail">Email</label>
          <input type="email" id="regEmail" class="modal-input" placeholder="you@example.com">
        </div>
        <div class="modal-form-group" style="margin-bottom: 1.5rem;">
          <label for="regPassword">Password</label>
          <input type="password" id="regPassword" class="modal-input" placeholder="••••••••"
            onkeypress="if(event.key==='Enter') submitRegister()">
        </div>
        <button class="modal-btn-submit" onclick="submitRegister()">Sign Up</button>
      </div>

      <!-- Forgot Password Form -->
      <div id="formForgotPassword" style="display: none;">
        <h3 style="font-size: 1.2rem; font-weight: 600; margin-bottom: 0.5rem; color: #ffffff;">Reset Your Password</h3>
        <p style="color: var(--text-muted, #94a3b8); font-size: 0.88rem; margin-bottom: 1.25rem; line-height: 1.4;">
          Enter your email address or username and we'll send you a password reset link.
        </p>
        <div class="modal-form-group" style="margin-bottom: 1.25rem;">
          <label for="forgotUserEmail">Username or Email</label>
          <input type="text" id="forgotUserEmail" class="modal-input" placeholder="username or email"
            onkeypress="if(event.key==='Enter') submitForgotPassword()">
        </div>
        <button class="modal-btn-submit" onclick="submitForgotPassword()">Send Reset Link</button>
        <div style="text-align: center; margin-top: 1rem;">
          <a href="javascript:void(0)" onclick="switchAuthTab('login')"
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
          <input type="password" id="resetConfirmPassword" class="modal-input" placeholder="••••••••"
            onkeypress="if(event.key==='Enter') submitResetPassword()">
        </div>
        <button class="modal-btn-submit" onclick="submitResetPassword()">Set New Password</button>
        <div style="text-align: center; margin-top: 1rem;">
          <a href="javascript:void(0)" onclick="switchAuthTab('login')"
            style="font-size: 0.85rem; color: var(--text-muted, #94a3b8); text-decoration: none;">← Back to Log In</a>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modalContainer);

  modalContainer.addEventListener('click', (e) => {
    if (e.target === modalContainer) closeAuthModal();
  });
}

async function checkAuthStatus({ refresh = false } = {}) {
  try {
    const data = await getAuthState({ refresh });
    const bar = document.getElementById('userAccountBar');

    if (data.authenticated && data.user) {
      window.currentUser = data.user;
      if (bar) {
        const hasBuyModal = typeof window.openBuyCreditsModal === 'function';
        bar.innerHTML = `
          <div class="credit-badge" ${hasBuyModal ? 'onclick="openBuyCreditsModal()"' : ''} title="Account credits balance">
            ⚡ ${(data.user.credits || 0).toFixed(1)} Credits
            ${hasBuyModal ? '<span class="buy-credits-plus-btn">+ Buy</span>' : ''}
          </div>
          <a class="user-pill" href="/users/${encodeURIComponent(data.user.username)}">👤 ${data.user.username}</a>
          <button class="auth-nav-btn" onclick="submitLogout()">Logout</button>
        `;
      }
    } else {
      window.currentUser = null;
      if (bar) {
        bar.innerHTML = `
          <button class="auth-nav-btn" onclick="openAuthModal('login')">Log In</button>
          <button class="auth-nav-btn auth-nav-btn-primary" onclick="openAuthModal('register')">Sign Up</button>
        `;
      }
    }

    window.dispatchEvent(new CustomEvent('narratron:auth-changed', { detail: data }));
    return data;
  } catch (e) {
    console.error('Failed to check auth status:', e);
  }
}

function openAuthModal(tab = 'login') {
  ensureAuthModalDOM();
  const modal = document.getElementById('authModal');
  if (modal) {
    modal.classList.add('active');
    switchAuthTab(tab);
  }
}

function closeAuthModal() {
  const modal = document.getElementById('authModal');
  if (modal) {
    modal.classList.remove('active');
  }
  const err = document.getElementById('modalError');
  const succ = document.getElementById('modalSuccess');
  if (err) err.style.display = 'none';
  if (succ) succ.style.display = 'none';
}

function switchAuthTab(tab) {
  ensureAuthModalDOM();
  const tabsContainer = document.getElementById('authModalTabs');
  const formLogin = document.getElementById('formLogin');
  const formRegister = document.getElementById('formRegister');
  const formForgot = document.getElementById('formForgotPassword');
  const formReset = document.getElementById('formResetPassword');
  const err = document.getElementById('modalError');
  const success = document.getElementById('modalSuccess');

  if (err) err.style.display = 'none';
  if (success) success.style.display = 'none';

  if (tabsContainer) {
    tabsContainer.style.display = (tab === 'login' || tab === 'register') ? 'flex' : 'none';
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

async function submitLogin() {
  const valInput = document.getElementById('loginUserEmail');
  const pwdInput = document.getElementById('loginPassword');
  if (!valInput || !pwdInput) return;

  const val = valInput.value.trim();
  const pwd = pwdInput.value;
  const err = document.getElementById('modalError');
  if (err) err.style.display = 'none';

  if (!val || !pwd) {
    if (err) {
      err.textContent = 'Please enter username/email and password.';
      err.style.display = 'block';
    }
    return;
  }

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username_or_email: val, password: pwd })
    });
    const data = await res.json();
    if (res.ok) {
      closeAuthModal();
      invalidateAuthState();
      await checkAuthStatus();
      if (typeof window.onAuthSuccess === 'function') {
        window.onAuthSuccess('login', data);
      }
    } else {
      if (err) {
        err.textContent = data.detail || 'Login failed.';
        err.style.display = 'block';
      }
    }
  } catch (e) {
    if (err) {
      err.textContent = 'Network error. Please try again.';
      err.style.display = 'block';
    }
  }
}

async function submitRegister() {
  const userEl = document.getElementById('regUsername');
  const emailEl = document.getElementById('regEmail');
  const passEl = document.getElementById('regPassword');
  if (!userEl || !emailEl || !passEl) return;

  const username = userEl.value.trim();
  const email = emailEl.value.trim();
  const password = passEl.value;
  const err = document.getElementById('modalError');
  if (err) err.style.display = 'none';

  if (!username || !email || !password) {
    if (err) {
      err.textContent = 'Please fill out all fields.';
      err.style.display = 'block';
    }
    return;
  }

  try {
    const res = await fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, email, password })
    });
    const data = await res.json();
    if (res.ok) {
      closeAuthModal();
      invalidateAuthState();
      await checkAuthStatus();
      if (typeof window.onAuthSuccess === 'function') {
        window.onAuthSuccess('register', data);
      }
    } else {
      if (err) {
        err.textContent = data.detail || 'Registration failed.';
        err.style.display = 'block';
      }
    }
  } catch (e) {
    if (err) {
      err.textContent = 'Network error. Please try again.';
      err.style.display = 'block';
    }
  }
}

async function submitForgotPassword() {
  const inputEl = document.getElementById('forgotUserEmail');
  if (!inputEl) return;

  const input = inputEl.value.trim();
  const err = document.getElementById('modalError');
  const success = document.getElementById('modalSuccess');
  if (err) err.style.display = 'none';
  if (success) success.style.display = 'none';

  if (!input) {
    if (err) {
      err.textContent = 'Please enter your username or email address.';
      err.style.display = 'block';
    }
    return;
  }

  try {
    const res = await fetch('/api/auth/forgot-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username_or_email: input })
    });
    const data = await res.json();
    if (res.ok) {
      let msg = data.message || 'Password reset link sent!';
      if (data.reset_link) {
        msg += `<br><br><strong>Dev Mode Reset Link:</strong><br><a href="${data.reset_link}" style="color: #c084fc; word-break: break-all;">${data.reset_link}</a>`;
      }
      if (success) {
        success.innerHTML = msg;
        success.style.display = 'block';
      }
    } else {
      if (err) {
        err.textContent = data.detail || 'Failed to request password reset.';
        err.style.display = 'block';
      }
    }
  } catch (e) {
    if (err) {
      err.textContent = 'Network error. Please try again.';
      err.style.display = 'block';
    }
  }
}

async function submitResetPassword() {
  const token = document.getElementById('resetTokenInput')?.value;
  const newPassword = document.getElementById('resetNewPassword')?.value;
  const confirmPassword = document.getElementById('resetConfirmPassword')?.value;
  const err = document.getElementById('modalError');
  const success = document.getElementById('modalSuccess');
  if (err) err.style.display = 'none';
  if (success) success.style.display = 'none';

  if (!newPassword || !confirmPassword) {
    if (err) {
      err.textContent = 'Please enter and confirm your new password.';
      err.style.display = 'block';
    }
    return;
  }

  if (newPassword !== confirmPassword) {
    if (err) {
      err.textContent = 'Passwords do not match.';
      err.style.display = 'block';
    }
    return;
  }

  try {
    const res = await fetch('/api/auth/reset-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: token, new_password: newPassword })
    });
    const data = await res.json();
    if (res.ok) {
      invalidateAuthState();
      switchAuthTab('login');
      const loginSuccess = document.getElementById('modalSuccess');
      if (loginSuccess) {
        loginSuccess.textContent = data.message || 'Password updated successfully! Please log in.';
        loginSuccess.style.display = 'block';
      }
    } else {
      if (err) {
        err.textContent = data.detail || 'Failed to reset password.';
        err.style.display = 'block';
      }
    }
  } catch (e) {
    if (err) {
      err.textContent = 'Network error. Please try again.';
      err.style.display = 'block';
    }
  }
}

async function checkResetTokenInURL() {
  const urlParams = new URLSearchParams(window.location.search);
  const resetToken = urlParams.get('reset_token');
  if (resetToken) {
    try {
      const res = await fetch(`/api/auth/reset-password/validate?token=${encodeURIComponent(resetToken)}`);
      const data = await res.json();
      if (data.valid) {
        ensureAuthModalDOM();
        document.getElementById('resetTokenInput').value = resetToken;
        const sub = document.getElementById('resetPasswordSubtext');
        if (sub) sub.textContent = `Set a new password for ${data.username}.`;
        openAuthModal('reset');
      } else {
        openAuthModal('forgot');
        const err = document.getElementById('modalError');
        if (err) {
          err.textContent = data.detail || 'The reset link is invalid or expired. Please request a new link.';
          err.style.display = 'block';
        }
      }
    } catch (e) {
      console.error('Error validating reset token:', e);
    }
  }
}

async function submitLogout() {
  await fetch('/api/auth/logout', { method: 'POST' });
  invalidateAuthState();
  await checkAuthStatus();
  if (typeof window.onAuthSuccess === 'function') {
    window.onAuthSuccess('logout');
  }
}

function initAuthFlow() {
  ensureAuthModalDOM();
  checkAuthStatus();
  checkResetTokenInURL();

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeAuthModal();
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAuthFlow);
} else {
  initAuthFlow();
}
