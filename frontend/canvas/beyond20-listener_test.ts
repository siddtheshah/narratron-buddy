import fs from 'node:fs';
import path from 'node:path';
import type { Page, Route, WebSocketRoute } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  expect,
  TEMPLATES_DIR,
  test,
  type CanvasHarness,
} from '../../testing/ui/canvas_fixtures';

type Beyond20Module = typeof import('/static/generated/canvas/beyond20-listener.js');
type Beyond20ChatController = import('/static/generated/canvas/beyond20-listener.js').Beyond20ChatController;
type Beyond20ChatPostPayload = import('/static/generated/canvas/beyond20-listener.js').Beyond20ChatPostPayload;
type Beyond20ListenerController = import('/static/generated/canvas/beyond20-listener.js').Beyond20ListenerController;
type Beyond20RollPayload = import('/static/generated/canvas/beyond20-listener.js').Beyond20RollPayload;
type Beyond20SettingsPayload = import('/static/generated/canvas/beyond20-listener.js').Beyond20SettingsPayload;

interface ChatControllerModule {
  initializeChatController(options: {
    messagesContainer: HTMLElement | null;
    getAuthState: () => Promise<{ authenticated: boolean; user: { username: string } }>;
  }): {
    renderChatMessage(msg: RollMessageFixture): HTMLElement;
  };
}

interface RollMessageFixture {
  author: string;
  text: string;
  roll_data: {
    title: string;
    html: string;
    attack_rolls: Array<{ total: number; is_crit: boolean; formula?: string }>;
    damage_rolls?: Array<[string, { total: number }]>;
  };
}

interface Beyond20HarnessWindow extends Window {
  auditRoll?: number;
  _isContributor?: boolean;
  beyond20Mod: Beyond20Module;
  beyond20Controller?: Beyond20ListenerController;
  postedMessages: Beyond20ChatPostPayload[];
  forwardedRolls: Array<{ summary: string; roll: Beyond20RollPayload }>;
  wsSentPayloads: string[];
  connectedPayloads: Array<Beyond20SettingsPayload | undefined>;
  wsReadyState: number;
  contributorFlag: boolean;
  collabFlag: boolean;
  oratorFlag: boolean;
}

interface ChatPostPayload {
  author: string;
  text: string;
  roll_data?: {
    title: string;
    character: string;
    forwarded_to_agent: boolean;
  };
}

const CANONICAL_URL = '/static/generated/canvas/beyond20-listener.js';
const SHIM_URL = '/static/js/beyond20-listener.js';

const MALICIOUS_LONGSWORD_MESSAGE: RollMessageFixture = {
  author: 'Ada',
  text: '🎲 Ada rolled Longsword Attack [23] dealing 8 Slashing damage',
  roll_data: {
    title: 'Longsword Attack',
    html:
      '<div class="beyond20-message beyond20-card dnd5e" onmouseover="window.auditRoll=1">' +
      '<img src="/missing" onerror="window.auditRoll=1">' +
      '<svg onload="window.auditRoll=2"><script>window.auditRoll=3</script></svg>' +
      '<iframe src="javascript:window.auditRoll=4"></iframe>' +
      '<a href="javascript:window.auditRoll=5" onclick="window.auditRoll=6">Link</a>' +
      '<div class="beyond20-dice-roller-content">' +
      '<strong class="beyond20-roll-result" title="1d20 + 5">23</strong>' +
      '<span class="beyond20-total-damage">8 Slashing</span>' +
      '</div></div>',
    attack_rolls: [{ total: 23, is_crit: true, formula: '1d20 + 5' }],
    damage_rolls: [['Slashing', { total: 8 }]],
  },
};

const PURE_SCRIPT_STEALTH_MESSAGE: RollMessageFixture = {
  author: 'Ada',
  text: '🎲 Ada rolled Stealth [19]',
  roll_data: {
    title: 'Stealth',
    html: '<script>window.auditRoll=99</script><img src=x onerror="window.auditRoll=99">',
    attack_rolls: [{ total: 19, is_crit: false }],
  },
};

