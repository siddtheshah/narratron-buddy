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

const TEXT_EFFECTS_HTML_PATH = path.join(REPO_ROOT, 'testlab', 'text_effects_lab.html');

test.describe('Testlab Text Effects Lab TS module', () => {
  test('loads presets, beautifies scene with dialogue, replays, and switches to sandbox tab', async ({
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

    await page.route(`${CANVAS_ORIGIN}/text-effects`, async (route: Route): Promise<void> => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html; charset=utf-8',
        body: fs.readFileSync(TEXT_EFFECTS_HTML_PATH, 'utf8'),
      });
    });

    await page.route(`${CANVAS_ORIGIN}/effects-static/**`, async (route: Route): Promise<void> => {
      const rel = new URL(route.request().url()).pathname.replace(/^\/effects-static\//, '');
      const filePath = path.join(STATIC_DIR, rel);
      const ext = path.extname(filePath).toLowerCase();
      const contentType =
        ext === '.css'
          ? 'text/css; charset=utf-8'
          : ext === '.js'
            ? 'text/javascript; charset=utf-8'
            : 'application/octet-stream';
      await route.fulfill({
        status: fs.existsSync(filePath) ? 200 : 404,
        contentType,
        body: fs.existsSync(filePath) ? fs.readFileSync(filePath) : '',
      });
    });

    await page.route(`${CANVAS_ORIGIN}/api/text-effects/presets`, async (route: Route): Promise<void> => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          presets: [
            {
              id: 'dragon',
              title: 'Dragon Roar',
              badge: 'Epic',
              narration: 'The cavern shakes!',
              dialogue: [{ speaker: 'Kael', text: 'Hold fast!' }],
            },
            {
              id: 'crypt',
              title: 'Whispering Crypt',
              badge: 'Horror',
              narration: 'Cold mist curls.',
              dialogue: [],
            },
          ],
        }),
      });
    });

    await page.route(`${CANVAS_ORIGIN}/api/text-effects/beautify`, async (route: Route): Promise<void> => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          latency_seconds: 0.42,
          model: 'gemini-3.5-flash-lite',
          narration: 'Fire!',
          narration_spans: [
            { text: 'Fire!', effect: 'flame', font: 'bangers', color: '#ff5722' },
          ],
          dialogue: [
            {
              speaker: 'Kael',
              text: 'Run!',
              kind: 'speech',
              spans: [{ text: 'Run!', effect: 'vibrate', font: 'bangers', color: '#ef4444' }],
            },
          ],
        }),
      });
    });

    await page.goto(`${CANVAS_ORIGIN}/text-effects`);

    await expect(page.locator('#presetsRow .preset-chip')).toHaveCount(2);
    await expect(page.locator('#narrationInput')).toHaveValue('The cavern shakes!');
    await expect(page.locator('#dialogueContainer .dialogue-item')).toHaveCount(1);

    // Add dialogue row via inline onclick="addDialogueRow()"
    await page.locator('.add-line-btn').click();
    await expect(page.locator('#dialogueContainer .dialogue-item')).toHaveCount(2);

    // Run beautification via inline onclick="submitBeautification()"
    await page.locator('#beautifyBtn').click();
    await expect(page.locator('#latencyBadge')).toContainText('0.42s');
    await expect(page.locator('#jsonContainer')).toBeVisible();
    await expect(page.locator('#sceneNarrationEl')).toContainText('Fire!');
    await expect(page.locator('#sceneDialogueEl')).toContainText('Kael');
    await expect(page.locator('#sceneDialogueEl')).toContainText('Run!');

    // Replay button via inline onclick="replayCurrentScene()"
    await page.locator('.replay-btn').click();
    await expect(page.locator('#sceneNarrationEl')).toContainText('Fire!');

    // Switch to Effect & Font Catalog tab via inline onclick="switchTab('catalog')"
    await page.locator('#tabCatalogBtn').click();
    await expect(page.locator('#viewCatalog')).toBeVisible();
    await expect(page.locator('#sandboxPreview .text-effect-flame')).toHaveCount(1);

    // Change sandbox effect via inline onchange="updateSandbox()"
    await page.locator('#sandboxEffect').selectOption('glitch');
    await expect(page.locator('#sandboxPreview .text-effect-glitch')).toHaveCount(1);
  });
});
