import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  TEMPLATES_DIR,
  expect,
  test,
} from '../../testing/ui/canvas_fixtures';
import type { AdventureSummary } from './adventures.js';

interface AdventuresTestWindow extends Window {
  __authModalTabs?: Array<string | undefined>;
  __alerts?: string[];
  __xssExecuted?: boolean;
}

function loadPreparedAdventuresHtml(headInject = ''): string {
  let html = fs.readFileSync(path.join(TEMPLATES_DIR, 'adventures.html'), 'utf-8');
  html = html.replace(
    /<script[^>]+src="(?!\/static\/generated\/pages\/adventures\.js)[^"]+"[^>]*><\/script>/g,
    '',
  );
  html = html.replace(/<link[^>]+href="https?:\/\/[^>]*>/g, '');
  if (headInject) {
    html = html.replace('<head>', `<head><script>${headInject}</script>`);
  }
  return html;
}

const SAMPLE_ADVENTURES: readonly AdventureSummary[] = [
  {
    id: 'starfall-station',
    title: 'Starfall Station',
    description: 'Orbital mystery aboard a drifting research dock.',
    genre: 'Sci-Fi',
    tags: ['space', 'mystery'],
    created_at: '2026-08-15T12:00:00Z',
    lore_count: 4,
    track_count: 2,
    reference_count: 3,
    difficulty: 'Medium',
  },
  {
    id: 'frost-fjord',
    title: 'Frost Fjord',
    description: 'Cross the frozen sea in search of the Ash Spear.',
    genre: 'Norse Fantasy',
    tags: ['norse', 'voyage'],
    created_at: '2026-09-01T12:00:00Z',
    lore_count: 2,
    track_count: 1,
    reference_count: 0,
  },
  {
    id: 'ashen-keep',
    title: 'Ashen <img src=x onerror="window.__xssExecuted=true"> Keep',
    description: 'Gothic siege <script>window.__xssExecuted=true</script> story.',
    genre: 'Dark Fantasy',
    tags: ['showcase', 'castle'],
    created_at: 'invalid-date',
    lore_count: 0,
    track_count: 0,
    reference_count: 0,
  },
];

test('adventures page loads catalog, filters by search and tag, escapes content, and retries errors', async ({
  canvas,
  page,
}) => {
  const rawTemplate = fs.readFileSync(path.join(TEMPLATES_DIR, 'adventures.html'), 'utf-8');
  expect(rawTemplate).toContain('<script type="module" src="/static/generated/shared/auth-flow.js"></script>');
  expect(rawTemplate).toContain('<script type="module" src="/static/generated/pages/adventures.js"></script>');
  expect(rawTemplate).not.toContain('oninput="filterAdventures()"');
  expect(rawTemplate).not.toContain('onclick="selectTag(');

  let failCatalog = true;
  const html = loadPreparedAdventuresHtml();

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && !url.pathname.startsWith('/static/'),
    async (route: Route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/adventures') {
        await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
      } else if (pathname === '/api/adventures') {
        if (failCatalog) {
          await route.fulfill({ status: 500, json: { detail: 'Catalog offline' } });
          return;
        }
        await route.fulfill({ json: SAMPLE_ADVENTURES });
      } else if (pathname === '/api/adventures/starfall-station/cover') {
        await route.fulfill({ status: 404, body: 'missing' });
      } else if (pathname.startsWith('/api/adventures/') && pathname.endsWith('/cover')) {
        await route.fulfill({
          contentType: 'image/svg+xml',
          body: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>',
        });
      } else if (pathname === '/favicon.png') {
        await route.fulfill({ status: 204 });
      } else {
        await route.fulfill({ contentType: 'text/plain', body: '' });
      }
    },
  );

  await page.goto(`${CANVAS_ORIGIN}/adventures`);

  // Initial load failure renders error state with working Retry button.
  await expect(page.locator('#adventuresGrid')).toContainText('Could not load adventures');
  failCatalog = false;
  await page.getByRole('button', { name: '↻ Retry', exact: true }).click();

  await expect(page.locator('#adventuresGrid .card')).toHaveCount(3);
  expect(
    await page.evaluate(() => (window as unknown as AdventuresTestWindow).__xssExecuted === undefined),
  ).toBe(true);

  // Missing cover falls back to placeholder, invalid date falls back to 'Recently Added'.
  await expect(page.locator('#card-starfall-station .card-cover-placeholder')).toBeVisible();
  await expect(page.locator('#card-starfall-station .meta-pill')).toHaveCount(4);
  await expect(page.locator('#card-ashen-keep .badge-date')).toContainText('Recently Added');
  await expect(page.locator('#card-starfall-station a.btn-secondary')).toHaveAttribute(
    'href',
    '/deploy?creation_path=adventure&preset=starfall-station',
  );

  // Tag buttons highlight properly (including Norse -> Norse Fantasy) and filter cards.
  const norseBtn = page.getByRole('button', { name: 'Norse', exact: true });
  await norseBtn.click();
  await expect(norseBtn).toHaveClass(/active/);
  await expect(page.locator('#adventuresGrid .card')).toHaveCount(1);
  await expect(page.locator('#card-frost-fjord')).toBeVisible();

  await page.getByRole('button', { name: 'Showcase', exact: true }).click();
  await expect(page.locator('#adventuresGrid .card')).toHaveCount(1);
  await expect(page.locator('#card-ashen-keep')).toBeVisible();

  // Search query with no match shows empty state and Clear Filters resets both query and tag.
  await page.locator('#searchInput').fill('nonexistent-xyz');
  await expect(page.locator('#adventuresGrid')).toContainText('No adventures matched your search');
  await page.getByRole('button', { name: 'Clear Filters', exact: true }).click();
  await expect(page.locator('#searchInput')).toHaveValue('');
  await expect(page.getByRole('button', { name: 'All Adventures', exact: true })).toHaveClass(/active/);
  await expect(page.locator('#adventuresGrid .card')).toHaveCount(3);
});

