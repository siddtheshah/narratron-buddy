import { expect, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';
import type { Route, WebSocketRoute } from '@playwright/test';

interface DoodleBatch {
  points: number[];
  color: string;
  size: number;
}

interface TextAnnotationPayload {
  type: 'text';
  id: string;
  text: string;
  x: number;
  y: number;
  color: string;
  size: number;
}

interface DoodleSnapshotPayload {
  type: 'doodle_snapshot';
  page_id: string;
  enabled: boolean;
  batches: DoodleBatch[];
  annotations: TextAnnotationPayload[];
}

interface SentDoodleMessage {
  type: string;
  page_id?: string;
  enabled?: boolean;
  client_message_id?: string;
  points?: number[];
  color?: string;
  size?: number;
}

function createSnapshots(): Record<string, DoodleSnapshotPayload> {
  return {
    first: {
      type: 'doodle_snapshot',
      page_id: 'first',
      enabled: true,
      batches: [{ points: [0.1, 0.1, 0.2, 0.2], color: '#ef4444', size: 3 }],
      annotations: [
        {
          type: 'text',
          id: 'clue',
          text: 'First clue',
          x: 0.2,
          y: 0.3,
          color: '#ffffff',
          size: 32,
        },
      ],
    },
    second: {
      type: 'doodle_snapshot',
      page_id: 'second',
      enabled: true,
      batches: [],
      annotations: [],
    },
  };
}

function createHistory() {
  return [
    { page_id: 'first', url: '/static/narratron-avatar.png', time: 1 },
    { page_id: 'second', url: '/static/narratron-avatar.png', time: 2 },
  ];
}

async function openHistoryCanvas(
  canvas: CanvasHarness,
  options: {
    isOrator?: boolean;
    initialCursor?: number;
    holdSnapshots?: boolean;
    closeDoodleSocket?: boolean;
  } = {},
) {
  const isOrator = options.isOrator ?? true;
  let oratorCursor = options.initialCursor ?? 0;
  const snapshots = createSnapshots();
  const history = createHistory();
  const subscriptions: string[] = [];
  const sent: SentDoodleMessage[] = [];
  const sockets: WebSocketRoute[] = [];
  const pendingSubscriptionReplies: Record<string, () => void> = {};
  let latestRequestCount = 0;

  let resolveDoodleSocketConnected: (() => void) | null = null;
  let doodleSocketConnected = new Promise<void>((resolve) => {
    resolveDoodleSocketConnected = resolve;
  });

  const resetSocketWaiter = (): void => {
    doodleSocketConnected = new Promise<void>((resolve) => {
      resolveDoodleSocketConnected = resolve;
    });
  };

  let closeOnConnect = options.closeDoodleSocket ?? false;
  let holdSnapshots = options.holdSnapshots ?? false;

  const pendingHttpDoodles: Record<string, Array<() => void>> = {};
  const httpDoodleRequests: string[] = [];
  let holdHttpDoodles = false;

  await canvas.openCanvas({
    isOrator,
    collabEnabled: true,
    viewport: { width: 1200, height: 800 },
    apiRoutes: {
      '/api/latest': async (route: Route) => {
        latestRequestCount += 1;
        if (!closeOnConnect) {
          await doodleSocketConnected;
        }
        await route.fulfill({
          json: {
            viewer_collab_enabled: true,
            orator_cursor: oratorCursor,
            latest: history[oratorCursor].url,
            time: history[oratorCursor].time,
            history,
          },
        });
      },
      '/api/doodles': async (route: Route, url: URL) => {
        const pageId = url.searchParams.get('page_id') ?? 'first';
        httpDoodleRequests.push(pageId);
        const reply = async (): Promise<void> => {
          await route.fulfill({ json: snapshots[pageId] ?? snapshots.first });
        };
        if (holdHttpDoodles) {
          pendingHttpDoodles[pageId] ??= [];
          pendingHttpDoodles[pageId].push(() => {
            void reply();
          });
          return;
        }
        await reply();
      },
      '/api/theaters/stage/orator-action': async (route: Route) => {
        const body = JSON.parse(route.request().postData() || '{}') as { action?: string };
        if (body.action === 'next_image' && oratorCursor < history.length - 1) {
          oratorCursor += 1;
        } else if (body.action === 'previous_image' && oratorCursor > 0) {
          oratorCursor -= 1;
        }
        await route.fulfill({ json: { ok: true, orator_cursor: oratorCursor } });
      },
    },
    onWebSocket(ws: WebSocketRoute) {
      if (!ws.url().includes('/ws/doodle')) return;
      if (closeOnConnect) {
        ws.close();
        return;
      }
      sockets.push(ws);
      resolveDoodleSocketConnected?.();
      ws.onMessage((raw) => {
        const message = JSON.parse(String(raw)) as SentDoodleMessage;
        if (message.type === 'subscribe_doodles' && message.page_id) {
          const pageId = message.page_id;
          subscriptions.push(pageId);
          const reply = (): void => {
            ws.send(JSON.stringify(snapshots[pageId]));
          };
          if (holdSnapshots) {
            pendingSubscriptionReplies[pageId] = reply;
          } else {
            reply();
          }
          return;
        }
        if (message.type === 'toggle_doodles' && message.page_id) {
          snapshots[message.page_id].enabled = Boolean(message.enabled);
        }
        sent.push(message);
        if (message.client_message_id) {
          ws.send(
            JSON.stringify({ type: 'doodle_ack', client_message_id: message.client_message_id }),
          );
        }
      });
    },
  });

  await expect
    .poll(() =>
      canvas.page.evaluate(() => {
        const img = document.getElementById('current-image') as HTMLImageElement | null;
        const doodle = document.getElementById('doodle-canvas') as HTMLCanvasElement | null;
        return Boolean(
          img &&
            img.complete &&
            img.naturalWidth > 0 &&
            doodle &&
            doodle.width > 0 &&
            doodle.height > 0,
        );
      }),
    )
    .toBe(true);
  await canvas.page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
  });

  return {
    snapshots,
    subscriptions,
    sent,
    sockets,
    pendingSubscriptionReplies,
    httpDoodleRequests,
    pendingHttpDoodles,
    get latestRequestCount() {
      return latestRequestCount;
    },
    setHoldSnapshots(value: boolean) {
      holdSnapshots = value;
    },
    setCloseDoodleSocket(value: boolean) {
      closeOnConnect = value;
      if (!value) resetSocketWaiter();
    },
    setHoldHttpDoodles(value: boolean) {
      holdHttpDoodles = value;
    },
    broadcast(payload: Record<string, unknown>) {
      for (const ws of sockets) {
        ws.send(JSON.stringify(payload));
      }
    },
  };
}