async function mountListenerHarness(
  canvas: CanvasHarness,
  page: Page,
  options: {
    moduleUrl?: string;
    contributor?: boolean;
    omitContributor?: boolean;
    collab?: boolean;
    orator?: boolean;
    forwardToNarrator?: boolean;
    notifyHPUpdates?: boolean;
    useForwardCallback?: boolean;
    wsReadyState?: number;
  } = {},
): Promise<void> {
  const {
    moduleUrl = CANONICAL_URL,
    contributor = true,
    omitContributor = false,
    collab = false,
    orator = false,
    forwardToNarrator = true,
    notifyHPUpdates = true,
    useForwardCallback = false,
    wsReadyState = 1,
  } = options;

  await canvas.mount({ html: '<div id="host"></div>' });
  await page.evaluate(
    async (config: {
      url: string;
      contributor: boolean;
      omitContributor: boolean;
      collab: boolean;
      orator: boolean;
      forwardToNarrator: boolean;
      notifyHPUpdates: boolean;
      useForwardCallback: boolean;
      wsReadyState: number;
    }) => {
      const win = window as unknown as Beyond20HarnessWindow;
      const mod = await window.__importModule<Beyond20Module>(config.url);
      win.beyond20Mod = mod;
      win.postedMessages = [];
      win.forwardedRolls = [];
      win.wsSentPayloads = [];
      win.connectedPayloads = [];
      win.wsReadyState = config.wsReadyState;
      win.contributorFlag = config.contributor;
      win.collabFlag = config.collab;
      win.oratorFlag = config.orator;

      const chatController: Beyond20ChatController = {
        getCurrentUsername: () => 'FallbackUser',
        postChatMessage: async (payload: Beyond20ChatPostPayload) => {
          win.postedMessages.push(payload);
          return { ok: true };
        },
      };

      win.beyond20Controller = mod.initializeBeyond20Listener({
        chatController,
        theaterId: 'stage',
        isContributor: config.omitContributor ? null : () => win.contributorFlag,
        isCollabModeEnabled: () => win.collabFlag,
        isCurrentOrator: () => win.oratorFlag,
        getAgentWs: () => ({
          get readyState(): number {
            return win.wsReadyState;
          },
          send(data: string): void {
            win.wsSentPayloads.push(data);
          },
        }),
        forwardRollToAgent: config.useForwardCallback
          ? (summary: string, roll: Beyond20RollPayload) => {
              win.forwardedRolls.push({ summary, roll });
            }
          : null,
        onConnected: (settings: Beyond20SettingsPayload | undefined) => {
          win.connectedPayloads.push(settings);
        },
        forwardToNarrator: config.forwardToNarrator,
        notifyHPUpdates: config.notifyHPUpdates,
      });
    },
    {
      url: moduleUrl,
      contributor,
      omitContributor,
      collab,
      orator,
      forwardToNarrator,
      notifyHPUpdates,
      useForwardCallback,
      wsReadyState,
    },
  );
}

test('formatBeyond20RollSummary formats attack crits/fails, damage rolls, and total damages', async ({
  canvas,
  page,
}) => {
  await canvas.mount();
  const summaries = await page.evaluate(async (moduleUrl: string) => {
    const mod = await window.__importModule<Beyond20Module>(moduleUrl);
    return {
      missing: mod.formatBeyond20RollSummary(null),
      critWithDamage: mod.formatBeyond20RollSummary({
        character: { name: 'Lyra' },
        title: 'Longsword',
        attack_rolls: [
          { total: 24, is_crit: true },
          { result: 3, is_fail: true },
        ],
        damage_rolls: [
          ['Slashing', { total: 9 }],
          ['Radiant', { result: 4 }],
        ],
      }),
      totalDamagesFallback: mod.formatBeyond20RollSummary({
        request: { name: 'Fireball' },
        total_damages: {
          Total: { total: 28 },
          Fire: 28,
        },
      }),
    };
  }, CANONICAL_URL);

  expect(summaries.missing).toBe('Unknown roll');
  expect(summaries.critWithDamage).toBe(
    'Lyra rolled Longsword [24 (Critical Hit!) / 3 (Critical Fail!)] dealing 9 Slashing + 4 Radiant damage',
  );
  expect(summaries.totalDamagesFallback).toBe(
    'Adventurer rolled Fireball dealing 28, 28 Fire damage',
  );
});

