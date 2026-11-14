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

type SceneModule = typeof import('/static/generated/canvas/scene.js');

interface PlayedAudioEntry {
  readonly src: string;
  readonly volume: number;
}

interface ObsAudioWindow extends Window {
  playedAudio: PlayedAudioEntry[];
}

const ONE_BY_ONE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=',
  'base64',
);

const OBS_TEMPLATE_PATH = path.join(TEMPLATES_DIR, 'obs.html');

function buildDoodleSnapshot(pageId: string): {
  type: 'doodle_snapshot';
  page_id: string;
  enabled: boolean;
  batches: Array<{ points: number[]; color: string; size: number }>;
} {
  return {
    type: 'doodle_snapshot',
    page_id: pageId,
    enabled: true,
    batches:
      pageId === 'first'
        ? [{ points: [0.1, 0.1, 0.8, 0.8], color: '#ff0000', size: 10 }]
        : [],
  };
}

async function setupObsPage(canvas: CanvasHarness, page: Page): Promise<void> {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.emulateMedia({ reducedMotion: 'reduce' });

  await page.addInitScript(() => {
    const audioWin = window as unknown as ObsAudioWindow;
    audioWin.playedAudio = [];
    const NativeAudio = window.Audio;
    window.Audio = class extends NativeAudio {
      override play(): Promise<void> {
        audioWin.playedAudio.push({ src: this.src, volume: this.volume });
        return Promise.resolve();
      }
    };
  });

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
          latest: '/image.png?variant=original',
          time: 1,
          effect: 'none',
          narration: 'The curtain rises.',
        },
      });
    },
  );

  await canvas.serveRoute(
    (url: URL) =>
      (url.origin === CANVAS_ORIGIN &&
        (url.pathname === '/image.png' || url.pathname === '/favicon.png')) ||
      (url.origin === 'https://fal.media' && url.pathname === '/image.png'),
    async (route: Route) => {
      await route.fulfill({
        contentType: 'image/png',
        body: ONE_BY_ONE_PNG,
        headers: { 'Access-Control-Allow-Origin': '*' },
      });
    },
  );

  await canvas.serveRoute('https://fonts.googleapis.com/**', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/css; charset=utf-8',
      body: '',
    });
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

for (const obsPath of ['/obs?theater_id=test&join_key=JOIN', '/obs/test?join_key=JOIN']) {
  test(`obs starts and renders state (${obsPath})`, async ({ canvas, page }) => {
    await setupObsPage(canvas, page);
    await page.goto(`${CANVAS_ORIGIN}${obsPath}`);

    await expect(page.locator('.scene-description')).toHaveText('The curtain rises.');
    await expect(page.locator('#current-image')).toHaveJSProperty('complete', true);
    await expect(page.locator('#current-image')).toHaveJSProperty('naturalWidth', 1);

    const source = await page.locator('#current-image').getAttribute('src');
    expect(source).not.toBeNull();
    expect(source).toContain('variant=original');
    expect(source).toContain('join_key=JOIN');
  });
}

for (const imageOrigin of ['', 'https://fal.media']) {
  test(`obs renders animation without still image (origin="${imageOrigin}")`, async ({
    canvas,
    page,
  }) => {
    await setupObsPage(canvas, page);

    await canvas.serveRoute('**/api/latest?**', async (route: Route) => {
      await route.fulfill({
        json: {
          time: 1,
          effect: 'none',
          animation: {
            type: 'triframe',
            frames: [
              `${imageOrigin}/image.png?frame=1`,
              `${imageOrigin}/image.png?frame=2`,
            ],
            frame_duration_ms: 10000,
          },
        },
      });
    });

    await page.goto(`${CANVAS_ORIGIN}/obs/test?join_key=JOIN`);
    await expect(page.locator('#current-image')).toHaveJSProperty('naturalWidth', 1);

    const source = await page.locator('#current-image').getAttribute('src');
    expect(source).not.toBeNull();
    expect(source).toContain('frame=1');
    if (imageOrigin) {
      expect(source!.startsWith(imageOrigin)).toBe(true);
      expect(source).not.toContain('join_key');
    } else {
      expect(source).toContain('join_key=JOIN');
    }
  });
}

