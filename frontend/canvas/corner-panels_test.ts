import { expect, test } from '../../testing/ui/canvas_fixtures';

type CornerPanelsModule = typeof import('/static/generated/canvas/corner-panels.js');
type CornerPanelsController = import('/static/generated/canvas/corner-panels.js').CornerPanelsController;

interface CornerPanelsHarnessWindow extends Window {
  cornerPanels?: CornerPanelsController;
}

const CORNER_PANELS_URL = '/static/generated/canvas/corner-panels.js';

const CORNER_PANELS_HTML = `
  <div id="image-container">
    <div id="suggestions-toggle-btn">
      <div id="suggestions-loading" style="display: none;"></div>
      <div id="suggestions-list" style="display: flex;"></div>
    </div>
    <div id="sticky-notes-toggle-btn">
      <span id="sticky-notes-count-badge" style="display: none;">0</span>
      <div id="sticky-notes-loading" style="display: none;"></div>
      <div id="sticky-notes-list" style="display: flex;"></div>
    </div>
    <button id="hide-canvas-text-btn" type="button" aria-pressed="false" title="Hide all text on canvas">
      <svg class="hide-text-icon-visible" style="display: block;"></svg>
      <svg class="hide-text-icon-hidden" style="display: none;"></svg>
    </button>
  </div>
`;

test('suggestions panel handles missing theaterId, fetch failures, empty lists, and XSS escaping', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    urlPath: '/canvas',
    html: CORNER_PANELS_HTML,
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { createCornerPanels } = await window.__importModule<CornerPanelsModule>(moduleUrl);
    const win = window as unknown as CornerPanelsHarnessWindow;
    win.cornerPanels = createCornerPanels({
      getTheaterId: () => null,
      suggestionsButton: document.getElementById('suggestions-toggle-btn'),
      suggestionsList: document.getElementById('suggestions-list'),
      suggestionsLoading: document.getElementById('suggestions-loading'),
      initialFetchStickyNotes: false,
    });
  }, CORNER_PANELS_URL);

  await page.locator('#suggestions-toggle-btn').dispatchEvent('mouseenter');
  await expect(page.locator('#suggestions-list')).toContainText(
    'Launch a theater to view suggestions.',
  );

  let mode: 'error' | 'empty' | 'items' = 'error';
  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/theaters/quest-1/suggestions',
    async (route) => {
      if (mode === 'error') {
        await route.fulfill({ status: 500, body: 'Server error' });
      } else if (mode === 'empty') {
        await route.fulfill({ json: { elements_fingerprint: 'fp-empty', suggestions: [] } });
      } else {
        await route.fulfill({
          json: {
            elements_fingerprint: 'fp-1',
            suggestions: [
              {
                title: `O'Brien <img src=x onerror=alert(1)>`,
                category: 'NPC & "Ally"',
                description: 'He whispers: "Check the lock & key."',
              },
            ],
          },
        });
      }
    },
  );

  await page.evaluate(async (moduleUrl: string) => {
    const { createCornerPanels } = await window.__importModule<CornerPanelsModule>(moduleUrl);
    const win = window as unknown as CornerPanelsHarnessWindow;
    win.cornerPanels?.destroy();
    win.cornerPanels = createCornerPanels({
      getTheaterId: () => 'quest-1',
      suggestionsButton: document.getElementById('suggestions-toggle-btn'),
      suggestionsList: document.getElementById('suggestions-list'),
      suggestionsLoading: document.getElementById('suggestions-loading'),
      initialFetchStickyNotes: false,
    });
  }, CORNER_PANELS_URL);

  await page.locator('#suggestions-toggle-btn').dispatchEvent('mouseenter');
  await expect(page.locator('#suggestions-list')).toContainText(
    'Unable to generate suggestions at this time.',
  );

  mode = 'empty';
  await page.locator('#suggestions-toggle-btn').dispatchEvent('mouseenter');
  await expect(page.locator('#suggestions-list')).toContainText(
    'No suggestions available right now.',
  );

  mode = 'items';
  await page.locator('#suggestions-toggle-btn').dispatchEvent('mouseenter');
  await expect(page.locator('.suggestion-card-title')).toHaveText(
    `O'Brien <img src=x onerror=alert(1)>`,
  );
  await expect(page.locator('.suggestion-card-badge')).toHaveText('NPC & "Ally"');
  await expect(page.locator('.suggestion-card-desc')).toHaveText(
    'He whispers: "Check the lock & key."',
  );
  expect(await page.locator('#suggestions-list img').count()).toBe(0);
});

