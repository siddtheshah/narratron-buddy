import fs from 'node:fs';
import path from 'node:path';
import { expect, REPO_ROOT, test } from './canvas_fixtures';

interface BookmarksModule {
  initializeCanvasBookmarks: (options: {
    theaterId: string;
    isOrator: () => boolean;
    getHistory: () => Array<{ path: string; time: number }>;
    getIndex: () => number;
    navigate: (target: number) => Promise<void>;
  }) => { refresh: () => void };
}

test('serves real production ES modules and CSS assets on https://canvas.test', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    html: '<div id="canvas-bookmarks" hidden></div>',
    stylesheets: ['/static/css/canvas-bookmarks.css'],
  });

  await page.evaluate(async () => {
    const mod = await window.__importModule<BookmarksModule>('/static/generated/canvas/bookmarks.js');
    mod.initializeCanvasBookmarks({
      theaterId: 'fixture-test',
      isOrator: () => true,
      getHistory: () => [{ path: 'images/forest.png', time: 1 }],
      getIndex: () => 0,
      navigate: async () => {},
    });
  });

  const toggle = page.locator('#canvas-bookmark-toggle');
  await expect(toggle).toBeVisible();
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.bookmark-flash')).toHaveText('1');
});

test('serves generated canvas assets under /static/generated/canvas/', async ({
  canvas,
  page,
}) => {
  const generatedDir = path.join(REPO_ROOT, 'static', 'generated', 'canvas');
  fs.mkdirSync(generatedDir, { recursive: true });
  const probeFile = `_fixture_probe_${process.pid}_${Date.now()}.js`;
  const tempProbePath = path.join(generatedDir, probeFile);
  fs.writeFileSync(tempProbePath, 'export const fixtureProbeValue = 42;\n', 'utf-8');

  try {
    await canvas.mount({ html: '<div id="probe"></div>' });
    const value = await page.evaluate(async (moduleUrl: string) => {
      const mod = await window.__importModule<{ fixtureProbeValue: number }>(moduleUrl);
      return mod.fixtureProbeValue;
    }, `/static/generated/canvas/${probeFile}`);
    expect(value).toBe(42);
  } finally {
    fs.rmSync(tempProbePath, { force: true });
  }
});

test('blocks service workers and records unmocked HTTP, WebSocket, and missing static requests', async ({
  canvas,
  context,
  page,
}) => {
  await canvas.mount({ html: '<div>isolated</div>' });

  const swBlocked = await page.evaluate(async () => {
    if (!('serviceWorker' in navigator)) {
      return true;
    }
    const registration = await navigator.serviceWorker.register('/static/generated/canvas/bookmarks.js');
    return registration === undefined && navigator.serviceWorker.controller === null;
  });
  expect(swBlocked).toBe(true);
  expect(context.serviceWorkers()).toEqual([]);

  await page.evaluate(async () => {
    try {
      await fetch('https://example.invalid/ping');
    } catch {
      // Expected abort from offline isolation route.
    }
    try {
      await fetch('/api/unexpected-endpoint');
    } catch {
      // Expected abort from offline isolation route.
    }
    try {
      await window.__importModule('/static/generated/canvas/does-not-exist.js');
    } catch {
      // Expected 404 for missing static asset.
    }
    await new Promise<void>((resolve) => {
      const ws = new WebSocket('wss://canvas.test/ws/unexpected');
      ws.addEventListener('close', () => resolve());
      ws.addEventListener('error', () => resolve());
    });
  });

  const recorded = canvas.clearUnexpectedRequests();
  expect(recorded).toContain('HTTP GET https://example.invalid/ping');
  expect(recorded).toContain('HTTP GET https://canvas.test/api/unexpected-endpoint');
  expect(recorded).toContain('Missing static asset: /static/generated/canvas/does-not-exist.js');
  expect(recorded).toContain('WS wss://canvas.test/ws/unexpected');
});

test('createIsolatedContext enforces isolation for secondary browser contexts', async ({
  canvas,
}) => {
  const secondary = await canvas.createIsolatedContext();
  const secondPage = await secondary.newPage();
  await secondPage.route(
    (url: URL) => url.origin === 'https://canvas.test' && url.pathname === '/secondary',
    async (route) => {
      await route.fulfill({ body: '<!DOCTYPE html><body>ok</body>', contentType: 'text/html' });
    },
  );
  await secondPage.goto('https://canvas.test/secondary');
  await secondPage.evaluate(async () => {
    try {
      await fetch('https://outside.invalid/leak');
    } catch {
      // Expected abort from secondary context isolation.
    }
  });

  const recorded = canvas.clearUnexpectedRequests();
  expect(recorded).toEqual(['HTTP GET https://outside.invalid/leak']);
});

test('openCanvas serves templates/canvas.html with mocked APIs and WebSocket isolation', async ({
  canvas,
  page,
}) => {
  const { theaterId, apiRequests } = await canvas.openCanvas({
    theaterId: 'stage',
    isOrator: true,
    collabEnabled: true,
  });

  expect(theaterId).toBe('stage');
  expect(apiRequests.some((req) => req.pathname === '/api/auth/me')).toBe(true);
  expect(apiRequests.some((req) => req.pathname === '/api/theaters/stage')).toBe(true);
  await expect(page.locator('#image-container')).toBeVisible();
});
