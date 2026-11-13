import type { Route } from '@playwright/test';
import { CANVAS_ORIGIN, expect, test } from '../../testing/ui/canvas_fixtures';

interface MinimalModeWindow extends Window {
  __resolveWebpQualityUrl: (rawUrl: string) => string;
}

const ONE_BY_ONE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=',
  'base64',
);

const MINIMAL_MODE_STORAGE_KEY = 'narratron.canvas.performance.minimalMode';

test('minimal mode removes compositor chrome, appends low-quality webp params, and persists across reloads', async ({
  canvas,
  page,
}) => {
  const imageRequests: string[] = [];

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname.startsWith('/static/images/'),
    async (route: Route) => {
      const url = new URL(route.request().url());
      imageRequests.push(`${url.pathname}${url.search}`);
      await route.fulfill({ body: ONE_BY_ONE_PNG, contentType: 'image/png' });
    },
  );

  await canvas.openCanvas({
    apiRoutes: {
      '/api/latest': {
        latest: '/static/images/scene2.webp',
        time: 2.0,
        history: [
          { url: '/static/images/scene1.webp', prompt: 'Scene 1', time: 1.0 },
          { url: '/static/images/scene2.webp', prompt: 'Scene 2', time: 2.0 },
        ],
        orator_cursor: 1,
      },
    },
  });

  await page.waitForFunction(
    () => document.getElementById('page-indicator')?.textContent?.trim() === '2 / 2',
  );

  // Before Minimal Mode: html.minimal-mode is absent and .webp URLs are untouched
  expect(
    await page.evaluate(() => document.documentElement.classList.contains('minimal-mode')),
  ).toBe(false);
  expect(
    await page.evaluate(() =>
      (window as unknown as MinimalModeWindow).__resolveWebpQualityUrl('/static/images/scene2.webp'),
    ),
  ).toBe('/static/images/scene2.webp');
  expect(imageRequests.some((req) => req.startsWith('/static/images/scene2.webp'))).toBe(true);
  expect(imageRequests.some((req) => req.includes('quality=low'))).toBe(false);

  // Open the pullout drawer and enable Minimal Mode
  await page.click('#menu-left-toggle-btn');
  const minimalInput = page.locator('#minimal-mode-input');
  await expect(minimalInput).toBeVisible();
  await expect(minimalInput).not.toBeChecked();

  imageRequests.length = 0;
  await minimalInput.check();

  await expect(page.locator('html')).toHaveClass(/minimal-mode/);
  expect(
    await page.evaluate((key: string) => localStorage.getItem(key), MINIMAL_MODE_STORAGE_KEY),
  ).toBe('true');

  // Compositor-heavy styles are disabled under html.minimal-mode
  const computed = await page.evaluate(() => {
    const bgLayer = document.getElementById('bg-layer')!;
    const container = document.getElementById('image-container')!;
    const currentImage = document.getElementById('current-image')!;
    const bgStyle = getComputedStyle(bgLayer);
    const containerStyle = getComputedStyle(container);
    const imageStyle = getComputedStyle(currentImage);
    return {
      bgDisplay: bgStyle.display,
      containerBackground: containerStyle.backgroundColor,
      containerBackdropFilter: containerStyle.backdropFilter,
      containerBoxShadow: containerStyle.boxShadow,
      imageAnimationName: imageStyle.animationName,
      imageTransitionProperty: imageStyle.transitionProperty,
      imageFilter: imageStyle.filter,
      imageWillChange: imageStyle.willChange,
    };
  });
  expect(computed.bgDisplay).toBe('none');
  expect(computed.containerBackground).toBe('rgb(0, 0, 0)');
  expect(computed.containerBackdropFilter).toBe('none');
  expect(computed.containerBoxShadow).toBe('none');
  expect(computed.imageAnimationName).toBe('none');
  expect(computed.imageTransitionProperty).toBe('none');
  expect(computed.imageFilter).toBe('none');
  expect(computed.imageWillChange).toBe('auto');

  // resolveWebpQualityUrl appends quality=low only to .webp URLs without an existing quality param
  const resolved = await page.evaluate(() => {
    const resolve = (window as unknown as MinimalModeWindow).__resolveWebpQualityUrl;
    return {
      webp: resolve('/static/images/scene2.webp'),
      webpWithQuery: resolve('/static/images/scene2.webp?v=1#hash'),
      webpWithQuality: resolve('/static/images/scene2.webp?quality=high'),
      png: resolve('/static/images/scene2.png'),
      absoluteWebp: resolve('https://cdn.example.com/art/frame.webp'),
    };
  });
  expect(resolved).toEqual({
    webp: '/static/images/scene2.webp?quality=low',
    webpWithQuery: '/static/images/scene2.webp?v=1&quality=low#hash',
    webpWithQuality: '/static/images/scene2.webp?quality=high',
    png: '/static/images/scene2.png',
    absoluteWebp: 'https://cdn.example.com/art/frame.webp?quality=low',
  });

  // Toggling Minimal Mode re-renders and preloads history images with quality=low
  await expect
    .poll(() =>
      imageRequests.some(
        (req) => req.startsWith('/static/images/scene2.webp') && req.includes('quality=low'),
      ),
    )
    .toBe(true);
  await expect
    .poll(() => imageRequests.some((req) => req === '/static/images/scene1.webp?quality=low'))
    .toBe(true);

  // Reload preserves Minimal Mode preference and immediately fetches quality=low
  imageRequests.length = 0;
  await page.reload();
  await page.waitForFunction(() => window._isActiveOratorState === true, null, { timeout: 5000 });
  await expect(page.locator('html')).toHaveClass(/minimal-mode/);
  await expect(page.locator('#minimal-mode-input')).toBeChecked();
  await expect
    .poll(() =>
      imageRequests.some(
        (req) => req.startsWith('/static/images/scene2.webp') && req.includes('quality=low'),
      ),
    )
    .toBe(true);

  // Disabling Minimal Mode removes html.minimal-mode, updates storage, and drops quality=low
  await page.click('#menu-left-toggle-btn');
  imageRequests.length = 0;
  await page.locator('#minimal-mode-input').uncheck();
  await expect(page.locator('html')).not.toHaveClass(/minimal-mode/);
  expect(
    await page.evaluate((key: string) => localStorage.getItem(key), MINIMAL_MODE_STORAGE_KEY),
  ).toBe('false');
  await expect
    .poll(() => imageRequests.some((req) => req.startsWith('/static/images/scene2.webp')))
    .toBe(true);
  expect(imageRequests.some((req) => req.includes('quality=low'))).toBe(false);
});