for (const [query, volume] of [
  ['', 0.8],
  ['&audio_volume=0', 0],
  ['&audio_volume=0.3', 0.3],
] as const) {
  test(`obs applies music volume from query (${query || 'default'})`, async ({
    canvas,
    page,
  }) => {
    await setupObsPage(canvas, page);

    await canvas.serveRoute('**/api/latest?**', async (route: Route) => {
      await route.fulfill({
        json: {
          music: {
            playlist: 'forest',
            tracks: ['/forest.mp3'],
            time: 1,
          },
        },
      });
    });

    await page.goto(`${CANVAS_ORIGIN}/obs/test?join_key=JOIN${query}`);
    await page.waitForFunction(
      () => (window as unknown as ObsAudioWindow).playedAudio.length > 0,
    );

    const firstPlayed = await page.evaluate(
      () => (window as unknown as ObsAudioWindow).playedAudio[0],
    );
    expect(firstPlayed.volume).toBe(volume);
    expect(firstPlayed.src).toContain('join_key=JOIN');
  });
}

test('shared scene preserves speech until scene commit', async ({ canvas, page }) => {
  await setupObsPage(canvas, page);
  await page.goto(`${CANVAS_ORIGIN}/obs/test`);
  await expect(page.locator('.scene-description')).toHaveText('The curtain rises.');

  const result = await page.evaluate(async () => {
    const { createCanvasScene } = await window.__importModule<SceneModule>(
      '/static/generated/canvas/scene.js',
    );
    const element = document.createElement('div');
    const scene = createCanvasScene({
      narrationPane: {
        element,
        prepare: (caption: HTMLElement) => {
          caption.tabIndex = 0;
        },
      },
      resolveUrl: (url: string) => `${url}?join_key=JOIN`,
    });

    scene.enqueueSceneSpeech('/future.mp3', 2);
    scene.updateSceneDialogue([], 'First scene', null, 1);
    const audioWin = window as unknown as ObsAudioWindow;
    const beforeCommit = audioWin.playedAudio.length;

    scene.updateSceneDialogue(
      [
        {
          speaker: 'Guide',
          text: 'Welcome',
          kind: 'thought',
          spans: [{ text: 'Welcome', color: 'red', font: 'fantasy' }],
        },
      ],
      'Next scene',
      null,
      2,
    );
    const afterCommit = audioWin.playedAudio.length;

    scene.updateSceneDialogue([], 'Next scene', null, 2);
    scene.enqueueSceneSpeech('/old.mp3', 1);

    const captionEl = element.querySelector<HTMLElement>('.scene-description');
    return {
      beforeCommit,
      afterCommit,
      finalCount: audioWin.playedAudio.length,
      source: audioWin.playedAudio[0]?.src ?? '',
      caption: captionEl?.textContent ?? '',
      focusable: captionEl?.tabIndex ?? -1,
    };
  });

  expect(result).toEqual({
    beforeCommit: 0,
    afterCommit: 1,
    finalCount: 1,
    source: `${CANVAS_ORIGIN}/future.mp3?join_key=JOIN`,
    caption: 'Next scene',
    focusable: 0,
  });
});