test('enforces contributor permission, collab mode forwarding, HP updates, detection, and disconnect', async ({
  canvas,
  page,
}) => {
  await mountListenerHarness(canvas, page, {
    contributor: false,
    collab: true,
    orator: false,
    wsReadyState: 1,
  });

  // 1. Non-contributors cannot post rolls or HP updates.
  await page.evaluate(() => {
    document.dispatchEvent(
      new CustomEvent('Beyond20_RenderedRoll', {
        detail: [{ title: 'Perception', attack_rolls: [{ total: 17 }] }],
      }),
    );
    document.dispatchEvent(
      new CustomEvent('Beyond20_UpdateHP', {
        detail: [{}, 'Lyra', 18, 24, 5],
      }),
    );
  });

  let state = await page.evaluate(() => {
    const win = window as unknown as Beyond20HarnessWindow;
    return {
      posted: win.postedMessages,
      wsSent: win.wsSentPayloads,
      detected: win.beyond20Controller?.isDetected(),
    };
  });
  expect(state.posted).toHaveLength(0);
  expect(state.wsSent).toHaveLength(0);
  expect(state.detected).toBe(false);

  // 2. Contributor with collab disabled posts to chat with forwarded_to_agent=false.
  await page.evaluate(() => {
    const win = window as unknown as Beyond20HarnessWindow;
    win.contributorFlag = true;
    win.collabFlag = false;
    document.dispatchEvent(
      new CustomEvent('Beyond20_RenderedRoll', {
        detail: [
          {
            title: 'Stealth',
            attack_rolls: [{ total: 19 }],
            html: '<div class="beyond20-card">19</div>',
          },
        ],
      }),
    );
  });

  state = await page.evaluate(() => {
    const win = window as unknown as Beyond20HarnessWindow;
    return {
      posted: win.postedMessages,
      wsSent: win.wsSentPayloads,
      detected: win.beyond20Controller?.isDetected(),
    };
  });
  expect(state.posted).toHaveLength(1);
  expect(state.posted[0]?.author).toBe('FallbackUser');
  expect(state.posted[0]?.rollData?.forwarded_to_agent).toBe(false);
  expect(state.wsSent).toHaveLength(0);

  // 3. Contributor with collab enabled and closed WebSocket does not mark forwarded_to_agent.
  await page.evaluate(() => {
    const win = window as unknown as Beyond20HarnessWindow;
    win.collabFlag = true;
    win.wsReadyState = 3; // CLOSED
    document.dispatchEvent(
      new CustomEvent('Beyond20_RenderedRoll', {
        detail: [{ character: { name: 'Lyra' }, title: 'Arcana', attack_rolls: [{ total: 15 }] }],
      }),
    );
  });

  state = await page.evaluate(() => {
    const win = window as unknown as Beyond20HarnessWindow;
    return {
      posted: win.postedMessages,
      wsSent: win.wsSentPayloads,
      detected: win.beyond20Controller?.isDetected(),
    };
  });
  expect(state.posted).toHaveLength(2);
  expect(state.posted[1]?.rollData?.forwarded_to_agent).toBe(false);
  expect(state.wsSent).toHaveLength(0);

  // 4. Contributor with collab enabled and open WebSocket forwards roll and posts HP update.
  await page.evaluate(() => {
    const win = window as unknown as Beyond20HarnessWindow;
    win.wsReadyState = 1; // OPEN
    document.dispatchEvent(
      new CustomEvent('Beyond20_RenderedRoll', {
        detail: [{ character: { name: 'Lyra' }, title: 'Insight', attack_rolls: [{ total: 21 }] }],
      }),
    );
    document.dispatchEvent(
      new CustomEvent('Beyond20_UpdateHP', {
        detail: [{}, 'Lyra', 18, 24, 5],
      }),
    );
    document.dispatchEvent(new CustomEvent('Beyond20_Loaded', { detail: [{ version: '2.0' }] }));
    document.dispatchEvent(new CustomEvent('Beyond20_NewSettings', { detail: [{ version: '2.1' }] }));
  });

  const finalState = await page.evaluate(() => {
    const win = window as unknown as Beyond20HarnessWindow;
    return {
      posted: win.postedMessages,
      wsSent: win.wsSentPayloads,
      connected: win.connectedPayloads,
      detected: win.beyond20Controller?.isDetected(),
    };
  });
  expect(finalState.posted).toHaveLength(4);
  expect(finalState.posted[2]?.rollData?.forwarded_to_agent).toBe(true);
  expect(finalState.posted[3]).toEqual({
    author: 'Lyra',
    text: "❤️ Lyra's HP: 18/24 (+5 temp)",
  });
  expect(finalState.wsSent).toEqual([
    JSON.stringify({ type: 'text', text: '[D&D Dice Roll] Lyra rolled Insight [21]' }),
  ]);
  expect(finalState.detected).toBe(true);
  expect(finalState.connected).toEqual([{ version: '2.0' }]);

  // 5. disconnect() unregisters listeners.
  await page.evaluate(() => {
    const win = window as unknown as Beyond20HarnessWindow;
    win.beyond20Controller?.disconnect();
    document.dispatchEvent(
      new CustomEvent('Beyond20_RenderedRoll', {
        detail: [{ title: 'After Disconnect', attack_rolls: [{ total: 10 }] }],
      }),
    );
    document.dispatchEvent(
      new CustomEvent('Beyond20_UpdateHP', {
        detail: [{}, 'Lyra', 10, 24, 0],
      }),
    );
  });
  const countAfterDisconnect = await page.evaluate(
    () => (window as unknown as Beyond20HarnessWindow).postedMessages.length,
  );
  expect(countAfterDisconnect).toBe(4);
});

