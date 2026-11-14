import fs from 'node:fs';
import path from 'node:path';
import { expect, REPO_ROOT, STATIC_DIR, TEMPLATES_DIR, test } from '../../testing/ui/canvas_fixtures';

interface CanvasAuthUser {
  readonly id?: number;
  readonly username?: string;
  readonly profile_color?: string;
}

interface CanvasAuthStateResponse {
  readonly authenticated: boolean;
  readonly user?: CanvasAuthUser | null;
  readonly testing_use_local?: boolean;
  readonly server_run_id?: string;
}

interface ChatController {
  initChatUser(): Promise<void>;
  updateChatName(): void;
  setChatUsername(newName: string): void;
  getCurrentUsername(): string;
}

interface ChatControllerOptions {
  readonly messagesContainer?: HTMLElement | null;
  readonly nameInput?: HTMLInputElement | null;
  readonly nameDisplay?: HTMLElement | null;
  readonly nameStatusIcon?: HTMLElement | null;
  readonly loginChip?: HTMLElement | null;
  readonly onOpenLogin?: (() => void) | null;
  readonly getAuthState?: () => Promise<CanvasAuthStateResponse>;
  readonly popoutBroadcast?: { postMessage(message: { type: string; name: string }): void } | null;
}

interface ChatControllerModule {
  initializeChatController(options?: ChatControllerOptions): ChatController;
}

interface ChatHarnessWindow extends Window {
  controller: ChatController;
  loginTriggeredWith: string | null;
  onOpenLoginCount: number;
  postedMessages: Array<{ type: string; name: string }>;
  openAuthModal?: (tab?: string) => void;
  onAuthSuccess?: (action?: string, data?: unknown) => void;
  resolveDelayedAuth?: (state: CanvasAuthStateResponse) => void;
}

const CHAT_CONTROLLER_MODULE_URL = '/static/generated/shared/chat-controller.js';

function loadChatIdentityHtml(): string {
  const template = fs.readFileSync(path.join(TEMPLATES_DIR, 'canvas.html'), 'utf-8');
  const start = template.indexOf('<div id="chat-identity"');
  const end = template.indexOf('            <div class="panel-actions">', start);
  if (start < 0 || end <= start) {
    throw new Error('Could not locate #chat-identity markup in templates/canvas.html');
  }
  return `<div id="chat-container">${template.slice(start, end)}</div><div id="chat-messages"></div>`;
}

const CHAT_IDENTITY_SHELL = loadChatIdentityHtml();

test('canvas template and controller wire chat identity display, login chip, and auth modal assets', async ({
  canvas,
  page,
}) => {
  const template = fs.readFileSync(path.join(TEMPLATES_DIR, 'canvas.html'), 'utf-8');
  const canvasScript = fs.readFileSync(
    path.join(REPO_ROOT, 'frontend', 'canvas', 'canvas.ts'),
    'utf-8',
  );

  expect(template).toContain('id="chat-name-display"');
  expect(template).toContain('id="chat-name-input"');
  expect(template).toContain('id="chat-login-chip"');
  expect(template).toContain('id="chat-name-status-icon"');
  expect(template).toContain('auth-flow.js');
  expect(template).toContain('auth-flow.css');
  expect(canvasScript).toContain("nameDisplay: document.getElementById('chat-name-display')");
  expect(canvasScript).toContain("loginChip: document.getElementById('chat-login-chip')");
  expect(canvasScript).toContain('window.onAuthSuccess');

  await canvas.openCanvas({
    viewport: { width: 1400, height: 900 },
    user: { id: 1, username: 'orator' },
    apiRoutes: {
      '/api/chat': [],
      '/api/suggestions': [],
    },
  });

  await expect(page.locator('#chat-name-display')).toBeVisible();
  await expect(page.locator('#chat-name-display')).toHaveText('orator');
  await expect(page.locator('#chat-name-input')).toBeHidden();
  await expect(page.locator('#chat-login-chip')).toBeHidden();
  await expect(page.locator('#chat-name-status-icon')).toHaveAttribute('aria-label', 'Member');
  await expect(page.locator('#stamp-manager-profile-link')).toBeVisible();
  await expect(page.locator('#stamp-manager-profile-link')).toHaveAttribute('href', '/users/orator');
  expect(
    await page.evaluate(() => {
      const win = window as unknown as ChatHarnessWindow;
      return typeof win.onAuthSuccess === 'function' && typeof win.openAuthModal === 'function';
    }),
  ).toBe(true);
});

