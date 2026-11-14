import fs from 'node:fs';
import path from 'node:path';
import type { WebSocketRoute } from '@playwright/test';
import { CANVAS_ORIGIN, expect, REPO_ROOT, STATIC_DIR, test } from '../../testing/ui/canvas_fixtures';

type AuthFlowModule = typeof import('/static/generated/shared/auth-flow.js');

const AUTH_FLOW_MODULE_URL = '/static/generated/shared/auth-flow.js';

test('auth-flow deduplicates /api/auth/me, supports invalidateAuthState, and retries transient errors', async ({
  canvas,
  page,
}) => {
  let meCalls = 0;
  let failMe = true;

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/auth/me',
    async (route) => {
      meCalls += 1;
      if (failMe) {
        await route.fulfill({ status: 500, contentType: 'text/plain', body: 'boom' });
        return;
      }
      await route.fulfill({ json: { authenticated: true, user: { id: 1, username: 'Ada' } } });
    },
  );

  await canvas.mount({
    moduleScripts: [`import ${JSON.stringify(AUTH_FLOW_MODULE_URL)};`],
  });

  // Initial initAuthFlow failed transiently, so subsequent getAuthState() calls retry.
  await expect.poll(() => meCalls).toBe(1);
  failMe = false;

  const first = await page.evaluate(async (url: string) => {
    const mod = await window.__importModule<AuthFlowModule>(url);
    const [a, b] = await Promise.all([mod.getAuthState(), mod.getAuthState()]);
    return { a, b };
  }, AUTH_FLOW_MODULE_URL);
  expect(first.a.authenticated).toBe(true);
  expect(first.b.user?.username).toBe('Ada');
  expect(meCalls).toBe(2);

  // Subsequent getAuthState() reuses the cached promise without hitting the network.
  await page.evaluate(async (url: string) => {
    const mod = await window.__importModule<AuthFlowModule>(url);
    await mod.getAuthState();
  }, AUTH_FLOW_MODULE_URL);
  expect(meCalls).toBe(2);

  // invalidateAuthState() forces the next getAuthState() to fetch again.
  await page.evaluate(async (url: string) => {
    const mod = await window.__importModule<AuthFlowModule>(url);
    mod.invalidateAuthState();
    await mod.getAuthState();
  }, AUTH_FLOW_MODULE_URL);
  expect(meCalls).toBe(3);
});

test('canvas uses dynamic isCurrentOrator check and deduplicates initial /api/auth/me', async ({
  canvas,
  page,
}) => {
  const canvasScript = fs.readFileSync(
    path.join(REPO_ROOT, 'frontend', 'canvas', 'canvas.ts'),
    'utf-8',
  );
  const hotkeyScript = fs.readFileSync(
    path.join(REPO_ROOT, 'frontend', 'canvas', 'hotkey-controls.ts'),
    'utf-8',
  );
  expect(canvasScript).toContain('function isCurrentOrator()');
  expect(canvasScript).toContain('isCurrentOrator() && agentWs');
  expect(hotkeyScript).toContain('if (!isCurrentOrator()) return;');

  let authMeCount = 0;
  let activeOratorId = 1;
  let doodleWs: WebSocketRoute | null = null;

  await canvas.openCanvas({
    viewport: { width: 1280, height: 800 },
    isOrator: true,
    user: { id: 1, username: 'orator' },
    onWebSocket: (ws) => {
      if (ws.url().includes('/ws/doodle')) {
        doodleWs = ws;
      }
    },
    apiRoutes: {
      '/api/auth/me': async (route) => {
        authMeCount += 1;
        await route.fulfill({
          json: { authenticated: true, user: { id: 1, username: 'orator' } },
        });
      },
      '/api/theaters/stage/baton': async (route) => {
        await route.fulfill({
          json: {
            owner: { id: 1 },
            active_orator: { id: activeOratorId },
            viewer_collab_enabled: true,
          },
        });
      },
      '/api/chat': [],
      '/api/suggestions': [],
    },
  });

  // One request from canvas.js (getCanvasAuthState shared across chat, stamps, baton, mic)
  // and one from auth-flow.js initAuthFlow().
  expect(authMeCount).toBeLessThanOrEqual(2);
  expect(await page.evaluate(() => window._isActiveOratorState)).toBe(true);
  await expect(page.locator('#orator-controls')).toBeVisible();

  // Hand off baton to another user via WebSocket baton_state; dynamic isCurrentOrator() updates canvas controls.
  activeOratorId = 999;
  await expect.poll(() => doodleWs !== null).toBe(true);
  doodleWs!.send(
    JSON.stringify({
      type: 'baton_state',
      baton_state: {
        owner: { id: 1 },
        active_orator: { id: 999 },
        contributors: [],
        viewer_collab_enabled: true,
      },
      active_viewers: [],
    }),
  );
  await page.waitForFunction(() => window._isActiveOratorState === false, undefined, {
    timeout: 5000,
  });
  await expect(page.locator('#orator-controls')).toBeHidden();
});

test('auth-flow performs testing_use_local auto-login once per narratron_server_run_id', async ({
  canvas,
  page,
}) => {
  let autoLoginCalls = 0;
  let sessionAuthenticated = false;

  await canvas.serveRoute(
    (url: URL) =>
      url.origin === CANVAS_ORIGIN &&
      (url.pathname === '/api/auth/me' || url.pathname === '/api/auth/auto-login'),
    async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/auth/auto-login') {
        autoLoginCalls += 1;
        sessionAuthenticated = true;
        await route.fulfill({
          json: { user: { id: 1, username: 'AutoOrator', credits: 5 } },
        });
        return;
      }
      await route.fulfill({
        json: sessionAuthenticated
          ? {
              authenticated: true,
              testing_use_local: true,
              server_run_id: 'run-99',
              user: { id: 1, username: 'AutoOrator', credits: 5 },
            }
          : {
              authenticated: false,
              testing_use_local: true,
              server_run_id: 'run-99',
            },
      });
    },
  );

  await canvas.mount({
    localStorage: { narratron_server_run_id: 'old-run' },
    html: '<div id="userAccountBar"></div>',
    moduleScripts: [`import ${JSON.stringify(AUTH_FLOW_MODULE_URL)};`],
  });

  await expect(page.locator('#userAccountBar .user-pill')).toHaveText('👤 AutoOrator');
  expect(autoLoginCalls).toBe(1);
  expect(await page.evaluate(() => localStorage.getItem('narratron_server_run_id'))).toBe('run-99');

  // Calling getAuthState({ refresh: true }) with the same server_run_id does not re-trigger auto-login.
  await page.evaluate(async (url: string) => {
    const mod = await window.__importModule<AuthFlowModule>(url);
    await mod.getAuthState({ refresh: true });
  }, AUTH_FLOW_MODULE_URL);
  expect(autoLoginCalls).toBe(1);
});
