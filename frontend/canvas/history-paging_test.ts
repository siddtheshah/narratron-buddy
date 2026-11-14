import { expect, test } from '../../testing/ui/canvas_fixtures';

type HistoryPagingModule = typeof import('/static/generated/canvas/history-paging.js');
type HistoryPagingController =
  import('/static/generated/canvas/history-paging.js').HistoryPagingController;
type HistoryImageRenderer =
  import('/static/generated/canvas/history-paging.js').HistoryImageRenderer;
type HistoryItem = import('/static/generated/canvas/history-paging.js').HistoryItem;

interface PagingHarnessWindow extends Window {
  historyPaging: HistoryPagingController;
  transitionCalls: { url: string; transition?: string; effect?: string }[];
  playedAnimations: { type: string; urls: string[] }[];
  stopSequenceCalls: number;
  bookmarkRefreshes: number;
  toolInteractionRefreshes: number;
  doodlePagesDisplayed: (string | null)[];
  isOratorFlag: boolean;
  isCanvasSelectionActive?: boolean;
  resolveWebpQualityUrlForMode: HistoryPagingModule['resolveWebpQualityUrlForMode'];
}

const HISTORY_PAGING_MODULE_URL = '/static/generated/canvas/history-paging.js';

const PAGING_HTML = `
  <div id="image-container" style="position: relative; width: 800px; height: 600px;">
    <div id="bg-layer"></div>
    <img id="current-image" alt="" />
    <img id="ghost-image" alt="" />
    <canvas id="doodle-canvas"></canvas>
    <div id="history-paging-controls">
      <button id="page-prev-btn" type="button">Prev</button>
      <span id="page-indicator">1 / 1</span>
      <button id="page-next-btn" type="button">Next</button>
      <button id="page-head-btn" type="button" style="display: none;">Jump to Current
        <span id="new-head-badge" style="display: none;">NEW</span>
      </button>
      <button id="page-sync-btn" type="button">Sync</button>
    </div>
    <button id="prompt-toggle-btn" type="button" style="display: none;">
      <span id="prompt-text"></span>
    </button>
  </div>
  <input id="minimal-mode-input" type="checkbox" />
`;

async function mountHistoryPaging(
  page: Parameters<Parameters<typeof test>[2]>[0]['page'],
  options: { isOrator?: boolean; initialMinimalMode?: boolean } = {},
): Promise<void> {
  await page.evaluate(
    async ({
      moduleUrl,
      isOrator,
      initialMinimalMode,
    }: {
      moduleUrl: string;
      isOrator: boolean;
      initialMinimalMode: boolean;
    }) => {
      if (initialMinimalMode) {
        localStorage.setItem('narratron.canvas.performance.minimalMode', 'true');
      } else {
        localStorage.removeItem('narratron.canvas.performance.minimalMode');
      }
      const { createHistoryPagingController, resolveWebpQualityUrlForMode } =
        await window.__importModule<HistoryPagingModule>(moduleUrl);
      const win = window as unknown as PagingHarnessWindow;
      win.transitionCalls = [];
      win.playedAnimations = [];
      win.stopSequenceCalls = 0;
      win.bookmarkRefreshes = 0;
      win.toolInteractionRefreshes = 0;
      win.doodlePagesDisplayed = [];
      win.isOratorFlag = isOrator;
      win.resolveWebpQualityUrlForMode = resolveWebpQualityUrlForMode;

      const renderer: HistoryImageRenderer = {
        applyTransition(url, transition, effect) {
          win.transitionCalls.push({ url, transition, effect });
        },
        async playLayeredAnimation() {},
        async playVideoAnimation() {},
        async playSequence() {},
        stopSequence() {
          win.stopSequenceCalls += 1;
        },
        setImageEffectsEnabled() {},
      };

      win.historyPaging = createHistoryPagingController({
        theaterId: 'th-paging',
        isOrator: () => win.isOratorFlag,
        imageRenderer: renderer,
        playCanvasAnimation: (_r, animation, resolveUrl) => {
          const urls: string[] = [];
          if (animation.type === 'triframe' && Array.isArray(animation.frames)) {
            for (const f of animation.frames) {
              urls.push(resolveUrl ? resolveUrl(f) : f);
            }
          } else if (animation.type === 'video' && animation.video_url) {
            urls.push(resolveUrl ? resolveUrl(animation.video_url) : animation.video_url);
          } else if (animation.type === 'layered' && Array.isArray(animation.layers)) {
            for (const layer of animation.layers) {
              urls.push(resolveUrl ? resolveUrl(layer.url) : layer.url);
            }
          }
          win.playedAnimations.push({ type: animation.type, urls });
        },
        promptButton: document.getElementById('prompt-toggle-btn'),
        promptTextSpan: document.getElementById('prompt-text'),
        currentImage: document.getElementById('current-image') as HTMLImageElement | null,
        ghostImage: document.getElementById('ghost-image') as HTMLImageElement | null,
        backgroundLayer: document.getElementById('bg-layer'),
        pagingControls: document.getElementById('history-paging-controls'),
        prevButton: document.getElementById('page-prev-btn') as HTMLButtonElement | null,
        nextButton: document.getElementById('page-next-btn') as HTMLButtonElement | null,
        headButton: document.getElementById('page-head-btn'),
        syncButton: document.getElementById('page-sync-btn'),
        pageIndicator: document.getElementById('page-indicator'),
        newHeadBadge: document.getElementById('new-head-badge'),
        imageContainer: document.getElementById('image-container'),
        minimalModeInput: document.getElementById('minimal-mode-input') as HTMLInputElement | null,
        hasDoodleCanvas: () => Boolean(document.getElementById('doodle-canvas')),
        isSelectionActive: () => Boolean(win.isCanvasSelectionActive),
        onRefreshBookmarks: () => {
          win.bookmarkRefreshes += 1;
        },
        onRefreshToolInteraction: () => {
          win.toolInteractionRefreshes += 1;
        },
        onDisplayPageDoodles: (item: HistoryItem) => {
          win.doodlePagesDisplayed.push(item.page_id ?? null);
        },
      });
    },
    {
      moduleUrl: HISTORY_PAGING_MODULE_URL,
      isOrator: Boolean(options.isOrator),
      initialMinimalMode: Boolean(options.initialMinimalMode),
    },
  );
}

