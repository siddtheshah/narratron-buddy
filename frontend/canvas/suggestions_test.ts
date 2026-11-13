import type { Route } from '@playwright/test';
import { expect, test } from '../../testing/ui/canvas_fixtures';

interface SuggestionItem {
  readonly title: string;
  readonly category: string;
  readonly description: string;
}

interface SuggestionsResponse {
  readonly elements_fingerprint: string;
  readonly suggestions: readonly SuggestionItem[];
}

test('suggestions button is visible for orator, hidden for viewer, and positioned in lower-left corner', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({ isOrator: true });

  const toggleBtn = page.locator('#suggestions-toggle-btn');
  const loading = page.locator('#suggestions-loading');
  const list = page.locator('#suggestions-list');

  await expect(toggleBtn).toBeVisible();
  await expect(loading).toHaveCSS('display', 'none');
  await expect(list).toBeAttached();

  const cornerCss = await page.evaluate(() => {
    const container = document.querySelector('.canvas-corner-bottom-left');
    return container ? getComputedStyle(container).bottom : null;
  });
  expect(cornerCss).not.toBeNull();
  expect(parseFloat(cornerCss!)).toBeGreaterThan(0);

  await canvas.openCanvas({ isOrator: false });
  await expect(toggleBtn).toBeHidden();
});

test('hovering suggestions fetches, renders cards safely, and caches by elements fingerprint', async ({
  canvas,
  page,
}) => {
  let fetchCount = 0;
  let currentPayload: SuggestionsResponse = {
    elements_fingerprint: 'fp-1',
    suggestions: [
      {
        title: 'Ambush <script>alert(1)</script>',
        category: 'Encounter',
        description: 'Goblins emerge from the mist & block the bridge.',
      },
    ],
  };

  await canvas.openCanvas({
    apiRoutes: {
      '/theaters/stage/suggestions': async (route: Route) => {
        fetchCount += 1;
        await route.fulfill({ json: currentPayload });
      },
    },
  });

  const toggleBtn = page.locator('#suggestions-toggle-btn');
  const loading = page.locator('#suggestions-loading');
  const list = page.locator('#suggestions-list');

  await toggleBtn.dispatchEvent('mouseenter');
  await expect(toggleBtn).toHaveClass(/expanded/);
  await expect
    .poll(() => fetchCount)
    .toBe(1);
  await expect(loading).toHaveCSS('display', 'none');
  await expect(list).toHaveCSS('display', 'flex');
  await expect(list.locator('.suggestion-card')).toHaveCount(1);
  await expect(list.locator('.suggestion-card-title')).toHaveText(
    'Ambush <script>alert(1)</script>',
  );
  await expect(list.locator('.suggestion-card-badge')).toHaveText('Encounter');
  await expect(list.locator('.suggestion-card-desc')).toHaveText(
    'Goblins emerge from the mist & block the bridge.',
  );
  expect(await list.locator('script').count()).toBe(0);

  // Leave and re-hover with the same fingerprint: keeps cached cards even if payload title changed
  await toggleBtn.dispatchEvent('mouseleave');
  await expect(toggleBtn).not.toHaveClass(/expanded/);

  currentPayload = {
    elements_fingerprint: 'fp-1',
    suggestions: [
      {
        title: 'Stale Cache Ignored',
        category: 'Note',
        description: 'Should not replace cached suggestions when fingerprint matches.',
      },
    ],
  };
  await toggleBtn.dispatchEvent('mouseenter');
  await expect.poll(() => fetchCount).toBe(2);
  await expect(list.locator('.suggestion-card-title')).toHaveText(
    'Ambush <script>alert(1)</script>',
  );

  // Fingerprint changes: renders the updated suggestions
  await toggleBtn.dispatchEvent('mouseleave');
  currentPayload = {
    elements_fingerprint: 'fp-2',
    suggestions: [
      {
        title: 'Secret Door',
        category: 'Discovery',
        description: 'A hidden latch behind the tapestry.',
      },
      {
        title: 'Bell Tower',
        category: 'Audio',
        description: 'Distant chimes echo across the valley.',
      },
    ],
  };
  await toggleBtn.dispatchEvent('mouseenter');
  await expect.poll(() => fetchCount).toBe(3);
  await expect(list.locator('.suggestion-card')).toHaveCount(2);
  await expect(list.locator('.suggestion-card-title').nth(0)).toHaveText('Secret Door');
  await expect(list.locator('.suggestion-card-title').nth(1)).toHaveText('Bell Tower');
});

test('suggestions shows loading indicator during fetch and surfaces empty or error states', async ({
  canvas,
  page,
}) => {
  let failNext = true;
  let releaseFirstFetch = (): void => {};
  const firstFetchGate = new Promise<void>((resolve) => {
    releaseFirstFetch = resolve;
  });

  await canvas.openCanvas({
    apiRoutes: {
      '/theaters/stage/suggestions': async (route: Route) => {
        if (failNext) {
          await firstFetchGate;
          await route.fulfill({ status: 500, json: { detail: 'Failed' } });
          return;
        }
        await route.fulfill({
          json: { elements_fingerprint: 'empty-1', suggestions: [] },
        });
      },
    },
  });

  const toggleBtn = page.locator('#suggestions-toggle-btn');
  const loading = page.locator('#suggestions-loading');
  const list = page.locator('#suggestions-list');

  await toggleBtn.dispatchEvent('mouseenter');
  await expect(loading).toHaveCSS('display', 'flex');
  await expect(list).toHaveCSS('display', 'none');
  releaseFirstFetch();

  await expect(list).toContainText('Unable to generate suggestions at this time.');
  await expect(loading).toHaveCSS('display', 'none');
  await expect(list).toHaveCSS('display', 'flex');

  await toggleBtn.dispatchEvent('mouseleave');
  failNext = false;
  await toggleBtn.dispatchEvent('mouseenter');
  await expect(list).toContainText('No suggestions available right now.');
  await expect(loading).toHaveCSS('display', 'none');
});
