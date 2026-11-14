import type { Route } from '@playwright/test';
import { expect, test } from '../../testing/ui/canvas_fixtures';
import type {
  ChatApplySettingsResult,
  ChatBroadcastMessage,
  ChatController,
  ChatControllerOptions,
  ChatMessage,
} from './chat-controller.js';

type ChatControllerModule = typeof import('/static/generated/canvas/chat-controller.js');
type ChatEmotesModule = typeof import('/static/generated/canvas/chat-emotes.js');

interface ChatControllerHarnessWindow extends Window {
  controller?: ChatController;
  broadcasts?: ChatBroadcastMessage[];
  wsSent?: string[];
  loginOpens?: number;
  settingsAppliedResults?: ChatApplySettingsResult[];
  isOwner?: boolean;
  isOrator?: boolean;
  wsReadyState?: number;
}

const COMPILED_CONTROLLER_URL = '/static/generated/canvas/chat-controller.js';
const COMPILED_EMOTES_URL = '/static/generated/canvas/chat-emotes.js';

const COMPOSER_SHELL_HTML = `
  <div id="chat-container">
    <div id="chat-messages" style="height: 200px; overflow-y: auto;"></div>
    <form id="chat-form" class="canvas-chat-composer">
      <textarea id="chat-input" rows="2" aria-label="Chat message"></textarea>
      <div class="chat-composer-actions">
        <div class="chat-emotes-control">
          <button type="button" data-chat-emotes-toggle aria-label="Emotes" aria-expanded="false" aria-controls="chat-emotes-palette">😀</button>
          <div id="chat-emotes-palette" class="chat-emotes-palette" role="group" aria-label="Emotes palette" hidden></div>
        </div>
        <button type="submit" data-chat-command="help" title="Find basic help and documentation (free)" aria-label="Find basic help and documentation (free)">?</button>
        <button type="submit" data-chat-command="suggest" aria-label="Send suggestion">💡</button>
        <button type="submit" aria-label="Send">Send</button>
      </div>
    </form>
  </div>
`;

test('pure helpers: generateFunUsername, getAuthorColor, and sanitizeRollHtmlToFragment', async ({
  canvas,
  page,
}) => {
  await canvas.mount();
  const result = await page.evaluate(async (moduleUrl: string) => {
    const mod = await window.__importModule<ChatControllerModule>(moduleUrl);
    const username = mod.generateFunUsername();
    const narratronColors = [
      mod.getAuthorColor(''),
      mod.getAuthorColor(null),
      mod.getAuthorColor(undefined),
      mod.getAuthorColor('agent'),
      mod.getAuthorColor('Agent'),
      mod.getAuthorColor('narratron'),
      mod.getAuthorColor('Narratron'),
    ];
    const aliceColor1 = mod.getAuthorColor('Alice');
    const aliceColor2 = mod.getAuthorColor('Alice');
    const bobColor = mod.getAuthorColor('Bob');

    const emptyFrag = mod.sanitizeRollHtmlToFragment('   ');
    const sanitizedFrag = mod.sanitizeRollHtmlToFragment(`
      <div class="card valid_class bad$class" title="  Roll details  ">
        <script>window.pwned = true;</script>
        <svg><circle /></svg>
        <a href="https://evil.test">Link text</a>
        <table><tr><th colspan="2" rowspan="99">Header</th><td rowspan="3">Cell</td></tr></table>
        <details open><summary>Details</summary><span>Body</span></details>
      </div>
    `);
    const host = document.createElement('div');
    host.appendChild(sanitizedFrag);

    return {
      username,
      narratronColors,
      aliceColor1,
      aliceColor2,
      bobColor,
      emptyCount: emptyFrag.childNodes.length,
      hasScript: host.querySelectorAll('script').length,
      hasSvg: host.querySelectorAll('svg').length,
      hasAnchor: host.querySelectorAll('a').length,
      linkTextPreserved: host.textContent?.includes('Link text') ?? false,
      cardClass: host.querySelector('.card')?.getAttribute('class') ?? null,
      cardTitle: host.querySelector('.card')?.getAttribute('title') ?? null,
      thColspan: host.querySelector('th')?.getAttribute('colspan') ?? null,
      thRowspan: host.querySelector('th')?.getAttribute('rowspan') ?? null,
      tdRowspan: host.querySelector('td')?.getAttribute('rowspan') ?? null,
      detailsOpen: host.querySelector('details')?.hasAttribute('open') ?? false,
    };
  }, COMPILED_CONTROLLER_URL);

  expect(result.username).toMatch(/^[A-Za-z]+ [A-Za-z]+ \d{2}$/);
  expect(result.narratronColors).toEqual(Array(7).fill('#ffffff'));
  expect(result.aliceColor1).toMatch(/^hsl\(\d+, 85%, 68%\)$/);
  expect(result.aliceColor1).toBe(result.aliceColor2);
  expect(result.bobColor).not.toBe(result.aliceColor1);
  expect(result.emptyCount).toBe(0);
  expect(result.hasScript).toBe(0);
  expect(result.hasSvg).toBe(0);
  expect(result.hasAnchor).toBe(0);
  expect(result.linkTextPreserved).toBe(true);
  expect(result.cardClass).toBe('card valid_class');
  expect(result.cardTitle).toBe('Roll details');
  expect(result.thColspan).toBe('2');
  expect(result.thRowspan).toBeNull();
  expect(result.tdRowspan).toBe('3');
  expect(result.detailsOpen).toBe(true);
});

