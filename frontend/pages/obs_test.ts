import fs from 'node:fs';
import path from 'node:path';
import type { Page, Route, WebSocketRoute } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  TEMPLATES_DIR,
  expect,
  test,
  type CanvasHarness,
} from '../../testing/ui/canvas_fixtures';

type ObsModule = typeof import('/static/generated/pages/obs.js');

interface AudioEventRecord {
  readonly src: string;
  readonly volume: number;
  readonly paused: boolean;
}

interface ObsTestWindow extends Window {
  __audioLog: AudioEventRecord[];
  __audioInstances: MockHtmlAudio[];
  __postedMessages: Array<{ message: unknown; targetOrigin: string }>;
}

interface MockHtmlAudio extends HTMLAudioElement {
  __triggerEnded?: () => void;
}

const ONE_BY_ONE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=',
  'base64',
);

const OBS_TEMPLATE_PATH = path.join(TEMPLATES_DIR, 'obs.html');

function installAudioStubScript(): string {
  return `
    window.__audioLog = [];
    window.__audioInstances = [];
    const NativeAudio = window.Audio;
    window.Audio = class extends NativeAudio {
      constructor(src = '') {
        super();
        if (src) this.src = src;
        this._mockPaused = true;
        window.__audioInstances.push(this);
      }
      get paused() {
        return this._mockPaused;
      }
      play() {
        this._mockPaused = false;
        window.__audioLog.push({ src: this.src, volume: this.volume, paused: false });
        return Promise.resolve();
      }
      pause() {
        this._mockPaused = true;
      }
      __triggerEnded() {
        this._mockPaused = true;
        this.dispatchEvent(new Event('ended'));
      }
    };
  `;
}

async function setupObsRoutes(canvas: CanvasHarness, page: Page): Promise<void> {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.addInitScript({ content: installAudioStubScript() });

  const obsHtml = await fs.promises.readFile(OBS_TEMPLATE_PATH, 'utf-8');

  await canvas.serveRoute(
    (url: URL) =>
      url.origin === CANVAS_ORIGIN &&
      (url.pathname === '/obs' || url.pathname.startsWith('/obs/')),
    async (route: Route) => {
      await route.fulfill({ contentType: 'text/html; charset=utf-8', body: obsHtml });
    },
  );

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/latest',
    async (route: Route) => {
      await route.fulfill({
        json: {
          latest: '/image.png?v=1',
          time: 1,
          effect: 'none',
          narration: 'Initial scene.',
        },
      });
    },
  );

  await canvas.serveRoute(
    (url: URL) =>
      url.origin === CANVAS_ORIGIN &&
      (url.pathname === '/image.png' ||
        url.pathname === '/favicon.png' ||
        url.pathname === '/stamp.png'),
    async (route: Route) => {
      await route.fulfill({
        contentType: 'image/png',
        body: ONE_BY_ONE_PNG,
        headers: { 'Access-Control-Allow-Origin': '*' },
      });
    },
  );

  await canvas.serveRoute('https://fonts.googleapis.com/**', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'text/css; charset=utf-8', body: '' });
  });

  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname.endsWith('.mp3'),
    async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'audio/mpeg', body: '' });
    },
  );

  await page.routeWebSocket('**/ws/**', (ws: WebSocketRoute) => {
    if (ws.url().includes('/ws/canvas-state')) {
      ws.send('{"type":"state_ready"}');
    } else {
      ws.send('{"type":"doodle_snapshot","batches":[],"annotations":[],"stamps":[]}');
    }
  });
}

test('initObsCanvas throws when required stage elements are missing', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    html: '<div id="canvas-renderer"></div>',
  });

  const errorMessage = await page.evaluate(async () => {
    const { initObsCanvas } = await window.__importModule<ObsModule>(
      '/static/generated/pages/obs.js',
    );
    try {
      await initObsCanvas();
      return null;
    } catch (error) {
      return error instanceof Error ? error.message : String(error);
    }
  });

  expect(errorMessage).toBe('Required OBS canvas DOM elements are missing.');
});

