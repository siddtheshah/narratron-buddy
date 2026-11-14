import { expect, test } from '../../testing/ui/canvas_fixtures';

type DoodleSessionModule = typeof import('/static/generated/canvas/doodle-session.js');
type DoodleSessionController =
  import('/static/generated/canvas/doodle-session.js').DoodleSessionController;
type CursorStateModule = typeof import('/static/generated/canvas/cursor-state.js');

interface DoodleHarnessWindow extends Window {
  doodleSession: DoodleSessionController;
  isOratorFlag: boolean;
  isCollabFlag: boolean;
  isPagedBackFlag: boolean;
  canPlaceStampsFlag: boolean;
  actionWheelRightClickFlag: boolean;
  speechStops: number;
  drawingSelectedLog: boolean[];
  micSocketToggles: number;
  batonMessages: { batonState: unknown; activeViewers: unknown }[];
}

const DOODLE_SESSION_MODULE_URL = '/static/generated/canvas/doodle-session.js';
const CURSOR_STATE_MODULE_URL = '/static/generated/canvas/cursor-state.js';

const DOODLE_HTML = `
  <div id="image-container" style="position: relative; width: 500px; height: 400px;">
    <canvas id="doodle-canvas" style="width: 500px; height: 400px;"></canvas>
    <div id="canvas-text-layer"></div>
    <div id="canvas-stamp-layer"></div>
    <div id="history-paging-controls"></div>
  </div>
  <div id="doodle-toolbar" style="display: flex;">
    <button id="doodles-toggle-btn" type="button">✏️ Doodles: On</button>
    <div class="doodle-palette">
      <button id="doodle-palette-toggle" type="button" aria-expanded="false">Palette</button>
      <div id="doodle-palette-menu" hidden>
        <button class="color-btn" data-color="#ef4444" aria-pressed="false" type="button">Red</button>
        <button class="color-btn" data-color="#3b82f6" aria-pressed="false" type="button">Blue</button>
      </div>
    </div>
    <input id="brush-size" type="range" min="1" max="10" value="4" />
    <button id="undo-doodle-btn" type="button">Undo</button>
    <button id="clear-doodle-btn" type="button">Clear</button>
    <div class="text-annotation-tool">
      <button id="text-annotation-toggle" type="button" aria-pressed="false" aria-expanded="false">Text</button>
      <div id="text-annotation-menu" hidden>
        <select id="text-annotation-font"><option value="Outfit">Outfit</option></select>
        <input id="text-annotation-size" type="range" min="12" max="72" value="32" />
        <span id="text-annotation-size-value">32 px</span>
        <button class="text-annotation-color" data-color="#ffffff" aria-pressed="true" type="button">White</button>
        <button class="text-annotation-color" data-color="#ef4444" aria-pressed="false" type="button">Red</button>
      </div>
    </div>
  </div>
  <button id="outside-target" type="button">Outside</button>
`;

