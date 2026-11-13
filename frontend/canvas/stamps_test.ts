import type { Locator, Page, WebSocketRoute } from '@playwright/test';
import { expect, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';
import type { DoodleAnnotation, DoodleRenderer } from './doodle-renderer.js';
import type { CanvasCursorStore } from './cursor-state.js';
import type {
  CanvasStampsController,
  StampCanvasItem,
  StampItem,
  StampUser,
} from './stamps.js';

type StampsModule = typeof import('./stamps.js');
type RendererModule = typeof import('./doodle-renderer.js');
type CursorStateModule = typeof import('./cursor-state.js');

interface SentDoodleMessage {
  type: string;
  id?: string;
  stamp_id?: string | number;
  x?: number;
  y?: number;
  size?: number;
  rotation?: number;
  enabled?: boolean;
  client_message_id?: string;
}

interface StampTestHarness {
  controller: CanvasStampsController;
  renderer: DoodleRenderer;
  cursorState: CanvasCursorStore;
  actions: StampCanvasItem[];
  sent: SentDoodleMessage[];
  setPagedBack(value: boolean): void;
  setContributorPermission(value: boolean): void;
  setOrator(value: boolean): void;
  setOwner(value: boolean): void;
  setDoodlesEnabled(value: boolean): void;
  getDoodlesEnabled(): boolean;
  setCurrentUser(user: StampUser | null): void;
  setTheaterId(theaterId: string): void;
  setCachedUserStamps(stamps: StampItem[] | null): void;
  setCachedTheaterStamps(stamps: StampItem[]): void;
  stampNameFromPrompt(prompt: string): string;
  isSameStampKind(a: StampCanvasItem | null, b: StampCanvasItem | null): boolean;
}

declare global {
  interface Window {
    stampHarness: StampTestHarness;
    generationRequests?: Array<{ url: string; method?: string; body?: string | null }>;
    generationResolvers?: Array<(value: { ok: boolean; json: () => Promise<unknown> }) => void>;
    theaterStampsGetResolvers?: Array<(stamps: StampItem[]) => void>;
  }
}

const TINY_STAMP_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" rx="6" fill="#6366f1"/></svg>';

async function serveMockStampAssets(canvas: CanvasHarness): Promise<void> {
  await canvas.serveRoute(
    (url) =>
      url.pathname.startsWith('/api/stamps/') ||
      (url.pathname.startsWith('/theaters/') && url.pathname.includes('/stamps/')),
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'image/svg+xml',
        body: TINY_STAMP_SVG,
      });
    },
  );
}

interface MountStampHarnessOptions {
  withFullGenerationFooter?: boolean;
}

