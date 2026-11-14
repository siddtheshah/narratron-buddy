import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import { expect, TEMPLATES_DIR, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';

const DOCS_NAV_MODULE_URL = '/static/generated/pages/docs-navigation.js';

function loadDocsSidebarMarkup(): string {
  return fs.readFileSync(path.join(TEMPLATES_DIR, 'docs_sidebar.html'), 'utf-8');
}

const DOCS_SIDEBAR_MARKUP = loadDocsSidebarMarkup();

async function mountDocsSidebar(
  canvas: CanvasHarness,
  moduleUrl: string = DOCS_NAV_MODULE_URL,
  extraHtml = '',
): Promise<void> {
  await canvas.mount({
    stylesheets: ['/static/css/docs-navigation.css'],
    html: `${DOCS_SIDEBAR_MARKUP}${extraHtml}`,
    moduleScripts: [`import ${JSON.stringify(moduleUrl)};`],
  });
}

test('docs navigation searches docs with debounce', async ({
  canvas,
  page,
}) => {
  const queries: string[] = [];
  await canvas.serveRoute(
    (url: URL) => url.pathname === '/api/docs/search',
    async (route: Route) => {
      const url = new URL(route.request().url());
      const q = url.searchParams.get('q') ?? '';
      queries.push(`${q}:${url.searchParams.get('limit')}`);
      if (q === 'empty') {
        await route.fulfill({ json: { results: [] } });
        return;
      }
      if (q === 'fail') {
        await route.fulfill({ status: 503, json: { detail: 'Unavailable' } });
        return;
      }
      await route.fulfill({
        json: {
          results: [
            {
              href: '/docs/theater-yaml#story-planning',
              title: 'Story planning',
              page_title: 'theater.yaml reference',
              excerpt: 'Configure character voicing and pacing.',
            },
            {
              href: '/docs/about',
              title: 'About Narratron',
              page_title: 'About Narratron',
              excerpt: 'Overview of live interactive storytelling.',
            },
          ],
        },
      });
    },
  );

  await mountDocsSidebar(canvas, DOCS_NAV_MODULE_URL);

  const input = page.locator('#docsSearch');
  const results = page.locator('#docsSearchResults');

  await input.fill('voicing');
  await expect(results.locator('a')).toHaveCount(2);
  expect(queries).toEqual(['voicing:8']);

  const firstLink = results.locator('a').first();
  await expect(firstLink).toHaveAttribute('href', '/docs/theater-yaml#story-planning');
  await expect(firstLink.locator('strong')).toHaveText('Story planning');
  await expect(firstLink.locator('small')).toHaveText('theater.yaml reference');
  await expect(firstLink.locator('span')).toHaveText('Configure character voicing and pacing.');

  const secondLink = results.locator('a').nth(1);
  await expect(secondLink.locator('strong')).toHaveText('About Narratron');
  await expect(secondLink.locator('small')).toHaveCount(0);

  await input.fill('empty');
  await expect(results.locator('.docs-search-empty')).toHaveText('No matching docs');

  await input.fill('fail');
  await expect(results.locator('.docs-search-empty')).toHaveText(
    'Search is temporarily unavailable',
  );

  await input.fill('');
  await expect(results).toBeEmpty();
});

test('docs navigation aborts in-flight searches on clear and handles slash shortcut safely', async ({
  canvas,
  page,
}) => {
  const searchGate: { resolve?: () => void } = {};
  let searchCalls = 0;

  await canvas.serveRoute(
    (url: URL) => url.pathname === '/api/docs/search',
    async (route: Route) => {
      searchCalls += 1;
      await new Promise<void>((resolve) => {
        searchGate.resolve = resolve;
      });
      await route.fulfill({ json: { results: [] } });
    },
  );

  await mountDocsSidebar(
    canvas,
    DOCS_NAV_MODULE_URL,
    '<input id="other-input" type="text"><textarea id="other-textarea"></textarea><button id="outside-btn" type="button">Outside</button>',
  );

  const input = page.locator('#docsSearch');
  const results = page.locator('#docsSearchResults');

  // '/' focuses #docsSearch from non-input elements, but ignores ctrl+/ and inputs/textareas.
  await page.locator('#outside-btn').focus();
  await page.keyboard.press('Control+/');
  await expect(input).not.toBeFocused();

  await page.locator('#other-input').focus();
  await page.keyboard.press('/');
  await expect(page.locator('#other-input')).toBeFocused();

  await page.locator('#other-textarea').focus();
  await page.keyboard.press('/');
  await expect(page.locator('#other-textarea')).toBeFocused();

  await page.locator('#outside-btn').focus();
  await page.keyboard.press('/');
  await expect(input).toBeFocused();

  // Clearing while a request is in flight aborts cleanly without error text.
  await input.fill('abortme');
  await expect(results.locator('.docs-search-empty')).toHaveText('Searching…');
  await expect.poll(() => searchCalls).toBe(1);

  await input.fill('');
  await expect(results).toBeEmpty();
  searchGate.resolve?.();
  await page.waitForTimeout(50);
  await expect(results).toBeEmpty();
});