const PROFILE_COLOR_CASES: ReadonlyArray<{
  label: string;
  profileColor: string | undefined;
  expectedRgb: string;
}> = [
  { label: 'pink hex', profileColor: '#ec4899', expectedRgb: 'rgb(236, 72, 153)' },
  { label: 'cyan hex', profileColor: '#22d3ee', expectedRgb: 'rgb(34, 211, 238)' },
  { label: 'omitted color falls back to indigo', profileColor: undefined, expectedRgb: 'rgb(129, 140, 248)' },
  { label: 'empty color falls back to indigo', profileColor: '', expectedRgb: 'rgb(129, 140, 248)' },
];

for (const { label, profileColor, expectedRgb } of PROFILE_COLOR_CASES) {
  test(`authenticated user displays read-only chat name and member badge (${label})`, async ({
    canvas,
    page,
  }) => {
    await canvas.serveRoute(
      (url) => url.origin === canvas.origin && url.pathname.startsWith('/api/'),
      async (route) => {
        await route.fulfill({ json: [] });
      },
    );

    await canvas.mount({
      viewport: { width: 400, height: 700 },
      stylesheets: ['/static/css/chat.css'],
      localStorage: { narratron_anon_chat_name: 'Previous Guest' },
      html: CHAT_IDENTITY_SHELL,
    });

    await page.evaluate(
      async ({ moduleUrl, color }) => {
        const { initializeChatController } =
          await window.__importModule<ChatControllerModule>(moduleUrl);
        const win = window as unknown as ChatHarnessWindow;
        win.controller = initializeChatController({
          messagesContainer: document.getElementById('chat-messages'),
          nameInput: document.getElementById('chat-name-input') as HTMLInputElement | null,
          nameDisplay: document.getElementById('chat-name-display'),
          nameStatusIcon: document.getElementById('chat-name-status-icon'),
          loginChip: document.getElementById('chat-login-chip'),
          getAuthState: async () => ({
            authenticated: true,
            user: {
              username: 'Gandalf',
              ...(color !== undefined ? { profile_color: color } : {}),
            },
          }),
        });
      },
      { moduleUrl: CHAT_CONTROLLER_MODULE_URL, color: profileColor },
    );

    const nameInput = page.locator('#chat-name-input');
    const nameDisplay = page.locator('#chat-name-display');
    const nameBadge = page.locator('#chat-name-status-icon');
    const loginChip = page.locator('#chat-login-chip');

    await expect(nameInput).toBeHidden();
    await expect(nameDisplay).toBeVisible();
    await expect(nameDisplay).toHaveText('Gandalf');
    await expect(nameBadge).toHaveAttribute('aria-label', 'Member');
    await expect(nameBadge).toHaveAttribute('title', 'Member');
    await expect(nameBadge).toHaveText('✓');
    await expect(loginChip).toBeHidden();

    const color = await nameDisplay.evaluate((el) => getComputedStyle(el).color);
    expect(color).toBe(expectedRgb);
    expect(
      await page.evaluate(() => (window as unknown as ChatHarnessWindow).controller.getCurrentUsername()),
    ).toBe('Gandalf');
    expect(await page.evaluate(() => localStorage.getItem('narratron_anon_chat_name'))).toBe(
      'Previous Guest',
    );
  });
}

