import type { Page, Route } from '@playwright/test';
import { expect, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';
import {
  loadGameSnapshots,
  parseSubmittedAction,
} from '../../testing/ui/game_fixtures';
import type { GamePayload, GameRenderer } from './game-renderer.js';

type GameRendererModule = typeof import('./game-renderer.js');

interface AgentGameWindow extends Window {
  orator: boolean;
  creditMessage?: string;
  startErrorMessage?: string;
  checkAgentStatus: () => Promise<unknown>;
  gameRenderer: GameRenderer;
  canvasInitialized?: boolean;
}

interface RendererTestWindow extends Window {
  editable: boolean;
  contributable: boolean;
  movable: boolean;
  moves: Array<{ left_pct: number; top_pct: number }>;
  lastGame: GamePayload;
  refresh: () => Promise<void>;
  renderer: GameRenderer;
}

const RENDERER_MODULE_URL = '/static/generated/canvas/game-renderer.js';
const RENDERER_SHIM_URL = '/static/js/game-renderer.js';
const AGENT_CONNECTION_MODULE_URL = '/static/generated/canvas/agent-connection.js';

const snapshots = loadGameSnapshots();

async function setupAgentGamePage(
  canvas: CanvasHarness,
  page: Page,
  options: {
    onStart?: (route: Route) => Promise<void> | void;
  } = {},
): Promise<{ starts: string[]; allRequests: string[] }> {
  const starts: string[] = [];
  const allRequests: string[] = [];

  await page.routeWebSocket(/\/ws\/test\/agent/, () => {});

  await canvas.serveRoute(
    (url) =>
      url.origin === canvas.origin &&
      (url.pathname.endsWith('/agent/start') ||
        url.pathname.endsWith('/agent/stop') ||
        url.pathname.endsWith('/agent/status')),
    async (route) => {
      const reqUrl = route.request().url();
      allRequests.push(reqUrl);
      if (reqUrl.endsWith('/agent/start')) {
        starts.push(route.request().method());
        if (options.onStart) {
          await options.onStart(route);
          return;
        }
        await route.fulfill({ json: {} });
        return;
      }
      if (reqUrl.endsWith('/agent/stop')) {
        await route.fulfill({ json: {} });
        return;
      }
      await route.fulfill({ json: { agent_running: false } });
    },
  );

  await canvas.mount({
    html: `
      <button id="agent-start-stop-btn"></button>
      <button id="game-state-toggle-btn" hidden></button>
      <div id="game-state-layer" hidden></div>
      <div id="insufficient-credits-modal" hidden>
        <p id="credits-modal-message"></p>
      </div>
    `,
    moduleScripts: [
      `
      import { createGameRenderer } from '${RENDERER_SHIM_URL}';
      import { createAgentConnectionController } from '${AGENT_CONNECTION_MODULE_URL}';
      const theaterId = 'test';
      const agentStartStopBtn = document.getElementById('agent-start-stop-btn');
      const creditsModal = document.getElementById('insufficient-credits-modal');
      const creditsModalMessage = document.getElementById('credits-modal-message');
      window.orator = true;
      Object.defineProperty(window, 'creditMessage', {
        get() {
          if (!creditsModal?.classList.contains('active')) return undefined;
          return creditsModalMessage?.textContent || undefined;
        },
        configurable: true,
      });
      let agentConnection = null;
      const gameRenderer = createGameRenderer({
        container: document.getElementById('game-state-layer'),
        toggleButton: document.getElementById('game-state-toggle-btn'),
        actionUrl: () => '/api/game/action?theater_id=' + encodeURIComponent(theaterId),
        canContribute: () => false,
        canMove: () => window.orator,
        canEdit: () => window.orator,
        refresh: () => {},
        beforeOpen: async () => {
          if (window.orator && !agentConnection.isAgentStarted()) {
            await agentConnection.startAgentTheater();
          }
        },
      });
      agentConnection = createAgentConnectionController({
        theaterId,
        agentStartStopBtn,
        agentConnectBtn: null,
        statusIndicator: null,
        insufficientCreditsModal: creditsModal,
        creditsModalMessage,
        isCurrentOrator: () => window.orator,
        isActiveCanvasWindow: () => true,
        getMicMode: () => 'toggle',
        refreshMicrophoneDevices: async () => {},
        getStartAudioRecorderWorklet: () => undefined,
        getSetMicrophoneTransmissionEnabled: () => undefined,
        getStopMicrophone: () => undefined,
        closeGameRenderer: () => gameRenderer.close(),
        showShareToast: message => { window.startErrorMessage = message; },
        updateAgentThought: () => {},
        setPcmBroadcasting: () => {},
        stopSceneSpeech: () => {},
      });
      window.checkAgentStatus = () => agentConnection.checkAgentStatus();
      window.gameRenderer = gameRenderer;
      gameRenderer.render({
        definition_hash: 'test',
        revision: 0,
        state: {},
        view: [{ kind: 'text', props: { text: 'Game ready' } }],
      });
      agentConnection.setAgentStartedUI(false);
      `,
    ],
  });

  await expect(page.getByRole('button', { name: 'Toggle game' })).toBeVisible();
  return { starts, allRequests };
}

async function setupMovableGamePage(canvas: CanvasHarness, page: Page): Promise<void> {
  let currentGame: GamePayload = snapshots.runtime_checkers.payload;

  await canvas.serveRoute(
    (url) =>
      url.origin === canvas.origin && (url.pathname === '/latest' || url.pathname === '/action'),
    async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/latest') {
        await route.fulfill({ json: currentGame });
        return;
      }
      const action = parseSubmittedAction(route.request().postData());
      if (
        action.name === 'move' &&
        action.arguments.source === 42 &&
        action.arguments.target === 35 &&
        snapshots.runtime_checkers.draft_move_result
      ) {
        currentGame = snapshots.runtime_checkers.draft_move_result;
      }
      await route.fulfill({ json: currentGame });
    },
  );

  await canvas.mount({
    viewport: { width: 1000, height: 800 },
    inlineStyles: [
      'body { background: #101827; margin: 0; padding: 0; width: 1000px; height: 800px; position: relative; }',
    ],
    html: `
      <button id="game-state-toggle-btn" hidden aria-label="Toggle game" aria-expanded="false"></button>
      <div id="game-state-layer" hidden></div>
    `,
    moduleScripts: [
      `
      import { createGameRenderer } from '${RENDERER_MODULE_URL}';
      window.editable = true;
      window.contributable = false;
      window.movable = false;
      window.moves = [];
      window.refresh = async () => {
        window.lastGame = await (await fetch('/latest')).json();
        renderer.render(window.lastGame);
      };
      const renderer = createGameRenderer({
        container: document.getElementById('game-state-layer'),
        toggleButton: document.getElementById('game-state-toggle-btn'),
        actionUrl: () => '/action',
        canEdit: () => window.editable,
        canContribute: () => window.contributable,
        canMove: () => window.editable || window.movable,
        refresh: window.refresh,
        onMove: move => window.moves.push(move),
      });
      window.renderer = renderer;
      window.refresh();
      `,
    ],
  });

  await page.getByRole('button', { name: 'Toggle game' }).click();
  await expect(page.locator('.game-panel')).toBeVisible();
}

