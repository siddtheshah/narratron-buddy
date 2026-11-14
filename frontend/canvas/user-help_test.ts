import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  expect,
  REPO_ROOT,
  TEMPLATES_DIR,
  test,
  type CanvasHarness,
} from '../../testing/ui/canvas_fixtures';

type UserHelpModule = typeof import('/static/generated/canvas/user-help.js');
type ChatControllerModule = typeof import('/static/generated/canvas/chat-controller.js');
type PopoutModule = typeof import('/static/generated/canvas/popout.js');

interface HelpRequestRecord {
  readonly url: string;
  readonly body: Record<string, unknown>;
}

interface ApplyRequestRecord {
  readonly url: string;
  readonly body: Record<string, unknown>;
  readonly confirmation: string | undefined;
}

interface ChatHelpHarnessWindow extends Window {
  controller?: ReturnType<ChatControllerModule['initializeChatController']>;
  message?: Parameters<
    ReturnType<ChatControllerModule['initializeChatController']>['renderChatMessage']
  >[0];
  applies?: ApplyRequestRecord[];
  restarts?: number;
  requests?: HelpRequestRecord[];
  forwarded?: string[];
  broadcasted?: unknown[];
  renderChatMessage?: ReturnType<ChatControllerModule['initializeChatController']>['renderChatMessage'];
  injected?: unknown;
  authModalCalls?: Array<string | undefined>;
}

const USER_HELP_MODULE_URL = '/static/generated/canvas/user-help.js';
const CHAT_CONTROLLER_MODULE_URL = '/static/generated/canvas/chat-controller.js';
const POPOUT_MODULE_URL = '/static/generated/canvas/popout.js';

function loadJoinSplashVisitorHelp(): { styles: string; markup: string } {
  const template = fs.readFileSync(path.join(TEMPLATES_DIR, 'join_splash.html'), 'utf-8');
  const styleMatch = template.match(/<style>([\s\S]*?)<\/style>/);
  if (!styleMatch) {
    throw new Error('Could not locate <style> block in templates/join_splash.html');
  }
  const start = template.indexOf('    <section class="visitor-help"');
  const end = template.indexOf('    <section class="features-carousel', start);
  if (start < 0 || end <= start) {
    throw new Error('Could not locate .visitor-help section in templates/join_splash.html');
  }
  return {
    styles: styleMatch[1],
    markup: template.slice(start, end),
  };
}

function loadCanvasChatFormMarkup(): string {
  const template = fs.readFileSync(path.join(TEMPLATES_DIR, 'canvas.html'), 'utf-8');
  const formMatch = template.match(/<form id="chat-form"[^>]*>[\s\S]*?<\/form>/);
  if (!formMatch) {
    throw new Error('Could not locate #chat-form in templates/canvas.html');
  }
  return formMatch[0];
}

const VISITOR_HELP_FIXTURE = loadJoinSplashVisitorHelp();
const CANVAS_CHAT_FORM_MARKUP = loadCanvasChatFormMarkup();

async function mountVisitorHelp(
  canvas: CanvasHarness,
  moduleUrl: string = USER_HELP_MODULE_URL,
): Promise<void> {
  await canvas.mount({
    viewport: { width: 390, height: 780 },
    inlineStyles: [VISITOR_HELP_FIXTURE.styles],
    html: VISITOR_HELP_FIXTURE.markup,
    moduleScripts: [`import ${JSON.stringify(moduleUrl)};`],
  });
}

