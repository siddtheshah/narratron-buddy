import fs from 'node:fs';
import path from 'node:path';
import type { Page, Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  REPO_ROOT,
  expect,
  test,
  type CanvasHarness,
} from '../../testing/ui/canvas_fixtures';

type TestlabLayeredLabModule = typeof import('./testlab-layered-animation-lab.js');

const LAYERED_LAB_HTML_PATH = path.join(REPO_ROOT, 'testlab', 'layered_animation_lab.html');

// 1x1 transparent PNG and 2x2 RGBA test PNG generated dynamically via browser canvas in test or static 1x1 PNG bytes.
const SAMPLE_PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAEklEQVR4nGP8z8DAwMDAxMDAwAAFAgEB/4n3WAAAAABJRU5ErkJggg==';

function loadPreparedLayeredLabHtml(): string {
  const raw = fs.readFileSync(LAYERED_LAB_HTML_PATH, 'utf-8');
  return raw.replace(/<link[^>]+href="https:\/\/fonts\.[^>]*>/g, '');
}

async function setupLayeredLabRoutes(
  canvas: CanvasHarness,
  page: Page,
  catalogPayload: Record<string, unknown>,
): Promise<void> {
  const html = loadPreparedLayeredLabHtml();
  const pngBytes = Buffer.from(SAMPLE_PNG_BASE64, 'base64');

  await canvas.serveRoute(
    (url: URL) =>
      url.origin === CANVAS_ORIGIN &&
      (url.pathname === '/layered-animation' ||
        url.pathname === '/api/layered-animation/catalog' ||
        url.pathname.startsWith('/test-images/') ||
        url.pathname === '/favicon.ico'),
    async (route: Route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/layered-animation') {
        await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
        return;
      }
      if (pathname === '/api/layered-animation/catalog') {
        await route.fulfill({ json: catalogPayload });
        return;
      }
      if (pathname.startsWith('/test-images/')) {
        await route.fulfill({ contentType: 'image/png', body: pngBytes });
        return;
      }
      await route.fulfill({ status: 204 });
    },
  );
}

const SAMPLE_CATALOG = {
  pieces: [
    { id: 'sky', url: '/test-images/pieces/bg_sky.png', filename: 'bg_sky.png' },
    { id: 'tree', url: '/test-images/pieces/tree.png', filename: 'tree.png' },
    { id: 'crystal', url: '/test-images/pieces/crystal.png', filename: 'crystal.png' },
  ],
  effects: [
    { id: 'none', name: 'None' },
    { id: 'sway', name: 'Sway' },
    { id: 'pulse', name: 'Pulse' },
    { id: 'vibrate', name: 'Vibrate' },
    { id: 'light_halo', name: 'Light Halo' },
    { id: 'ghostly', name: 'Ghostly' },
  ],
  presets: [
    {
      id: 'forest-scene',
      name: 'Forest Scene',
      layers: [
        {
          name: 'Sky Backdrop',
          url: '/test-images/pieces/bg_sky.png',
          filename: 'bg_sky.png',
          effect: 'none',
          speed: 1.0,
          amplitude: 1.0,
          opacity: 1.0,
        },
        {
          name: 'Swaying Tree',
          url: '/test-images/pieces/tree.png',
          filename: 'tree.png',
          effect: 'sway',
          speed: 1.2,
          amplitude: 1.4,
          opacity: 0.9,
        },
      ],
    },
  ],
};

