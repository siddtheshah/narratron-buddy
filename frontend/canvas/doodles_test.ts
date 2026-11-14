import fs from 'node:fs';
import path from 'node:path';
import {
  CANVAS_ORIGIN,
  TEMPLATES_DIR,
  expect,
  test,
  type CanvasHarness,
} from '../../testing/ui/canvas_fixtures';
import type { Route, WebSocketRoute } from '@playwright/test';

interface DoodleMsg {
  type: string;
  page_id?: string;
  enabled?: boolean;
  client_message_id?: string;
  id?: string;
  text?: string;
  x?: number;
  y?: number;
  color?: string;
  size?: number;
  points?: number[];
}

interface DoodleBatchRecord {
  points: number[];
  color: string;
  size: number;
}

interface DoodleTextRecord {
  type: 'text';
  id: string;
  text: string;
  x: number;
  y: number;
  color: string;
  size: number;
  font?: string;
}

interface PageSnapshotState {
  type: 'doodle_snapshot';
  page_id: string;
  enabled: boolean;
  batches: DoodleBatchRecord[];
  annotations: DoodleTextRecord[];
}

interface NarrationPaneController {
  element: HTMLElement;
  prepare: (narration: HTMLElement | null) => void;
  setDrawingColorSelected: (selected: boolean) => void;
}

interface NarrationPaneModule {
  createNarrationPane: (mount: HTMLElement) => Promise<NarrationPaneController>;
}

interface ClipRecorderMockWindow extends Window {
  __clipMockState?: {
    displayMediaCalls: Array<Record<string, unknown>>;
    userMediaCalls: Array<Record<string, unknown>>;
    mixedSourceCount: number;
    recorderTrackCounts: number[];
    apiClipCalls: number;
    draftUploadCalls: number;
    openUrls: string[];
  };
  __doodleTestSchedule?: (delayMs?: number) => void;
}

test('deselecting drawing color enables scrollable narration pane', async ({ canvas, page }) => {
  const longText = Array.from(
    { length: 24 },
    (_, i) => `Paragraph ${i + 1}: The detective examines the locked study.`,
  ).join(' ');

  await canvas.openCanvas({
    isOrator: true,
    apiRoutes: {
      '/api/latest': {
        viewer_collab_enabled: true,
        orator_cursor: 0,
        latest: '/static/narratron-avatar.png',
        time: 1,
        history: [{ page_id: 'p1', url: '/static/narratron-avatar.png', time: 1 }],
        narration: longText,
      },
    },
  });

  const doodleCanvas = page.locator('#doodle-canvas');
  const overlay = page.locator('#scene-dialogue-overlay');
  const description = overlay.locator('.scene-description');
  const redBtn = page.locator('.color-btn[data-color="#ef4444"]');

  // Canvas starts with no drawing color selected, so narration pane is interactive & scrollable.
  await expect(doodleCanvas).toHaveClass(/drawing-disabled/);
  await expect(overlay).toHaveClass(/narration-interactive/);
  await expect(description).toHaveAttribute('tabindex', '0');
  await expect(description).toHaveAttribute('title', 'Scroll to read the full narration');

  await overlay.hover();
  await expect(description).toHaveCSS('overflow-y', 'auto');
  await expect(description).toHaveCSS('overscroll-behavior', 'contain');

  // Selecting a drawing color disables narration interaction & scrolling.
  await redBtn.click();
  await expect(doodleCanvas).not.toHaveClass(/drawing-disabled/);
  await expect(overlay).not.toHaveClass(/narration-interactive/);
  await expect(overlay).toHaveCSS('pointer-events', 'none');
  await expect(description).toHaveAttribute('tabindex', '-1');
  await expect(description).toHaveAttribute(
    'title',
    'Deselect the active drawing color to scroll the full narration',
  );

  // Clicking the selected color again deselects it and restores scrollable narration.
  await redBtn.click();
  await expect(doodleCanvas).toHaveClass(/drawing-disabled/);
  await expect(overlay).toHaveClass(/narration-interactive/);
  await expect(description).toHaveAttribute('tabindex', '0');

  // Direct module check of narration-pane.js contract via mount().
  await canvas.mount({
    stylesheets: ['/static/css/narration-pane.css'],
    html: '<div id="mount" style="position:relative;width:400px;height:200px;"><div id="slot"></div></div>',
  });

  const paneState = await page.evaluate(async (text: string) => {
    const mod = await window.__importModule<NarrationPaneModule>('/static/generated/canvas/narration-pane.js');
    const hasFactory = typeof mod.createNarrationPane === 'function';
    const slot = document.getElementById('slot')!;
    const ctrl = await mod.createNarrationPane(slot);
    const desc = document.createElement('div');
    desc.className = 'scene-description';
    desc.textContent = text;
    ctrl.element.appendChild(desc);
    ctrl.prepare(desc);
    const initialTabIndex = desc.tabIndex;
    ctrl.setDrawingColorSelected(false);
    const interactiveClassOn = ctrl.element.classList.contains('narration-interactive');
    const tabIndexAfterDeselect = desc.tabIndex;
    ctrl.setDrawingColorSelected(true);
    const interactiveClassOff = !ctrl.element.classList.contains('narration-interactive');
    const tabIndexAfterSelect = desc.tabIndex;
    return {
      hasFactory,
      initialTabIndex,
      interactiveClassOn,
      tabIndexAfterDeselect,
      interactiveClassOff,
      tabIndexAfterSelect,
    };
  }, longText);

  expect(paneState).toEqual({
    hasFactory: true,
    initialTabIndex: -1,
    interactiveClassOn: true,
    tabIndexAfterDeselect: 0,
    interactiveClassOff: true,
    tabIndexAfterSelect: -1,
  });
});

