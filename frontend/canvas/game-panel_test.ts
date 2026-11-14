import { expect, test } from '../../testing/ui/canvas_fixtures';
import { loadGameSnapshots } from '../../testing/ui/game_fixtures';
import type { GamePayload, GameRenderer } from './game-renderer.js';

type GamePanelModule = typeof import('./game-panel.js');
type GameRendererModule = typeof import('./game-renderer.js');

interface PanelTestWindow extends Window {
  editable: boolean;
  renderer: GameRenderer;
  compromised?: boolean;
  micMode?: string;
  micClicks?: number;
  micPressLog?: string[];
  setMicMode?: (mode: string) => void;
}

const PANEL_MODULE_URL = '/static/generated/canvas/game-panel.js';
const RENDERER_MODULE_URL = '/static/generated/canvas/game-renderer.js';
const HOTKEYS_MODULE_URL = '/static/generated/canvas/hotkey-controls.js';

const snapshots = loadGameSnapshots();

test('html panel binding, actions, permissions, sizing, and network isolation', async ({
  canvas,
  page,
}) => {
  canvas.allowPageErrors();
  let stateCount = 0;

  await canvas.serveRoute(
    (url) =>
      url.origin === canvas.origin && (url.pathname === '/latest' || url.pathname === '/action'),
    async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/latest') {
        await route.fulfill({ json: snapshots.panel_0 });
        return;
      }
      stateCount = 1;
      await route.fulfill({ json: snapshots.panel_1 });
    },
  );

  await canvas.mount({
    html: '<button id="toggle"></button><div id="root"></div>',
  });

  await page.evaluate(
    async ({ moduleUrl }) => {
      const { createGameRenderer } = await window.__importModule<GameRendererModule>(moduleUrl);
      const win = window as unknown as PanelTestWindow;
      win.editable = true;
      win.renderer = createGameRenderer({
        container: document.querySelector('#root') as HTMLElement,
        toggleButton: document.querySelector('#toggle') as HTMLElement,
        actionUrl: () => '/action',
        canEdit: () => win.editable,
        initiallyOpen: true,
        refresh: () => {},
      });
      const response = await fetch('/latest');
      win.renderer.render((await response.json()) as GamePayload);
    },
    { moduleUrl: RENDERER_MODULE_URL },
  );

  const unsafeConstant =
    '</script><script>parent.compromised = true</script><img src="https://evil.test/injected">';
  const frame = page.frameLocator('iframe.game-html-panel');
  await expect(frame.locator('p')).toHaveText('0');
  await expect(frame.locator('pre')).toHaveText(unsafeConstant);
  await expect(frame.getByRole('img', { name: 'Token' })).toBeVisible();
  await expect(frame.getByRole('img', { name: 'Token' })).toHaveJSProperty('naturalWidth', 32);
  expect(await page.evaluate(() => (window as unknown as PanelTestWindow).compromised === undefined)).toBe(
    true,
  );
  await expect(frame.getByRole('button', { name: 'Unknown action' })).toBeDisabled();
  const canAccessParent = await frame.locator('body').evaluate(() => {
    try {
      return parent.document.body !== null;
    } catch {
      return false;
    }
  });
  expect(canAccessParent).toBe(false);

  // Messages from any other window cannot trigger a named action.
  await page.evaluate(() => {
    window.postMessage({ type: 'game-panel-action', id: 'increment' }, '*');
  });
  await expect(frame.locator('p')).toHaveText('0');

  await frame.getByRole('button', { name: 'Add one' }).click();
  await expect(frame.locator('p')).toHaveText('1');
  await expect(frame.getByRole('img', { name: 'Token' })).toHaveJSProperty('naturalWidth', 32);
  expect(stateCount).toBe(1);

  await page.evaluate(() => {
    const win = window as unknown as PanelTestWindow;
    win.editable = false;
    win.renderer.render();
  });
  await expect(frame.getByRole('button', { name: 'Add one' })).toBeDisabled();

  // Default sizing: 510px panel width, 430px iframe height
  await page.setViewportSize({ width: 1280, height: 1000 });
  const panelBox = await page.locator('.game-panel').boundingBox();
  const frameBox = await page.locator('iframe.game-html-panel').boundingBox();
  expect(panelBox).not.toBeNull();
  expect(frameBox).not.toBeNull();
  expect(Math.round(panelBox!.width)).toBe(510);
  expect(Math.round(frameBox!.height)).toBe(430);

  // Author-controlled sizing within limits (620px width, 540px height)
  await page.evaluate(() => {
    const win = window as unknown as PanelTestWindow;
    win.renderer.render({
      definition_hash: 'custom-size',
      revision: 2,
      state: { count: 1 },
      panel_html:
        '<!doctype html><html><head><style>body { width: 620px; height: 540px; }</style></head><body><p data-panel-text="count"></p></body></html>',
      view: [{ kind: 'column', props: { count: 1 }, children: [], action: null }],
    });
  });
  await expect(frame.locator('p')).toHaveText('1');
  const customPanel = await page.locator('.game-panel').boundingBox();
  const customFrame = await page.locator('iframe.game-html-panel').boundingBox();
  expect(customPanel).not.toBeNull();
  expect(customFrame).not.toBeNull();
  expect(Math.round(customPanel!.width)).toBe(620);
  expect(Math.round(customFrame!.height)).toBe(540);

  // Extreme author sizes are clamped to host bounds (max 960x780, min 280x220)
  await page.evaluate(() => {
    const win = window as unknown as PanelTestWindow;
    win.renderer.render({
      definition_hash: 'oversized',
      revision: 3,
      state: { count: 1 },
      panel_html:
        '<!doctype html><html><head><style>body { width: 2400px; height: 2000px; }</style></head><body><p data-panel-text="count"></p></body></html>',
      view: [{ kind: 'column', props: { count: 1 }, children: [], action: null }],
    });
  });
  await expect(frame.locator('p')).toHaveText('1');
  const oversizedPanel = await page.locator('.game-panel').boundingBox();
  const oversizedFrame = await page.locator('iframe.game-html-panel').boundingBox();
  expect(oversizedPanel).not.toBeNull();
  expect(oversizedFrame).not.toBeNull();
  expect(Math.round(oversizedPanel!.width)).toBe(960);
  expect(Math.round(oversizedFrame!.height)).toBe(780);

  await page.evaluate(() => {
    const win = window as unknown as PanelTestWindow;
    win.renderer.render({
      definition_hash: 'undersized',
      revision: 4,
      state: { count: 1 },
      panel_html:
        '<!doctype html><html><head><style>body { width: 80px; height: 60px; }</style></head><body><p data-panel-text="count"></p></body></html>',
      view: [{ kind: 'column', props: { count: 1 }, children: [], action: null }],
    });
  });
  await expect(frame.locator('p')).toHaveText('1');
  const undersizedPanel = await page.locator('.game-panel').boundingBox();
  const undersizedFrame = await page.locator('iframe.game-html-panel').boundingBox();
  expect(undersizedPanel).not.toBeNull();
  expect(undersizedFrame).not.toBeNull();
  expect(Math.round(undersizedPanel!.width)).toBe(280);
  expect(Math.round(undersizedFrame!.height)).toBe(220);
  expect(canvas.pageErrors.filter((err) => !err.message.includes('serviceWorker'))).toEqual([]);
});