test('full canvas renders authenticated profile color and profile link', async ({ canvas, page }) => {
  await canvas.openCanvas({
    viewport: { width: 1400, height: 900 },
    apiRoutes: {
      '/api/auth/me': {
        authenticated: true,
        user: { id: 1, username: 'Gandalf', profile_color: '#ec4899' },
      },
      '/api/chat': [],
      '/api/suggestions': [],
    },
  });

  const nameDisplay = page.locator('#chat-name-display');
  await expect(nameDisplay).toBeVisible();
  await expect(nameDisplay).toHaveText('Gandalf');
  expect(await nameDisplay.evaluate((el) => getComputedStyle(el).color)).toBe('rgb(236, 72, 153)');
  await expect(page.locator('#chat-name-input')).toBeHidden();
  await expect(page.locator('#chat-login-chip')).toBeHidden();
  await expect(page.locator('#chat-name-status-icon')).toHaveAttribute('aria-label', 'Member');
  await expect(page.locator('#stamp-manager-profile-link')).toBeVisible();
  await expect(page.locator('#stamp-manager-profile-link')).toHaveAttribute('href', '/users/Gandalf');
});

test('unauthenticated guest displays editable name input, persists edits, and restores saved name', async ({
  canvas,
  page,
}) => {
  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname.startsWith('/api/'),
    async (route) => {
      await route.fulfill({ json: [] });
    },
  );

  await canvas.mount({
    viewport: { width: 400, height: 700 },
    stylesheets: ['/static/css/chat.css'],
    html: CHAT_IDENTITY_SHELL,
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { initializeChatController } =
      await window.__importModule<ChatControllerModule>(moduleUrl);
    const win = window as unknown as ChatHarnessWindow;
    win.postedMessages = [];
    win.controller = initializeChatController({
      messagesContainer: document.getElementById('chat-messages'),
      nameInput: document.getElementById('chat-name-input') as HTMLInputElement | null,
      nameDisplay: document.getElementById('chat-name-display'),
      nameStatusIcon: document.getElementById('chat-name-status-icon'),
      loginChip: document.getElementById('chat-login-chip'),
      popoutBroadcast: {
        postMessage(message) {
          win.postedMessages.push(message);
        },
      },
      getAuthState: async () => ({ authenticated: false }),
    });
  }, CHAT_CONTROLLER_MODULE_URL);

  const nameInput = page.locator('#chat-name-input');
  const nameDisplay = page.locator('#chat-name-display');
  const nameBadge = page.locator('#chat-name-status-icon');
  const loginChip = page.locator('#chat-login-chip');

  await expect(nameInput).toBeVisible();
  await expect(nameDisplay).toBeHidden();
  await expect(nameBadge).toHaveAttribute('aria-label', 'Guest');
  await expect(loginChip).toBeVisible();
  await expect(loginChip).toHaveText('Log In');

  const generatedName = await nameInput.inputValue();
  expect(generatedName).toMatch(/^[A-Za-z]+ [A-Za-z]+ \d{2}$/);
  expect(await page.evaluate(() => localStorage.getItem('narratron_anon_chat_name'))).toBe(
    generatedName,
  );

  await nameInput.fill('Moonlit Guide');
  await nameInput.press('Tab');
  expect(await page.evaluate(() => localStorage.getItem('narratron_anon_chat_name'))).toBe(
    'Moonlit Guide',
  );
  expect(
    await page.evaluate(() => (window as unknown as ChatHarnessWindow).controller.getCurrentUsername()),
  ).toBe('Moonlit Guide');
  const posted = await page.evaluate(() => (window as unknown as ChatHarnessWindow).postedMessages);
  expect(posted.length).toBeGreaterThanOrEqual(1);
  expect(posted.every((msg) => msg.type === 'name_updated' && msg.name === 'Moonlit Guide')).toBe(
    true,
  );

  // Whitespace-only input restores the previous name without clearing persistence.
  await nameInput.fill('   ');
  await nameInput.press('Tab');
  await expect(nameInput).toHaveValue('Moonlit Guide');
  expect(await page.evaluate(() => localStorage.getItem('narratron_anon_chat_name'))).toBe(
    'Moonlit Guide',
  );

  // Leading/trailing whitespace is trimmed on commit.
  await nameInput.fill('  Astral Bard  ');
  await nameInput.press('Tab');
  expect(await page.evaluate(() => localStorage.getItem('narratron_anon_chat_name'))).toBe(
    'Astral Bard',
  );
  expect(
    await page.evaluate(() => (window as unknown as ChatHarnessWindow).controller.getCurrentUsername()),
  ).toBe('Astral Bard');

  // setChatUsername updates display & input while ignoring blank values.
  await page.evaluate(() => {
    const ctrl = (window as unknown as ChatHarnessWindow).controller;
    ctrl.setChatUsername('   ');
    ctrl.setChatUsername('  Starlight Scout  ');
  });
  await expect(nameInput).toHaveValue('Starlight Scout');
  await expect(nameDisplay).toHaveText('Starlight Scout');
  expect(
    await page.evaluate(() => (window as unknown as ChatHarnessWindow).controller.getCurrentUsername()),
  ).toBe('Starlight Scout');

  // Re-opening canvas restores the persisted guest name from localStorage ('Astral Bard').
  await canvas.openCanvas({
    viewport: { width: 1400, height: 900 },
    isOrator: false,
    apiRoutes: {
      '/api/auth/me': { authenticated: false },
      '/api/chat': [],
      '/api/suggestions': [],
    },
  });

  await expect(page.locator('#chat-name-input')).toBeVisible();
  await expect(page.locator('#chat-name-input')).toHaveValue('Astral Bard');
  await expect(page.locator('#chat-name-display')).toBeHidden();
  await expect(page.locator('#chat-name-status-icon')).toHaveAttribute('aria-label', 'Guest');
  await expect(page.locator('#chat-login-chip')).toBeVisible();
  await expect(page.locator('#stamp-manager-profile-link')).toBeHidden();
});