test('resolveWebpQualityUrlForMode appends quality=low only when minimalMode is active', async ({
  canvas,
  page,
}) => {
  await canvas.mount({ html: PAGING_HTML });
  await mountHistoryPaging(page);

  const results = await page.evaluate(() => {
    const win = window as unknown as PagingHarnessWindow;
    const fn = win.resolveWebpQualityUrlForMode;
    return {
      offWebp: fn('/static/img/scene.webp', false),
      onWebp: fn('/static/img/scene.webp', true),
      onWebpQuery: fn('/static/img/scene.webp?v=2', true),
      onExplicitQuality: fn('/static/img/scene.webp?quality=high', true),
      onPng: fn('/static/img/scene.png', true),
      onAbsWebp: fn('https://cdn.example.com/scene.webp', true),
    };
  });

  expect(results.offWebp).toBe('/static/img/scene.webp');
  expect(results.onWebp).toBe('/static/img/scene.webp?quality=low');
  expect(results.onWebpQuery).toBe('/static/img/scene.webp?v=2&quality=low');
  expect(results.onExplicitQuality).toBe('/static/img/scene.webp?quality=high');
  expect(results.onPng).toBe('/static/img/scene.png');
  expect(results.onAbsWebp).toBe('https://cdn.example.com/scene.webp?quality=low');
});