test('supports forwardRollToAgent, forwardToNarrator=false, notifyHPUpdates=false, and isCurrentOrator fallback', async ({
  canvas,
  page,
}) => {
  await mountListenerHarness(canvas, page, {
    omitContributor: true,
    orator: true,
    collab: true,
    forwardToNarrator: true,
    notifyHPUpdates: false,
    useForwardCallback: true,
  });

  await page.evaluate(() => {
    document.dispatchEvent(
      new CustomEvent('Beyond20_RenderedRoll', {
        detail: [{ character: { name: 'Kael' }, title: 'Athletics', attack_rolls: [{ total: 18 }] }],
      }),
    );
    document.dispatchEvent(
      new CustomEvent('Beyond20_UpdateHP', {
        detail: [{}, 'Kael', 12, 20, 0],
      }),
    );
  });

  const firstRun = await page.evaluate(() => {
    const win = window as unknown as Beyond20HarnessWindow;
    return {
      posted: win.postedMessages,
      forwarded: win.forwardedRolls,
    };
  });
  expect(firstRun.posted).toHaveLength(1);
  expect(firstRun.posted[0]?.rollData?.forwarded_to_agent).toBe(true);
  expect(firstRun.forwarded).toHaveLength(1);
  expect(firstRun.forwarded[0]?.summary).toBe('Kael rolled Athletics [18]');

  await mountListenerHarness(canvas, page, {
    contributor: true,
    collab: true,
    forwardToNarrator: false,
    useForwardCallback: true,
  });

  await page.evaluate(() => {
    document.dispatchEvent(
      new CustomEvent('Beyond20_RenderedRoll', {
        detail: [{ title: 'History', attack_rolls: [{ total: 14 }] }],
      }),
    );
  });
  const suppressedRun = await page.evaluate(() => {
    const win = window as unknown as Beyond20HarnessWindow;
    return {
      posted: win.postedMessages,
      forwarded: win.forwardedRolls,
    };
  });
  expect(suppressedRun.posted).toHaveLength(1);
  expect(suppressedRun.posted[0]?.rollData?.forwarded_to_agent).toBe(false);
  expect(suppressedRun.forwarded).toHaveLength(0);
});