async function mountStampHarness(
  canvas: CanvasHarness,
  page: Page,
  options: MountStampHarnessOptions = {},
): Promise<void> {
  await serveMockStampAssets(canvas);
  const footerHtml = options.withFullGenerationFooter
    ? `
      <div id="stamp-generation-status" role="status" aria-live="polite" hidden>Uses image generation credits. Saved to this theater for everyone to use.</div>
      <div class="stamp-manager-footer">
        <form id="stamp-generation-form" class="stamp-generation-form" hidden>
          <textarea id="stamp-generation-prompt" maxlength="4000" rows="2" wrap="soft"
            aria-label="Stamp image prompt" aria-keyshortcuts="Enter" required
            placeholder="describe a stamp and press (enter)" autocomplete="off"></textarea>
        </form>
      </div>`
    : `<div class="stamp-manager-footer"></div>`;

  await canvas.mount({
    viewport: { width: 1400, height: 800 },
    stylesheets: ['/static/css/chat.css', '/static/css/canvas.css'],
    inlineStyles: [
      `body { margin: 0; padding: 0; display: block; }
       #image-container { position: relative; width: 1000px; height: 600px; flex: none; border: 0; border-radius: 0; }
       #doodle-canvas { width: 100%; height: 100%; display: block; }
       #chat-sidebar { position: relative; width: 250px; height: 600px; display: flex; flex-direction: column; }`,
    ],
    html: `
      <div style="display:flex;">
        <div id="image-container">
          <canvas id="doodle-canvas"></canvas>
          <div id="canvas-stamp-layer"></div>
        </div>
        <div id="chat-sidebar">
          <div id="chat-identity" class="chat-identity">
            <a href="/profile" id="stamp-manager-profile-link" target="_blank" rel="noopener" class="panel-btn chat-profile-link" style="display:none;">Profile</a>
          </div>
          <div id="chat-user-bar" class="canvas-panel-tabs" role="tablist" aria-label="Canvas panel">
            <button type="button" id="chat-show-chat-btn" role="tab" aria-selected="true" aria-controls="panel-body">Chat</button>
            <button type="button" id="chat-open-stamps-btn" role="tab" aria-selected="false" aria-controls="stamp-manager-pane">Assets</button>
          </div>
          <div id="panel-body" role="tabpanel" style="display: flex; flex-direction: column; flex: 1;">
            <div id="chat-messages" style="flex:1;">Chat messages</div>
            <form id="chat-form" class="canvas-chat-composer">
              <textarea id="chat-input" rows="2"></textarea>
            </form>
          </div>
          <div id="stamp-manager-pane" role="tabpanel" style="display:none; flex-direction: column; flex: 1; min-height: 0;">
            <div class="asset-search-bar">
              <input id="asset-search" type="search" aria-label="Search assets" placeholder="Search assets…" autocomplete="off">
              <span id="stamp-manager-count" class="stamp-manager-count-badge">0/10</span>
            </div>
            <div id="asset-search-empty" role="status" hidden>No matching assets.</div>
            <div id="stamp-manager-permission-banner" class="stamp-manager-permission-banner non-contributor"></div>
            <div id="stamp-manager-body" class="stamp-manager-body">
              <div id="asset-generation-pending" class="scenes-grid" hidden></div>
              <div id="stamp-manager-grid" class="stamp-grid"></div>
              <div id="stamp-manager-empty" class="stamp-manager-empty" style="display:none;">
                <div id="stamp-empty-message">Empty</div>
                <a href="/profile" id="stamp-manager-empty-profile-btn" target="_blank">Go to Profile</a>
              </div>
            </div>
            ${footerHtml}
          </div>
        </div>
      </div>`,
  });

  await page.evaluate(async () => {
    const stampsMod = await window.__importModule<StampsModule>('/static/generated/canvas/stamps.js');
    const rendererMod = await window.__importModule<RendererModule>('/static/generated/canvas/doodle-renderer.js');
    const cursorMod = await window.__importModule<CursorStateModule>('/static/generated/canvas/cursor-state.js');

    const canvasEl = document.getElementById('doodle-canvas') as HTMLCanvasElement;
    let isPagedBack = false;
    let contributorPermission = true;
    let activeOrator = false;
    let isOwner = false;
    let doodlesEnabled = true;
    let theaterId = 'stage';
    let currentUser: StampUser | null = { id: 5, username: 'Alice' };
    const doodleActions: StampCanvasItem[] = [];
    const sent: SentDoodleMessage[] = [];
    let controller!: CanvasStampsController;

    const sendDoodleMessage = (msg: Record<string, unknown>): void => {
      if (!msg.client_message_id) {
        msg.client_message_id = `msg-${sent.length}`;
      }
      sent.push(msg as unknown as SentDoodleMessage);
    };

    const renderer = rendererMod.createDoodleRenderer({
      canvas: canvasEl,
      stampLayer: document.getElementById('canvas-stamp-layer'),
      canMoveStamp: () => contributorPermission && !isPagedBack,
      onMoveStamp: (action, previous, commit) =>
        controller.moveStampAnnotation(action, previous, commit),
      onRemoveStamp: (action) => controller.removeStampAnnotation(action),
      onSelectStamp: (action) => controller.selectStampAnnotation(action),
    });

    controller = stampsMod.createCanvasStampsController({
      canvas: canvasEl,
      imgContainer: document.getElementById('image-container'),
      getTheaterId: () => theaterId,
      getCurrentUser: () => currentUser,
      setCurrentUser: (user) => {
        currentUser = user;
      },
      getMyUserId: () => 'Alice',
      hasContributorPermission: () => contributorPermission,
      isOrator: () => activeOrator,
      isOwner: () => isOwner,
      isDoodlesEnabled: () => doodlesEnabled,
      isPagedBack: () => isPagedBack,
      enableDoodles: () => {
        doodlesEnabled = true;
        canvasEl.style.display = 'block';
        sendDoodleMessage({ type: 'toggle_doodles', enabled: true });
      },
      selectSelectCursor: () => cursorMod.canvasCursorState.select({ type: 'select' }),
      getDoodleActions: () => doodleActions,
      redrawDoodles: () => renderer.redraw(doodleActions as DoodleAnnotation[]),
      sendDoodleMessage,
      getActiveTab: () => 'stamps',
    });

    // Stub loadUserStamps network call when opening the panel in offline unit tests unless a test mocks fetch.
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url === '/api/users/me/stamps') {
        return new Response(JSON.stringify(controller.getCachedUserStamps() ?? []), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url === '/api/theaters/stage/stamps') {
        return new Response(JSON.stringify(controller.getCachedTheaterStamps()), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return originalFetch(input, init);
    };

    const syncCanvasPointerEvents = (): void => {
      const hasTool = cursorMod.canvasCursorState.current.type !== 'select';
      canvasEl.style.pointerEvents = hasTool && doodlesEnabled && !isPagedBack ? 'auto' : 'none';
    };
    cursorMod.canvasCursorState.subscribe(syncCanvasPointerEvents);
    syncCanvasPointerEvents();

    renderer.resize(doodleActions as DoodleAnnotation[]);

    window.stampHarness = {
      controller,
      renderer,
      cursorState: cursorMod.canvasCursorState,
      actions: doodleActions,
      sent,
      setPagedBack: (value) => {
        isPagedBack = value;
        syncCanvasPointerEvents();
      },
      setContributorPermission: (value) => {
        contributorPermission = value;
      },
      setOrator: (value) => {
        activeOrator = value;
      },
      setOwner: (value) => {
        isOwner = value;
        controller.updateGenerationPermission();
      },
      setDoodlesEnabled: (value) => {
        doodlesEnabled = value;
        canvasEl.style.display = value ? 'block' : 'none';
        syncCanvasPointerEvents();
      },
      getDoodlesEnabled: () => doodlesEnabled,
      setCurrentUser: (user) => {
        currentUser = user;
      },
      setTheaterId: (nextTheaterId) => {
        theaterId = nextTheaterId;
      },
      setCachedUserStamps: (stamps) => {
        controller.setCachedUserStamps(stamps);
      },
      setCachedTheaterStamps: (stamps) => {
        controller.setCachedTheaterStamps(stamps);
      },
      stampNameFromPrompt: stampsMod.stampNameFromPrompt,
      isSameStampKind: stampsMod.isSameStampKind,
    };
  });
}

test('canvas.html and chat.css stamp wiring and module exports', async ({ canvas, page }) => {
  await serveMockStampAssets(canvas);
  await canvas.openCanvas({
    viewport: { width: 1400, height: 800 },
    user: { id: 5, username: 'Alice' },
    apiRoutes: {
      '/api/users/me/stamps': [],
      '/api/theaters/stage/stamps': [],
    },
  });

  // Persistent asset tab & stamp manager pane in canvas.html
  await expect(page.locator('#chat-open-stamps-btn')).toBeAttached();
  await expect(page.locator('.canvas-panel-tabs')).toBeAttached();
  await expect(page.locator('#stamp-manager-pane')).toBeAttached();
  await expect(page.locator('#close-stamp-manager-btn')).toHaveCount(0);
  await expect(page.locator('#stamp-manager-back-chat-btn')).toHaveCount(0);
  await expect(page.locator('#stamp-manager-grid')).toBeAttached();
  await expect(page.locator('#stamp-manager-profile-link')).toHaveAttribute('href', '/users/Alice');
  await expect(page.locator('#stamp-manager-empty-profile-btn')).toHaveAttribute('href', '/users/Alice');
  await expect(page.locator('#canvas-stamp-layer')).toBeAttached();

  // CSS rules from chat.css are applied to the stamp layer and cards
  const layerZIndex = await page
    .locator('#canvas-stamp-layer')
    .evaluate((el) => getComputedStyle(el).zIndex);
  expect(layerZIndex).toBe('4');

  // Stamps module & doodle-renderer exports behave properly
  const apiCheck = await page.evaluate(async () => {
    const stampsMod = await window.__importModule<StampsModule>('/static/generated/canvas/stamps.js');
    const rendererMod = await window.__importModule<RendererModule>('/static/generated/canvas/doodle-renderer.js');
    stampsMod.updateStampManagerProfileLinks({ id: 9, username: 'Bob' });
    const profileHref = (document.getElementById('stamp-manager-profile-link') as HTMLAnchorElement).getAttribute('href');
    const emptyHref = (document.getElementById('stamp-manager-empty-profile-btn') as HTMLAnchorElement).getAttribute('href');
    return {
      hasControllerFactory: typeof stampsMod.createCanvasStampsController === 'function',
      hasRendererFactory: typeof rendererMod.createDoodleRenderer === 'function',
      profileHref,
      emptyHref,
    };
  });
  expect(apiCheck).toEqual({
    hasControllerFactory: true,
    hasRendererFactory: true,
    profileHref: '/users/Bob',
    emptyHref: '/users/Bob',
  });
});

test('stamp manager separates user and theater sources, filters search, and places theater stamps', async ({
  canvas,
  page,
}) => {
  await mountStampHarness(canvas, page);
  await page.evaluate(() => {
    window.stampHarness.setCachedTheaterStamps([
      { id: 'theater:stage:hero.png', name: 'Theater Hero', url: '/theaters/stage/stamps/hero.png' },
    ]);
    const userStamps = [{ id: 1, name: 'User Hero', url: '/api/stamps/1' }];
    window.stampHarness.setCachedUserStamps(userStamps);
    window.stampHarness.controller.renderGrid(userStamps);
  });

  await expect(page.locator('.stamp-section-label')).toHaveText(['User stamps', 'Theater stamps']);
  await expect(page.locator('.stamp-section-divider')).toHaveCount(1);
  await expect(page.locator('.stamp-card-name')).toHaveText(['User Hero', 'Theater Hero']);
  await expect(page.locator('#stamp-manager-count')).toHaveText('1/10');
  await expect(page.locator('#stamp-manager-count')).toHaveAttribute(
    'title',
    '1 user stamps · 1 theater stamps',
  );

  await page.locator('#chat-open-stamps-btn').click();
  await page.locator('#asset-search').fill('theater hero');
  await expect(page.locator('.stamp-card:visible')).toHaveCount(1);
  await expect(page.locator('.stamp-card:visible')).toContainText('Theater Hero');

  await page.locator('#asset-search').fill('unknown');
  await expect(page.locator('.stamp-card:visible')).toHaveCount(0);
  await expect(page.locator('#asset-search-empty')).toBeVisible();

  await page.locator('#asset-search').fill('');
  await expect(page.locator('.stamp-card:visible')).toHaveCount(2);

  await page.evaluate(() => window.stampHarness.cursorState.select({ type: 'navigate' }));
  await page
    .locator('.stamp-card[data-stamp-id="theater:stage:hero.png"]')
    .dragTo(page.locator('#image-container'));

  expect(await page.evaluate(() => window.stampHarness.actions[0]?.stamp_id)).toBe(
    'theater:stage:hero.png',
  );
  expect(await page.evaluate(() => window.stampHarness.cursorState.current.type)).toBe('select');
  await expect(page.locator('.stamp-card')).toHaveCount(2);

  await page.evaluate(() => {
    window.stampHarness.setCachedUserStamps([]);
    window.stampHarness.controller.renderGrid([]);
  });
  await expect(page.locator('.stamp-card-name')).toHaveText(['Theater Hero']);
  await expect(page.locator('#stamp-manager-empty')).toBeHidden();

  await page.evaluate(() => {
    window.stampHarness.setContributorPermission(false);
    window.stampHarness.controller.renderGrid([]);
  });
  await expect(page.locator('.stamp-card')).toHaveAttribute('draggable', 'false');
});

test('stamp drag respects doodles disabled for contributor and auto-enables for orator', async ({
  canvas,
  page,
}) => {
  await mountStampHarness(canvas, page);
  await page.evaluate(() => {
    window.stampHarness.setCachedTheaterStamps([
      {
        id: 'theater:stage:dragon.png',
        name: 'Theater Dragon',
        url: '/theaters/stage/stamps/dragon.png',
      },
    ]);
    window.stampHarness.setOrator(false);
    window.stampHarness.setDoodlesEnabled(false);
    window.stampHarness.setCachedUserStamps([]);
    window.stampHarness.controller.renderGrid([]);
  });

  await page.locator('#chat-open-stamps-btn').click();
  const card = page.locator('.stamp-card[data-stamp-id="theater:stage:dragon.png"]');
  await expect(card).toHaveAttribute('draggable', 'false');
  await expect(card).toHaveClass(/locked/);
  const lockedTitle = await card.getAttribute('title');
  expect(lockedTitle).toContain('Doodles must be enabled by Orator');

  await card.dragTo(page.locator('#image-container'));
  expect(await page.evaluate(() => window.stampHarness.actions.length)).toBe(0);

  // Orator when doodles are disabled: allowed to drag and auto-enables doodles
  await page.evaluate(() => {
    window.stampHarness.setOrator(true);
    window.stampHarness.controller.renderGrid([]);
  });
  await expect(card).toHaveAttribute('draggable', 'true');
  await expect(card).toHaveClass(/can-drag/);

  await card.dragTo(page.locator('#image-container'));
  const placed = await page.evaluate(() => window.stampHarness.actions[0]);
  expect(placed.stamp_id).toBe('theater:stage:dragon.png');
  expect(placed.x).toBeGreaterThanOrEqual(0);
  expect(placed.x).toBeLessThanOrEqual(1);
  expect(placed.y).toBeGreaterThanOrEqual(0);
  expect(placed.y).toBeLessThanOrEqual(1);
  expect(await page.evaluate(() => window.stampHarness.getDoodlesEnabled())).toBe(true);
});

for (const success of [true, false]) {
  test(`stamp generation permissions, concurrent pending jobs, and ${success ? 'success' : 'failure'} handling`, async ({
    canvas,
    page,
  }) => {
    await mountStampHarness(canvas, page, { withFullGenerationFooter: true });
    await page.evaluate(() => {
      window.generationRequests = [];
      window.generationResolvers = [];
      window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (url === '/api/users/me/stamps') {
          return new Response(JSON.stringify([]), { status: 200 });
        }
        if (url === '/api/theaters/stage/stamps') {
          return new Response(JSON.stringify(window.stampHarness.controller.getCachedTheaterStamps()), {
            status: 200,
          });
        }
        window.generationRequests!.push({
          url,
          method: init?.method,
          body: typeof init?.body === 'string' ? init.body : null,
        });
        return new Promise((resolve) => {
          window.generationResolvers!.push((mockResp) =>
            resolve({
              ok: mockResp.ok,
              json: mockResp.json,
            } as unknown as Response),
          );
        });
      };
    });

    const derivedNames = await page.evaluate(() =>
      [
        'THE silver, dragon on a mountain beyond the clouds',
        'A goblin',
        'the and of',
        'Un café près du château',
      ].map((p) => window.stampHarness.stampNameFromPrompt(p)),
    );
    expect(derivedNames).toEqual([
      'silver dragon mountain',
      'goblin',
      'Stamp',
      'Un café près',
    ]);

    await page.locator('#chat-open-stamps-btn').click();
    // Non-owner cannot see or submit the owner-only form
    await expect(page.locator('#stamp-generation-form')).toBeHidden();
    await page.evaluate(() => {
      document
        .getElementById('stamp-generation-form')!
        .dispatchEvent(new Event('submit', { cancelable: true }));
    });
    expect(await page.evaluate(() => window.generationRequests!.length)).toBe(0);

    await page.evaluate(() => window.stampHarness.setOwner(true));
    await expect(page.locator('#stamp-generation-form')).toBeVisible();
    await expect(page.locator('#stamp-generation-name')).toHaveCount(0);
    await expect(page.locator('#stamp-generation-submit')).toHaveCount(0);
    await expect(page.locator('#stamp-generation-prompt')).toHaveAttribute(
      'placeholder',
      'describe a stamp and press (enter)',
    );

    await page.locator('#stamp-generation-prompt').fill('A green goblin with a spear');
    await page.locator('#stamp-generation-prompt').press('Enter');
    await expect(page.locator('#stamp-generation-prompt')).toHaveValue('');
    await expect(page.locator('#stamp-generation-status')).toContainText('1 stamp generating');
    await expect(page.locator('.stamp-card-generating')).toHaveCount(1);
    const animName = await page
      .locator('.stamp-generation-placeholder')
      .evaluate((element) => getComputedStyle(element, '::after').animationName);
    expect(animName).toBe('stamp-generation-spin');
    await expect(page.locator('.stamp-card-generating')).toHaveAttribute('draggable', 'false');
    await expect(page.locator('#stamp-manager-empty')).toBeHidden();

    // Empty submit does not trigger a second request
    await page.evaluate(() => {
      (document.getElementById('stamp-generation-form') as HTMLFormElement).requestSubmit();
    });
    expect(await page.evaluate(() => window.generationRequests!.length)).toBe(1);
    expect(await page.evaluate(() => window.generationRequests![0].url)).toBe(
      '/api/theaters/stage/stamps/generate',
    );
    expect(
      await page.evaluate(() => JSON.parse(window.generationRequests![0].body || '{}')),
    ).toEqual({
      name: 'green goblin spear',
      prompt: 'A green goblin with a spear',
    });

    await page.locator('#stamp-generation-prompt').fill('A silver dragon');
    await page.locator('#stamp-generation-prompt').press('Enter');
    expect(await page.evaluate(() => window.generationRequests!.length)).toBe(2);
    await expect(page.locator('.stamp-card-generating')).toHaveCount(2);
    await expect(page.locator('#stamp-generation-status')).toContainText('2 stamps generating');

    // Switching tabs and back preserves pending generation cards
    await page.locator('#chat-show-chat-btn').click();
    await page.locator('#chat-open-stamps-btn').click();
    await page.evaluate(() => window.stampHarness.controller.renderGrid([]));
    await expect(page.locator('.stamp-card-generating')).toHaveCount(2);

    // Resolve second request first (out of order)
    await page.evaluate(() => {
      window.generationResolvers![1]({
        ok: true,
        json: async () => ({
          stamps: [
            {
              id: 'theater:stage:dragon.png',
              name: 'Silver dragon',
              url: '/theaters/stage/stamps/dragon.png',
            },
          ],
          credits_charged: 4,
        }),
      });
    });
    await page.waitForFunction(() => window.stampHarness.controller.pendingGenerations.size === 1);
    await expect(page.locator('.stamp-card-generating .stamp-card-name')).toHaveText([
      'green goblin spear',
    ]);
    await expect(page.locator('#stamp-generation-status')).toContainText('1 stamp generating');

    await page.locator('#stamp-generation-prompt').fill('Next stamp draft');
    if (success) {
      await page.evaluate(() => {
        window.generationResolvers![0]({
          ok: true,
          json: async () => ({
            stamps: [
              {
                id: 'theater:stage:goblin.png',
                name: 'Goblin scout',
                url: '/theaters/stage/stamps/goblin.png',
              },
            ],
            credits_charged: 4,
          }),
        });
      });
      await page.waitForFunction(() => window.stampHarness.controller.pendingGenerations.size === 0);
      await expect(page.locator('.stamp-card-name')).toHaveText(['Silver dragon', 'Goblin scout']);
      await expect(page.locator('.stamp-card').first()).toHaveAttribute('draggable', 'true');
    } else {
      await page.evaluate(() => {
        window.generationResolvers![0]({
          ok: false,
          json: async () => ({ detail: 'Generation requires 4 credits.' }),
        });
      });
      await page.waitForFunction(() =>
        (document.getElementById('stamp-generation-status')?.textContent || '').includes(
          'requires 4 credits',
        ),
      );
      await expect(page.locator('.stamp-card-name')).toHaveText(['Silver dragon']);
    }

    await expect(page.locator('#stamp-generation-prompt')).toHaveValue('Next stamp draft');
    await expect(page.locator('.stamp-card-generating')).toHaveCount(0);
    await expect(page.locator('#stamp-generation-prompt')).toBeEditable();

    await page.evaluate(() => window.stampHarness.setOwner(false));
    await expect(page.locator('#stamp-generation-form')).toBeHidden();
  });
}