test('layered_animation_lab.html uses compiled TS module and computeImageCentroid caches opaque bbox', async ({
  canvas,
  page,
}) => {
  const rawHtml = await fs.promises.readFile(LAYERED_LAB_HTML_PATH, 'utf-8');
  expect(rawHtml).toContain(
    '<script type="module" src="/static/generated/testlab/testlab-layered-animation-lab.js"></script>',
  );
  expect(rawHtml).not.toContain('<script type="module">\n');
  expect(rawHtml).not.toContain('/effects-static/js/animation-effects.js');

  await canvas.mount({ html: '<div id="empty"></div>' });
  const result = await page.evaluate(async () => {
    const mod = await window.__importModule<TestlabLayeredLabModule>(
      '/static/generated/testlab/testlab-layered-animation-lab.js',
    );
    const emptyInit = mod.initLayeredAnimationLab();
    const nullCentroid = mod.computeImageCentroid(null);

    // Draw a 4x4 canvas with opaque pixels only in (0,0)-(1,1) (top-left quadrant).
    const offscreen = document.createElement('canvas');
    offscreen.width = 4;
    offscreen.height = 4;
    const offCtx = offscreen.getContext('2d')!;
    offCtx.fillStyle = 'rgba(255, 0, 0, 1)';
    offCtx.fillRect(0, 0, 2, 2);
    const dataUrl = offscreen.toDataURL('image/png');

    const img = new Image();
    await new Promise<void>((resolve) => {
      img.onload = (): void => resolve();
      img.src = dataUrl;
    });

    const first = mod.computeImageCentroid(img);
    const second = mod.computeImageCentroid(img);
    return { emptyInit, nullCentroid, first, sameCachedRef: first === second };
  });

  expect(result.emptyInit).toBeNull();
  expect(result.nullCentroid).toEqual({ cx: 0.5, cy: 0.5, maxRadiusRatio: 0.707 });
  expect(result.first.cx).toBeLessThan(0.3);
  expect(result.first.cy).toBeLessThan(0.3);
  expect(result.sameCachedRef).toBe(true);
});