test('obs restores doodles from selected history page', async ({ canvas, page }) => {
  await setupObsPage(canvas, page);

  let selected = 0;
  const sockets: WebSocketRoute[] = [];

  await canvas.serveRoute('**/api/latest?**', async (route: Route) => {
    await route.fulfill({
      json: {
        latest: '/image.png',
        time: selected + 1,
        effect: 'none',
        doodle_page_id: ['first', 'second'][selected],
        orator_cursor: selected,
        doodles_enabled: true,
        history: [{ page_id: 'first' }, { page_id: 'second' }],
      },
    });
  });

  await canvas.serveRoute('**/api/doodles?**', async (route: Route) => {
    const pageId = new URL(route.request().url()).searchParams.get('page_id') ?? '';
    await route.fulfill({ json: buildDoodleSnapshot(pageId) });
  });

  await page.routeWebSocket('**/ws/doodle?**', (ws: WebSocketRoute) => {
    ws.onMessage((message: string | Buffer) => {
      const payload = JSON.parse(
        typeof message === 'string' ? message : message.toString('utf-8'),
      ) as { page_id: string };
      ws.send(JSON.stringify(buildDoodleSnapshot(payload.page_id)));
    });
    ws.send(JSON.stringify(buildDoodleSnapshot('first')));
  });

  await page.routeWebSocket('**/ws/canvas-state?**', (ws: WebSocketRoute) => {
    sockets.push(ws);
    ws.send('{"type":"state_ready"}');
  });

  await page.goto(`${CANVAS_ORIGIN}/obs/test`);
  await expect(page.locator('#current-image')).toHaveJSProperty('naturalWidth', 1);

  const firstDrawing = await page
    .locator('#doodle-canvas')
    .evaluate((doodleCanvas: HTMLCanvasElement) => doodleCanvas.toDataURL());

  selected = 1;
  sockets[0].send('{"type":"state_changed","domains":["latest"]}');
  await page.waitForFunction(
    (previous: string) =>
      (document.getElementById('doodle-canvas') as HTMLCanvasElement).toDataURL() !== previous,
    firstDrawing,
  );

  selected = 0;
  sockets[0].send('{"type":"state_changed","domains":["latest"]}');
  await page.waitForFunction(
    (expected: string) =>
      (document.getElementById('doodle-canvas') as HTMLCanvasElement).toDataURL() === expected,
    firstDrawing,
  );
});

test('canvas scene speech is silenced when muted', async ({ canvas, page }) => {
  await setupObsPage(canvas, page);
  await page.goto(`${CANVAS_ORIGIN}/obs/test`);

  const result = await page.evaluate(async () => {
    const { createCanvasScene } = await window.__importModule<SceneModule>(
      '/static/generated/canvas/scene.js',
    );
    const element = document.createElement('div');
    let muted = true;
    const scene = createCanvasScene({
      narrationPane: { element, prepare: () => {} },
      resolveUrl: (url: string) => url,
      audioEnabled: () => !muted,
    });
    const audioWin = window as unknown as ObsAudioWindow;

    scene.updateSceneDialogue([], 'Scene 1', null, 1);
    scene.enqueueSceneSpeech('/speech1.mp3', 1);
    const playedWhileMuted = audioWin.playedAudio.length;

    muted = false;
    scene.updateSceneDialogue([], 'Scene 2', null, 2);
    scene.enqueueSceneSpeech('/speech2.mp3', 2);
    const playedAfterUnmute = audioWin.playedAudio.length;

    muted = true;
    scene.stopSceneSpeech();
    scene.enqueueSceneSpeech('/speech3.mp3', 2);
    const playedAfterMuteAgain = audioWin.playedAudio.length;

    return { playedWhileMuted, playedAfterUnmute, playedAfterMuteAgain };
  });

  expect(result).toEqual({
    playedWhileMuted: 0,
    playedAfterUnmute: 1,
    playedAfterMuteAgain: 1,
  });
});

test('scene speech interrupt, replay, and next scene', async ({ canvas, page }) => {
  await setupObsPage(canvas, page);
  await page.goto(`${CANVAS_ORIGIN}/obs/test`);

  const result = await page.evaluate(async () => {
    const { createCanvasScene } = await window.__importModule<SceneModule>(
      '/static/generated/canvas/scene.js',
    );
    const scene = createCanvasScene({
      narrationPane: { element: document.createElement('div'), prepare: () => {} },
    });
    const audioWin = window as unknown as ObsAudioWindow;

    scene.updateSceneDialogue([], 'First scene', null, 1);
    scene.enqueueSceneSpeech('/first.mp3', 1);
    scene.stopSceneSpeech();
    scene.enqueueSceneSpeech('/second.mp3', 1);
    const afterInterrupt = audioWin.playedAudio.length;

    scene.replayLatestSpeech();
    const afterReplay = audioWin.playedAudio.length;

    scene.updateSceneDialogue([], 'Next scene', null, 2);
    scene.enqueueSceneSpeech('/next.mp3', 2);

    return {
      afterInterrupt,
      afterReplay,
      finalCount: audioWin.playedAudio.length,
      lastSource: audioWin.playedAudio.at(-1)?.src ?? '',
    };
  });

  expect(result).toEqual({
    afterInterrupt: 1,
    afterReplay: 2,
    finalCount: 3,
    lastSource: `${CANVAS_ORIGIN}/next.mp3`,
  });
});