test('sticky notes resolve path theaterId, forward join_key, handle hidden-only notes, and pin on click', async ({
  canvas,
  page,
}) => {
  const seenRequests: string[] = [];
  await canvas.serveRoute(
    (url) =>
      url.origin === canvas.origin &&
      (url.pathname === '/theaters/tavern-9/sticky-notes' || url.pathname === '/api/sticky-notes'),
    async (route) => {
      const reqUrl = new URL(route.request().url());
      seenRequests.push(`${reqUrl.pathname}${reqUrl.search}`);
      if (reqUrl.pathname === '/theaters/tavern-9/sticky-notes') {
        await route.fulfill({ status: 404, body: 'missing' });
        return;
      }
      await route.fulfill({
        json: {
          sticky_notes: [{ topic: 'Trapdoor', info: 'Under the rug', hidden: true }],
          hidden_stickies: ['trapdoor'],
        },
      });
    },
  );

  await canvas.mount({
    urlPath: '/theaters/tavern-9/stage',
    query: '?join_key=secret-123',
    html: CORNER_PANELS_HTML,
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { createCornerPanels } = await window.__importModule<CornerPanelsModule>(moduleUrl);
    const win = window as unknown as CornerPanelsHarnessWindow;
    win.cornerPanels = createCornerPanels({
      stickyNotesButton: document.getElementById('sticky-notes-toggle-btn'),
      stickyNotesList: document.getElementById('sticky-notes-list'),
      stickyNotesLoading: document.getElementById('sticky-notes-loading'),
      stickyNotesBadge: document.getElementById('sticky-notes-count-badge'),
      initialFetchStickyNotes: true,
    });
  }, CORNER_PANELS_URL);

  await expect.poll(() => seenRequests.length).toBe(2);
  expect(seenRequests).toEqual([
    '/theaters/tavern-9/sticky-notes?join_key=secret-123',
    '/api/sticky-notes?theater_id=tavern-9&join_key=secret-123',
  ]);

  const badge = page.locator('#sticky-notes-count-badge');
  await expect(badge).toHaveText('1 🔒');
  await expect(page.locator('#sticky-notes-list')).toContainText('No standard sticky notes active.');

  const btn = page.locator('#sticky-notes-toggle-btn');
  // Clicking the panel pins it expanded even after mouseleave.
  await btn.click();
  await expect(btn).toHaveClass(/expanded/);
  await btn.dispatchEvent('mouseleave');
  await page.waitForTimeout(320);
  await expect(btn).toHaveClass(/expanded/);

  // Clicking again unpins and collapses after mouseleave delay.
  await btn.click();
  await expect(btn).not.toHaveClass(/expanded/);
  await btn.dispatchEvent('mouseenter');
  await expect(btn).toHaveClass(/expanded/);
  await btn.dispatchEvent('mouseleave');
  await expect(btn).not.toHaveClass(/expanded/, { timeout: 1000 });
});

test('hide canvas text persists and destroy removes corner panel listeners', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    urlPath: '/canvas',
    query: '?theater_id=stage',
    html: CORNER_PANELS_HTML,
    localStorage: { 'narratron-hide-canvas-text': 'true' },
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { createCornerPanels } = await window.__importModule<CornerPanelsModule>(moduleUrl);
    const win = window as unknown as CornerPanelsHarnessWindow;
    win.cornerPanels = createCornerPanels({
      hideCanvasTextButton: document.getElementById('hide-canvas-text-btn') as HTMLButtonElement | null,
      imageContainer: document.getElementById('image-container'),
      suggestionsButton: document.getElementById('suggestions-toggle-btn'),
      suggestionsList: document.getElementById('suggestions-list'),
      suggestionsLoading: document.getElementById('suggestions-loading'),
      initialFetchStickyNotes: false,
    });
  }, CORNER_PANELS_URL);

  const btn = page.locator('#hide-canvas-text-btn');
  const container = page.locator('#image-container');
  await expect(container).toHaveClass(/hide-canvas-text/);
  await expect(btn).toHaveAttribute('aria-pressed', 'true');
  await expect(btn.locator('.hide-text-icon-visible')).toHaveCSS('display', 'none');
  await expect(btn.locator('.hide-text-icon-hidden')).toHaveCSS('display', 'block');

  // Toggle off with persist=false keeps storage untouched, then click persists.
  await page.evaluate(() => {
    (window as unknown as CornerPanelsHarnessWindow).cornerPanels?.setCanvasTextHidden(false, false);
  });
  await expect(container).not.toHaveClass(/hide-canvas-text/);
  expect(await page.evaluate(() => localStorage.getItem('narratron-hide-canvas-text'))).toBe('true');

  await btn.click();
  await expect(container).toHaveClass(/hide-canvas-text/);
  await btn.click();
  await expect(container).not.toHaveClass(/hide-canvas-text/);
  expect(await page.evaluate(() => localStorage.getItem('narratron-hide-canvas-text'))).toBe(
    'false',
  );

  // Destroy detaches listeners.
  await page.evaluate(() => {
    (window as unknown as CornerPanelsHarnessWindow).cornerPanels?.destroy();
  });
  await btn.click();
  await expect(container).not.toHaveClass(/hide-canvas-text/);
  await page.locator('#suggestions-toggle-btn').dispatchEvent('mouseenter');
  await expect(page.locator('#suggestions-toggle-btn')).not.toHaveClass(/expanded/);
});