test('Layered Animation Lab loads preset, supports layer stack CRUD, stage controls, and JSON import/export', async ({
  canvas,
  page,
}) => {
  const alerts: string[] = [];
  await page.addInitScript(() => {
    (window as unknown as { __alerts: string[] }).__alerts = [];
    window.alert = (message?: unknown): void => {
      (window as unknown as { __alerts: string[] }).__alerts.push(String(message));
    };
  });

  await setupLayeredLabRoutes(canvas, page, SAMPLE_CATALOG);
  await page.goto(`${CANVAS_ORIGIN}/layered-animation`);

  // Preset auto-loads 2 layers rendered foreground-first in the stack UI.
  await expect(page.locator('#preset-select')).toHaveValue('forest-scene');
  await expect(page.locator('#layer-count-status')).toHaveText('2 Layers Active');
  await expect(page.locator('#layer-list .layer-card')).toHaveCount(2);
  await expect(
    page.locator('#layer-list .layer-card input[data-action="rename"]').first(),
  ).toHaveValue('Swaying Tree');
  await expect(page.locator('#json-io')).toHaveValue(/Swaying Tree/);

  // Rename, adjust speed slider, and change piece/effect.
  const topCard = page.locator('#layer-list .layer-card').first();
  await topCard.locator('input[data-action="rename"]').fill('Ancient Pine');
  await topCard.locator('input[data-action="change-speed"]').fill('2');
  await topCard.locator('input[data-action="change-speed"]').dispatchEvent('input');
  await expect(topCard.locator('span[id^="label-speed-"]')).toHaveText('Speed (2.0x):');

  await topCard.locator('select[data-action="change-effect"]').selectOption('pulse');
  await topCard.locator('select[data-action="change-image"]').selectOption('/test-images/pieces/crystal.png');
  await expect(
    page.locator('#layer-list .layer-card select[data-action="change-image"]').first(),
  ).toHaveValue('/test-images/pieces/crystal.png');

  // Export JSON reflects updated name, effect, speed, and image.
  await page.locator('#btn-export-json').click();
  const exported = JSON.parse(await page.locator('#json-io').inputValue()) as {
    layers: Array<{ name: string; effect: string; speed: number; filename: string }>;
  };
  expect(exported.layers[1].name).toBe('Ancient Pine');
  expect(exported.layers[1].effect).toBe('pulse');
  expect(exported.layers[1].speed).toBe(2);
  expect(exported.layers[1].filename).toBe('crystal.png');

  // Toggle visibility and move layer down stack.
  await page.locator('#layer-list .layer-card').first().locator('button[data-action="toggle-vis"]').click();
  await expect(
    page.locator('#layer-list .layer-card').first().locator('button[data-action="toggle-vis"]'),
  ).toHaveText('🙈');

  await page.locator('#layer-list .layer-card').first().locator('button[data-action="move-down"]').click();
  await expect(
    page.locator('#layer-list .layer-card input[data-action="rename"]').first(),
  ).toHaveValue('Sky Backdrop');

  // Add Layer and Delete Layer.
  await page.locator('#btn-add-layer').click();
  await expect(page.locator('#layer-count-status')).toHaveText('3 Layers Active');
  await page.locator('#layer-list .layer-card').first().locator('button[data-action="delete"]').click();
  await expect(page.locator('#layer-count-status')).toHaveText('2 Layers Active');

  // Stage controls: background, aspect ratio, play/pause, master speed.
  await page.locator('#bg-select').selectOption('black');
  await expect(page.locator('#stage-viewport')).toHaveClass(/bg-black/);

  await page.locator('#aspect-select').selectOption('1/1');
  await expect(page.locator('#canvas-dim-status')).toContainText('Canvas:');

  await page.locator('#btn-play-pause').click();
  await expect(page.locator('#btn-play-pause')).toHaveText('Resume Motion');
  await page.locator('#btn-play-pause').click();
  await expect(page.locator('#btn-play-pause')).toHaveText('Pause Motion');

  await page.locator('#master-speed').fill('1.8');
  await page.locator('#master-speed').dispatchEvent('input');
  await expect(page.locator('#master-speed-val')).toHaveText('1.8x');

  // Clear All removes all layers and shows empty state message.
  await page.locator('#btn-clear-all').click();
  await expect(page.locator('#layer-count-status')).toHaveText('0 Layers Active');
  await expect(page.locator('#layer-list')).toContainText('No layers. Click "+ Add Layer" to start.');

  // Apply valid JSON restores layers; invalid JSON triggers alert.
  await page.locator('#json-io').fill(
    JSON.stringify({
      version: 1,
      layers: [
        {
          name: 'Imported Crystal',
          url: '/test-images/pieces/crystal.png',
          filename: 'crystal.png',
          effect: 'light_halo',
          speed: 1.5,
          amplitude: 1.2,
          opacity: 0.85,
          scale: 1.1,
        },
      ],
    }),
  );
  await page.locator('#btn-import-json').click();
  await expect(page.locator('#layer-count-status')).toHaveText('1 Layer Active');
  await expect(
    page.locator('#layer-list .layer-card input[data-action="rename"]').first(),
  ).toHaveValue('Imported Crystal');

  await page.locator('#json-io').fill('{not valid json');
  await page.locator('#btn-import-json').click();
  const capturedAlerts = await page.evaluate(
    () => (window as unknown as { __alerts: string[] }).__alerts,
  );
  alerts.push(...capturedAlerts);
  expect(alerts).toEqual(['Invalid JSON manifest format.']);
});

test('Layered Animation Lab falls back to default piece layers when catalog has no presets', async ({
  canvas,
  page,
}) => {
  await setupLayeredLabRoutes(canvas, page, {
    pieces: SAMPLE_CATALOG.pieces,
    effects: SAMPLE_CATALOG.effects,
    presets: [],
  });
  await page.goto(`${CANVAS_ORIGIN}/layered-animation`);

  await expect(page.locator('#layer-count-status')).toHaveText('3 Layers Active');
  await expect(
    page.locator('#layer-list .layer-card select[data-action="change-effect"]').nth(0),
  ).toHaveValue('vibrate');
  await expect(
    page.locator('#layer-list .layer-card select[data-action="change-effect"]').nth(1),
  ).toHaveValue('pulse');
  await expect(
    page.locator('#layer-list .layer-card select[data-action="change-effect"]').nth(2),
  ).toHaveValue('none');
});