test('page doodles and visibility restore when browsing back', async ({ canvas, page }) => {
  const harness = await openHistoryCanvas(canvas, { isOrator: true, initialCursor: 0 });
  const doodleCanvas = page.locator('#doodle-canvas');
  const doodlesToggle = page.locator('#doodles-toggle-btn');

  const sampleNormalizedPixel = async (nx: number, ny: number): Promise<number[]> =>
    doodleCanvas.evaluate(
      (el: HTMLCanvasElement, coords: { nx: number; ny: number }) => {
        const ctx = el.getContext('2d')!;
        const x = Math.round(el.width * coords.nx);
        const y = Math.round(el.height * coords.ny);
        return Array.from(ctx.getImageData(x, y, 1, 1).data);
      },
      { nx, ny },
    );

  await expect(page.locator('.canvas-text-annotation')).toHaveText('First clue');
  await expect.poll(async () => (await sampleNormalizedPixel(0.15, 0.15))[0]).toBeGreaterThan(180);
  const firstStrokePixel = await sampleNormalizedPixel(0.15, 0.15);
  expect(firstStrokePixel[3]).toBe(255);
  expect(await sampleNormalizedPixel(0.5, 0.5)).toEqual([0, 0, 0, 0]);

  await doodlesToggle.click();
  await expect.poll(() => harness.sent.length).toBe(1);
  expect(harness.sent[0]).toEqual({
    type: 'toggle_doodles',
    enabled: false,
    page_id: 'first',
    client_message_id: harness.sent[0].client_message_id,
  });
  await expect(doodleCanvas).toBeHidden();

  await page.locator('#page-next-btn').click();
  await expect(doodlesToggle).toHaveText('✏️ Doodles: On');
  await expect(page.locator('.canvas-text-annotation')).toHaveCount(0);
  await expect.poll(() => sampleNormalizedPixel(0.15, 0.15)).toEqual([0, 0, 0, 0]);

  await page.locator('#page-prev-btn').click();
  await expect(doodlesToggle).toHaveText('✏️ Doodles: Off');
  await expect(doodleCanvas).toBeHidden();

  await doodlesToggle.click();
  await expect(doodleCanvas).toBeVisible();
  await expect(page.locator('#canvas-text-layer')).toBeVisible();
  await expect(page.locator('.canvas-text-annotation')).toHaveText('First clue');
  await expect.poll(() => sampleNormalizedPixel(0.15, 0.15)).toEqual(firstStrokePixel);
  expect(await sampleNormalizedPixel(0.5, 0.5)).toEqual([0, 0, 0, 0]);
  expect(
    await doodleCanvas.evaluate((el: HTMLCanvasElement) => el.style.pointerEvents),
  ).toBe('none');
  await expect.poll(() => harness.sent.length).toBe(2);
  expect(harness.sent[1].enabled).toBe(true);
  expect(harness.sent[1].page_id).toBe('first');
  expect(harness.subscriptions).toEqual(['first', 'second', 'first']);
});