test('sliding-window resource manager prunes entries outside HISTORY_WINDOW_RADIUS (5)', async ({
  canvas,
  page,
}) => {
  await canvas.serveRoute('/static/img/page-*.png**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('') });
  });
  await canvas.mount({ html: PAGING_HTML });
  await mountHistoryPaging(page, { isOrator: false });

  const windowStats = await page.evaluate(() => {
    const win = window as unknown as PagingHarnessWindow;
    const history: HistoryItem[] = Array.from({ length: 15 }, (_, idx) => ({
      url: `/static/img/page-${idx}.png`,
      prompt: `Prompt ${idx}`,
      page_id: `p-${idx}`,
      time: idx + 1,
    }));
    win.historyPaging.applyLatestState({
      latest: '/static/img/page-14.png',
      time: 15,
      orator_cursor: 14,
      history,
    });

    // Populate resources 0..14 (including a mock video at index 0 outside [2..12]).
    const rm = win.historyPaging.resourceManager;
    let paused = false;
    let srcRemoved = false;
    for (let i = 0; i < 15; i++) {
      if (i === 0) {
        rm.loadedHistoryResources.set(0, {
          type: 'animation',
          animation: { type: 'video', video_url: '/static/vid.mp4' },
          video: {
            pause() {
              paused = true;
            },
            removeAttribute(name: string) {
              if (name === 'src') srcRemoved = true;
            },
            load() {},
          } as unknown as HTMLVideoElement,
        });
      } else {
        const img = new Image();
        img.src = `/static/img/page-${i}.png`;
        rm.loadedHistoryResources.set(i, { type: 'image', img });
      }
    }

    // Center view on index 7 -> retains [2..12] and unloads [0, 1, 13, 14]
    win.historyPaging.displayHistoryImage(7, 'none');

    return {
      radius: rm.HISTORY_WINDOW_RADIUS,
      retainedKeys: Array.from(rm.loadedHistoryResources.keys()).sort((a, b) => a - b),
      videoCleared: paused && srcRemoved,
    };
  });

  expect(windowStats.radius).toBe(5);
  expect(windowStats.retainedKeys).toEqual([2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
  expect(windowStats.videoCleared).toBe(true);
});

test('viewer paging, new-head badge, Arrow keys, syncToOratorCursor, and prompt pinning', async ({
  canvas,
  page,
}) => {
  await canvas.serveRoute('/static/img/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('') });
  });
  await canvas.mount({ html: PAGING_HTML });
  await mountHistoryPaging(page, { isOrator: false });

  await page.evaluate(() => {
    const win = window as unknown as PagingHarnessWindow;
    win.historyPaging.applyLatestState({
      latest: '/static/img/scene-3.png',
      prompt: 'Third scene prompt',
      time: 3,
      orator_cursor: 2,
      history: [
        { url: '/static/img/scene-1.png', prompt: 'First prompt', page_id: 'p1', time: 1 },
        {
          url: '/static/img/scene-2.png',
          page_id: 'p2',
          time: 2,
          animation: { type: 'triframe', frames: ['/static/img/f1.png'], scene_prompt: 'Anim scene prompt' },
        },
        { url: '/static/img/scene-3.png', prompt: 'Third scene prompt', page_id: 'p3', time: 3 },
      ],
    });
  });

  await expect(page.locator('#page-indicator')).toHaveText('3 / 3');
  await expect(page.locator('#page-head-btn')).toBeHidden();

  // Prompt hover/pin behavior
  const promptBtn = page.locator('#prompt-toggle-btn');
  await expect(promptBtn).toBeVisible();
  await expect(page.locator('#prompt-text')).toHaveText('Third scene prompt');
  await promptBtn.dispatchEvent('mouseenter');
  await expect(promptBtn).toHaveClass(/expanded/);
  await promptBtn.dispatchEvent('mouseleave');
  await expect(promptBtn).not.toHaveClass(/expanded/);
  await promptBtn.click();
  await expect(promptBtn).toHaveClass(/expanded/);
  await promptBtn.dispatchEvent('mouseleave');
  await expect(promptBtn).toHaveClass(/expanded/);

  // Viewer clicks Prev -> enters navigating/paged-back mode, displays animation + scene_prompt
  await page.locator('#page-prev-btn').click();
  await expect(page.locator('#page-indicator')).toHaveText('2 / 3');
  await expect(page.locator('#page-head-btn')).toBeVisible();
  await expect(page.locator('#prompt-text')).toHaveText('Anim scene prompt');

  // ArrowLeft navigates further back to page 1
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator('#page-indicator')).toHaveText('1 / 3');
  await expect(page.locator('#prompt-text')).toHaveText('First prompt');

  // New live scene arrives while viewer is paged back -> new-head-badge shows, view stays at 1/3
  await page.evaluate(() => {
    const win = window as unknown as PagingHarnessWindow;
    win.historyPaging.applyLatestState({
      latest: '/static/img/scene-4.png',
      prompt: 'Fourth scene',
      time: 4,
      orator_cursor: 3,
      history: [
        { url: '/static/img/scene-1.png', prompt: 'First prompt', page_id: 'p1', time: 1 },
        { url: '/static/img/scene-2.png', prompt: 'Second', page_id: 'p2', time: 2 },
        { url: '/static/img/scene-3.png', prompt: 'Third', page_id: 'p3', time: 3 },
        { url: '/static/img/scene-4.png', prompt: 'Fourth scene', page_id: 'p4', time: 4 },
      ],
    });
  });

  await expect(page.locator('#page-indicator')).toHaveText('1 / 4');
  await expect(page.locator('#new-head-badge')).toBeVisible();

  // Clicking Jump to Current / Head button returns to orator_cursor (4/4) and hides badge
  await page.locator('#page-head-btn').click();
  await expect(page.locator('#page-indicator')).toHaveText('4 / 4');
  await expect(page.locator('#page-head-btn')).toBeHidden();
  await expect(page.locator('#new-head-badge')).toBeHidden();
});

