import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  REPO_ROOT,
  expect,
  test,
} from '../../testing/ui/canvas_fixtures';

const A2UI_LAB_HTML_PATH = path.join(REPO_ROOT, 'testlab', 'a2ui_canvas_lab.html');

const TINY_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);

test.describe('Testlab A2UI Canvas Lab TS module', () => {
  test('loads defaults, previews image, submits run, polls, and renders A2UI surface components', async ({
    canvas,
    page,
  }): Promise<void> => {
    void canvas;
    let pollCount = 0;

    await page.route(`${CANVAS_ORIGIN}/a2ui-canvas`, async (route: Route): Promise<void> => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html; charset=utf-8',
        body: fs.readFileSync(A2UI_LAB_HTML_PATH, 'utf8'),
      });
    });

    await page.route(`${CANVAS_ORIGIN}/api/a2ui-canvas/**`, async (route: Route): Promise<void> => {
      const req = route.request();
      const url = new URL(req.url());

      if (req.method() === 'GET' && url.pathname === '/api/a2ui-canvas/default-config') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            model: 'gemini-3.8-flash',
            request: 'Render party status card.',
            expected_surface_count: 1,
          }),
        });
        return;
      }

      if (req.method() === 'GET' && url.pathname === '/api/a2ui-canvas/images') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            images: [{ path: 'testlab/images/trace-knight-sword.png', label: 'Knight Sword' }],
          }),
        });
        return;
      }

      if (req.method() === 'GET' && url.pathname === '/api/a2ui-canvas/image') {
        await route.fulfill({
          status: 200,
          contentType: 'image/png',
          body: TINY_PNG,
        });
        return;
      }

      if (req.method() === 'POST' && url.pathname === '/api/a2ui-canvas/runs') {
        pollCount = 0;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'a2ui-run-1',
            status: 'running',
          }),
        });
        return;
      }

      if (req.method() === 'GET' && url.pathname === '/api/a2ui-canvas/runs/a2ui-run-1') {
        pollCount += 1;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'a2ui-run-1',
            status: pollCount >= 1 ? 'completed' : 'running',
            result: {
              result: { status: 'displayed' },
              surfaces: [
                {
                  placement: { left_pct: 25, top_pct: 70, width_pct: 30 },
                  messages: [
                    {
                      createSurface: {
                        components: [
                          { id: 'root', component: 'Card', child: 'col' },
                          { id: 'col', component: 'Column', children: ['hdr', 'hp', 'btn', 'div'] },
                          {
                            id: 'hdr',
                            component: 'Row',
                            children: ['ico', 'ttl'],
                          },
                          { id: 'ico', component: 'Icon', name: 'sword' },
                          {
                            id: 'ttl',
                            component: 'Text',
                            variant: 'heading',
                            text: { literalString: 'Knight Status' },
                          },
                          {
                            id: 'hp',
                            component: 'Progress',
                            variant: 'health',
                            label: 'HP',
                            value: { literalNumber: 80 },
                            max: 100,
                          },
                          { id: 'btn', component: 'Button', child: 'btn_lbl' },
                          { id: 'btn_lbl', component: 'Text', text: 'Inspect' },
                          { id: 'div', component: 'Divider' },
                        ],
                      },
                    },
                  ],
                },
              ],
            },
          }),
        });
        return;
      }

      await route.fulfill({ status: 404, body: 'Not found' });
    });

    await page.goto(`${CANVAS_ORIGIN}/a2ui-canvas`);

    await expect(page.locator('#model')).toHaveValue('gemini-3.8-flash');
    await expect(page.locator('#request')).toHaveValue('Render party status card.');
    await expect(page.locator('#surface_count')).toHaveValue('1');

    // Select image updates stage background
    await page.locator('#image_path').selectOption('testlab/images/trace-knight-sword.png');
    const bgStyle = await page.locator('#stage').evaluate(
      (node: HTMLElement): string => node.style.backgroundImage,
    );
    expect(bgStyle).toContain('/api/a2ui-canvas/image');

    // Submit run and verify surface rendering
    await page.locator('#submit').click();
    await expect(page.locator('#submit')).toBeEnabled();
    await expect(page.locator('#empty-stage')).toHaveJSProperty('hidden', true);
    await expect(page.locator('#stage .surface')).toHaveCount(1);
    await expect(page.locator('#stage .a2ui-text.heading')).toHaveText('Knight Status');
    await expect(page.locator('#stage .a2ui-progress.health')).toContainText('80/100');
    await expect(page.locator('#stage .a2ui-icon')).toHaveText('⚔');
    await expect(page.locator('#stage .a2ui-button')).toHaveText('Inspect');
  });
});
