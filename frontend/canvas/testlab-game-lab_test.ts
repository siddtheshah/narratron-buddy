import fs from 'node:fs';
import path from 'node:path';
import type { Page, Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  REPO_ROOT,
  STATIC_DIR,
  expect,
  test,
  type CanvasHarness,
} from '../../testing/ui/canvas_fixtures';

type TestlabGameLabModule = typeof import('./testlab-game-lab.js');

const GAME_LAB_HTML_PATH = path.join(REPO_ROOT, 'testlab', 'game_lab.html');

interface RecordedRequest {
  readonly method: string;
  readonly pathname: string;
  readonly search: string;
  readonly body: string | null;
}

interface MockGameLabRoutes {
  readonly requests: RecordedRequest[];
  setCatalogError(message: string | null): void;
  setSnapshot(next: Record<string, unknown>): void;
  setActionError(message: string | null): void;
  setScenarioResult(result: Record<string, unknown>): void;
}

function makeSampleSnapshot(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    session_id: 'sess-1',
    game: {
      definition_hash: 'hash-1',
      revision: 0,
      state: { turn: 1, selected: null },
      view: [
        {
          kind: 'column',
          props: {},
          children: [
            { kind: 'text', props: { text: 'Checkers Test Board' }, children: [] },
            {
              kind: 'button',
              props: { text: 'Select 40' },
              action: { name: 'select', arguments: { index: 40 }, permission: 'contributor' },
              children: [],
            },
            {
              kind: 'button',
              props: { text: 'Reset' },
              action: { name: 'reset', arguments: {}, permission: 'orator' },
              children: [],
            },
          ],
        },
      ],
    },
    state: {
      actions: {
        narratron_move: { description: 'AI moves a piece' },
      },
      state: {
        turn: 1,
        legal_moves: [{ source: 17, target: 24 }],
      },
    },
    turns: [
      {
        ai_actions: [{ name: 'narratron_move', arguments: { source: 17, target: 24 } }],
        responses: [{ response: 'I advance from 17 to 24.' }],
      },
    ],
    ai_play: true,
    ...overrides,
  };
}

async function setupGameLabRoutes(
  canvas: CanvasHarness,
  page: Page,
): Promise<MockGameLabRoutes> {
  const html = await fs.promises.readFile(GAME_LAB_HTML_PATH, 'utf-8');
  const requests: RecordedRequest[] = [];
  let catalogError: string | null = null;
  let currentSnapshot = makeSampleSnapshot();
  let actionError: string | null = null;
  let scenarioResult: Record<string, unknown> = {
    passed: true,
    scenario: 'checkers-player-opening',
    steps: [{ ok: true }],
  };
  let nextSessionSeq = 1;

  await canvas.serveRoute(
    (url: URL) =>
      url.origin === CANVAS_ORIGIN &&
      (url.pathname === '/game-lab' ||
        url.pathname.startsWith('/game-lab-assets/') ||
        url.pathname.startsWith('/api/game-lab/') ||
        url.pathname === '/favicon.ico'),
    async (route: Route) => {
      const url = new URL(route.request().url());
      const pathname = url.pathname;
      if (pathname === '/game-lab') {
        await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
        return;
      }
      if (pathname.startsWith('/game-lab-assets/js/')) {
        const name = pathname.slice('/game-lab-assets/js/'.length);
        const filePath = path.join(STATIC_DIR, 'generated', 'canvas', name);
        const body = await fs.promises.readFile(filePath);
        await route.fulfill({ contentType: 'text/javascript; charset=utf-8', body });
        return;
      }
      if (pathname.startsWith('/game-lab-assets/css/')) {
        const name = pathname.slice('/game-lab-assets/css/'.length);
        const filePath = path.join(STATIC_DIR, 'css', name);
        const body = await fs.promises.readFile(filePath);
        await route.fulfill({ contentType: 'text/css; charset=utf-8', body });
        return;
      }
      if (pathname === '/favicon.ico') {
        await route.fulfill({ status: 204 });
        return;
      }

      requests.push({
        method: route.request().method(),
        pathname,
        search: url.search,
        body: route.request().postData(),
      });

      if (pathname === '/api/game-lab/catalog') {
        if (catalogError) {
          await route.fulfill({ status: 500, json: { detail: catalogError } });
          return;
        }
        await route.fulfill({
          json: {
            games: [
              'adventures/example_adventure/game/game.yaml',
              'games/example_game/game/game.yaml',
            ],
            providers: [
              { id: 'gemini-3', name: 'Gemini 3', status: 'ready' },
              { id: 'openai', name: 'OpenAI', status: 'configured' },
            ],
          },
        });
        return;
      }

      if (pathname === '/api/game-lab/sessions' && route.request().method() === 'POST') {
        const sessionId = `sess-${nextSessionSeq}`;
        nextSessionSeq += 1;
        currentSnapshot = makeSampleSnapshot({ session_id: sessionId });
        await route.fulfill({ json: currentSnapshot });
        return;
      }

      if (pathname.startsWith('/api/game-lab/sessions/') && pathname.endsWith('/action')) {
        if (actionError) {
          await route.fulfill({ status: 400, json: { detail: actionError } });
          return;
        }
        const game = currentSnapshot.game as Record<string, unknown>;
        const nextRev = Number(game.revision ?? 0) + 1;
        currentSnapshot = {
          ...currentSnapshot,
          game: { ...game, revision: nextRev },
        };
        await route.fulfill({ json: currentSnapshot.game });
        return;
      }

      if (pathname.startsWith('/api/game-lab/sessions/')) {
        if (route.request().method() === 'DELETE') {
          await route.fulfill({ json: { closed: true } });
          return;
        }
        if (route.request().method() === 'PATCH') {
          await route.fulfill({ json: currentSnapshot });
          return;
        }
        await route.fulfill({ json: currentSnapshot });
        return;
      }

      if (pathname === '/api/game-lab/run') {
        await route.fulfill({ json: scenarioResult });
        return;
      }

      await route.fulfill({ status: 404, json: { detail: 'Not found' } });
    },
  );

  return {
    requests,
    setCatalogError(message: string | null): void {
      catalogError = message;
    },
    setSnapshot(next: Record<string, unknown>): void {
      currentSnapshot = next;
    },
    setActionError(message: string | null): void {
      actionError = message;
    },
    setScenarioResult(result: Record<string, unknown>): void {
      scenarioResult = result;
    },
  };
}