test('viewer browsing back enters paged-back read-only state while preserving page doodles', async ({
  canvas,
  page,
}) => {
  await openHistoryCanvas(canvas, { isOrator: false, initialCursor: 1 });
  const doodleCanvas = page.locator('#doodle-canvas');

  // Viewer selects a drawing color on live page 'second'.
  await page.locator('.color-btn[data-color="#ef4444"]').click();
  await expect
    .poll(() => doodleCanvas.evaluate((el: HTMLCanvasElement) => el.style.pointerEvents))
    .toBe('auto');

  // Viewer pages back to historical page 'first'.
  await page.locator('#page-prev-btn').click();
  await expect(page.locator('.canvas-text-annotation')).toHaveText('First clue');
  expect(
    await doodleCanvas.evaluate((el: HTMLCanvasElement) => el.style.pointerEvents),
  ).toBe('none');
  await expect(doodleCanvas).toHaveClass(/drawing-disabled/);
});

test('live messages for another page do not replace browsed doodles', async ({ canvas, page }) => {
  const harness = await openHistoryCanvas(canvas, { isOrator: true, initialCursor: 0 });
  const doodleCanvas = page.locator('#doodle-canvas');
  const doodlesToggle = page.locator('#doodles-toggle-btn');

  await expect(page.locator('.canvas-text-annotation')).toHaveText('First clue');
  const beforePixels = await doodleCanvas.evaluate((el: HTMLCanvasElement) => el.toDataURL());
  const initialLatestCount = harness.latestRequestCount;

  for (const type of ['clear', 'draw_batch', 'doodles_toggle', 'doodle_snapshot']) {
    harness.broadcast({
      type,
      page_id: 'second',
      enabled: false,
      batches: [],
      points: [0.1, 0.5, 0.9, 0.5],
      color: '#22c55e',
      size: 6,
    });
  }

  await expect(page.locator('.canvas-text-annotation')).toHaveText('First clue');
  await expect(doodlesToggle).toHaveText('✏️ Doodles: On');
  expect(await doodleCanvas.evaluate((el: HTMLCanvasElement) => el.toDataURL())).toBe(beforePixels);
  expect(harness.latestRequestCount).toBe(initialLatestCount);

  harness.broadcast({ type: 'clear', page_id: 'first' });
  await expect(page.locator('.canvas-text-annotation')).toHaveCount(0);
  await expect
    .poll(() => doodleCanvas.evaluate((el: HTMLCanvasElement) => el.toDataURL()))
    .not.toBe(beforePixels);
});