test('unauthenticated login chip triggers login modal via callback and in real canvas', async ({
  canvas,
  page,
}) => {
  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname.startsWith('/api/'),
    async (route) => {
      await route.fulfill({ json: [] });
    },
  );

  await canvas.mount({
    viewport: { width: 400, height: 700 },
    stylesheets: ['/static/css/chat.css'],
    html: CHAT_IDENTITY_SHELL,
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { initializeChatController } =
      await window.__importModule<ChatControllerModule>(moduleUrl);
    const win = window as unknown as ChatHarnessWindow;
    win.loginTriggeredWith = null;
    win.openAuthModal = (tab?: string) => {
      win.loginTriggeredWith = tab ?? null;
    };
    win.controller = initializeChatController({
      messagesContainer: document.getElementById('chat-messages'),
      nameInput: document.getElementById('chat-name-input') as HTMLInputElement | null,
      nameDisplay: document.getElementById('chat-name-display'),
      nameStatusIcon: document.getElementById('chat-name-status-icon'),
      loginChip: document.getElementById('chat-login-chip'),
      getAuthState: async () => ({ authenticated: false }),
    });
  }, CHAT_CONTROLLER_MODULE_URL);

  const loginChip = page.locator('#chat-login-chip');
  await expect(loginChip).toBeVisible();
  await loginChip.click();
  expect(
    await page.evaluate(() => (window as unknown as ChatHarnessWindow).loginTriggeredWith),
  ).toBe('login');

  // When onOpenLogin is supplied, it takes precedence over window.openAuthModal.
  await canvas.mount({
    viewport: { width: 400, height: 700 },
    stylesheets: ['/static/css/chat.css'],
    html: CHAT_IDENTITY_SHELL,
  });
  await page.evaluate(async (moduleUrl: string) => {
    const { initializeChatController } =
      await window.__importModule<ChatControllerModule>(moduleUrl);
    const win = window as unknown as ChatHarnessWindow;
    win.loginTriggeredWith = null;
    win.onOpenLoginCount = 0;
    win.openAuthModal = (tab?: string) => {
      win.loginTriggeredWith = tab ?? null;
    };
    win.controller = initializeChatController({
      messagesContainer: document.getElementById('chat-messages'),
      nameInput: document.getElementById('chat-name-input') as HTMLInputElement | null,
      nameDisplay: document.getElementById('chat-name-display'),
      nameStatusIcon: document.getElementById('chat-name-status-icon'),
      loginChip: document.getElementById('chat-login-chip'),
      onOpenLogin: () => {
        win.onOpenLoginCount += 1;
      },
      getAuthState: async () => ({ authenticated: false }),
    });
  }, CHAT_CONTROLLER_MODULE_URL);
  await expect(page.locator('#chat-login-chip')).toBeVisible();
  await page.locator('#chat-login-chip').click();
  expect(await page.evaluate(() => (window as unknown as ChatHarnessWindow).onOpenLoginCount)).toBe(
    1,
  );
  expect(
    await page.evaluate(() => (window as unknown as ChatHarnessWindow).loginTriggeredWith),
  ).toBeNull();

  // Verify real canvas integration opens #authModal on the login tab.
  await canvas.openCanvas({
    viewport: { width: 1400, height: 900 },
    isOrator: false,
    apiRoutes: {
      '/api/auth/me': { authenticated: false },
      '/api/chat': [],
      '/api/suggestions': [],
    },
  });

  await expect(page.locator('#chat-login-chip')).toBeVisible();
  await page.locator('#chat-login-chip').click();
  await expect(page.locator('#authModal')).toHaveClass(/active/);
  await expect(page.locator('#formLogin')).toBeVisible();
  await expect(page.locator('#tabLogin')).toHaveClass(/active/);
});

