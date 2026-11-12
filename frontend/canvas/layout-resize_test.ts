import { expect, test } from '../../testing/ui/canvas_fixtures';

type LayoutResizeModule = typeof import('/static/generated/canvas/layout-resize.js');
type LayoutResizeController =
  import('/static/generated/canvas/layout-resize.js').LayoutResizeController;

interface ResizeHarnessWindow extends Window {
  drawnSizes: number[];
  layoutResize: LayoutResizeController;
}

const LAYOUT_RESIZE_URL = '/static/generated/canvas/layout-resize.js';

test('canvas reveals final layout after chat slide reversal and window resize', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    viewport: { width: 1500, height: 800 },
    stylesheets: ['/static/css/canvas.css'],
    inlineStyles: [
      `
        body { display: flex; }
        #main-content { flex: 1; min-width: 0; display: flex; }
      `,
    ],
    html: `
      <div id="main-content"><div id="image-container"></div></div>
      <div id="chat-sidebar"></div>
    `,
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { createLayoutResizeController } =
      await window.__importModule<LayoutResizeModule>(moduleUrl);
    const win = window as unknown as ResizeHarnessWindow;
    win.drawnSizes = [];
    const container = document.getElementById('image-container')!;
    win.layoutResize = createLayoutResizeController({
      container,
      sidebar: document.getElementById('chat-sidebar'),
      onSettle: () => {
        win.drawnSizes.push(container.getBoundingClientRect().width);
      },
    });
  }, LAYOUT_RESIZE_URL);

  const container = page.locator('#image-container');
  await expect(container).not.toHaveClass(/layout-resizing/);

  await page.evaluate(() => {
    (window as unknown as ResizeHarnessWindow).layoutResize.setPanelCollapsed(true, true);
  });
  await expect(container).toHaveClass(/layout-resizing/);

  const coverStyles = await page.evaluate(() => {
    const cover = getComputedStyle(document.getElementById('image-container')!, '::after');
    return { backgroundColor: cover.backgroundColor, content: cover.content };
  });
  expect(coverStyles.backgroundColor).toBe('rgb(2, 6, 23)');
  expect(coverStyles.content).not.toBe('none');

  // Reverse the slide before it finishes; the cover must survive both motions.
  await page.waitForTimeout(150);
  await expect(container).toHaveClass(/layout-resizing/);
  await page.evaluate(() => {
    (window as unknown as ResizeHarnessWindow).layoutResize.setPanelCollapsed(false, true);
  });
  await page.waitForTimeout(200);
  await expect(container).toHaveClass(/layout-resizing/);
  await expect(container).not.toHaveClass(/layout-resizing/, { timeout: 2000 });

  expect(
    await page.evaluate(() => {
      const win = window as unknown as ResizeHarnessWindow;
      return win.drawnSizes.at(-1) === document.getElementById('image-container')!.getBoundingClientRect().width;
    }),
  ).toBe(true);

  await page.setViewportSize({ width: 1400, height: 800 });
  await expect(container).toHaveClass(/layout-resizing/);
  await expect(container).not.toHaveClass(/layout-resizing/, { timeout: 2000 });
  expect(
    await page.evaluate(() => {
      const win = window as unknown as ResizeHarnessWindow;
      return win.drawnSizes.at(-1) === document.getElementById('image-container')!.getBoundingClientRect().width;
    }),
  ).toBe(true);
});
