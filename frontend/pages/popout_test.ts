import fs from 'node:fs';
import path from 'node:path';
import type { Page, Route, WebSocketRoute } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  TEMPLATES_DIR,
  expect,
  test,
  type CanvasHarness,
} from '../../testing/ui/canvas_fixtures';

type PopoutModule = typeof import('/static/generated/pages/popout.js');

interface PopoutBroadcastRecord {
  readonly type?: string;
  readonly name?: string;
  readonly text?: string;
}

interface PopoutTestWindow extends Window {
  __popoutBroadcasts: PopoutBroadcastRecord[];
  __windowClosed: boolean;
}

const POPOUT_TEMPLATE_PATH = path.join(TEMPLATES_DIR, 'popout.html');

async function setupPopoutRoutes(canvas: CanvasHarness, page: Page): Promise<void> {
  await page.setViewportSize({ width: 420, height: 720 });
  const popoutHtml = await fs.promises.readFile(POPOUT_TEMPLATE_PATH, 'utf-8');

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/popout',
    async (route: Route) => {
      await route.fulfill({ contentType: 'text/html; charset=utf-8', body: popoutHtml });
    },
  );
  await canvas.serveRoute('https://fonts.googleapis.com/**', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'text/css; charset=utf-8', body: '' });
  });
  await canvas.serveRoute(`${CANVAS_ORIGIN}/favicon.png`, async (route: Route) => {
    await route.fulfill({ status: 204 });
  });
  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/auth/me',
    async (route: Route) => {
      await route.fulfill({ json: { authenticated: false } });
    },
  );
  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/chat',
    async (route: Route) => {
      await route.fulfill({ json: [] });
    },
  );
  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/latest',
    async (route: Route) => {
      await route.fulfill({ json: {} });
    },
  );
  await page.routeWebSocket('**/ws/**', (ws: WebSocketRoute) => {
    if (ws.url().includes('/ws/canvas-state')) {
      ws.send('{"type":"state_ready"}');
    }
  });
}

test('initPopoutChat throws when required DOM elements are missing', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    html: '<div id="popout-narratron-theater-id"></div>',
  });

  const errorMessage = await page.evaluate(async () => {
    const { initPopoutChat } = await window.__importModule<PopoutModule>(
      '/static/generated/pages/popout.js',
    );
    try {
      initPopoutChat();
      return null;
    } catch (error) {
      return error instanceof Error ? error.message : String(error);
    }
  });

  expect(errorMessage).toBe('Required popout chat DOM elements are missing.');
});

test('popout initializes guest/member identity, tabs, BroadcastChannel sync, and dock-back action', async ({
  canvas,
  page,
}) => {
  await setupPopoutRoutes(canvas, page);
  await page.addInitScript(() => {
    const win = window as unknown as PopoutTestWindow;
    win.__popoutBroadcasts = [];
    win.__windowClosed = false;
    window.close = () => {
      win.__windowClosed = true;
    };
    const channel = new BroadcastChannel('narratron_popout_channel');
    channel.addEventListener('message', (event: MessageEvent<PopoutBroadcastRecord>) => {
      win.__popoutBroadcasts.push(event.data);
    });
  });

  // 1. Guest flow without theater_id shows "No Theater Selected" and persists random guest name.
  await page.goto(`${CANVAS_ORIGIN}/popout`);
  await expect(page).toHaveTitle('Narratron - Pop-out Chat');
  await expect(page.locator('#chat-messages')).toBeAttached();
  await expect(page.locator('#popout-prompt-text')).toBeAttached();
  await expect(page.locator('#popout-narratron-theater-id')).toHaveText('No Theater Selected');
  await expect(page.locator('#chat-name-badge')).toHaveText('Anon');
  const guestName = await page.locator('#chat-name-input').inputValue();
  expect(guestName.length).toBeGreaterThan(3);

  // Updating guest name saves to localStorage and broadcasts name_updated.
  await page.locator('#chat-name-input').fill('Star Scout');
  await page.locator('#chat-name-input').press('Tab');
  expect(
    await page.evaluate(() => localStorage.getItem('narratron_anon_chat_name')),
  ).toBe('Star Scout');
  await expect
    .poll(async () =>
      page.evaluate(() => (window as unknown as PopoutTestWindow).__popoutBroadcasts),
    )
    .toContainEqual({ type: 'name_updated', name: 'Star Scout' });

  // Tab switching reveals Theater Info tab and hides Live Chat tab.
  await page.locator('.tab-btn[data-tab="controls-tab"]').click();
  await expect(page.locator('#controls-tab')).toHaveClass(/active/);
  await expect(page.locator('#chat-tab')).not.toHaveClass(/active/);

  // Dock Back broadcasts dock_panel and closes window.
  await page.locator('#dock-back-btn').click();
  await expect
    .poll(async () =>
      page.evaluate(() => (window as unknown as PopoutTestWindow).__windowClosed),
    )
    .toBe(true);
  const dockResult = await page.evaluate(() => ({
    broadcasts: (window as unknown as PopoutTestWindow).__popoutBroadcasts,
    closed: (window as unknown as PopoutTestWindow).__windowClosed,
  }));
  expect(dockResult.closed).toBe(true);
  expect(dockResult.broadcasts).toContainEqual({ type: 'name_updated', name: 'Star Scout' });
  expect(dockResult.broadcasts.at(-1)).toEqual({ type: 'dock_panel' });

  // 2. Authenticated user with theater_id shows Member badge and theater id.
  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/auth/me',
    async (route: Route) => {
      await route.fulfill({ json: { authenticated: true, user: { username: 'Aria' } } });
    },
  );
  await page.goto(`${CANVAS_ORIGIN}/popout?theater_id=moonlit_stage`);
  await expect(page.locator('#popout-narratron-theater-id')).toHaveText(
    'Theater: moonlit_stage',
  );
  await expect(page.locator('#chat-name-badge')).toHaveText('Member');
  await expect(page.locator('#chat-name-input')).toHaveValue('Aria');
});