for (const viewportWidth of [1400, 1000]) {
  for (const owner of [true, false]) {
    test(`canvas panel tabs remain visible and preserve stamp composer (${viewportWidth}px, owner=${owner})`, async ({
      canvas,
      page,
    }) => {
      await serveMockStampAssets(canvas);
      let isActiveOrator = false;
      const sockets: WebSocketRoute[] = [];
      const makeBaton = () => ({
        owner: { id: owner ? 5 : 999 },
        active_orator: { id: isActiveOrator ? 5 : 999 },
        viewer_collab_enabled: true,
      });

      await canvas.openCanvas({
        viewport: { width: viewportWidth, height: 800 },
        isOrator: false,
        user: { id: 5, username: 'Alice' },
        ownerId: owner ? 5 : 999,
        apiRoutes: {
          '/api/theaters/stage': (route) =>
            route.fulfill({
              json: {
                metadata: {
                  is_owner: owner,
                  is_active_orator: isActiveOrator,
                  is_contributor: true,
                },
              },
            }),
          '/api/theaters/stage/baton': (route) => route.fulfill({ json: makeBaton() }),
          '/api/users/me/stamps': async (route) => {
            await route.fulfill({
              json: [{ id: 1, name: 'Scroll Filler', url: '/api/stamps/1' }],
            });
          },
          '/api/theaters/stage/stamps': [],
        },
        onWebSocket(ws) {
          if (ws.url().includes('/ws/doodle')) {
            sockets.push(ws);
            ws.send(
              JSON.stringify({
                type: 'doodle_snapshot',
                page_id: 'page-1',
                enabled: true,
                batches: [],
                stamps: [],
                annotations: [],
              }),
            );
          }
        },
      });

      const sidebar = page.locator('#chat-sidebar');
      if (await sidebar.evaluate((el) => el.classList.contains('collapsed'))) {
        await page.locator('#chat-side-toggle-btn').click();
        await expect(sidebar).toHaveCSS('margin-right', '0px');
      }
      await expect(page.locator('#chat-open-stamps-btn')).toBeInViewport();
      await expect(page.locator('#stamp-manager-profile-link')).toHaveAttribute('href', '/users/Alice');
      await page.locator('#chat-input').evaluate((el: HTMLElement) => {
        el.style.height = '110px';
      });
      const chatBox = await page.locator('#chat-input').boundingBox();
      const toggleBox = await page.locator('#chat-open-stamps-btn').boundingBox();
      expect(chatBox).not.toBeNull();
      expect(toggleBox).not.toBeNull();

      await page.locator('#chat-open-stamps-btn').click();
      expect(await page.locator('#chat-open-stamps-btn').boundingBox()).toEqual(toggleBox);
      await expect(page.locator('#chat-show-chat-btn')).toBeVisible();
      await expect(page.locator('#chat-open-stamps-btn')).toHaveAttribute('aria-selected', 'true');
      await expect(page.locator('#stamp-manager-permission-banner')).toBeVisible();

      // Toggle orator state via real baton_state WebSocket update
      isActiveOrator = true;
      for (const ws of sockets) {
        ws.send(JSON.stringify({ type: 'baton_state', baton_state: makeBaton(), active_viewers: [] }));
      }
      await expect(page.locator('#stamp-manager-permission-banner')).toBeHidden();

      isActiveOrator = false;
      for (const ws of sockets) {
        ws.send(JSON.stringify({ type: 'baton_state', baton_state: makeBaton(), active_viewers: [] }));
      }
      await expect(page.locator('#stamp-manager-permission-banner')).toBeVisible();

      await expect(page.locator('#chat-form #chat-open-stamps-btn')).toHaveCount(0);
      await expect(page.locator('#popout-collapse-btn')).toHaveCount(0);

      if (owner) {
        const promptBox = await page.locator('#stamp-generation-prompt').boundingBox();
        expect(promptBox).not.toBeNull();
        expect(Math.abs(promptBox!.x - chatBox!.x)).toBeLessThanOrEqual(1);
        expect(Math.abs(promptBox!.width - chatBox!.width)).toBeLessThanOrEqual(1);
        expect(Math.abs(promptBox!.height - chatBox!.height)).toBeLessThanOrEqual(1);

        // Add tall content to stamp grid so body scrolls while composer remains fixed
        await page.locator('#stamp-manager-grid').evaluate((grid) => {
          const spacer = document.createElement('div');
          spacer.style.height = '1200px';
          grid.appendChild(spacer);
        });
        await page.locator('#stamp-manager-body').evaluate((el) => {
          el.scrollTop = 1200;
        });
        expect(await page.locator('#stamp-generation-prompt').boundingBox()).toEqual(promptBox);
      }

      const profileBox = await page.locator('#stamp-manager-profile-link').boundingBox();
      expect(profileBox).not.toBeNull();
      expect(profileBox!.width).toBeGreaterThan(0);
      await expect(page.locator('#chat-identity #stamp-manager-profile-link')).toBeVisible();
      await expect(page.locator('.stamp-manager-footer a')).toHaveCount(0);

      await page.locator('#chat-show-chat-btn').click();
      await expect(page.locator('#stamp-manager-profile-link')).toBeVisible();

      await page.mouse.dblclick(
        toggleBox!.x + toggleBox!.width / 2,
        toggleBox!.y + toggleBox!.height / 2,
        { delay: 100 },
      );
      await expect(page.locator('#panel-body')).toBeHidden();
      await expect(page.locator('#stamp-manager-pane')).toBeVisible();

      await page.locator('#chat-open-stamps-btn').press('ArrowLeft');
      await expect(page.locator('#panel-body')).toBeVisible();
      await expect(page.locator('#stamp-manager-pane')).toBeHidden();
      await expect(page.locator('#chat-show-chat-btn')).toHaveAttribute('aria-selected', 'true');
    });
  }
}

