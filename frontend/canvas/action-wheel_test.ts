import fs from 'node:fs';
import path from 'node:path';
import type { Page, Route, WebSocketRoute } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  expect,
  TEMPLATES_DIR,
  test,
  type CanvasHarness,
} from '../../testing/ui/canvas_fixtures';
import type {
  ActionWheelAction,
  ActionWheelController,
  ActionWheelDirection,
} from './action-wheel.js';

type ActionWheelModule = typeof import('/static/generated/canvas/action-wheel.js');

interface WheelHarnessWindow extends Window {
  sent: ActionWheelAction[];
  isOrator: boolean;
  centerTriggered: number;
  wheelController: ActionWheelController;
}

const ACTION_WHEEL_MODULE_URL = '/static/generated/canvas/action-wheel.js';
const ACTION_WHEEL_CSS_URL = '/static/css/canvas-action-wheel.css';

const WHEEL_MARKUP = `
  <div id="orator-action-wheel" role="group" aria-label="Orator action wheel" hidden>
    <span class="wheel-action" data-primary data-direction="up">📌<br>Pin image</span>
    <span class="wheel-action" data-primary data-direction="upright">🖼<br>New image</span>
    <span class="wheel-action" data-primary data-direction="downright">♫<br>New music</span>
    <span class="wheel-action" data-primary data-direction="down">📌<br>Pin music</span>
    <span class="wheel-action" data-primary data-direction="downleft">⏮♫<br>Prev music</span>
    <span class="wheel-action" data-primary data-direction="upleft">⏮🖼<br>Prev image</span>
    <span class="wheel-action" data-secondary data-direction="up">📖<br>Update Story</span>
    <span class="wheel-action" data-secondary data-direction="down">🎛<br>Update UI</span>
    <span class="wheel-center">Cycle<br>cursor</span>
  </div>
  <button id="action-wheel-rebind"><span id="action-wheel-binding-label"></span></button>
  <button id="action-wheel-2-rebind"><span id="action-wheel-2-binding-label"></span></button>
  <div id="action-wheel-status" hidden></div>
  <input id="text" style="position:absolute;left:100px;top:100px;width:200px;height:30px;">
`;

const ONE_BY_ONE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=',
  'base64',
);

async function mountWheelPage(
  canvas: CanvasHarness,
  page: Page,
  options: { viewport?: { width: number; height: number } } = {},
): Promise<void> {
  await canvas.mount({
    viewport: options.viewport ?? { width: 800, height: 600 },
    stylesheets: [ACTION_WHEEL_CSS_URL],
    html: WHEEL_MARKUP,
    moduleScripts: [
      `
      import { initializeActionWheel } from '${ACTION_WHEEL_MODULE_URL}';
      window.sent = [];
      window.isOrator = true;
      window.centerTriggered = 0;
      window.wheelController = initializeActionWheel({
        isOrator: () => window.isOrator,
        sendAction: async (action) => { window.sent.push(action); },
        onCenterAction: () => { window.centerTriggered += 1; },
      });
      `,
    ],
  });
  await page.waitForFunction(() =>
    Boolean((window as unknown as WheelHarnessWindow).wheelController),
  );
}

async function waitForSentLength(page: Page, expectedLength: number): Promise<void> {
  await page.waitForFunction(
    (len: number) => (window as unknown as WheelHarnessWindow).sent.length === len,
    expectedLength,
  );
}

async function getSentActions(page: Page): Promise<ActionWheelAction[]> {
  return page.evaluate(() => (window as unknown as WheelHarnessWindow).sent);
}

async function getCenterTriggered(page: Page): Promise<number> {
  return page.evaluate(() => (window as unknown as WheelHarnessWindow).centerTriggered);
}

async function serveMockHistoryImages(
  serve: (
    urlOrPredicate: string | RegExp | ((url: URL) => boolean),
    handler: (route: Route) => Promise<void> | void,
  ) => Promise<unknown>,
): Promise<void> {
  await serve(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname.startsWith('/static/images/'),
    async (route: Route) => {
      await route.fulfill({ body: ONE_BY_ONE_PNG, contentType: 'image/png' });
    },
  );
}

const NATIVE_MOUSE_BUTTONS: ReadonlyArray<{
  button: 'middle' | 'right' | 'back' | 'forward';
  name: string;
  mask: number;
  index: number;
}> = [
  { button: 'middle', name: 'Middle', mask: 4, index: 1 },
  { button: 'right', name: 'Right', mask: 2, index: 2 },
  { button: 'back', name: 'Back', mask: 8, index: 3 },
  { button: 'forward', name: 'Forward', mask: 16, index: 4 },
];