test('deprecated game-renderer shim re-exports createGameRenderer', async ({ canvas, page }) => {
  await canvas.mount();
  const sameReference = await page.evaluate(
    async ({ canonicalUrl, shimUrl }) => {
      const canonical = await window.__importModule<GameRendererModule>(canonicalUrl);
      const shim = await window.__importModule<GameRendererModule>(shimUrl);
      return canonical.createGameRenderer === shim.createGameRenderer;
    },
    { canonicalUrl: RENDERER_MODULE_URL, shimUrl: RENDERER_SHIM_URL },
  );
  expect(sameReference).toBe(true);
});

test('opening game summons once and banishing closes panel', async ({ canvas, page }) => {
  const { starts } = await setupAgentGamePage(canvas, page);
  const toggle = page.getByRole('button', { name: 'Toggle game' });
  await toggle.click();
  await expect(page.locator('#game-state-layer')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Banish' })).toBeVisible();
  expect(starts).toEqual(['POST']);

  await toggle.click();
  await expect(page.locator('#game-state-layer')).toBeHidden();
  await toggle.click();
  await expect(page.locator('#game-state-layer')).toBeVisible();
  expect(starts).toEqual(['POST']);

  await page.getByRole('button', { name: 'Banish' }).click();
  await expect(page.locator('#game-state-layer')).toBeHidden();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');

  await toggle.click();
  await expect(page.locator('#game-state-layer')).toBeVisible();
  expect(starts).toEqual(['POST', 'POST']);
});

test('stopped agent status closes game panel', async ({ canvas, page }) => {
  await setupAgentGamePage(canvas, page);
  await page.getByRole('button', { name: 'Toggle game' }).click();
  await expect(page.locator('#game-state-layer')).toBeVisible();
  await page.evaluate(() => (window as unknown as AgentGameWindow).checkAgentStatus());
  await expect(page.locator('#game-state-layer')).toBeHidden();
});

test('game stays closed when summoning fails with insufficient credits', async ({
  canvas,
  page,
}) => {
  await setupAgentGamePage(canvas, page, {
    onStart: async (route) => {
      await route.fulfill({
        status: 402,
        json: { insufficient_credits: true, detail: 'Top up credits' },
      });
    },
  });

  const toggle = page.getByRole('button', { name: 'Toggle game' });
  await toggle.click();
  await page.waitForFunction(
    () => (window as unknown as AgentGameWindow).creditMessage === 'Top up credits',
  );
  await expect(page.locator('#game-state-layer')).toBeHidden();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(page.getByRole('button', { name: 'Summon' })).toBeVisible();
});

for (const [status, detail] of [
  [401, 'Authentication required.'],
  [403, 'Only the active orator can start the agent.'],
  [500, 'Could not start Narratron (500). Please try again.'],
] as const) {
  test(`failed start (${status}) does not label error as insufficient credits`, async ({
    canvas,
    page,
  }) => {
    await setupAgentGamePage(canvas, page, {
      onStart: async (route) => {
        await route.fulfill({
          status,
          json: status !== 500 ? { detail } : {},
        });
      },
    });

    await page.getByRole('button', { name: 'Toggle game' }).click();
    await expect(page.getByRole('button', { name: 'Summon' })).toBeVisible();
    expect(
      await page.evaluate(() => (window as unknown as AgentGameWindow).creditMessage === undefined),
    ).toBe(true);
    expect(
      await page.evaluate(() => (window as unknown as AgentGameWindow).startErrorMessage),
    ).toBe(detail);
    await expect(page.locator('#game-state-layer')).toBeHidden();
  });
}

test('viewer can open game without summoning', async ({ canvas, page }) => {
  const { allRequests } = await setupAgentGamePage(canvas, page);
  await page.evaluate(() => {
    (window as unknown as AgentGameWindow).orator = false;
  });
  await page.getByRole('button', { name: 'Toggle game' }).click();
  await expect(page.locator('#game-state-layer')).toBeVisible();
  expect(allRequests).toEqual([]);
});

test('closing game during summon cancels pending open', async ({ canvas, page }) => {
  const pending: Route[] = [];
  await setupAgentGamePage(canvas, page, {
    onStart: (route) => {
      pending.push(route);
    },
  });

  const toggle = page.getByRole('button', { name: 'Toggle game' });
  await toggle.click();
  await expect(page.getByRole('button', { name: 'Starting...' })).toBeVisible();
  await expect(page.locator('#game-state-layer')).toBeHidden();

  await toggle.click();
  expect(pending).toHaveLength(1);
  await pending[0].fulfill({ json: {} });
  await expect(page.getByRole('button', { name: 'Banish' })).toBeVisible();
  await expect(page.locator('#game-state-layer')).toBeHidden();
});

test('canvas game permissions do not interrupt startup', async ({ canvas, page }) => {
  await canvas.openCanvas({
    isOrator: false,
    apiRoutes: {
      '/api/latest': async (route) => {
        await route.fulfill({ json: { game_state: snapshots.counter_badge_0 } });
      },
    },
  });

  await expect(page.locator('#game-state-toggle-btn')).toBeVisible();
  expect(canvas.pageErrors).toEqual([]);
});

test('checkers renderer moves, mobile geometry, viewer/contributor permissions, SVG asset, and error recovery', async ({
  canvas,
  page,
}) => {
  canvas.allowPageErrors();
  let mode: 'checkers' | 'counter' = 'checkers';
  let currentCheckers = snapshots.example_checkers_0;
  let currentCounter = snapshots.counter_badge_0;

  await canvas.serveRoute(
    (url) =>
      url.origin === canvas.origin && (url.pathname === '/latest' || url.pathname === '/action'),
    async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/latest') {
        await route.fulfill({ json: mode === 'checkers' ? currentCheckers : currentCounter });
        return;
      }
      const action = parseSubmittedAction(route.request().postData());
      if (mode === 'checkers') {
        if (action.name === 'select' && action.arguments.index === 40) {
          currentCheckers = snapshots.example_checkers_1;
        } else if (action.name === 'move' && action.arguments.target === 33) {
          currentCheckers = snapshots.example_checkers_2;
        } else if (action.name === 'select' && action.arguments.index === 17) {
          currentCheckers = snapshots.example_checkers_3;
        } else if (action.name === 'move' && action.arguments.target === 26) {
          currentCheckers = snapshots.example_checkers_4;
        }
        await route.fulfill({ json: currentCheckers });
      } else {
        currentCounter =
          currentCounter.revision === 0 ? snapshots.counter_badge_1 : snapshots.counter_badge_2;
        await route.fulfill({ json: currentCounter });
      }
    },
  );

  await canvas.mount({
    viewport: { width: 1100, height: 900 },
    inlineStyles: ['body { background: #101827; }'],
    html: `
      <button id="game-state-toggle-btn" hidden aria-label="Toggle game" aria-expanded="false"></button>
      <div id="game-state-layer" hidden></div>
    `,
    moduleScripts: [
      `
      import { createGameRenderer } from '${RENDERER_SHIM_URL}';
      window.editable = true;
      window.contributable = false;
      window.refresh = async () => {
        window.lastGame = await (await fetch('/latest')).json();
        renderer.render(window.lastGame);
      };
      const renderer = createGameRenderer({
        container: document.getElementById('game-state-layer'),
        toggleButton: document.getElementById('game-state-toggle-btn'),
        actionUrl: () => '/action',
        canEdit: () => window.editable,
        canContribute: () => window.contributable,
        refresh: window.refresh,
      });
      window.refresh();
      `,
    ],
  });

  const toggle = page.getByRole('button', { name: 'Toggle game' });
  await expect(toggle).toBeVisible();
  await toggle.hover();
  await expect(page.locator('#game-state-layer')).toBeHidden();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');

  await toggle.click();
  await expect(page.locator('#game-state-layer')).toBeVisible();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');

  const frame = page.frameLocator('iframe.game-html-panel');
  await expect(frame.getByRole('img', { name: 'Checkers Club crest' })).toBeVisible();
  await expect(frame.locator('.board button')).toHaveCount(32);

  await frame.locator('#square-40').click();
  await expect(frame.locator('#square-33')).toBeEnabled();
  await frame.locator('#square-33').click();
  await expect(frame.locator('#status')).toHaveText('Ivory to move');
  expect(currentCheckers.revision).toBe(2);
  await expect(page.getByRole('button', { name: 'Submit', exact: true })).toHaveCount(0);
  await expect(frame.locator('#square-33 .piece.red')).toBeVisible();

  await toggle.click();
  await page.evaluate(() => (window as unknown as RendererTestWindow).refresh());
  await expect(page.locator('#game-state-layer')).toBeHidden();
  await page.reload();
  await expect(page.locator('#game-state-layer')).toBeHidden();
  await toggle.click();
  await expect(frame.locator('#square-33 .piece.red')).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  const geometry = await frame.locator('#square-33').evaluate((square: Element) => {
    const cell = square.getBoundingClientRect();
    const piece = square.querySelector('.piece.red')!.getBoundingClientRect();
    const board = document.querySelector('.board')!.getBoundingClientRect();
    return {
      diameter: piece.width / cell.width,
      height: piece.height / cell.height,
      centerX: (piece.left + piece.width / 2 - cell.left) / cell.width,
      centerY: (piece.top + piece.height / 2 - cell.top) / cell.height,
      boardTop: board.top,
      boardBottom: board.bottom,
      frameHeight: window.innerHeight,
    };
  });
  expect(geometry.diameter).toBeCloseTo(0.8, 2);
  expect(geometry.height).toBeCloseTo(0.8, 2);
  expect(geometry.centerX).toBeCloseTo(0.5, 2);
  expect(geometry.centerY).toBeCloseTo(0.5, 2);
  expect(geometry.boardTop).toBeGreaterThanOrEqual(0);
  expect(geometry.boardBottom).toBeGreaterThan(geometry.boardTop);
  expect(geometry.boardBottom).toBeLessThanOrEqual(geometry.frameHeight);

  const bounds = await page.locator('.game-panel').boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);

  await page.evaluate(() => {
    const win = window as unknown as RendererTestWindow;
    win.editable = false;
    void win.refresh();
  });
  await expect(frame.getByRole('button', { name: 'New game' })).toBeDisabled();
  expect(await frame.locator('.board button:enabled').count()).toBe(0);

  await page.evaluate(() => {
    const win = window as unknown as RendererTestWindow;
    win.contributable = true;
    void win.refresh();
  });
  await expect(frame.getByRole('button', { name: 'New game' })).toBeDisabled();
  await expect(frame.locator('#square-17')).toBeEnabled();
  await frame.locator('#square-17').click();
  await expect(frame.locator('#square-26')).toBeEnabled();
  await frame.locator('#square-26').click();
  await expect(frame.locator('#status')).toHaveText('Red to move');
  await expect(frame.locator('#square-26 .piece.ivory')).toBeVisible();

  // Switch to counter + inline SVG badge.svg
  mode = 'counter';
  await page.reload();
  await toggle.click();
  await expect(page.locator('[data-game-id="counter"]')).toHaveText('Count: 0');
  await expect(page.locator('.game-svg circle')).toHaveAttribute('r', '0');
  await expect(page.locator('[data-game-id="svg-badge"]')).toHaveAttribute(
    'data-game-svg',
    'badge.svg',
  );
  await expect(page.locator('[data-game-id="svg-badge"] g path')).toHaveAttribute(
    'd',
    'M12 2 L15 9 L22 9 L16 14 L18 21 L12 17 L6 21 L8 14 L2 9 L9 9 Z',
  );

  await page.getByRole('button', { name: 'Add one' }).click();
  await expect(page.locator('[data-game-id="counter"]')).toHaveText('Count: 1');
  expect(currentCounter.revision).toBe(1);

  await toggle.click();
  currentCounter = snapshots.counter_badge_err;
  await page.evaluate(() => (window as unknown as RendererTestWindow).refresh());
  await expect(toggle).toHaveAttribute('data-game-error', 'true');
  await expect(page.locator('#game-state-layer')).toBeHidden();
  await toggle.hover();
  await expect(page.locator('#game-state-layer')).toBeHidden();

  await toggle.click();
  await expect(page.getByRole('alert')).toContainText('previous game state was retained');
  await expect(page.locator('[data-game-id="counter"]')).toHaveText('Count: 1');

  await page.getByRole('button', { name: 'Add one' }).click();
  await expect(toggle).toHaveAttribute('data-game-error', 'false');
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(canvas.pageErrors.filter((err) => !err.message.includes('serviceWorker'))).toEqual([]);
});

