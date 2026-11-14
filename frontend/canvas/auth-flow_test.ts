import { CANVAS_ORIGIN, expect, test } from '../../testing/ui/canvas_fixtures';

type AuthFlowModule = typeof import('/static/generated/canvas/auth-flow.js');

interface AuthHarnessWindow extends Window {
  registrationRequests?: Array<Record<string, unknown>>;
  authCallbacks?: Array<{ action?: string; data?: unknown }>;
  authEvents?: Array<{ authenticated: boolean; username?: string }>;
  buyCreditsCalls?: number;
}

const AUTH_FLOW_MODULE_URL = '/static/generated/canvas/auth-flow.js';

const RESET_TABS = [
  {
    tab: 'forgot',
    submitMethod: 'submitForgotPassword',
    fallback: 'Password reset request failed. Please try again later.',
  },
  {
    tab: 'reset',
    submitMethod: 'submitResetPassword',
    fallback: 'Password reset failed. Please try again later.',
  },
] as const;

const RESET_FAILURES = ['json', 'text', 'html', 'network'] as const;

for (const { tab, submitMethod, fallback } of RESET_TABS) {
  for (const failure of RESET_FAILURES) {
    test(`password reset (${tab}) displays server and network errors (${failure})`, async ({
      canvas,
      page,
    }) => {
      await canvas.serveRoute(
        (url: URL) =>
          url.origin === CANVAS_ORIGIN &&
          (url.pathname === '/api/auth/me' ||
            url.pathname === '/api/auth/forgot-password' ||
            url.pathname === '/api/auth/reset-password'),
        async (route) => {
          const url = new URL(route.request().url());
          if (url.pathname === '/api/auth/me') {
            await route.fulfill({ json: { authenticated: false } });
            return;
          }
          if (failure === 'network') {
            await route.abort('failed');
            return;
          }
          if (failure === 'json') {
            await route.fulfill({
              status: 503,
              contentType: 'application/json',
              body: JSON.stringify({
                detail: 'Password reset email could not be sent. Please try again later.',
              }),
            });
            return;
          }
          await route.fulfill({
            status: 500,
            contentType: failure === 'html' ? 'text/html' : 'text/plain',
            body: failure === 'html' ? '<html>Internal Server Error</html>' : 'Internal Server Error',
          });
        },
      );

      await canvas.mount({
        stylesheets: ['/static/css/auth-flow.css'],
        moduleScripts: [`import ${JSON.stringify(AUTH_FLOW_MODULE_URL)};`],
      });

      await page.evaluate((targetTab: string) => {
        window.openAuthModal?.(targetTab);
      }, tab);

      if (tab === 'forgot') {
        await page.locator('#forgotUserEmail').fill('ada@example.test');
      } else {
        await page.locator('#resetTokenInput').evaluate((input) => {
          if (input instanceof HTMLInputElement) {
            input.value = 'reset-token';
          }
        });
        await page.locator('#resetNewPassword').fill('NewPassword123');
        await page.locator('#resetConfirmPassword').fill('NewPassword123');
      }

      await page.evaluate(async (methodName: string) => {
        if (methodName === 'submitForgotPassword') {
          await window.submitForgotPassword?.();
        } else {
          await window.submitResetPassword?.();
        }
      }, submitMethod);

      await expect(page.locator('#modalError')).toBeVisible();
      const expected =
        failure === 'json'
          ? 'Password reset email could not be sent. Please try again later.'
          : failure === 'network'
            ? 'Network error. Please try again.'
            : fallback;
      await expect(page.locator('#modalError')).toHaveText(expected);
      await expect(page.locator('#modalSuccess')).not.toBeVisible();
    });
  }
}