test('stamp manager requires authentication and opens login modal for guests', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({
    viewport: { width: 1400, height: 900 },
    isOrator: false,
    apiRoutes: {
      '/api/auth/me': { authenticated: false },
      '/api/chat': [],
      '/api/suggestions': [],
    },
  });

  await expect(page.locator('#panel-body')).toBeVisible();
  await expect(page.locator('#stamp-manager-pane')).toBeHidden();

  await page.locator('#chat-open-stamps-btn').click();

  await expect(page.locator('#panel-body')).toBeVisible();
  await expect(page.locator('#stamp-manager-pane')).toBeHidden();
  await expect(page.locator('#authModal')).toHaveClass(/active/);
  await expect(page.locator('#formLogin')).toBeVisible();

  // Authenticated users can open the stamp manager pane directly.
  await canvas.openCanvas({
    viewport: { width: 1400, height: 900 },
    apiRoutes: {
      '/api/auth/me': {
        authenticated: true,
        user: { id: 1, username: 'Gandalf', profile_color: '#ec4899' },
      },
      '/api/users/me/stamps': { stamps: [] },
      '/api/chat': [],
      '/api/suggestions': [],
    },
  });

  await page.locator('#chat-open-stamps-btn').click();
  await expect(page.locator('#stamp-manager-pane')).toBeVisible();
  await expect(page.locator('#panel-body')).toBeHidden();
  await expect(page.locator('#authModal')).not.toHaveClass(/active/);
});

