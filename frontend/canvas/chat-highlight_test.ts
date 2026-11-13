import fs from 'node:fs';
import path from 'node:path';
import type { WebSocketRoute } from '@playwright/test';
import { expect, TEMPLATES_DIR, test } from '../../testing/ui/canvas_fixtures';

interface AgentThoughtState {
  text: string;
  highlighted: boolean;
}

const TEMPLATES = ['canvas', 'popout'] as const;

for (const templateName of TEMPLATES) {
  test(`${templateName} updates current-thought highlighting without changing message`, async ({
    canvas,
    page,
  }) => {
    let currentThought: AgentThoughtState = { text: 'Hello', highlighted: false };
    let stateSocket: WebSocketRoute | null = null;

    if (templateName === 'canvas') {
      await canvas.openCanvas({
        viewport: { width: 1400, height: 900 },
        apiRoutes: {
          '/api/latest': async (route) => {
            await route.fulfill({
              json: {
                viewer_collab_enabled: true,
                agent_thought: currentThought,
              },
            });
          },
        },
        onWebSocket: (ws) => {
          if (new URL(ws.url()).pathname === '/ws/canvas-state') {
            stateSocket = ws;
            ws.send(JSON.stringify({ type: 'state_ready' }));
          }
        },
      });
    } else {
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
          await route.fulfill({ json: { authenticated: false } });
        },
      );
      await canvas.serveRoute(
        (url) => url.origin === canvas.origin && url.pathname === '/api/chat',
        async (route) => {
          await route.fulfill({ json: [] });
        },
      );
      await canvas.serveRoute(
        (url) => url.origin === canvas.origin && url.pathname === '/api/latest',
        async (route) => {
          await route.fulfill({ json: { agent_thought: currentThought } });
        },
      );
      await page.routeWebSocket(/wss?:\/\/canvas\.test\/ws\/.*/, (ws) => {
        if (new URL(ws.url()).pathname === '/ws/canvas-state') {
          stateSocket = ws;
          ws.send(JSON.stringify({ type: 'state_ready' }));
        }
      });
      await page.goto(`${canvas.origin}/popout?theater_id=stage`);
    }

    await expect.poll(() => stateSocket !== null).toBe(true);

    const card = page.locator('.agent-thought-card');
    const thoughtText = page.locator('#agent-thought-text');

    await expect(thoughtText).toHaveText('Hello');
    await expect(card).toHaveClass('agent-thought-card');
    const normalShadow = await card.evaluate((el) => getComputedStyle(el).boxShadow);

    currentThought = { text: 'Hello', highlighted: true };
    stateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
    await expect(card).toHaveClass('agent-thought-card is-highlighted');
    const highlightedShadow = await card.evaluate((el) => getComputedStyle(el).boxShadow);
    expect(highlightedShadow).not.toBe(normalShadow);
    await expect(thoughtText).toHaveText('Hello');

    currentThought = { text: 'Hello', highlighted: false };
    stateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
    await expect(card).toHaveClass('agent-thought-card');
    await expect(thoughtText).toHaveText('Hello');
  });
}