for (const owner of [true, false]) {
  test(`help settings button is owner-only and requires confirmation click (owner=${owner})`, async ({
    canvas,
    page,
  }) => {
    await canvas.mount({
      html: '<div id="chat-messages"></div>',
    });

    await page.evaluate(
      async ({ moduleUrl, isOwner }) => {
        const win = window as unknown as ChatHelpHarnessWindow;
        win.applies = [];
        win.restarts = 0;

        window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
          const url = typeof input === 'string' ? input : input.toString();
          if (!url.endsWith('/apply-settings')) {
            return new Response(
              JSON.stringify(url.startsWith('/api/chat') && win.message ? [win.message] : []),
              { status: 200, headers: { 'Content-Type': 'application/json' } },
            );
          }
          const headers = init?.headers as Record<string, string> | undefined;
          win.applies!.push({
            url,
            body: JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>,
            confirmation: headers?.['X-Narratron-Settings-Confirm'],
          });
          if (win.applies!.length === 1) {
            return new Response(JSON.stringify({ detail: 'Settings changed. Ask again.' }), {
              status: 409,
              headers: { 'Content-Type': 'application/json' },
            });
          }
          return new Response(
            JSON.stringify({
              applied: true,
              agent_running: true,
              message: 'Settings saved and agent restarted.',
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        };

        const { initializeChatController } =
          await window.__importModule<ChatControllerModule>(moduleUrl);
        const controller = initializeChatController({
          messagesContainer: document.querySelector('#chat-messages'),
          theaterId: 'stage',
          isTheaterOwner: () => isOwner,
          onSettingsApplied: async () => {
            win.restarts = (win.restarts ?? 0) + 1;
          },
        });
        win.controller = controller;
        await controller.fetchChat();
        const message: NonNullable<ChatHelpHarnessWindow['message']> = {
          type: 'user_help',
          author: 'Narratron User Help',
          text: 'Review this change.',
          settings_proposal: {
            config_digest: 'a'.repeat(64),
            changes: [{ setting: 'image_generation.enabled', enabled: false }],
          },
          settings_summary: ['Disable AI image generation'],
        };
        win.message = message;
        document.querySelector('#chat-messages')!.appendChild(controller.renderChatMessage(message));
      },
      { moduleUrl: CHAT_CONTROLLER_MODULE_URL, isOwner: owner },
    );

    const button = page.getByRole('button', { name: 'Apply settings and restart agent' });
    await expect(button).toHaveCount(owner ? 1 : 0);
    expect(await page.evaluate(() => (window as unknown as ChatHelpHarnessWindow).applies)).toEqual(
      [],
    );

    if (owner) {
      await expect(page.getByText('Disable AI image generation')).toHaveCount(1);
      await button.click();
      await expect(
        page.getByRole('status').filter({ hasText: 'Settings changed. Ask again.' }),
      ).toBeVisible();
      await expect(button).toBeEnabled();
      expect(
        await page.evaluate(() => (window as unknown as ChatHelpHarnessWindow).restarts),
      ).toBe(0);

      await button.click();
      await expect(
        page.getByRole('status').filter({ hasText: 'Settings saved and agent restarted.' }),
      ).toBeVisible();
      expect(
        await page.evaluate(() => (window as unknown as ChatHelpHarnessWindow).restarts),
      ).toBe(1);
      expect(
        await page.evaluate(() => (window as unknown as ChatHelpHarnessWindow).applies?.[1]),
      ).toEqual({
        url: '/api/user-help/stage/apply-settings',
        confirmation: '1',
        body: {
          config_digest: 'a'.repeat(64),
          changes: [{ setting: 'image_generation.enabled', enabled: false }],
        },
      });

      await page.evaluate(() => {
        const win = window as unknown as ChatHelpHarnessWindow;
        document
          .querySelector('#chat-messages')!
          .replaceChildren(win.controller!.renderChatMessage(win.message!));
      });
      await expect(page.getByRole('button', { name: 'Settings applied' })).toBeDisabled();
    }
  });
}

test('canvas help is private and skips the live agent', async ({ canvas, page }) => {
  await canvas.mount({
    viewport: { width: 390, height: 780 },
    html: `<div id="chat-messages"></div>${CANVAS_CHAT_FORM_MARKUP}`,
  });

  await page.evaluate(async (moduleUrl: string) => {
    const win = window as unknown as ChatHelpHarnessWindow;
    win.requests = [];
    win.forwarded = [];
    win.broadcasted = [];

    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (init?.method === 'POST') {
        const body = JSON.parse(String(init.body ?? '{}')) as Record<string, unknown>;
        win.requests!.push({ url, body });
        const text = body.personalized ? 'Researched answer' : 'Private answer';
        return new Response(
          JSON.stringify({
            type: 'user_help',
            text,
            html: `<p>${text}</p>`,
            help_mode: body.personalized ? 'personalized' : 'basic',
            can_personalize: true,
            personalized_credit_cost: 0.15,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    };

    const { initializeChatController } =
      await window.__importModule<ChatControllerModule>(moduleUrl);
    win.controller = initializeChatController({
      messagesContainer: document.querySelector('#chat-messages'),
      chatForm: document.querySelector('#chat-form') as HTMLFormElement | null,
      chatInput: document.querySelector('#chat-input') as HTMLTextAreaElement | null,
      theaterId: 'stage',
      isCurrentOrator: () => true,
      getAgentWs: () => ({
        readyState: WebSocket.OPEN,
        send: (value: string) => {
          win.forwarded!.push(value);
        },
      }),
      getAuthState: async () => ({ authenticated: true, user: { username: 'alice' } }),
      popoutBroadcast: {
        postMessage: (value: unknown) => {
          win.broadcasted!.push(value);
        },
      },
    });
  }, CHAT_CONTROLLER_MODULE_URL);

  await page.locator('#chat-input').fill('How do I start?');
  const helpBtn = page.locator('[data-chat-command="help"]');
  await expect(helpBtn).toHaveText('?');
  await helpBtn.click();

  await page.waitForFunction(
    () => document.querySelector('#chat-messages')?.textContent?.includes('Private answer') ?? false,
  );
  expect(
    await page.evaluate(() => (window as unknown as ChatHelpHarnessWindow).requests),
  ).toEqual([{ url: '/api/user-help?theater_id=stage', body: { question: 'How do I start?' } }]);
  expect(
    await page.evaluate(() => (window as unknown as ChatHelpHarnessWindow).forwarded),
  ).toEqual([]);
  expect(
    await page.evaluate(() => (window as unknown as ChatHelpHarnessWindow).broadcasted),
  ).toEqual([]);

  await page.evaluate(() => (window as unknown as ChatHelpHarnessWindow).controller!.fetchChat());
  const chatText = await page.locator('#chat-messages').innerText();
  expect(chatText.split('Private answer').length - 1).toBe(1);
  expect(chatText).toContain('only you');
  await expect(helpBtn).toHaveText('?');
  await expect(helpBtn).toHaveAttribute('title', 'Find basic help and documentation (free)');

  const upgrade = page.getByRole('button', { name: 'Personalized help · 0.15 cr' });
  await expect(upgrade).toHaveCount(1);
  expect(
    await page.evaluate(() => (window as unknown as ChatHelpHarnessWindow).requests?.length),
  ).toBe(1);
  await upgrade.click();

  await page.waitForFunction(
    () =>
      document.querySelector('#chat-messages')?.textContent?.includes('Researched answer') ?? false,
  );
  expect(
    await page.evaluate(() => (window as unknown as ChatHelpHarnessWindow).requests?.[1]?.body),
  ).toEqual({
    question: 'How do I start?',
    personalized: true,
    quoted_credit_cost: 0.15,
  });
  expect(
    await page.evaluate(() => (window as unknown as ChatHelpHarnessWindow).forwarded),
  ).toEqual([]);
  expect(
    await page.evaluate(() => (window as unknown as ChatHelpHarnessWindow).broadcasted),
  ).toEqual([]);
});

test('visitor help is free, upgrades cleanly, displays errors, and stays within mobile viewport', async ({
  canvas,
  page,
}) => {
  const requests: HelpRequestRecord[] = [];
  await canvas.serveRoute('/api/user-help', async (route: Route) => {
    const body = JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>;
    requests.push({ url: new URL(route.request().url()).pathname, body });
    const ok = requests.length <= 2;
    await route.fulfill({
      status: ok ? 200 : 429,
      json: ok
        ? {
            html: body.personalized
              ? '<p>Researched answer</p>'
              : '<p>Explore <a href="/docs">Docs</a>.</p>',
            help_mode: body.personalized ? 'personalized' : 'basic',
            can_personalize: true,
            personalized_credit_cost: 0.15,
          }
        : { detail: 'Please wait a moment.' },
    });
  });

  await mountVisitorHelp(canvas);

  const panel = page.locator('#visitor-help-panel');
  const launcher = page.locator('#visitor-help-launcher');
  const questionInput = page.locator('#visitor-help-question');
  const submitButton = page.locator('#visitor-help-form button');

  await expect(panel).toBeHidden();
  await launcher.click();
  await expect(panel).toBeVisible();

  await questionInput.fill('What is Narratron?');
  await submitButton.click();
  await expect(page.locator('#visitor-help-log')).toContainText('Explore');
  expect(requests[0]).toEqual({
    url: '/api/user-help',
    body: { question: 'What is Narratron?' },
  });
  await expect(page.locator('#visitor-help-log a')).toHaveAttribute('href', '/docs');

  const upgrade = page.getByRole('button', { name: 'Personalized help · 0.15 cr' });
  await expect(upgrade).toHaveCount(1);
  expect(requests).toHaveLength(1);
  await upgrade.click();
  await expect(page.locator('#visitor-help-log')).toContainText('Researched answer');
  expect(requests[1]?.body).toEqual({
    question: 'What is Narratron?',
    personalized: true,
    quoted_credit_cost: 0.15,
  });

  await questionInput.fill('What else?');
  await submitButton.click();
  await expect(page.locator('#visitor-help-status')).toHaveText('Please wait a moment.');
  await expect(questionInput).toHaveValue('What else?');
  await expect(submitButton).toBeEnabled();

  await page.locator('#visitor-help-close').click();
  await expect(panel).toBeHidden();
  await launcher.click();
  await expect(panel).toBeVisible();

  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true);
});

test('visitor help window API, Escape dismissal, suggestion chips, and sign-in prompt', async ({
  canvas,
  page,
}) => {
  const requests: HelpRequestRecord[] = [];
  await canvas.serveRoute('/api/user-help', async (route: Route) => {
    const body = JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>;
    requests.push({ url: new URL(route.request().url()).pathname, body });
    await route.fulfill({
      status: 200,
      json: {
        html: '<p>Adventure Mode runs scripted quests.</p>',
        help_mode: 'basic',
        can_personalize: false,
      },
    });
  });

  await mountVisitorHelp(canvas, USER_HELP_MODULE_URL);

  const panel = page.locator('#visitor-help-panel');
  await expect(panel).toBeHidden();

  await page.evaluate(() => window.openVisitorHelp?.());
  await expect(panel).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(panel).toBeHidden();
  await page.evaluate(() => window.toggleVisitorHelp?.());
  await expect(panel).toBeVisible();

  await page.locator('.help-chip').first().click();
  await expect(page.locator('#visitor-help-log')).toContainText(
    'Adventure Mode runs scripted quests.',
  );
  await expect(page.locator('#visitor-help-welcome')).toBeHidden();
  expect(requests).toEqual([
    { url: '/api/user-help', body: { question: 'What is Adventure Mode?' } },
  ]);

  await page.evaluate(() => {
    const win = window as unknown as ChatHelpHarnessWindow;
    win.authModalCalls = [];
    win.openAuthModal = (tab?: string) => {
      win.authModalCalls!.push(tab);
    };
  });
  await page.getByRole('button', { name: 'Sign in for personalized help' }).click();
  await expect(page.locator('#visitor-help-question')).toHaveValue('What is Adventure Mode?');
  await expect(page.locator('#visitor-help-status')).toHaveText(
    'Sign in, then ask again to request personalized help.',
  );
  expect(
    await page.evaluate(() => (window as unknown as ChatHelpHarnessWindow).authModalCalls),
  ).toEqual(['login']);

  await page.evaluate(() => window.closeVisitorHelp?.());
  await expect(panel).toBeHidden();
});

for (const view of ['canvas', 'popout'] as const) {
  test(`help markdown renders and escapes model html in ${view}`, async ({ canvas, page }) => {
    const answerText = [
      '## Start an adventure',
      '',
      '1. Open **Settings**.',
      '2. Press `Shift+A`.',
      '',
      'See [the guide](https://example.test/guide).',
      '',
      '```yaml',
      'adventure_mode: true',
      '```',
      '',
      '<img src=x onerror="window.injected=true">',
    ].join('\n');

    // Matches utils.markdown.render_markdown(answerText) output.
    const answerHtml = [
      '<h2>Start an adventure</h2>',
      '<ol><li>Open <strong>Settings</strong>.</li><li>Press <code>Shift+A</code>.</li></ol>',
      '<p>See <a href="https://example.test/guide">the guide</a>.</p>',
      '<pre><code class="language-yaml">adventure_mode: true</code></pre>',
      '<p>&lt;img src=x onerror="window.injected=true"&gt;</p>',
    ].join('\n');

    const helpMessage = {
      author: 'Narratron User Help',
      type: 'user_help',
      text: answerText,
      html: answerHtml,
    };

    const chatCss = await fs.promises.readFile(
      path.join(REPO_ROOT, 'static', 'css', 'chat.css'),
      'utf-8',
    );

    await page.context().route('https://example.test/**', async (route: Route) => {
      await route.fulfill({ contentType: 'text/html', body: '<h1>Guide</h1>' });
    });

    await canvas.mount({
      viewport: { width: 400, height: 700 },
      inlineStyles: [chatCss],
      html: "<div id='messages'></div>",
    });

    if (view === 'canvas') {
      await page.evaluate(
        async ({ moduleUrl, msg }) => {
          const { initializeChatController } =
            await window.__importModule<ChatControllerModule>(moduleUrl);
          const renderChatMessage = initializeChatController().renderChatMessage;
          const win = window as unknown as ChatHelpHarnessWindow;
          win.renderChatMessage = renderChatMessage;
          document.querySelector('#messages')!.appendChild(renderChatMessage(msg));
        },
        { moduleUrl: CHAT_CONTROLLER_MODULE_URL, msg: helpMessage },
      );
    } else {
      await page.evaluate(
        async ({ moduleUrl, msg }) => {
          const { renderChatMessage } = await window.__importModule<PopoutModule>(moduleUrl);
          const win = window as unknown as ChatHelpHarnessWindow;
          win.renderChatMessage = renderChatMessage;
          document.querySelector('#messages')!.appendChild(renderChatMessage(msg));
        },
        { moduleUrl: POPOUT_MODULE_URL, msg: helpMessage },
      );
    }

    const helpBody = page.locator('.help-markdown');
    await expect(helpBody.locator('h2')).toHaveText('Start an adventure');
    await expect(helpBody.locator('ol li')).toHaveCount(2);
    await expect(helpBody.locator('strong')).toHaveText('Settings');
    await expect(helpBody.locator('li code')).toHaveText('Shift+A');
    await expect(helpBody.locator('pre code')).toHaveText('adventure_mode: true');
    await expect(helpBody.locator('a')).toHaveAttribute('href', 'https://example.test/guide');
    await expect(helpBody.locator('a')).toHaveAttribute('target', '_blank');
    const rel = (await helpBody.locator('a').getAttribute('rel')) ?? '';
    expect(rel).toContain('noopener');

    const [popup] = await Promise.all([
      page.waitForEvent('popup'),
      helpBody.locator('a').click(),
    ]);
    await popup.waitForLoadState('domcontentloaded');
    expect(popup.url()).toBe('https://example.test/guide');
    await popup.close();

    await expect(helpBody.locator('img')).toHaveCount(0);
    expect(await helpBody.innerText()).toContain('<img src=x onerror="window.injected=true">');
    expect(
      await page.evaluate(() => (window as unknown as ChatHelpHarnessWindow).injected === undefined),
    ).toBe(true);

    // Regular messages stay literal even if a message includes an HTML field.
    await page.evaluate(() => {
      const win = window as unknown as ChatHelpHarnessWindow;
      document.querySelector('#messages')!.appendChild(
        win.renderChatMessage!({
          author: 'Player',
          text: '**hello**',
          html: '<strong>hello</strong>',
        }),
      );
    });
    const lastMessage = page.locator('.chat-message').last();
    await expect(lastMessage.locator('.chat-text')).toHaveText('**hello**');
    await expect(lastMessage.locator('strong')).toHaveCount(0);
  });
}