for (const { button, name, mask, index } of NATIVE_MOUSE_BUTTONS) {
  test(`freeform native mouse rebinding supports ${button} (${name})`, async ({
    canvas,
    page,
  }) => {
    await mountWheelPage(canvas, page);
    const cdp = await page.context().newCDPSession(page);

    await page.click('#action-wheel-rebind');
    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mousePressed',
      button,
      buttons: mask,
      x: 400,
      y: 300,
      clickCount: 1,
    });
    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mouseReleased',
      button,
      buttons: 0,
      x: 400,
      y: 300,
      clickCount: 1,
    });

    await expect(page.locator('#action-wheel-binding-label')).toContainText(name);
    await expect(page.locator('#orator-action-wheel')).toBeHidden();
    const saved = await page.evaluate(() => {
      const raw = localStorage.getItem('narratron_action_wheel_binding');
      return raw ? (JSON.parse(raw) as { button?: number }) : null;
    });
    expect(saved?.button).toBe(index);

    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mousePressed',
      button,
      buttons: mask,
      x: 400,
      y: 300,
      clickCount: 1,
    });
    await expect(page.locator('#orator-action-wheel')).toBeVisible();
    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mouseMoved',
      button: 'none',
      buttons: mask,
      x: 340,
      y: 260,
    });
    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mouseReleased',
      button,
      buttons: 0,
      x: 340,
      y: 260,
      clickCount: 1,
    });

    await waitForSentLength(page, 1);
    expect(await getSentActions(page)).toEqual(['previous_image']);

    await page.reload();
    await page.waitForFunction(() =>
      Boolean((window as unknown as WheelHarnessWindow).wheelController),
    );
    await expect(page.locator('#action-wheel-binding-label')).toContainText(name);
  });
}

const CHORD_ORDERS: ReadonlyArray<{ first: 'left' | 'right'; second: 'left' | 'right' }> = [
  { first: 'left', second: 'right' },
  { first: 'right', second: 'left' },
];
const SECONDARY_TARGETS: ReadonlyArray<{ dy: number; action: ActionWheelAction }> = [
  { dy: -80, action: 'update_story' },
  { dy: 80, action: 'update_ui' },
];
const RELEASE_ORDERS: readonly boolean[] = [true, false];

for (const { first, second } of CHORD_ORDERS) {
  for (const { dy, action } of SECONDARY_TARGETS) {
    for (const releaseFirst of RELEASE_ORDERS) {
      test(`secondary wheel mouse chord (${first} then ${second}, dy=${dy}, releaseFirst=${releaseFirst}) triggers ${action}`, async ({
        canvas,
        page,
      }) => {
        await mountWheelPage(canvas, page);
        await page.mouse.move(400, 300);
        await page.mouse.down({ button: first });
        await page.mouse.down({ button: second });

        const wheel = page.locator('#orator-action-wheel');
        await expect(wheel).toHaveAttribute('data-mode', 'secondary');
        await expect(page.locator('[data-secondary][data-direction="up"]')).toBeVisible();
        await expect(page.locator('[data-primary][data-direction="up"]')).toBeHidden();

        await page.mouse.move(400, 300 + dy);
        await page.mouse.up({ button: releaseFirst ? first : second });
        await page.mouse.up({ button: releaseFirst ? second : first });

        await waitForSentLength(page, 1);
        expect(await getSentActions(page)).toEqual([action]);
        await expect(wheel).toBeHidden();
      });
    }
  }
}

test('secondary wheel cancels without firing primary', async ({ canvas, page }) => {
  await mountWheelPage(canvas, page);
  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'right' });
  await page.mouse.move(460, 260);
  await page.mouse.down({ button: 'left' });
  await page.mouse.up({ button: 'left' });
  await page.mouse.up({ button: 'right' });
  expect(await getSentActions(page)).toEqual([]);

  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'left' });
  await page.mouse.down({ button: 'right' });
  await page.mouse.move(400, 220);
  await page.keyboard.press('Escape');
  await page.mouse.up({ button: 'right' });
  await page.mouse.up({ button: 'left' });
  expect(await getSentActions(page)).toEqual([]);
});

test('secondary wheel requires orator', async ({ canvas, page }) => {
  await mountWheelPage(canvas, page);
  await page.evaluate(() => {
    (window as unknown as WheelHarnessWindow).isOrator = false;
  });

  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'left' });
  await page.mouse.down({ button: 'right' });
  await expect(page.locator('#orator-action-wheel')).toBeHidden();
  await page.mouse.move(400, 220);
  await page.mouse.up({ button: 'right' });
  await page.mouse.up({ button: 'left' });
  expect(await getSentActions(page)).toEqual([]);
});