test('page snapshots load on demand and stale HTTP responses are ignored', async ({
  canvas,
  page,
}) => {
  let oratorCursor = 0;
  const snapshots = createSnapshots();
  const history = createHistory();
  const httpRequests: string[] = [];
  const pendingHttp: Record<string, Array<() => void>> = {};
  let allowSocketConnect = false;
  let socketConnectedCount = 0;
  const sockets: WebSocketRoute[] = [];

  await canvas.openCanvas({
    isOrator: true,
    collabEnabled: true,
    viewport: { width: 1200, height: 800 },
    apiRoutes: {
      '/api/latest': {
        viewer_collab_enabled: true,
        orator_cursor: oratorCursor,
        latest: history[0].url,
        time: history[0].time,
        history,
      },
      '/api/doodles': async (route: Route, url: URL) => {
        const pageId = url.searchParams.get('page_id') ?? 'first';
        httpRequests.push(pageId);
        pendingHttp[pageId] ??= [];
        pendingHttp[pageId].push(() => {
          void route.fulfill({ json: snapshots[pageId] });
        });
      },
      '/api/theaters/stage/orator-action': async (route: Route) => {
        const body = JSON.parse(route.request().postData() || '{}') as { action?: string };
        if (body.action === 'next_image') oratorCursor = 1;
        else if (body.action === 'previous_image') oratorCursor = 0;
        await route.fulfill({ json: { ok: true, orator_cursor: oratorCursor } });
      },
    },
    onWebSocket(ws: WebSocketRoute) {
      if (!ws.url().includes('/ws/doodle')) return;
      if (!allowSocketConnect) {
        ws.close();
        return;
      }
      socketConnectedCount += 1;
      sockets.push(ws);
      ws.onMessage((raw) => {
        const message = JSON.parse(String(raw)) as SentDoodleMessage;
        if (message.client_message_id) {
          ws.send(
            JSON.stringify({ type: 'doodle_ack', client_message_id: message.client_message_id }),
          );
        }
      });
    },
  });

  // Initial load requested 'first' via HTTP while socket was closed.
  await expect.poll(() => httpRequests.slice()).toEqual(['first']);

  // Navigate to 'second' before 'first' responds, then resolve stale 'first' response.
  await page.locator('#page-next-btn').click();
  await expect.poll(() => httpRequests.slice()).toEqual(['first', 'second']);
  pendingHttp.first.shift()?.();
  await page.waitForTimeout(50);

  await expect(page.locator('.canvas-text-annotation')).toHaveCount(0);
  pendingHttp.second.shift()?.();
  await page.waitForTimeout(50);
  await expect(page.locator('.canvas-text-annotation')).toHaveCount(0);
  expect(httpRequests).toEqual(['first', 'second']);

  // A socket can open while the HTTP fallback is still in flight. Its newer
  // snapshot and live clear must win over that older HTTP response.
  await page.locator('#page-prev-btn').click();
  await expect.poll(() => httpRequests.slice()).toEqual(['first', 'second', 'first']);

  // Allow socket reconnect and trigger reconnect by queueing a local doodle action.
  allowSocketConnect = true;
  await page.bringToFront();
  await page.evaluate(() => window.focus());
  await page.locator('#doodles-toggle-btn').click();
  await page.locator('#doodles-toggle-btn').click();
  await expect.poll(() => socketConnectedCount).toBe(1);

  sockets[0].send(JSON.stringify(snapshots.first));
  await expect(page.locator('.canvas-text-annotation')).toHaveText('First clue');
  sockets[0].send(JSON.stringify({ type: 'clear', page_id: 'first' }));
  await expect(page.locator('.canvas-text-annotation')).toHaveCount(0);

  // Fulfill the older in-flight HTTP snapshot for 'first'; it must be ignored.
  pendingHttp.first.shift()?.();
  await page.waitForTimeout(50);
  await expect(page.locator('.canvas-text-annotation')).toHaveCount(0);
});

test('drawing while page snapshot loads is replayed', async ({ canvas, page }) => {
  const harness = await openHistoryCanvas(canvas, {
    isOrator: true,
    initialCursor: 0,
    holdSnapshots: true,
  });
  const doodleCanvas = page.locator('#doodle-canvas');

  await expect.poll(() => Boolean(harness.pendingSubscriptionReplies.first)).toBe(true);

  // Select green marker and draw while the initial snapshot is still pending.
  await page.locator('.color-btn[data-color="#22c55e"]').click();
  const box = (await doodleCanvas.boundingBox())!;
  const drawY = box.y + box.height * 0.2;
  const startX = box.x + box.width * 0.7;
  const endX = box.x + box.width * 0.8;

  await page.mouse.move(startX, drawY);
  await page.mouse.down();
  await page.mouse.move(endX, drawY, { steps: 4 });
  await page.mouse.up();

  // Wait one animation frame so flushDrawBatch queues the draw_batch message.
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())));
  expect(harness.sent).toHaveLength(0);

  // Deliver the held snapshot; queued draw_batch must flush and replay onto the canvas.
  harness.pendingSubscriptionReplies.first();
  await expect(page.locator('.canvas-text-annotation')).toHaveText('First clue');
  await expect
    .poll(() => harness.sent.filter((m) => m.type === 'draw_batch').length)
    .toBeGreaterThanOrEqual(1);

  const greenMidPixel = await doodleCanvas.evaluate((el: HTMLCanvasElement) => {
    const ctx = el.getContext('2d')!;
    const x = Math.round(el.width * 0.75);
    const y = Math.round(el.height * 0.2);
    return Array.from(ctx.getImageData(x, y, 1, 1).data);
  });
  // #22c55e -> (34, 197, 94, 255)
  expect(greenMidPixel[1]).toBeGreaterThan(150);
  expect(greenMidPixel[3]).toBe(255);

  // Snapshot's red stroke at (0.15, 0.15) should also be rendered.
  const redMidPixel = await doodleCanvas.evaluate((el: HTMLCanvasElement) => {
    const ctx = el.getContext('2d')!;
    const x = Math.round(el.width * 0.15);
    const y = Math.round(el.height * 0.15);
    return Array.from(ctx.getImageData(x, y, 1, 1).data);
  });
  // #ef4444 -> (239, 68, 68, 255)
  expect(redMidPixel[0]).toBeGreaterThan(180);
  expect(redMidPixel[3]).toBe(255);
});