for (const edge of ['top', 'bottom', 'left', 'right', 'control'] as const) {
  test(`game panel drag from ${edge} preserves grab offset and saves position`, async ({
    canvas,
    page,
  }) => {
    await setupMovableGamePage(canvas, page);
    const panel = page.locator('.game-panel');
    const bounds = await panel.boundingBox();
    expect(bounds).not.toBeNull();
    let x = bounds!.x + bounds!.width / 2;
    let y = bounds!.y + bounds!.height / 2;
    if (edge === 'top') {
      y = bounds!.y + 10;
    } else if (edge === 'bottom') {
      y = bounds!.y + bounds!.height - 10;
    } else if (edge === 'left') {
      x = bounds!.x + 10;
    } else if (edge === 'right') {
      x = bounds!.x + bounds!.width - 10;
    } else {
      const control = await page.locator('.game-move').boundingBox();
      expect(control).not.toBeNull();
      x = control!.x + control!.width / 2;
      y = control!.y + control!.height / 2;
    }

    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + 100, y + 80);
    expect(await panel.evaluate((el: HTMLElement) => el.style.left)).toBe('60%');
    expect(await panel.evaluate((el: HTMLElement) => el.style.top)).toBe('60%');
    expect((await panel.getAttribute('class'))?.split(/\s+/)).toContain('dragging');

    await page.mouse.up();
    expect((await panel.getAttribute('class'))?.split(/\s+/)).not.toContain('dragging');
    expect(await page.evaluate(() => (window as unknown as RendererTestWindow).moves)).toEqual([
      { left_pct: 60, top_pct: 60 },
    ]);
  });
}

