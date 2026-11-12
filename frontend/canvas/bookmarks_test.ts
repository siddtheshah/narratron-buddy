import type { Page } from '@playwright/test';
import { expect, test } from '../../testing/ui/canvas_fixtures';

type BookmarksModule = typeof import('/static/generated/canvas/bookmarks.js');
type BookmarkHistoryItem = import('/static/generated/canvas/bookmarks.js').BookmarkHistoryItem;
type BookmarksController = import('/static/generated/canvas/bookmarks.js').CanvasBookmarksController;

interface BookmarksHarnessWindow extends Window {
  historyItems: BookmarkHistoryItem[];
  index: number;
  orator: boolean;
  failNavigation: boolean;
  bookmarks: BookmarksController;
}

const BOOKMARKS_URL = '/static/generated/canvas/bookmarks.js';
const BOOKMARKS_SHIM_URL = '/static/js/canvas-bookmarks.js';

const MOUNT_OPTIONS = {
  html: '<div id="canvas-bookmarks" hidden></div>',
  stylesheets: ['/static/css/canvas-bookmarks.css'],
  viewport: { width: 800, height: 600 },
};

async function initBookmarksOnPage(page: Page): Promise<void> {
  await page.evaluate(async (moduleUrl: string) => {
    const { initializeCanvasBookmarks } = await window.__importModule<BookmarksModule>(moduleUrl);
    const win = window as unknown as BookmarksHarnessWindow;
    win.historyItems = [
      { path: 'images/Ancient_forest.png', time: 1 },
      {
        path: 'images/A_very_long_image_name_that_should_be_truncated_in_the_panel.png',
        time: 2,
      },
      { path: 'images/Ancient_forest.png', time: 3 },
    ];
    win.index = 0;
    win.orator = true;
    win.failNavigation = false;
    win.bookmarks = initializeCanvasBookmarks({
      theaterId: 'test',
      isOrator: () => win.orator,
      getHistory: () => win.historyItems,
      getIndex: () => win.index,
      navigate: async (target: number) => {
        if (win.failNavigation) {
          throw new Error('Rejected');
        }
        win.index = target;
      },
    });
  }, BOOKMARKS_URL);
}

test('bookmarks toggle, hover, navigation, failed jump feedback, persistence, and role visibility', async ({
  canvas,
  page,
}) => {
  await canvas.mount(MOUNT_OPTIONS);
  await initBookmarksOnPage(page);

  const toggle = page.locator('#canvas-bookmark-toggle');
  const panel = page.locator('#canvas-bookmark-panel');
  const rows = page.locator('.bookmark-item');

  await expect(panel).toBeHidden();
  await toggle.hover();
  await expect(panel).toBeVisible();

  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.bookmark-flash')).toHaveText('1');
  const firstColor = await page
    .locator('.bookmark-number')
    .evaluate((el) => getComputedStyle(el).color);

  await page.mouse.move(700, 500);
  await expect(panel).toBeHidden();
  await toggle.hover();
  await expect(panel).toBeVisible();

  await page.evaluate(() => {
    const win = window as unknown as BookmarksHarnessWindow;
    win.index = 1;
    win.bookmarks.refresh();
  });
  await toggle.click();
  await expect(rows).toHaveCount(2);
  const secondName = await rows.nth(1).locator('.bookmark-name').innerText();
  expect(secondName.endsWith('…')).toBe(true);
  const secondColor = await rows
    .nth(1)
    .locator('.bookmark-number')
    .evaluate((el) => getComputedStyle(el).color);
  expect(secondColor).not.toBe(firstColor);

  // Repeated presentation with a different timestamp is a separate bookmarkable entry.
  await page.evaluate(() => {
    const win = window as unknown as BookmarksHarnessWindow;
    win.index = 2;
    win.bookmarks.refresh();
  });
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await toggle.click();
  await expect(rows).toHaveCount(3);

  await rows.first().click();
  expect(await page.evaluate(() => (window as unknown as BookmarksHarnessWindow).index)).toBe(0);
  await expect(toggle).toBeFocused();
  await expect(panel).toBeHidden();

  await toggle.click();
  await expect(rows).toHaveCount(2);
  await expect(rows.first().locator('.bookmark-number')).toHaveText('2');
  await expect(page.locator('.bookmark-status')).toHaveText('Bookmark 1 removed.');

  // A failed jump leaves the current page unchanged and gives feedback.
  await page.evaluate(() => {
    (window as unknown as BookmarksHarnessWindow).failNavigation = true;
  });
  await rows.first().click();
  await expect(page.locator('.bookmark-status')).toHaveText(
    'Could not open bookmark. Try again.',
  );
  expect(await page.evaluate(() => (window as unknown as BookmarksHarnessWindow).index)).toBe(0);

  // Persist through a real reload and exercise keyboard navigation.
  await page.reload();
  await initBookmarksOnPage(page);

  await toggle.focus();
  await toggle.press('ArrowDown');
  await expect(rows.first()).toBeFocused();
  await expect(rows).toHaveCount(2);
  await rows.first().press('Enter');
  expect(await page.evaluate(() => (window as unknown as BookmarksHarnessWindow).index)).toBe(1);
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await toggle.press('Escape');
  await expect(panel).toBeHidden();

  // Missing history entries stay out of the panel; appending retains identities.
  await page.evaluate(() => {
    const win = window as unknown as BookmarksHarnessWindow;
    win.historyItems.pop();
    win.historyItems.push({ path: 'new.png', time: 4 });
    win.bookmarks.refresh();
  });
  await toggle.hover();
  await expect(rows).toHaveCount(1);

  await page.evaluate(() => {
    const win = window as unknown as BookmarksHarnessWindow;
    win.orator = false;
    win.bookmarks.refresh();
  });
  await expect(page.locator('#canvas-bookmarks')).toBeHidden();
});

