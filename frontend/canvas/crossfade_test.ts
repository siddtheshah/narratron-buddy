import { expect, test } from '../../testing/ui/canvas_fixtures';

// Minimal valid 1x1 solid PNGs so <img> decode & canvas drawImage succeed cleanly offline.
const RED_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==',
  'base64',
);
const BLUE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPj/HwADBwIAMCbHYQAAAABJRU5ErkJggg==',
  'base64',
);

interface AudioMockWindow extends Window {
  __audioLog: { src: string; playCalls: number; pauseCalls: number }[];
}

test('canvas transitions between images using crossfade and fade payloads', async ({
  canvas,
  page,
}) => {
  await canvas.serveRoute('/static/img/scene-1.png**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'image/png', body: RED_PNG });
  });
  await canvas.serveRoute('/static/img/scene-2.png**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'image/png', body: BLUE_PNG });
  });
  await canvas.serveRoute('/static/img/scene-3.png**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'image/png', body: RED_PNG });
  });

  let latestPayload: Record<string, unknown> = {
    latest: '/static/img/scene-1.png',
    time: 1,
    transition: 'none',
    effect: 'gleam3',
    history: [{ url: '/static/img/scene-1.png', page_id: 'p1', time: 1, transition: 'none', effect: 'gleam3' }],
  };
  let canvasStateSocket: { send(data: string): void } | null = null;

  await canvas.openCanvas({
    isOrator: true,
    apiRoutes: {
      '/api/latest': async (route) => {
        await route.fulfill({ json: latestPayload });
      },
    },
    onWebSocket: (ws) => {
      if (ws.url().includes('/ws/canvas-state')) {
        canvasStateSocket = ws;
      }
    },
  });

  const currentImg = page.locator('#current-image');
  await expect(currentImg).toHaveClass(/t-crossfade/);
  await expect(currentImg).toHaveClass(/loaded/);
  await expect(currentImg).toHaveAttribute('src', /\/static\/img\/scene-1\.png/);
  await expect(page.locator('#ghost-image')).toBeAttached();

  // Crossfade to scene-2: image updates and effect frame uses 2000ms crossfade transition.
  latestPayload = {
    latest: '/static/img/scene-2.png',
    time: 2,
    transition: 'crossfade',
    effect: 'gleam3',
    history: [
      { url: '/static/img/scene-1.png', page_id: 'p1', time: 1 },
      { url: '/static/img/scene-2.png', page_id: 'p2', time: 2, transition: 'crossfade', effect: 'gleam3' },
    ],
  };
  canvasStateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
  await expect(currentImg).toHaveAttribute('src', /\/static\/img\/scene-2\.png/);
  await expect
    .poll(() =>
      page.evaluate(() => {
        const frame = document.querySelector<HTMLElement>('.canvas-image-effect-frame');
        return frame?.style.transition || '';
      }),
    )
    .toContain('2000ms');

  // Fade transition to scene-3 uses the shorter fade transition duration instead of 2000ms.
  latestPayload = {
    latest: '/static/img/scene-3.png',
    time: 3,
    transition: 'fade',
    effect: 'gleam3',
    history: [
      { url: '/static/img/scene-1.png', page_id: 'p1', time: 1 },
      { url: '/static/img/scene-2.png', page_id: 'p2', time: 2 },
      { url: '/static/img/scene-3.png', page_id: 'p3', time: 3, transition: 'fade', effect: 'gleam3' },
    ],
  };
  canvasStateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
  await expect(currentImg).toHaveAttribute('src', /\/static\/img\/scene-3\.png/);
  await expect
    .poll(() =>
      page.evaluate(() => {
        const frame = document.querySelector<HTMLElement>('.canvas-image-effect-frame');
        return frame?.style.transition || '';
      }),
    )
    .toContain('1500ms');
});