test('signup requires age attestation for both click and Enter submission', async ({
  canvas,
  page,
}) => {
  const registrationRequests: Array<Record<string, unknown>> = [];

  await canvas.serveRoute(
    (url: URL) =>
      url.origin === CANVAS_ORIGIN &&
      (url.pathname === '/api/auth/me' || url.pathname === '/api/auth/register'),
    async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/auth/register') {
        const payload = JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>;
        registrationRequests.push(payload);
        await route.fulfill({ json: { status: 'ok' } });
        return;
      }
      await route.fulfill({ json: { authenticated: false } });
    },
  );

  await canvas.mount({
    viewport: { width: 480, height: 800 },
    stylesheets: ['/static/css/auth-flow.css'],
    moduleScripts: [`import ${JSON.stringify(AUTH_FLOW_MODULE_URL)};`],
  });

  await page.evaluate(() => {
    window.openAuthModal?.('register');
  });

  await page.locator('#regUsername').fill('ada');
  await page.locator('#regEmail').fill('ada@example.test');
  await page.locator('#regPassword').fill('secret');

  const checkbox = page.getByRole('checkbox', {
    name: 'I confirm that I am at least 13 years old.',
  });
  await expect(checkbox).not.toBeChecked();

  await page.getByRole('button', { name: 'Sign Up', exact: true }).click();
  await expect(page.locator('#modalError')).toHaveText(
    'Please confirm that you are at least 13 years old.',
  );
  await expect(checkbox).toBeFocused();

  await page.locator('#regPassword').press('Enter');
  expect(registrationRequests).toEqual([]);

  await checkbox.check();
  await page.getByRole('button', { name: 'Sign Up', exact: true }).click();

  await expect(page.locator('#authModal')).not.toHaveClass(/active/);
  expect(registrationRequests).toEqual([
    {
      username: 'ada',
      email: 'ada@example.test',
      password: 'secret',
      age_attested: true,
    },
  ]);

  await page.evaluate(() => {
    window.openAuthModal?.('register');
  });
  await expect(checkbox).not.toBeChecked();
});

test('signup enforces username and password size limits', async ({ canvas, page }) => {
  const registrationRequests: Array<Record<string, unknown>> = [];

  await canvas.serveRoute(
    (url: URL) =>
      url.origin === CANVAS_ORIGIN &&
      (url.pathname === '/api/auth/me' || url.pathname === '/api/auth/register'),
    async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/auth/register') {
        const payload = JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>;
        registrationRequests.push(payload);
        await route.fulfill({ json: { status: 'ok' } });
        return;
      }
      await route.fulfill({ json: { authenticated: false } });
    },
  );

  await canvas.mount({
    viewport: { width: 480, height: 800 },
    stylesheets: ['/static/css/auth-flow.css'],
    moduleScripts: [`import ${JSON.stringify(AUTH_FLOW_MODULE_URL)};`],
  });

  await page.evaluate(() => {
    window.openAuthModal?.('register');
  });

  // Username too short (< 3 chars)
  await page.locator('#regUsername').fill('ab');
  await page.locator('#regEmail').fill('test@example.com');
  await page.locator('#regPassword').fill('validpassword');
  await page.locator('#regAgeAttested').check();
  await page.getByRole('button', { name: 'Sign Up', exact: true }).click();
  await expect(page.locator('#modalError')).toHaveText(
    'Username must be between 3 and 50 characters.',
  );
  expect(registrationRequests).toEqual([]);

  // Password too short (< 6 chars)
  await page.locator('#regUsername').fill('validuser');
  await page.locator('#regPassword').fill('12345');
  await page.getByRole('button', { name: 'Sign Up', exact: true }).click();
  await expect(page.locator('#modalError')).toHaveText(
    'Password must be between 6 and 128 characters.',
  );
  expect(registrationRequests).toEqual([]);

  // Valid registration submits successfully
  await page.locator('#regPassword').fill('validpass');
  await page.getByRole('button', { name: 'Sign Up', exact: true }).click();
  await expect(page.locator('#authModal')).not.toHaveClass(/active/);
  expect(registrationRequests).toEqual([
    {
      username: 'validuser',
      email: 'test@example.com',
      password: 'validpass',
      age_attested: true,
    },
  ]);
});

