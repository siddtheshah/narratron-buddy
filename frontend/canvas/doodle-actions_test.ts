import type { Page, WebSocketRoute } from '@playwright/test';
import { expect, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';

type DoodleActionsModule = typeof import('/static/generated/canvas/doodle-actions.js');
type DoodleCanvasItem = import('/static/generated/canvas/doodle-actions.js').DoodleCanvasItem;
type DoodleUndoManager = import('/static/generated/canvas/doodle-actions.js').DoodleUndoManager;
type DrawSegmentItem = import('/static/generated/canvas/doodle-actions.js').DrawSegmentItem;
type StampAnnotationItem = import('/static/generated/canvas/doodle-actions.js').StampAnnotationItem;
type TextAnnotationItem = import('/static/generated/canvas/doodle-actions.js').TextAnnotationItem;

interface DoodleMessageRecord {
  type?: string;
  stroke_id?: string;
  id?: string;
  ids?: string[];
  color?: string;
  points?: number[];
  text?: string;
  x?: number;
  y?: number;
  size?: number;
  rotation?: number;
  [key: string]: unknown;
}

interface DoodleSnapshotBatch {
  points: number[];
  color: string;
  size: number;
  stroke_id?: string;
}

interface CanvasUndoWindow extends Window {
  doodleUndoManager: DoodleUndoManager;
  doodleUndoStack: Array<{ type?: string }>;
  undoLastDoodleAction: () => boolean;
}

const DOODLE_ACTIONS_URL = '/static/generated/canvas/doodle-actions.js';
const DOODLE_ACTIONS_SHIM_URL = '/static/js/canvas-doodle-actions.js';

async function openUndoCanvas(
  canvas: CanvasHarness,
  options: {
    batches?: DoodleSnapshotBatch[];
    stamps?: StampAnnotationItem[];
    annotations?: TextAnnotationItem[];
    userStamps?: Array<{ id: string; name: string; url: string }>;
  } = {},
): Promise<{
  sentWsMessages: DoodleMessageRecord[];
  waitForSnapshot: () => Promise<void>;
}> {
  const sentWsMessages: DoodleMessageRecord[] = [];
  let snapshotSent = false;
  const userStamps = options.userStamps ?? [
    { id: 'dragon', name: 'Dragon', url: '/static/narratron-avatar.png' },
    { id: 'flag', name: 'Flag', url: '/static/narratron-avatar.png' },
  ];
  const makeSnapshot = (pageId: string): Record<string, unknown> => ({
    type: 'doodle_snapshot',
    page_id: pageId,
    enabled: true,
    batches: options.batches ?? [],
    stamps: options.stamps ?? [],
    annotations: options.annotations ?? [],
  });

  await canvas.openCanvas({
    theaterId: 'stage',
    isOrator: true,
    collabEnabled: true,
    viewport: { width: 1400, height: 900 },
    apiRoutes: {
      '/api/latest': {
        viewer_collab_enabled: true,
        orator_cursor: 0,
        latest: '/static/narratron-avatar.png',
        time: 1,
        history: [
          {
            url: '/static/narratron-avatar.png',
            path: '/static/narratron-avatar.png',
            page_id: 'page-1',
            time: 1,
          },
        ],
      },
      '/api/doodles': async (route) => {
        await route.fulfill({ json: makeSnapshot('page-1') });
      },
      '/api/users/me/stamps': async (route) => {
        await route.fulfill({ json: userStamps });
      },
      '/api/theaters/stage/stamps': [],
    },
    onWebSocket: (ws: WebSocketRoute) => {
      if (!ws.url().includes('/ws/doodle')) {
        return;
      }
      ws.send(JSON.stringify(makeSnapshot('page-1')));
      snapshotSent = true;
      ws.onMessage((raw) => {
        const msg = JSON.parse(String(raw)) as DoodleMessageRecord;
        if (msg.type === 'subscribe_doodles') {
          ws.send(JSON.stringify(makeSnapshot((msg.page_id as string | undefined) ?? 'page-1')));
          snapshotSent = true;
          return;
        }
        sentWsMessages.push(msg);
        if (typeof msg.client_message_id === 'string') {
          ws.send(
            JSON.stringify({
              type: 'doodle_ack',
              client_message_id: msg.client_message_id,
            }),
          );
        }
      });
    },
  });

  const waitForSnapshot = async (): Promise<void> => {
    await expect.poll(() => snapshotSent).toBe(true);
    await canvas.page.waitForFunction(
      () => document.getElementById('doodle-canvas') !== null,
      undefined,
      { timeout: 5000 },
    );
  };
  await waitForSnapshot();

  return { sentWsMessages, waitForSnapshot };
}

function lastMessageOfType(
  messages: readonly DoodleMessageRecord[],
  type: string,
): DoodleMessageRecord | undefined {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i]?.type === type) {
      return messages[i];
    }
  }
  return undefined;
}