test('secondary wheel rebinds mouse and persists', async ({ canvas, page }) => {
  await mountWheelPage(canvas, page);
  await page.click('#action-wheel-2-rebind');
  await page.mouse.click(400, 300, { button: 'middle' });
  await expect(page.locator('#action-wheel-2-binding-label')).toContainText('Middle mouse');
  await expect(page.locator('#action-wheel-binding-label')).toContainText('Right mouse');

  await page.reload();
  await page.waitForFunction(() =>
    Boolean((window as unknown as WheelHarnessWindow).wheelController),
  );
  await expect(page.locator('#action-wheel-2-binding-label')).toContainText('Middle mouse');

  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'middle' });
  await expect(page.locator('#orator-action-wheel')).toHaveAttribute('data-mode', 'secondary');
  await page.mouse.move(400, 220);
  await page.mouse.up({ button: 'middle' });
  await waitForSentLength(page, 1);
  expect(await getSentActions(page)).toEqual(['update_story']);
});

test('secondary wheel rebinds keyboard combo', async ({ canvas, page }) => {
  await mountWheelPage(canvas, page);
  await page.click('#action-wheel-2-rebind');
  await page.keyboard.press('Control+Shift+U');
  await expect(page.locator('#action-wheel-2-binding-label')).toContainText('Ctrl + Shift + U');

  await page.mouse.move(400, 300);
  await page.keyboard.down('Control');
  await page.keyboard.down('Shift');
  await page.keyboard.down('U');
  await expect(page.locator('#orator-action-wheel')).toHaveAttribute('data-mode', 'secondary');
  await page.mouse.move(400, 380);
  await page.keyboard.up('U');
  await page.keyboard.up('Shift');
  await page.keyboard.up('Control');
  await waitForSentLength(page, 1);
  expect(await getSentActions(page)).toEqual(['update_ui']);

  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'left' });
  await page.mouse.down({ button: 'right' });
  await expect(page.locator('#orator-action-wheel')).toHaveAttribute('data-mode', 'primary');
  await page.mouse.up({ button: 'right' });
  await page.mouse.up({ button: 'left' });
  expect(await getSentActions(page)).toEqual(['update_ui']);
});

for (const { first, second } of CHORD_ORDERS) {
  test(`secondary wheel can rebind to default chord (${first} then ${second})`, async ({
    canvas,
    page,
  }) => {
    await mountWheelPage(canvas, page);
    await page.click('#action-wheel-2-rebind');
    await page.mouse.move(400, 300);
    await page.mouse.down({ button: first });
    await page.mouse.down({ button: second });
    await page.mouse.up({ button: first });
    await page.mouse.up({ button: second });

    await expect(page.locator('#action-wheel-2-binding-label')).toContainText(
      'Left + Right mouse',
    );
    const savedType = await page.evaluate(() => {
      const raw = localStorage.getItem('narratron_action_wheel_2_binding');
      return raw ? (JSON.parse(raw) as { type?: string }).type : null;
    });
    expect(savedType).toBe('chord');

    await page.mouse.down({ button: first });
    await page.mouse.down({ button: second });
    await page.mouse.move(400, 220);
    await page.mouse.up({ button: second });
    await page.mouse.up({ button: first });
    await waitForSentLength(page, 1);
    expect(await getSentActions(page)).toEqual(['update_story']);
  });
}

test('secondary rebinding cancel and conflict', async ({ canvas, page }) => {
  await mountWheelPage(canvas, page);
  await page.click('#action-wheel-2-rebind');
  await page.mouse.click(400, 300, { button: 'right' });
  await expect(page.locator('#action-wheel-2-binding-label')).toContainText('Already used');
  await page.keyboard.press('Escape');
  await expect(page.locator('#action-wheel-2-binding-label')).toContainText('Left + Right mouse');

  await page.click('#action-wheel-2-rebind');
  await page.mouse.click(400, 300, { button: 'left' });
  await expect(page.locator('#action-wheel-2-binding-label')).toContainText('not left click');
  await page.keyboard.press('Escape');
  expect(
    await page.evaluate(() => localStorage.getItem('narratron_action_wheel_2_binding')),
  ).toBeNull();
});

test('left click cannot be bound', async ({ canvas, page }) => {
  await mountWheelPage(canvas, page);
  await page.click('#action-wheel-rebind');
  await page.mouse.click(400, 300, { button: 'left' });
  await expect(page.locator('#action-wheel-binding-label')).toContainText('not left');
  expect(
    await page.evaluate(() => localStorage.getItem('narratron_action_wheel_binding')),
  ).toBeNull();

  await page.mouse.click(400, 300, { button: 'middle' });
  await expect(page.locator('#action-wheel-binding-label')).toContainText('Middle mouse');

  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'left' });
  await expect(page.locator('#orator-action-wheel')).toBeHidden();
  await page.mouse.up({ button: 'left' });
});