test('canvas hotkeys work after clicking game panel', async ({ canvas, page }) => {
  canvas.allowPageErrors();

  const panelPayload: GamePayload = {
    ...snapshots.panel_0,
    panel_html: `<!doctype html><html><body>
      <p id="status" data-panel-text="count"></p>
      <button data-panel-action="increment" data-panel-disabled="full">Add one</button>
      <button data-panel-action="unknown">Disabled action</button>
    </body></html>`,
  };

  await canvas.serveRoute(
    (url) =>
      url.origin === canvas.origin && (url.pathname === '/latest' || url.pathname === '/action'),
    async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/latest') {
        await route.fulfill({ json: panelPayload });
        return;
      }
      await route.fulfill({ json: snapshots.panel_1 });
    },
  );

  await canvas.mount({
    html: `
      <button id="agent-start-stop-btn" type="button">Banish</button>
      <button id="agent-connect-btn" type="button">Mic On</button>
      <button id="game-state-toggle-btn" type="button"></button>
      <div id="game-state-layer"></div>
      <div id="orator-command-overlay" aria-hidden="true">
        <form id="orator-command-form">
          <input id="orator-command-input" type="text">
          <span id="orator-command-dismiss-hotkey"></span>
          <span id="orator-command-status"></span>
        </form>
      </div>
    `,
    moduleScripts: [
      `
      import { createGameRenderer } from '${RENDERER_MODULE_URL}';
      import { createHotkeyControlsController } from '${HOTKEYS_MODULE_URL}';
      const agentStartStopBtn = document.getElementById('agent-start-stop-btn');
      const agentConnectBtn = document.getElementById('agent-connect-btn');
      window.micMode = 'toggle';
      let micMode = 'toggle';
      let micPressSource = null;
      window.micClicks = 0;
      window.micPressLog = [];
      agentConnectBtn.addEventListener('click', () => { window.micClicks += 1; });
      const beginMicPress = source => {
        micPressSource = source;
        window.micPressLog.push('begin:' + source);
      };
      const releaseMicPress = () => {
        micPressSource = null;
        window.micPressLog.push('release');
      };
      window.setMicMode = mode => { micMode = mode; window.micMode = mode; };
      createHotkeyControlsController({
        isCurrentOrator: () => true,
        getMicMode: () => micMode,
        getMicPressSource: () => micPressSource,
        beginMicPress,
        releaseMicPress,
        agentStartStopBtn,
        agentConnectBtn,
        oratorCommandOverlay: document.getElementById('orator-command-overlay'),
        oratorCommandForm: document.getElementById('orator-command-form'),
        oratorCommandInput: document.getElementById('orator-command-input'),
        oratorCommandStatus: document.getElementById('orator-command-status'),
        dismissHotkeyDisplay: document.getElementById('orator-command-dismiss-hotkey'),
        getTheaterId: () => 'test',
        stopSceneSpeech: () => {},
        onRebindStart: () => {},
      });
      const renderer = createGameRenderer({
        container: document.getElementById('game-state-layer'),
        toggleButton: document.getElementById('game-state-toggle-btn'),
        actionUrl: () => '/action',
        canEdit: () => true,
        initiallyOpen: true,
        refresh: () => {},
      });
      renderer.render(await (await fetch('/latest')).json());
      window.__hotkeysReady = true;
      `,
    ],
  });

  await page.waitForFunction(() => (window as unknown as { __hotkeysReady?: boolean }).__hotkeysReady === true);

  const frame = page.frameLocator('iframe.game-html-panel');
  await expect(frame.locator('#status')).toHaveText('0');

  // Clicking inside the sandboxed game panel moves focus into the iframe.
  await frame.locator('#status').click();
  expect(await page.evaluate(() => document.activeElement?.className)).toBe('game-html-panel');

  // Spoofed key messages from any other window must be ignored.
  await page.evaluate(() => {
    window.postMessage(
      { type: 'game-panel-key', eventType: 'keydown', key: ' ', code: 'Space', shiftKey: true },
      '*',
    );
  });
  expect(
    await page.evaluate(() =>
      document.getElementById('orator-command-overlay')?.classList.contains('active'),
    ),
  ).toBe(false);

  // Text hotkey (Shift+Space) still opens and focuses the Orator command input.
  await page.keyboard.press('Shift+Space');
  await page.waitForFunction(() =>
    document.getElementById('orator-command-overlay')?.classList.contains('active'),
  );
  await page.waitForFunction(() => document.activeElement?.id === 'orator-command-input');
  await page.keyboard.type('look around');
  await expect(page.locator('#orator-command-input')).toHaveValue('look around');
  await page.keyboard.press('Escape');
  await page.waitForFunction(
    () => !document.getElementById('orator-command-overlay')?.classList.contains('active'),
  );

  // Click inside the iframe again (a disabled button) and trigger the mic hotkey (Ctrl+Shift+[).
  await frame.getByRole('button', { name: 'Disabled action' }).click({ force: true });
  expect(await page.evaluate(() => document.activeElement?.className)).toBe('game-html-panel');
  await page.keyboard.press('Control+Shift+BracketLeft');
  await page.waitForFunction(() => (window as unknown as PanelTestWindow).micClicks === 1);

  // Push-to-talk keydown and keyup must both forward from the iframe.
  await page.evaluate(() => {
    (window as unknown as PanelTestWindow).setMicMode?.('push-to-talk');
  });
  await frame.locator('#status').click();
  await page.keyboard.down('Control');
  await page.keyboard.down('Shift');
  await page.keyboard.down('BracketLeft');
  await page.waitForFunction(() =>
    (window as unknown as PanelTestWindow).micPressLog?.includes('begin:hotkey'),
  );
  await page.keyboard.up('BracketLeft');
  await page.keyboard.up('Shift');
  await page.keyboard.up('Control');
  await page.waitForFunction(() =>
    (window as unknown as PanelTestWindow).micPressLog?.includes('release'),
  );
  expect(await page.evaluate(() => (window as unknown as PanelTestWindow).micPressLog)).toEqual([
    'begin:hotkey',
    'release',
  ]);
  expect(canvas.pageErrors.filter((err) => !err.message.includes('serviceWorker'))).toEqual([]);
});