test('canvas starts without a drawing color and click controls page bar in select mode', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({
    isOrator: true,
    apiRoutes: {
      '/api/latest': {
        viewer_collab_enabled: true,
        orator_cursor: 0,
        latest: '/static/narratron-avatar.png',
        time: 1,
        history: [
          { page_id: 'p1', url: '/static/narratron-avatar.png', time: 1 },
          { page_id: 'p2', url: '/static/narratron-avatar.png', time: 2 },
        ],
      },
    },
  });

  const doodleCanvas = page.locator('#doodle-canvas');
  const pagingControls = page.locator('#history-paging-controls');
  const redBtn = page.locator('.color-btn[data-color="#ef4444"]');
  const imageContainer = page.locator('#image-container');

  // Canvas starts with no drawing color active.
  await expect(page.locator('.color-btn.active')).toHaveCount(0);
  await expect(doodleCanvas).toHaveClass(/drawing-disabled/);

  // Selecting a drawing color prevents image clicks from toggling .canvas-selected.
  await redBtn.click();
  await expect(redBtn).toHaveClass(/active/);
  await imageContainer.click();
  await expect(pagingControls).not.toHaveClass(/canvas-selected/);

  // Clicking outside #image-container and #doodle-toolbar clears the active color.
  await page.locator('header').click();
  await expect(page.locator('.color-btn.active')).toHaveCount(0);
  await expect(doodleCanvas).toHaveClass(/drawing-disabled/);

  // Clicking inside #image-container with no active drawing tool shows #history-paging-controls;
  // clicking outside #image-container clears .canvas-selected.
  await imageContainer.click();
  await expect(pagingControls).toHaveClass(/canvas-selected/);
  await page.locator('header').click();
  await expect(pagingControls).not.toHaveClass(/canvas-selected/);
});

test('selected white color swatch renders a black ring and border', async ({ canvas, page }) => {
  await canvas.openCanvas({ isOrator: true });

  const whiteBtn = page.locator('.color-btn[data-color="#ffffff"]');
  await expect(whiteBtn).toBeVisible();
  await whiteBtn.click();
  await expect(whiteBtn).toHaveClass(/active/);

  const ring = await whiteBtn.evaluate((el) => {
    const cs = getComputedStyle(el);
    return {
      borderColor: cs.borderColor,
      boxShadow: cs.boxShadow,
    };
  });
  expect(ring.borderColor).toBe('rgb(0, 0, 0)');
  expect(ring.boxShadow).toContain('rgb(0, 0, 0) 0px 0px 0px 2px');
});