test('obs handles state_changed, scene_speech_ready, narration-pane wiring, and ignores stale out-of-order /api/latest responses', async ({
  canvas,
  page,
}) => {
  await setupObsRoutes(canvas, page);

  let latestCallCount = 0;
  let releaseFirstFetch: (() => void) | null = null;
  let stateSocket: WebSocketRoute | null = null;

  await canvas.serveRoute('**/api/latest?**', async (route: Route) => {
    latestCallCount += 1;
    if (latestCallCount === 1) {
      await new Promise<void>((resolve) => {
        releaseFirstFetch = resolve;
      });
      await route.fulfill({
        json: {
          latest: '/image.png?v=stale',
          time: 1,
          effect: 'none',
          narration: 'Stale first response.',
          committed_scene_speech_generation: 1,
        },
      });
      return;
    }

    await route.fulfill({
      json: {
        latest: '/image.png?v=fresh',
        time: 2,
        effect: 'none',
        narration: 'Fresh second response.',
        committed_scene_speech_generation: 2,
      },
    });
  });

  await page.routeWebSocket('**/ws/canvas-state?**', (ws: WebSocketRoute) => {
    stateSocket = ws;
    ws.send('{"type":"state_ready"}');
  });

  await page.goto(`${CANVAS_ORIGIN}/obs/stage-1?join_key=KEY42`);
  await expect.poll(() => stateSocket !== null && releaseFirstFetch !== null).toBe(true);

  // Trigger a second /api/latest fetch via state_changed while the first is still in flight.
  stateSocket!.send('{"type":"state_changed","domains":["latest"]}');
  await expect(page.locator('.scene-description')).toHaveText('Fresh second response.');
  await expect(page.locator('#scene-dialogue-overlay')).toHaveClass(/narration-interactive/);
  await expect(page.locator('.scene-description')).toHaveAttribute('tabindex', '0');

  // Releasing the slower first response must NOT overwrite the newer scene.
  releaseFirstFetch!();
  await page.waitForTimeout(50);
  await expect(page.locator('.scene-description')).toHaveText('Fresh second response.');
  const imgSrc = await page.locator('#current-image').getAttribute('src');
  expect(imgSrc).toContain('v=fresh');
  expect(imgSrc).toContain('join_key=KEY42');

  // scene_speech_ready delivers speech audio with join_key appended.
  stateSocket!.send(
    JSON.stringify({
      type: 'scene_speech_ready',
      audio_url: '/voice-line.mp3',
      speech_generation: 2,
    }),
  );
  await page.waitForFunction(() => (window as unknown as ObsTestWindow).__audioLog.length > 0);
  const audioLog = await page.evaluate(() => (window as unknown as ObsTestWindow).__audioLog);
  expect(audioLog[0].src).toBe(`${CANVAS_ORIGIN}/voice-line.mp3?join_key=KEY42`);
});

test('obs recovers from /api/latest HTTP errors and reconnects closed canvas-state socket', async ({
  canvas,
  page,
}) => {
  await setupObsRoutes(canvas, page);

  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      consoleErrors.push(msg.text());
    }
  });

  let latestCalls = 0;
  let stateConnections = 0;
  let firstSocket: WebSocketRoute | null = null;

  await canvas.serveRoute('**/api/latest?**', async (route: Route) => {
    latestCalls += 1;
    if (latestCalls === 1) {
      await route.fulfill({ status: 503, body: 'Service Unavailable' });
      return;
    }
    await route.fulfill({
      json: {
        latest: '/image.png?v=recovered',
        time: 5,
        effect: 'none',
        narration: 'Recovered state.',
      },
    });
  });

  await page.routeWebSocket('**/ws/canvas-state?**', (ws: WebSocketRoute) => {
    stateConnections += 1;
    if (stateConnections === 1) {
      firstSocket = ws;
      return;
    }
    ws.send('{"type":"state_ready"}');
  });

  await page.goto(`${CANVAS_ORIGIN}/obs?theater_id=retry-stage`);
  await expect.poll(() => firstSocket !== null).toBe(true);
  await expect.poll(() => consoleErrors.some((text) => text.includes('503'))).toBe(true);

  // Closing the first state socket triggers reconnect (~1000ms backoff), which fetches latest again.
  firstSocket!.close();
  await expect.poll(() => stateConnections, { timeout: 5000 }).toBeGreaterThanOrEqual(2);
  await expect(page.locator('.scene-description')).toHaveText('Recovered state.');
});

