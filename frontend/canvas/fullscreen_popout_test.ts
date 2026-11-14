import type { Page } from '@playwright/test';
import { expect, test } from '../../testing/ui/canvas_fixtures';

interface FullscreenWindow extends Window {
  __fullscreenMockActive: boolean;
}

async function installFullscreenMock(page: Page): Promise<void> {
  await page.evaluate(() => {
    const win = window as unknown as FullscreenWindow;
    win.__fullscreenMockActive = false;
    Object.defineProperty(document, 'fullscreenElement', {
      configurable: true,
      get: () => (win.__fullscreenMockActive ? document.documentElement : null),
    });
    document.documentElement.requestFullscreen = async () => {
      win.__fullscreenMockActive = true;
      document.dispatchEvent(new Event('fullscreenchange'));
    };
    document.exitFullscreen = async () => {
      win.__fullscreenMockActive = false;
      document.dispatchEvent(new Event('fullscreenchange'));
    };
  });
}

test('smaller-monitor @media (max-width: 1280px) auto-collapses chat sidebar to 280px width', async ({
  canvas,
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await canvas.openCanvas({ isOrator: true });

  const sidebar = page.locator('#chat-sidebar');
  await expect(sidebar).toHaveClass(/collapsed/);
  await expect
    .poll(() =>
      sidebar.evaluate((el) => {
        const cs = getComputedStyle(el);
        return { width: cs.width, marginRight: cs.marginRight };
      }),
    )
    .toEqual({ width: '280px', marginRight: '-280px' });

  await page.setViewportSize({ width: 1400, height: 800 });
  await page.evaluate(() => window.dispatchEvent(new Event('resize')));
  await expect(sidebar).not.toHaveClass(/collapsed/);
  await expect.poll(() => sidebar.evaluate((el) => getComputedStyle(el).marginRight)).toBe('0px');
});

test('fullscreen mode swaps SVG icon, activates cinematic UI fade, collapses sidebar, and keeps toggle tab clickable', async ({
  canvas,
  page,
}) => {
  await page.clock.install();
  await page.setViewportSize({ width: 1400, height: 900 });
  await canvas.openCanvas({ isOrator: true });
  await installFullscreenMock(page);

  // Legacy popout window buttons were removed in favor of slide-out chat panel.
  await expect(page.locator('#popout-window-btn')).toHaveCount(0);
  await expect(page.locator('#popped-out-placeholder')).toHaveCount(0);

  const fullscreenBtn = page.locator('#fullscreen-btn');
  const fullscreenIcon = page.locator('#fullscreen-icon');
  const sidebar = page.locator('#chat-sidebar');
  const sideToggle = page.locator('#chat-side-toggle-btn');

  await expect(fullscreenBtn).toBeVisible();
  await expect(page.locator('#obs-theater-btn')).toBeVisible();
  await expect(page.locator('#popout-expand-btn')).toBeAttached();
  await expect(fullscreenBtn).toHaveAttribute('title', 'Full Screen (F11)');
  const enterIconHtml = await fullscreenIcon.innerHTML();

  // Enter fullscreen
  await fullscreenBtn.click();
  await expect(page.locator('body')).toHaveClass(/fullscreen-cinematic/);
  await expect(page.locator('body')).toHaveClass(/ui-visible/);
  await expect(sidebar).toHaveClass(/collapsed/);
  await expect(fullscreenBtn).toHaveAttribute('title', 'Exit Full Screen (Esc)');
  const exitIconHtml = await fullscreenIcon.innerHTML();
  expect(exitIconHtml).not.toBe(enterIconHtml);

  // Verify computed styles on retracted toggle tab in fullscreen-cinematic
  const tabStyles = await sideToggle.evaluate((el) => {
    const cs = getComputedStyle(el);
    return {
      position: cs.position,
      right: cs.right,
      opacity: cs.opacity,
      pointerEvents: cs.pointerEvents,
      zIndex: cs.zIndex,
    };
  });
  expect(tabStyles.position).toBe('fixed');
  expect(tabStyles.right).toBe('0px');
  expect(tabStyles.opacity).toBe('1');
  expect(tabStyles.pointerEvents).toBe('auto');
  expect(tabStyles.zIndex).toBe('9997');

  // After 2.5s of mouse inactivity, ui-visible fades out; mousemove restores it
  await page.clock.fastForward(2600);
  await expect(page.locator('body')).not.toHaveClass(/ui-visible/);
  await page.evaluate(() => window.dispatchEvent(new MouseEvent('mousemove')));
  await expect(page.locator('body')).toHaveClass(/ui-visible/);

  // Clicking the side toggle tab expands the sidebar while in fullscreen
  await sideToggle.click();
  await expect(sidebar).not.toHaveClass(/collapsed/);

  // Exit fullscreen clears fullscreen-cinematic & restores button title
  await fullscreenBtn.click();
  await expect(page.locator('body')).not.toHaveClass(/fullscreen-cinematic/);
  await expect(fullscreenBtn).toHaveAttribute('title', 'Full Screen (F11)');
});