test('text annotation uses expandable bottom bar control, enforces collab gating, and updates on OBS', async ({
  canvas,
  page,
}) => {
  const sent: DoodleMsg[] = [];
  const latestPayload = {
    viewer_collab_enabled: false,
    orator_cursor: 0,
    latest: '/static/narratron-avatar.png',
    time: 1,
    history: [{ page_id: 'p1', url: '/static/narratron-avatar.png', time: 1 }],
  };
  await canvas.openCanvas({
    isOrator: false,
    collabEnabled: false,
    apiRoutes: { '/api/latest': latestPayload },
    onWebSocket(ws: WebSocketRoute) {
      if (!ws.url().includes('/ws/doodle')) return;
      ws.onMessage((raw) => {
        const msg = JSON.parse(String(raw)) as DoodleMsg;
        if (msg.type === 'subscribe_doodles') {
          ws.send(
            JSON.stringify({
              type: 'doodle_snapshot',
              page_id: msg.page_id ?? 'p1',
              enabled: true,
              batches: [],
              annotations: [],
            }),
          );
          return;
        }
        sent.push(msg);
      });
    },
  });

  // Single expandable text control with font, size, and 7 color swatches (#ffffff active).
  const toggle = page.locator('#text-annotation-toggle');
  const menu = page.locator('#text-annotation-menu');
  await expect(toggle).toHaveAttribute('aria-controls', 'text-annotation-menu');
  await expect(menu).toBeHidden();
  await expect(menu.locator('#text-annotation-font')).toHaveCount(1);
  await expect(menu.locator('#text-annotation-size')).toHaveCount(1);
  await expect(menu.locator('.text-annotation-color')).toHaveCount(7);
  await expect(menu.locator('.text-annotation-color.active')).toHaveAttribute(
    'data-color',
    '#ffffff',
  );

  // Viewer without collab cannot open text annotation mode or create annotations.
  await expect(toggle).toBeDisabled();
  await page.locator('#image-container').click();
  await expect(page.locator('.canvas-text-editor')).toHaveCount(0);

  // Viewer with collab enabled CAN expand menu, select settings, and place a text annotation.
  await canvas.openCanvas({
    isOrator: false,
    collabEnabled: true,
    apiRoutes: {
      '/api/latest': {
        ...latestPayload,
        viewer_collab_enabled: true,
      },
    },
    onWebSocket(ws: WebSocketRoute) {
      if (!ws.url().includes('/ws/doodle')) return;
      ws.onMessage((raw) => {
        const msg = JSON.parse(String(raw)) as DoodleMsg;
        if (msg.type === 'subscribe_doodles') {
          ws.send(
            JSON.stringify({
              type: 'doodle_snapshot',
              page_id: msg.page_id ?? 'p1',
              enabled: true,
              batches: [],
              annotations: [],
            }),
          );
          return;
        }
        sent.push(msg);
        if (msg.client_message_id) {
          ws.send(
            JSON.stringify({ type: 'doodle_ack', client_message_id: msg.client_message_id }),
          );
        }
      });
    },
  });

  await expect(toggle).toBeEnabled();
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(menu).toBeVisible();
  await menu.locator('#text-annotation-font').selectOption('Bangers');
  await menu.locator('.text-annotation-color[data-color="#22c55e"]').click();

  const imgBox = (await page.locator('#image-container').boundingBox())!;
  await page.mouse.click(imgBox.x + imgBox.width * 0.4, imgBox.y + imgBox.height * 0.4);
  const editor = page.locator('.canvas-text-editor');
  await expect(editor).toBeVisible();
  await editor.fill('Collab note');
  await editor.press('Enter');

  await expect(page.locator('.canvas-text-annotation')).toHaveText('Collab note');
  await expect.poll(() => sent.some((m) => m.type === 'text' && m.text === 'Collab note')).toBe(
    true,
  );
  const sentText = sent.find((m) => m.type === 'text')!;
  expect(sentText.color).toBe('#22c55e');
});