test('saved left click binding falls back to right', async ({ canvas, page }) => {
  await mountWheelPage(canvas, page);
  await page.evaluate(() => {
    localStorage.setItem(
      'narratron_action_wheel_binding',
      JSON.stringify({
        type: 'mouse',
        button: 0,
        ctrlKey: false,
        altKey: false,
        shiftKey: false,
        metaKey: false,
      }),
    );
  });
  await page.reload();
  await page.waitForFunction(() =>
    Boolean((window as unknown as WheelHarnessWindow).wheelController),
  );
  await expect(page.locator('#action-wheel-binding-label')).toContainText(
    'Action Wheel: Right mouse',
  );
});

test('keyboard combo rebinding and cancel', async ({ canvas, page }) => {
  await mountWheelPage(canvas, page);
  await page.click('#action-wheel-rebind');
  await page.keyboard.press('Control+Shift+K');
  await expect(page.locator('#action-wheel-binding-label')).toContainText('Ctrl + Shift + K');

  await page.mouse.move(400, 300);
  await page.keyboard.down('k');
  await expect(page.locator('#orator-action-wheel')).toBeHidden();
  await page.keyboard.up('k');

  await page.keyboard.down('Control');
  await page.keyboard.down('Shift');
  await page.keyboard.down('K');
  await expect(page.locator('#orator-action-wheel')).toBeVisible();
  await page.mouse.move(460, 260);
  await page.keyboard.up('K');
  await page.keyboard.up('Shift');
  await page.keyboard.up('Control');
  await waitForSentLength(page, 1);
  expect(await getSentActions(page)).toEqual(['new_image']);

  await page.click('#action-wheel-rebind');
  await page.keyboard.press('Escape');
  await expect(page.locator('#action-wheel-binding-label')).toContainText('Ctrl + Shift + K');
});

test('rebound button works in mouse chord', async ({ canvas, page }) => {
  await mountWheelPage(canvas, page);
  await page.click('#action-wheel-rebind');
  await page.mouse.click(400, 300, { button: 'middle' });

  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'left' });
  await page.mouse.down({ button: 'middle' });
  await expect(page.locator('#orator-action-wheel')).toBeVisible();
  await page.mouse.move(340, 260);
  await page.mouse.up({ button: 'middle' });
  await page.mouse.up({ button: 'left' });
  await waitForSentLength(page, 1);
  expect(await getSentActions(page)).toEqual(['previous_image']);
});

test('mouse modifiers and focus cancellation', async ({ canvas, page }) => {
  await mountWheelPage(canvas, page);
  await page.click('#action-wheel-rebind');
  await page.keyboard.down('Control');
  await page.mouse.click(400, 300, { button: 'middle' });
  await page.keyboard.up('Control');
  await expect(page.locator('#action-wheel-binding-label')).toContainText('Ctrl + Middle mouse');

  await page.mouse.down({ button: 'middle' });
  await expect(page.locator('#orator-action-wheel')).toBeHidden();
  await page.mouse.up({ button: 'middle' });

  await page.keyboard.down('Control');
  await page.mouse.down({ button: 'middle' });
  await expect(page.locator('#orator-action-wheel')).toBeVisible();
  await page.mouse.move(340, 260);
  await page.evaluate(() => {
    window.dispatchEvent(new Event('blur'));
  });
  await page.mouse.up({ button: 'middle' });
  await page.keyboard.up('Control');
  expect(await getSentActions(page)).toEqual([]);
});

