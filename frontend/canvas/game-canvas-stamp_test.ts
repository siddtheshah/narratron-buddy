import fs from 'node:fs';
import path from 'node:path';
import { expect, REPO_ROOT, test } from '../../testing/ui/canvas_fixtures';
import {
  loadGameSnapshots,
  parseSubmittedAction,
  type SubmittedGameAction,
} from '../../testing/ui/game_fixtures';
import type { GamePayload } from './game-renderer.js';

const snapshots = loadGameSnapshots();

test('winning checkers refreshes the page and keeps crown when panel closes', async ({
  canvas,
  page,
}) => {
  canvas.allowPageErrors();
  const crownPath = path.join(REPO_ROOT, 'adventures', 'example_adventure', 'stamps', 'crown.png');
  const crownBytes = fs.readFileSync(crownPath);
  const stampUrl = snapshots.checkers_near_win_stamps.stamps[0].url;
  expect(stampUrl.startsWith('/theaters/checkers/output/')).toBe(true);

  const submitted: SubmittedGameAction[] = [];
  let currentGame: GamePayload = snapshots.checkers_near_win_0;
  let hasWon = false;

  const canvasTs = fs.readFileSync(
    path.join(REPO_ROOT, 'frontend', 'canvas', 'canvas.ts'),
    'utf-8',
  );
  expect(canvasTs).toContain('const gameCanvasRevision = data.game_state?.canvas_revision;');
  expect(canvasTs).toContain('void requestPageDoodles();');

  await canvas.serveRoute(
    (url) =>
      url.origin === canvas.origin &&
      (url.pathname === '/latest' ||
        url.pathname === '/doodles' ||
        url.pathname === '/action' ||
        url.pathname === stampUrl),
    async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/latest') {
        await route.fulfill({ json: { game_state: currentGame } });
        return;
      }
      if (url.pathname === '/doodles') {
        await route.fulfill({
          json: hasWon
            ? snapshots.checkers_near_win_stamps
            : { ...snapshots.checkers_near_win_stamps, stamps: [] },
        });
        return;
      }
      if (url.pathname === '/action') {
        const action = parseSubmittedAction(route.request().postData());
        expect(action.canvas_revision).not.toBeUndefined();
        expect(action.canvas_revision).not.toBeNull();
        submitted.push(action);
        if (action.name === 'select') {
          currentGame = snapshots.checkers_near_win_1;
        } else {
          currentGame = snapshots.checkers_near_win_2;
          hasWon = true;
        }
        await route.fulfill({ json: currentGame });
        return;
      }
      await route.fulfill({ body: crownBytes, contentType: 'image/png' });
    },
  );

  await canvas.mount({
    viewport: { width: 1100, height: 800 },
    stylesheets: ['/static/css/chat.css'],
    inlineStyles: ['body { margin: 0; background: #1c2634; color: white; }'],
    html: `
      <div id="image-container" style="position:relative;width:1000px;height:700px">
        <canvas id="drawing" width="1000" height="700"></canvas>
        <div id="canvas-stamp-layer" style="position:absolute;inset:0"></div>
        <button id="toggle">Game</button>
        <div id="game"></div>
      </div>
    `,
    moduleScripts: [
      `
      import { createGameRenderer } from '/static/generated/canvas/game-renderer.js';
      import { createDoodleRenderer } from '/static/generated/canvas/doodle-renderer.js';
      let lastGameCanvasRevision = null;
      const stamps = createDoodleRenderer({
        canvas: document.querySelector('#drawing'),
        stampLayer: document.querySelector('#canvas-stamp-layer'),
        canMoveStamp: () => true,
      });
      async function requestPageDoodles() {
        const snapshot = await (await fetch('/doodles')).json();
        stamps.redraw(snapshot.stamps);
      }
      async function refresh() {
        const data = await (await fetch('/latest')).json();
        gameRenderer.render(data.game_state);
        const gameCanvasRevision = data.game_state?.canvas_revision;
        if (gameCanvasRevision !== undefined && gameCanvasRevision !== lastGameCanvasRevision) {
          lastGameCanvasRevision = gameCanvasRevision;
          void requestPageDoodles();
        }
      }
      const gameRenderer = createGameRenderer({
        container: document.querySelector('#game'),
        toggleButton: document.querySelector('#toggle'),
        canEdit: () => true,
        actionUrl: () => '/action',
        refresh,
        initiallyOpen: true,
      });
      refresh();
      `,
    ],
  });

  const frame = page.frameLocator('iframe.game-html-panel');
  await frame.locator('#square-42').click();
  await frame.locator('#square-28').click();
  await expect(frame.locator('#status')).toHaveText('Red wins');

  const crown = page.locator('.canvas-stamp-annotation');
  await expect(crown).toHaveCount(1);
  await expect(crown.locator('img')).toBeVisible();
  const naturalWidth = await page.evaluate(
    () => (document.querySelector('.canvas-stamp-annotation img') as HTMLImageElement | null)?.naturalWidth ?? 0,
  );
  expect(naturalWidth).toBeGreaterThan(0);
  expect(hasWon).toBe(true);
  expect(submitted).toHaveLength(2);

  await page.locator('#toggle').click();
  await expect(page.locator('#game')).toBeHidden();
  await expect(crown).toBeVisible();

  await page.reload();
  await expect(crown).toHaveCount(1);
  await expect(crown).toBeVisible();
  expect(canvas.pageErrors.filter((err) => !err.message.includes('serviceWorker'))).toEqual([]);
});
