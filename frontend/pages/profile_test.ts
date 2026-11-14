import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  expect,
  TEMPLATES_DIR,
  test,
} from '../../testing/ui/canvas_fixtures';

interface ProfileStampFixture {
  id: number;
  name: string;
  url: string;
}

interface GenerateStampRequest {
  name: string;
  prompt: string;
}

function prepareProfileTemplate(extraHeadScript = ''): string {
  let html = fs.readFileSync(path.join(TEMPLATES_DIR, 'profile.html'), 'utf-8');
  html = html.replace(
    /<script[^>]+src="(?!\/static\/generated\/pages\/profile\.js)[^"]+"[^>]*><\/script>/g,
    '',
  );
  html = html.replace(/<link[^>]+>/g, '');
  if (extraHeadScript) {
    html = html.replace('<head>', `<head><script>${extraHeadScript}</script>`);
  }
  return html;
}

test('profile stamp generation errors, retry, mobile accordion, capacity limit, and non-owner view', async ({
  canvas,
  page,
}) => {
  await page.setViewportSize({ width: 1100, height: 900 });

  let stamps: ProfileStampFixture[] = [];
  const requests: GenerateStampRequest[] = [];
  const state = { owner: true, fail: true };

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && !url.pathname.startsWith('/static/'),
    async (route: Route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/users/Alice') {
        await route.fulfill({ contentType: 'text/html', body: prepareProfileTemplate() });
      } else if (pathname === '/api/users/Alice/profile') {
        await route.fulfill({
          json: {
            username: 'Alice',
            is_owner: state.owner,
            bio: '',
            stats: null,
            stamps,
          },
        });
      } else if (pathname === '/api/friends') {
        await route.fulfill({ json: [] });
      } else if (pathname === '/api/pricing') {
        await route.fulfill({ json: { image_credit_rate: 4 } });
      } else if (pathname === '/api/users/me/stamps/generate') {
        requests.push(JSON.parse(route.request().postData() ?? '{}') as GenerateStampRequest);
        if (state.fail) {
          await route.fulfill({
            status: 402,
            json: { detail: 'Generation requires 4 credits. Top up on /deploy.' },
          });
        } else {
          const stamp: ProfileStampFixture = { id: 42, name: 'Goblin', url: '/api/stamps/42' };
          stamps.push(stamp);
          await route.fulfill({ json: { stamp, credits_charged: 4, credits: 6 } });
        }
      } else if (pathname === '/api/stamps/42') {
        await route.fulfill({
          contentType: 'image/svg+xml',
          body: '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><circle cx="40" cy="40" r="30" fill="green"/></svg>',
        });
      } else {
        await route.abort('failed');
        throw new Error(`Unexpected request: ${pathname}`);
      }
    },
  );

  await page.goto(`${CANVAS_ORIGIN}/users/Alice`);
  await page.getByRole('tab', { name: /Stamps/ }).click();

  await expect(page.getByRole('button', { name: 'Upload', exact: true })).toBeHidden();
  await page.locator('#stamp-upload summary').click();
  await expect(page.getByRole('button', { name: 'Upload', exact: true })).toBeVisible();
  await page.locator('#stamp-upload summary').focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: 'Upload', exact: true })).toBeHidden();

  await expect(page.locator('#stamp-generate-form')).toBeHidden();
  await page.locator('#stamp-generation summary').click();
  await expect(page.locator('#stamp-generate-form')).toBeVisible();
  await expect(page.locator('#stamp-generation-cost')).toContainText('4 credits per stamp');

  await page.getByLabel('Stamp name', { exact: true }).fill('Goblin');
  await page.getByLabel('Describe your stamp').fill('Goblin scout in a green cloak');
  await page.getByRole('button', { name: 'Generate stamp', exact: true }).click();
  await expect(page.locator('#stamp-generate-status')).toContainText(
    'Generation requires 4 credits',
  );
  await expect(page.getByRole('button', { name: 'Generate stamp', exact: true })).toBeEnabled();
  await expect(page.getByLabel('Describe your stamp')).toHaveValue(
    'Goblin scout in a green cloak',
  );

  state.fail = false;
  await page.getByRole('button', { name: 'Generate stamp', exact: true }).click();
  await expect(page.locator('.stamp-title')).toHaveText('Goblin');
  await expect(page.locator('#stamps')).toBeVisible();
  expect(requests).toEqual([
    { name: 'Goblin', prompt: 'Goblin scout in a green cloak' },
    { name: 'Goblin', prompt: 'Goblin scout in a green cloak' },
  ]);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('#stamp-generate-form')).toBeHidden();
  await page.locator('#stamp-generation summary').focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: 'Generate stamp', exact: true })).toBeVisible();
  await page.locator('#stamp-generation summary').click();
  await expect(page.locator('#stamp-generate-form')).toBeHidden();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true);

  stamps = Array.from({ length: 10 }, (_, index) => ({
    id: 42,
    name: `Stamp ${index}`,
    url: '/api/stamps/42',
  }));
  await page.reload();
  await page.getByRole('tab', { name: /Stamps/ }).click();
  await page.locator('#stamp-upload summary').click();
  await expect(page.getByText('You have reached your limit.', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Generate stamp', exact: true })).toHaveCount(0);

  state.owner = false;
  stamps = [];
  await page.reload();
  await page.getByRole('tab', { name: /Stamps/ }).click();
  await expect(page.getByRole('button', { name: 'Generate stamp', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Upload', exact: true })).toHaveCount(0);
});

test('profile settings, bio editing, gift links, stamp upload/delete, account deletion, and 404', async ({
  canvas,
  page,
}) => {
  await page.setViewportSize({ width: 1100, height: 900 });

  let bio = 'Initial bio';
  let statsVisible = true;
  let profileColor = '#818cf8';
  let stamps: ProfileStampFixture[] = [{ id: 7, name: 'Rune', url: '/api/stamps/7' }];
  let profileFound = true;
  let accountDeleted = false;
  const savedProfiles: Array<Record<string, unknown>> = [];

  const pickrStubScript = `
    window.Pickr = {
      create(opts) {
        let current = opts.default;
        const handlers = {};
        const inst = {
          on(ev, cb) { handlers[ev] = cb; return inst; },
          hide() {},
          destroyAndRemove() {},
        };
        window.__pickColor = (hex) => {
          current = hex;
          handlers.change?.({ toHEXA: () => ({ toString: () => current }) });
        };
        return inst;
      },
    };
  `;

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && !url.pathname.startsWith('/static/'),
    async (route: Route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/users/Alice' || pathname === '/deploy') {
        await route.fulfill({
          contentType: 'text/html',
          body:
            pathname === '/deploy'
              ? '<h1>Deployer</h1>'
              : prepareProfileTemplate(pickrStubScript),
        });
      } else if (pathname === '/api/users/Alice/profile') {
        if (!profileFound) {
          await route.fulfill({ status: 404, json: { detail: 'Not found' } });
          return;
        }
        await route.fulfill({
          json: {
            username: 'Alice',
            is_owner: true,
            bio,
            profile_color: profileColor,
            stats_visible: statsVisible,
            stats: { lifetime_credits_used: 12.34, theater_views: 9 },
            stamps,
          },
        });
      } else if (pathname === '/api/users/me/profile') {
        const body = JSON.parse(route.request().postData() ?? '{}') as {
          bio: string;
          stats_visible: boolean;
          profile_color: string;
        };
        savedProfiles.push(body);
        bio = body.bio;
        statsVisible = body.stats_visible;
        profileColor = body.profile_color;
        await route.fulfill({ json: { status: 'ok' } });
      } else if (pathname === '/api/credit-gifts') {
        await route.fulfill({
          json: {
            link: 'https://canvas.test/gift/tok123',
            expires_at: '2026-12-01T00:00:00Z',
          },
        });
      } else if (pathname === '/api/users/me/stamps') {
        stamps = [...stamps, { id: 8, name: 'dragon-badge', url: '/api/stamps/8' }];
        await route.fulfill({ json: { id: 8 } });
      } else if (pathname === '/api/users/me/stamps/7') {
        stamps = stamps.filter((stamp) => stamp.id !== 7);
        await route.fulfill({ json: { deleted: true } });
      } else if (pathname === '/api/users/me') {
        accountDeleted = true;
        await route.fulfill({ json: { deleted: true } });
      } else if (pathname === '/api/friends') {
        await route.fulfill({ json: [] });
      } else if (pathname === '/api/pricing') {
        await route.fulfill({ json: { image_credit_rate: 2 } });
      } else if (pathname.startsWith('/api/stamps/')) {
        await route.fulfill({
          contentType: 'image/svg+xml',
          body: '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"></svg>',
        });
      } else {
        await route.fulfill({ json: {} });
      }
    },
  );

  await page.goto(`${CANVAS_ORIGIN}/users/Alice`);
  await expect(page.locator('#bio-display')).toHaveText('Initial bio');
  await expect(page.locator('.stats')).toContainText('12.3');

  // Bio editing
  await page.getByRole('button', { name: 'Edit bio' }).click();
  await page.locator('#bio-inline').fill('Updated bio');
  await page.getByRole('button', { name: 'Save bio' }).click();
  await expect(page.locator('#bio-display')).toHaveText('Updated bio');

  // Settings tab (Pickr + stats checkbox)
  await page.getByRole('tab', { name: /Settings/ }).click();
  await page.evaluate(() => {
    (window as unknown as { __pickColor: (hex: string) => void }).__pickColor('#22c55e');
  });
  await page.locator('#visible').uncheck();
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect.poll(() => savedProfiles.length).toBe(2);
  expect(savedProfiles[1]).toEqual({
    bio: 'Updated bio',
    stats_visible: false,
    profile_color: '#22c55e',
  });

  // Gift credits validation + creation
  await page.getByRole('tab', { name: /Gift credits/ }).click();
  await page.locator('#gift-credits').fill('0');
  await page.getByRole('button', { name: 'Create gift link' }).click();
  await expect(page.locator('#gift-status')).toHaveText('Enter a positive number of credits.');
  await page.locator('#gift-credits').fill('5');
  await page.getByRole('button', { name: 'Create gift link' }).click();
  await expect(page.locator('#gift-status a')).toHaveAttribute(
    'href',
    'https://canvas.test/gift/tok123',
  );

  // Stamp upload + delete
  await page.getByRole('tab', { name: /Stamps/ }).click();
  await page.locator('#stamp-upload summary').click();
  await page.getByRole('button', { name: 'Upload', exact: true }).click();
  await expect(page.locator('#stamp-upload-status')).toHaveText(
    'Please choose an image file first.',
  );
  await page.locator('#stamp-file-input').setInputFiles({
    name: 'dragon-badge.png',
    mimeType: 'image/png',
    buffer: Buffer.from([137, 80, 78, 71]),
  });
  await expect(page.locator('#stamp-name-input')).toHaveValue('dragon-badge');
  await page.getByRole('button', { name: 'Upload', exact: true }).click();
  await expect(page.locator('.stamp-title')).toHaveText(['Rune', 'dragon-badge']);

  page.once('dialog', (dialog) => dialog.accept());
  await page.locator('.stamp-delete-btn').first().click();
  await expect(page.locator('.stamp-title')).toHaveText(['dragon-badge']);

  // Danger zone deletion confirmation + redirect
  await page.getByRole('tab', { name: /Danger zone/ }).click();
  await page.getByRole('button', { name: 'Delete account' }).click();
  const confirmDelete = page.getByRole('button', { name: 'Permanently delete' });
  await expect(confirmDelete).toBeDisabled();
  await page.locator('#delete-confirm-input').fill('Alice');
  await expect(confirmDelete).toBeEnabled();
  await confirmDelete.click();
  await expect.poll(() => accountDeleted).toBe(true);
  await expect(page).toHaveURL(`${CANVAS_ORIGIN}/deploy`, { timeout: 5000 });

  // 404 profile state
  profileFound = false;
  await page.goto(`${CANVAS_ORIGIN}/users/Alice`);
  await expect(page.getByRole('heading', { name: 'Profile not found' })).toBeVisible();
});
