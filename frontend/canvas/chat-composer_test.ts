import fs from 'node:fs';
import path from 'node:path';
import { expect, TEMPLATES_DIR, test } from '../../testing/ui/canvas_fixtures';

interface ChatMessagePayload {
  author: string;
  text: string;
  type?: string;
}

interface ChatController {
  renderChatMessage(msg: ChatMessagePayload): HTMLElement;
  setChatUsername(newName: string): void;
}

interface ChatControllerModule {
  initializeChatController(options: {
    messagesContainer: HTMLElement | null;
    chatForm: HTMLFormElement | null;
    chatInput: HTMLTextAreaElement | null;
    getAuthState: () => Promise<{ authenticated: boolean; user: { username: string } }>;
  }): ChatController;
}

interface ComposerHarnessWindow extends Window {
  controller?: ChatController;
}

function loadCanvasChatFormHtml(): string {
  const template = fs.readFileSync(path.join(TEMPLATES_DIR, 'canvas.html'), 'utf-8');
  const match = template.match(/<form id="chat-form"[^>]*>[\s\S]*?<\/form>/);
  if (!match) {
    throw new Error('Could not locate <form id="chat-form"> in templates/canvas.html');
  }
  return match[0];
}

const CANVAS_CHAT_FORM_HTML = loadCanvasChatFormHtml();
const VIEWS = ['canvas', 'popout'] as const;

