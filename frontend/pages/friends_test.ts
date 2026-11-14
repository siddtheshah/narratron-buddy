import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  expect,
  TEMPLATES_DIR,
  test,
} from '../../testing/ui/canvas_fixtures';

type FriendsModule = typeof import('/static/generated/pages/friends.js');

interface FriendRelationship {
  username: string;
  status: 'incoming' | 'accepted' | 'outgoing';
}

interface MockFriendTheater {
  username: string;
  theater_id: string;
  name: string;
  online: boolean;
  active_viewers?: string[];
}

interface FriendsHarnessWindow extends Window {
  authModalCalls?: Array<string | undefined>;
}

const FRIENDS_MODULE_URL = '/static/generated/pages/friends.js';

function prepareTemplateHtml(templateName: string, authScript: string): string {
  let html = fs.readFileSync(path.join(TEMPLATES_DIR, templateName), 'utf-8');
  html = html.replace(
    /<script[^>]+src="(?!\/static\/generated\/pages\/(?:friends|join-splash|profile)\.js)[^"]+"[^>]*><\/script>/g,
    '',
  );
  html = html.replace(/<link[^>]+href="https?:\/\/[^>]*>/g, '');
  html = html.replace('<head>', `<head><script>${authScript}</script>`);
  return html;
}