test('popout updates thought card, music status, prompt, and chat messages over WebSocket state_changed', async ({
  canvas,
  page,
}) => {
  await setupPopoutRoutes(canvas, page);

  let stateSocket: WebSocketRoute | null = null;
  let latestState: Record<string, unknown> = {
    agent_thought: { text: 'Inspecting the runes.', highlighted: true },
    prompt: 'A misty stone bridge',
    music: { playlist: 'mist', paused: false },
  };
  let chatHistory: Array<Record<string, unknown>> = [
    { author: 'Narratron', text: 'Welcome to the bridge.' },
    { author: 'Rowan', type: 'suggestion', status: 'accepted', text: 'Light a torch' },
    {
      author: 'Narratron User Help',
      type: 'user_help',
      text: 'Press Shift+A',
      html: '<p>See <a href="https://example.test/docs">the guide</a>.</p>',
    },
    {
      author: 'Rowan',
      roll_data: {
        attack_rolls: [{ total: 20, is_crit: true }, { result: 1, is_fail: true }],
        damage_rolls: [['fire', { total: 9 }]],
      },
    },
  ];
  const postedMessages: Array<{ author: string; text: string }> = [];

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/auth/me',
    async (route: Route) => {
      await route.fulfill({ json: { authenticated: true, user: { username: 'Rowan' } } });
    },
  );
  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/latest',
    async (route: Route) => {
      await route.fulfill({ json: latestState });
    },
  );
  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/chat',
    async (route: Route) => {
      if (route.request().method() === 'POST') {
        const payload = JSON.parse(route.request().postData() ?? '{}') as {
          author: string;
          text: string;
        };
        postedMessages.push(payload);
        chatHistory = [...chatHistory, payload];
        await route.fulfill({ json: { ok: true } });
        return;
      }
      await route.fulfill({ json: chatHistory });
    },
  );
  await page.routeWebSocket('**/ws/canvas-state?**', (ws: WebSocketRoute) => {
    stateSocket = ws;
    ws.send('{"type":"state_ready"}');
  });

  await page.goto(`${CANVAS_ORIGIN}/popout?theater_id=bridge`);
  await expect.poll(() => stateSocket !== null).toBe(true);

  // Thought card, prompt, and music status reflect initial /api/latest state.
  await expect(page.locator('#agent-thought-text')).toHaveText('Inspecting the runes.');
  await expect(page.locator('.agent-thought-card')).toHaveClass(/is-highlighted/);
  await expect(page.locator('#popout-prompt-text')).toHaveText('A misty stone bridge');
  await expect(page.locator('#popout-music-status')).toHaveText('🎵 mist');

  // Verify author styling, accepted suggestion, user_help markdown attributes, and roll badges.
  const messages = page.locator('#chat-messages .chat-message');
  await expect(messages).toHaveCount(4);

  const narratronColor = await messages
    .nth(0)
    .locator('.chat-prefix')
    .evaluate((el) => getComputedStyle(el).color);
  expect(narratronColor).toBe('rgb(255, 255, 255)');

  await expect(messages.nth(1)).toHaveClass(/suggestion-accepted/);
  await expect(messages.nth(1).locator('.suggestion-accepted-badge')).toHaveText('✓ Accepted');

  const helpMessage = messages.nth(2);
  await expect(helpMessage).toHaveClass(/user-help-message/);
  await expect(helpMessage.locator('.chat-text')).toHaveClass(/help-markdown/);
  await expect(helpMessage.locator('a')).toHaveAttribute('target', '_blank');
  await expect(helpMessage.locator('a')).toHaveAttribute('rel', 'noopener noreferrer');

  const rollMessage = messages.nth(3);
  await expect(rollMessage).toHaveClass(/chat-roll-message/);
  await expect(rollMessage.locator('.attack-roll.crit-hit')).toHaveText('🎯 20');
  await expect(rollMessage.locator('.attack-roll.crit-fail')).toHaveText('🎯 1');
  await expect(rollMessage.locator('.damage-roll')).toHaveText('💥 9 fire');

  // state_changed updates wandering thought and paused/silent music status.
  latestState = {
    agent_thought: { text: 'wandering', highlighted: false },
    music: { playlist: 'mist', paused: true },
  };
  stateSocket!.send('{"type":"state_changed","domains":["latest"]}');
  await expect(page.locator('#agent-thought-text')).toHaveText(
    'Narratron has wandered away. (Try resummoning?)',
  );
  await expect(page.locator('#agent-thought-text')).toHaveClass(/is-wandering/);
  await expect(page.locator('.agent-thought-card')).not.toHaveClass(/is-highlighted/);
  await expect(page.locator('#popout-music-status')).toHaveText('🎵 mist (Paused)');

  latestState = { agent_thought: { text: '' }, music: null };
  stateSocket!.send('{"type":"state_changed","domains":["latest"]}');
  await expect(page.locator('#popout-music-status')).toHaveText('🎵 Silent');

  // Submitting plain text and Ctrl+Enter suggestion posts to /api/chat.
  await page.locator('#chat-input').fill('Check the lantern');
  await page.locator('#chat-input').press('Enter');
  await expect.poll(() => postedMessages.length).toBe(1);
  expect(postedMessages[0]).toEqual({ author: 'Rowan', text: 'Check the lantern' });

  await page.locator('#chat-input').fill('Open the gate');
  await page.locator('#chat-input').press('Control+Enter');
  await expect.poll(() => postedMessages.length).toBe(2);
  expect(postedMessages[1]).toEqual({ author: 'Rowan', text: '/suggest Open the gate' });
  await expect(messages).toHaveCount(6);
});