test('obs handles music track rotation, pause/resume, audio=0 mute, volume clamping, and Foundry iframe postMessage bridge', async ({
  canvas,
  page,
}) => {
  await setupObsRoutes(canvas, page);

  let stateSocket: WebSocketRoute | null = null;
  let musicPayload = {
    playlist: 'tavern',
    tracks: ['/track-a.mp3', '/track-b.mp3'],
    time: 1,
    paused: false,
  };

  await canvas.serveRoute('**/api/latest?**', async (route: Route) => {
    await route.fulfill({
      json: {
        latest: '/image.png?v=1',
        time: 1,
        effect: 'none',
        music: musicPayload,
      },
    });
  });

  await page.routeWebSocket('**/ws/canvas-state?**', (ws: WebSocketRoute) => {
    stateSocket = ws;
    ws.send('{"type":"state_ready"}');
  });

  // Load inside a parent-like frame check by verifying audio_volume=1.5 clamps to 1.0.
  await page.goto(`${CANVAS_ORIGIN}/obs/music-stage?join_key=J1&audio_volume=1.5`);
  await page.waitForFunction(() => (window as unknown as ObsTestWindow).__audioLog.length >= 1);

  const initialAudio = await page.evaluate(() => {
    const win = window as unknown as ObsTestWindow;
    return {
      first: win.__audioLog[0],
      paused: win.__audioInstances[0].paused,
    };
  });
  expect(initialAudio.first.volume).toBe(1);
  expect(initialAudio.first.src).toContain('join_key=J1');
  expect(initialAudio.paused).toBe(false);

  // Track ended advances to the other track in the playlist.
  await page.evaluate(() => {
    const win = window as unknown as ObsTestWindow;
    (win.__audioInstances[0] as MockHtmlAudio).__triggerEnded?.();
  });
  await page.waitForFunction(() => (window as unknown as ObsTestWindow).__audioLog.length >= 2);
  const rotatedAudio = await page.evaluate(() => (window as unknown as ObsTestWindow).__audioLog);
  expect(rotatedAudio[1].src).not.toBe(rotatedAudio[0].src);

  // Foundry pause-audio / resume-audio messages control bgMusic.
  await page.evaluate(() => {
    window.postMessage({ source: 'narratron-buddy-foundry', action: 'pause-audio' }, '*');
  });
  expect(
    await page.evaluate(() => (window as unknown as ObsTestWindow).__audioInstances[0].paused),
  ).toBe(true);

  await page.evaluate(() => {
    window.postMessage({ source: 'narratron-buddy-foundry', action: 'resume-audio' }, '*');
  });
  expect(
    await page.evaluate(() => (window as unknown as ObsTestWindow).__audioInstances[0].paused),
  ).toBe(false);

  // Clearing playlist stops and clears bgMusic.
  musicPayload = { playlist: '', tracks: [], time: 2, paused: false };
  stateSocket!.send('{"type":"state_changed","domains":["latest"]}');
  await expect
    .poll(async () =>
      page.evaluate(() => ({
        paused: (window as unknown as ObsTestWindow).__audioInstances[0].paused,
        hasSrc: (window as unknown as ObsTestWindow).__audioInstances[0].hasAttribute('src'),
      })),
    )
    .toEqual({ paused: true, hasSrc: false });
});

test('obs relays Alt+O hide-overlay shortcut when embedded in an iframe', async ({
  canvas,
  page,
}) => {
  await setupObsRoutes(canvas, page);

  await canvas.mount({
    urlPath: '/foundry-host',
    html: '<iframe id="obs-frame" src="/obs/iframe-stage" style="width:800px;height:450px;"></iframe>',
    moduleScripts: [
      `window.__hostMessages = [];
       window.addEventListener('message', (event) => {
         window.__hostMessages.push(event.data);
       });`,
    ],
  });

  const frame = page.frameLocator('#obs-frame');
  await expect(frame.locator('.scene-description')).toHaveText('Initial scene.');

  await frame.locator('.scene-description').focus();
  await page.keyboard.press('Alt+KeyO');

  await expect
    .poll(async () =>
      page.evaluate(
        () => (window as unknown as { __hostMessages: unknown[] }).__hostMessages,
      ),
    )
    .toEqual([{ source: 'narratron-buddy-obs', action: 'hide-overlay' }]);
});