test('profile requests, friend tabs, active theater browser, responsive layout, and one-click join', async ({
  canvas,
  page,
}) => {
  await page.setViewportSize({ width: 1100, height: 900 });

  let relationships: FriendRelationship[] = [];
  const joins: string[] = [];
  const longName = 'Forest story with a very long theater name that should be truncated';
  const initialTheaters: MockFriendTheater[] = [
    { username: 'Dana', theater_id: 'dana-stage', name: 'Sun story', online: true },
    {
      username: 'Bobby',
      theater_id: 'bob-stage',
      name: 'Dragon story',
      online: true,
      active_viewers: ['Bobby', '<Alice>'],
    },
    { username: 'Bobby', theater_id: 'bob-other', name: longName, online: true },
    { username: 'Bobby', theater_id: 'bob-offline', name: 'Sleeping story', online: false },
    { username: 'Carol', theater_id: 'carol-stage', name: 'Moon story', online: false },
  ];
  let theaters: MockFriendTheater[] = initialTheaters.map((theater) => ({ ...theater }));

  const authHeadScript = `
    function getAuthState() { return Promise.resolve({ authenticated: true, user: { username: 'Alice' } }); }
    function Splide() { this.mount = () => {}; }
  `;

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && !url.pathname.startsWith('/static/'),
    async (route: Route) => {
      const url = new URL(route.request().url());
      const pathname = url.pathname;
      if (pathname === '/favicon.png') {
        await route.fulfill({ status: 204 });
      } else if (pathname === '/api/friends') {
        await route.fulfill({ json: relationships });
      } else if (pathname === '/api/friends/Bobby') {
        const payload = JSON.parse(route.request().postData() ?? '{}') as { action?: string };
        if (payload.action === 'request') {
          relationships = [{ username: 'Bobby', status: 'outgoing' }];
        } else {
          relationships = [];
        }
        await route.fulfill({ json: { status: 'ok' } });
      } else if (pathname.startsWith('/api/users/')) {
        const owner = pathname.includes('/Alice/');
        await route.fulfill({
          json: {
            username: owner ? 'Alice' : 'Bobby',
            is_owner: owner,
            bio: '',
            stats: null,
            stamps: [],
          },
        });
      } else if (pathname === '/api/friend-theaters') {
        await route.fulfill({ json: theaters });
      } else if (pathname === '/api/friend-theaters/bob-stage/join') {
        joins.push(pathname);
        await route.fulfill({ json: { theater_id: 'bob-stage' } });
      } else if (pathname === '/canvas') {
        await route.fulfill({ contentType: 'text/html', body: '<h1>Theater canvas</h1>' });
      } else if (pathname === '/users/Bobby' || pathname === '/users/Alice' || pathname === '/join') {
        const template = pathname === '/join' ? 'join_splash.html' : 'profile.html';
        await route.fulfill({
          contentType: 'text/html',
          body: prepareTemplateHtml(template, authHeadScript),
        });
      } else {
        await route.fulfill({ contentType: 'text/plain', body: '' });
      }
    },
  );

  await page.goto(`${CANVAS_ORIGIN}/users/Bobby`);
  await page.getByRole('button', { name: 'Send Friend Request', exact: true }).click();
  await expect(page.getByText('Friend request sent', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Cancel request', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Send Friend Request', exact: true })).toBeVisible();

  relationships = [{ username: 'Bobby', status: 'incoming' }];
  await page.goto(`${CANVAS_ORIGIN}/users/Alice`);
  await page.getByRole('tab', { name: /Friends & requests/ }).click();
  await expect(page.getByRole('heading', { name: 'Incoming requests' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Accept', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Decline', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Accept', exact: true })).toHaveCount(0);

  await page.goto(`${CANVAS_ORIGIN}/join`);
  const groups = page.locator('#friend-theaters-list .friend-username');
  await expect(groups).toHaveText(['Bobby', 'Dana']);
  await expect(page.locator('#friend-active-theaters')).toContainText('Select a friend');

  const dragon = page.getByRole('radio', { name: 'Dragon story', exact: true });
  await expect(dragon).not.toBeVisible();
  await groups.first().click();
  await expect(dragon).toBeVisible();
  await expect(page.locator('.friend-theater-viewers').first()).toHaveText('2 viewers');
  await expect(page.locator('.friend-theater-viewers').first()).toHaveAttribute(
    'title',
    'Viewing: @Bobby, @<Alice>',
  );
  await expect(page.locator('.friend-theater-viewers').last()).toHaveText('0 viewers');
  await expect(page.getByRole('radio', { name: longName, exact: true })).toBeVisible();
  await expect(page.locator('#friend-theaters-list')).not.toContainText('Sleeping story');
  await expect(page.locator('#friend-theaters-list')).not.toContainText('Carol');
  await expect(page.getByRole('radio', { name: 'Sun story', exact: true })).not.toBeVisible();
  await expect(groups.first()).toHaveAttribute('aria-pressed', 'true');

  let usernamesBox = await page.locator('.friend-usernames').boundingBox();
  let theatersBox = await page.locator('#friend-active-theaters').boundingBox();
  expect(usernamesBox).not.toBeNull();
  expect(theatersBox).not.toBeNull();
  expect(usernamesBox!.x + usernamesBox!.width).toBeLessThanOrEqual(theatersBox!.x + 1);

  await groups.getByText('Dana', { exact: true }).click();
  await expect(page.getByRole('radio', { name: 'Sun story', exact: true })).toBeVisible();
  await expect(dragon).not.toBeVisible();
  await groups.first().focus();
  await page.keyboard.press('Enter');
  await expect(dragon).toBeVisible();

  // Polling preserves the selected friend.
  theaters[1].active_viewers = ['Bobby'];
  await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/friend-theaters')),
    page.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))),
  ]);
  await expect(page.locator('.friend-theater-viewers').first()).toHaveText('1 viewer');
  await expect(dragon).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('#friend-theaters')).toHaveAttribute('aria-hidden', 'true');
  await page.getByRole('button', { name: "Friends' theaters", exact: true }).click();
  await expect(page.locator('#join-adventure')).toHaveAttribute('aria-hidden', 'true');
  await page.waitForFunction(
    () =>
      (document.getElementById('friend-theaters')?.getBoundingClientRect().right ?? Infinity) <=
      window.innerWidth,
  );
  await page
    .locator('.join-options-track')
    .evaluate((el) => Promise.all(el.getAnimations().map((animation) => animation.finished)));
  await expect(page.getByRole('button', { name: 'Join / adventure', exact: true })).toBeFocused();

  usernamesBox = await page.locator('.friend-usernames').boundingBox();
  theatersBox = await page.locator('#friend-active-theaters').boundingBox();
  expect(usernamesBox).not.toBeNull();
  expect(theatersBox).not.toBeNull();
  expect(usernamesBox!.x + usernamesBox!.width).toBeLessThanOrEqual(theatersBox!.x + 1);
  expect(theatersBox!.x + theatersBox!.width).toBeLessThanOrEqual(390);

  const longTitle = page.locator('.friend-theater-name').last();
  await expect(longTitle).toHaveAttribute('title', longName);
  expect(
    await longTitle.evaluate(
      (el) =>
        el.scrollWidth > el.clientWidth && getComputedStyle(el).textOverflow === 'ellipsis',
    ),
  ).toBe(true);
  const nameBox = await longTitle.boundingBox();
  const countBox = await page.locator('.friend-theater-viewers').last().boundingBox();
  expect(nameBox).not.toBeNull();
  expect(countBox).not.toBeNull();
  expect(nameBox!.x + nameBox!.width).toBeLessThanOrEqual(countBox!.x);

  await page.getByRole('button', { name: 'Join / adventure', exact: true }).click();
  await expect(page.locator('#friend-theaters')).toHaveAttribute('aria-hidden', 'true');
  await expect(page.getByRole('button', { name: "Friends' theaters", exact: true })).toBeFocused();

  await page.setViewportSize({ width: 1100, height: 900 });
  const join = page.getByRole('button', { name: 'Join theater', exact: true });
  await expect(join).toBeDisabled();
  await expect(page.locator('#friend-active-theaters h3')).toHaveCount(0);
  await dragon.check();
  await expect(join).toBeEnabled();
  expect(joins).toEqual([]);

  const originalTheaters = theaters.map((theater) => ({ ...theater }));
  for (let index = 0; index < 20; index += 1) {
    theaters.push({
      username: `Friend ${String(index).padStart(2, '0')}`,
      theater_id: `stage-${index}`,
      name: 'Story',
      online: true,
    });
  }
  await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/friend-theaters')),
    page.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))),
  ]);
  await expect(groups).toHaveCount(22);
  await expect(dragon).toBeChecked();
  await expect(join).toBeEnabled();
  await expect(page.locator('#friend-theaters-pagination')).toHaveCount(0);
  expect(
    await page.locator('.friend-usernames').evaluate((el) => el.scrollHeight > el.clientHeight),
  ).toBe(true);
  usernamesBox = await page.locator('.friend-usernames').boundingBox();
  const browserBox = await page.locator('.friend-theaters-browser').boundingBox();
  expect(usernamesBox).not.toBeNull();
  expect(browserBox).not.toBeNull();
  expect(Math.abs(usernamesBox!.height - (browserBox!.height - 2))).toBeLessThan(1);

  await groups.last().click();
  await expect(join).toBeDisabled();

  theaters = originalTheaters;
  await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/friend-theaters')),
    page.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))),
  ]);
  await expect(groups).toHaveText(['Bobby', 'Dana']);
  await expect(join).toBeDisabled();

  await groups.first().click();
  await dragon.check();
  await join.click();
  await expect(page).toHaveURL(`${CANVAS_ORIGIN}/canvas?theater_id=bob-stage`);
  expect(joins).toEqual(['/api/friend-theaters/bob-stage/join']);

  theaters = theaters.filter((theater) => !theater.online);
  await page.goto(`${CANVAS_ORIGIN}/join`);
  await expect(page.locator('#friend-theaters-list')).toContainText(
    'You do not have any friends yet.',
  );
  const giftLink = page.getByRole('link', { name: 'Create a gift link', exact: true });
  await expect(giftLink).toHaveAttribute('href', '/users/Alice#gifts');
  await giftLink.click();
  await expect(page).toHaveURL(`${CANVAS_ORIGIN}/users/Alice#gifts`);
  await expect(page.getByRole('heading', { name: 'Gift credits', exact: true })).toBeVisible();
  await expect(groups).toHaveCount(0);
});

