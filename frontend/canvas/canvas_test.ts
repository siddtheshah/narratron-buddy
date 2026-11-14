import type { WebSocketRoute } from '@playwright/test';
import { expect, test } from '../../testing/ui/canvas_fixtures';

interface CanvasEntryModule {
  escapeHtml(str: unknown): string;
  getCanvasAuthState(): Promise<{
    authenticated: boolean;
    user?: { id?: number; username?: string } | null;
  }>;
}

interface CanvasCompatGlobals extends Window {
  setMicHotkey?: (code: string) => void;
}

const CANVAS_MODULE_URL = '/static/generated/canvas/canvas.js';
const CANVAS_SHIM_URL = '/static/js/canvas.js';
const PNG_DATA_URL =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

test('auth hydration runs testing auto-login once and displays authenticated user', async ({
  canvas,
  page,
}) => {
  let meCalls = 0;
  let autoLoginCalls = 0;
  let sessionLoggedIn = false;

  await canvas.openCanvas({
    user: { id: 7, username: 'AutoNarrator' },
    apiRoutes: {
      '/api/auth/me': async (route) => {
        meCalls += 1;
        await route.fulfill({
          json: sessionLoggedIn
            ? {
                authenticated: true,
                testing_use_local: true,
                server_run_id: 'run-42',
                user: { id: 7, username: 'AutoNarrator' },
              }
            : {
                authenticated: false,
                testing_use_local: true,
                server_run_id: 'run-42',
                user: null,
              },
        });
      },
      '/api/auth/auto-login': async (route) => {
        autoLoginCalls += 1;
        sessionLoggedIn = true;
        await route.fulfill({
          json: {
            authenticated: true,
            user: { id: 7, username: 'AutoNarrator' },
          },
        });
      },
    },
  });

  await expect(page.locator('#chat-name-display')).toHaveText('AutoNarrator');
  expect(autoLoginCalls).toBe(1);
  expect(await page.evaluate(() => localStorage.getItem('narratron_server_run_id'))).toBe('run-42');

  const callsBeforeSecondRead = meCalls;
  const auth = await page.evaluate(async (moduleUrl) => {
    const mod = await window.__importModule<CanvasEntryModule>(moduleUrl);
    return mod.getCanvasAuthState();
  }, CANVAS_MODULE_URL);
  expect(auth.authenticated).toBe(true);
  expect(auth.user?.username).toBe('AutoNarrator');
  expect(meCalls).toBe(callsBeforeSecondRead);
});

test('orator role UI activates controls and submits text commands via Shift+Space', async ({
  canvas,
  page,
}) => {
  const { apiRequests } = await canvas.openCanvas({
    isOrator: true,
    apiRoutes: {
      '/api/orator/command': async (route) => {
        await route.fulfill({ json: { ok: true } });
      },
    },
  });

  await expect(page.locator('#role-badge')).toHaveText('Orator');
  await expect(page.locator('#orator-controls')).toBeVisible();
  await expect(page.locator('#suggestions-toggle-btn')).toBeVisible();
  await expect(page.locator('#sticky-notes-toggle-btn')).toBeVisible();
  await expect(page.locator('#menu-item-theater-builder')).toHaveAttribute(
    'href',
    '/theater-editor?theater_id=stage',
  );

  await page.keyboard.press('Shift+Space');
  const overlay = page.locator('#orator-command-overlay');
  await expect(overlay).toHaveClass(/active/);
  await expect(page.locator('#orator-command-input')).toBeFocused();

  await page.keyboard.type('Summon the dragon');
  await page.keyboard.press('Enter');
  await expect(overlay).not.toHaveClass(/active/);
  await expect(overlay).toHaveAttribute('aria-hidden', 'true');

  const commandPosts = apiRequests.filter((req) => req.pathname === '/api/orator/command');
  expect(commandPosts).toHaveLength(1);
  expect(JSON.parse(commandPosts[0].postData ?? '{}')).toEqual({ text: 'Summon the dragon' });
});