test('tool_activity indicators, wandering pulse animation, and white italic scene narration render via canvas.js', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({
    isOrator: true,
    apiRoutes: {
      '/api/latest': {
        narration: 'A silver lantern sways above the wet cobblestones.',
        agent_thought: { text: 'wandering', highlighted: false },
        tool_activity: {
          image_generating: true,
          user_action_processing: true,
          dice_rolling: true,
          music_generating: true,
          reference_generating: true,
        },
      },
    },
  });

  for (const id of [
    '#agent-drawing-indicator',
    '#agent-writing-indicator',
    '#agent-dice-indicator',
    '#agent-music-indicator',
    '#agent-reference-indicator',
    '#agent-disconnected-indicator',
  ]) {
    await expect(page.locator(id)).toHaveClass(/active/);
    await expect(page.locator(id)).toHaveAttribute('aria-hidden', 'false');
  }

  const disconnectedAnim = await page
    .locator('#agent-disconnected-indicator')
    .evaluate((el) => getComputedStyle(el).animationName);
  expect(disconnectedAnim).toBe('agentDisconnectedPulse');

  const narrationEl = page.locator('.scene-narration, .scene-description, .narration').first();
  await expect(narrationEl).toContainText('A silver lantern sways above the wet cobblestones.');
  const narrationStyles = await narrationEl.evaluate((el) => {
    const cs = getComputedStyle(el);
    return { fontStyle: cs.fontStyle, color: cs.color };
  });
  expect(narrationStyles.fontStyle).toBe('italic');
  expect(narrationStyles.color).toBe('rgb(255, 255, 255)');
});

test('canvas drops superseded out-of-order /api/latest responses', async ({ canvas, page }) => {
  await canvas.serveRoute('/static/img/fresh-scene.png**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'image/png', body: BLUE_PNG });
  });

  let requestCount = 0;
  let releaseFirstRequest: (() => void) | null = null;
  let canvasStateSocket: { send(data: string): void } | null = null;

  await canvas.openCanvas({
    isOrator: true,
    apiRoutes: {
      '/api/latest': async (route) => {
        requestCount += 1;
        if (requestCount === 1) {
          await new Promise<void>((resolve) => {
            releaseFirstRequest = resolve;
          });
          await route.fulfill({
            json: {
              latest: null,
              time: 0,
              narration: 'Stale snapshot that must be ignored',
              history: [],
            },
          });
          return;
        }
        await route.fulfill({
          json: {
            latest: '/static/img/fresh-scene.png',
            time: 42,
            transition: 'none',
            narration: 'Fresh scene wins',
            history: [{ url: '/static/img/fresh-scene.png', page_id: 'fresh', time: 42 }],
          },
        });
      },
    },
    onWebSocket: (ws) => {
      if (ws.url().includes('/ws/canvas-state')) {
        canvasStateSocket = ws;
      }
    },
  });

  await expect.poll(() => requestCount).toBe(1);
  // Trigger a second /api/latest request while the first is still in flight.
  canvasStateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
  await expect(page.locator('#current-image')).toHaveAttribute('src', /\/static\/img\/fresh-scene\.png/);
  await expect(page.locator('.scene-narration, .scene-description, .narration').first()).toContainText(
    'Fresh scene wins',
  );

  // Release the older /api/latest request -> must be ignored without clearing the fresh scene.
  releaseFirstRequest!();
  await page.waitForTimeout(100);
  await expect(page.locator('#current-image')).toHaveAttribute('src', /\/static\/img\/fresh-scene\.png/);
  await expect(page.locator('.scene-narration, .scene-description, .narration').first()).toContainText(
    'Fresh scene wins',
  );
});