test('deprecated shim re-exports the same compiled initializeCanvasBookmarks', async ({
  canvas,
  page,
}) => {
  await canvas.mount(MOUNT_OPTIONS);

  const sameReference = await page.evaluate(
    async ({ compiledUrl, shimUrl }: { compiledUrl: string; shimUrl: string }) => {
      const compiled = await window.__importModule<BookmarksModule>(compiledUrl);
      const shim = await window.__importModule<BookmarksModule>(shimUrl);
      return compiled.initializeCanvasBookmarks === shim.initializeCanvasBookmarks;
    },
    { compiledUrl: BOOKMARKS_URL, shimUrl: BOOKMARKS_SHIM_URL },
  );

  expect(sameReference).toBe(true);
});

test('missing root returns a no-op controller and storage events sync bookmarks', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    html: '<div id="other"></div>',
    stylesheets: ['/static/css/canvas-bookmarks.css'],
  });

  const noopOk = await page.evaluate(async (moduleUrl: string) => {
    const { initializeCanvasBookmarks } = await window.__importModule<BookmarksModule>(moduleUrl);
    const controller = initializeCanvasBookmarks({
      theaterId: 'missing',
      isOrator: () => true,
      getHistory: () => [],
      getIndex: () => 0,
      navigate: async () => {},
    });
    controller.refresh();
    return true;
  }, BOOKMARKS_URL);
  expect(noopOk).toBe(true);

  await canvas.mount(MOUNT_OPTIONS);
  await page.evaluate(async (moduleUrl: string) => {
    const { initializeCanvasBookmarks } = await window.__importModule<BookmarksModule>(moduleUrl);
    const items: BookmarkHistoryItem[] = [
      { url: 'https://cdn.example/scene.webp?token=1', time: 10 },
      { animation: { scene_prompt: 'Moonlit harbor' }, time: 20 },
      { time: 30 },
    ];
    initializeCanvasBookmarks({
      theaterId: 'sync-stage',
      isOrator: () => true,
      getHistory: () => items,
      getIndex: () => 1,
      navigate: async () => {},
    });
    const synced = [
      { key: JSON.stringify(['https://cdn.example/scene.webp?token=1', 10]), number: 1 },
      { key: JSON.stringify([{ scene_prompt: 'Moonlit harbor' }, 20]), number: 2 },
    ];
    window.dispatchEvent(
      new StorageEvent('storage', {
        key: 'narratron-canvas-bookmarks:sync-stage',
        newValue: JSON.stringify(synced),
      }),
    );
  }, BOOKMARKS_URL);

  await expect(page.locator('#canvas-bookmark-toggle')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.bookmark-current-number')).toHaveText('2');
  await page.locator('#canvas-bookmark-toggle').hover();
  await expect(page.locator('.bookmark-name')).toHaveText(['scene', 'Moonlit harbor']);
});