test('game_lab.html loads compiled TS bundle and initGameLab returns null on empty DOM', async ({
  canvas,
  page,
}) => {
  const html = await fs.promises.readFile(GAME_LAB_HTML_PATH, 'utf-8');
  expect(html).toContain(
    '<script type="module" src="/game-lab-assets/js/testlab-game-lab.js"></script>',
  );
  expect(html).not.toContain('<script type="module">\n');

  await canvas.mount({ html: '<div id="empty"></div>' });
  const initResult = await page.evaluate(async () => {
    const mod = await window.__importModule<TestlabGameLabModule>(
      '/static/generated/canvas/testlab-game-lab.js',
    );
    return mod.initGameLab();
  });
  expect(initResult).toBeNull();
});

test('Game Testing Lab loads catalog, manages sessions, handles role permissions, and closes cleanly', async ({
  canvas,
  page,
}) => {
  const mock = await setupGameLabRoutes(canvas, page);
  await page.goto(`${CANVAS_ORIGIN}/game-lab`);

  // Catalog populates games datalist and provider dropdown defaulting to gemini-3.
  await expect(page.locator('#games option')).toHaveCount(2);
  await expect(page.locator('#provider')).toHaveValue('gemini-3');

  // Load fresh game session.
  await page.locator('#load').click();
  await expect(page.locator('#placeholder')).toBeHidden();
  await expect(page.locator('#game-preview')).toBeVisible();
  await expect(page.locator('#close')).toBeEnabled();
  await expect(page.locator('#download')).toBeVisible();
  await expect(page.locator('#turn-summary')).toHaveText(
    'Revision 0 · 1 player actions · 1 AI actions',
  );
  await expect(page.locator('#replies')).toContainText('Narratron: I advance from 17 to 24.');
  await expect(page.locator('#status')).toHaveText('Ready for your next move.');

  // Role switching updates hint and button permissions on rendered board.
  const resetBtn = page.locator('#game-preview').getByRole('button', { name: 'Reset' });
  const selectBtn = page.locator('#game-preview').getByRole('button', { name: 'Select 40' });
  await expect(resetBtn).toBeEnabled();
  await expect(selectBtn).toBeEnabled();

  await page.locator('#role-contributor').click();
  await expect(page.locator('#role-hint')).toContainText('Simulating Contributor');
  await expect(resetBtn).toBeDisabled();
  await expect(selectBtn).toBeEnabled();

  await page.locator('#role-viewer').click();
  await expect(page.locator('#role-hint')).toContainText('Simulating Viewer');
  await expect(resetBtn).toBeDisabled();
  await expect(selectBtn).toBeDisabled();

  // Loading a second session deletes the first session first.
  await page.locator('#load').click();
  await expect
    .poll(() =>
      mock.requests.some(
        (req) => req.method === 'DELETE' && req.pathname === '/api/game-lab/sessions/sess-1',
      ),
    )
    .toBe(true);
  await expect(page.locator('#load')).toBeEnabled();

  // Closing session resets UI and clears placeholder.
  await page.locator('#close').click();
  await expect(page.locator('#placeholder')).toBeVisible();
  await expect(page.locator('#close')).toBeDisabled();
  await expect(page.locator('#download')).toBeHidden();
  await expect(page.locator('#state')).toHaveText('Session closed.');
  await expect(page.locator('#status')).toHaveText('Session closed. Load a game to start again.');
});