async function getCanvasCenterBox(page: Page): Promise<{ x: number; y: number; width: number; height: number }> {
  const box = await page.locator('#doodle-canvas').boundingBox();
  expect(box).not.toBeNull();
  return box!;
}

test.describe('doodle-actions module & undo manager unit behavior', () => {
  test('deprecated shim re-exports compiled module and attaches classes to window', async ({
    canvas,
    page,
  }) => {
    await canvas.mount({ html: '<div id="root"></div>' });

    const parity = await page.evaluate(
      async ({ compiledUrl, shimUrl }: { compiledUrl: string; shimUrl: string }) => {
        const compiled = await window.__importModule<DoodleActionsModule>(compiledUrl);
        const shim = await window.__importModule<DoodleActionsModule>(shimUrl);
        const names = [
          'DoodleAction',
          'DrawStrokeDoodleAction',
          'DeleteSegmentsDoodleAction',
          'PlaceTextDoodleAction',
          'EditTextDoodleAction',
          'MoveTextDoodleAction',
          'DeleteTextDoodleAction',
          'PlaceStampDoodleAction',
          'TransformStampDoodleAction',
          'DeleteStampDoodleAction',
          'CompoundDeleteDoodleAction',
          'ClearCanvasDoodleAction',
          'createDoodleUndoManager',
          'restoreSegmentsToServer',
        ] as const;

        const compiledRecord = compiled as unknown as Record<string, unknown>;
        const shimRecord = shim as unknown as Record<string, unknown>;
        const winRecord = window as unknown as Record<string, unknown>;

        return names.every(
          (name) =>
            typeof compiledRecord[name] === 'function' &&
            compiledRecord[name] === shimRecord[name] &&
            compiledRecord[name] === winRecord[name],
        );
      },
      { compiledUrl: DOODLE_ACTIONS_URL, shimUrl: DOODLE_ACTIONS_SHIM_URL },
    );

    expect(parity).toBe(true);
  });

  test('restoring disconnected segments does not send a fabricated bridge', async ({
    canvas,
    page,
  }) => {
    await canvas.mount({ html: '<div></div>' });

    const restored = await page.evaluate(async (moduleUrl: string) => {
      const { restoreSegmentsToServer } = await window.__importModule<DoodleActionsModule>(
        moduleUrl,
      );
      const messages: Array<{ points: number[]; color: string; size: number; stroke_id?: string }> =
        [];
      restoreSegmentsToServer(
        [
          { stroke_id: 'one', x0: 0.1, y0: 0.1, x1: 0.2, y1: 0.2, color: 'red', size: 3 },
          { stroke_id: 'one', x0: 0.8, y0: 0.8, x1: 0.9, y1: 0.9, color: 'red', size: 3 },
        ],
        (message) => {
          messages.push(message);
        },
      );
      return messages;
    }, DOODLE_ACTIONS_URL);

    expect(restored).toHaveLength(2);
    expect(restored.map((message) => message.points)).toEqual([
      [0.1, 0.1, 0.2, 0.2],
      [0.8, 0.8, 0.9, 0.9],
    ]);
  });

  test('DoodleAction classes, sequence tracking, from() deserialization, and undo manager limits', async ({
    canvas,
    page,
  }) => {
    await canvas.mount({ html: '<div></div>' });

    const result = await page.evaluate(async (moduleUrl: string) => {
      const mod = await window.__importModule<DoodleActionsModule>(moduleUrl);
      const {
        DoodleAction,
        DrawStrokeDoodleAction,
        PlaceTextDoodleAction,
        PlaceStampDoodleAction,
        TransformStampDoodleAction,
        createDoodleUndoManager,
      } = mod;

      let actions: DoodleCanvasItem[] = [];
      let redraws = 0;
      const sent: Array<Record<string, unknown>> = [];
      let stateChanges = 0;
      let enabled = true;
      const clearedSelections: number[] = [];

      const manager = createDoodleUndoManager({
        maxHistory: 3,
        isEnabled: () => enabled,
        onStateChange: () => {
          stateChanges += 1;
        },
        getContext: () => ({
          get doodleActions() {
            return actions;
          },
          set doodleActions(val: DoodleCanvasItem[]) {
            actions = val;
          },
          doodleRenderer: {
            clearSelectedSegments: () => {
              clearedSelections.push(1);
            },
          },
          redrawAllDoodles: () => {
            redraws += 1;
          },
          sendOrQueueDoodleMessage: (msg) => {
            sent.push({ ...msg });
          },
        }),
      });

      const seg: DrawSegmentItem = {
        type: 'draw',
        id: 'seg-1',
        stroke_id: 'stroke-1',
        x0: 0.1,
        y0: 0.1,
        x1: 0.2,
        y1: 0.2,
        color: '#ef4444',
        size: 3,
      };
      const textAction: TextAnnotationItem = {
        type: 'text',
        id: 'txt-1',
        x: 0.2,
        y: 0.2,
        text: 'First Text',
        color: '#ffffff',
        size: 32,
        font: 'Outfit',
      };
      const stampAction: StampAnnotationItem = {
        type: 'stamp',
        id: 'st-1',
        stamp_id: 'apple',
        x: 0.5,
        y: 0.5,
        size: 48,
        rotation: 0,
      };

      actions.push(seg, textAction, stampAction);
      manager.push(new DrawStrokeDoodleAction('stroke-1', [seg]));
      manager.push(new PlaceTextDoodleAction(textAction));
      manager.push(new PlaceStampDoodleAction(stampAction));

      const stackInstanceTypes = [
        manager.stack[0] instanceof DrawStrokeDoodleAction,
        manager.stack[1] instanceof PlaceTextDoodleAction,
        manager.stack[2] instanceof PlaceStampDoodleAction,
      ];

      enabled = false;
      const canUndoWhileDisabled = manager.canUndo();
      enabled = true;

      manager.undo();
      const afterUndo1 = { stackLen: manager.stack.length, actionLen: actions.length, hasStamp: actions.some((a) => a.type === 'stamp') };

      manager.undo();
      const afterUndo2 = { stackLen: manager.stack.length, actionLen: actions.length, hasText: actions.some((a) => a.type === 'text') };

      manager.undo();
      const afterUndo3 = { stackLen: manager.stack.length, actionLen: actions.length };

      // Deserialize via DoodleAction.from and verify maxHistory eviction
      const fromUnknown = DoodleAction.from(null);
      const fromRotate = DoodleAction.from({
        type: 'rotate_stamp',
        before: { ...stampAction, rotation: 0 },
        after: { ...stampAction, rotation: 45 },
      });
      const isTransform = fromRotate instanceof TransformStampDoodleAction;

      let baseError = '';
      try {
        new DoodleAction('custom').undo();
      } catch (err) {
        baseError = err instanceof Error ? err.message : String(err);
      }

      const missingIdUndo = new PlaceTextDoodleAction(null).undo();

      manager.push({ type: 'draw_stroke', stroke_id: 's1', segments: [] });
      manager.push({ type: 'draw_stroke', stroke_id: 's2', segments: [] });
      manager.push({ type: 'draw_stroke', stroke_id: 's3', segments: [] });
      manager.push({ type: 'draw_stroke', stroke_id: 's4', segments: [] });
      const cappedIds = manager.stack.map((item) => (item as { stroke_id?: string }).stroke_id);
      manager.clear();

      return {
        stackInstanceTypes,
        canUndoWhileDisabled,
        afterUndo1,
        afterUndo2,
        afterUndo3,
        fromUnknown,
        isTransform,
        baseError,
        missingIdUndo,
        cappedIds,
        afterClearLen: manager.stack.length,
        redraws,
        stateChanges,
        clearedCount: clearedSelections.length,
        sentTypes: sent.map((m) => m.type),
      };
    }, DOODLE_ACTIONS_URL);

    expect(result.stackInstanceTypes).toEqual([true, true, true]);
    expect(result.canUndoWhileDisabled).toBe(false);
    expect(result.afterUndo1).toEqual({ stackLen: 2, actionLen: 2, hasStamp: false });
    expect(result.afterUndo2).toEqual({ stackLen: 1, actionLen: 1, hasText: false });
    expect(result.afterUndo3).toEqual({ stackLen: 0, actionLen: 0 });
    expect(result.fromUnknown).toBeNull();
    expect(result.isTransform).toBe(true);
    expect(result.baseError).toBe('Undo not implemented for action type custom');
    expect(result.missingIdUndo).toBe(false);
    expect(result.cappedIds).toEqual(['s2', 's3', 's4']);
    expect(result.afterClearLen).toBe(0);
    expect(result.redraws).toBe(3);
    expect(result.clearedCount).toBe(1);
    expect(result.sentTypes).toEqual(['remove_stamp', 'remove_text', 'remove_draw']);
  });

  test('CompoundDeleteDoodleAction and ClearCanvasDoodleAction restore segments, stamps, and texts', async ({
    canvas,
    page,
  }) => {
    await canvas.mount({ html: '<canvas id="doodle-canvas"></canvas>' });
    const result = await page.evaluate(async (moduleUrl: string) => {
      const mod = await window.__importModule<DoodleActionsModule>(moduleUrl);
      const seg: DrawSegmentItem = {
        type: 'draw',
        id: 's1',
        stroke_id: 'str1',
        x0: 0.1,
        y0: 0.1,
        x1: 0.2,
        y1: 0.2,
        color: '#ef4444',
        size: 3,
      };
      const text: TextAnnotationItem = {
        type: 'text',
        id: 't1',
        x: 0.3,
        y: 0.3,
        text: 'Compound',
        color: '#ffffff',
        size: 32,
        font: 'Outfit',
      };
      const stamp: StampAnnotationItem = {
        type: 'stamp',
        id: 'm1',
        stamp_id: 'gem',
        url: '/gem.png',
        name: 'Gem',
        x: 0.4,
        y: 0.4,
        size: 80,
        rotation: 0,
      };
      let actions: DoodleCanvasItem[] = [];
      const sent: Array<Record<string, unknown>> = [];
      const manager = mod.createDoodleUndoManager({
        getContext: () => ({
          get doodleActions() {
            return actions;
          },
          set doodleActions(v: DoodleCanvasItem[]) {
            actions = v;
          },
          redrawAllDoodles: () => {},
          sendOrQueueDoodleMessage: (msg) => sent.push(msg),
          restoreSegmentsToServer: mod.restoreSegmentsToServer,
        }),
      });

      manager.push({
        type: 'compound_delete',
        segments: [seg],
        stamps: [stamp],
        texts: [text],
      });
      manager.undo();
      const afterCompoundTypes = actions.map((a) => a.type);
      const afterCompoundSent = sent.map((m) => m.type);

      // Verify clear + undo restores all 3 items and sends draw_batch + stamp + text
      sent.length = 0;
      const snapshot = [...actions];
      actions = [];
      manager.push(new mod.ClearCanvasDoodleAction(snapshot));
      manager.undo();
      const afterClearUndoTypes = actions.map((a) => a.type);
      const afterClearUndoSent = sent.map((m) => m.type);

      return {
        afterCompoundTypes,
        afterCompoundSent,
        afterClearUndoTypes,
        afterClearUndoSent,
      };
    }, DOODLE_ACTIONS_URL);

    // Stamps are inserted before non-stamp items
    expect(result.afterCompoundTypes).toEqual(['stamp', 'draw', 'text']);
    expect(result.afterCompoundSent).toEqual(['draw_batch', 'stamp', 'text']);
    expect(result.afterClearUndoTypes).toEqual(['stamp', 'draw', 'text']);
    expect(result.afterClearUndoSent).toEqual(['draw_batch', 'stamp', 'text']);
  });
});