test('canvas deduplicates auth requests, retries after failure, and persists testing auto-login', async ({
  canvas,
  page,
}) => {
  // 1. Multiple canvas consumers share one cached /api/auth/me request (plus 1 from auth-flow.js).
  let authCount = 0;
  await canvas.openCanvas({
    viewport: { width: 1400, height: 900 },
    isOrator: false,
    apiRoutes: {
      '/api/auth/me': async (route) => {
        authCount += 1;
        await route.fulfill({ json: { authenticated: false } });
      },
      '/api/chat': [],
      '/api/suggestions': [],
    },
  });

  await expect(page.locator('#chat-name-input')).toBeVisible();
  const countAfterLoad = authCount;
  expect(countAfterLoad).toBeLessThanOrEqual(2);

  // Opening stamp manager reuses the cached canvas auth state without re-fetching /api/auth/me.
  await page.locator('#chat-open-stamps-btn').click();
  await expect(page.locator('#authModal')).toHaveClass(/active/);
  expect(authCount).toBe(countAfterLoad);

  // 2. Transient /api/auth/me failure does not stick; clicking stamp manager retries and opens pane.
  let failAuth = true;
  let retryAuthCount = 0;
  await canvas.openCanvas({
    viewport: { width: 1400, height: 900 },
    isOrator: false,
    apiRoutes: {
      '/api/auth/me': async (route) => {
        retryAuthCount += 1;
        if (failAuth) {
          await route.fulfill({ status: 500, contentType: 'text/plain', body: 'boom' });
          return;
        }
        await route.fulfill({
          json: { authenticated: true, user: { id: 1, username: 'Gandalf', profile_color: '#ec4899' } },
        });
      },
      '/api/users/me/stamps': { stamps: [] },
      '/api/chat': [],
      '/api/suggestions': [],
    },
  });

  await expect(page.locator('#chat-name-input')).toBeVisible();
  failAuth = false;
  const beforeRetryCount = retryAuthCount;
  await page.locator('#chat-open-stamps-btn').click();
  await expect(page.locator('#stamp-manager-pane')).toBeVisible();
  await expect(page.locator('#panel-body')).toBeHidden();
  expect(retryAuthCount).toBe(beforeRetryCount + 1);

  // 3. testing_use_local performs auto-login once per server_run_id and persists run ID.
  let autoLoginCalls = 0;
  let sessionLoggedIn = false;
  await canvas.openCanvas({
    viewport: { width: 1400, height: 900 },
    localStorage: { narratron_server_run_id: 'old-run' },
    apiRoutes: {
      '/api/auth/me': async (route) => {
        await route.fulfill({
          json: sessionLoggedIn
            ? {
                authenticated: true,
                testing_use_local: true,
                server_run_id: 'run-42',
                user: { id: 1, username: 'LocalOrator', profile_color: '#22d3ee' },
              }
            : {
                authenticated: false,
                testing_use_local: true,
                server_run_id: 'run-42',
              },
        });
      },
      '/api/auth/auto-login': async (route) => {
        autoLoginCalls += 1;
        sessionLoggedIn = true;
        await route.fulfill({
          json: { user: { id: 1, username: 'LocalOrator', profile_color: '#22d3ee' } },
        });
      },
      '/api/chat': [],
      '/api/suggestions': [],
    },
  });

  await expect(page.locator('#chat-name-display')).toBeVisible();
  await expect(page.locator('#chat-name-display')).toHaveText('LocalOrator');
  expect(await page.evaluate(() => localStorage.getItem('narratron_server_run_id'))).toBe('run-42');
  expect(autoLoginCalls).toBe(1);
});

test('delayed auth resolution settles to authenticated member display without leaving guest controls visible', async ({
  canvas,
  page,
}) => {
  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname.startsWith('/api/'),
    async (route) => {
      await route.fulfill({ json: [] });
    },
  );

  await canvas.mount({
    viewport: { width: 400, height: 700 },
    stylesheets: ['/static/css/chat.css'],
    html: CHAT_IDENTITY_SHELL,
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { initializeChatController } =
      await window.__importModule<ChatControllerModule>(moduleUrl);
    const win = window as unknown as ChatHarnessWindow;
    win.controller = initializeChatController({
      messagesContainer: document.getElementById('chat-messages'),
      nameInput: document.getElementById('chat-name-input') as HTMLInputElement | null,
      nameDisplay: document.getElementById('chat-name-display'),
      nameStatusIcon: document.getElementById('chat-name-status-icon'),
      loginChip: document.getElementById('chat-login-chip'),
      getAuthState: () =>
        new Promise<CanvasAuthStateResponse>((resolve) => {
          win.resolveDelayedAuth = resolve;
        }),
    });
  }, CHAT_CONTROLLER_MODULE_URL);

  // While auth is pending, neither guest nor member name is finalized yet.
  await expect(page.locator('#chat-name-display')).toBeHidden();

  await page.evaluate(() => {
    const win = window as unknown as ChatHarnessWindow;
    win.resolveDelayedAuth?.({
      authenticated: true,
      user: { username: 'Gandalf', profile_color: '#ec4899' },
    });
  });

  await expect(page.locator('#chat-name-display')).toBeVisible();
  await expect(page.locator('#chat-name-display')).toHaveText('Gandalf');
  await expect(page.locator('#chat-name-input')).toBeHidden();
  await expect(page.locator('#chat-login-chip')).toBeHidden();
  await expect(page.locator('#chat-name-status-icon')).toHaveAttribute('aria-label', 'Member');
});