test('friends unauthenticated/accepted-empty states and error recovery', async ({
  canvas,
  page,
}) => {
  let authenticated = false;
  let friendships: FriendRelationship[] = [{ username: 'Bobby', status: 'accepted' }];
  let joinShouldFail = true;

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && !url.pathname.startsWith('/static/'),
    async (route: Route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/api/friends') {
        await route.fulfill({ json: friendships });
      } else if (pathname === '/api/friend-theaters') {
        await route.fulfill({
          json: joinShouldFail
            ? [{ username: 'Bobby', theater_id: 'bob-stage', name: 'Dragon', online: true }]
            : [],
        });
      } else if (pathname === '/api/friend-theaters/bob-stage/join') {
        await route.fulfill({ status: 404, json: { detail: 'Theater went offline.' } });
      } else if (pathname === '/api/friends/Bobby') {
        await route.fulfill({ status: 500, json: { detail: 'Could not update right now.' } });
      } else {
        await route.fulfill({ json: {} });
      }
    },
  );

  await canvas.mount({
    html: `
      <section id="friend-theaters">
        <div id="friend-theaters-list"></div>
        <div class="friend-theaters-footer" hidden>
          <button type="button" id="friend-theater-join" disabled>Join theater</button>
        </div>
        <p id="friend-theaters-status" role="status"></p>
      </section>
      <div class="profile-shell">
        <div class="identity"></div>
      </div>
    `,
    moduleScripts: [
      `
      window.authModalCalls = [];
      window.openAuthModal = (tab) => window.authModalCalls.push(tab);
      window.getAuthState = async () => (${JSON.stringify({ authenticated: false })});
      import ${JSON.stringify(FRIENDS_MODULE_URL)};
      `,
    ],
  });

  // Signed-out visitors see Sign in button calling openAuthModal('login').
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  expect(
    await page.evaluate(() => (window as unknown as FriendsHarnessWindow).authModalCalls),
  ).toEqual(['login']);

  // Signed-in user with a join failure recovers controls and sees error detail.
  authenticated = true;
  await page.evaluate(
    (isAuth: boolean) => {
      window.getAuthState = async () => ({
        authenticated: isAuth,
        user: { username: 'Alice' },
      });
    },
    authenticated,
  );
  await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/friend-theaters')),
    page.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))),
  ]);
  await page.locator('#friend-theaters-list .friend-username').first().click();
  await page.getByRole('radio', { name: 'Dragon', exact: true }).check();
  await page.getByRole('button', { name: 'Join theater', exact: true }).click();
  await expect(page.locator('#friend-theaters-status')).toHaveText('Theater went offline.');
  await expect(page.getByRole('button', { name: 'Join theater', exact: true })).toBeEnabled();

  // Signed-in user with accepted friends but no online theaters sees manage-friends link.
  joinShouldFail = false;
  await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/friend-theaters')),
    page.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))),
  ]);
  await expect(page.locator('#friend-theaters-list')).toContainText(
    'No friends have active theaters right now.',
  );
  await expect(page.getByRole('link', { name: 'your friends' })).toHaveAttribute(
    'href',
    '/users/Alice',
  );

  // Profile friendship error re-enables button and shows server detail.
  await page.evaluate(async () => {
    await window.setupProfileFriends?.({ username: 'Bobby', is_owner: false });
  });
  const removeBtn = page.getByRole('button', { name: 'Remove friend', exact: true });
  await removeBtn.click();
  await expect(page.locator('.profile-shell [role="status"]')).toHaveText(
    'Could not update right now.',
  );
  await expect(removeBtn).toBeEnabled();

  // Unauthenticated 401 on /api/friends displays sign-in guidance.
  await canvas.serveRoute('/api/friends', async (route: Route) => {
    await route.fulfill({ status: 401, json: { detail: 'Unauthorized' } });
  });
  await page.evaluate(async () => {
    document.querySelector('.profile-shell')!.innerHTML = '<div class="identity"></div>';
    await window.setupProfileFriends?.({ username: 'Bobby', is_owner: false });
  });
  await expect(page.locator('.profile-shell')).toContainText('Sign in to send a friend request.');
  friendships = [];
});