test('initializeChatEmotes validates elements, inserts emotes, handles focus, and closes via timer/escape/outside pointerdown', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    stylesheets: ['/static/css/chat.css'],
    html: `
      ${COMPOSER_SHELL_HTML}
      <form id="broken-form"><input id="broken-input" /></form>
      <div id="outside" style="width: 50px; height: 50px;">outside</div>
    `,
  });

  await page.clock.install({ time: 0 });
  await page.clock.pauseAt(1000);

  const missingElementsThrew = await page.evaluate(async (moduleUrl: string) => {
    const { initializeChatEmotes } = await window.__importModule<ChatEmotesModule>(moduleUrl);
    const form = document.getElementById('chat-form') as HTMLFormElement;
    const input = document.getElementById('chat-input') as HTMLTextAreaElement;
    initializeChatEmotes(form, input);

    const brokenForm = document.getElementById('broken-form') as HTMLFormElement;
    const brokenInput = document.getElementById('broken-input') as HTMLInputElement;
    try {
      initializeChatEmotes(brokenForm, brokenInput);
      return false;
    } catch {
      return true;
    }
  }, COMPILED_EMOTES_URL);

  expect(missingElementsThrew).toBe(true);

  const toggle = page.getByRole('button', { name: 'Emotes', exact: true });
  const palette = page.getByRole('group', { name: 'Emotes palette' });
  const input = page.getByRole('textbox', { name: 'Chat message' });

  await expect(page.locator('#chat-emotes-palette button')).toHaveCount(24);
  await expect(palette).toBeHidden();

  await toggle.click();
  await expect(palette).toBeVisible();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(palette.getByRole('button', { name: 'Smile', exact: true })).toBeFocused();

  // Clicking inside the form does not close the palette.
  await page.locator('#chat-form').dispatchEvent('pointerdown', { bubbles: true });
  await expect(palette).toBeVisible();

  // Clicking outside closes the palette.
  await page.locator('#outside').dispatchEvent('pointerdown', { bubbles: true });
  await expect(palette).toBeHidden();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');

  // Reopen, click an emote while focused inside palette, wait 10s -> focuses input and closes.
  await toggle.click();
  await palette.getByRole('button', { name: 'Fire', exact: true }).focus();
  await page.clock.fastForward(10000);
  await expect(palette).toBeHidden();
  await expect(input).toBeFocused();

  // Escape closes and returns focus to toggle.
  await toggle.click();
  await page.keyboard.press('Escape');
  await expect(palette).toBeHidden();
  await expect(toggle).toBeFocused();
});