test('stamp manager toggles and occupies chat pane', async ({ canvas, page }) => {
  await mountStampHarness(canvas, page);
  await expect(page.locator('#panel-body')).toBeVisible();
  await expect(page.locator('#stamp-manager-pane')).toBeHidden();

  await page.locator('#chat-open-stamps-btn').click();
  await expect(page.locator('#stamp-manager-pane')).toBeVisible();
  await expect(page.locator('#panel-body')).toBeHidden();

  await page.locator('#chat-show-chat-btn').click();
  await expect(page.locator('#panel-body')).toBeVisible();
  await expect(page.locator('#stamp-manager-pane')).toBeHidden();
});

test('stamp drag and drop enforces one of a kind per user', async ({ canvas, page }) => {
  await mountStampHarness(canvas, page);
  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 10, name: 'Dragon', url: '/api/stamps/10' },
      0.2,
      0.3,
    );
  });
  expect(await page.evaluate(() => window.stampHarness.actions.length)).toBe(1);
  expect(await page.evaluate(() => window.stampHarness.actions[0].x)).toBeCloseTo(0.2);
  expect(await page.evaluate(() => window.stampHarness.actions[0].y)).toBeCloseTo(0.3);
  await expect(page.locator('.canvas-stamp-annotation')).toHaveCount(1);

  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 11, name: 'Heart', url: '/api/stamps/11' },
      0.5,
      0.5,
    );
  });
  expect(await page.evaluate(() => window.stampHarness.actions.length)).toBe(2);
  await expect(page.locator('.canvas-stamp-annotation')).toHaveCount(2);

  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 10, name: 'Dragon', url: '/api/stamps/10' },
      0.8,
      0.9,
    );
  });
  expect(await page.evaluate(() => window.stampHarness.actions.length)).toBe(2);
  await expect(page.locator('.canvas-stamp-annotation')).toHaveCount(2);
  const dragon = await page.evaluate(() =>
    window.stampHarness.actions.find((s) => s.stamp_id === 10),
  );
  expect(dragon?.x).toBeCloseTo(0.8);
  expect(dragon?.y).toBeCloseTo(0.9);
});