test('viewer role hides orator controls and ignores orator command hotkey', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({
    isOrator: false,
  });

  await expect(page.locator('#role-badge')).toHaveText('Viewer');
  await expect(page.locator('#orator-controls')).toBeHidden();
  await expect(page.locator('#suggestions-toggle-btn')).toBeHidden();
  await expect(page.locator('#sticky-notes-toggle-btn')).toBeHidden();

  await page.keyboard.press('Shift+Space');
  await expect(page.locator('#orator-command-overlay')).not.toHaveClass(/active/);
});

test('canvas-state WebSocket state_changed refreshes latest scene, thought, and pin state', async ({
  canvas,
  page,
}) => {
  let stateSocket: WebSocketRoute | null = null;
  let latestPayload: Record<string, unknown> = {
    latest: null,
    prompt: '',
    history: [],
    agent_thought: { text: 'Waiting…', highlighted: false },
    pinned: false,
    music_pinned: false,
  };

  await canvas.openCanvas({
    isOrator: true,
    onWebSocket: (ws) => {
      if (ws.url().includes('/ws/canvas-state')) {
        stateSocket = ws;
      }
    },
    apiRoutes: {
      '/api/latest': async (route) => {
        await route.fulfill({ json: latestPayload });
      },
    },
  });

  expect(stateSocket).not.toBeNull();

  latestPayload = {
    latest: PNG_DATA_URL,
    prompt: 'Stormy keep',
    history: [{ url: PNG_DATA_URL, prompt: 'Stormy keep', time: 1 }],
    orator_cursor: 0,
    agent_thought: { text: 'Storm gathers', highlighted: true },
    pinned: true,
    music_pinned: true,
  };

  stateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));

  await expect(page.locator('#agent-thought-text')).toHaveText('Storm gathers');
  await expect(page.locator('.agent-thought-card')).toHaveClass(/is-highlighted/);
  await expect(page.locator('#prompt-text')).toHaveText('Stormy keep');
  await expect(page.locator('#canvas-pin-btn')).toHaveClass(/active/);
  await expect(page.locator('#canvas-pin-btn')).toHaveAttribute('aria-pressed', 'true');
  const currentSrc = await page.locator('#current-image').getAttribute('src');
  expect(currentSrc?.startsWith(PNG_DATA_URL)).toBe(true);
});

test('deprecated /static/js/canvas.js shim re-exports canonical canvas helpers and exposes globals', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({ isOrator: true });

  const result = await page.evaluate(
    async ({ canonicalUrl, shimUrl }) => {
      const canonical = await window.__importModule<CanvasEntryModule>(canonicalUrl);
      const shim = await window.__importModule<CanvasEntryModule>(shimUrl);
      const win = window as CanvasCompatGlobals;
      return {
        sameEscapeHtml: canonical.escapeHtml === shim.escapeHtml,
        sameGetAuthState: canonical.getCanvasAuthState === shim.getCanvasAuthState,
        escaped: shim.escapeHtml('<script>"ok"</script>'),
        hasGlobals:
          typeof win.openPulloutMenu === 'function' &&
          typeof win.closePulloutMenu === 'function' &&
          typeof win.openAdventureGuideModal === 'function' &&
          typeof win.openTheaterConfigModal === 'function' &&
          typeof win.toggleFullScreen === 'function' &&
          typeof win.togglePopoutPanel === 'function' &&
          typeof win.setMicHotkey === 'function' &&
          typeof win.setTextHotkey === 'function' &&
          typeof win.startAgentTheater === 'function' &&
          typeof win.stopAgentTheater === 'function' &&
          typeof win.onAuthSuccess === 'function',
      };
    },
    { canonicalUrl: CANVAS_MODULE_URL, shimUrl: CANVAS_SHIM_URL },
  );

  expect(result).toEqual({
    sameEscapeHtml: true,
    sameGetAuthState: true,
    escaped: '&lt;script&gt;&quot;ok&quot;&lt;/script&gt;',
    hasGlobals: true,
  });
});