test('obs processes doodle WebSocket draw, draw_batch, text, stamp ordering, select_stamp, remove_stamp, clear, and page filtering', async ({
  canvas,
  page,
}) => {
  await setupObsRoutes(canvas, page);

  let doodleWs: WebSocketRoute | null = null;
  await canvas.serveRoute('**/api/latest?**', async (route: Route) => {
    await route.fulfill({
      json: {
        latest: '/image.png?v=1',
        time: 1,
        effect: 'none',
        doodle_page_id: 'page-1',
        doodles_enabled: true,
      },
    });
  });

  await canvas.serveRoute('**/api/doodles?**', async (route: Route) => {
    await route.fulfill({
      json: {
        type: 'doodle_snapshot',
        page_id: 'page-1',
        enabled: true,
        batches: [],
        annotations: [],
        stamps: [],
      },
    });
  });

  await page.routeWebSocket('**/ws/doodle?**', (ws: WebSocketRoute) => {
    doodleWs = ws;
    ws.send(JSON.stringify({ type: 'doodle_snapshot', page_id: 'page-1', enabled: true, batches: [], annotations: [], stamps: [] }));
  });

  await page.goto(`${CANVAS_ORIGIN}/obs/doodle-stage`);
  await expect.poll(() => doodleWs !== null).toBe(true);
  await expect(page.locator('#current-image')).toHaveJSProperty('naturalWidth', 1);

  const blankUrl = await page
    .locator('#doodle-canvas')
    .evaluate((el: HTMLCanvasElement) => el.toDataURL());

  // Messages targeting another page_id are ignored.
  doodleWs!.send(
    JSON.stringify({
      type: 'text',
      page_id: 'other-page',
      id: 'ignored-text',
      text: 'Wrong page',
      x: 0.2,
      y: 0.2,
    }),
  );
  await page.waitForTimeout(40);
  await expect(page.locator('.canvas-text-annotation')).toHaveCount(0);

  // Add draw segment, draw_batch, text, and two stamps.
  doodleWs!.send(
    JSON.stringify({
      type: 'draw',
      page_id: 'page-1',
      x0: 0.1,
      y0: 0.1,
      x1: 0.4,
      y1: 0.4,
      color: '#22c55e',
      size: 6,
    }),
  );
  doodleWs!.send(
    JSON.stringify({
      type: 'draw_batch',
      page_id: 'page-1',
      points: [0.2, 0.2, 0.6, 0.2, 0.6, 0.6],
      color: '#38bdf8',
      size: 4,
    }),
  );
  doodleWs!.send(
    JSON.stringify({
      type: 'text',
      page_id: 'page-1',
      id: 'txt-1',
      text: 'Obs Clue',
      x: 0.5,
      y: 0.5,
      color: '#ffffff',
      size: 28,
    }),
  );
  doodleWs!.send(
    JSON.stringify({
      type: 'stamp',
      page_id: 'page-1',
      id: 'stamp-a',
      stamp_id: 'sa',
      user_id: 'u1',
      url: '/stamp.png',
      x: 0.3,
      y: 0.3,
      size: 60,
    }),
  );
  doodleWs!.send(
    JSON.stringify({
      type: 'stamp',
      page_id: 'page-1',
      id: 'stamp-b',
      stamp_id: 'sb',
      user_id: 'u2',
      url: '/stamp.png',
      x: 0.7,
      y: 0.3,
      size: 60,
    }),
  );

  await expect(page.locator('.canvas-stamp-annotation')).toHaveCount(2);
  const drawnUrl = await page
    .locator('#doodle-canvas')
    .evaluate((el: HTMLCanvasElement) => el.toDataURL());
  expect(drawnUrl).not.toBe(blankUrl);

  // select_stamp brings stamp-a to the end of #canvas-stamp-layer.
  doodleWs!.send(JSON.stringify({ type: 'select_stamp', page_id: 'page-1', id: 'stamp-a' }));
  await expect
    .poll(async () =>
      page
        .locator('#canvas-stamp-layer .canvas-stamp-annotation')
        .evaluateAll((nodes) => nodes.map((node) => (node as HTMLElement).dataset.annotationId)),
    )
    .toEqual(['stamp-b', 'stamp-a']);

  // remove_stamp removes stamp-b.
  doodleWs!.send(JSON.stringify({ type: 'remove_stamp', page_id: 'page-1', id: 'stamp-b' }));
  await expect(page.locator('#canvas-stamp-layer .canvas-stamp-annotation')).toHaveCount(1);

  // doodles_toggle false clears visible canvas, true restores.
  doodleWs!.send(JSON.stringify({ type: 'doodles_toggle', page_id: 'page-1', enabled: false }));
  await expect
    .poll(async () =>
      page.locator('#doodle-canvas').evaluate((el: HTMLCanvasElement) => el.toDataURL()),
    )
    .toBe(blankUrl);

  doodleWs!.send(JSON.stringify({ type: 'doodles_toggle', page_id: 'page-1', enabled: true }));
  await expect
    .poll(async () =>
      page.locator('#doodle-canvas').evaluate((el: HTMLCanvasElement) => el.toDataURL()),
    )
    .not.toBe(blankUrl);

  // clear removes all doodle actions and stamps.
  doodleWs!.send(JSON.stringify({ type: 'clear', page_id: 'page-1' }));
  await expect(page.locator('#canvas-stamp-layer .canvas-stamp-annotation')).toHaveCount(0);
});