test('stamp selection and keyboard removal', async ({ canvas, page }) => {
  await mountStampHarness(canvas, page);
  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 10, name: 'Dragon', url: '/api/stamps/10' },
      0.4,
      0.4,
    );
  });
  const stamp = page.locator('.canvas-stamp-annotation').first();
  await expect(stamp).toBeVisible();
  await expect(stamp).not.toHaveClass(/movable/);

  await stamp.click();
  await expect(stamp).toHaveClass(/movable/);

  await page.keyboard.press('Delete');
  expect(await page.evaluate(() => window.stampHarness.actions.length)).toBe(0);
  await expect(page.locator('.canvas-stamp-annotation')).toHaveCount(0);
});

test('stamp click and hold immediate drag', async ({ canvas, page }) => {
  await mountStampHarness(canvas, page);
  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 10, name: 'Dragon', url: '/api/stamps/10' },
      0.3,
      0.3,
    );
  });
  const stamp = page.locator('.canvas-stamp-annotation').first();
  await expect(stamp).toBeVisible();
  await expect(stamp).not.toHaveClass(/movable/);

  const box = (await stamp.boundingBox())!;
  const startX = box.x + box.width / 2;
  const startY = box.y + box.height / 2;

  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(startX + 100, startY + 100);
  await page.mouse.up();

  const moved = await page.evaluate(() => window.stampHarness.actions[0]);
  expect(moved.x!).toBeGreaterThan(0.3);
  expect(moved.y!).toBeGreaterThan(0.3);
});

test('stamp placement rejected without contributor permission', async ({ canvas, page }) => {
  await mountStampHarness(canvas, page);
  await page.evaluate(() => {
    window.stampHarness.setContributorPermission(false);
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 10, name: 'Dragon', url: '/api/stamps/10' },
      0.3,
      0.3,
    );
  });
  expect(await page.evaluate(() => window.stampHarness.actions.length)).toBe(0);
  await expect(page.locator('.canvas-stamp-annotation')).toHaveCount(0);
});

test('stamp resizing via handle and wheel', async ({ canvas, page }) => {
  await mountStampHarness(canvas, page);
  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 10, name: 'Dragon', url: '/api/stamps/10' },
      0.5,
      0.5,
    );
  });
  const stamp = page.locator('.canvas-stamp-annotation').first();
  await expect(stamp).toBeVisible();
  expect(await stamp.evaluate((el: HTMLElement) => el.style.width)).toBe('80px');
  expect(await stamp.evaluate((el: HTMLElement) => el.style.height)).toBe('80px');

  await stamp.click();
  await expect(stamp).toHaveClass(/movable/);
  const resizeHandle = stamp.locator('.stamp-resize-handle');
  await expect(resizeHandle).toBeVisible();

  await stamp.dispatchEvent('wheel', { deltaY: -100 });
  expect(await page.evaluate(() => window.stampHarness.actions[0].size)).toBe(88);
  expect(await stamp.evaluate((el: HTMLElement) => el.style.width)).toBe('88px');
  expect(await stamp.evaluate((el: HTMLElement) => el.style.height)).toBe('88px');

  await stamp.dispatchEvent('wheel', { deltaY: 100 });
  expect(await page.evaluate(() => window.stampHarness.actions[0].size)).toBe(80);
  expect(await stamp.evaluate((el: HTMLElement) => el.style.width)).toBe('80px');

  const box = (await resizeHandle.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 50, box.y + box.height / 2 + 50);
  await page.mouse.up();

  const newSize = await page.evaluate(() => window.stampHarness.actions[0].size!);
  expect(newSize).toBeGreaterThan(80);
  expect(await stamp.evaluate((el: HTMLElement) => el.style.width)).toBe(`${newSize}px`);
  expect(await stamp.evaluate((el: HTMLElement) => el.style.height)).toBe(`${newSize}px`);
});

test('stamp rotation handle commit, rotated resize, and replay', async ({ canvas, page }) => {
  await mountStampHarness(canvas, page);
  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 10, name: 'Dragon', url: '/api/stamps/10' },
      0.5,
      0.5,
    );
  });
  const stamp = page.locator('.canvas-stamp-annotation').first();
  const handle = stamp.locator('.stamp-rotate-handle');
  await expect(handle).toBeHidden();

  await stamp.click();
  const box = (await handle.boundingBox())!;
  const stampBox = (await stamp.boundingBox())!;
  const resizeBox = (await stamp.locator('.stamp-resize-handle').boundingBox())!;
  expect(box.x).toBeLessThan(stampBox.x);
  expect(box.y).toBeLessThan(stampBox.y);
  expect(resizeBox.x).toBeGreaterThan(stampBox.x);
  expect(resizeBox.y).toBeGreaterThan(stampBox.y);

  const centerX = stampBox.x + stampBox.width / 2;
  const centerY = stampBox.y + stampBox.height / 2;
  const startX = box.x + box.width / 2;
  const startY = box.y + box.height / 2;

  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(centerX - (startY - centerY), centerY + (startX - centerX), { steps: 10 });
  await page.mouse.up();

  expect(await page.evaluate(() => window.stampHarness.actions[0].rotation)).toBe(90);
  expect(
    await page.evaluate(() =>
      window.stampHarness.sent.filter((m) => m.type === 'stamp').at(-1)?.rotation,
    ),
  ).toBe(90);
  expect(
    await page.evaluate(() => {
      const a = window.stampHarness.actions[0];
      return [a.x, a.y, a.size];
    }),
  ).toEqual([0.5, 0.5, 80]);
  expect(await stamp.evaluate((el: HTMLElement) => el.style.transform)).toBe(
    'translate(-50%, -50%) rotate(90deg)',
  );

  // Resize in the rotated stamp's local coordinates, retaining its angle
  const rotatedResizeBox = (await stamp.locator('.stamp-resize-handle').boundingBox())!;
  await page.mouse.move(
    rotatedResizeBox.x + rotatedResizeBox.width / 2,
    rotatedResizeBox.y + rotatedResizeBox.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(centerX - 75, centerY + 75);
  await page.mouse.up();

  expect(await page.evaluate(() => window.stampHarness.actions[0].size)).toBe(150);
  expect(await page.evaluate(() => window.stampHarness.actions[0].rotation)).toBe(90);

  await page.evaluate(() => {
    window.stampHarness.renderer.redraw([]);
    window.stampHarness.renderer.redraw(window.stampHarness.actions as DoodleAnnotation[]);
  });
  expect(await stamp.evaluate((el: HTMLElement) => el.style.transform)).toBe(
    'translate(-50%, -50%) rotate(90deg)',
  );
});