test.describe('canvas.html real doodle undo integration', () => {
  test('undo drawing stroke removes segments and sends remove_draw', async ({ canvas, page }) => {
    const { sentWsMessages } = await openUndoCanvas(canvas);
    const undoBtn = page.locator('#undo-doodle-btn');
    await expect(undoBtn).toBeDisabled();

    await page.locator('.color-btn[data-color="#ef4444"]').click();
    const box = await getCanvasCenterBox(page);
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;

    await page.mouse.move(cx - 40, cy - 20);
    await page.mouse.down();
    await page.mouse.move(cx, cy);
    await page.mouse.move(cx + 40, cy + 20);
    await page.mouse.up();

    await expect.poll(() => sentWsMessages.some((m) => m.type === 'draw_batch')).toBe(true);
    await expect(undoBtn).toBeEnabled();
    const drawBatch = lastMessageOfType(sentWsMessages, 'draw_batch');
    expect(drawBatch?.stroke_id).toBeTruthy();

    await undoBtn.click();
    await expect(undoBtn).toBeDisabled();
    await expect.poll(() => Boolean(lastMessageOfType(sentWsMessages, 'remove_draw'))).toBe(true);
    const removeMsg = lastMessageOfType(sentWsMessages, 'remove_draw');
    expect(removeMsg?.stroke_id).toBe(drawBatch?.stroke_id);
  });

  test('undo drawing segment deletion restores segments and sends draw_batch', async ({
    canvas,
    page,
  }) => {
    const { sentWsMessages } = await openUndoCanvas(canvas, {
      batches: [
        { points: [0.45, 0.45, 0.55, 0.55], color: '#ef4444', size: 3, stroke_id: 'str-1' },
        { points: [0.75, 0.75, 0.85, 0.85], color: '#3b82f6', size: 3, stroke_id: 'str-2' },
      ],
    });

    const box = await getCanvasCenterBox(page);
    // Marquee select only the center segment in select mode
    await page.mouse.move(box.x + box.width * 0.4, box.y + box.height * 0.4);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.6);
    await page.mouse.up();

    await page.keyboard.press('Delete');
    await expect.poll(() => Boolean(lastMessageOfType(sentWsMessages, 'remove_draw'))).toBe(true);

    const beforeUndoLength = sentWsMessages.length;
    await page.locator('#undo-doodle-btn').click();
    await expect
      .poll(() => sentWsMessages.slice(beforeUndoLength).some((m) => m.type === 'draw_batch'))
      .toBe(true);
    const restoredBatch = lastMessageOfType(sentWsMessages.slice(beforeUndoLength), 'draw_batch');
    expect(restoredBatch?.color).toBe('#ef4444');
  });

  test('undo text placement, edit, move, and deletion', async ({ canvas, page }) => {
    const { sentWsMessages } = await openUndoCanvas(canvas);
    const undoBtn = page.locator('#undo-doodle-btn');
    const box = await getCanvasCenterBox(page);
    const tx = box.x + box.width * 0.45;
    const ty = box.y + box.height * 0.45;

    // 1. Place text
    await page.locator('#text-annotation-toggle').click();
    await page.mouse.click(tx, ty);
    const editor = page.locator('.canvas-text-editor');
    await expect(editor).toBeVisible();
    await editor.fill('Secret Cave');
    await editor.press('Enter');

    const label = page.locator('.canvas-text-annotation');
    await expect(label).toHaveText('Secret Cave');
    await expect.poll(() => lastMessageOfType(sentWsMessages, 'text')?.text).toBe('Secret Cave');

    // Undo placement removes text and sends remove_text
    await undoBtn.click();
    await expect(label).toHaveCount(0);
    await expect.poll(() => Boolean(lastMessageOfType(sentWsMessages, 'remove_text'))).toBe(true);

    // 2. Place again, then edit, move, and delete
    await page.mouse.click(tx, ty);
    await expect(editor).toBeVisible();
    await editor.fill('Original Text');
    await editor.press('Enter');
    await expect(label).toHaveText('Original Text');

    // Exit text tool to select mode for editing/moving/deleting
    await page.locator('#text-annotation-toggle').click();

    // Edit text via double-click
    await label.dblclick();
    await expect(editor).toBeVisible();
    await editor.fill('Updated Text');
    await editor.press('Enter');
    await expect(label).toHaveText('Updated Text');

    // Undo edit restores 'Original Text'
    const beforeUndoEdit = sentWsMessages.length;
    await undoBtn.click();
    await expect(label).toHaveText('Original Text');
    await expect
      .poll(() => lastMessageOfType(sentWsMessages.slice(beforeUndoEdit), 'text')?.text)
      .toBe('Original Text');

    // Move text via pointer drag (select first so .movable is active)
    await label.click();
    const initialLabelBox = await label.boundingBox();
    expect(initialLabelBox).not.toBeNull();
    const startX = initialLabelBox!.x + initialLabelBox!.width / 2;
    const startY = initialLabelBox!.y + initialLabelBox!.height / 2;
    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.mouse.move(startX + 80, startY + 60, { steps: 5 });
    await page.mouse.up();
    const movedLabelBox = await label.boundingBox();
    expect(movedLabelBox!.x).toBeGreaterThan(initialLabelBox!.x + 40);

    // Undo move restores previous position
    await undoBtn.click();
    const restoredLabelBox = await label.boundingBox();
    expect(restoredLabelBox!.x).toBeCloseTo(initialLabelBox!.x, 0);
    expect(restoredLabelBox!.y).toBeCloseTo(initialLabelBox!.y, 0);

    // Delete text via selection + Delete
    await label.click();
    await page.keyboard.press('Delete');
    await expect(label).toHaveCount(0);

    // Undo deletion restores text
    await undoBtn.click();
    await expect(label).toHaveText('Original Text');
  });

  test('undo stamp placement, replacement, transformations (move/resize/rotate), and deletion', async ({
    canvas,
    page,
  }) => {
    const { sentWsMessages } = await openUndoCanvas(canvas);
    const undoBtn = page.locator('#undo-doodle-btn');
    await page.locator('#chat-open-stamps-btn').click();

    const flagCard = page.locator('.stamp-card[data-stamp-id="flag"]');
    await expect(flagCard).toBeVisible();
    const box = await getCanvasCenterBox(page);

    // Place first flag stamp at (0.35, 0.4)
    await flagCard.dragTo(page.locator('#image-container'), {
      targetPosition: { x: Math.round(box.width * 0.35), y: Math.round(box.height * 0.4) },
    });
    const stamps = page.locator('.canvas-stamp-annotation');
    await expect(stamps).toHaveCount(1);
    const firstBox = await stamps.first().boundingBox();
    expect(firstBox).not.toBeNull();

    // Place second flag stamp at (0.65, 0.6) -> replaces first flag
    await flagCard.dragTo(page.locator('#image-container'), {
      targetPosition: { x: Math.round(box.width * 0.65), y: Math.round(box.height * 0.6) },
    });
    await expect(stamps).toHaveCount(1);
    const replacedBox = await stamps.first().boundingBox();
    expect(replacedBox!.x).toBeGreaterThan(firstBox!.x + 80);

    // Undo replacement restores original stamp position
    await undoBtn.click();
    await expect(stamps).toHaveCount(1);
    const restoredFirstBox = await stamps.first().boundingBox();
    expect(restoredFirstBox!.x).toBeCloseTo(firstBox!.x, 0);

    // Undo first placement removes stamp and sends remove_stamp
    await undoBtn.click();
    await expect(stamps).toHaveCount(0);
    await expect.poll(() => Boolean(lastMessageOfType(sentWsMessages, 'remove_stamp'))).toBe(true);

    // Place a stamp for move -> resize -> rotate -> delete sequence
    await flagCard.dragTo(page.locator('#image-container'), {
      targetPosition: { x: Math.round(box.width * 0.5), y: Math.round(box.height * 0.5) },
    });
    await expect(stamps).toHaveCount(1);
    const initialStamp = stamps.first();
    const baseBox = (await initialStamp.boundingBox())!;

    // 1. Move stamp
    const stampCx = baseBox.x + baseBox.width / 2;
    const stampCy = baseBox.y + baseBox.height / 2;
    await page.mouse.move(stampCx, stampCy);
    await page.mouse.down();
    await page.mouse.move(stampCx + 60, stampCy + 40);
    await page.mouse.up();
    const afterMoveBox = (await initialStamp.boundingBox())!;
    expect(afterMoveBox.x).toBeGreaterThan(baseBox.x + 30);

    // 2. Resize stamp via resize handle
    const resizeHandle = initialStamp.locator('.stamp-resize-handle');
    const resizeBox = (await resizeHandle.boundingBox())!;
    await page.mouse.move(resizeBox.x + resizeBox.width / 2, resizeBox.y + resizeBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(
      resizeBox.x + resizeBox.width / 2 + 40,
      resizeBox.y + resizeBox.height / 2 + 40,
    );
    await page.mouse.up();
    const afterResizeSize = await initialStamp.evaluate((el) => parseFloat(el.style.width));
    expect(afterResizeSize).toBeGreaterThan(baseBox.width + 10);

    // 3. Rotate stamp via rotate handle
    const rotateHandle = initialStamp.locator('.stamp-rotate-handle');
    const rotateBox = (await rotateHandle.boundingBox())!;
    await page.mouse.move(rotateBox.x + rotateBox.width / 2, rotateBox.y + rotateBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(
      rotateBox.x + rotateBox.width / 2 + 50,
      rotateBox.y + rotateBox.height / 2 + 20,
    );
    await page.mouse.up();
    const afterRotateTransform = await initialStamp.evaluate((el) => el.style.transform);
    expect(afterRotateTransform).toContain('rotate(');
    expect(afterRotateTransform).not.toContain('rotate(0deg)');

    // Undo rotation
    await undoBtn.click();
    const transformAfterUndoRotate = await initialStamp.evaluate((el) => el.style.transform);
    expect(transformAfterUndoRotate).toContain('rotate(0deg)');
    expect(await initialStamp.evaluate((el) => parseFloat(el.style.width))).toBeCloseTo(
      afterResizeSize,
      0,
    );

    // Undo resize
    await undoBtn.click();
    expect(await initialStamp.evaluate((el) => parseFloat(el.style.width))).toBeCloseTo(
      baseBox.width,
      0,
    );
    expect((await initialStamp.boundingBox())!.x).toBeCloseTo(afterMoveBox.x, 0);

    // Undo move
    await undoBtn.click();
    expect((await initialStamp.boundingBox())!.x).toBeCloseTo(baseBox.x, 0);

    // Delete stamp via selection + Delete
    await initialStamp.click();
    await page.keyboard.press('Delete');
    await expect(stamps).toHaveCount(0);

    // Undo deletion restores stamp
    await undoBtn.click();
    await expect(stamps).toHaveCount(1);
  });

  test('undo compound delete and clear canvas restore all items', async ({ canvas, page }) => {
    const { sentWsMessages } = await openUndoCanvas(canvas, {
      batches: [
        { points: [0.42, 0.42, 0.52, 0.52], color: '#ef4444', size: 3, stroke_id: 'str-1' },
      ],
      annotations: [
        {
          type: 'text',
          id: 't1',
          x: 0.45,
          y: 0.48,
          text: 'Compound Text',
          color: '#ffffff',
          size: 32,
          font: 'Outfit',
        },
      ],
      stamps: [
        {
          type: 'stamp',
          id: 'm1',
          stamp_id: 'gem',
          url: '/static/narratron-avatar.png',
          name: 'Gem',
          x: 0.55,
          y: 0.5,
          size: 80,
          rotation: 0,
        },
      ],
    });

    const box = await getCanvasCenterBox(page);
    const textLoc = page.locator('.canvas-text-annotation');
    const stampLoc = page.locator('.canvas-stamp-annotation');
    await expect(textLoc).toHaveText('Compound Text');
    await expect(stampLoc).toHaveCount(1);

    // Marquee select all 3 items around the center and press Delete
    await page.mouse.move(box.x + box.width * 0.35, box.y + box.height * 0.35);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.65, box.y + box.height * 0.65);
    await page.mouse.up();
    await page.keyboard.press('Delete');

    await expect(textLoc).toHaveCount(0);
    await expect(stampLoc).toHaveCount(0);
    const undoBtn = page.locator('#undo-doodle-btn');
    const beforeDeleteUndo = sentWsMessages.length;

    // Undo all deletion actions on the stack to restore draw segment, stamp, and text
    while (!(await undoBtn.isDisabled())) {
      await undoBtn.click();
    }
    await expect(textLoc).toHaveText('Compound Text');
    await expect(stampLoc).toHaveCount(1);
    await expect
      .poll(() => {
        const types = new Set(sentWsMessages.slice(beforeDeleteUndo).map((m) => m.type));
        return types.has('draw_batch') && types.has('stamp') && types.has('text');
      })
      .toBe(true);

    // Clear canvas removes all items, and a single undo restores all items + sends messages
    const beforeClearUndo = sentWsMessages.length;
    await page.locator('#clear-doodle-btn').click();
    await expect(textLoc).toHaveCount(0);
    await expect(stampLoc).toHaveCount(0);
    await expect.poll(() => Boolean(lastMessageOfType(sentWsMessages, 'clear'))).toBe(true);

    await undoBtn.click();
    await expect(textLoc).toHaveText('Compound Text');
    await expect(stampLoc).toHaveCount(1);
    await expect
      .poll(() => {
        const types = new Set(sentWsMessages.slice(beforeClearUndo).map((m) => m.type));
        return types.has('draw_batch') && types.has('stamp') && types.has('text');
      })
      .toBe(true);
  });

  test('Ctrl+Z and Meta+Z keyboard shortcuts undo strokes unless typing in an input', async ({
    canvas,
    page,
  }) => {
    const { sentWsMessages } = await openUndoCanvas(canvas);
    const box = await getCanvasCenterBox(page);
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;

    // Draw stroke 1 (red)
    await page.locator('.color-btn[data-color="#ef4444"]').click();
    await page.mouse.move(cx - 50, cy - 30);
    await page.mouse.down();
    await page.mouse.move(cx - 10, cy - 10);
    await page.mouse.up();

    // Draw stroke 2 (blue)
    await page.locator('.color-btn[data-color="#3b82f6"]').click();
    await page.mouse.move(cx + 10, cy + 10);
    await page.mouse.down();
    await page.mouse.move(cx + 50, cy + 30);
    await page.mouse.up();

    await expect
      .poll(() => sentWsMessages.filter((m) => m.type === 'draw_batch').length)
      .toBe(2);
    const batches = sentWsMessages.filter((m) => m.type === 'draw_batch');

    // Focusing chat input suppresses canvas Ctrl+Z
    const chatInput = page.locator('#chat-input');
    await chatInput.focus();
    await page.keyboard.press('Control+z');
    expect(sentWsMessages.filter((m) => m.type === 'remove_draw')).toHaveLength(0);
    expect(await page.evaluate(() => (window as unknown as CanvasUndoWindow).doodleUndoStack.length)).toBe(2);

    // Blur input and undo stroke 2 with Control+z
    await chatInput.blur();
    await page.keyboard.press('Control+z');
    await expect
      .poll(() => sentWsMessages.filter((m) => m.type === 'remove_draw').length)
      .toBe(1);
    expect(lastMessageOfType(sentWsMessages, 'remove_draw')?.stroke_id).toBe(batches[1]?.stroke_id);

    // Undo stroke 1 with Meta+z
    await page.keyboard.press('Meta+z');
    await expect
      .poll(() => sentWsMessages.filter((m) => m.type === 'remove_draw').length)
      .toBe(2);
    expect(lastMessageOfType(sentWsMessages, 'remove_draw')?.stroke_id).toBe(batches[0]?.stroke_id);
  });
});