test('game panel button click does not drag', async ({ canvas, page }) => {
  await setupMovableGamePage(canvas, page);
  const panel = page.locator('.game-panel');
  const button = page.locator('[data-game-id="square-40"]');
  await button.click();
  expect(await panel.evaluate((el: HTMLElement) => el.style.left)).toBe('50%');
  expect(await panel.evaluate((el: HTMLElement) => el.style.top)).toBe('50%');
  expect(await page.evaluate(() => (window as unknown as RendererTestWindow).moves)).toEqual([]);
  await expect(button).toHaveAttribute('aria-pressed', 'true');
});

test('game panel cancelled drag restores position', async ({ canvas, page }) => {
  await setupMovableGamePage(canvas, page);
  const panel = page.locator('.game-panel');
  await page.mouse.move(370, 400);
  await page.mouse.down();
  await page.mouse.move(470, 480);
  await panel.evaluate((el: Element) => {
    el.dispatchEvent(new PointerEvent('pointercancel', { pointerId: 1 }));
  });
  await page.mouse.up();
  expect(await panel.evaluate((el: HTMLElement) => el.style.left)).toBe('50%');
  expect(await panel.evaluate((el: HTMLElement) => el.style.top)).toBe('50%');
  expect(await page.evaluate(() => (window as unknown as RendererTestWindow).moves)).toEqual([]);
});