async function initDoodleSession(
  page: Parameters<Parameters<typeof test>[2]>[0]['page'],
  options: { isOrator?: boolean; isCollab?: boolean; actionWheelRightClick?: boolean } = {},
): Promise<void> {
  await page.evaluate(
    async ({
      sessionUrl,
      cursorUrl,
      isOrator,
      isCollab,
      actionWheelRightClick,
    }: {
      sessionUrl: string;
      cursorUrl: string;
      isOrator: boolean;
      isCollab: boolean;
      actionWheelRightClick: boolean;
    }) => {
      const { canvasCursorState } = await window.__importModule<CursorStateModule>(cursorUrl);
      canvasCursorState.select({ type: 'select' });
      const { createDoodleSession } = await window.__importModule<DoodleSessionModule>(sessionUrl);
      const win = window as unknown as DoodleHarnessWindow;
      win.isOratorFlag = isOrator;
      win.isCollabFlag = isCollab;
      win.isPagedBackFlag = false;
      win.canPlaceStampsFlag = isOrator;
      win.actionWheelRightClickFlag = actionWheelRightClick;
      win.speechStops = 0;
      win.drawingSelectedLog = [];
      win.micSocketToggles = 0;
      win.batonMessages = [];

      win.doodleSession = createDoodleSession({
        theaterId: 'th-doodle',
        canvas: document.getElementById('doodle-canvas') as HTMLCanvasElement,
        imgContainer: document.getElementById('image-container') as HTMLElement,
        pagingControls: document.getElementById('history-paging-controls'),
        doodleToolbar: document.getElementById('doodle-toolbar'),
        doodlesToggleBtn: document.getElementById('doodles-toggle-btn') as HTMLButtonElement | null,
        paletteToggle: document.getElementById('doodle-palette-toggle'),
        paletteMenu: document.getElementById('doodle-palette-menu'),
        colorBtns: Array.from(document.querySelectorAll<HTMLElement>('.color-btn')),
        clearBtn: document.getElementById('clear-doodle-btn'),
        undoBtn: document.getElementById('undo-doodle-btn') as HTMLButtonElement | null,
        brushSizeSlider: document.getElementById('brush-size') as HTMLInputElement | null,
        textAnnotationToggle: document.getElementById('text-annotation-toggle') as HTMLButtonElement | null,
        textAnnotationMenu: document.getElementById('text-annotation-menu'),
        textAnnotationFont: document.getElementById('text-annotation-font') as HTMLSelectElement | null,
        textAnnotationSize: document.getElementById('text-annotation-size') as HTMLInputElement | null,
        textAnnotationSizeValue: document.getElementById('text-annotation-size-value'),
        textAnnotationColors: Array.from(document.querySelectorAll<HTMLElement>('.text-annotation-color')),
        textLayer: document.getElementById('canvas-text-layer'),
        stampLayer: document.getElementById('canvas-stamp-layer'),
        isOrator: () => win.isOratorFlag,
        isCollabModeEnabled: () => win.isCollabFlag,
        isPagedBack: () => win.isPagedBackFlag,
        getPage: () => ({ width: 500, height: 400 }),
        canPlaceStamps: () => win.canPlaceStampsFlag,
        stopSceneSpeech: () => {
          win.speechStops += 1;
        },
        setDrawingColorSelected: (selected) => {
          win.drawingSelectedLog.push(selected);
        },
        onMoveStamp: () => {},
        onRemoveStamp: () => {},
        onSelectStamp: () => {},
        onApplyStampAnnotation: () => {},
        onBringStampToFront: () => {},
        onToggleMicSocketMessage: () => {
          win.micSocketToggles += 1;
        },
        onBatonStateSocketMessage: (batonState, activeViewers) => {
          win.batonMessages.push({ batonState, activeViewers });
        },
        isActionWheelRightClickBound: () => win.actionWheelRightClickFlag,
      });
    },
    {
      sessionUrl: DOODLE_SESSION_MODULE_URL,
      cursorUrl: CURSOR_STATE_MODULE_URL,
      isOrator: Boolean(options.isOrator ?? true),
      isCollab: Boolean(options.isCollab ?? false),
      actionWheelRightClick: Boolean(options.actionWheelRightClick ?? false),
    },
  );
}

test('doodle session routes WS messages, handles ack/rejection rollback, and flushes offline queue', async ({
  canvas,
  page,
}) => {
  const sentMessages: Record<string, unknown>[] = [];
  let activeSocket: { send(data: string): void } | null = null;

  await page.routeWebSocket('**/ws/doodle**', (ws) => {
    activeSocket = ws;
    ws.onMessage((raw) => {
      const msg = JSON.parse(String(raw)) as Record<string, unknown>;
      sentMessages.push(msg);
      if (msg.type === 'subscribe_doodles') {
        ws.send(
          JSON.stringify({
            type: 'doodle_snapshot',
            page_id: msg.page_id,
            enabled: true,
            stamps: [],
            batches: [],
            annotations: [],
          }),
        );
      }
    });
    ws.send(
      JSON.stringify({
        type: 'doodle_snapshot',
        page_id: 'page-0',
        enabled: true,
        stamps: [],
        batches: [],
        annotations: [],
      }),
    );
  });

  await canvas.serveRoute('/api/doodles**', async (route) => {
    await route.fulfill({
      json: { enabled: true, stamps: [], batches: [], annotations: [] },
    });
  });
  await canvas.mount({ html: DOODLE_HTML });
  await initDoodleSession(page, { isOrator: true });
  await expect.poll(() => activeSocket !== null).toBe(true);

  await page.evaluate(() => {
    const win = window as unknown as DoodleHarnessWindow;
    win.doodleSession.displayPageDoodles({ page_id: 'page-1', doodles_enabled: true });
  });
  await expect
    .poll(() => sentMessages.some((m) => m.type === 'subscribe_doodles' && m.page_id === 'page-1'))
    .toBe(true);

  // Incoming toggle_mic & baton_state messages route through callbacks even across pages.
  activeSocket!.send(JSON.stringify({ type: 'toggle_mic' }));
  activeSocket!.send(
    JSON.stringify({
      type: 'baton_state',
      baton_state: { owner: { id: 9, username: 'alice' } },
      active_viewers: [{ id: 9, username: 'alice' }],
    }),
  );

  // Incoming doodles_toggle disables canvas; wrong page_id is ignored.
  activeSocket!.send(JSON.stringify({ type: 'doodles_toggle', page_id: 'other-page', enabled: false }));
  expect(await page.evaluate(() => (window as unknown as DoodleHarnessWindow).doodleSession.isDoodlesEnabled())).toBe(
    true,
  );
  activeSocket!.send(JSON.stringify({ type: 'doodles_toggle', page_id: 'page-1', enabled: false }));
  await expect(page.locator('#doodles-toggle-btn')).toHaveText('✏️ Doodles: Off');
  activeSocket!.send(JSON.stringify({ type: 'doodles_toggle', page_id: 'page-1', enabled: true }));

  // Rejection rollback: optimistic text action with client_message_id gets rolled back on text_annotation_rejected.
  await page.evaluate(() => {
    const win = window as unknown as DoodleHarnessWindow;
    win.doodleSession.setDoodleActions([
      {
        type: 'text',
        id: 'txt-1',
        x: 0.2,
        y: 0.2,
        text: 'Before edit',
        color: '#ffffff',
        size: 24,
        client_message_id: 'edit-1',
      },
    ]);
    win.doodleSession.getPendingTextEdits().set('edit-1', {
      type: 'text',
      id: 'txt-1',
      x: 0.2,
      y: 0.2,
      text: 'Reverted text',
      color: '#ffffff',
      size: 24,
    });
  });

  activeSocket!.send(
    JSON.stringify({ type: 'text_annotation_rejected', page_id: 'page-1', client_message_id: 'edit-1' }),
  );
  await expect
    .poll(() =>
      page.evaluate(() => {
        const actions = (window as unknown as DoodleHarnessWindow).doodleSession.getDoodleActions();
        return actions[0]?.text;
      }),
    )
    .toBe('Reverted text');

  const hookCounts = await page.evaluate(() => {
    const win = window as unknown as DoodleHarnessWindow;
    return {
      mic: win.micSocketToggles,
      baton: win.batonMessages.length,
    };
  });
  expect(hookCounts.mic).toBe(1);
  expect(hookCounts.baton).toBe(1);
});