test('auth-flow login/logout bar updates, auth events, and URL reset token handling', async ({
  canvas,
  page,
}) => {
  let authenticated = false;

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname.startsWith('/api/auth/'),
    async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/auth/me') {
        await route.fulfill({
          json: authenticated
            ? { authenticated: true, user: { id: 7, username: 'Ada', credits: 12.5 } }
            : { authenticated: false },
        });
        return;
      }
      if (url.pathname === '/api/auth/login') {
        authenticated = true;
        await route.fulfill({ json: { user: { id: 7, username: 'Ada', credits: 12.5 } } });
        return;
      }
      if (url.pathname === '/api/auth/logout') {
        authenticated = false;
        await route.fulfill({ json: { status: 'ok' } });
        return;
      }
      if (url.pathname === '/api/auth/reset-password/validate') {
        const token = url.searchParams.get('token');
        if (token === 'valid-tok') {
          await route.fulfill({ json: { valid: true, username: 'Ada' } });
        } else {
          await route.fulfill({ json: { valid: false, detail: 'Reset token expired.' } });
        }
        return;
      }
      await route.fulfill({ status: 404, json: {} });
    },
  );

  await canvas.mount({
    stylesheets: ['/static/css/auth-flow.css'],
    html: '<div id="userAccountBar"></div>',
    moduleScripts: [
      `
      window.authCallbacks = [];
      window.authEvents = [];
      window.buyCreditsCalls = 0;
      window.openBuyCreditsModal = () => { window.buyCreditsCalls += 1; };
      window.onAuthSuccess = (action, data) => { window.authCallbacks.push({ action, data }); };
      window.addEventListener('narratron:auth-changed', (event) => {
        window.authEvents.push({
          authenticated: Boolean(event.detail?.authenticated),
          username: event.detail?.user?.username,
        });
      });
      import ${JSON.stringify(AUTH_FLOW_MODULE_URL)};
      `,
    ],
  });

  await expect(page.locator('#userAccountBar .auth-nav-btn')).toHaveCount(2);
  await page.locator('#userAccountBar .auth-nav-btn', { hasText: 'Log In' }).click();
  await expect(page.locator('#authModal')).toHaveClass(/active/);

  await page.locator('#loginUserEmail').fill('Ada');
  await page.locator('#loginPassword').fill('password123');
  await page.locator('#loginPassword').press('Enter');

  await expect(page.locator('#authModal')).not.toHaveClass(/active/);
  await expect(page.locator('#userAccountBar .user-pill')).toHaveText('👤 Ada');
  await expect(page.locator('#userAccountBar .credit-badge')).toContainText('⚡ 12.5');
  await page.locator('#userAccountBar .credit-badge').click();
  expect(
    await page.evaluate(() => (window as unknown as AuthHarnessWindow).buyCreditsCalls),
  ).toBe(1);

  await page.locator('#userAccountBar .user-menu').hover();
  await page.locator('#userAccountBar .user-menu-item').click();
  await expect(page.locator('#userAccountBar .auth-nav-btn')).toHaveCount(2);

  const summary = await page.evaluate(() => {
    const win = window as unknown as AuthHarnessWindow;
    return {
      callbacks: win.authCallbacks?.map((entry) => entry.action),
      events: win.authEvents,
    };
  });
  expect(summary.callbacks).toEqual(['login', 'logout']);
  expect(summary.events).toEqual([
    { authenticated: false },
    { authenticated: true, username: 'Ada' },
    { authenticated: false },
  ]);

  // Valid reset_token query opens Reset Password tab with user prompt.
  await canvas.mount({
    query: '?reset_token=valid-tok',
    stylesheets: ['/static/css/auth-flow.css'],
    moduleScripts: [`import ${JSON.stringify(AUTH_FLOW_MODULE_URL)};`],
  });
  await expect(page.locator('#authModal')).toHaveClass(/active/);
  await expect(page.locator('#formResetPassword')).toBeVisible();
  await expect(page.locator('#resetPasswordSubtext')).toHaveText('Set a new password for Ada.');

  // Invalid reset_token query opens Forgot Password tab with error alert.
  await canvas.mount({
    query: '?reset_token=bad-tok',
    stylesheets: ['/static/css/auth-flow.css'],
    moduleScripts: [`import ${JSON.stringify(AUTH_FLOW_MODULE_URL)};`],
  });
  await expect(page.locator('#authModal')).toHaveClass(/active/);
  await expect(page.locator('#formForgotPassword')).toBeVisible();
  await expect(page.locator('#modalError')).toHaveText('Reset token expired.');
});

test('remote-login opens login modal for unauthenticated visitor', async ({
  canvas,
  page,
}) => {
  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/auth/me',
    async (route) => {
      await route.fulfill({ json: { authenticated: false } });
    },
  );

  await canvas.mount({
    stylesheets: ['/static/css/auth-flow.css'],
    html: '<button id="remote-sign-in" type="button">Sign in</button>',
    moduleScripts: [`import '/static/generated/canvas/remote-login.js';`],
  });

  await expect(page.locator('#authModal')).toHaveClass(/active/);
  await expect(page.locator('#formLogin')).toBeVisible();
});