test('adventures Play Adventure prompts login if signed out, auto-resumes on auth, and handles deploy errors', async ({
  canvas,
  page,
}) => {
  const html = loadPreparedAdventuresHtml(`
    window.__authModalTabs = [];
    window.__alerts = [];
    window.currentUser = null;
    window.openAuthModal = (tab) => window.__authModalTabs.push(tab);
    window.alert = (msg) => window.__alerts.push(String(msg));
  `);

  let deployBehavior: 'fail' | 'success' = 'fail';
  const deployRequests: Array<string | null> = [];

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && !url.pathname.startsWith('/static/'),
    async (route: Route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/adventures') {
        await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
      } else if (pathname === '/api/adventures') {
        await route.fulfill({ json: [SAMPLE_ADVENTURES[0]] });
      } else if (pathname.endsWith('/cover')) {
        await route.fulfill({
          contentType: 'image/svg+xml',
          body: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>',
        });
      } else if (pathname === '/api/theaters/create-and-deploy') {
        deployRequests.push(route.request().postData());
        if (deployBehavior === 'fail') {
          await route.fulfill({ status: 402, json: { detail: 'Insufficient credits' } });
          return;
        }
        await route.fulfill({ json: { theater_id: 'adv-stage-42' } });
      } else if (pathname === '/canvas') {
        await route.fulfill({
          contentType: 'text/html; charset=utf-8',
          body: '<h1>Canvas Loaded</h1>',
        });
      } else if (pathname === '/favicon.png') {
        await route.fulfill({ status: 204 });
      } else {
        await route.fulfill({ contentType: 'text/plain', body: '' });
      }
    },
  );

  await page.goto(`${CANVAS_ORIGIN}/adventures`);
  const playBtn = page.locator('#card-starfall-station').getByRole('button', { name: /Play Adventure/ });
  await playBtn.click();

  expect(
    await page.evaluate(() => (window as unknown as AdventuresTestWindow).__authModalTabs),
  ).toEqual(['login']);
  expect(deployRequests).toEqual([]);

  // Completing login triggers pending deploy; failed deploy hides overlay and alerts.
  await page.evaluate(() => {
    window.currentUser = { id: 7, username: 'adventurer' };
    window.onAuthSuccess?.('login', { user: window.currentUser });
  });

  await expect.poll(() => deployRequests.length).toBe(1);
  expect(deployRequests[0]).toContain('starfall-station');
  await expect(page.locator('#deployOverlay')).not.toHaveClass(/show/);
  await expect
    .poll(async () =>
      page.evaluate(() => (window as unknown as AdventuresTestWindow).__alerts),
    )
    .toEqual(['Could not launch adventure: Insufficient credits']);

  // Successful deploy navigates to /canvas?theater_id=...
  deployBehavior = 'success';
  await playBtn.click();
  await expect(page).toHaveURL(`${CANVAS_ORIGIN}/canvas?theater_id=adv-stage-42`);
});