test('/canvas blocks Beyond20 rolls for non-contributors and updates _isContributor from metadata then baton', async ({
  canvas,
  page,
}) => {
  const postedToChat: ChatPostPayload[] = [];
  let releaseBaton: (() => void) | null = null;
  const batonGate = new Promise<void>((resolve) => {
    releaseBaton = resolve;
  });

  await canvas.openCanvas({
    isOrator: false,
    collabEnabled: true,
    user: { id: 10, username: 'viewer' },
    apiRoutes: {
      '/api/chat': async (route: Route): Promise<void> => {
        if (route.request().method() === 'POST') {
          const payload = JSON.parse(route.request().postData() || '{}') as ChatPostPayload;
          postedToChat.push(payload);
          await route.fulfill({ json: { type: 'chat', ...payload } });
          return;
        }
        await route.fulfill({ json: [] });
      },
      '/api/theaters/stage': {
        metadata: { is_owner: false, is_active_orator: false, is_contributor: true },
      },
      '/api/theaters/stage/baton': async (route: Route): Promise<void> => {
        await batonGate;
        await route.fulfill({
          json: {
            owner: { id: 1, username: 'owner' },
            active_orator: { id: 1, username: 'owner' },
            contributors: [],
            viewer_collab_enabled: true,
          },
        });
      },
    },
  });

  // Theater metadata sets _isContributor = true before baton settles.
  await page.waitForFunction(
    () => (window as unknown as Beyond20HarnessWindow)._isContributor === true,
  );

  await page.evaluate(() => {
    document.dispatchEvent(
      new CustomEvent('Beyond20_RenderedRoll', {
        detail: [{ character: { name: 'MetaContrib' }, title: 'Nature', attack_rolls: [{ total: 16 }] }],
      }),
    );
  });
  await expect.poll(() => postedToChat.length).toBe(1);
  expect(postedToChat[0]?.roll_data?.forwarded_to_agent).toBe(false);

  // When baton resolves with contributors: [], _isContributor updates to false and rolls are blocked.
  if (releaseBaton) {
    (releaseBaton as () => void)();
  }
  await page.waitForFunction(
    () => (window as unknown as Beyond20HarnessWindow)._isContributor === false,
  );

  await page.evaluate(() => {
    document.dispatchEvent(
      new CustomEvent('Beyond20_RenderedRoll', {
        detail: [{ title: 'Blocked Roll', attack_rolls: [{ total: 12 }] }],
      }),
    );
    document.dispatchEvent(
      new CustomEvent('Beyond20_UpdateHP', {
        detail: [{}, 'ViewerChar', 5, 10, 0],
      }),
    );
  });
  await page.waitForTimeout(50);
  expect(postedToChat).toHaveLength(1);
});

test('/canvas grants Beyond20 permission when baton state lists user in contributors', async ({
  canvas,
  page,
}) => {
  const postedToChat: ChatPostPayload[] = [];

  await canvas.openCanvas({
    isOrator: false,
    collabEnabled: false,
    user: { id: 10, username: 'viewer' },
    apiRoutes: {
      '/api/chat': async (route: Route): Promise<void> => {
        if (route.request().method() === 'POST') {
          const payload = JSON.parse(route.request().postData() || '{}') as ChatPostPayload;
          postedToChat.push(payload);
          await route.fulfill({ json: { type: 'chat', ...payload } });
          return;
        }
        await route.fulfill({ json: [] });
      },
      '/api/theaters/stage': {
        metadata: { is_owner: false, is_active_orator: false, is_contributor: false },
      },
      '/api/theaters/stage/baton': {
        owner: { id: 1, username: 'owner' },
        active_orator: { id: 1, username: 'owner' },
        contributors: [{ id: 10, username: 'viewer' }],
        viewer_collab_enabled: false,
      },
    },
  });

  await page.waitForFunction(
    () => (window as unknown as Beyond20HarnessWindow)._isContributor === true,
  );

  await page.evaluate(() => {
    document.dispatchEvent(
      new CustomEvent('Beyond20_RenderedRoll', {
        detail: [{ character: { name: 'BatonContrib' }, title: 'Religion', attack_rolls: [{ total: 18 }] }],
      }),
    );
  });
  await expect.poll(() => postedToChat.length).toBe(1);
  expect(postedToChat[0]?.roll_data?.forwarded_to_agent).toBe(false);
});

