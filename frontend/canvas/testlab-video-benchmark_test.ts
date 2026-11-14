import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  REPO_ROOT,
  expect,
  test,
} from '../../testing/ui/canvas_fixtures';

const VIDEO_BENCH_HTML_PATH = path.join(REPO_ROOT, 'testlab', 'video_benchmark.html');

test.describe('Testlab Video Benchmark TS module', () => {
  test('renders catalog, submits video generation run, polls completion, and displays gallery card', async ({
    canvas,
    page,
  }): Promise<void> => {
    void canvas;

    await page.route('https://fonts.googleapis.com/**', async (route: Route): Promise<void> => {
      await route.fulfill({
        status: 200,
        contentType: 'text/css; charset=utf-8',
        body: '',
      });
    });

    await page.route(`${CANVAS_ORIGIN}/video-benchmark`, async (route: Route): Promise<void> => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html; charset=utf-8',
        body: fs.readFileSync(VIDEO_BENCH_HTML_PATH, 'utf8'),
      });
    });

    await page.route(`${CANVAS_ORIGIN}/sample-video.mp4`, async (route: Route): Promise<void> => {
      await route.fulfill({
        status: 200,
        contentType: 'video/mp4',
        body: Buffer.alloc(0),
      });
    });

    await page.route(`${CANVAS_ORIGIN}/api/video-benchmark/**`, async (route: Route): Promise<void> => {
      const req = route.request();
      const url = new URL(req.url());

      if (req.method() === 'GET' && url.pathname === '/api/video-benchmark/catalog') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            providers: [
              {
                id: 'fal-minimax-h3-turbo',
                name: 'FAL MiniMax H3 Turbo',
                model: 'minimax/h3-max-turbo/text-to-video',
                status: 'available',
                notes: 'Fast cinematic video',
              },
              {
                id: 'other-provider',
                name: 'Other Provider',
                model: 'other/v1',
                status: 'unconfigured',
                notes: 'Needs key',
              },
            ],
            prompts: [
              {
                id: 'dragon-flight',
                title: 'Dragon Flight',
                category: 'Fantasy',
                prompt: 'A brass dragon soaring over misty spires.',
              },
            ],
          }),
        });
        return;
      }

      if (req.method() === 'POST' && url.pathname === '/api/video-benchmark/runs') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'vid-run-1',
            status: 'running',
          }),
        });
        return;
      }

      if (req.method() === 'GET' && url.pathname === '/api/video-benchmark/runs/vid-run-1') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'vid-run-1',
            status: 'completed',
            title: 'Dragon Flight',
            prompt: 'A brass dragon soaring over misty spires.',
            latency_ms: 2340,
            video_url: '/sample-video.mp4',
          }),
        });
        return;
      }

      await route.fulfill({ status: 404, body: 'Not found' });
    });

    await page.goto(`${CANVAS_ORIGIN}/video-benchmark`);

    await expect(page.locator('#providers .choice')).toHaveCount(2);
    await expect(page.locator('#providers .pill-available')).toContainText('Ready');
    await expect(page.locator('#prompts .choice')).toHaveCount(1);

    // Custom prompt auto-selects radio
    await page.locator('#customPromptText').fill('Custom storm clip');
    await expect(page.locator('#useCustomPrompt')).toBeChecked();

    await page.locator('#runBtn').click();

    await expect(page.locator('#status')).toContainText('Video generated in 2.34s');
    await expect(page.locator('#gallerySection')).not.toHaveClass(/hidden/);
    await expect(page.locator('#gallery .video-card')).toHaveCount(1);
    await expect(page.locator('#gallery .video-title')).toHaveText('Dragon Flight');
    await expect(page.locator('#gallery .download-btn')).toHaveAttribute('href', '/sample-video.mp4');
  });
});