test('canvas mute button stops active scene speech and blocks subsequent scene_speech_ready playback', async ({
  canvas,
  page,
}) => {
  await page.addInitScript(() => {
    const win = window as unknown as AudioMockWindow;
    win.__audioLog = [];
    class MockAudio {
      src: string;
      volume = 1;
      paused = true;
      onended: (() => void) | null = null;
      onerror: (() => void) | null = null;
      private listeners = new Map<string, (() => void)[]>();

      constructor(src = '') {
        this.src = src;
        const entry = { src, playCalls: 0, pauseCalls: 0 };
        win.__audioLog.push(entry);
        const origPlay = () => {
          this.paused = false;
          entry.playCalls += 1;
          return Promise.resolve();
        };
        const origPause = () => {
          this.paused = true;
          entry.pauseCalls += 1;
        };
        this.play = origPlay;
        this.pause = origPause;
      }

      play(): Promise<void> {
        return Promise.resolve();
      }
      pause(): void {}
      addEventListener(type: string, cb: () => void): void {
        const list = this.listeners.get(type) ?? [];
        list.push(cb);
        this.listeners.set(type, list);
      }
      removeEventListener(type: string, cb: () => void): void {
        const list = this.listeners.get(type) ?? [];
        this.listeners.set(
          type,
          list.filter((fn) => fn !== cb),
        );
      }
      load(): void {}
    }
    (window as unknown as { Audio: typeof MockAudio }).Audio = MockAudio;
  });

  await canvas.serveRoute('/static/img/scene-1.png**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'image/png', body: RED_PNG });
  });

  let latestPayload: Record<string, unknown> = {
    latest: '/static/img/scene-1.png',
    time: 1,
    narration: 'Speech scene 1',
    committed_scene_speech_generation: 1,
    history: [{ url: '/static/img/scene-1.png', page_id: 'p1', time: 1 }],
  };
  let canvasStateSocket: { send(data: string): void } | null = null;
  await canvas.openCanvas({
    isOrator: true,
    apiRoutes: {
      '/api/latest': async (route) => {
        await route.fulfill({ json: latestPayload });
      },
    },
    onWebSocket: (ws) => {
      if (ws.url().includes('/ws/canvas-state')) {
        canvasStateSocket = ws;
      }
    },
  });

  await expect.poll(() => canvasStateSocket !== null).toBe(true);
  await expect(page.locator('#current-image')).toHaveAttribute('src', /\/static\/img\/scene-1\.png/);
  canvasStateSocket!.send(
    JSON.stringify({
      type: 'scene_speech_ready',
      audio_url: '/static/audio/speech-1.mp3',
      speech_generation: 1,
    }),
  );

  await expect
    .poll(() =>
      page.evaluate(() =>
        (window as unknown as AudioMockWindow).__audioLog.some(
          (entry) => entry.src.includes('speech-1.mp3') && entry.playCalls > 0,
        ),
      ),
    )
    .toBe(true);

  // Clicking #music-mute-btn stops active speech playback.
  await page.locator('#music-mute-btn').click();
  await expect
    .poll(() =>
      page.evaluate(() =>
        (window as unknown as AudioMockWindow).__audioLog.some(
          (entry) => entry.src.includes('speech-1.mp3') && entry.pauseCalls > 0,
        ),
      ),
    )
    .toBe(true);

  // Subsequent scene_speech_ready does not play while muted.
  latestPayload = {
    latest: '/static/img/scene-1.png',
    time: 2,
    narration: 'Speech scene 2',
    committed_scene_speech_generation: 2,
    history: [{ url: '/static/img/scene-1.png', page_id: 'p1', time: 2 }],
  };
  canvasStateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
  canvasStateSocket!.send(
    JSON.stringify({
      type: 'scene_speech_ready',
      audio_url: '/static/audio/speech-2.mp3',
      speech_generation: 2,
    }),
  );
  await page.waitForTimeout(100);
  const playedSecondSpeech = await page.evaluate(() =>
    (window as unknown as AudioMockWindow).__audioLog.some(
      (entry) => entry.src.includes('speech-2.mp3') && entry.playCalls > 0,
    ),
  );
  expect(playedSecondSpeech).toBe(false);
});