test('game panel read-only cannot be dragged', async ({ canvas, page }) => {
  await setupMovableGamePage(canvas, page);
  await page.evaluate(() => {
    const win = window as unknown as RendererTestWindow;
    win.editable = false;
    void win.refresh();
  });
  await expect(page.locator('.game-panel-editable')).toHaveCount(0);
  await expect(page.locator('.game-move')).toHaveCount(0);
  const panel = page.locator('.game-panel');
  await page.mouse.move(370, 400);
  await page.mouse.down();
  await page.mouse.move(470, 480);
  await page.mouse.up();
  expect(await panel.evaluate((el: HTMLElement) => el.style.left)).toBe('50%');
  expect(await panel.evaluate((el: HTMLElement) => el.style.top)).toBe('50%');
  expect(await page.evaluate(() => (window as unknown as RendererTestWindow).moves)).toEqual([]);
});

test('game panel toggle preserves position', async ({ canvas, page }) => {
  await setupMovableGamePage(canvas, page);
  const control = await page.locator('.game-move').boundingBox();
  expect(control).not.toBeNull();
  const x = control!.x + control!.width / 2;
  const y = control!.y + control!.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 100, y + 80);
  await page.mouse.up();

  const panel = page.locator('.game-panel');
  expect(await panel.evaluate((el: HTMLElement) => el.style.left)).toBe('60%');
  expect(await panel.evaluate((el: HTMLElement) => el.style.top)).toBe('60%');

  const toggle = page.getByRole('button', { name: 'Toggle game' });
  await toggle.click();
  await expect(panel).toBeHidden();
  await toggle.click();
  await expect(panel).toBeVisible();
  expect(await panel.evaluate((el: HTMLElement) => el.style.left)).toBe('60%');
  expect(await panel.evaluate((el: HTMLElement) => el.style.top)).toBe('60%');
});