for (const view of VIEWS) {
  test(`${view} chat composer sends suggestions, handles emotes, and wraps text`, async ({
    canvas,
    page,
  }) => {
    const sentPayloads: Array<{ author: string; text: string }> = [];

    await canvas.serveRoute(
      (url) => url.origin === canvas.origin && url.pathname === '/api/chat',
      async (route) => {
        if (route.request().method() === 'POST') {
          const payload = JSON.parse(route.request().postData() ?? '{}') as {
            author: string;
            text: string;
          };
          sentPayloads.push(payload);
          await route.fulfill({ json: { type: 'chat' } });
          return;
        }
        await route.fulfill({ json: [] });
      },
    );
    await canvas.serveRoute(
      (url) => url.origin === canvas.origin && url.pathname === '/api/suggestions',
      async (route) => {
        await route.fulfill({ json: [] });
      },
    );

    if (view === 'canvas') {
      await canvas.mount({
        viewport: { width: 360, height: 700 },
        stylesheets: ['/static/css/canvas.css', '/static/css/chat.css'],
        html: `<div id="chat-messages" style="height: 350px"></div>${CANVAS_CHAT_FORM_HTML}`,
      });

      await page.evaluate(async () => {
        const { initializeChatController } = await window.__importModule<ChatControllerModule>(
          '/static/generated/canvas/chat-controller.js',
        );
        const win = window as unknown as ComposerHarnessWindow;
        win.controller = initializeChatController({
          messagesContainer: document.getElementById('chat-messages'),
          chatForm: document.getElementById('chat-form') as HTMLFormElement | null,
          chatInput: document.getElementById('chat-input') as HTMLTextAreaElement | null,
          getAuthState: async () => ({ authenticated: true, user: { username: 'alice' } }),
        });
      });
    } else {
      await page.setViewportSize({ width: 360, height: 700 });
      const popoutHtml = await fs.promises.readFile(path.join(TEMPLATES_DIR, 'popout.html'), 'utf-8');
      await canvas.serveRoute(
        (url) => url.origin === canvas.origin && url.pathname === '/popout',
        async (route) => {
          await route.fulfill({ body: popoutHtml, contentType: 'text/html; charset=utf-8' });
        },
      );
      await canvas.serveRoute('https://fonts.googleapis.com/**', async (route) => {
        await route.fulfill({ status: 200, contentType: 'text/css; charset=utf-8', body: '' });
      });
      await canvas.serveRoute(`${canvas.origin}/favicon.png`, async (route) => {
        await route.fulfill({ status: 204 });
      });
      await canvas.serveRoute(
        (url) => url.origin === canvas.origin && url.pathname === '/api/auth/me',
        async (route) => {
          await route.fulfill({ json: { authenticated: true, user: { username: 'alice' } } });
        },
      );
      await canvas.serveRoute(
        (url) => url.origin === canvas.origin && url.pathname === '/api/latest',
        async (route) => {
          await route.fulfill({ json: {} });
        },
      );
      await page.routeWebSocket(/wss?:\/\/canvas\.test\/ws\/.*/, (ws) => {
        ws.send(JSON.stringify({ type: 'state_ready' }));
      });
      await page.goto(`${canvas.origin}/popout?theater_id=stage`);
    }

    const chatInput = page.getByRole('textbox', { name: 'Chat message' });
    const suggestBtn = page.getByRole('button', { name: 'Send suggestion' });
    const sendBtn = page.getByRole('button', { name: 'Send', exact: true });

    const layoutOk = await page.evaluate(() => {
      const input = document.getElementById('chat-input')!.getBoundingClientRect();
      const actions = document.querySelector('.chat-composer-actions')!.getBoundingClientRect();
      const buttons = Array.from(
        document.querySelectorAll('.chat-composer-actions > button, [data-chat-emotes-toggle]'),
      ).map((button) => button.getBoundingClientRect());
      return (
        actions.top >= input.bottom &&
        Math.abs(actions.width - input.width) < 1 &&
        buttons.every((button) => button.left >= input.left && button.right <= input.right + 1) &&
        Math.abs(buttons[0].top - buttons[1].top) < 1
      );
    });
    expect(layoutOk).toBe(true);

    // Empty submission is a no-op.
    await suggestBtn.click();
    expect(sentPayloads).toHaveLength(0);

    // Suggest button prepends /suggest when absent, preserves when already present.
    await chatInput.fill('Explore the moon');
    await suggestBtn.click();
    await expect.poll(() => sentPayloads.length).toBe(1);
    expect(sentPayloads[0].text).toBe('/suggest Explore the moon');
    await expect(chatInput).toHaveValue('');

    await chatInput.fill('/suggest Open the door');
    await suggestBtn.click();
    await expect.poll(() => sentPayloads.length).toBe(2);
    expect(sentPayloads[1].text).toBe('/suggest Open the door');

    // Regular send button and Shift+Enter multiline + Enter submit.
    await chatInput.fill('Hello everyone');
    await sendBtn.click();
    await expect.poll(() => sentPayloads.length).toBe(3);
    expect(sentPayloads[2].text).toBe('Hello everyone');

    await chatInput.fill('First line');
    await chatInput.press('Shift+Enter');
    await chatInput.press('End');
    await chatInput.pressSequentially('Second line');
    await expect(chatInput).toHaveValue('First line\nSecond line');
    expect(sentPayloads).toHaveLength(3);
    await chatInput.press('Enter');
    await expect.poll(() => sentPayloads.length).toBe(4);
    expect(sentPayloads[3].text).toBe('First line\nSecond line');

    // Ctrl+Enter submits as suggestion.
    await expect(suggestBtn).toHaveAttribute('title', 'Send as a suggestion (Ctrl+Enter)');
    await expect(suggestBtn).toHaveAttribute('aria-keyshortcuts', 'Control+Enter');
    await chatInput.fill('Follow the trail');
    await chatInput.press('Control+Enter');
    await expect.poll(() => sentPayloads.length).toBe(5);
    expect(sentPayloads[4].text).toBe('/suggest Follow the trail');
    await expect(chatInput).toHaveValue('');

    await chatInput.fill('/suggest Find shelter');
    await chatInput.press('Control+Enter');
    await expect.poll(() => sentPayloads.length).toBe(6);
    expect(sentPayloads[5].text).toBe('/suggest Find shelter');
    await chatInput.press('Control+Enter');
    expect(sentPayloads).toHaveLength(6);

    // Emotes palette interactions and 10s inactivity timer.
    await page.clock.install({ time: 0 });
    await page.clock.pauseAt(1000);
    const emotesBtn = page.getByRole('button', { name: 'Emotes', exact: true });
    const palette = page.getByRole('group', { name: 'Emotes palette' });
    await expect(palette).toBeHidden();

    await chatInput.fill('Hello world');
    await chatInput.evaluate((el: HTMLTextAreaElement) => el.setSelectionRange(6, 11));
    await emotesBtn.click();
    await expect(palette).toBeVisible();
    await expect(emotesBtn).toHaveAttribute('aria-expanded', 'true');

    const paletteGeometryOk = await palette.evaluate((el: HTMLElement) => {
      const bounds = el.getBoundingClientRect();
      const input = document.getElementById('chat-input')!.getBoundingClientRect();
      const composer = document.getElementById('chat-form')!.getBoundingClientRect();
      return (
        bounds.left >= 0 &&
        bounds.right <= window.innerWidth &&
        Math.abs(bounds.left - composer.left) < 1 &&
        Math.abs(bounds.right - composer.right) < 1 &&
        bounds.bottom < input.top
      );
    });
    expect(paletteGeometryOk).toBe(true);

    await palette.getByRole('button', { name: 'Dragon', exact: true }).click();
    await expect(chatInput).toHaveValue('Hello 🐉');
    await expect(chatInput).toBeFocused();
    await expect(palette).toBeVisible();

    await palette.getByRole('button', { name: 'Dragon', exact: true }).click();
    await expect(chatInput).toHaveValue('Hello 🐉🐉');

    await page.clock.fastForward(9000);
    await chatInput.click();
    await chatInput.pressSequentially('!');
    await page.clock.fastForward(9000);
    await expect(palette).toBeVisible();

    await palette.getByRole('button', { name: 'Dragon', exact: true }).click();
    await page.clock.fastForward(9999);
    await expect(palette).toBeVisible();
    await page.clock.fastForward(1);
    await expect(palette).toBeHidden();
    await expect(emotesBtn).toHaveAttribute('aria-expanded', 'false');
    expect(sentPayloads).toHaveLength(6);

    await chatInput.press('Enter');
    await expect.poll(() => sentPayloads.length).toBe(7);
    expect(sentPayloads[6].text).toBe('Hello 🐉🐉!🐉');

    // Escape and outside click close palette cleanly.
    await emotesBtn.click();
    await page.keyboard.press('Escape');
    await expect(palette).toBeHidden();
    await expect(emotesBtn).toBeFocused();
    await expect(emotesBtn).toHaveAttribute('aria-expanded', 'false');

    await emotesBtn.click();
    await page.mouse.click(2, 2);
    await expect(palette).toBeHidden();

    // Opening palette focuses Smile, Enter inserts emote, subsequent Enter sends message while keeping palette open.
    await emotesBtn.click();
    await expect(palette.getByRole('button', { name: 'Smile', exact: true })).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(chatInput).toHaveValue('😀');
    await expect(palette).toBeVisible();

    await chatInput.press('Enter');
    await expect.poll(() => sentPayloads.length).toBe(8);
    expect(sentPayloads[7].text).toBe('😀');
    await expect(chatInput).toHaveValue('');
    await expect(palette).toBeVisible();

    await palette.getByRole('button', { name: 'Dragon', exact: true }).click();
    await page.clock.fastForward(9000);
    await sendBtn.click();
    await expect.poll(() => sentPayloads.length).toBe(9);
    expect(sentPayloads[8].text).toBe('🐉');
    await expect(chatInput).toHaveValue('');
    await expect(palette).toBeVisible();
    await page.clock.fastForward(9999);
    await expect(palette).toBeVisible();
    await expect(emotesBtn).toHaveAttribute('aria-expanded', 'true');
    await page.clock.fastForward(1);
    await expect(palette).toBeHidden();
    expect(sentPayloads).toHaveLength(9);

    // Long words and multi-line text wrap cleanly without horizontal overflow.
    await chatInput.fill('word '.repeat(100));
    expect(
      await chatInput.evaluate(
        (el: HTMLTextAreaElement) =>
          el.scrollHeight > el.clientHeight && el.scrollWidth === el.clientWidth,
      ),
    ).toBe(true);
    await chatInput.fill('x'.repeat(500));
    expect(
      await chatInput.evaluate((el: HTMLTextAreaElement) => el.scrollWidth === el.clientWidth),
    ).toBe(true);
    expect(
      await chatInput.evaluate((el: HTMLTextAreaElement) => {
        const style = getComputedStyle(el);
        return el.rows >= 2 && el.clientHeight >= 2 * parseFloat(style.lineHeight);
      }),
    ).toBe(true);

    if (view === 'canvas') {
      await page.evaluate(() => {
        const win = window as unknown as ComposerHarnessWindow;
        const messages = document.getElementById('chat-messages')!;
        messages.appendChild(
          win.controller!.renderChatMessage({
            type: 'suggestion',
            author: 'alice',
            text: 'Own idea',
          }),
        );
        messages.appendChild(
          win.controller!.renderChatMessage({
            type: 'suggestion',
            author: 'bob',
            text: 'Other idea',
          }),
        );
      });
      const ownVote = page.locator('[data-suggestion-author="alice"] .suggestion-upvote-btn');
      const otherVote = page.locator('[data-suggestion-author="bob"] .suggestion-upvote-btn');
      await expect(ownVote).toBeDisabled();
      await expect(otherVote).toBeEnabled();
      await expect(page.locator('[data-vote-count-for="alice"]')).toHaveText('1 vote');

      await page.evaluate(() => {
        const win = window as unknown as ComposerHarnessWindow;
        win.controller!.setChatUsername('bob');
      });
      await expect(ownVote).toBeEnabled();
      await expect(otherVote).toBeDisabled();
    }
  });
}