test('action wheel initializes on full canvas', async ({ canvas, page }) => {
  const actions: string[] = [];
  await canvas.openCanvas({
    viewport: { width: 1500, height: 900 },
    apiRoutes: {
      '/api/theaters/stage/orator-action': async (route: Route) => {
        const body = JSON.parse(route.request().postData() || '{}') as { action?: string };
        if (body.action) actions.push(body.action);
        await route.fulfill({ json: { status: 'accepted', pinned: false, music_pinned: false } });
      },
    },
  });

  // Default must be right click
  await expect(page.locator('#action-wheel-binding-label')).toContainText(
    'Action Wheel: Right mouse',
  );

  // Right click over header must NOT open wheel
  await page.mouse.move(600, 20);
  await page.mouse.down({ button: 'right' });
  await expect(page.locator('#orator-action-wheel')).toBeHidden();
  await page.mouse.up({ button: 'right' });

  // Right click over chat sidebar must NOT open wheel
  await page.mouse.move(1350, 400);
  await page.mouse.down({ button: 'right' });
  await expect(page.locator('#orator-action-wheel')).toBeHidden();
  await page.mouse.up({ button: 'right' });

  // Right click over canvas DOES open wheel
  await page.mouse.move(600, 400);
  await page.mouse.down({ button: 'right' });
  await expect(page.locator('#orator-action-wheel')).toBeVisible();
  await page.mouse.move(660, 365);
  await page.mouse.up({ button: 'right' });
  await page.waitForFunction(() =>
    (document.getElementById('action-wheel-status')?.textContent ?? '').includes('applied'),
  );
  expect(actions).toEqual(['new_image']);

  await page.locator('#menu-item-mic-config').evaluate((el) => (el as HTMLElement).click());
  await page.click('#action-wheel-rebind');
  await page.keyboard.press('Control+Shift+K');
  await expect(page.locator('#action-wheel-binding-label')).toContainText('Ctrl + Shift + K');
  await page.click('#mic-config-done-btn');

  // Keyboard combo over chat sidebar must NOT open wheel
  await page.mouse.move(1350, 400);
  await page.keyboard.down('Control');
  await page.keyboard.down('Shift');
  await page.keyboard.down('K');
  await expect(page.locator('#orator-action-wheel')).toBeHidden();
  await page.keyboard.up('K');
  await page.keyboard.up('Shift');
  await page.keyboard.up('Control');

  // Keyboard combo over canvas DOES open wheel
  await page.mouse.move(600, 400);
  await page.keyboard.down('Control');
  await page.keyboard.down('Shift');
  await page.keyboard.down('K');
  await expect(page.locator('#orator-action-wheel')).toBeVisible();
  await page.mouse.move(660, 435);
  await page.keyboard.up('K');
  await page.keyboard.up('Shift');
  await page.keyboard.up('Control');
  await page.waitForFunction(
    () => document.getElementById('action-wheel-status')?.textContent === 'New music applied',
  );
  expect(actions).toEqual(['new_image', 'new_music']);
});

test('action wheel in browser covers all directions, clamping, exclusions, and rebinding', async ({
  canvas,
  page,
}) => {
  await mountWheelPage(canvas, page);

  const expectedActions: ReadonlyArray<{
    dx: number;
    dy: number;
    direction: ActionWheelDirection;
    action: ActionWheelAction;
  }> = [
    { dx: 0, dy: -70, direction: 'up', action: 'toggle_canvas_pin' },
    { dx: 60, dy: -35, direction: 'upright', action: 'new_image' },
    { dx: 60, dy: 35, direction: 'downright', action: 'new_music' },
    { dx: 0, dy: 70, direction: 'down', action: 'toggle_music_pin' },
    { dx: -60, dy: 35, direction: 'downleft', action: 'previous_music' },
    { dx: -60, dy: -35, direction: 'upleft', action: 'previous_image' },
  ];

  for (const { dx, dy, direction, action } of expectedActions) {
    await page.mouse.move(400, 300);
    await page.mouse.down({ button: 'right' });
    await expect(page.locator('#orator-action-wheel')).toBeVisible();
    expect(
      await page.locator('#orator-action-wheel').evaluate((el) => (el as HTMLElement).style.left),
    ).toBe('400px');
    await page.mouse.move(400 + dx, 300 + dy);
    expect(
      await page
        .locator(`[data-primary][data-direction="${direction}"]`)
        .evaluate((el) => el.classList.contains('selected')),
    ).toBe(true);
    await page.mouse.up({ button: 'right' });
    await expect(page.locator('#orator-action-wheel')).toBeHidden();
    await page.waitForFunction(
      (expected: string) => (window as unknown as WheelHarnessWindow).sent.at(-1) === expected,
      action,
    );
  }

  expect(await getSentActions(page)).toEqual([
    'toggle_canvas_pin',
    'new_image',
    'new_music',
    'toggle_music_pin',
    'previous_music',
    'previous_image',
  ]);

  await page.evaluate(() => {
    (window as unknown as WheelHarnessWindow).wheelController.updateState(true, true);
  });
  await expect(page.locator('[data-primary][data-direction="up"]')).toContainText('Unpin image');
  await expect(page.locator('[data-primary][data-direction="down"]')).toContainText('Unpin music');

  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'right' });
  await page.mouse.move(400, 220);
  await page.keyboard.press('Escape');
  await expect(page.locator('#orator-action-wheel')).toBeHidden();
  await page.mouse.up({ button: 'right' });

  // Tiny drags, viewport edges, viewer access, and text input must not send.
  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'right' });
  await page.mouse.move(404, 304);
  await page.mouse.up({ button: 'right' });

  await page.mouse.move(5, 580);
  await page.mouse.down({ button: 'right' });
  const bounds = await page.locator('#orator-action-wheel').boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(800);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(600);
  await page.mouse.up({ button: 'right' });

  await page.evaluate(() => {
    (window as unknown as WheelHarnessWindow).isOrator = false;
  });
  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'right' });
  await page.mouse.move(480, 300);
  await page.mouse.up({ button: 'right' });

  await page.evaluate(() => {
    (window as unknown as WheelHarnessWindow).isOrator = true;
  });
  await page.mouse.move(130, 115);
  await page.mouse.down({ button: 'right' });
  await page.mouse.move(210, 115);
  await page.mouse.up({ button: 'right' });

  await page.click('#action-wheel-rebind');
  await page.mouse.click(400, 300, { button: 'middle' });
  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'middle' });
  await page.mouse.move(460, 260);
  await page.mouse.up({ button: 'middle' });
  await waitForSentLength(page, 7);
  await expect(page.locator('[data-primary][data-direction="up"]')).toContainText('Unpin image');

  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'middle' });
  await page.mouse.move(460, 340);
  await page.mouse.up({ button: 'middle' });
  await waitForSentLength(page, 8);
  await expect(page.locator('[data-primary][data-direction="down"]')).toContainText('Unpin music');

  expect(await getSentActions(page)).toEqual([
    'toggle_canvas_pin',
    'new_image',
    'new_music',
    'toggle_music_pin',
    'previous_music',
    'previous_image',
    'new_image',
    'new_music',
  ]);
});