test('Game Testing Lab handles manual AI simulation, action errors, scenario runs, and transcript download', async ({
  canvas,
  page,
}) => {
  const mock = await setupGameLabRoutes(canvas, page);
  await page.goto(`${CANVAS_ORIGIN}/game-lab`);
  await page.locator('#load').click();
  await expect(page.locator('#game-preview')).toBeVisible();

  // Disabling AI play patches session and renders legal move manual AI buttons.
  await page.locator('#ai-play').uncheck();
  await expect(page.locator('#manual-ai-box')).toBeVisible();
  await expect(page.locator('#manual-ai-actions button')).toHaveText(['AI move: 17 → 24']);
  await expect(
    mock.requests.some(
      (req) =>
        req.method === 'PATCH' &&
        req.pathname === '/api/game-lab/sessions/sess-1' &&
        req.body === '{"ai_play":false}',
    ),
  ).toBe(true);

  // Clicking manual AI move executes action with ai_play=false&actor=narratron and refreshes.
  await page.locator('#manual-ai-actions button').first().click();
  await expect(page.locator('#turn-summary')).toHaveText(
    'Revision 1 · 1 player actions · 1 AI actions',
  );
  expect(
    mock.requests.some(
      (req) =>
        req.method === 'POST' &&
        req.pathname === '/api/game-lab/sessions/sess-1/action' &&
        req.search === '?ai_play=false&actor=narratron',
    ),
  ).toBe(true);

  // Fallback manual AI buttons when legal_moves is empty.
  mock.setSnapshot(
    makeSampleSnapshot({
      session_id: 'sess-1',
      state: {
        actions: { narratron_pass: { description: 'Pass turn' } },
        state: {},
      },
    }),
  );
  await page.locator('#manual-ai-actions button').first().click();
  await expect(page.locator('#manual-ai-actions button')).toHaveText(['Execute narratron_pass']);

  // Rejected manual AI action displays error in #status.
  mock.setActionError('AI action out of turn');
  await page.locator('#manual-ai-actions button').first().click();
  await expect(page.locator('#status')).toHaveText('AI action out of turn');
  await expect(page.locator('#status')).toHaveClass(/fail/);

  // Download button triggers a JSON blob download.
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#download').click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('game-session.json');

  // Scenario validation PASS and FAIL + invalid JSON handling.
  await page.locator('.scenario-section summary').click();
  await page.locator('#run').click();
  await expect(page.locator('#report')).toBeVisible();
  await expect(page.locator('#status')).toHaveText('PASS: checkers-player-opening');

  mock.setScenarioResult({ passed: false, scenario: 'checkers-player-opening' });
  await page.locator('#run').click();
  await expect(page.locator('#status')).toHaveText('FAIL: checkers-player-opening');
  await expect(page.locator('#status')).toHaveClass(/fail/);

  await page.locator('#scenario').fill('{invalid json');
  await page.locator('#run').click();
  await expect(page.locator('#status')).toHaveClass(/fail/);
});

test('Game Testing Lab reports catalog load errors', async ({ canvas, page }) => {
  const mock = await setupGameLabRoutes(canvas, page);
  mock.setCatalogError('Catalog unavailable');
  await page.goto(`${CANVAS_ORIGIN}/game-lab`);
  await expect(page.locator('#status')).toHaveText('Catalog unavailable');
  await expect(page.locator('#status')).toHaveClass(/fail/);
});