for (const collaboration of [false, true] as const) {
  test(`contributor panel position is private and survives refresh (collab=${collaboration})`, async ({
    canvas,
    page,
  }) => {
    await setupMovableGamePage(canvas, page);
    await page.evaluate((collab: boolean) => {
      const win = window as unknown as RendererTestWindow;
      win.editable = false;
      win.movable = true;
      win.contributable = collab;
      win.renderer.render({ ...win.lastGame, placement: { left_pct: 50, top_pct: 50 } });
    }, collaboration);

    const control = page.locator('.game-move');
    await expect(control).toBeVisible();
    const bounds = await control.boundingBox();
    expect(bounds).not.toBeNull();
    const x = bounds!.x + bounds!.width / 2;
    const y = bounds!.y + bounds!.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + 100, y + 80);
    await page.mouse.up();

    const panel = page.locator('.game-panel');
    expect(await panel.evaluate((el: HTMLElement) => el.style.left)).toBe('60%');
    expect(await panel.evaluate((el: HTMLElement) => el.style.top)).toBe('60%');
    expect(await page.evaluate(() => (window as unknown as RendererTestWindow).moves)).toEqual([]);

    await page.evaluate(() => {
      const win = window as unknown as RendererTestWindow;
      win.renderer.render({
        ...win.lastGame,
        revision: 1,
        placement: { left_pct: 40, top_pct: 40 },
      });
    });
    expect(await panel.evaluate((el: HTMLElement) => el.style.left)).toBe('60%');
    expect(await panel.evaluate((el: HTMLElement) => el.style.top)).toBe('60%');

    const toggle = page.getByRole('button', { name: 'Toggle game' });
    await toggle.click();
    await toggle.click();
    expect(await panel.evaluate((el: HTMLElement) => el.style.left)).toBe('60%');
    expect(await panel.evaluate((el: HTMLElement) => el.style.top)).toBe('60%');
    await expect(page.getByRole('button', { name: 'New game' })).toBeDisabled();
  });
}