test('OBS stage updates text annotation in place', async ({ canvas, page }) => {
  await canvas.mount({ html: '' });
  await page.addInitScript(() => {
    const win = window as Window & {
      __obsFillTextCalls?: Array<{ text: string; fillStyle: string }>;
    };
    win.__obsFillTextCalls = [];
    const origFillText = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (
      text: string,
      x: number,
      y: number,
      maxWidth?: number,
    ): void {
      win.__obsFillTextCalls!.push({
        text: String(text),
        fillStyle: String(this.fillStyle),
      });
      origFillText.call(this, text, x, y, maxWidth);
    };
  });
  const obsHtml = fs.readFileSync(path.join(TEMPLATES_DIR, 'obs.html'), 'utf8');
  let obsWs: WebSocketRoute | null = null;
  await canvas.serveRoute(
    (url) => url.origin === 'https://fonts.googleapis.com',
    async (route: Route) => {
      await route.fulfill({ contentType: 'text/css', body: '' });
    },
  );
  await canvas.serveRoute(
    (url) => url.origin === CANVAS_ORIGIN && url.pathname === '/obs',
    async (route: Route) => {
      await route.fulfill({ contentType: 'text/html; charset=utf-8', body: obsHtml });
    },
  );
  await canvas.serveRoute(
    (url) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/latest',
    async (route: Route) => {
      await route.fulfill({
        json: {
          latest: '/static/narratron-avatar.png',
          orator_cursor: 0,
          doodle_page_id: 'p1',
          history: [{ page_id: 'p1', url: '/static/narratron-avatar.png', time: 1 }],
        },
      });
    },
  );
  await canvas.serveRoute(
    (url) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/doodles',
    async (route: Route) => {
      await route.fulfill({
        json: {
          type: 'doodle_snapshot',
          page_id: 'p1',
          enabled: true,
          batches: [],
          annotations: [
            {
              type: 'text',
              id: 'obs-t1',
              text: 'Initial OBS note',
              x: 0.3,
              y: 0.3,
              color: '#ffffff',
              size: 32,
            },
          ],
        },
      });
    },
  );
  await page.routeWebSocket(/\/ws\//, (ws) => {
    if (!ws.url().includes('/ws/doodle')) return;
    obsWs = ws;
    ws.onMessage((raw) => {
      const msg = JSON.parse(String(raw)) as DoodleMsg;
      if (msg.type === 'subscribe_doodles') {
        ws.send(
          JSON.stringify({
            type: 'doodle_snapshot',
            page_id: 'p1',
            enabled: true,
            batches: [],
            annotations: [
              {
                type: 'text',
                id: 'obs-t1',
                text: 'Initial OBS note',
                x: 0.3,
                y: 0.3,
                color: '#ffffff',
                size: 32,
              },
            ],
          }),
        );
      }
    });
  });

  await page.goto(`${CANVAS_ORIGIN}/obs?theater_id=stage`);
  await expect
    .poll(() =>
      page.evaluate(() =>
        ((window as Window & { __obsFillTextCalls?: Array<{ text: string; fillStyle: string }> })
          .__obsFillTextCalls ?? []).some((c) => c.text === 'Initial OBS note'),
      ),
    )
    .toBe(true);
  await expect.poll(() => obsWs !== null).toBe(true);

  await page.evaluate(() => {
    (
      window as Window & { __obsFillTextCalls?: Array<{ text: string; fillStyle: string }> }
    ).__obsFillTextCalls = [];
  });

  obsWs!.send(
    JSON.stringify({
      type: 'text',
      id: 'obs-t1',
      text: 'Updated OBS note',
      x: 0.3,
      y: 0.3,
      color: '#ef4444',
      size: 36,
    }),
  );

  await expect
    .poll(() =>
      page.evaluate(() =>
        ((window as Window & { __obsFillTextCalls?: Array<{ text: string; fillStyle: string }> })
          .__obsFillTextCalls ?? []).some(
          (c) => c.text === 'Updated OBS note' && c.fillStyle === '#ef4444',
        ),
      ),
    )
    .toBe(true);
  const callsAfterUpdate = await page.evaluate(
    () =>
      (
        window as Window & { __obsFillTextCalls?: Array<{ text: string; fillStyle: string }> }
      ).__obsFillTextCalls ?? [],
  );
  expect(callsAfterUpdate.some((c) => c.text === 'Initial OBS note')).toBe(false);
});