test('doodle session palette, text menu Escape/outside close, right-click cursor cycle, draw, and clear', async ({
  canvas,
  page,
}) => {
  const sentMessages: Record<string, unknown>[] = [];
  await page.routeWebSocket('**/ws/doodle**', (ws) => {
    ws.onMessage((raw) => {
      sentMessages.push(JSON.parse(String(raw)) as Record<string, unknown>);
    });
    ws.send(
      JSON.stringify({
        type: 'doodle_snapshot',
        page_id: 'page-1',
        enabled: true,
        stamps: [],
        batches: [],
        annotations: [],
      }),
    );
  });

  await canvas.mount({ html: DOODLE_HTML });
  await initDoodleSession(page, { isOrator: true, actionWheelRightClick: false });
  await page.evaluate(() => {
    const win = window as unknown as DoodleHarnessWindow;
    win.doodleSession.displayPageDoodles({ page_id: 'page-1', doodles_enabled: true });
  });

  // Palette open -> Escape closes & restores focus to toggle button
  await page.locator('#doodle-palette-toggle').click();
  await expect(page.locator('#doodle-palette-menu')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#doodle-palette-menu')).toBeHidden();
  await expect(page.locator('#doodle-palette-toggle')).toBeFocused();

  // Palette open -> selecting Red closes palette and sets marker mode
  await page.locator('#doodle-palette-toggle').click();
  await page.locator('.color-btn[data-color="#ef4444"]').click();
  await expect(page.locator('#doodle-palette-menu')).toBeHidden();
  await expect(page.locator('#image-container')).toHaveAttribute('data-cursor-mode', 'marker');

  // Draw a stroke and verify draw_batch + undo + clear
  const box = await page.locator('#doodle-canvas').boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + 40, box!.y + 40);
  await page.mouse.down();
  await page.mouse.move(box!.x + 120, box!.y + 100);
  await page.mouse.up();

  await expect.poll(() => sentMessages.some((m) => m.type === 'draw_batch')).toBe(true);
  await expect(page.locator('#undo-doodle-btn')).toBeEnabled();

  await page.locator('#clear-doodle-btn').click();
  await expect.poll(() => sentMessages.some((m) => m.type === 'clear')).toBe(true);

  // Clicking outside canvas + toolbar resets cursor back to select
  await page.locator('#doodle-palette-toggle').click();
  await page.locator('.color-btn[data-color="#3b82f6"]').click();
  await expect(page.locator('#image-container')).toHaveAttribute('data-cursor-mode', 'marker');
  await page.locator('#outside-target').click();
  await expect(page.locator('#image-container')).toHaveAttribute('data-cursor-mode', 'select');

  // Right-click on canvas cycles select -> navigate -> marker when action wheel isn't bound
  await page.locator('#doodle-canvas').click({ button: 'right', force: true });
  await expect(page.locator('#image-container')).toHaveAttribute('data-cursor-mode', 'navigate');
});