test('orator stepping calls orator-action API and minimal mode skips animations', async ({
  canvas,
  page,
}) => {
  const actions: string[] = [];
  await canvas.serveRoute('/api/theaters/th-paging/orator-action', async (route) => {
    const payload = JSON.parse(route.request().postData() || '{}') as { action?: string };
    actions.push(payload.action || '');
    const cursor = payload.action === 'previous_image' ? 0 : 1;
    await route.fulfill({ json: { orator_cursor: cursor } });
  });
  await canvas.serveRoute('/static/img/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('') });
  });

  await canvas.mount({ html: PAGING_HTML });
  await mountHistoryPaging(page, { isOrator: true, initialMinimalMode: false });

  await page.evaluate(() => {
    const win = window as unknown as PagingHarnessWindow;
    win.historyPaging.applyLatestState({
      latest: '/static/img/scene-2.png',
      time: 2,
      orator_cursor: 1,
      history: [
        {
          url: '/static/img/scene-1.png',
          prompt: 'One',
          page_id: 'p1',
          time: 1,
          animation: { type: 'triframe', frames: ['/static/img/frame0.webp'] },
        },
        { url: '/static/img/scene-2.png', prompt: 'Two', page_id: 'p2', time: 2 },
      ],
    });
  });

  await page.locator('#page-prev-btn').click();
  await expect.poll(() => actions).toEqual(['previous_image']);
  await expect(page.locator('#page-indicator')).toHaveText('1 / 2');

  await page.locator('#page-next-btn').click();
  await expect.poll(() => actions).toEqual(['previous_image', 'next_image']);
  await expect(page.locator('#page-indicator')).toHaveText('2 / 2');

  // Enable Minimal Mode -> triframe animation falls back to static still with quality=low
  await page.locator('#minimal-mode-input').check();
  await page.evaluate(() => {
    const win = window as unknown as PagingHarnessWindow;
    win.playedAnimations.length = 0;
    win.transitionCalls.length = 0;
    win.historyPaging.displayHistoryImage(0, 'crossfade');
  });

  const minimalResult = await page.evaluate(() => {
    const win = window as unknown as PagingHarnessWindow;
    return {
      isMinimal: win.historyPaging.isMinimalMode(),
      playedCount: win.playedAnimations.length,
      lastTransitionUrl: win.transitionCalls[win.transitionCalls.length - 1]?.url || '',
    };
  });
  expect(minimalResult.isMinimal).toBe(true);
  expect(minimalResult.playedCount).toBe(0);
  expect(minimalResult.lastTransitionUrl).toBe('/static/img/frame0.webp?quality=low');
});

const DEMO_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==',
  'base64',
);

test('paging bar hover discovery zone and story log toggle inside real canvas.html', async ({
  canvas,
  page,
}) => {
  await canvas.serveRoute('/static/img/demo.png**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'image/png', body: DEMO_PNG });
  });
  await canvas.openCanvas({
    isOrator: true,
    apiRoutes: {
      '/api/latest': {
        latest: '/static/img/demo.png',
        prompt: 'Dungeon hall',
        time: 1,
        orator_cursor: 0,
        history: [{ url: '/static/img/demo.png', prompt: 'Dungeon hall', page_id: 'p1', time: 1 }],
      },
    },
  });

  const controls = page.locator('#history-paging-controls');
  await expect(controls).not.toHaveClass(/hover-discovered/);

  const box = await page.locator('#image-container').boundingBox();
  expect(box).not.toBeNull();
  const centerX = box!.x + box!.width / 2;

  // Move pointer inside top center discovery zone (within 70px top, +/-180px center)
  await page.mouse.move(centerX, box!.y + 30);
  await expect(controls).toHaveClass(/hover-discovered/);

  // Move pointer outside zone -> hover-discovered clears after timeout
  await page.mouse.move(centerX, box!.y + 300);
  await expect(controls).not.toHaveClass(/hover-discovered/);

  // Story Log toggle opens #story-log-inspector cleanly
  await page.locator('#story-log-toggle-btn').click({ force: true });
  await expect(page.locator('#story-log-inspector')).toBeVisible();
});