for (const finish of ['pointercancel', 'lostpointercapture', 'permission_revoked'] as const) {
  test(`stamp rotation cancel restores original (${finish})`, async ({ canvas, page }) => {
    await mountStampHarness(canvas, page);
    await page.evaluate(() => {
      window.stampHarness.controller.placeStampOnCanvas(
        { stamp_id: 10, name: 'Dragon', url: '/api/stamps/10' },
        0.5,
        0.5,
      );
    });
    const stamp = page.locator('.canvas-stamp-annotation').first();
    await stamp.click();
    const handle = stamp.locator('.stamp-rotate-handle');
    const box = (await handle.boundingBox())!;
    await page.evaluate(() => {
      window.stampHarness.sent.length = 0;
    });

    await page.mouse.move(box.x + 12, box.y + 12);
    await page.mouse.down();
    await page.mouse.move(550, 250);
    const previewRotation = await page.evaluate(() => window.stampHarness.actions[0].rotation ?? 0);
    expect(previewRotation).toBeGreaterThan(0);
    expect(previewRotation).toBeLessThan(360);

    if (finish === 'permission_revoked') {
      await page.evaluate(() => window.stampHarness.setContributorPermission(false));
      await page.mouse.up();
    } else {
      await handle.dispatchEvent(finish, { pointerId: 1 });
      await page.mouse.up();
    }

    expect(await page.evaluate(() => window.stampHarness.actions[0].rotation || 0)).toBe(0);
    expect(
      await page.evaluate(() => window.stampHarness.sent.filter((m) => m.type === 'stamp').length),
    ).toBe(0);

    await page.evaluate(() => window.stampHarness.setContributorPermission(false));
    await handle.press('ArrowRight');
    expect(await page.evaluate(() => window.stampHarness.actions[0].rotation || 0)).toBe(0);
  });
}

test('stamp rotation keyboard steps wrap around 360', async ({ canvas, page }) => {
  await mountStampHarness(canvas, page);
  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 10, name: 'Dragon', url: '/api/stamps/10' },
      0.5,
      0.5,
    );
  });
  const stamp = page.locator('.canvas-stamp-annotation').first();
  await stamp.click();
  const handle = stamp.locator('.stamp-rotate-handle');

  await handle.press('ArrowLeft');
  expect(await page.evaluate(() => window.stampHarness.actions[0].rotation)).toBe(345);
  await handle.press('ArrowRight');
  expect(await page.evaluate(() => window.stampHarness.actions[0].rotation)).toBe(0);
});

test('stamp hover cursor styles on canvas and manager cards', async ({ canvas, page }) => {
  await mountStampHarness(canvas, page);
  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 10, name: 'Dragon', url: '/api/stamps/10' },
      0.5,
      0.5,
    );
  });
  const stamp = page.locator('.canvas-stamp-annotation').first();
  await expect(stamp).toBeVisible();
  expect(await stamp.evaluate((el) => getComputedStyle(el).cursor)).toBe('grab');

  await page.locator('#chat-open-stamps-btn').click();
  await expect(page.locator('#stamp-manager-empty')).toBeVisible();
  await page.evaluate(() => {
    const grid = document.getElementById('stamp-manager-grid')!;
    grid.innerHTML = `
      <div class="stamp-card can-drag" id="test-card-drag">Stamp 1</div>
      <div class="stamp-card locked" id="test-card-locked">Stamp 2</div>
    `;
  });
  expect(await page.locator('#test-card-drag').evaluate((el) => getComputedStyle(el).cursor)).toBe(
    'grab',
  );
  expect(await page.locator('#test-card-locked').evaluate((el) => getComputedStyle(el).cursor)).toBe(
    'not-allowed',
  );
});

test('stamps dynamically resize when canvas container resizes', async ({ canvas, page }) => {
  await mountStampHarness(canvas, page);
  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 10, name: 'Dragon', url: '/api/stamps/10' },
      0.5,
      0.5,
    );
  });
  const stamp = page.locator('.canvas-stamp-annotation').first();
  await expect(stamp).toBeVisible();
  expect(await stamp.evaluate((el: HTMLElement) => el.style.width)).toBe('80px');
  expect(await stamp.evaluate((el: HTMLElement) => el.style.height)).toBe('80px');

  await page.evaluate(() => {
    document.getElementById('image-container')!.style.width = '500px';
    window.stampHarness.renderer.resize(window.stampHarness.actions as DoodleAnnotation[]);
  });
  expect(await stamp.evaluate((el: HTMLElement) => el.style.width)).toBe('40px');
  expect(await stamp.evaluate((el: HTMLElement) => el.style.height)).toBe('40px');

  await page.evaluate(() => {
    document.getElementById('image-container')!.style.width = '800px';
    window.stampHarness.renderer.resize(window.stampHarness.actions as DoodleAnnotation[]);
  });
  expect(await stamp.evaluate((el: HTMLElement) => el.style.width)).toBe('64px');
  expect(await stamp.evaluate((el: HTMLElement) => el.style.height)).toBe('64px');
});

for (const draggedIndex of [0, 1]) {
  test(`stamp drag with multiple stamps does not snap back (index=${draggedIndex})`, async ({
    canvas,
    page,
  }) => {
    await mountStampHarness(canvas, page);
    await page.evaluate(() => {
      window.stampHarness.controller.placeStampOnCanvas(
        { stamp_id: 10, name: 'Dragon', url: '/api/stamps/10' },
        0.2,
        0.2,
      );
      window.stampHarness.controller.placeStampOnCanvas(
        { stamp_id: 11, name: 'Heart', url: '/api/stamps/11' },
        0.6,
        0.6,
      );
    });

    const { draggedId, otherId, originalX, originalY, otherBefore } = await page.evaluate(
      (idx) => {
        const actions = window.stampHarness.actions;
        const dId = actions[idx].id!;
        const oId = actions[1 - idx].id!;
        const d = actions.find((s) => s.id === dId)!;
        const o = actions.find((s) => s.id === oId)!;
        return {
          draggedId: dId,
          otherId: oId,
          originalX: d.x!,
          originalY: d.y!,
          otherBefore: { ...o },
        };
      },
      draggedIndex,
    );

    const stamp = page.locator(`.canvas-stamp-annotation[data-annotation-id="${draggedId}"]`);
    const box = (await stamp.boundingBox())!;
    const startX = box.x + box.width / 2;
    const startY = box.y + box.height / 2;

    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.mouse.move(startX + 100, startY + 50, { steps: 10 });
    await page.mouse.up();

    const moved = await page.evaluate(
      (id) => window.stampHarness.actions.find((s) => s.id === id)!,
      draggedId,
    );
    expect(moved.x!).toBeGreaterThan(originalX + 0.05);
    expect(moved.y!).toBeGreaterThan(originalY + 0.02);

    const otherAfter = await page.evaluate(
      (id) => window.stampHarness.actions.find((s) => s.id === id)!,
      otherId,
    );
    expect(otherAfter).toEqual(otherBefore);

    const committed = await page.evaluate(
      (id) => window.stampHarness.sent.filter((m) => m.type === 'stamp' && m.id === id),
      draggedId,
    );
    expect(committed.at(-1)!.x!).toBeCloseTo(moved.x!);
    expect(committed.at(-1)!.y!).toBeCloseTo(moved.y!);
  });
}