test('pending submit keeps panel dimensions and disables controls', async ({ canvas, page }) => {
  await setupMovableGamePage(canvas, page);
  const heldActions: Route[] = [];
  await page.route(`${canvas.origin}/action`, (route) => {
    heldActions.push(route);
  });

  await page.locator('[data-game-id="square-40"]').click();
  await page.locator('[data-game-id="square-33"]').click();
  const panel = page.locator('.game-panel');
  const before = await panel.boundingBox();
  expect(before).not.toBeNull();

  const submit = page.getByRole('button', { name: 'Submit', exact: true });
  await submit.click();
  await expect(panel).toHaveAttribute('aria-busy', 'true');
  await expect(submit).toBeDisabled();
  await expect(submit).toHaveText('Submit');
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeDisabled();
  await expect(page.getByText('Resolving action…', { exact: true })).toHaveCount(0);

  const during = await panel.boundingBox();
  expect(during).not.toBeNull();
  for (const dim of ['x', 'y', 'width', 'height'] as const) {
    expect(Math.abs(during![dim] - before![dim])).toBeLessThan(1);
  }

  expect(heldActions).toHaveLength(1);
  await heldActions[0].fulfill({ status: 409, json: { detail: 'Preview expired.' } });
  await expect(panel).toHaveAttribute('aria-busy', 'false');
  await expect(page.getByRole('alert')).toHaveText('Preview expired.');
});