test('renderChatMessage renders text, profile links, suggestions, voting, withdraw, and roll fallbacks', async ({
  canvas,
  page,
}) => {
  const apiCalls: Array<{ path: string; body: Record<string, unknown> }> = [];
  const suggestionsPayload: Array<{ author: string; upvote_count: number; upvoters: string[] }> = [
    { author: 'bob', upvote_count: 2, upvoters: ['alice'] },
  ];

  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname.startsWith('/api/'),
    async (route: Route) => {
      const url = new URL(route.request().url());
      if (route.request().method() === 'POST') {
        const body = JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>;
        apiCalls.push({ path: url.pathname, body });
        await route.fulfill({ json: { ok: true } });
        return;
      }
      if (url.pathname === '/api/suggestions') {
        await route.fulfill({ json: suggestionsPayload });
        return;
      }
      await route.fulfill({ json: [] });
    },
  );

  await canvas.mount({
    stylesheets: ['/static/css/chat.css'],
    html: '<div id="chat-messages"></div>',
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { initializeChatController } =
      await window.__importModule<ChatControllerModule>(moduleUrl);
    const win = window as unknown as ChatControllerHarnessWindow;
    const container = document.getElementById('chat-messages');
    const controller = initializeChatController({
      messagesContainer: container,
      theaterId: 'stage',
      getAuthState: async () => ({ authenticated: true, user: { username: 'alice' } }),
    });
    await controller.initChatUser();
    await controller.fetchChat();
    win.controller = controller;

    const messages: ChatMessage[] = [
      { author: 'agent', text: 'Welcome!' },
      {
        author: 'alice',
        text: '<b>literal</b>',
        profile_color: '#12ab34',
        profile_username: 'alice_p',
      },
      { author: 'alice', text: 'My idea', type: 'suggestion' },
      { author: 'bob', text: 'Bob idea', type: 'suggestion' },
      {
        author: 'bob',
        text: '🎲 fallback',
        roll_data: {
          attack_rolls: [
            { total: 25, is_crit: true, formula: '1d20+5' },
            { result: 1, rolls: [{ rolls: [1] }] },
          ],
          damage_rolls: [['Fire', { total: 12 }]],
        },
      },
      {
        author: 'bob',
        text: '🎲 total damage',
        roll_data: {
          total_damages: { Total: { total: 18 }, Cold: 4 },
        },
      },
      {
        author: 'bob',
        text: '🎲 Rolled Stealth 15',
        roll_data: { html: '<script>evil()</script>' },
      },
    ];

    for (const msg of messages) {
      container!.appendChild(controller.renderChatMessage(msg));
    }
  }, COMPILED_CONTROLLER_URL);

  const msgLocators = page.locator('#chat-messages .chat-message');
  await expect(msgLocators).toHaveCount(7);

  // Narratron prefix & white color
  await expect(msgLocators.nth(0).locator('.chat-prefix')).toHaveText('Narratron:');
  const agentColor = await msgLocators
    .nth(0)
    .locator('.chat-prefix')
    .evaluate((el) => getComputedStyle(el).color);
  expect(agentColor).toBe('rgb(255, 255, 255)');

  // Profile link, valid hex color, and literal text
  const alicePrefix = msgLocators.nth(1).locator('a.chat-prefix');
  await expect(alicePrefix).toHaveAttribute('href', '/users/alice_p');
  await expect(msgLocators.nth(1).locator('.chat-text')).toHaveText('<b>literal</b>');
  expect(await alicePrefix.evaluate((el) => getComputedStyle(el).color)).toBe('rgb(18, 171, 52)');

  // Roll fallback badges and summary
  await expect(msgLocators.nth(4).locator('.attack-roll.crit-hit')).toHaveText('🎯 25');
  await expect(msgLocators.nth(4).locator('.attack-roll.crit-hit')).toHaveAttribute(
    'title',
    'Formula: 1d20+5',
  );
  await expect(msgLocators.nth(4).locator('.attack-roll.crit-fail')).toHaveText('🎯 1');
  await expect(msgLocators.nth(4).locator('.damage-roll')).toHaveText('💥 12 Fire');

  await expect(msgLocators.nth(5).locator('.damage-roll').nth(0)).toHaveText('💥 18');
  await expect(msgLocators.nth(5).locator('.damage-roll').nth(1)).toHaveText('💥 4 Cold');
  await expect(msgLocators.nth(6).locator('.chat-roll-summary-text')).toHaveText('Rolled Stealth 15');

  // setSuggestionsHidden toggles class
  await page.evaluate(() => {
    (window as unknown as ChatControllerHarnessWindow).controller!.setSuggestionsHidden(true);
  });
  await expect(page.locator('#chat-messages')).toHaveClass(/suggestions-hidden/);
  await page.evaluate(() => {
    (window as unknown as ChatControllerHarnessWindow).controller!.setSuggestionsHidden(false);
  });
  await expect(page.locator('#chat-messages')).not.toHaveClass(/suggestions-hidden/);

  // Own suggestion has disabled upvote and withdraw button; other's suggestion has enabled upvote
  const aliceSuggestion = page.locator('[data-suggestion-author="alice"]');
  const bobSuggestion = page.locator('[data-suggestion-author="bob"]');
  await expect(aliceSuggestion.locator('.suggestion-upvote-btn')).toBeDisabled();
  await expect(aliceSuggestion.locator('.suggestion-withdraw-btn')).toBeVisible();
  await expect(bobSuggestion.locator('.suggestion-upvote-btn')).toBeEnabled();

  await bobSuggestion.locator('.suggestion-upvote-btn').click();
  await expect.poll(() => apiCalls.length).toBe(1);
  expect(apiCalls[0]).toEqual({
    path: '/api/suggestions/upvote',
    body: { voter: 'alice', target_author: 'bob' },
  });
  await expect(bobSuggestion.locator('[data-vote-count-for="bob"]')).toHaveText('2 votes');
  await expect(bobSuggestion.locator('.suggestion-upvote-btn')).toHaveClass(/voted/);

  await aliceSuggestion.locator('.suggestion-withdraw-btn').click();
  await expect.poll(() => apiCalls.length).toBe(2);
  expect(apiCalls[1]).toEqual({
    path: '/api/suggestions/withdraw',
    body: { author: 'alice' },
  });
});