test('stamp layering order on creation, selection, deselection, and replacement', async ({
  canvas,
  page,
}) => {
  await mountStampHarness(canvas, page);
  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 10, name: 'Dragon', url: '/api/stamps/10' },
      0.3,
      0.3,
    );
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 11, name: 'Heart', url: '/api/stamps/11' },
      0.35,
      0.35,
    );
  });

  const lastChildId = (): Promise<string | undefined> =>
    page.evaluate(
      () =>
        (document.getElementById('canvas-stamp-layer')!.lastElementChild as HTMLElement | null)
          ?.dataset.annotationId,
    );

  const initialIds = await page.evaluate(() =>
    window.stampHarness.actions.map((a) => ({ id: a.id!, stampId: a.stamp_id })),
  );
  expect(await lastChildId()).toBe(initialIds[1].id);
  expect(initialIds[0].stampId).toBe(10);
  expect(initialIds[1].stampId).toBe(11);

  // Select Dragon (stamp 10) -> moves to front
  const dragonId = initialIds[0].id;
  const heartId = initialIds[1].id;
  await page.locator(`.canvas-stamp-annotation[data-annotation-id="${dragonId}"]`).click();

  expect(await lastChildId()).toBe(dragonId);
  expect(await page.evaluate(() => window.stampHarness.actions.map((a) => a.stamp_id))).toEqual([
    11, 10,
  ]);
  const selectMessages = await page.evaluate(() =>
    window.stampHarness.sent.filter((m) => m.type === 'select_stamp'),
  );
  expect(selectMessages.length).toBeGreaterThanOrEqual(1);
  expect(selectMessages.at(-1)!.id).toBe(dragonId);

  // Select Heart (stamp 11) -> moves to front
  await page.locator(`.canvas-stamp-annotation[data-annotation-id="${heartId}"]`).click();
  expect(await lastChildId()).toBe(heartId);
  expect(await page.evaluate(() => window.stampHarness.actions.map((a) => a.stamp_id))).toEqual([
    10, 11,
  ]);

  // Click background to deselect -> Heart stays at front
  await page.locator('#image-container').click({ position: { x: 10, y: 10 } });
  expect(await lastChildId()).toBe(heartId);

  // Re-place Dragon -> replaces and moves to front
  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 10, name: 'Dragon', url: '/api/stamps/10' },
      0.7,
      0.7,
    );
  });
  expect(await lastChildId()).toBe(dragonId);
  expect(await page.evaluate(() => window.stampHarness.actions.map((a) => a.stamp_id))).toEqual([
    11, 10,
  ]);
});

test('stamp manager orders stamps from most recently used to least and persists across re-renders', async ({
  canvas,
  page,
}) => {
  await mountStampHarness(canvas, page);
  await page.evaluate(() => {
    const userStamps = [
      { id: 1, name: 'Star', url: '/api/stamps/1' },
      { id: 2, name: 'Moon', url: '/api/stamps/2' },
      { id: 3, name: 'Sun', url: '/api/stamps/3' },
    ];
    window.stampHarness.setCachedUserStamps(userStamps);
    window.stampHarness.controller.renderGrid(userStamps);
  });

  const getCardIds = (): Promise<Array<string | undefined>> =>
    page.evaluate(() =>
      Array.from(document.querySelectorAll<HTMLElement>('#stamp-manager-grid .stamp-card')).map(
        (c) => c.dataset.stampId,
      ),
    );

  expect(await getCardIds()).toEqual(['1', '2', '3']);

  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 3, name: 'Sun', url: '/api/stamps/3' },
      0.2,
      0.2,
    );
  });
  expect(await getCardIds()).toEqual(['3', '1', '2']);

  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 1, name: 'Star', url: '/api/stamps/1' },
      0.5,
      0.5,
    );
  });
  expect(await getCardIds()).toEqual(['1', '3', '2']);

  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 2, name: 'Moon', url: '/api/stamps/2' },
      0.7,
      0.7,
    );
  });
  expect(await getCardIds()).toEqual(['2', '1', '3']);

  // Selecting Stamp 3 on canvas also makes Stamp 3 most recently used
  const sunAnnotationId = await page.evaluate(
    () => window.stampHarness.actions.find((a) => a.stamp_id === 3)!.id!,
  );
  await page.locator(`.canvas-stamp-annotation[data-annotation-id="${sunAnnotationId}"]`).click();
  expect(await getCardIds()).toEqual(['3', '2', '1']);

  // Preserved across re-render (persisted in localStorage)
  await page.evaluate(() => window.stampHarness.controller.renderGrid());
  expect(await getCardIds()).toEqual(['3', '2', '1']);
});

test('stamps always remain under doodles in action ordering and layer z-index', async ({
  canvas,
  page,
}) => {
  await mountStampHarness(canvas, page);
  await page.evaluate(() => {
    (window.stampHarness.actions as DoodleAnnotation[]).push({
      type: 'draw',
      x0: 0.1,
      y0: 0.1,
      x1: 0.5,
      y1: 0.5,
      color: '#ff0000',
      size: 3,
    });
    window.stampHarness.renderer.redraw(window.stampHarness.actions as DoodleAnnotation[]);
  });

  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 10, name: 'Dragon', url: '/api/stamps/10' },
      0.3,
      0.3,
    );
  });
  expect(await page.evaluate(() => window.stampHarness.actions.map((a) => a.type))).toEqual([
    'stamp',
    'draw',
  ]);
  expect(await page.evaluate(() => window.stampHarness.actions[0].stamp_id)).toBe(10);

  await page.evaluate(() => {
    window.stampHarness.controller.placeStampOnCanvas(
      { stamp_id: 11, name: 'Heart', url: '/api/stamps/11' },
      0.35,
      0.35,
    );
  });
  expect(await page.evaluate(() => window.stampHarness.actions.map((a) => a.type))).toEqual([
    'stamp',
    'stamp',
    'draw',
  ]);
  expect(await page.evaluate(() => window.stampHarness.actions[0].stamp_id)).toBe(10);
  expect(await page.evaluate(() => window.stampHarness.actions[1].stamp_id)).toBe(11);

  const dragonId = await page.evaluate(() => window.stampHarness.actions[0].id!);
  await page.locator(`.canvas-stamp-annotation[data-annotation-id="${dragonId}"]`).click();

  expect(await page.evaluate(() => window.stampHarness.actions.map((a) => a.type))).toEqual([
    'stamp',
    'stamp',
    'draw',
  ]);
  expect(await page.evaluate(() => window.stampHarness.actions[0].stamp_id)).toBe(11);
  expect(await page.evaluate(() => window.stampHarness.actions[1].stamp_id)).toBe(10);
  expect(await page.evaluate(() => window.stampHarness.actions[2].type)).toBe('draw');

  const stampLayerZ = await page
    .locator('#canvas-stamp-layer')
    .evaluate((el) => getComputedStyle(el).zIndex);
  expect(stampLayerZ).toBe('4');
});