test('previous_image pages orator and sync button ends navigational state', async ({
  canvas,
  page,
}) => {
  const actions: string[] = [];
  let currentCursor = 1;
  const history = [
    { url: '/static/images/p1.png', prompt: 'Image 1', time: 1.0 },
    { url: '/static/images/p2.png', prompt: 'Image 2', time: 2.0 },
  ];

  await serveMockHistoryImages((predicate, handler) => canvas.serveRoute(predicate, handler));
  await canvas.openCanvas({
    viewport: { width: 1500, height: 900 },
    apiRoutes: {
      '/api/latest': async (route: Route) => {
        await route.fulfill({
          json: {
            latest: '/static/images/p1.png',
            time: 2.0,
            history,
            orator_cursor: currentCursor,
          },
        });
      },
      '/api/theaters/stage/orator-action': async (route: Route) => {
        const body = JSON.parse(route.request().postData() || '{}') as { action?: string };
        const actionName = String(body.action ?? '');
        actions.push(actionName);
        if (actionName === 'previous_image') {
          currentCursor = Math.max(0, currentCursor - 1);
        } else if (actionName === 'next_image') {
          currentCursor = Math.min(1, currentCursor + 1);
        }
        await route.fulfill({
          json: {
            status: 'accepted',
            pinned: false,
            music_pinned: false,
            orator_cursor: currentCursor,
          },
        });
      },
    },
  });

  await page.waitForFunction(
    () => document.getElementById('page-indicator')?.textContent?.trim() === '2 / 2',
  );

  // Sync button replaces Head and is hidden for orator
  const syncButton = page.locator('#page-head-btn');
  await expect(syncButton).toContainText('Sync');
  await expect(syncButton).toBeHidden();

  // Trigger previous image on action wheel (upleft: dx=-60, dy=-35)
  await page.mouse.move(600, 400);
  await page.mouse.down({ button: 'right' });
  await expect(page.locator('#orator-action-wheel')).toBeVisible();
  await page.mouse.move(540, 365);
  await page.mouse.up({ button: 'right' });
  await page.waitForFunction(() =>
    (document.getElementById('action-wheel-status')?.textContent ?? '').includes('applied'),
  );
  expect(actions).toEqual(['previous_image']);

  // The orator should be paged back simultaneously to 1 / 2
  await expect(page.locator('#page-indicator')).toHaveText('1 / 2');
  await expect(syncButton).toBeHidden();

  // Orator's next button is enabled because orator is at 1 / 2
  const nextButton = page.locator('#page-next-btn');
  await expect(nextButton).toBeEnabled();

  // Connect a viewer in a second isolated context
  const viewerContext = await canvas.createIsolatedContext({
    viewport: { width: 1500, height: 900 },
  });
  const viewerPage = await viewerContext.newPage();
  const viewerErrors: Error[] = [];
  viewerPage.on('pageerror', (err: Error) => {
    viewerErrors.push(err);
  });

  await viewerPage.addInitScript(() => {
    localStorage.setItem('narratron_viewer_collab_guide_seen:stage', 'true');
    localStorage.setItem('narratron_orator_howto_seen', 'true');
  });
  const templateHtml = await fs.promises.readFile(path.join(TEMPLATES_DIR, 'canvas.html'), 'utf-8');
  await viewerPage.route(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/canvas',
    async (route: Route) => {
      await route.fulfill({ body: templateHtml, contentType: 'text/html; charset=utf-8' });
    },
  );
  await viewerPage.route('https://fonts.googleapis.com/**', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'text/css; charset=utf-8', body: '' });
  });
  await viewerPage.route(`${CANVAS_ORIGIN}/favicon.png`, async (route: Route) => {
    await route.fulfill({ status: 204 });
  });
  await serveMockHistoryImages((predicate, handler) => viewerPage.route(predicate, handler));
  await viewerPage.route(
    (url: URL) =>
      url.origin === CANVAS_ORIGIN &&
      (url.pathname.startsWith('/api/') || url.pathname.startsWith('/theaters/stage/')),
    async (route: Route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/auth/me') {
        await route.fulfill({ json: { authenticated: true, user: { id: 2, username: 'viewer' } } });
      } else if (url.pathname === '/api/theaters/stage') {
        await route.fulfill({ json: { metadata: { is_owner: false, is_active_orator: false } } });
      } else if (url.pathname === '/api/theaters/stage/baton') {
        await route.fulfill({ json: { owner: { id: 1 }, active_orator: { id: 1 } } });
      } else if (url.pathname === '/api/latest') {
        await route.fulfill({
          json: {
            latest: '/static/images/p1.png',
            time: 2.0,
            history,
            orator_cursor: currentCursor,
          },
        });
      } else {
        await route.fulfill({ json: {} });
      }
    },
  );
  await viewerPage.routeWebSocket(/wss?:\/\/canvas\.test\/ws\/.*/, (_ws: WebSocketRoute) => {
    // Keep WebSocket local for the viewer page.
  });

  await viewerPage.goto(`${CANVAS_ORIGIN}/canvas?theater_id=stage`);
  await viewerPage.waitForFunction(() => window._isActiveOratorState === false, null, {
    timeout: 5000,
  });
  // Viewer starts synced to orator cursor (1 / 2)
  await viewerPage.waitForFunction(
    () => document.getElementById('page-indicator')?.textContent?.trim() === '1 / 2',
  );
  const viewerSync = viewerPage.locator('#page-head-btn');
  await expect(viewerSync).toBeHidden();

  // Viewer can navigate forward and enter navigational state
  await viewerPage.locator('#page-next-btn').click();
  await expect(viewerPage.locator('#page-indicator')).toHaveText('2 / 2');
  await expect(viewerSync).toBeVisible();

  // Viewer clicks 'Sync' to end navigational state and return to orator cursor (1 / 2)
  await viewerSync.click();
  await expect(viewerPage.locator('#page-indicator')).toHaveText('1 / 2');
  await expect(viewerSync).toBeHidden();

  // Orator clicks next button to return to head (2 / 2)
  await nextButton.click();
  await page.waitForFunction(
    () => document.getElementById('page-indicator')?.textContent?.trim() === '2 / 2',
  );
  expect(actions).toEqual(['previous_image', 'next_image']);
  await expect(nextButton).toBeDisabled();
  await expect(syncButton).toBeHidden();
  expect(viewerErrors).toEqual([]);
});