test('popout handles malformed WS messages, API errors, and reconnects closed state socket', async ({
  canvas,
  page,
}) => {
  await setupPopoutRoutes(canvas, page);

  const warnings: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'warning') {
      warnings.push(msg.text());
    }
  });

  let stateConnections = 0;
  let firstSocket: WebSocketRoute | null = null;
  let chatCalls = 0;
  let latestCalls = 0;

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/auth/me',
    async (route: Route) => {
      await route.fulfill({ status: 500, body: 'auth down' });
    },
  );
  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/chat',
    async (route: Route) => {
      chatCalls += 1;
      if (chatCalls === 1) {
        await route.fulfill({ status: 500, body: 'chat down' });
        return;
      }
      await route.fulfill({ json: [{ author: 'Narratron', text: 'Back online.' }] });
    },
  );
  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/latest',
    async (route: Route) => {
      latestCalls += 1;
      if (latestCalls === 1) {
        await route.fulfill({ status: 500, body: 'latest down' });
        return;
      }
      await route.fulfill({
        json: { agent_thought: { text: 'Reconnected thought.', highlighted: false } },
      });
    },
  );

  await page.routeWebSocket('**/ws/canvas-state?**', (ws: WebSocketRoute) => {
    stateConnections += 1;
    if (stateConnections === 1) {
      firstSocket = ws;
      return;
    }
    ws.send('{"type":"state_ready"}');
  });

  await page.goto(`${CANVAS_ORIGIN}/popout?theater_id=recover-stage`);
  await expect.poll(() => firstSocket !== null).toBe(true);

  // Malformed JSON triggers console.warn without throwing a pageerror.
  firstSocket!.send('{invalid-json');
  await expect
    .poll(() => warnings.some((text) => text.includes('Ignoring invalid canvas-state message')))
    .toBe(true);

  // Closing the first socket triggers reconnect (~1000ms) and recovers both chat and latest state.
  firstSocket!.close();
  await expect.poll(() => stateConnections, { timeout: 5000 }).toBeGreaterThanOrEqual(2);
  await expect(page.locator('#chat-messages .chat-message')).toHaveCount(1);
  await expect(page.locator('#agent-thought-text')).toHaveText('Reconnected thought.');
});
