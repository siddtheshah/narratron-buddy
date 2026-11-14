import { CANVAS_ORIGIN, expect, test } from '../../testing/ui/canvas_fixtures';

type DeviceNavigationModule = typeof import('/static/generated/canvas/device-navigation.js');

const DEVICE_NAV_MODULE_URL = '/static/generated/canvas/device-navigation.js';

test('device-navigation exposes global helpers, toggles mobile class on resize, and computes theaterUrl()', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    viewport: { width: 1200, height: 800 },
    moduleScripts: [`import ${JSON.stringify(DEVICE_NAV_MODULE_URL)};`],
  });

  const globalsAttached = await page.evaluate(async (compiledUrl) => {
    const compiled = await window.__importModule<DeviceNavigationModule>(compiledUrl);
    return (
      window.NarratronNavigation?.isMobile === compiled.isMobile &&
      window.NarratronNavigation?.theaterUrl === compiled.theaterUrl
    );
  }, DEVICE_NAV_MODULE_URL);
  expect(globalsAttached).toBe(true);

  await expect(page.locator('html')).not.toHaveClass(/mobile-device/);
  expect(await page.evaluate(() => window.NarratronNavigation?.isMobile())).toBe(false);
  expect(await page.evaluate(() => window.NarratronNavigation?.theaterUrl('stage-1'))).toBe(
    '/canvas?theater_id=stage-1',
  );

  await page.setViewportSize({ width: 640, height: 800 });
  await expect(page.locator('html')).toHaveClass(/mobile-device/);
  expect(await page.evaluate(() => window.NarratronNavigation?.isMobile())).toBe(true);
  expect(await page.evaluate(() => window.NarratronNavigation?.theaterUrl('stage-1'))).toBe(
    '/remote?theater_id=stage-1',
  );
});

test('device-navigation redirects mobile /canvas without theater_id to /deploy', async ({
  canvas,
  page,
}) => {
  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/deploy',
    async (route) => {
      await route.fulfill({ contentType: 'text/html', body: '<h1>Deploy</h1>' });
    },
  );

  await canvas.mount({
    urlPath: '/canvas',
    viewport: { width: 500, height: 800 },
    moduleScripts: [`import ${JSON.stringify(DEVICE_NAV_MODULE_URL)};`],
  });

  await page.waitForURL(`${CANVAS_ORIGIN}/deploy`);
  await expect(page.locator('h1')).toHaveText('Deploy');
});