test('user_help settings proposal is owner-only and personalized help upgrade retries cleanly', async ({
  canvas,
  page,
}) => {
  let applyAttempt = 0;
  let personalizedCalls = 0;
  const helpWithProposal: ChatMessage = {
    type: 'user_help',
    author: 'Narratron User Help',
    text: 'Review settings.',
    html: '<p>Review settings <a href="https://example.test/docs">docs</a>.</p>',
    settings_proposal: {
      config_digest: 'a'.repeat(64),
      changes: [{ setting: 'image_generation.enabled', enabled: false }],
    },
    settings_summary: ['Disable AI image generation'],
    help_mode: 'basic',
    help_question: 'How do I disable images?',
    can_personalize: true,
    personalized_credit_cost: 0.25,
  };
  const chatMessages: ChatMessage[] = [helpWithProposal];

  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname.startsWith('/api/'),
    async (route: Route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/chat') {
        await route.fulfill({ json: chatMessages });
        return;
      }
      if (url.pathname === '/api/user-help/stage/apply-settings') {
        applyAttempt += 1;
        if (applyAttempt === 1) {
          await route.fulfill({
            status: 409,
            json: { detail: 'Settings changed. Ask again.' },
          });
          return;
        }
        await route.fulfill({
          json: {
            applied: true,
            agent_running: true,
            message: 'Settings saved and agent restarted.',
          },
        });
        return;
      }
      if (url.pathname === '/api/user-help') {
        personalizedCalls += 1;
        await route.fulfill({
          json: {
            type: 'user_help',
            text: 'Personalized details',
            html: '<p>Personalized details</p>',
            help_mode: 'personalized',
          },
        });
        return;
      }
      await route.fulfill({ json: [] });
    },
  );

  await canvas.mount({
    stylesheets: ['/static/css/chat.css'],
    html: COMPOSER_SHELL_HTML,
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { initializeChatController } =
      await window.__importModule<ChatControllerModule>(moduleUrl);
    const win = window as unknown as ChatControllerHarnessWindow;
    win.isOwner = false;
    win.settingsAppliedResults = [];
    const container = document.getElementById('chat-messages')!;
    const controller = initializeChatController({
      messagesContainer: container,
      chatForm: document.getElementById('chat-form') as HTMLFormElement | null,
      chatInput: document.getElementById('chat-input') as HTMLTextAreaElement | null,
      theaterId: 'stage',
      isTheaterOwner: () => Boolean(win.isOwner),
      onSettingsApplied: (result) => {
        win.settingsAppliedResults?.push(result);
      },
      getAuthState: async () => ({ authenticated: true, user: { username: 'owner' } }),
    });
    await controller.fetchChat();
    win.controller = controller;
  }, COMPILED_CONTROLLER_URL);

  await expect(page.getByRole('button', { name: 'Apply settings and restart agent' })).toHaveCount(
    0,
  );
  await expect(page.locator('.help-markdown a')).toHaveAttribute('target', '_blank');
  await expect(page.locator('.help-markdown a')).toHaveAttribute('rel', 'noopener noreferrer');

  // Owner sees proposal, retries after 409 error, and succeeds on second click.
  await page.evaluate(async () => {
    const win = window as unknown as ChatControllerHarnessWindow;
    win.isOwner = true;
    await win.controller!.fetchChat();
  });

  const applyBtn = page.getByRole('button', { name: 'Apply settings and restart agent' });
  await expect(applyBtn).toBeVisible();
  await applyBtn.click();
  await expect(page.getByRole('status')).toHaveText('Settings changed. Ask again.');
  await expect(applyBtn).toBeEnabled();

  await applyBtn.click();
  await expect(page.getByRole('status')).toHaveText('Settings saved and agent restarted.');
  await expect(page.getByRole('button', { name: 'Settings applied' })).toBeDisabled();
  expect(
    await page.evaluate(
      () => (window as unknown as ChatControllerHarnessWindow).settingsAppliedResults?.length,
    ),
  ).toBe(1);

  // Personalized help upgrade button triggers /api/user-help with personalized: true.
  const upgradeBtn = page.getByRole('button', { name: 'Personalized help · 0.25 cr' });
  await upgradeBtn.click();
  await expect.poll(() => personalizedCalls).toBe(1);
  await expect(page.locator('#chat-messages')).toContainText('Personalized details');
});