test('obs clip=1 mode activates capture UI and surfaces unsupported-browser recording errors', async ({
  canvas,
  page,
}) => {
  await setupObsRoutes(canvas, page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {},
    });
  });

  await page.goto(`${CANVAS_ORIGIN}/obs/clip-stage?clip=1`);
  await expect(page).toHaveTitle('Narratron Clip Stage');
  await expect(page.locator('#clip-capture-panel')).toHaveClass(/active/);

  await page.locator('#clip-capture-start').click();
  await expect(page.locator('#clip-recording-bar')).toHaveClass(/active/);
  await expect(page.locator('#clip-recording-time')).toHaveText(
    'This browser cannot create a clip. Try a current desktop browser.',
  );
});

test('obs plays triframe and silent looping video animations via playCanvasAnimation and cleans up on static scenes', async ({
  canvas,
  page,
}) => {
  await page.addInitScript(() => {
    Object.defineProperty(HTMLMediaElement.prototype, 'src', {
      configurable: true,
      get() {
        return this.getAttribute('data-test-src') || '';
      },
      set(url: string) {
        this.setAttribute('data-test-src', url);
      },
    });
    (window as unknown as { __obsVideoPlays: number }).__obsVideoPlays = 0;
    HTMLMediaElement.prototype.play = async function (): Promise<void> {
      (window as unknown as { __obsVideoPlays: number }).__obsVideoPlays += 1;
    };
    HTMLMediaElement.prototype.pause = function (): void {};
    HTMLMediaElement.prototype.load = function (): void {};
  });

  await setupObsRoutes(canvas, page);

  let stateSocket: WebSocketRoute | null = null;
  let latestPayload: Record<string, unknown> = {
    latest: '/image.png?v=1',
    time: 1,
    effect: 'none',
    narration: 'Initial scene.',
  };

  await canvas.serveRoute('**/api/latest?**', async (route: Route) => {
    await route.fulfill({ json: latestPayload });
  });

  await page.routeWebSocket('**/ws/canvas-state?**', (ws: WebSocketRoute) => {
    stateSocket = ws;
    ws.send('{"type":"state_ready"}');
  });

  await page.goto(`${CANVAS_ORIGIN}/obs/anim-stage?join_key=OBSKEY`);
  await expect.poll(() => stateSocket).not.toBeNull();

  // 1. Triframe animation hides static image/background layers and passes join_key.
  latestPayload = {
    latest: '/image.png?v=1',
    time: 2,
    effect: 'none',
    narration: 'Lantern loop',
    animation: {
      type: 'triframe',
      frames: ['/image.png?f=1', '/image.png?f=2', '/image.png?f=3'],
      frame_duration_ms: 100,
      crossfade_duration_ms: 30,
    },
  };
  stateSocket!.send('{"type":"state_changed","domains":["latest"]}');
  await expect(page.locator('.scene-description')).toHaveText('Lantern loop');
  await expect(page.locator('#current-image')).toHaveCSS('opacity', '0');

  // 2. Video animation renders a silent, looping <video> with join_key appended.
  latestPayload = {
    latest: '/image.png?v=1',
    time: 3,
    effect: 'none',
    narration: 'Soaring griffin',
    animation: {
      type: 'video',
      id: 'flight_123',
      video_url: '/video.mp4',
      video_duration_seconds: 5,
      loop: true,
      muted: true,
    },
  };
  stateSocket!.send('{"type":"state_changed","domains":["latest"]}');
  await expect(page.locator('#image-container video')).toHaveCount(1);
  expect(
    await page.locator('#image-container video').evaluate((video: HTMLVideoElement) => ({
      src: video.getAttribute('data-test-src'),
      muted: video.muted,
      defaultMuted: video.defaultMuted,
      volume: video.volume,
      loop: video.loop,
      mutedAttr: video.hasAttribute('muted'),
    })),
  ).toEqual({
    src: '/video.mp4?join_key=OBSKEY',
    muted: true,
    defaultMuted: true,
    volume: 0,
    loop: true,
    mutedAttr: true,
  });

  await page.locator('#image-container video').dispatchEvent('loadeddata');
  await page.locator('#image-container video').dispatchEvent('ended');
  await expect
    .poll(() =>
      page.evaluate(() => (window as unknown as { __obsVideoPlays: number }).__obsVideoPlays),
    )
    .toBe(2);

  // 3. Returning to a static image detaches the <video>.
  latestPayload = {
    latest: '/image.png?v=static',
    time: 4,
    effect: 'none',
    narration: 'Still scene.',
  };
  stateSocket!.send('{"type":"state_changed","domains":["latest"]}');
  await expect(page.locator('#image-container video')).toHaveCount(0);
});
