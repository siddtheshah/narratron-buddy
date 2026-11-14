import fs from 'node:fs';
import path from 'node:path';
import { CANVAS_ORIGIN, expect, TEMPLATES_DIR, test } from '../../testing/ui/canvas_fixtures';

const AUTH_FLOW_MODULE_URL = '/static/generated/shared/auth-flow.js';

test('docs sidebar and public footers link to Terms of Service and Privacy Policy', () => {
  const sidebarHtml = fs.readFileSync(path.join(TEMPLATES_DIR, 'docs_sidebar.html'), 'utf-8');
  expect(sidebarHtml).toContain('href="/docs/terms"');
  expect(sidebarHtml).toContain('href="/docs/privacy"');
  expect(sidebarHtml).toContain('Terms of Service');
  expect(sidebarHtml).toContain('Privacy Policy');

  const splashHtml = fs.readFileSync(path.join(TEMPLATES_DIR, 'join_splash.html'), 'utf-8');
  expect(splashHtml).toContain('href="/terms"');
  expect(splashHtml).toContain('href="/privacy"');

  const statsHtml = fs.readFileSync(path.join(TEMPLATES_DIR, 'stats.html'), 'utf-8');
  expect(statsHtml).toContain('href="/terms"');
  expect(statsHtml).toContain('href="/privacy"');
});

test('auth-flow sign up modal renders Terms of Service and Privacy Policy links', async ({
  canvas,
  page,
}) => {
  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/auth/me',
    async (route) => {
      await route.fulfill({ json: { authenticated: false } });
    },
  );

  await canvas.mount({
    stylesheets: ['/static/css/auth-flow.css'],
    moduleScripts: [`import ${JSON.stringify(AUTH_FLOW_MODULE_URL)};`],
  });

  await page.evaluate(() => {
    window.openAuthModal?.('register');
  });

  const termsLink = page.locator('#formRegister a[href="/terms"]');
  const privacyLink = page.locator('#formRegister a[href="/privacy"]');
  await expect(termsLink).toBeVisible();
  await expect(termsLink).toHaveText('Terms of Service');
  await expect(privacyLink).toBeVisible();
  await expect(privacyLink).toHaveText('Privacy Policy');
});