test('roll HTML is sanitized in canvas chat-controller renderer', async ({ canvas, page }) => {
  await canvas.serveRoute('**/api/auth/me', async (route: Route) => {
    await route.fulfill({ json: { authenticated: true, user: { username: 'Ada' } } });
  });
  await canvas.serveRoute('**/api/chat*', async (route: Route) => {
    await route.fulfill({ json: [] });
  });
  await canvas.serveRoute('**/api/suggestions*', async (route: Route) => {
    await route.fulfill({ json: [] });
  });

  await canvas.mount({ html: '<div id="chat-messages"></div>' });

  const result = await page.evaluate(
    async ({
      longswordMsg,
      stealthMsg,
    }: {
      longswordMsg: RollMessageFixture;
      stealthMsg: RollMessageFixture;
    }) => {
      const win = window as unknown as Beyond20HarnessWindow;
      win.auditRoll = 0;
      const mod = await window.__importModule<ChatControllerModule>('/static/js/chat-controller.js');
      const container = document.getElementById('chat-messages');
      if (!container) throw new Error('Missing #chat-messages');

      const controller = mod.initializeChatController({
        messagesContainer: container,
        getAuthState: async () => ({ authenticated: true, user: { username: 'Ada' } }),
      });

      const rendered = controller.renderChatMessage(longswordMsg);
      container.appendChild(rendered);
      const fallbackRendered = controller.renderChatMessage(stealthMsg);
      container.appendChild(fallbackRendered);

      return {
        auditRoll: win.auditRoll,
        hasImg: container.querySelectorAll('img').length,
        hasSvg: container.querySelectorAll('svg').length,
        hasScript: container.querySelectorAll('script').length,
        hasIframe: container.querySelectorAll('iframe').length,
        hasAnchor: container.querySelectorAll('a').length,
        rollResultText: container.querySelector('.beyond20-roll-result')?.textContent ?? null,
        rollResultTitle: container.querySelector('.beyond20-roll-result')?.getAttribute('title') ?? null,
        totalDamageText: container.querySelector('.beyond20-total-damage')?.textContent ?? null,
        fallbackBadgeText: fallbackRendered.querySelector('.roll-badge')?.textContent ?? null,
      };
    },
    { longswordMsg: MALICIOUS_LONGSWORD_MESSAGE, stealthMsg: PURE_SCRIPT_STEALTH_MESSAGE },
  );

  await page.waitForTimeout(50);
  expect(await page.evaluate(() => (window as unknown as Beyond20HarnessWindow).auditRoll)).toBe(0);
  expect(result).toEqual({
    auditRoll: 0,
    hasImg: 0,
    hasSvg: 0,
    hasScript: 0,
    hasIframe: 0,
    hasAnchor: 0,
    rollResultText: '23',
    rollResultTitle: '1d20 + 5',
    totalDamageText: '8 Slashing',
    fallbackBadgeText: '🎯 19',
  });
});

