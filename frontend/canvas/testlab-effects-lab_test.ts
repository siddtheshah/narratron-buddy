import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  REPO_ROOT,
  STATIC_DIR,
  expect,
  test,
} from '../../testing/ui/canvas_fixtures';

const EFFECTS_LAB_HTML_PATH = path.join(REPO_ROOT, 'testlab', 'effects_lab.html');

const TINY_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);

test.describe('Testlab Effects Lab TS module', () => {
  test('switches effects, updates intensity, changes artwork, pauses motion, and toggles debug maps', async ({
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

    await page.route(`${CANVAS_ORIGIN}/effects-lab`, async (route: Route): Promise<void> => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html; charset=utf-8',
        body: fs.readFileSync(EFFECTS_LAB_HTML_PATH, 'utf8'),
      });
    });

    await page.route(`${CANVAS_ORIGIN}/effects-static/**`, async (route: Route): Promise<void> => {
      const rel = new URL(route.request().url()).pathname.replace(/^\/effects-static\//, '');
      const filePath = path.join(STATIC_DIR, rel);
      const ext = path.extname(filePath).toLowerCase();
      await route.fulfill({
        status: fs.existsSync(filePath) ? 200 : 404,
        contentType: ext === '.css' ? 'text/css; charset=utf-8' : 'application/octet-stream',
        body: fs.existsSync(filePath) ? fs.readFileSync(filePath) : '',
      });
    });

    await page.route(`${CANVAS_ORIGIN}/carousel/**`, async (route: Route): Promise<void> => {
      await route.fulfill({
        status: 200,
        contentType: 'image/png',
        body: TINY_PNG,
      });
    });

    await page.route(`${CANVAS_ORIGIN}/test-images/**`, async (route: Route): Promise<void> => {
      await route.fulfill({
        status: 200,
        contentType: 'image/png',
        body: TINY_PNG,
      });
    });

    await page.goto(`${CANVAS_ORIGIN}/effects-lab`);

    await expect(page.locator('#status')).toHaveText('Gleam 3: 75');
    await expect(page.locator('#artwork-title')).toHaveText('Spacetime vortex');

    // Change effect and intensity
    await page.locator('#effect').selectOption('sparkle');
    await expect(page.locator('#status')).toHaveText('Starlight twinkle: 75');

    await page.locator('#intensity').fill('40');
    await page.locator('#intensity').dispatchEvent('input');
    await expect(page.locator('#intensity-value')).toHaveText('40');
    await expect(page.locator('#status')).toHaveText('Starlight twinkle: 40');

    // Change artwork
    await page.locator('#artwork').selectOption('knightSword');
    await expect(page.locator('#artwork-title')).toHaveText('Trace knight and sword');
    await expect(page.locator('#artwork-kind')).toHaveText('trace target: sword blade');
    await expect(page.locator('#featured-image')).toHaveAttribute(
      'src',
      '/test-images/trace-knight-sword.png',
    );

    // Pause and resume motion
    await page.locator('#pause').click();
    await expect(page.locator('body')).toHaveClass(/motion-paused/);
    await expect(page.locator('#pause')).toHaveText('Resume motion');
    await page.locator('#pause').click();
    await expect(page.locator('#pause')).toHaveText('Pause motion');

    // Toggle debug maps
    await page.locator('#gleam3-seed-map').click();
    await expect(page.locator('#gleam3-seed-map')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#gleam3-seed-map')).toHaveText('Hide Gleam 3 flow seeds');

    await page.locator('#inverse-blur-map').click();
    await expect(page.locator('#inverse-blur-map')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#inverse-blur-map')).toHaveText('Hide V inverse-Gaussian map');

    await page.locator('#trace-map').click();
    await expect(page.locator('#trace-map')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#trace-map')).toHaveText('Hide Light trace pixels');
  });
});