test('minimal mode presents a static still frame instead of running triframe animations', async ({
  canvas,
  page,
}) => {
  const imageRequests: string[] = [];

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname.startsWith('/static/images/'),
    async (route: Route) => {
      const url = new URL(route.request().url());
      imageRequests.push(`${url.pathname}${url.search}`);
      await route.fulfill({ body: ONE_BY_ONE_PNG, contentType: 'image/png' });
    },
  );

  await canvas.openCanvas({
    localStorage: {
      [MINIMAL_MODE_STORAGE_KEY]: 'true',
    },
    apiRoutes: {
      '/api/latest': {
        latest: '/static/images/frame1.webp',
        time: 1.0,
        history: [
          {
            url: '/static/images/frame1.webp',
            prompt: 'Animated scene',
            time: 1.0,
            animation: {
              type: 'triframe',
              frames: [
                '/static/images/frame1.webp',
                '/static/images/frame2.webp',
                '/static/images/frame3.webp',
              ],
            },
          },
        ],
        orator_cursor: 0,
      },
    },
  });

  await expect(page.locator('html')).toHaveClass(/minimal-mode/);
  await expect
    .poll(() =>
      imageRequests.some(
        (req) => req.startsWith('/static/images/frame1.webp') && req.includes('quality=low'),
      ),
    )
    .toBe(true);

  // Give any hypothetical animation loop a moment, then confirm frame2/frame3 were never loaded
  await page.waitForTimeout(150);
  expect(imageRequests.some((req) => req.includes('frame2.webp'))).toBe(false);
  expect(imageRequests.some((req) => req.includes('frame3.webp'))).toBe(false);

  // Turning Minimal Mode OFF re-renders the item as an active triframe animation
  await page.click('#menu-left-toggle-btn');
  await page.locator('#minimal-mode-input').uncheck();
  await expect
    .poll(() => imageRequests.some((req) => req.startsWith('/static/images/frame2.webp')))
    .toBe(true);
  await expect
    .poll(() => imageRequests.some((req) => req.startsWith('/static/images/frame3.webp')))
    .toBe(true);
});