test('orator page bar previous moves cursor and has no sync', async ({ canvas, page }) => {
  const actions: string[] = [];
  let currentCursor = 2;

  await serveMockHistoryImages((predicate, handler) => canvas.serveRoute(predicate, handler));
  await canvas.openCanvas({
    viewport: { width: 1500, height: 900 },
    apiRoutes: {
      '/api/latest': async (route: Route) => {
        await route.fulfill({
          json: {
            latest: '/static/images/p3.png',
            time: 3.0,
            history: [
              { url: '/static/images/p1.png', prompt: 'Image 1', time: 1.0 },
              { url: '/static/images/p2.png', prompt: 'Image 2', time: 2.0 },
              { url: '/static/images/p3.png', prompt: 'Image 3', time: 3.0 },
            ],
            orator_cursor: currentCursor,
          },
        });
      },
      '/api/theaters/stage/orator-action': async (route: Route) => {
        const body = JSON.parse(route.request().postData() || '{}') as { action?: string };
        const actionName = String(body.action ?? '');
        actions.push(actionName);
        if (actionName === 'previous_image') {
          currentCursor = Math.max(0, currentCursor - 1);
        } else if (actionName === 'next_image') {
          currentCursor = Math.min(2, currentCursor + 1);
        }
        await route.fulfill({
          json: {
            status: 'accepted',
            pinned: false,
            music_pinned: false,
            orator_cursor: currentCursor,
          },
        });
      },
    },
  });

  await page.waitForFunction(
    () => document.getElementById('page-indicator')?.textContent?.trim() === '3 / 3',
  );

  const syncButton = page.locator('#page-head-btn');
  await expect(syncButton).toBeHidden();

  const nextButton = page.locator('#page-next-btn');
  await expect(nextButton).toBeDisabled();

  // Orator clicks Previous Image button in the page bar
  await page.locator('#page-prev-btn').click();
  await page.waitForFunction(
    () => document.getElementById('page-indicator')?.textContent?.trim() === '2 / 3',
  );
  expect(actions).toEqual(['previous_image']);
  await expect(syncButton).toBeHidden();

  // At 2 / 3, next button is enabled; clicking it advances back to 3 / 3
  await expect(nextButton).toBeEnabled();
  await nextButton.click();
  await page.waitForFunction(
    () => document.getElementById('page-indicator')?.textContent?.trim() === '3 / 3',
  );
  expect(actions).toEqual(['previous_image', 'next_image']);
  await expect(nextButton).toBeDisabled();
  await expect(syncButton).toBeHidden();
});