test('canvas retries active doodle websocket connections and flushes queued messages', async ({
  canvas,
  page,
}) => {
  let wsAttempts = 0;
  let allowSocket = false;
  const sentMessages: DoodleMsg[] = [];

  await canvas.openCanvas({
    isOrator: true,
    apiRoutes: {
      '/api/latest': {
        viewer_collab_enabled: true,
        orator_cursor: 0,
        latest: '/static/narratron-avatar.png',
        time: 1,
        history: [{ page_id: 'p1', url: '/static/narratron-avatar.png', time: 1 }],
      },
    },
    onWebSocket(ws: WebSocketRoute) {
      if (!ws.url().includes('/ws/doodle')) return;
      wsAttempts += 1;
      if (!allowSocket) {
        ws.close();
        return;
      }
      ws.onMessage((raw) => {
        const msg = JSON.parse(String(raw)) as DoodleMsg;
        if (msg.type === 'subscribe_doodles') {
          ws.send(
            JSON.stringify({
              type: 'doodle_snapshot',
              page_id: msg.page_id ?? 'p1',
              enabled: true,
              batches: [],
              annotations: [],
            }),
          );
          return;
        }
        sentMessages.push(msg);
        if (msg.client_message_id) {
          ws.send(
            JSON.stringify({ type: 'doodle_ack', client_message_id: msg.client_message_id }),
          );
        }
      });
      setTimeout(() => {
        ws.send(
          JSON.stringify({
            type: 'doodle_snapshot',
            page_id: 'p1',
            enabled: true,
            batches: [],
            annotations: [],
          }),
        );
      }, 10);
    },
  });

  // Initial connection was rejected; mark window unfocused so queuing does not spin immediate reconnects.
  await expect.poll(() => wsAttempts).toBeGreaterThanOrEqual(1);
  await page.evaluate(() => {
    document.hasFocus = () => false;
  });
  const attemptsBeforeAction = wsAttempts;

  // Trigger a doodle message while disconnected and unfocused so it queues without connecting yet.
  await page.locator('#doodles-toggle-btn').click();
  expect(sentMessages).toHaveLength(0);
  expect(wsAttempts).toBe(attemptsBeforeAction);

  // Allow reconnect and activate window via focus -> immediate reconnect + snapshot + flush.
  allowSocket = true;
  await page.bringToFront();
  await page.evaluate(() => {
    document.hasFocus = () => true;
    window.dispatchEvent(new Event('focus'));
  });

  await expect.poll(() => wsAttempts).toBeGreaterThan(attemptsBeforeAction);
  await expect
    .poll(() => sentMessages.some((m) => m.type === 'toggle_doodles' && m.enabled === false))
    .toBe(true);
});

