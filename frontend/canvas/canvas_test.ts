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

test('canonical canvas module exports helpers and exposes globals', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({ isOrator: true });

  const result = await page.evaluate(
    async ({ canonicalUrl }) => {
      const canonical = await window.__importModule<CanvasEntryModule>(canonicalUrl);
      const win = window as CanvasCompatGlobals;
      return {
        escaped: canonical.escapeHtml('<script>"ok"</script>'),
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
    { canonicalUrl: CANVAS_MODULE_URL },
  );

  expect(result).toEqual({
    escaped: '&lt;script&gt;&quot;ok&quot;&lt;/script&gt;',
    hasGlobals: true,
  });
});

test('canvas plays triframe and silent looping video animations from state updates and stops on static images', async ({
  canvas,
  page,
}) => {
  await page.addInitScript(() => {
    Object.defineProperty(HTMLMediaElement.prototype, 'src', {
      configurable: true,
      get() {
        return this.getAttribute('data-test-src') || '';
      },
      set(url: string) {
        this.setAttribute('data-test-src', url);
      },
    });
    (window as unknown as { __canvasVideoPlays: number }).__canvasVideoPlays = 0;
    HTMLMediaElement.prototype.play = async function (): Promise<void> {
      (window as unknown as { __canvasVideoPlays: number }).__canvasVideoPlays += 1;
    };
    HTMLMediaElement.prototype.pause = function (): void {};
    HTMLMediaElement.prototype.load = function (): void {};
  });

  let stateSocket: WebSocketRoute | null = null;
  let latestPayload: Record<string, unknown> = {
    latest: null,
    prompt: '',
    history: [],
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

  await expect.poll(() => stateSocket).not.toBeNull();

  // 1. Triframe animation hides #current-image and #background-layer and renders to #canvas-renderer.
  latestPayload = {
    latest: PNG_DATA_URL,
    prompt: 'Lantern loop',
    time: 10,
    orator_cursor: 0,
    history: [
      {
        url: PNG_DATA_URL,
        prompt: 'Lantern loop',
        time: 10,
        animation: {
          type: 'triframe',
          frames: [PNG_DATA_URL, PNG_DATA_URL, PNG_DATA_URL],
          frame_duration_ms: 100,
          crossfade_duration_ms: 30,
        },
      },
    ],
    animation: {
      type: 'triframe',
      frames: [PNG_DATA_URL, PNG_DATA_URL, PNG_DATA_URL],
      frame_duration_ms: 100,
      crossfade_duration_ms: 30,
    },
  };
  stateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
  await expect(page.locator('#prompt-text')).toHaveText('Lantern loop');
  await expect(page.locator('#current-image')).toHaveCSS('opacity', '0');
  await expect(page.locator('#bg-layer')).toHaveCSS('background-image', 'none');

  // 2. Video animation attaches a silent, looping <video> inside #image-container.
  latestPayload = {
    latest: PNG_DATA_URL,
    prompt: 'Soaring griffin',
    time: 20,
    orator_cursor: 1,
    history: [
      {
        url: PNG_DATA_URL,
        prompt: 'Soaring griffin',
        time: 20,
        animation: {
          type: 'video',
          id: 'flight_123',
          video_url: '/video.mp4',
          video_duration_seconds: 5,
          loop: true,
          muted: true,
        },
      },
    ],
    animation: {
      type: 'video',
      id: 'flight_123',
      video_url: '/video.mp4',
      video_duration_seconds: 5,
      loop: true,
      muted: true,
    },
  };
  stateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
  await expect(page.locator('#image-container video')).toHaveCount(1);
  expect(
    await page.locator('#image-container video').evaluate((video: HTMLVideoElement) => ({
      muted: video.muted,
      defaultMuted: video.defaultMuted,
      volume: video.volume,
      loop: video.loop,
      mutedAttr: video.hasAttribute('muted'),
    })),
  ).toEqual({
    muted: true,
    defaultMuted: true,
    volume: 0,
    loop: true,
    mutedAttr: true,
  });

  await page.locator('#image-container video').dispatchEvent('loadeddata');
  await expect
    .poll(() =>
      page.evaluate(() => (window as unknown as { __canvasVideoPlays: number }).__canvasVideoPlays),
    )
    .toBe(1);

  // Returning to a static scene removes the <video> element cleanly.
  latestPayload = {
    latest: PNG_DATA_URL,
    prompt: 'Quiet camp',
    time: 30,
    orator_cursor: 2,
    history: [{ url: PNG_DATA_URL, prompt: 'Quiet camp', time: 30 }],
  };
  stateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
  await expect(page.locator('#image-container video')).toHaveCount(0);
});