test('stamp generation reference tagging autocomplete and submission', async ({ canvas, page }) => {
  await mountStampHarness(canvas, page, { withFullGenerationFooter: true });
  await page.evaluate(() => {
    window.stampHarness.setCachedTheaterStamps([
      {
        id: 'theater:stage:goblin.webp',
        name: 'goblin',
        url: '/theaters/stage/stamps/goblin.webp',
      },
    ]);
    window.generationRequests = [];
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url === '/api/users/me/stamps') {
        return new Response(JSON.stringify([]), { status: 200 });
      }
      if (url === '/api/theaters/stage/stamps') {
        return new Response(JSON.stringify(window.stampHarness.controller.getCachedTheaterStamps()), {
          status: 200,
        });
      }
      window.generationRequests!.push({
        url,
        method: init?.method,
        body: typeof init?.body === 'string' ? init.body : null,
      });
      return new Response(
        JSON.stringify({
          stamps: window.stampHarness.controller.getCachedTheaterStamps(),
          credits_charged: 4,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    };
  });

  await page.locator('#chat-open-stamps-btn').click();
  await page.evaluate(() => window.stampHarness.setOwner(true));

  const prompt: Locator = page.locator('#stamp-generation-prompt');
  await prompt.focus();
  await page.keyboard.type('Give @gob');

  const menu = page.locator('.reference-mention-menu:visible');
  await expect(menu).toBeVisible();
  await expect(menu.locator('.reference-mention-name')).toHaveText(['@goblin']);

  await page.keyboard.press('Tab');
  await expect(prompt).toHaveValue('Give @goblin ');
  await page.keyboard.type('a golden crown');
  await prompt.press('Enter');

  await page.waitForFunction(() => (window.generationRequests?.length ?? 0) === 1);
  const references = await page.evaluate(
    () => (JSON.parse(window.generationRequests![0].body || '{}') as { references?: string[] }).references,
  );
  expect(references).toEqual(['goblin']);
});

test('isSameStampKind handles theater, game, and user stamp matching and canvas.html handles theater_stamps_updated', async ({
  canvas,
  page,
}) => {
  await serveMockStampAssets(canvas);
  let doodleWs: WebSocketRoute | null = null;
  await canvas.openCanvas({
    viewport: { width: 1400, height: 800 },
    user: { id: 5, username: 'Alice' },
    apiRoutes: {
      '/api/users/me/stamps': [{ id: 7, name: 'Star', url: '/api/stamps/7' }],
      '/api/theaters/stage/stamps': [],
    },
    onWebSocket(ws) {
      if (ws.url().includes('/ws/doodle')) {
        doodleWs = ws;
        ws.send(
          JSON.stringify({
            type: 'doodle_snapshot',
            page_id: 'page-1',
            enabled: true,
            batches: [],
            stamps: [],
            annotations: [],
          }),
        );
      }
    },
  });

  await page.locator('#chat-open-stamps-btn').click();
  await expect(page.locator('.stamp-card[data-stamp-id="7"]')).toBeVisible();

  // Push theater_stamps_updated over doodle WebSocket and verify grid updates live
  doodleWs!.send(
    JSON.stringify({
      type: 'theater_stamps_updated',
      stamps: [{ id: 'theater:stage:phoenix.png', name: 'Phoenix', url: '/theaters/stage/stamps/phoenix.png' }],
    }),
  );
  await expect(page.locator('.stamp-card[data-stamp-id="theater:stage:phoenix.png"]')).toBeVisible();

  // Verify isSameStampKind matching rules directly on compiled module
  const sameKindResults = await page.evaluate(async () => {
    const { isSameStampKind } = await window.__importModule<StampsModule>('/static/generated/canvas/stamps.js');
    return {
      sameUserAndStamp: isSameStampKind(
        { type: 'stamp', id: 'a', stamp_id: 7, user_id: 5 },
        { type: 'stamp', id: 'b', stamp_id: 7, user_id: 5 },
      ),
      differentUsersSameUserStamp: isSameStampKind(
        { type: 'stamp', id: 'a', stamp_id: 7, user_id: 5 },
        { type: 'stamp', id: 'b', stamp_id: 7, user_id: 9 },
      ),
      theaterStemMatch: isSameStampKind(
        { type: 'stamp', id: 'a', stamp_id: 'theater:stage:phoenix.png', user_id: 5 },
        { type: 'stamp', id: 'b', canvas_asset_id: 'stamp:phoenix', user_id: 9 },
      ),
      gameStemMatch: isSameStampKind(
        { type: 'stamp', id: 'a', managed_by: 'game', name: 'goblin' },
        { type: 'stamp', id: 'b', canvas_asset_id: 'stamp:goblin' },
      ),
    };
  });
  expect(sameKindResults).toEqual({
    sameUserAndStamp: true,
    differentUsersSameUserStamp: false,
    theaterStemMatch: true,
    gameStemMatch: true,
  });
});

test('in-flight loadUserStamps GET does not clobber concurrently generated stamps or switched theater stamps', async ({
  canvas,
  page,
}) => {
  await mountStampHarness(canvas, page, { withFullGenerationFooter: true });
  await page.evaluate(() => {
    window.generationResolvers = [];
    window.theaterStampsGetResolvers = [];
    window.fetch = async (input: RequestInfo | URL): Promise<Response> => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url === '/api/users/me/stamps') {
        return new Response(
          JSON.stringify([{ id: 7, name: 'Star', url: '/api/stamps/7' }]),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url === '/api/theaters/stage/stamps') {
        return new Promise((resolve) => {
          window.theaterStampsGetResolvers!.push((stamps) =>
            resolve(
              new Response(JSON.stringify(stamps), {
                status: 200,
                headers: { 'Content-Type': 'application/json' },
              }),
            ),
          );
        });
      }
      if (url === '/api/theaters/stage/stamps/generate') {
        return new Promise((resolve) => {
          window.generationResolvers!.push((mockResp) =>
            resolve({
              ok: mockResp.ok,
              json: mockResp.json,
            } as unknown as Response),
          );
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    };
  });

  // 1. Open the stamp manager to start loadUserStamps(), holding /api/theaters/stage/stamps open.
  await page.evaluate(() => window.stampHarness.setOwner(true));
  await page.locator('#chat-open-stamps-btn').click();
  await page.waitForFunction(() => (window.theaterStampsGetResolvers?.length ?? 0) === 1);

  // 2. While the GET is still in flight, generate a stamp and resolve it.
  await page.locator('#stamp-generation-prompt').fill('A silver dragon');
  await page.locator('#stamp-generation-prompt').press('Enter');
  await page.waitForFunction(() => (window.generationResolvers?.length ?? 0) === 1);
  await page.evaluate(() => {
    window.generationResolvers![0]({
      ok: true,
      json: async () => ({
        stamps: [
          {
            id: 'theater:stage:dragon.png',
            name: 'Silver dragon',
            url: '/theaters/stage/stamps/dragon.png',
          },
        ],
        credits_charged: 4,
      }),
    });
  });
  await page.waitForFunction(() => window.stampHarness.controller.pendingGenerations.size === 0);
  await expect(page.locator('.stamp-card[data-stamp-id="theater:stage:dragon.png"]')).toBeVisible();

  // 3. Resolve the held-open stale GET with [] and verify Silver dragon is preserved alongside user stamps.
  await page.evaluate(() => {
    window.theaterStampsGetResolvers![0]([]);
  });
  await page.waitForFunction(
    () => (window.stampHarness.controller.getCachedUserStamps()?.length ?? 0) === 1,
  );
  expect(
    await page.evaluate(() =>
      window.stampHarness.controller.getCachedTheaterStamps().map((s) => s.name),
    ),
  ).toEqual(['Silver dragon']);
  await expect(page.locator('.stamp-card-name')).toHaveText(['Star', 'Silver dragon']);

  // 4. Verify theater-switch isolation: an in-flight GET for 'stage' cannot overwrite 'other'.
  await page.evaluate(() => {
    window.stampHarness.setCachedTheaterStamps([]);
    void window.stampHarness.controller.loadUserStamps();
  });
  await page.waitForFunction(() => (window.theaterStampsGetResolvers?.length ?? 0) === 2);
  await page.evaluate(() => {
    window.stampHarness.setTheaterId('other');
    window.stampHarness.setCachedTheaterStamps([
      {
        id: 'theater:other:phoenix.png',
        name: 'Phoenix',
        url: '/theaters/other/stamps/phoenix.png',
      },
    ]);
    window.theaterStampsGetResolvers![1]([
      {
        id: 'theater:stage:goblin.png',
        name: 'Goblin scout',
        url: '/theaters/stage/stamps/goblin.png',
      },
    ]);
  });
  // Allow the resolved promise microtasks to flush.
  await expect(page.locator('.stamp-card[data-stamp-id="theater:other:phoenix.png"]')).toBeVisible();
  expect(
    await page.evaluate(() =>
      window.stampHarness.controller.getCachedTheaterStamps().map((s) => s.name),
    ),
  ).toEqual(['Phoenix']);
});