test('canvas clip sharing opens OBS clip stage and mixes opt-in voice and tab audio locally', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({ isOrator: true });

  // Open clip share modal and confirm "Open clean clip stage" targets /obs?theater_id=stage&clip=1.
  await page.evaluate(() => {
    const win = window as ClipRecorderMockWindow;
    win.__clipMockState = {
      displayMediaCalls: [],
      userMediaCalls: [],
      mixedSourceCount: 0,
      recorderTrackCounts: [],
      apiClipCalls: 0,
      draftUploadCalls: 0,
      openUrls: [],
    };
    window.open = ((url?: string | URL) => {
      win.__clipMockState!.openUrls.push(String(url ?? ''));
      return null;
    }) as typeof window.open;
  });

  await page.locator('#clip-share-btn').click();
  await expect(page.locator('#clip-share-modal')).toHaveAttribute('aria-hidden', 'false');
  await page.locator('#clip-record-btn').click();

  const openedUrls = await page.evaluate(
    () => (window as ClipRecorderMockWindow).__clipMockState!.openUrls,
  );
  expect(openedUrls).toEqual([`${CANVAS_ORIGIN}/obs?theater_id=stage&clip=1`]);

  // Now mount OBS in ?clip=1 mode and mock MediaDevices / AudioContext / MediaRecorder.
  const obsHtml = fs.readFileSync(path.join(TEMPLATES_DIR, 'obs.html'), 'utf8');
  await canvas.serveRoute(
    (url) => url.origin === CANVAS_ORIGIN && url.pathname === '/obs',
    async (route: Route) => {
      await route.fulfill({ contentType: 'text/html; charset=utf-8', body: obsHtml });
    },
  );
  let apiClipsCalled = false;
  let draftUploadCalled = false;
  await canvas.serveRoute(
    (url) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/clips',
    async (route: Route) => {
      apiClipsCalled = true;
      await route.fulfill({ status: 404 });
    },
  );
  await canvas.serveRoute(
    (url) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/tiktok/draft-upload-transcoded',
    async (route: Route) => {
      draftUploadCalled = true;
      await route.fulfill({ json: { publish_id: 'pub-1' } });
    },
  );
  await canvas.serveRoute(
    (url) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/tiktok/draft-status',
    async (route: Route) => {
      await route.fulfill({ json: { status: 'SEND_TO_USER_INBOX' } });
    },
  );

  await page.addInitScript(() => {
    const win = window as ClipRecorderMockWindow & {
      AudioContext: typeof AudioContext;
      MediaRecorder: typeof MediaRecorder;
      MediaStream: typeof MediaStream;
    };
    win.__clipMockState = {
      displayMediaCalls: [],
      userMediaCalls: [],
      mixedSourceCount: 0,
      recorderTrackCounts: [],
      apiClipCalls: 0,
      draftUploadCalls: 0,
      openUrls: [],
    };
    localStorage.setItem('narratron_mic_device_id', 'mic-studio-1');

    function makeTrack(kind: 'video' | 'audio'): MediaStreamTrack {
      return Object.assign(new EventTarget(), {
        kind,
        stop: () => {},
      }) as unknown as MediaStreamTrack;
    }

    function makeStream(videoCount: number, audioCount: number): MediaStream {
      const vTracks = Array.from({ length: videoCount }, () => makeTrack('video'));
      const aTracks = Array.from({ length: audioCount }, () => makeTrack('audio'));
      const allTracks = [...vTracks, ...aTracks];
      return {
        getVideoTracks: () => vTracks,
        getAudioTracks: () => aTracks,
        getTracks: () => allTracks,
      } as unknown as MediaStream;
    }

    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getDisplayMedia: async (opts: Record<string, unknown>) => {
          win.__clipMockState!.displayMediaCalls.push(opts);
          return makeStream(1, 1);
        },
        getUserMedia: async (opts: Record<string, unknown>) => {
          win.__clipMockState!.userMediaCalls.push(opts);
          return makeStream(0, 1);
        },
      },
    });

    class FakeMediaStream {
      private tracks: MediaStreamTrack[];
      constructor(tracks: MediaStreamTrack[]) {
        this.tracks = tracks;
      }
      getTracks(): MediaStreamTrack[] {
        return this.tracks;
      }
    }
    win.MediaStream = FakeMediaStream as unknown as typeof MediaStream;

    class FakeAudioContext {
      createMediaStreamDestination(): { stream: MediaStream } {
        return { stream: makeStream(0, 1) };
      }
      createMediaStreamSource(): { connect: () => void; disconnect: () => void } {
        win.__clipMockState!.mixedSourceCount += 1;
        return { connect: () => {}, disconnect: () => {} };
      }
      resume(): Promise<void> {
        return Promise.resolve();
      }
      close(): Promise<void> {
        return Promise.resolve();
      }
    }
    win.AudioContext = FakeAudioContext as unknown as typeof AudioContext;

    class FakeMediaRecorder extends EventTarget {
      static isTypeSupported(mime: string): boolean {
        return mime === 'video/webm';
      }
      state: 'inactive' | 'recording' = 'inactive';
      mimeType: string;
      constructor(stream: MediaStream, options?: MediaRecorderOptions) {
        super();
        this.mimeType = options?.mimeType ?? 'video/webm';
        win.__clipMockState!.recorderTrackCounts.push(stream.getTracks().length);
      }
      start(): void {
        this.state = 'recording';
        const dataEvent = Object.assign(new Event('dataavailable'), {
          data: new Blob(['clip-bytes'], { type: this.mimeType }),
        });
        this.dispatchEvent(dataEvent);
      }
      stop(): void {
        if (this.state === 'inactive') return;
        this.state = 'inactive';
        this.dispatchEvent(new Event('stop'));
      }
    }
    win.MediaRecorder = FakeMediaRecorder as unknown as typeof MediaRecorder;

    win.open = ((url?: string | URL) => {
      win.__clipMockState!.openUrls.push(String(url ?? ''));
      return {} as Window;
    }) as typeof window.open;
  });

  await page.goto(`${CANVAS_ORIGIN}/obs?theater_id=stage&clip=1`);

  const panel = page.locator('#clip-capture-panel');
  const includeMic = page.locator('#clip-include-mic');
  await expect(panel).toHaveClass(/active/);
  await expect(includeMic).toBeChecked();

  // Record with mic checked -> mixes display audio + microphone via AudioContext, then stop early.
  await page.locator('#clip-capture-start').click();
  await expect(page.locator('#clip-recording-bar')).toHaveClass(/active/);
  await expect(page.locator('#clip-recording-bar')).toHaveClass(/recording/);
  await page.locator('#clip-stop-early-btn').click();

  await expect(page.locator('#clip-draft-handoff')).toHaveClass(/active/);

  const clipState = await page.evaluate(
    () => (window as ClipRecorderMockWindow).__clipMockState!,
  );
  expect(clipState.displayMediaCalls).toHaveLength(1);
  expect(clipState.displayMediaCalls[0].audio).toBe(true);
  expect(clipState.userMediaCalls).toHaveLength(1);
  expect(clipState.userMediaCalls[0]).toEqual({
    audio: { deviceId: { exact: 'mic-studio-1' } },
    video: false,
  });
  expect(clipState.mixedSourceCount).toBe(2);
  expect(clipState.recorderTrackCounts).toEqual([2]);

  // Verify TikTok draft handoff posts to /api/tiktok/draft-upload-transcoded and never /api/clips.
  await page.locator('#clip-tiktok-draft-link').click();
  const postDraftOpenUrls = await page.evaluate(
    () => (window as ClipRecorderMockWindow).__clipMockState!.openUrls,
  );
  expect(postDraftOpenUrls).toContain('/api/tiktok/connect');
  await page.evaluate(() => {
    window.postMessage({ type: 'narratron-tiktok-connected' }, window.location.origin);
  });
  await expect.poll(() => draftUploadCalled).toBe(true);
  expect(apiClipsCalled).toBe(false);
});

