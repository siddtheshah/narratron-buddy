import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  TEMPLATES_DIR,
  expect,
  test,
} from '../../testing/ui/canvas_fixtures';

function loadPreparedJoinSplashHtml(headInject = ''): string {
  let html = fs.readFileSync(path.join(TEMPLATES_DIR, 'join_splash.html'), 'utf-8');
  html = html.replace(
    /<script[^>]+src="(?!\/static\/generated\/canvas\/(?:friends|join-splash|user-help)\.js)[^"]+"[^>]*><\/script>/g,
    '',
  );
  html = html.replace(/<link[^>]+href="https?:\/\/[^>]*>/g, '');
  if (headInject) {
    html = html.replace('<head>', `<head><script>${headInject}</script>`);
  }
  return html;
}

const SPLIDE_STUB_SCRIPT = `
  window.__splideCalls = [];
  window.Splide = function Splide(selector, options) {
    window.__splideCalls.push({ selector, options });
    this.mount = () => { window.__splideMounted = (window.__splideMounted || 0) + 1; };
  };
  window.getAuthState = async () => ({ authenticated: false });
`;

const ENTRIES = ['ABC234', 'abc234', 'KEY-ABC234', ' key-abc234 '] as const;
const SUBMIT_MODES = ['button', 'enter'] as const;

for (const entry of ENTRIES) {
  for (const submit of SUBMIT_MODES) {
    test(`join accepts code or full key (${JSON.stringify(entry)}, ${submit})`, async ({
      canvas,
      page,
    }) => {
      const html = loadPreparedJoinSplashHtml(SPLIDE_STUB_SCRIPT);
      const submittedKeys: string[] = [];

      await canvas.serveRoute(
        (url: URL) => url.origin === CANVAS_ORIGIN && !url.pathname.startsWith('/static/'),
        async (route: Route) => {
          const pathname = new URL(route.request().url()).pathname;
          if (pathname === '/') {
            await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
          } else if (pathname === '/api/theaters/resolve-join-key') {
            const payload = JSON.parse(route.request().postData() ?? '{}') as {
              join_key?: string;
            };
            if (payload.join_key) {
              submittedKeys.push(payload.join_key);
            }
            await route.fulfill({ json: { theater_id: 'test-stage' } });
          } else if (pathname === '/canvas') {
            await route.fulfill({
              contentType: 'text/html; charset=utf-8',
              body: '<h1>Theater canvas</h1>',
            });
          } else if (pathname === '/favicon.png') {
            await route.fulfill({ status: 204 });
          } else {
            await route.fulfill({ contentType: 'text/plain', body: '' });
          }
        },
      );

      await page.goto(`${CANVAS_ORIGIN}/`);
      const field = page.getByRole('textbox', { name: 'Theater code' });
      await expect(field).toHaveAttribute('placeholder', 'XXXXXX');
      await field.fill(entry);
      if (submit === 'enter') {
        await field.press('Enter');
      } else {
        await page.getByRole('button', { name: 'Join', exact: true }).click();
      }
      await expect(page).toHaveURL(`${CANVAS_ORIGIN}/canvas?theater_id=test-stage`);
      expect(submittedKeys).toEqual(['KEY-ABC234']);
    });
  }
}

test('join splash template wires module scripts, validates errors, and mounts carousel', async ({
  canvas,
  page,
}) => {
  const rawTemplate = fs.readFileSync(path.join(TEMPLATES_DIR, 'join_splash.html'), 'utf-8');
  expect(rawTemplate).toContain('<script type="module" src="/static/generated/canvas/auth-flow.js"></script>');
  expect(rawTemplate).toContain('<script type="module" src="/static/generated/canvas/join-splash.js"></script>');
  expect(rawTemplate).not.toContain('onclick="submitJoinKey()"');
  expect(rawTemplate).not.toContain('onkeydown="if(event.key===\'Enter\') submitJoinKey()"');

  const html = loadPreparedJoinSplashHtml(SPLIDE_STUB_SCRIPT);
  let resolveMode: 'error' | 'network-fail' = 'error';

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && !url.pathname.startsWith('/static/'),
    async (route: Route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/') {
        await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
      } else if (pathname === '/api/theaters/resolve-join-key') {
        if (resolveMode === 'network-fail') {
          await route.abort('failed');
          return;
        }
        await route.fulfill({ status: 404, json: { detail: 'Join key expired.' } });
      } else if (pathname === '/favicon.png') {
        await route.fulfill({ status: 204 });
      } else {
        await route.fulfill({ contentType: 'text/plain', body: '' });
      }
    },
  );

  await page.goto(`${CANVAS_ORIGIN}/`);

  const splideState = await page.evaluate(() => {
    const win = window as unknown as {
      __splideCalls?: Array<{ selector: string }>;
      __splideMounted?: number;
    };
    return { calls: win.__splideCalls, mounted: win.__splideMounted };
  });
  expect(splideState.mounted).toBe(1);
  expect(splideState.calls?.[0]?.selector).toBe('#featuresCarousel');

  const errorBox = page.locator('#errorMessage');
  const joinBtn = page.getByRole('button', { name: 'Join', exact: true });
  const field = page.getByRole('textbox', { name: 'Theater code' });

  await joinBtn.click();
  await expect(errorBox).toBeVisible();
  await expect(errorBox).toHaveText('Please enter a valid Join Key.');

  await field.fill('KEY-BADKEY');
  await joinBtn.click();
  await expect(errorBox).toBeVisible();
  await expect(errorBox).toHaveText('Join key expired.');

  resolveMode = 'network-fail';
  await joinBtn.click();
  await expect(errorBox).toBeVisible();
  await expect(errorBox).toHaveText('Failed to connect to server. Please try again.');
});