test('roll HTML is sanitized in popout renderer', async ({ canvas, page }) => {
  const popoutHtml = await fs.promises.readFile(path.join(TEMPLATES_DIR, 'popout.html'), 'utf-8');

  await page.addInitScript(() => {
    (window as unknown as Beyond20HarnessWindow).auditRoll = 0;
  });

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/popout',
    async (route: Route) => {
      await route.fulfill({ body: popoutHtml, contentType: 'text/html; charset=utf-8' });
    },
  );
  await canvas.serveRoute('https://fonts.googleapis.com/**', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'text/css; charset=utf-8', body: '' });
  });
  await canvas.serveRoute(`${CANVAS_ORIGIN}/favicon.png`, async (route: Route) => {
    await route.fulfill({ status: 204 });
  });
  await canvas.serveRoute('**/api/auth/me', async (route: Route) => {
    await route.fulfill({ json: { authenticated: true, user: { username: 'Ada' } } });
  });
  await canvas.serveRoute('**/api/latest*', async (route: Route) => {
    await route.fulfill({ json: {} });
  });
  await canvas.serveRoute('**/api/chat*', async (route: Route) => {
    await route.fulfill({
      json: [MALICIOUS_LONGSWORD_MESSAGE, PURE_SCRIPT_STEALTH_MESSAGE],
    });
  });
  await page.routeWebSocket(/wss?:\/\/canvas\.test\/ws\/.*/, (_ws: WebSocketRoute) => {
    // Keep WebSocket open/idle in test isolation.
  });

  await page.goto(`${CANVAS_ORIGIN}/popout?theater_id=stage`);

  const container = page.locator('#chat-messages');
  await expect(container.locator('.chat-roll-message')).toHaveCount(2);
  await page.waitForTimeout(50);

  const result = await page.evaluate(() => {
    const win = window as unknown as Beyond20HarnessWindow;
    const chatContainer = document.getElementById('chat-messages');
    if (!chatContainer) throw new Error('Missing #chat-messages');
    const messages = chatContainer.querySelectorAll('.chat-roll-message');
    return {
      auditRoll: win.auditRoll,
      hasImg: chatContainer.querySelectorAll('img').length,
      hasSvg: chatContainer.querySelectorAll('svg').length,
      hasScript: chatContainer.querySelectorAll('script').length,
      hasIframe: chatContainer.querySelectorAll('iframe').length,
      hasAnchor: chatContainer.querySelectorAll('a').length,
      rollResultText: chatContainer.querySelector('.beyond20-roll-result')?.textContent ?? null,
      rollResultTitle: chatContainer.querySelector('.beyond20-roll-result')?.getAttribute('title') ?? null,
      totalDamageText: chatContainer.querySelector('.beyond20-total-damage')?.textContent ?? null,
      fallbackBadgeText: messages[1]?.querySelector('.roll-badge')?.textContent ?? null,
    };
  });

  expect(result).toEqual({
    auditRoll: 0,
    hasImg: 0,
    hasSvg: 0,
    hasScript: 0,
    hasIframe: 0,
    hasAnchor: 0,
    rollResultText: '23',
    rollResultTitle: '1d20 + 5',
    totalDamageText: '8 Slashing',
    fallbackBadgeText: '🎯 19',
  });
});

test('deprecated shim re-exports canonical symbols and works end-to-end', async ({
  canvas,
  page,
}) => {
  await mountListenerHarness(canvas, page, {
    moduleUrl: SHIM_URL,
    contributor: true,
    collab: false,
  });

  const sameReferences = await page.evaluate(
    async ({ canonicalUrl, shimUrl }: { canonicalUrl: string; shimUrl: string }) => {
      const canonical = await window.__importModule<Beyond20Module>(canonicalUrl);
      const shim = await window.__importModule<Beyond20Module>(shimUrl);
      return {
        formatSame: canonical.formatBeyond20RollSummary === shim.formatBeyond20RollSummary,
        initSame: canonical.initializeBeyond20Listener === shim.initializeBeyond20Listener,
      };
    },
    { canonicalUrl: CANONICAL_URL, shimUrl: SHIM_URL },
  );
  expect(sameReferences).toEqual({ formatSame: true, initSame: true });

  await page.evaluate(() => {
    document.dispatchEvent(
      new CustomEvent('Beyond20_RenderedRoll', {
        detail: [{ character: { name: 'ShimHero' }, title: 'Initiative', attack_rolls: [{ total: 20 }] }],
      }),
    );
  });

  const posted = await page.evaluate(
    () => (window as unknown as Beyond20HarnessWindow).postedMessages,
  );
  expect(posted).toHaveLength(1);
  expect(posted[0]?.text).toBe('🎲 ShimHero rolled Initiative [20]');
});
