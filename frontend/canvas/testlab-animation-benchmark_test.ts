import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  REPO_ROOT,
  expect,
  test,
} from '../../testing/ui/canvas_fixtures';

const ANIMATION_BENCH_HTML_PATH = path.join(REPO_ROOT, 'testlab', 'animation_benchmark.html');

const TINY_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);

test.describe('Testlab Animation Benchmark TS module', () => {
  test('loads catalog, submits run, polls manifest, and toggles layer/solo/base filters', async ({
    canvas,
    page,
  }): Promise<void> => {
    void canvas;

    await page.route(`${CANVAS_ORIGIN}/animation-benchmark`, async (route: Route): Promise<void> => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html; charset=utf-8',
        body: fs.readFileSync(ANIMATION_BENCH_HTML_PATH, 'utf8'),
      });
    });

    await page.route(`${CANVAS_ORIGIN}/assets/**`, async (route: Route): Promise<void> => {
      await route.fulfill({
        status: 200,
        contentType: 'image/png',
        body: TINY_PNG,
      });
    });

    await page.route(`${CANVAS_ORIGIN}/api/animation-benchmark/**`, async (route: Route): Promise<void> => {
      const req = route.request();
      const url = new URL(req.url());

      if (req.method() === 'GET' && url.pathname === '/api/animation-benchmark/catalog') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            providers: [{ id: 'gemini-anim', name: 'Gemini Animation', status: 'available' }],
            prompts: [
              { id: 'forest-path', title: 'Forest Path', prompt: 'Sunlit forest canopy' },
              { id: 'coastal-light', title: 'Coastal Light', prompt: 'Waves at dusk' },
            ],
          }),
        });
        return;
      }

      if (req.method() === 'POST' && url.pathname === '/api/animation-benchmark/runs') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'anim-run-1',
            status: 'running',
          }),
        });
        return;
      }

      if (req.method() === 'GET' && url.pathname === '/api/animation-benchmark/runs/anim-run-1') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'anim-run-1',
            status: 'completed',
            latency_ms: 1450,
            manifest: {
              base_url: '/assets/base.png',
              layers: [
                {
                  order: 0,
                  name: 'Canopy Leaves',
                  effect: 'sway',
                  description: 'Gentle leaf sway',
                  url: '/assets/layer0.png',
                },
                {
                  order: 1,
                  name: 'Floating Spores',
                  effect: 'float',
                  description: 'Soft ambient spores',
                  url: '/assets/layer1.png',
                },
              ],
            },
          }),
        });
        return;
      }

      await route.fulfill({ status: 404, body: 'Not found' });
    });

    await page.goto(`${CANVAS_ORIGIN}/animation-benchmark`);

    await expect(page.locator('#status')).toHaveText('Ready.');
    await expect(page.locator('#provider option')).toHaveCount(1);
    await expect(page.locator('#prompts label.choice')).toHaveCount(2);

    // Custom prompt validation check
    await page.locator('#useCustomPrompt').check();
    await page.locator('#run').click();
    await expect(page.locator('#status')).toHaveText('Enter a write-in prompt.');

    // Reset to preset prompt and run benchmark
    await page.locator('input[name="prompt-choice"][value="forest-path"]').check();
    await page.locator('#run').click();

    await expect(page.locator('#status')).toHaveText('Completed in 1.45 s.');
    await expect(page.locator('#layerCountBadge')).toHaveText('2 layers');
    await expect(page.locator('#activeFilterBadge')).toHaveText('All Layers (2)');
    await expect(page.locator('#stage img')).toHaveCount(2);
    await expect(page.locator('#layerFilterList .layer-card')).toHaveCount(2);

    // Solo second layer
    await page.locator('.layer-card').nth(1).locator('.solo-btn').click();
    await expect(page.locator('#activeFilterBadge')).toHaveText('Solo: Layer 2 (Floating Spores)');
    await expect(page.locator('#stage img')).toHaveCount(1);

    // Switch to base image view
    await page.locator('#btnBaseImage').click();
    await expect(page.locator('#activeFilterBadge')).toHaveText('Base Image (Original)');
    await expect(page.locator('#stage img')).toHaveAttribute('alt', 'Base Image');

    // Back to all layers
    await page.locator('#btnAllLayers').click();
    await expect(page.locator('#activeFilterBadge')).toHaveText('All Layers (2)');
    await expect(page.locator('#stage img')).toHaveCount(2);
  });
});
