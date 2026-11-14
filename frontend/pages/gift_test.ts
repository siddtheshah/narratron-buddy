import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  TEMPLATES_DIR,
  expect,
  test,
} from '../../testing/ui/canvas_fixtures';

interface GiftTestWindow extends Window {
  __authModalTabs?: Array<string | undefined>;
  __invalidatedCount?: number;
  __checkAuthCalls?: Array<{ refresh?: boolean } | undefined>;
  __authenticated?: boolean;
}

function loadPreparedGiftHtml(authHeadScript: string): string {
  let html = fs.readFileSync(path.join(TEMPLATES_DIR, 'gift.html'), 'utf-8');
  html = html.replace(
    /<script[^>]+src="(?!\/static\/generated\/pages\/gift\.js)[^"]+"[^>]*><\/script>/g,
    '',
  );
  html = html.replace(/<link[^>]+href="https?:\/\/[^>]*>/g, '');
  html = html.replace('<head>', `<head><script>${authHeadScript}</script>`);
  return html;
}

const AUTH_STUB_HEADER = `
  window.__authModalTabs = [];
  window.__invalidatedCount = 0;
  window.__checkAuthCalls = [];
  window.__authenticated = false;
  window.currentUser = null;
  window.openAuthModal = (tab) => window.__authModalTabs.push(tab);
  window.invalidateAuthState = () => { window.__invalidatedCount += 1; };
  window.checkAuthStatus = async (opts) => {
    window.__checkAuthCalls.push(opts);
    return window.__authenticated
      ? { authenticated: true, user: { id: 1, username: 'recipient', credits: 15 } }
      : { authenticated: false, user: null };
  };
`;

test('gift page template uses module scripts and handles missing, expired, and claimed states', async ({
  canvas,
  page,
}) => {
  const rawTemplate = fs.readFileSync(path.join(TEMPLATES_DIR, 'gift.html'), 'utf-8');
  expect(rawTemplate).toContain('<script type="module" src="/static/generated/shared/auth-flow.js"></script>');
  expect(rawTemplate).toContain('<script type="module" src="/static/generated/pages/gift.js"></script>');

  const html = loadPreparedGiftHtml(AUTH_STUB_HEADER);
  let giftResponse: { status: number; body: Record<string, unknown> } = {
    status: 404,
    body: { detail: 'Gift link not found.' },
  };

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && !url.pathname.startsWith('/static/'),
    async (route: Route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname.startsWith('/gift/')) {
        await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
      } else if (pathname === '/api/credit-gifts/missing-tok') {
        await route.fulfill({ status: giftResponse.status, json: giftResponse.body });
      } else if (pathname === '/api/credit-gifts/expired-tok') {
        await route.fulfill({ json: { status: 'expired', claimed: false, credits: 10 } });
      } else if (pathname === '/api/credit-gifts/claimed-tok') {
        await route.fulfill({ json: { status: 'pending', claimed: true, credits: 10 } });
      } else if (pathname === '/favicon.png') {
        await route.fulfill({ status: 204 });
      } else {
        await route.fulfill({ contentType: 'text/plain', body: '' });
      }
    },
  );

  await page.goto(`${CANVAS_ORIGIN}/gift/missing-tok`);
  await expect(page.locator('#message')).toHaveText('Gift link not found.');
  await expect(page.locator('#message')).toHaveClass('error');
  await expect(page.locator('#claim')).toBeHidden();

  await page.goto(`${CANVAS_ORIGIN}/gift/expired-tok`);
  await expect(page.locator('#message')).toHaveText('This gift link has expired.');
  await expect(page.locator('#message')).toHaveClass('error');

  await page.goto(`${CANVAS_ORIGIN}/gift/claimed-tok`);
  await expect(page.locator('#message')).toHaveText('This gift has already been claimed.');
  await expect(page.locator('#message')).toHaveClass('error');
});

test('gift page prompts sign-in when unauthenticated and auto-claims on auth-changed', async ({
  canvas,
  page,
}) => {
  const html = loadPreparedGiftHtml(AUTH_STUB_HEADER);
  let claimCalls = 0;

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && !url.pathname.startsWith('/static/'),
    async (route: Route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/gift/tok%20123') {
        await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
      } else if (pathname === '/api/credit-gifts/tok%20123') {
        await route.fulfill({ json: { status: 'pending', claimed: false, credits: 12.5 } });
      } else if (pathname === '/api/credit-gifts/tok%20123/claim') {
        claimCalls += 1;
        await route.fulfill({ json: { credits: 12.5 } });
      } else if (pathname === '/favicon.png') {
        await route.fulfill({ status: 204 });
      } else {
        await route.fulfill({ contentType: 'text/plain', body: '' });
      }
    },
  );

  await page.goto(`${CANVAS_ORIGIN}/gift/tok%20123`);
  await expect(page.locator('#message')).toHaveText(
    'This link gives you 12.5 credits. Sign in or create an account to claim it.',
  );
  const claimBtn = page.locator('#claim');
  await expect(claimBtn).toBeVisible();
  await expect(claimBtn).toHaveText('Sign in to claim');

  await claimBtn.click();
  expect(
    await page.evaluate(() => (window as unknown as GiftTestWindow).__authModalTabs),
  ).toEqual(['login']);
  expect(claimCalls).toBe(0);

  await page.evaluate(() => {
    window.dispatchEvent(
      new CustomEvent('narratron:auth-changed', { detail: { authenticated: true } }),
    );
  });

  await expect(page.locator('#message')).toHaveText(
    'Success! 12.5 credits were added to your account. You and the sender are now friends.',
  );
  await expect(page.locator('#message')).toHaveClass('success');
  await expect(claimBtn).toBeHidden();
  expect(claimCalls).toBe(1);
  expect(
    await page.evaluate(() => (window as unknown as GiftTestWindow).__invalidatedCount),
  ).toBe(1);
  expect(
    await page.evaluate(() => (window as unknown as GiftTestWindow).__checkAuthCalls),
  ).toEqual([{ refresh: true }, { refresh: true }]);
});

test('gift page auto-claims immediately when already signed in and surfaces claim errors', async ({
  canvas,
  page,
}) => {
  const html = loadPreparedGiftHtml(`${AUTH_STUB_HEADER}\nwindow.__authenticated = true;`);
  let claimStatus = 400;

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && !url.pathname.startsWith('/static/'),
    async (route: Route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/gift/self-gift') {
        await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
      } else if (pathname === '/api/credit-gifts/self-gift') {
        await route.fulfill({ json: { status: 'pending', claimed: false, credits: 5 } });
      } else if (pathname === '/api/credit-gifts/self-gift/claim') {
        await route.fulfill({
          status: claimStatus,
          json: { detail: 'You cannot claim your own gift.' },
        });
      } else if (pathname === '/favicon.png') {
        await route.fulfill({ status: 204 });
      } else {
        await route.fulfill({ contentType: 'text/plain', body: '' });
      }
    },
  );

  await page.goto(`${CANVAS_ORIGIN}/gift/self-gift`);
  await expect(page.locator('#message')).toHaveText('You cannot claim your own gift.');
  await expect(page.locator('#message')).toHaveClass('error');
  await expect(page.locator('#claim')).toBeHidden();
});