test('action wheel center invokes center action', async ({ canvas, page }) => {
  await mountWheelPage(canvas, page);
  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'right' });
  const wheel = page.locator('#orator-action-wheel');
  await expect(wheel).toBeVisible();
  await expect(wheel.locator('.wheel-center')).toContainText('Cycle');

  // Release in center
  await page.mouse.up({ button: 'right' });
  await expect(wheel).toBeHidden();
  expect(await getCenterTriggered(page)).toBe(1);
  expect(await getSentActions(page)).toEqual([]);

  // Dragging to action does not invoke center action
  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'right' });
  await page.mouse.move(400, 220);
  await page.mouse.up({ button: 'right' });
  await waitForSentLength(page, 1);
  expect(await getSentActions(page)).toEqual(['toggle_canvas_pin']);
  expect(await getCenterTriggered(page)).toBe(1);
});

test('secondary action wheel center does not invoke center action', async ({ canvas, page }) => {
  await mountWheelPage(canvas, page);
  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'left' });
  await page.mouse.down({ button: 'right' });
  const wheel = page.locator('#orator-action-wheel');
  await expect(wheel).toBeVisible();
  await expect(wheel).toHaveAttribute('data-mode', 'secondary');
  await expect(wheel.locator('.wheel-center')).toContainText('cancel');

  await page.mouse.up({ button: 'right' });
  await page.mouse.up({ button: 'left' });
  await expect(wheel).toBeHidden();
  expect(await getCenterTriggered(page)).toBe(0);
  expect(await getSentActions(page)).toEqual([]);
});

test('left plus right click in center opens action wheel 2', async ({ canvas, page }) => {
  await mountWheelPage(canvas, page);

  // Right click down opens action wheel 1
  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'right' });
  const wheel = page.locator('#orator-action-wheel');
  await expect(wheel).toBeVisible();
  await expect(wheel).toHaveAttribute('data-mode', 'primary');

  // While holding right click, pressing left click in the center opens action wheel 2
  await page.mouse.down({ button: 'left' });
  await expect(wheel).toBeVisible();
  await expect(wheel).toHaveAttribute('data-mode', 'secondary');
  await expect(page.locator('[data-secondary][data-direction="up"]')).toBeVisible();
  await expect(page.locator('[data-primary][data-direction="up"]')).toBeHidden();

  // Releasing both in center cancels secondary wheel without firing center action
  await page.mouse.up({ button: 'left' });
  await page.mouse.up({ button: 'right' });
  await expect(wheel).toBeHidden();
  expect(await getCenterTriggered(page)).toBe(0);
  expect(await getSentActions(page)).toEqual([]);

  // Also test left down first, then right down in center opens action wheel 2
  await page.mouse.move(400, 300);
  await page.mouse.down({ button: 'left' });
  await page.mouse.down({ button: 'right' });
  await expect(wheel).toBeVisible();
  await expect(wheel).toHaveAttribute('data-mode', 'secondary');
  await page.mouse.move(400, 220);
  await page.mouse.up({ button: 'right' });
  await page.mouse.up({ button: 'left' });
  await waitForSentLength(page, 1);
  expect(await getSentActions(page)).toEqual(['update_story']);
  expect(await getCenterTriggered(page)).toBe(0);
});