test('doodles persist across page switches and reset when cleared', async ({ canvas, page }) => {
  const snapshots: Record<string, PageSnapshotState> = {
    first: {
      type: 'doodle_snapshot',
      page_id: 'first',
      enabled: true,
      batches: [],
      annotations: [],
    },
    second: {
      type: 'doodle_snapshot',
      page_id: 'second',
      enabled: true,
      batches: [],
      annotations: [],
    },
  };
  let cursor = 0;
  const subscribeCounts: Record<'first' | 'second', number> = { first: 0, second: 0 };
  let initialSnapshotHandled = false;

  await canvas.openCanvas({
    isOrator: true,
    apiRoutes: {
      '/api/latest': {
        viewer_collab_enabled: true,
        orator_cursor: cursor,
        latest: '/static/narratron-avatar.png',
        time: 1,
        history: [
          { page_id: 'first', url: '/static/narratron-avatar.png', time: 1 },
          { page_id: 'second', url: '/static/narratron-avatar.png', time: 2 },
        ],
      },
      '/api/doodles': async (route: Route, url: URL) => {
        const pageId = url.searchParams.get('page_id') ?? 'first';
        if (pageId === 'first') {
          initialSnapshotHandled = true;
        }
        await route.fulfill({ json: snapshots[pageId] ?? snapshots.first });
      },
      '/api/theaters/stage/orator-action': async (route: Route) => {
        const body = JSON.parse(route.request().postData() || '{}') as { action?: string };
        if (body.action === 'next_image' && cursor < 1) cursor += 1;
        if (body.action === 'previous_image' && cursor > 0) cursor -= 1;
        await route.fulfill({ json: { ok: true, orator_cursor: cursor } });
      },
    },
    onWebSocket(ws: WebSocketRoute) {
      if (!ws.url().includes('/ws/doodle')) return;
      const wsUrl = new URL(ws.url());
      const initialPageId = wsUrl.searchParams.get('page_id');
      if (initialPageId && Object.hasOwn(snapshots, initialPageId)) {
        ws.send(JSON.stringify(snapshots[initialPageId]));
        if (initialPageId === 'first') {
          initialSnapshotHandled = true;
        }
      }
      ws.onMessage((raw) => {
        const msg = JSON.parse(String(raw)) as DoodleMsg;
        const pageId = msg.page_id ?? 'first';
        if (msg.type === 'subscribe_doodles') {
          if (pageId === 'first' || pageId === 'second') {
            subscribeCounts[pageId] += 1;
          }
          ws.send(JSON.stringify(snapshots[pageId]));
          if (pageId === 'first') {
            initialSnapshotHandled = true;
          }
          return;
        }
        if (msg.type === 'draw_batch' && msg.points) {
          snapshots[pageId].batches.push({
            points: msg.points,
            color: msg.color ?? '#ef4444',
            size: msg.size ?? 3,
          });
        } else if (msg.type === 'clear') {
          snapshots[pageId].batches = [];
          snapshots[pageId].annotations = [];
        }
        if (msg.client_message_id) {
          ws.send(
            JSON.stringify({ type: 'doodle_ack', client_message_id: msg.client_message_id }),
          );
        }
      });
    },
  });

  const doodleCanvas = page.locator('#doodle-canvas');
  await expect
    .poll(() =>
      page.evaluate(() => {
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
  await expect.poll(() => initialSnapshotHandled).toBe(true);
  const sampleCenterStroke = (): Promise<number[]> =>
    doodleCanvas.evaluate((el: HTMLCanvasElement) => {
      const ctx = el.getContext('2d')!;
      return Array.from(
        ctx.getImageData(Math.round(el.width * 0.5), Math.round(el.height * 0.5), 1, 1).data,
      );
    });

  // Draw red horizontal stroke across center on page 1.
  await page.locator('.color-btn[data-color="#ef4444"]').click();
  await expect(doodleCanvas).not.toHaveClass(/drawing-disabled/);
  const box = (await doodleCanvas.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.4, box.y + box.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.5, { steps: 2 });
  await page.mouse.up();

  await expect.poll(async () => (await sampleCenterStroke())[0]).toBeGreaterThan(180);
  await expect.poll(() => snapshots.first.batches.length).toBeGreaterThanOrEqual(1);

  // Switch to page 2 -> blank, then switch back to page 1 -> stroke restored from snapshot.
  await page.locator('#page-next-btn').click();
  await expect.poll(() => sampleCenterStroke()).toEqual([0, 0, 0, 0]);
  await page.locator('#page-prev-btn').click();
  await expect.poll(async () => (await sampleCenterStroke())[0]).toBeGreaterThan(180);

  // Clear resets doodles on page 1 and remains empty across page switches.
  await page.locator('#clear-doodle-btn').click();
  await expect.poll(() => sampleCenterStroke()).toEqual([0, 0, 0, 0]);
  await expect.poll(() => snapshots.first.batches.length).toBe(0);

  const secondBefore = subscribeCounts.second;
  const firstBefore = subscribeCounts.first;
  await page.locator('#page-next-btn').click();
  await expect.poll(() => subscribeCounts.second).toBeGreaterThan(secondBefore);
  await page.locator('#page-prev-btn').click();
  await expect.poll(() => subscribeCounts.first).toBeGreaterThan(firstBefore);
  expect(await sampleCenterStroke()).toEqual([0, 0, 0, 0]);
});

test('canvas right-click opens action wheel with center cursor cycling wiring', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({ isOrator: true });

  const container = page.locator('#image-container');
  const doodleCanvas = page.locator('#doodle-canvas');
  const redBtn = page.locator('.color-btn[data-color="#ef4444"]');
  const box = (await container.boundingBox())!;
  const centerX = box.x + box.width / 2;
  const centerY = box.y + box.height / 2;

  // Starts in select mode (no drawing color active).
  await expect(doodleCanvas).toHaveClass(/drawing-disabled/);

  // Right-click opens the action wheel with center cycle prompt and cycles select -> navigate -> marker (#ef4444).
  await page.mouse.move(centerX, centerY);
  await page.mouse.down({ button: 'right' });

  const wheel = page.locator('#orator-action-wheel');
  const status = page.locator('#action-wheel-status');
  await expect(wheel).toBeVisible();
  await expect(wheel.locator('.wheel-center')).toContainText(/Cycle/i);
  await expect(status).toContainText(
    'Release in the center to cycle drawing tool, selection, and zoom and pan',
  );

  // Release in the center triggers onCenterAction -> cycleCanvasCursor().
  await page.mouse.up({ button: 'right' });
  await expect(wheel).toBeHidden();

  // Cycle order from select -> navigate -> marker (#ef4444) -> select; step through remaining cycle via center release.
  await expect(container).toHaveAttribute('data-cursor-mode', 'navigate');

  await page.mouse.down({ button: 'right' });
  await expect(wheel).toBeVisible();
  await page.mouse.up({ button: 'right' });
  await expect(container).toHaveAttribute('data-cursor-mode', 'marker');
  await expect(redBtn).toHaveClass(/active/);
  await expect(doodleCanvas).not.toHaveClass(/drawing-disabled/);

  await page.mouse.down({ button: 'right' });
  await expect(wheel).toBeVisible();
  await page.mouse.up({ button: 'right' });
  await expect(container).toHaveAttribute('data-cursor-mode', 'select');
  await expect(redBtn).not.toHaveClass(/active/);
  await expect(doodleCanvas).toHaveClass(/drawing-disabled/);
});