test('exploration, undo, and polling need no action requests', async ({ canvas, page }) => {
  await setupMovableGamePage(canvas, page);
  const requests: string[] = [];
  page.on('request', (request) => {
    requests.push(request.url());
  });

  await page.locator('[data-game-id="square-40"]').click();
  const submit = page.getByRole('button', { name: 'Submit', exact: true });
  await expect(submit).toBeDisabled();
  await page.locator('[data-game-id="square-33"]').click();
  await expect(submit).toBeEnabled();
  await page.mouse.move(0, 0);
  expect(
    await submit.evaluate((el: HTMLElement) => getComputedStyle(el).backgroundColor),
  ).toBe('rgb(249, 115, 22)');
  await expect(page.locator('[data-game-id="status"]')).toHaveText('Ivory to move');
  expect(requests).toEqual([]);

  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await expect(submit).toBeDisabled();
  expect(
    await submit.evaluate((el: HTMLElement) => getComputedStyle(el).backgroundColor),
  ).not.toBe('rgb(249, 115, 22)');
  await expect(page.locator('[data-game-id="square-40"]')).toHaveAttribute('aria-pressed', 'true');

  await page.locator('[data-game-id="square-42"]').click();
  await page.locator('[data-game-id="square-35"]').click();
  expect(requests).toEqual([]);

  await page.evaluate(() => (window as unknown as RendererTestWindow).refresh());
  await expect(page.locator('[data-game-id="square-35"] circle')).toHaveAttribute(
    'fill',
    '#e05252',
  );
  expect(requests).toHaveLength(1);
  expect(requests[0].endsWith('/latest')).toBe(true);

  await page.getByRole('button', { name: 'Submit', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Submit', exact: true })).toHaveCount(0);
  expect(requests.filter((url) => url.endsWith('/action'))).toHaveLength(1);
  await page.waitForFunction(() => (window as unknown as RendererTestWindow).lastGame.revision === 1);
  expect(
    await page.evaluate(() => (window as unknown as RendererTestWindow).lastGame.revision),
  ).toBe(1);
  expect(
    await page.evaluate(
      () => ((window as unknown as RendererTestWindow).lastGame.state?.board as number[])[40],
    ),
  ).toBe(1);
  expect(
    await page.evaluate(
      () => ((window as unknown as RendererTestWindow).lastGame.state?.board as number[])[35],
    ),
  ).toBe(1);
});

for (const viewport of [
  { width: 1100, height: 950 },
  { width: 390, height: 844 },
] as const) {
  test(`information tabs are private, accessible, and survive updates (${viewport.width}x${viewport.height})`, async ({
    canvas,
    page,
  }) => {
    await setupMovableGamePage(canvas, page);
    await page.setViewportSize(viewport);
    await page.evaluate(() => {
      document.body.style.width = '100vw';
      document.body.style.height = '100vh';
      const win = window as unknown as RendererTestWindow;
      win.editable = false;
      win.lastGame = {
        ...win.lastGame,
        view: [
          {
            kind: 'tabs',
            props: { id: 'information', label: 'Game information' },
            action: null,
            children: [
              {
                kind: 'column',
                props: { label: 'Events' },
                action: null,
                children: [{ kind: 'text', props: { text: 'Latest event' }, children: [], action: null }],
              },
              {
                kind: 'column',
                props: { label: 'Terms' },
                action: null,
                children: Array.from({ length: 60 }, (_, i) => ({
                  kind: 'text',
                  props: { text: `Term definition ${i + 1}` },
                  children: [],
                  action: null,
                })),
              },
            ],
          },
        ],
      };
      win.renderer.render(win.lastGame);
    });

    const requests: string[] = [];
    page.on('request', (request) => {
      requests.push(request.url());
    });

    const events = page.getByRole('tab', { name: 'Events', exact: true });
    const terms = page.getByRole('tab', { name: 'Terms', exact: true });
    await expect(events).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('tabpanel', { name: 'Events' })).toBeVisible();
    await expect(page.getByRole('tabpanel', { name: 'Terms', includeHidden: true })).toBeHidden();

    const originalBounds = await page.locator('.game-panel').boundingBox();
    expect(originalBounds).not.toBeNull();

    await events.focus();
    await events.press('ArrowLeft');
    await expect(terms).toBeFocused();
    await expect(terms).toHaveAttribute('tabindex', '0');
    await expect(events).toHaveAttribute('tabindex', '-1');
    await expect(page.getByRole('tabpanel', { name: 'Terms' })).toBeVisible();

    const helpBounds = await page.locator('.game-panel').boundingBox();
    expect(helpBounds).not.toBeNull();
    for (const dim of ['x', 'y', 'width', 'height'] as const) {
      expect(Math.abs(helpBounds![dim] - originalBounds![dim])).toBeLessThan(1);
    }

    const helpPanel = page.getByRole('tabpanel', { name: 'Terms' });
    expect(await helpPanel.evaluate((el: HTMLElement) => el.scrollHeight > el.clientHeight)).toBe(
      true,
    );
    await helpPanel.focus();
    await helpPanel.press('PageDown');
    const panelId = await helpPanel.getAttribute('id');
    await page.waitForFunction(
      (id: string | null) => (id ? (document.getElementById(id)?.scrollTop ?? 0) > 0 : false),
      panelId,
    );

    await terms.press('Home');
    await expect(events).toBeFocused();
    await events.press('End');
    await expect(terms).toBeFocused();
    await terms.press('ArrowRight');
    await expect(events).toBeFocused();

    await terms.click();
    await page.evaluate(() => {
      const win = window as unknown as RendererTestWindow;
      win.renderer.render({ ...win.lastGame, revision: 1 });
    });
    await expect(terms).toHaveAttribute('aria-selected', 'true');

    const toggle = page.getByRole('button', { name: 'Toggle game' });
    await toggle.click();
    await toggle.click();
    await expect(terms).toHaveAttribute('aria-selected', 'true');

    await page.evaluate(() => {
      const win = window as unknown as RendererTestWindow;
      win.renderer.render({ ...win.lastGame, definition_hash: 'new-package' });
    });
    await expect(events).toHaveAttribute('aria-selected', 'true');
    expect(requests).toEqual([]);
  });
}

for (const change of ['revision', 'permission', 'package'] as const) {
  test(`exploration is discarded when authority changes (${change})`, async ({
    canvas,
    page,
  }) => {
    await setupMovableGamePage(canvas, page);
    await page.locator('[data-game-id="square-40"]').click();
    await page.locator('[data-game-id="square-33"]').click();
    await expect(page.getByRole('button', { name: 'Submit', exact: true })).toBeVisible();

    if (change === 'revision') {
      await page.evaluate(() => {
        const win = window as unknown as RendererTestWindow;
        win.renderer.render({ ...win.lastGame, revision: 1 });
      });
    } else if (change === 'permission') {
      await page.evaluate(() => {
        const win = window as unknown as RendererTestWindow;
        win.editable = false;
        win.renderer.render(win.lastGame);
      });
    } else {
      await page.evaluate(() => {
        const win = window as unknown as RendererTestWindow;
        win.renderer.render({ ...win.lastGame, definition_hash: 'new-package' });
      });
    }

    await expect(page.getByRole('button', { name: 'Submit', exact: true })).toHaveCount(0);
    await expect(page.locator('[data-game-id="status"]')).toHaveText('Red to move');
    await expect(page.getByRole('alert')).toContainText('Local exploration was discarded');
  });
}