test('submission pipeline forwards orator non-suggestion messages, handles /help errors, and preserves scroll', async ({
  canvas,
  page,
}) => {
  let chatMessages: ChatMessage[] = Array.from({ length: 20 }, (_, i) => ({
    author: 'System',
    text: `History line ${i}`,
  }));
  let helpStatus = 200;

  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname.startsWith('/api/'),
    async (route: Route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/chat') {
        if (route.request().method() === 'POST') {
          const payload = JSON.parse(route.request().postData() ?? '{}') as {
            author: string;
            text: string;
          };
          const isSuggestion = payload.text.startsWith('/suggest');
          chatMessages = [
            ...chatMessages,
            {
              author: payload.author,
              text: payload.text,
              type: isSuggestion ? 'suggestion' : 'chat',
            },
          ];
          await route.fulfill({ json: { type: isSuggestion ? 'suggestion' : 'chat' } });
          return;
        }
        await route.fulfill({ json: chatMessages });
        return;
      }
      if (url.pathname === '/api/suggestions') {
        await route.fulfill({ json: [] });
        return;
      }
      if (url.pathname === '/api/user-help') {
        if (helpStatus === 401) {
          await route.fulfill({ status: 401, json: { detail: 'Login required' } });
          return;
        }
        await route.fulfill({
          json: {
            type: 'user_help',
            text: 'Help reply',
            html: '<p>Help reply</p>',
            help_mode: 'basic',
            can_personalize: false,
          },
        });
        return;
      }
      await route.fulfill({ json: [] });
    },
  );

  await canvas.mount({
    stylesheets: ['/static/css/chat.css'],
    html: COMPOSER_SHELL_HTML,
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { initializeChatController } =
      await window.__importModule<ChatControllerModule>(moduleUrl);
    const win = window as unknown as ChatControllerHarnessWindow;
    win.broadcasts = [];
    win.wsSent = [];
    win.loginOpens = 0;
    win.isOrator = true;
    win.wsReadyState = WebSocket.OPEN;

    const options: ChatControllerOptions = {
      messagesContainer: document.getElementById('chat-messages'),
      chatForm: document.getElementById('chat-form') as HTMLFormElement | null,
      chatInput: document.getElementById('chat-input') as HTMLTextAreaElement | null,
      theaterId: 'stage',
      isCurrentOrator: () => Boolean(win.isOrator),
      onOpenLogin: () => {
        win.loginOpens = (win.loginOpens ?? 0) + 1;
      },
      getAgentWs: () => ({
        readyState: win.wsReadyState ?? WebSocket.CLOSED,
        send: (data: string) => {
          win.wsSent?.push(data);
        },
      }),
      getAuthState: async () => ({ authenticated: true, user: { username: 'orator' } }),
      popoutBroadcast: {
        postMessage: (msg: ChatBroadcastMessage) => {
          win.broadcasts?.push(msg);
        },
      },
    };
    win.controller = initializeChatController(options);
  }, COMPILED_CONTROLLER_URL);

  const messagesEl = page.locator('#chat-messages');
  await expect(messagesEl.locator('.chat-message')).toHaveCount(20);
  // Initial rendering schedules its follow-to-bottom update on the next frame.
  await expect.poll(() => messagesEl.evaluate((el) =>
    el.scrollTop + el.clientHeight >= el.scrollHeight,
  )).toBe(true);

  // Scroll up to top; a background fetchChat(false) must preserve scrollTop = 0.
  await messagesEl.evaluate((el) => {
    el.scrollTop = 0;
  });
  await page.evaluate(async () => {
    await (window as unknown as ChatControllerHarnessWindow).controller!.fetchChat(false);
  });
  expect(await messagesEl.evaluate((el) => el.scrollTop)).toBe(0);

  // Regular chat message by orator forwards to agentWs and broadcasts.
  const chatInput = page.getByRole('textbox', { name: 'Chat message' });
  await chatInput.fill('Hello agent');
  await chatInput.press('Enter');
  await expect(messagesEl.locator('.chat-message')).toHaveCount(21);
  await expect.poll(
    () => page.evaluate(() => (window as unknown as ChatControllerHarnessWindow).wsSent),
  ).toEqual([JSON.stringify({ type: 'text', text: 'Hello agent' })]);
  await expect.poll(
    () => page.evaluate(() => (window as unknown as ChatControllerHarnessWindow).broadcasts),
  ).toEqual([{ type: 'chat_sent', text: 'Hello agent' }]);

  // Suggestion does NOT forward to agentWs.
  await chatInput.fill('Open the gate');
  await chatInput.press('Control+Enter');
  await expect(messagesEl.locator('.chat-message')).toHaveCount(22);
  await expect.poll(
    () => page.evaluate(() => (window as unknown as ChatControllerHarnessWindow).broadcasts),
  ).toHaveLength(2);
  expect(
    await page.evaluate(() => (window as unknown as ChatControllerHarnessWindow).wsSent),
  ).toHaveLength(1);

  // Empty /help restores input without /help prefix and adds error message.
  await chatInput.fill('/help   ');
  await chatInput.press('Enter');
  await expect(messagesEl.locator('.chat-message').last()).toContainText(
    'Enter a question after /help.',
  );
  await expect(chatInput).toHaveValue('');

  // 401 on /help opens login and restores question in input.
  helpStatus = 401;
  await chatInput.fill('/help Why did it fail?');
  await chatInput.press('Enter');
  await expect(messagesEl.locator('.chat-message').last()).toContainText('Login required');
  await expect(chatInput).toHaveValue('Why did it fail?');
  expect(
    await page.evaluate(() => (window as unknown as ChatControllerHarnessWindow).loginOpens),
  ).toBe(1);

  // Successful basic help shows sign-in upgrade button; clicking it populates input and calls onOpenLogin.
  helpStatus = 200;
  await chatInput.fill('/help How do pins work?');
  await chatInput.press('Enter');
  const signInHelpBtn = page.getByRole('button', { name: 'Sign in for personalized help' });
  await expect(signInHelpBtn).toBeVisible();
  await signInHelpBtn.click();
  await expect(chatInput).toHaveValue('How do pins work?');
  expect(
    await page.evaluate(() => (window as unknown as ChatControllerHarnessWindow).loginOpens),
  ).toBe(2);
});
