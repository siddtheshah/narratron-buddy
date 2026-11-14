import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  TEMPLATES_DIR,
  expect,
  test,
} from '../../testing/ui/canvas_fixtures';
import type { StatsDashboardData } from './stats.js';

function loadPreparedStatsHtml(): string {
  let html = fs.readFileSync(path.join(TEMPLATES_DIR, 'stats.html'), 'utf-8');
  html = html.replace(
    /<script[^>]+src="(?!\/static\/generated\/pages\/stats\.js)[^"]+"[^>]*><\/script>/g,
    '',
  );
  html = html.replace(/<link[^>]+href="https?:\/\/[^>]*>/g, '');
  return html;
}

const INITIAL_STATS: StatsDashboardData = {
  total_accounts: 1234,
  active_users_7d: 89,
  total_theater_views: 5678,
  theater_views_7d: 432,
  daily_views_7d: [
    { date: '2026-11-07', views: 10 },
    { date: '2026-11-08', views: 50 },
  ],
  daily_active_users_7d: [
    { date: '2026-11-07', active_users: 4 },
    { date: '2026-11-08', active_users: 12 },
  ],
  top_viewed_theaters: [
    { theater_id: 'dragon-den', name: 'Dragon Den <script>', views: 320 },
    { theater_id: 'quiet-inn', views: 45 },
  ],
};

const UPDATED_STATS: StatsDashboardData = {
  total_accounts: 2000,
  active_users_7d: 100,
  total_theater_views: 6000,
  theater_views_7d: 500,
  daily_views_7d: [],
  daily_active_users_7d: [],
  top_viewed_theaters: [],
};

test('stats page renders metrics, charts, top theaters, refresh, and join-key modal validation', async ({
  canvas,
  page,
}) => {
  const rawTemplate = fs.readFileSync(path.join(TEMPLATES_DIR, 'stats.html'), 'utf-8');
  expect(rawTemplate).toContain('<script type="module" src="/static/generated/shared/auth-flow.js"></script>');
  expect(rawTemplate).toContain('<script type="module" src="/static/generated/pages/stats.js"></script>');
  expect(rawTemplate).not.toContain('onclick="fetchStats()"');

  const html = loadPreparedStatsHtml();
  let statsPayload: StatsDashboardData = INITIAL_STATS;
  const submittedKeys: string[] = [];

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && !url.pathname.startsWith('/static/'),
    async (route: Route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/stats') {
        await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
      } else if (pathname === '/api/stats') {
        await route.fulfill({ json: statsPayload });
      } else if (pathname === '/api/theaters/resolve-join-key') {
        const body = JSON.parse(route.request().postData() ?? '{}') as { join_key?: string };
        const key = body.join_key ?? '';
        submittedKeys.push(key);
        if (key === 'KEY-OTHER1') {
          await route.fulfill({ json: { theater_id: 'quiet-inn' } });
        } else if (key === 'KEY-BADKEY') {
          await route.fulfill({ status: 404, json: { detail: 'Unknown Join Key.' } });
        } else {
          await route.fulfill({ json: { theater_id: 'dragon-den' } });
        }
      } else if (pathname === '/canvas') {
        await route.fulfill({
          contentType: 'text/html; charset=utf-8',
          body: '<h1>Canvas Target</h1>',
        });
      } else if (pathname === '/favicon.png') {
        await route.fulfill({ status: 204 });
      } else {
        await route.fulfill({ contentType: 'text/plain', body: '' });
      }
    },
  );

  await page.goto(`${CANVAS_ORIGIN}/stats`);

  await expect(page.locator('#val-accounts')).toHaveText((1234).toLocaleString());
  await expect(page.locator('#val-active-7d')).toHaveText('89');
  await expect(page.locator('#val-total-views')).toHaveText((5678).toLocaleString());
  await expect(page.locator('#val-views-7d')).toHaveText('432');

  await expect(page.locator('#chart-daily-views .bar-col')).toHaveCount(2);
  await expect(page.locator('#chart-daily-views .bar-label').first()).toHaveText('11-07');
  await expect(page.locator('#chart-daily-users .bar-fill.alt')).toHaveCount(2);

  const rows = page.locator('#top-theaters-body tr');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0).locator('.theater-name')).toHaveText('Dragon Den <script>');
  await expect(rows.nth(0).locator('.theater-id-tag')).toHaveText('dragon-den');
  await expect(rows.nth(1).locator('.theater-name')).toHaveText('quiet-inn');

  // Open join modal for dragon-den and verify Escape + Cancel + validation + success navigation.
  await rows.nth(0).getByRole('button', { name: 'View Canvas', exact: true }).click();
  const modal = page.locator('#join-modal');
  await expect(modal).toBeVisible();
  await expect(page.locator('#join-narratron-theater-id')).toHaveText('dragon-den');

  const keyInput = page.locator('#join-key-input');
  await expect(keyInput).toBeFocused();
  await keyInput.press('Escape');
  await expect(modal).toBeHidden();

  await rows.nth(0).getByRole('button', { name: 'View Canvas', exact: true }).click();
  await page.locator('#join-submit').click();
  await expect(page.locator('#join-error')).toHaveText('Please enter a Join Key.');

  await keyInput.fill('KEY-BADKEY');
  await page.locator('#join-submit').click();
  await expect(page.locator('#join-error')).toHaveText('Unknown Join Key.');

  await keyInput.fill('KEY-OTHER1');
  await page.locator('#join-submit').click();
  await expect(page.locator('#join-error')).toHaveText(
    'That Join Key belongs to a different theater.',
  );

  await page.locator('#join-cancel').click();
  await expect(modal).toBeHidden();

  // Refresh button fetches updated stats and renders empty states cleanly.
  statsPayload = UPDATED_STATS;
  await page.locator('#refresh-stats-btn').click();
  await expect(page.locator('#val-accounts')).toHaveText((2000).toLocaleString());
  await expect(page.locator('#chart-daily-views')).toContainText('No chart data available');
  await expect(page.locator('#top-theaters-body')).toContainText(
    'No theater view history recorded yet.',
  );

  // Restore non-empty stats and verify Enter key submits and navigates to canvas.
  statsPayload = INITIAL_STATS;
  await page.locator('#refresh-stats-btn').click();
  await page.locator('#top-theaters-body tr').nth(0).getByRole('button', { name: 'View Canvas' }).click();
  await keyInput.fill('KEY-DRAGON');
  await keyInput.press('Enter');
  await expect(page).toHaveURL(`${CANVAS_ORIGIN}/canvas?theater_id=dragon-den`);
  expect(submittedKeys).toEqual(['KEY-BADKEY', 'KEY-OTHER1', 'KEY-DRAGON']);
});
