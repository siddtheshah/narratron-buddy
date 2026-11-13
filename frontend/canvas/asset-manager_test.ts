import fs from 'node:fs';
import path from 'node:path';
import type { Page, Route, WebSocketRoute } from '@playwright/test';
import { expect, TEMPLATES_DIR, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';

type AssetManagerModule = typeof import('/static/generated/canvas/asset-manager.js');
type CanvasAssetManager = import('/static/generated/canvas/asset-manager.js').CanvasAssetManager;
type StampManagerTab = import('/static/generated/canvas/asset-manager.js').StampManagerTab;
type TheaterCharacter = import('/static/generated/canvas/asset-manager.js').TheaterCharacter;
type TheaterScene = import('/static/generated/canvas/asset-manager.js').TheaterScene;
type TheaterStampSummary = import('/static/generated/canvas/asset-manager.js').TheaterStampSummary;

interface AssetHarnessState {
  theaterId: string | null;
  orator: boolean;
  stampOwner: boolean;
  managerOpen: boolean;
  activeTab: StampManagerTab;
  cachedScenes: TheaterScene[] | null;
  cachedCharacters: TheaterCharacter[] | null;
  cachedUserStamps: TheaterStampSummary[];
  currentTrackSrc: string | null;
  lastToast: string | null;
  imageRefreshed: boolean;
  refreshedDomains: readonly string[] | null;
  lateStampRenderCount: number;
  manager: CanvasAssetManager;
  applyRoleUI(isOrator: boolean): void;
  setStampManagerOpen(open: boolean): void;
  renderStampManagerGrid(stamps: TheaterStampSummary[]): void;
}

interface AssetHarnessWindow extends Window {
  __assetHarness: AssetHarnessState;
  __extractTaggedRefsForTest?: (prompt: string) => string[];
}

const ASSET_MANAGER_MODULE_URL = '/static/generated/canvas/asset-manager.js';

// Valid 1x1 transparent PNG so <img> elements load cleanly under route isolation.
const TINY_PNG_BYTES = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=',
  'base64',
);

function extractStampManagerPaneHtml(): string {
  const canvasHtml = fs.readFileSync(path.join(TEMPLATES_DIR, 'canvas.html'), 'utf-8');
  const startPane = canvasHtml.indexOf('<div id="stamp-manager-pane"');
  const endPaneBound = canvasHtml.indexOf('<script type="module"', startPane);
  const closingOuter = canvasHtml.lastIndexOf('</div>', endPaneBound);
  const closingPane = canvasHtml.lastIndexOf('</div>', closingOuter - 1);
  return canvasHtml.slice(startPane, closingPane + '</div>'.length);
}

const STAMP_MANAGER_PANE_HTML = extractStampManagerPaneHtml();

async function mountAssetManagerHarness(
  canvas: CanvasHarness,
  page: Page,
  options: {
    isOrator?: boolean;
    isStampOwner?: boolean;
    openManager?: boolean;
  } = {},
): Promise<void> {
  await canvas.serveRoute(
    (url) =>
      url.origin === canvas.origin &&
      (url.pathname.startsWith('/img/') || url.pathname.includes('/animations/')),
    async (route) => {
      await route.fulfill({ status: 200, contentType: 'image/png', body: TINY_PNG_BYTES });
    },
  );

  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/scenes',
    async (route) => {
      await route.fulfill({ json: [] });
    },
  );

  await canvas.mount({
    stylesheets: ['/static/css/chat.css'],
    inlineStyles: [
      '#chat-sidebar { position: relative; width: 350px; height: 600px; display: flex; flex-direction: column; }',
    ],
    html: `
      <div id="chat-sidebar">
        <div id="panel-body" style="display: flex; flex-direction: column; flex: 1;">
          <button type="button" id="chat-open-stamps-btn">Assets</button>
        </div>
        ${STAMP_MANAGER_PANE_HTML}
      </div>
    `,
  });

  await page.evaluate(
    async ({ moduleUrl, isOrator, isStampOwner, openManager }) => {
      const { createCanvasAssetManager } = await window.__importModule<AssetManagerModule>(moduleUrl);
      const win = window as unknown as AssetHarnessWindow;
      const countEl = document.getElementById('stamp-manager-count');
      const paneEl = document.getElementById('stamp-manager-pane');

      const state: AssetHarnessState = {
        theaterId: 'stage',
        orator: isOrator,
        stampOwner: isStampOwner,
        managerOpen: openManager,
        activeTab: 'stamps',
        cachedScenes: null,
        cachedCharacters: null,
        cachedUserStamps: [],
        currentTrackSrc: null,
        lastToast: null,
        imageRefreshed: false,
        refreshedDomains: null,
        lateStampRenderCount: 0,
        manager: undefined as unknown as CanvasAssetManager,
        applyRoleUI(nextOrator: boolean): void {
          state.orator = Boolean(nextOrator);
          state.manager.updateStampTabsVisibility();
        },
        setStampManagerOpen(open: boolean): void {
          state.managerOpen = Boolean(open);
          if (paneEl) {
            paneEl.style.display = state.managerOpen ? 'flex' : 'none';
          }
          if (state.managerOpen) {
            state.manager.updateStampTabsVisibility();
          }
        },
        renderStampManagerGrid(stamps: TheaterStampSummary[]): void {
          state.lateStampRenderCount += 1;
          if (countEl && state.activeTab === 'stamps') {
            countEl.textContent = `${stamps.length}/10`;
          }
        },
      };

      state.manager = createCanvasAssetManager({
        getTheaterId: () => state.theaterId,
        isOrator: () => state.orator,
        isStampOwner: () => state.stampOwner,
        isManagerOpen: () => state.managerOpen,
        getActiveTab: () => state.activeTab,
        setActiveTab: (tab) => {
          state.activeTab = tab;
        },
        getCachedScenes: () => state.cachedScenes,
        setCachedScenes: (scenes) => {
          state.cachedScenes = scenes;
        },
        getCachedCharacters: () => state.cachedCharacters,
        setCachedCharacters: (characters) => {
          state.cachedCharacters = characters;
        },
        getCurrentTrackSrc: () => state.currentTrackSrc,
        setCurrentTrackSrc: (src) => {
          state.currentTrackSrc = src;
        },
        showShareToast: (msg) => {
          state.lastToast = msg;
        },
        fetchLatestImage: () => {
          state.imageRefreshed = true;
        },
        refreshCanvasState: (domains) => {
          state.refreshedDomains = domains ?? null;
        },
        onShowStampsTab: () => {
          state.renderStampManagerGrid(state.cachedUserStamps);
        },
        bindPrimaryTabButtons: true,
      });

      win.__assetHarness = state;
      if (openManager) {
        state.setStampManagerOpen(true);
      } else {
        state.applyRoleUI(isOrator);
      }
    },
    {
      moduleUrl: ASSET_MANAGER_MODULE_URL,
      isOrator: options.isOrator ?? true,
      isStampOwner: options.isStampOwner ?? false,
      openManager: options.openManager ?? true,
    },
  );
}

// 1. Replaces test_canvas_html_and_css_scenes_characters_wiring
test('canvas.html and chat.css wire stamp manager tabs, grids, and computed card styles', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({
    isOrator: true,
    viewport: { width: 1400, height: 900 },
    apiRoutes: {
      '/api/theaters/stage/scenes': [
        { id: 's1', name: 'Scene One', description: 'First scene', image_url: null },
      ],
      '/api/theaters/stage/characters': [
        { id: 'c1', name: 'Char One', description: 'First char', image_url: null },
      ],
    },
  });

  await page.locator('#chat-open-stamps-btn').click();
  await expect(page.locator('#stamp-manager-tabs')).toBeVisible();
  await expect(page.locator('#stamp-tab-stamps')).toBeVisible();
  await expect(page.locator('#stamp-tab-scenes')).toBeVisible();
  await expect(page.locator('#stamp-tab-characters')).toBeVisible();
  await expect(page.locator('#stamp-tab-playlists')).toBeVisible();
  await expect(page.locator('#stamp-tab-animations')).toBeVisible();

  const tabsDisplay = await page
    .locator('#stamp-manager-tabs')
    .evaluate((el) => getComputedStyle(el).display);
  expect(tabsDisplay).toBe('flex');

  await page.locator('#stamp-tab-scenes').click();
  await expect(page.locator('#scenes-manager-grid')).toBeVisible();
  await expect(page.locator('.scene-card')).toHaveCount(1);
  const scenesGridStyle = await page
    .locator('#scenes-manager-grid')
    .evaluate((el) => ({
      display: getComputedStyle(el).display,
      cursor: getComputedStyle(el.querySelector('.scene-card')!).cursor,
    }));
  expect(scenesGridStyle.display).toBe('grid');
  expect(scenesGridStyle.cursor).toBe('pointer');
  await expect(page.locator('.scene-card-thumb-container')).toHaveCount(1);
  await expect(page.locator('.scene-card-push-badge')).toHaveText('Push');

  await page.locator('#stamp-tab-characters').click();
  await expect(page.locator('#characters-manager-grid')).toBeVisible();
  await expect(page.locator('.character-card')).toHaveCount(1);
  const charsGridDisplay = await page
    .locator('#characters-manager-grid')
    .evaluate((el) => getComputedStyle(el).display);
  expect(charsGridDisplay).toBe('grid');
  await expect(page.locator('.character-card-thumb-container')).toHaveCount(1);
  await expect(page.locator('.character-card-push-badge')).toHaveText('Push');
});

// 2. Replaces test_playlist_headers_push_entire_playlist_or_selected_track
test('playlist headers push entire playlist or selected track and toggle accordion', async ({
  canvas,
  page,
}) => {
  await mountAssetManagerHarness(canvas, page, { isOrator: true, openManager: true });
  const pushedIds: string[] = [];

  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/playlists',
    async (route) => {
      await route.fulfill({
        json: [
          {
            id: 'playlist:Calm',
            name: 'Calm',
            tracks: [
              { id: 'calm-a', name: 'First track' },
              { id: 'calm-b', name: 'Second track' },
            ],
          },
          {
            id: 'playlist:Battle',
            name: 'Battle',
            tracks: [{ id: 'battle-a', name: 'Battle track' }],
          },
        ],
      });
    },
  );
  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/playlists/push',
    async (route) => {
      const body = JSON.parse(route.request().postData() || '{}') as { id: string };
      pushedIds.push(body.id);
      await route.fulfill({ json: { status: 'ok', message: 'Playing music.' } });
    },
  );

  await page.locator('#stamp-tab-playlists').click();

  const calmGroup = page.locator('.playlist-group[data-playlist-name="Calm"]');
  const battleGroup = page.locator('.playlist-group[data-playlist-name="Battle"]');
  await expect(page.locator('.playlist-group')).toHaveCount(2);
  await expect(calmGroup.locator('.playlist-header-toggle')).toHaveAttribute('aria-expanded', 'false');
  await expect(battleGroup.locator('.playlist-header-toggle')).toHaveAttribute('aria-expanded', 'false');
  await expect(calmGroup.locator('.asset-track-list')).toBeHidden();
  await expect(battleGroup.locator('.asset-track-list')).toBeHidden();
  expect(pushedIds).toEqual([]);

  // Expand Calm: track list becomes visible.
  await calmGroup.locator('.playlist-header-toggle').click();
  await expect(calmGroup.locator('.playlist-header-toggle')).toHaveAttribute('aria-expanded', 'true');
  await expect(calmGroup.locator('.asset-track-list')).toBeVisible();
  await expect(calmGroup.locator('.asset-track-list .asset-push-button')).toHaveCount(2);
  await expect(calmGroup.locator('.asset-track-list .asset-push-button small').first()).toHaveText('Push');

  // Collapse Calm again.
  await calmGroup.locator('.playlist-header-toggle').click();
  await expect(calmGroup.locator('.playlist-header-toggle')).toHaveAttribute('aria-expanded', 'false');
  await expect(calmGroup.locator('.asset-track-list')).toBeHidden();

  // Push whole Calm playlist: auto-expands it.
  const playlistButton = page.locator('[data-asset-id="playlist:Calm"]');
  await playlistButton.click();
  await expect(playlistButton.locator('small')).toHaveText('Pushed!');
  await expect(calmGroup.locator('.asset-track-list')).toBeVisible();

  // Push whole Battle playlist: auto-expands Battle and reverts Calm's button.
  const battleButton = page.locator('[data-asset-id="playlist:Battle"]');
  await battleButton.click();
  await expect(battleButton.locator('small')).toHaveText('Pushed!');
  await expect(playlistButton.locator('small')).toHaveText('Push');
  await expect(playlistButton.locator('.asset-label')).toBeVisible();
  await expect(playlistButton.locator('.asset-label')).toHaveText('Push');
  await expect(battleGroup.locator('.asset-track-list')).toBeVisible();
  await expect(battleGroup.locator('.asset-track-list .asset-push-button')).toHaveCount(1);

  // Push individual track: reverts Battle playlist header button.
  const trackButton = page.locator('[data-asset-id="battle-a"]');
  await trackButton.click();
  await expect(trackButton.locator('small')).toHaveText('Pushed!');
  await expect(battleButton.locator('small')).toHaveText('Push');
  await expect(battleButton.locator('.asset-label')).toBeVisible();
  await expect(battleButton.locator('.asset-label')).toHaveText('Push');

  expect(pushedIds).toEqual(['playlist:Calm', 'playlist:Battle', 'battle-a']);
  await page.locator('#stamp-tab-scenes').click();
  await expect(page.locator('#playlists-manager')).toBeHidden();
});

// 3. Replaces test_animation_tab_push_and_role_loss
test('animation tab pushes animation and falls back to stamps on orator role loss', async ({
  canvas,
  page,
}) => {
  await mountAssetManagerHarness(canvas, page, { isOrator: true, openManager: true });

  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/animations',
    async (route) => {
      await route.fulfill({
        json: [{ id: 'storm', name: 'Storm', description: 'A rolling storm', type: 'video', url: '' }],
      });
    },
  );
  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/animations/push',
    async (route) => {
      expect(JSON.parse(route.request().postData() || '{}')).toEqual({ id: 'storm' });
      await route.fulfill({ json: { status: 'ok', message: 'Playing Storm.' } });
    },
  );

  await page.locator('#stamp-tab-animations').click();
  await expect(page.locator('#animations-manager .asset-push-button')).toHaveCount(1);
  await page.locator('#animations-manager .asset-push-button').click();
  await expect(page.locator('#animations-manager small')).toHaveText('Pushed!');

  await page.evaluate(() => {
    (window as unknown as AssetHarnessWindow).__assetHarness.applyRoleUI(false);
  });
  await expect(page.locator('#animations-manager')).toBeHidden();
  expect(
    await page.evaluate(() => (window as unknown as AssetHarnessWindow).__assetHarness.activeTab),
  ).toBe('stamps');
});

// 4. Replaces test_animation_tab_renders_thumbnails_rather_than_titles
test('animation tab renders image, video, and fallback thumbnails rather than titles', async ({
  canvas,
  page,
}) => {
  await mountAssetManagerHarness(canvas, page, { isOrator: true, openManager: true });

  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/animations',
    async (route) => {
      await route.fulfill({
        json: [
          {
            id: 'storm',
            name: 'Storm',
            description: 'A rolling storm',
            type: 'layered',
            url: '/theaters/stage/animations/storm/base.webp',
          },
          {
            id: 'portal',
            name: 'Portal',
            description: 'Swirling vortex',
            type: 'video',
            url: '/theaters/stage/animations/portal/video.mp4',
          },
          {
            id: 'rain',
            name: 'Rain',
            description: 'Gentle rain',
            type: 'video',
            url: '',
          },
        ],
      });
    },
  );

  await page.locator('#stamp-tab-animations').click();
  await expect(page.locator('#animations-manager')).toHaveClass('animations-grid');
  await expect(page.locator('#animations-manager .asset-push-button')).toHaveCount(3);

  const stormBtn = page.locator('#animations-manager [data-asset-id="storm"]');
  await expect(stormBtn.locator('.asset-thumb-container img.asset-thumb')).toHaveAttribute(
    'src',
    '/theaters/stage/animations/storm/base.webp',
  );
  await expect(stormBtn.locator('.asset-label')).toBeHidden();
  await expect(stormBtn.locator('small')).toHaveText('Push');

  const portalBtn = page.locator('#animations-manager [data-asset-id="portal"]');
  await expect(portalBtn.locator('.asset-thumb-container video.asset-thumb')).toHaveAttribute(
    'src',
    '/theaters/stage/animations/portal/video.mp4#t=0.001',
  );
  await expect(portalBtn.locator('.asset-label')).toBeHidden();
  await expect(portalBtn.locator('small')).toHaveText('Push');

  const rainBtn = page.locator('#animations-manager [data-asset-id="rain"]');
  await expect(rainBtn.locator('.asset-thumb-container .asset-fallback-thumb')).toHaveText('🎬');
  await expect(rainBtn.locator('.asset-label')).toBeHidden();
  await expect(rainBtn.locator('small')).toHaveText('Push');
});

// 5. Replaces test_no_music_available_without_playlist_tracks
test('no music button works when theater has no playlists', async ({ canvas, page }) => {
  await mountAssetManagerHarness(canvas, page, { isOrator: true, openManager: true });

  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/playlists',
    async (route) => {
      await route.fulfill({ json: [] });
    },
  );
  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/playlists/push',
    async (route) => {
      expect(JSON.parse(route.request().postData() || '{}')).toEqual({ id: 'no_music' });
      await route.fulfill({ json: { status: 'ok', message: 'Music stopped.' } });
    },
  );

  await page.locator('#stamp-tab-playlists').click();
  await expect(page.locator('#playlists-manager-content')).toHaveText(
    'No playlists found for this theater.',
  );
  await page.locator('#no-music-button').click();
  await expect(page.locator('#no-music-button small')).toHaveText('Pushed!');
  expect(
    await page.evaluate(() => (window as unknown as AssetHarnessWindow).__assetHarness.lastToast),
  ).toBe('Music stopped.');
});

// 6. Replaces test_playing_track_highlight_survives_tabs_and_updates_for_silence
test('playing track highlight survives expand/collapse and updates for pause, track, and silence', async ({
  canvas,
  page,
}) => {
  await mountAssetManagerHarness(canvas, page, { isOrator: true, openManager: true });

  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/playlists',
    async (route) => {
      await route.fulfill({
        json: [
          {
            id: 'playlist:Calm',
            name: 'Calm',
            tracks: [{ id: '/calm.mp3', url: '/calm.mp3', name: 'Calm track' }],
          },
          {
            id: 'playlist:Battle',
            name: 'Battle',
            tracks: [{ id: '/battle.mp3', url: '/battle.mp3', name: 'Battle track' }],
          },
        ],
      });
    },
  );
  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/playlists/push',
    async (route) => {
      await route.fulfill({ json: { status: 'ok', message: 'Playing playlist.' } });
    },
  );

  await page.evaluate(() => {
    (window as unknown as AssetHarnessWindow).__assetHarness.manager.updateAssetPlaybackSelection({
      music_id: 'Battle',
      tracks: ['/battle.mp3'],
      paused: false,
    });
  });
  await page.locator('#stamp-tab-playlists').click();

  const battleToggle = page.locator('.playlist-header-toggle[data-playlist-name="Battle"]');
  const playlistPlaying = page.locator('[data-asset-id="playlist:Battle"]');
  const calmButton = page.locator('[data-asset-id="playlist:Calm"]');

  await expect(playlistPlaying).toHaveAttribute('aria-current', 'true');
  await expect(page.locator('.playlist-group[data-playlist-name="Battle"] .asset-track-list')).toBeHidden();
  await expect(playlistPlaying.locator('.asset-label')).toBeHidden();
  await expect(calmButton.locator('.asset-label')).toBeVisible();
  await expect(calmButton.locator('.asset-label')).toHaveText('Push');

  // Expand Battle: track list visible and playing track highlighted.
  await battleToggle.click();
  await expect(page.locator('.playlist-group[data-playlist-name="Battle"] .asset-track-list')).toBeVisible();
  const playing = page.locator('#playlists-manager-content .currently-playing[data-asset-id="/battle.mp3"]');
  await expect(playing).toHaveCount(1);
  await expect(playing.locator('.asset-playing-indicator')).toHaveText('▶ Playing');
  await expect(playing).toHaveAttribute('aria-current', 'true');

  // Minimize Battle: header keeps active indicator.
  await battleToggle.click();
  await expect(page.locator('.playlist-group[data-playlist-name="Battle"] .asset-track-list')).toBeHidden();
  await expect(playlistPlaying).toHaveAttribute('aria-current', 'true');

  // Re-expand Battle.
  await battleToggle.click();
  await expect(page.locator('.playlist-group[data-playlist-name="Battle"] .asset-track-list')).toBeVisible();
  await expect(playing).toHaveCount(1);

  // Paused state.
  await page.evaluate(() => {
    (window as unknown as AssetHarnessWindow).__assetHarness.manager.updateAssetPlaybackSelection({
      music_id: 'Battle',
      tracks: ['/battle.mp3'],
      paused: true,
    });
  });
  await expect(playing.locator('.asset-playing-indicator')).toHaveText('Ⅱ Paused');
  await expect(playlistPlaying.locator('.asset-playing-indicator')).toHaveText('Ⅱ Paused');
  await expect(playlistPlaying.locator('.asset-label')).toBeHidden();

  // Specific track selected instead of playlist.
  await page.evaluate(() => {
    (window as unknown as AssetHarnessWindow).__assetHarness.manager.updateAssetPlaybackSelection({
      music_id: 'Battle / Battle track',
      tracks: ['/battle.mp3'],
      paused: false,
    });
  });
  await expect(playlistPlaying).not.toHaveAttribute('aria-current', 'true');
  await expect(playlistPlaying.locator('.asset-label')).toBeVisible();
  await expect(playlistPlaying.locator('.asset-label')).toHaveText('Push');
  await expect(playing).toHaveCount(1);

  // Silence selects No Music and restores Push on playlist chips.
  await page.evaluate(() => {
    (window as unknown as AssetHarnessWindow).__assetHarness.manager.updateAssetPlaybackSelection({
      music_id: '',
      tracks: [],
      paused: false,
    });
  });
  await expect(playing).toHaveCount(0);
  await expect(page.locator('#no-music-button')).toHaveClass('asset-push-button currently-playing');
  await expect(page.locator('#no-music-button .asset-playing-indicator')).toHaveText('✓ Selected');
  await expect(playlistPlaying.locator('.asset-label')).toBeVisible();
  await expect(playlistPlaying.locator('.asset-label')).toHaveText('Push');
  await expect(calmButton.locator('.asset-label')).toBeVisible();
  await expect(calmButton.locator('.asset-label')).toHaveText('Push');

  // Selecting Calm drops Push on Calm while Battle retains Push.
  await page.evaluate(() => {
    (window as unknown as AssetHarnessWindow).__assetHarness.manager.updateAssetPlaybackSelection({
      music_id: 'Calm',
      tracks: ['/calm.mp3'],
      paused: false,
    });
  });
  await expect(calmButton.locator('.asset-label')).toBeHidden();
  await expect(calmButton.locator('.asset-playing-indicator')).toHaveText('▶ Playing');
  await expect(playlistPlaying.locator('.asset-label')).toBeVisible();
  await expect(playlistPlaying.locator('.asset-label')).toHaveText('Push');
});

// 7. Replaces test_stamp_manager_tabs_visibility_and_orator_restriction
test('stamp manager tabs hide for viewers and enforce orator restriction', async ({
  canvas,
  page,
}) => {
  await mountAssetManagerHarness(canvas, page, { isOrator: false, openManager: true });

  await expect(page.locator('#stamp-manager-tabs')).toBeHidden();

  await page.evaluate(() => {
    (window as unknown as AssetHarnessWindow).__assetHarness.manager.switchStampManagerTab('scenes');
  });
  expect(
    await page.evaluate(() => (window as unknown as AssetHarnessWindow).__assetHarness.activeTab),
  ).toBe('stamps');

  await page.evaluate(() => {
    const h = (window as unknown as AssetHarnessWindow).__assetHarness;
    h.applyRoleUI(true);
    h.cachedScenes = [{ id: 's1', name: 'Scene 1', description: 'Desc 1', image_url: null }];
    h.cachedCharacters = [
      { id: 'c1', name: 'Char 1', gender: 'Any', voice_tags: [], description: 'Desc', image_url: null },
    ];
  });
  await expect(page.locator('#stamp-manager-tabs')).toBeVisible();

  await page.locator('#stamp-tab-scenes').click();
  expect(
    await page.evaluate(() => (window as unknown as AssetHarnessWindow).__assetHarness.activeTab),
  ).toBe('scenes');
  expect(
    await page.locator('#scenes-manager-grid').evaluate((el) => (el as HTMLElement).style.display),
  ).toBe('grid');
  await expect(page.locator('#scenes-manager-grid')).toBeVisible();
  await expect(page.locator('#stamp-manager-grid')).toBeHidden();
  await expect(page.locator('#characters-manager-grid')).toBeHidden();

  await page.locator('#stamp-tab-characters').click();
  expect(
    await page.evaluate(() => (window as unknown as AssetHarnessWindow).__assetHarness.activeTab),
  ).toBe('characters');
  expect(
    await page.locator('#characters-manager-grid').evaluate((el) => (el as HTMLElement).style.display),
  ).toBe('grid');
  await expect(page.locator('#characters-manager-grid')).toBeVisible();
  await expect(page.locator('#scenes-manager-grid')).toBeHidden();
  await expect(page.locator('#stamp-manager-grid')).toBeHidden();

  await page.evaluate(() => {
    (window as unknown as AssetHarnessWindow).__assetHarness.applyRoleUI(false);
  });
  await expect(page.locator('#stamp-manager-tabs')).toBeHidden();
  expect(
    await page.evaluate(() => (window as unknown as AssetHarnessWindow).__assetHarness.activeTab),
  ).toBe('stamps');
});

// 8. Replaces test_scenes_tab_push_to_canvas
test('scenes tab renders scene cards and pushes selected scene to canvas', async ({
  canvas,
  page,
}) => {
  await mountAssetManagerHarness(canvas, page, { isOrator: true, openManager: true });
  const pushedRequests: Array<{ name: string; scene_id: string }> = [];

  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/scenes/push',
    async (route) => {
      pushedRequests.push(JSON.parse(route.request().postData() || '{}') as { name: string; scene_id: string });
      await route.fulfill({ json: { status: 'ok', scene: 'Grand Ballroom' } });
    },
  );

  await page.evaluate(() => {
    const h = (window as unknown as AssetHarnessWindow).__assetHarness;
    h.cachedScenes = [
      {
        id: 'ballroom',
        name: 'Grand Ballroom',
        description: 'A magnificent ballroom with chandeliers',
        image_url: '/img/ballroom.png',
      },
      { id: 'dungeon', name: 'Dark Dungeon', description: 'Damp and dark cells', image_url: null },
    ];
    h.manager.switchStampManagerTab('scenes');
  });

  await expect(page.locator('.scene-card')).toHaveCount(2);
  expect(await page.locator('.scene-card-name').allTextContents()).toEqual([
    'Grand Ballroom',
    'Dark Dungeon',
  ]);
  await expect(page.locator('#stamp-manager-count')).toHaveText('2');
  expect(await page.locator('.scene-card-push-badge').allTextContents()).toEqual(['Push', 'Push']);

  const responsePromise = page.waitForResponse('**/api/theaters/stage/scenes/push');
  await page.locator('.scene-card[data-scene-id="ballroom"]').click();
  await responsePromise;

  expect(pushedRequests).toEqual([{ name: 'Grand Ballroom', scene_id: 'ballroom' }]);
  expect(
    await page.evaluate(() => (window as unknown as AssetHarnessWindow).__assetHarness.imageRefreshed),
  ).toBe(true);
});

// 9. Replaces test_characters_tab_push_to_canvas
test('characters tab renders character cards and pushes selected character to canvas', async ({
  canvas,
  page,
}) => {
  await mountAssetManagerHarness(canvas, page, { isOrator: true, openManager: true });
  const pushedRequests: Array<{ name: string; character_id: string }> = [];

  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/characters/push',
    async (route) => {
      pushedRequests.push(
        JSON.parse(route.request().postData() || '{}') as { name: string; character_id: string },
      );
      await route.fulfill({ json: { status: 'ok', character_name: 'Elena Vance' } });
    },
  );

  await page.evaluate(() => {
    const h = (window as unknown as AssetHarnessWindow).__assetHarness;
    h.cachedCharacters = [
      {
        id: 'elena',
        name: 'Elena Vance',
        description: 'A daring archaeologist',
        image_url: '/img/elena.png',
      },
      { id: 'marcus', name: 'Marcus Gray', description: 'A grumpy tavern keeper', image_url: null },
    ];
    h.manager.switchStampManagerTab('characters');
  });

  await expect(page.locator('.character-card')).toHaveCount(2);
  expect(await page.locator('.character-card-name').allTextContents()).toEqual([
    'Elena Vance',
    'Marcus Gray',
  ]);
  await expect(page.locator('#stamp-manager-count')).toHaveText('2');
  expect(await page.locator('.character-card-push-badge').allTextContents()).toEqual(['Push', 'Push']);

  const responsePromise = page.waitForResponse('**/api/theaters/stage/characters/push');
  await page.locator('.character-card[data-character-id="elena"]').click();
  await responsePromise;

  expect(pushedRequests).toEqual([{ name: 'Elena Vance', character_id: 'elena' }]);
  expect(
    await page.evaluate(() => (window as unknown as AssetHarnessWindow).__assetHarness.imageRefreshed),
  ).toBe(true);
});

// 10. Replaces test_characters_tab_renders_descriptions_without_gender_or_voice_tags
test('characters tab renders descriptions without gender or voice tags', async ({
  canvas,
  page,
}) => {
  await mountAssetManagerHarness(canvas, page, { isOrator: true, openManager: true });

  await page.evaluate(() => {
    const h = (window as unknown as AssetHarnessWindow).__assetHarness;
    h.cachedCharacters = [
      {
        id: 'elena',
        name: 'Elena Vance',
        gender: 'Female',
        voice_tags: ['Warm', 'Confident'],
        description: 'A daring archaeologist who loves ancient riddles.',
        image_url: '/img/elena.png',
      },
      {
        id: 'marcus',
        name: 'Marcus Gray',
        gender: 'Male',
        voice_tags: ['Gruff'],
        description: 'A grumpy tavern keeper with a heart of gold.',
        image_url: null,
      },
    ];
    h.manager.switchStampManagerTab('characters');
  });

  await expect(page.locator('.character-card')).toHaveCount(2);
  expect(await page.locator('.character-card-name').allTextContents()).toEqual([
    'Elena Vance',
    'Marcus Gray',
  ]);
  await expect(page.locator('#stamp-manager-count')).toHaveText('2');
  await expect(page.locator('.character-trait-badge')).toHaveCount(0);
  await expect(
    page.locator('.character-card[data-character-id="elena"] .character-card-desc'),
  ).toHaveText('A daring archaeologist who loves ancient riddles.');
  await expect(
    page.locator('.character-card[data-character-id="marcus"] .character-card-desc'),
  ).toHaveText('A grumpy tavern keeper with a heart of gold.');
});

// 11. Replaces test_scenes_and_characters_count_not_overwritten_by_late_stamp_render_and_images_visible
test('scenes and characters counts resist late stamp render and preload images', async ({
  canvas,
  page,
}) => {
  await mountAssetManagerHarness(canvas, page, { isOrator: true, openManager: true });

  await page.evaluate(() => {
    const h = (window as unknown as AssetHarnessWindow).__assetHarness;
    h.cachedScenes = [
      { id: 'throne', name: 'Throne Room', description: 'Royal hall', image_url: '/img/throne.png' },
      { id: 'armory', name: 'Armory', description: 'Weapons', url: '/img/armory.png' },
      { id: 'tower', name: 'Tower', description: 'High tower', image_url: null },
    ];
    h.manager.switchStampManagerTab('scenes');
  });

  await expect(page.locator('#stamp-manager-count')).toHaveText('3');
  await expect(page.locator('.scene-card[data-scene-id="throne"] img.scene-card-thumb')).toHaveAttribute(
    'src',
    '/img/throne.png',
  );
  await expect(page.locator('.scene-card[data-scene-id="armory"] img.scene-card-thumb')).toHaveAttribute(
    'src',
    '/img/armory.png',
  );

  await page.evaluate(() => {
    const h = (window as unknown as AssetHarnessWindow).__assetHarness;
    h.cachedUserStamps = [{ id: 1 }, { id: 2 }];
    h.renderStampManagerGrid(h.cachedUserStamps);
  });
  await expect(page.locator('#stamp-manager-count')).toHaveText('3');

  await page.evaluate(() => {
    const h = (window as unknown as AssetHarnessWindow).__assetHarness;
    h.cachedCharacters = [
      { id: 'c1', name: 'Knight', image_url: '/img/knight.png' },
      { id: 'c2', name: 'Mage', url: '/img/mage.png' },
    ];
    h.manager.switchStampManagerTab('characters');
  });

  await expect(page.locator('#stamp-manager-count')).toHaveText('2');
  await expect(
    page.locator('.character-card[data-character-id="c1"] img.character-card-thumb'),
  ).toHaveAttribute('src', '/img/knight.png');
  await expect(
    page.locator('.character-card[data-character-id="c2"] img.character-card-thumb'),
  ).toHaveAttribute('src', '/img/mage.png');

  await page.evaluate(() => {
    const h = (window as unknown as AssetHarnessWindow).__assetHarness;
    h.cachedUserStamps = [{ id: 1 }, { id: 2 }, { id: 3 }];
    h.renderStampManagerGrid(h.cachedUserStamps);
  });
  await expect(page.locator('#stamp-manager-count')).toHaveText('2');

  await page.evaluate(() => {
    (window as unknown as AssetHarnessWindow).__assetHarness.manager.switchStampManagerTab('stamps');
  });
  await expect(page.locator('#stamp-manager-count')).toHaveText('3/10');

  const preloaded = await page.evaluate(() =>
    Array.from((window as unknown as AssetHarnessWindow).__assetHarness.manager.getPreloadedImageUrls()),
  );
  expect(preloaded).toEqual(
    expect.arrayContaining(['/img/throne.png', '/img/armory.png', '/img/knight.png', '/img/mage.png']),
  );
});

// 12. Replaces test_non_recent_push_items_revert_properly
test('non-recent pushed scenes, animations, and characters revert badges properly', async ({
  canvas,
  page,
}) => {
  await mountAssetManagerHarness(canvas, page, { isOrator: true, openManager: true });

  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/scenes/push',
    async (route) => route.fulfill({ json: { status: 'ok', scene: 'Done' } }),
  );
  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/animations/push',
    async (route) => route.fulfill({ json: { status: 'ok', message: 'Animation playing.' } }),
  );
  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/animations',
    async (route) =>
      route.fulfill({
        json: [
          { id: 'anim1', name: 'Rain', description: 'Rain anim', type: 'video', url: '' },
          { id: 'anim2', name: 'Snow', description: 'Snow anim', type: 'video', url: '' },
        ],
      }),
  );
  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/characters/push',
    async (route) => route.fulfill({ json: { status: 'ok', character_name: 'Char' } }),
  );

  // Scenes
  await page.evaluate(() => {
    const h = (window as unknown as AssetHarnessWindow).__assetHarness;
    h.cachedScenes = [
      { id: 's1', name: 'Scene One', description: 'First scene', image_url: null },
      { id: 's2', name: 'Scene Two', description: 'Second scene', image_url: null },
    ];
    h.manager.switchStampManagerTab('scenes');
  });

  const sceneBadge1 = page.locator('.scene-card[data-scene-id="s1"] .scene-card-push-badge');
  const sceneBadge2 = page.locator('.scene-card[data-scene-id="s2"] .scene-card-push-badge');
  await expect(sceneBadge1).toHaveText('Push');
  await expect(sceneBadge2).toHaveText('Push');
  await page.locator('.scene-card[data-scene-id="s1"]').click();
  await expect(sceneBadge1).toHaveText('Pushed!');
  await expect(sceneBadge2).toHaveText('Push');
  await page.locator('.scene-card[data-scene-id="s2"]').click();
  await expect(sceneBadge2).toHaveText('Pushed!');
  await expect(sceneBadge1).toHaveText('Push');

  // Animations
  await page.locator('#stamp-tab-animations').click();
  const animBtn1 = page.locator('#animations-manager [data-asset-id="anim1"]');
  const animBtn2 = page.locator('#animations-manager [data-asset-id="anim2"]');
  await expect(animBtn1.locator('small')).toHaveText('Push');
  await expect(animBtn2.locator('small')).toHaveText('Push');
  await animBtn1.click();
  await expect(animBtn1.locator('small')).toHaveText('Pushed!');
  await expect(animBtn2.locator('small')).toHaveText('Push');
  await animBtn2.click();
  await expect(animBtn2.locator('small')).toHaveText('Pushed!');
  await expect(animBtn1.locator('small')).toHaveText('Push');

  // Characters
  await page.evaluate(() => {
    const h = (window as unknown as AssetHarnessWindow).__assetHarness;
    h.cachedCharacters = [
      { id: 'c1', name: 'Char One', description: 'First char', image_url: null },
      { id: 'c2', name: 'Char Two', description: 'Second char', image_url: null },
    ];
    h.manager.switchStampManagerTab('characters');
  });
  const charBadge1 = page.locator('.character-card[data-character-id="c1"] .character-card-push-badge');
  const charBadge2 = page.locator('.character-card[data-character-id="c2"] .character-card-push-badge');
  await expect(charBadge1).toHaveText('Push');
  await expect(charBadge2).toHaveText('Push');
  await page.locator('.character-card[data-character-id="c1"]').click();
  await expect(charBadge1).toHaveText('Pushed!');
  await expect(charBadge2).toHaveText('Push');
  await page.locator('.character-card[data-character-id="c2"]').click();
  await expect(charBadge2).toHaveText('Pushed!');
  await expect(charBadge1).toHaveText('Push');
});

// 13. Replaces test_asset_manager_tabs_scroll_properly
test('asset manager body scrolls overflow and resets scrollTop on tab switches', async ({
  canvas,
  page,
}) => {
  await mountAssetManagerHarness(canvas, page, { isOrator: true, openManager: true });

  await page.evaluate(() => {
    const h = (window as unknown as AssetHarnessWindow).__assetHarness;
    h.cachedCharacters = Array.from({ length: 30 }, (_, idx) => ({
      id: `c_${idx + 1}`,
      name: `Character ${idx + 1}`,
      description: `Desc for character ${idx + 1}`,
    }));
    h.manager.switchStampManagerTab('characters');
  });

  const canScrollChars = await page.evaluate(() => {
    const body = document.getElementById('stamp-manager-body')!;
    return body.scrollHeight > body.clientHeight;
  });
  expect(canScrollChars).toBe(true);

  await page.evaluate(() => {
    document.getElementById('stamp-manager-body')!.scrollTop = 200;
  });
  expect(
    await page.evaluate(() => document.getElementById('stamp-manager-body')!.scrollTop),
  ).toBeGreaterThan(0);

  await page.evaluate(() => {
    const h = (window as unknown as AssetHarnessWindow).__assetHarness;
    h.cachedScenes = Array.from({ length: 24 }, (_, idx) => ({
      id: `s_${idx + 1}`,
      name: `Scene ${idx + 1}`,
      description: `Desc for scene ${idx + 1}`,
    }));
    h.manager.switchStampManagerTab('scenes');
  });

  expect(
    await page.evaluate(() => document.getElementById('stamp-manager-body')!.scrollTop),
  ).toBe(0);
  const canScrollScenes = await page.evaluate(() => {
    const body = document.getElementById('stamp-manager-body')!;
    return body.scrollHeight > body.clientHeight;
  });
  expect(canScrollScenes).toBe(true);
});

// 14. Replaces test_scene_generator_visibility_and_submission
test('scene generator visibility, upward preset menu geometry, and Enter submission', async ({
  canvas,
  page,
}) => {
  await mountAssetManagerHarness(canvas, page, {
    isOrator: true,
    isStampOwner: false,
    openManager: true,
  });

  const requests: Array<{ name: string; prompt: string; scene_type: string }> = [];
  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/scenes/generate',
    async (route) => {
      requests.push(
        JSON.parse(route.request().postData() || '{}') as {
          name: string;
          prompt: string;
          scene_type: string;
        },
      );
      await route.fulfill({
        json: {
          scenes: [{ id: 'forest', name: 'Forest map', url: '/img/forest.png' }],
          credits_charged: 4,
        },
      });
    },
  );

  await page.evaluate(() => {
    (window as unknown as AssetHarnessWindow).__assetHarness.manager.switchStampManagerTab('scenes');
  });
  await expect(page.locator('#scene-generation-form')).toBeHidden();

  await page.evaluate(() => {
    const h = (window as unknown as AssetHarnessWindow).__assetHarness;
    h.stampOwner = true;
    h.manager.switchStampManagerTab('scenes');
  });
  await expect(page.locator('#scene-generation-form')).toBeVisible();
  await expect(page.locator('#stamp-generation-form')).toBeHidden();
  await expect(page.locator('#scene-generation-type')).toHaveValue('none');
  await expect(page.locator('#scene-generation-type-label')).toHaveText('No preset');
  await expect(page.locator('#scene-generation-submit')).toHaveCount(0);

  await page.locator('#scene-generation-presets summary').click();
  const menuBox = await page.locator('.scene-preset-menu').boundingBox();
  const triggerBox = await page.locator('#scene-generation-presets summary').boundingBox();
  expect(menuBox).not.toBeNull();
  expect(triggerBox).not.toBeNull();
  expect(menuBox!.y + menuBox!.height).toBeLessThan(triggerBox!.y);

  await expect(page.getByRole('button', { name: 'Cinematic', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Tactical Map', exact: true }).click();
  await expect(page.locator('.scene-preset-menu')).toBeHidden();

  await page.locator('#scene-generation-prompt').fill('A forest clearing');
  await page.locator('#scene-generation-prompt').press('Enter');

  await expect(page.locator('#scene-generation-status')).toContainText('Charged 4 credits');
  await expect(page.locator('#scenes-manager-grid .scene-card')).toHaveCount(1);
  expect(requests).toEqual([
    { name: 'forest clearing', prompt: 'A forest clearing', scene_type: 'tactical_map' },
  ]);
  await expect(page.locator('#scene-generation-prompt')).toHaveValue('');

  await page.evaluate(() => {
    const h = (window as unknown as AssetHarnessWindow).__assetHarness;
    h.cachedCharacters = [];
    h.manager.switchStampManagerTab('characters');
  });
  await expect(page.locator('#scene-generation-form')).toBeHidden();
  await expect(page.locator('#scene-generation-status')).toBeHidden();

  await page.evaluate(() => {
    (window as unknown as AssetHarnessWindow).__assetHarness.manager.switchStampManagerTab('stamps');
  });
  await expect(page.locator('#scene-generation-form')).toBeHidden();
});

// 15. Replaces test_scene_generator_preserves_prompt_after_failure
test('scene generator preserves prompt and preset after server failure', async ({
  canvas,
  page,
}) => {
  await mountAssetManagerHarness(canvas, page, {
    isOrator: true,
    isStampOwner: true,
    openManager: true,
  });

  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/scenes/generate',
    async (route) => {
      await route.fulfill({
        status: 502,
        json: { detail: 'Generation failed; no credits were charged.' },
      });
    },
  );

  await page.evaluate(() => {
    (window as unknown as AssetHarnessWindow).__assetHarness.manager.switchStampManagerTab('scenes');
  });

  await page.locator('#scene-generation-presets summary').click();
  await page.getByRole('button', { name: 'Regional Map', exact: true }).click();
  await page.locator('#scene-generation-prompt').fill('An island kingdom');
  await page.locator('#scene-generation-prompt').press('Enter');

  await expect(page.locator('#scene-generation-status')).toContainText('no credits were charged');
  await expect(page.locator('#scene-generation-prompt')).toHaveValue('An island kingdom');
  await expect(page.locator('#scene-generation-type')).toHaveValue('regional_map');
  await expect(page.locator('#scene-generation-form')).toHaveAttribute('aria-busy', 'false');
});

// 16. Replaces test_canvas_asset_manager_reference_tagging_autocomplete_and_submission
test('canvas asset manager @ reference tagging autocomplete and submission across tabs', async ({
  canvas,
  page,
}) => {
  const submitted: Record<string, { references?: string[] }> = {};
  const makeHandler =
    (kindKey: string) =>
    async (route: Route): Promise<void> => {
      submitted[kindKey] = JSON.parse(route.request().postData() || '{}') as {
        references?: string[];
      };
      await route.fulfill({
        json: {
          [kindKey]: [
            { id: 'new1', name: 'New Asset', url: '/static/narratron-avatar.png', image_url: null },
          ],
          credits_charged: 4,
        },
      });
    };

  await canvas.openCanvas({
    isOrator: true,
    viewport: { width: 1400, height: 900 },
    apiRoutes: {
      '/api/theaters/stage': {
        metadata: { is_owner: true, is_active_orator: true },
        references: [],
      },
      '/api/users/me/stamps': [],
      '/api/theaters/stage/stamps': [
        {
          id: 'theater:stage:goblin.webp',
          name: 'goblin',
          url: '/static/narratron-avatar.png',
          is_theater_stamp: true,
        },
      ],
      '/api/theaters/stage/scenes': [
        { id: 'pier', name: 'Moonlit Pier', description: 'Misty pier', image_url: null },
      ],
      '/api/theaters/stage/characters': [
        { id: 'zara', name: 'Captain Zara', description: 'Pirate captain', image_url: null },
      ],
      '/api/theaters/stage/animations': [],
      '/api/theaters/stage/playlists': [],
      '/api/theaters/stage/scenes/generate': makeHandler('scenes'),
      '/api/theaters/stage/characters/generate': makeHandler('characters'),
      '/api/theaters/stage/animations/generate': makeHandler('animations'),
      '/api/theaters/stage/stamps/generate': makeHandler('stamps'),
    },
  });

  // Open Assets pane and populate theater stamps, characters, and scenes caches cleanly.
  await page.locator('#chat-open-stamps-btn').click();
  await expect(page.locator('.stamp-card[data-stamp-name="goblin"]')).toBeVisible();
  await page.locator('#stamp-tab-characters').click();
  await expect(page.locator('.character-card[data-character-id="zara"]')).toBeVisible();
  await page.locator('#stamp-tab-scenes').click();
  await expect(page.locator('.scene-card[data-scene-id="pier"]')).toBeVisible();

  // 1. Scenes tab: Tab completion and click selection.
  const scenePrompt = page.locator('#scene-generation-prompt');
  await scenePrompt.focus();
  await page.keyboard.type('Show @Cap');
  const menu = page.locator('.reference-mention-menu:visible');
  await expect(menu).toBeVisible();
  await expect(menu.locator('.reference-mention-name')).toHaveText(['@Captain Zara']);
  await page.keyboard.press('Tab');
  await expect(scenePrompt).toHaveValue('Show @Captain Zara ');
  await page.keyboard.type('on @Moon');
  await expect(menu).toBeVisible();
  await menu.locator('.reference-mention-option', { hasText: 'Moonlit Pier' }).click();
  await expect(scenePrompt).toHaveValue('Show @Captain Zara on @Moonlit Pier ');
  await scenePrompt.press('Enter');
  await expect(page.locator('#scene-generation-status')).toContainText('Charged 4 credits');
  expect(submitted.scenes.references).toEqual(['Captain Zara', 'Moonlit Pier']);

  // 2. Characters tab: Tab completion and submission.
  await page.locator('#stamp-tab-characters').click();
  const charPrompt = page.locator('#character-generation-prompt');
  await charPrompt.focus();
  await page.keyboard.type('Update @Cap');
  await page.keyboard.press('Tab');
  await page.keyboard.type('with a tricorne hat');
  await charPrompt.press('Enter');
  await expect(page.locator('#asset-generation-status')).toContainText('Charged 4 credits');
  expect(submitted.characters.references).toEqual(['Captain Zara']);

  // 3. Animations tab: Tab completion and submission.
  await page.locator('#stamp-tab-animations').click();
  const animPrompt = page.locator('#animation-generation-prompt');
  await animPrompt.focus();
  await page.keyboard.type('@Cap');
  await page.keyboard.press('Tab');
  await page.keyboard.type('dueling on @Moon');
  await page.keyboard.press('Tab');
  await animPrompt.press('Enter');
  await expect(page.locator('#asset-generation-status')).toContainText('Charged 4 credits');
  expect(submitted.animations.references).toEqual(['Captain Zara', 'Moonlit Pier']);

  // 4. Stamps tab: Tab completion and submission with reference tag.
  await page.locator('#stamp-tab-stamps').click();
  const stampPrompt = page.locator('#stamp-generation-prompt');
  await stampPrompt.focus();
  await page.keyboard.type('Give @gob');
  await page.keyboard.press('Tab');
  await page.keyboard.type('a golden crown');
  await expect(stampPrompt).toHaveValue('Give @goblin a golden crown');
  await stampPrompt.press('Enter');
  await expect(page.locator('#stamp-generation-status')).toContainText('Charged 4 credits');
  expect(submitted.stamps.references).toEqual(['goblin']);
});

// 17. Replaces test_asset_generation_permissions_pending_and_retry (3 parametrized cases)
for (const { tab, kind } of [
  { tab: 'characters', kind: 'character' },
  { tab: 'animations', kind: 'animation' },
  { tab: 'playlists', kind: 'music' },
] as const) {
  test(`asset generation permissions, concurrent pending cards, and retry (${tab}/${kind})`, async ({
    canvas,
    page,
  }) => {
    await mountAssetManagerHarness(canvas, page, {
      isOrator: true,
      isStampOwner: false,
      openManager: true,
    });

    await canvas.serveRoute(
      (url) => url.origin === canvas.origin && url.pathname === `/api/theaters/stage/${tab}`,
      async (route) => route.fulfill({ json: [] }),
    );

    const heldRoutes: Route[] = [];
    const endpoint = kind === 'music' ? 'music' : tab;
    await canvas.serveRoute(
      (url) =>
        url.origin === canvas.origin && url.pathname === `/api/theaters/stage/${endpoint}/generate`,
      async (route) => {
        heldRoutes.push(route);
      },
    );

    await page.evaluate((targetTab) => {
      (window as unknown as AssetHarnessWindow).__assetHarness.manager.switchStampManagerTab(
        targetTab,
      );
    }, tab);

    await expect(page.locator(`#${kind}-generation-form`)).toBeHidden();
    await page.evaluate(() => {
      const h = (window as unknown as AssetHarnessWindow).__assetHarness;
      h.stampOwner = true;
      h.manager.updateAssetGenerationUI();
    });
    await expect(page.locator(`#${kind}-generation-form`)).toBeVisible();

    const prompt = page.locator(`#${kind}-generation-prompt`);
    await prompt.fill('Gentle forest rain');
    await prompt.press('Enter');
    await expect(page.locator('.asset-card-generating')).toHaveCount(1);
    await expect(prompt).toHaveValue('');
    await expect(prompt).toBeEditable();

    await prompt.fill('Next draft');
    await prompt.press('Enter');
    await expect(page.locator('.asset-card-generating')).toHaveCount(2);

    await expect.poll(() => heldRoutes.length).toBe(2);
    const expectedFirst: Record<string, string> = {
      name: 'Gentle forest rain',
      prompt: 'Gentle forest rain',
    };
    if (kind === 'animation') {
      expectedFirst.animation_type = 'video';
    }
    expect(JSON.parse(heldRoutes[0].request().postData() || '{}')).toEqual(expectedFirst);

    await heldRoutes[1].fulfill({
      status: 502,
      json: { detail: 'Provider failed; no credits were charged.' },
    });
    await expect(page.locator('#asset-generation-status')).toContainText('no credits were charged');
    await expect(prompt).toHaveValue('Next draft');

    await heldRoutes[0].fulfill({
      json: { characters: [{ id: 'rain', name: 'Rain' }], credits_charged: 4 },
    });
    await expect(page.locator('.asset-card-generating')).toHaveCount(0);
    await expect(page.locator('#asset-generation-status')).toContainText('Charged 4 credits');
    await expect(prompt).toHaveValue('Next draft');
    await expect(page.locator(`#${kind}-generation-form`)).toHaveAttribute('aria-busy', 'false');
  });
}

// 18. Replaces test_animation_presets_submit_selected_type (3 parametrized cases)
for (const { preset, label } of [
  { preset: 'video', label: 'Video' },
  { preset: 'layered', label: 'Layered' },
  { preset: 'triframe', label: 'Tri-frame' },
] as const) {
  test(`animation preset menu opens upward and submits ${preset} (${label})`, async ({
    canvas,
    page,
  }) => {
    await mountAssetManagerHarness(canvas, page, {
      isOrator: true,
      isStampOwner: true,
      openManager: true,
    });

    const requests: Array<{ name: string; prompt: string; animation_type: string }> = [];
    await canvas.serveRoute(
      (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/animations',
      async (route) => route.fulfill({ json: [] }),
    );
    await canvas.serveRoute(
      (url) =>
        url.origin === canvas.origin &&
        url.pathname === '/api/theaters/stage/animations/generate',
      async (route) => {
        requests.push(
          JSON.parse(route.request().postData() || '{}') as {
            name: string;
            prompt: string;
            animation_type: string;
          },
        );
        await route.fulfill({
          status: 502,
          json: { detail: 'Generation failed; no credits were charged.' },
        });
      },
    );

    await page.locator('#stamp-tab-animations').click();
    const menu = page.locator('#animation-generation-presets');
    await menu.locator('summary').click();

    const bounds = await menu.locator('.generation-preset-menu').boundingBox();
    const trigger = await menu.locator('summary').boundingBox();
    expect(bounds).not.toBeNull();
    expect(trigger).not.toBeNull();
    expect(bounds!.y + bounds!.height).toBeLessThan(trigger!.y);

    await menu.getByRole('button', { name: label, exact: true }).click();
    await expect(
      menu.getByRole('button', { name: label, exact: true, includeHidden: true }),
    ).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#animation-generation-type-label')).toHaveText(label);
    await expect(menu.locator('.generation-preset-menu')).toBeHidden();

    await page.locator('#animation-generation-prompt').fill('Gentle forest rain');
    await page.locator('#animation-generation-prompt').press('Enter');

    await expect(page.locator('#asset-generation-status')).toContainText('no credits were charged');
    expect(requests).toEqual([
      { name: 'Gentle forest rain', prompt: 'Gentle forest rain', animation_type: preset },
    ]);
    await expect(page.locator('#animation-generation-type')).toHaveValue(preset);
    await expect(page.locator('#animation-generation-prompt')).toHaveValue('Gentle forest rain');

    await menu.locator('summary').click();
    await menu.locator('summary').press('Escape');
    await expect(menu.locator('.generation-preset-menu')).toBeHidden();
  });
}

// 19. Replaces test_asset_search_filters_descriptions_and_survives_tab_switches
test('asset search filters descriptions and survives tab switches', async ({ canvas, page }) => {
  await canvas.openCanvas({
    isOrator: true,
    viewport: { width: 1400, height: 900 },
    apiRoutes: {
      '/api/theaters/stage/scenes': [
        { id: 'forest', name: 'Forest', description: 'Misty woodland' },
        { id: 'port', name: 'Harbor' },
      ],
      '/api/theaters/stage/characters': [
        { id: 'scout', name: 'Scout', description: 'Woodland ranger' },
        { id: 'captain', name: 'Captain' },
      ],
    },
  });

  await page.locator('#chat-open-stamps-btn').click();
  await page.locator('#stamp-tab-scenes').click();
  await expect(page.locator('#scenes-manager-grid .scene-card')).toHaveCount(2);

  const search = page.getByRole('searchbox', { name: 'Search assets' });
  await search.fill('  WOODLAND  ');
  await expect(page.locator('#scenes-manager-grid .scene-card:visible')).toHaveCount(1);
  await expect(page.locator('#scenes-manager-grid .scene-card:visible')).toContainText('Forest');

  await page.locator('#stamp-tab-characters').click();
  await expect(page.locator('#characters-manager-grid .character-card:visible')).toHaveCount(1);
  await expect(page.locator('#characters-manager-grid .character-card:visible')).toContainText(
    'Scout',
  );

  await search.fill('no match');
  await expect(page.locator('#characters-manager-grid .character-card:visible')).toHaveCount(0);
  await expect(page.locator('#asset-search-empty')).toBeVisible();

  await search.fill('');
  await expect(page.locator('#characters-manager-grid .character-card:visible')).toHaveCount(2);
  await expect(page.locator('#asset-search-empty')).toBeHidden();
});

// 20. Replaces test_asset_search_expands_matching_tracks_and_filters_animations
test('asset search expands matching playlist tracks and filters animations', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({
    isOrator: true,
    viewport: { width: 1400, height: 900 },
    apiRoutes: {
      '/api/theaters/stage/playlists': [
        {
          id: 'ambient',
          name: 'Ambient',
          tracks: [
            { id: 'rain', name: 'Rainfall' },
            { id: 'wind', name: 'Wind' },
          ],
        },
        { id: 'battle', name: 'Battle', tracks: [{ id: 'drums', name: 'Drums' }] },
      ],
      '/api/theaters/stage/animations': [
        { id: 'rain', name: 'Clouds', description: 'Rainfall over the forest' },
        { id: 'fire', name: 'Fire' },
      ],
    },
  });

  await page.locator('#chat-open-stamps-btn').click();
  await page.locator('#stamp-tab-playlists').click();
  await expect(page.locator('.playlist-group')).toHaveCount(2);

  const search = page.locator('#asset-search');
  await search.fill('rainfall');
  await expect(page.locator('.playlist-group:visible')).toHaveCount(1);
  await expect(page.locator('.playlist-track-list .asset-push-button:visible')).toHaveCount(1);
  await expect(page.locator('.playlist-track-list .asset-push-button:visible')).toContainText(
    'Rainfall',
  );

  await search.fill('ambient');
  await expect(page.locator('.playlist-track-list .asset-push-button:visible')).toHaveCount(2);

  await search.fill('rainfall');
  await page.locator('#stamp-tab-animations').click();
  await expect(page.locator('#animations-manager .asset-push-button:visible')).toHaveCount(1);

  await search.fill('');
  await expect(page.locator('#animations-manager .asset-push-button:visible')).toHaveCount(2);
});

// 21. Replaces test_scene_generation_pending_cards_and_overlapping_jobs (2 parametrized cases)
for (const olderSucceeds of [true, false]) {
  test(`scene generation pending cards and overlapping out-of-order jobs (olderSucceeds=${olderSucceeds})`, async ({
    canvas,
    page,
  }) => {
    await mountAssetManagerHarness(canvas, page, {
      isOrator: true,
      isStampOwner: true,
      openManager: true,
    });

    const jobs: Route[] = [];
    await canvas.serveRoute(
      (url) =>
        url.origin === canvas.origin && url.pathname === '/api/theaters/stage/scenes/generate',
      async (route) => {
        jobs.push(route);
      },
    );

    await page.evaluate(() => {
      const h = (window as unknown as AssetHarnessWindow).__assetHarness;
      h.cachedScenes = [];
      h.manager.switchStampManagerTab('scenes');
    });

    const prompt = page.locator('#scene-generation-prompt');
    await prompt.fill('Forest clearing');
    await prompt.press('Enter');

    await expect(page.locator('.scene-card-generating')).toHaveCount(1);
    await expect(prompt).toHaveValue('');
    await expect(prompt).toBeEditable();
    await expect(page.locator('#scenes-manager-empty')).toBeHidden();
    await expect(page.locator('.scene-card-generating')).toHaveAttribute('aria-busy', 'true');
    await expect(page.locator('.scene-card-generating [role="button"]')).toHaveCount(0);
    await expect(page.locator('.scene-card-generating .scene-card-push-badge')).toHaveCount(0);

    await page.evaluate(() => {
      const h = (window as unknown as AssetHarnessWindow).__assetHarness;
      h.cachedCharacters = [];
      h.manager.switchStampManagerTab('characters');
      h.manager.switchStampManagerTab('scenes');
    });
    await expect(page.locator('.scene-card-generating')).toHaveCount(1);

    await prompt.fill('Island kingdom');
    await prompt.press('Enter');
    await expect(page.locator('.scene-card-generating')).toHaveCount(2);
    await expect(page.locator('#scene-generation-status')).toContainText('2 scenes generating');
    await expect(page.locator('#stamp-manager-count')).toContainText('2 generating');

    await expect.poll(() => jobs.length).toBe(2);

    // Newer snapshot arrives before the first request finishes.
    const newerScenes: Array<{ id: string; name: string }> = [
      { id: 'island', name: 'Island kingdom' },
    ];
    if (olderSucceeds) {
      newerScenes.push({ id: 'forest', name: 'Forest clearing' });
    }
    await jobs[1].fulfill({ json: { scenes: newerScenes, credits_charged: 4 } });
    await expect(page.locator('.scene-card-generating')).toHaveCount(1);
    await expect(page.locator('#scene-generation-status')).toContainText('1 scene generating');

    await prompt.fill('Next scene draft');
    if (olderSucceeds) {
      await jobs[0].fulfill({
        json: {
          scenes: [{ id: 'forest', name: 'Forest clearing' }],
          credits_charged: 4,
        },
      });
    } else {
      await jobs[0].fulfill({
        status: 429,
        json: { detail: 'Too many concurrent generation jobs.' },
      });
      await expect(page.locator('#scene-generation-status')).toContainText(
        'Too many concurrent generation jobs',
      );
    }

    await expect(page.locator('.scene-card-generating')).toHaveCount(0);
    await expect(page.locator('#scenes-manager-grid .scene-card')).toHaveCount(
      olderSucceeds ? 2 : 1,
    );
    await expect(prompt).toHaveValue('Next scene draft');
    await expect(page.locator('#scene-generation-form')).toHaveAttribute('aria-busy', 'false');
  });
}

// 22. Replaces test_websocket_characters_and_scenes_update_active_and_background_tabs
test('websocket character and scene updates refresh active and background tabs', async ({
  canvas,
  page,
}) => {
  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname.startsWith('/img/'),
    async (route) => route.fulfill({ status: 200, contentType: 'image/png', body: TINY_PNG_BYTES }),
  );

  let doodleSocket: WebSocketRoute | null = null;
  await canvas.openCanvas({
    isOrator: true,
    viewport: { width: 1400, height: 900 },
    apiRoutes: {
      '/api/theaters/stage/characters': [],
      '/api/theaters/stage/scenes': [],
    },
    onWebSocket: (ws) => {
      if (new URL(ws.url()).pathname === '/ws/doodle') {
        doodleSocket = ws;
      }
    },
  });

  await expect.poll(() => doodleSocket !== null).toBe(true);
  await page.locator('#chat-open-stamps-btn').click();
  await page.locator('#stamp-tab-characters').click();
  await expect(page.locator('#characters-manager-grid .character-card')).toHaveCount(0);

  // 1. Active characters tab receives theater_characters_updated.
  doodleSocket!.send(
    JSON.stringify({
      type: 'theater_characters_updated',
      characters: [
        { id: 'elena', name: 'Elena', description: 'Brave warrior', image_url: '/img/elena.png' },
        { id: 'rowan', name: 'Rowan', description: 'Swift scout', image_url: '/img/rowan.png' },
      ],
    }),
  );
  await expect(page.locator('#characters-manager-grid .character-card')).toHaveCount(2);
  await expect(page.locator('#characters-manager-grid .character-card').first()).toContainText(
    'Elena',
  );

  // 2. Switch to scenes tab and receive theater_scenes_updated.
  await page.locator('#stamp-tab-scenes').click();
  await expect(page.locator('#scenes-manager-grid .scene-card')).toHaveCount(0);
  doodleSocket!.send(
    JSON.stringify({
      type: 'theater_scenes_updated',
      scenes: [
        {
          id: 'mountain_pass',
          name: 'Mountain Pass',
          description: 'Snowy trail',
          image_url: '/img/mountain.png',
        },
      ],
    }),
  );
  await expect(page.locator('#scenes-manager-grid .scene-card')).toHaveCount(1);
  await expect(page.locator('#scenes-manager-grid .scene-card').first()).toContainText(
    'Mountain Pass',
  );

  // 3. Background characters update while on scenes tab updates cache without clobbering scenes.
  doodleSocket!.send(
    JSON.stringify({
      type: 'theater_characters_updated',
      characters: [
        { id: 'elena', name: 'Elena', description: 'Brave warrior', image_url: '/img/elena.png' },
        { id: 'rowan', name: 'Rowan', description: 'Swift scout', image_url: '/img/rowan.png' },
        { id: 'val', name: 'Val', description: 'Wise mage', image_url: '/img/val.png' },
      ],
    }),
  );
  await expect(page.locator('#scenes-manager-grid .scene-card')).toHaveCount(1);

  await page.locator('#stamp-tab-characters').click();
  await expect(page.locator('#characters-manager-grid .character-card')).toHaveCount(3);
});

// 23. Replaces test_websocket_animations_updated_reloads_animations_panel
test('websocket animations updated event reloads animations panel', async ({ canvas, page }) => {
  const animList: Array<{
    id: string;
    name: string;
    description: string;
    type: string;
    url: string;
  }> = [{ id: 'storm', name: 'Storm', description: 'Rolling storm', type: 'video', url: '' }];

  let doodleSocket: WebSocketRoute | null = null;
  await canvas.openCanvas({
    isOrator: true,
    viewport: { width: 1400, height: 900 },
    apiRoutes: {
      '/api/theaters/stage/animations': async (route) => {
        await route.fulfill({ json: animList });
      },
    },
    onWebSocket: (ws) => {
      if (new URL(ws.url()).pathname === '/ws/doodle') {
        doodleSocket = ws;
      }
    },
  });

  await expect.poll(() => doodleSocket !== null).toBe(true);
  await page.locator('#chat-open-stamps-btn').click();
  await page.locator('#stamp-tab-animations').click();
  await expect(page.locator('#animations-manager .asset-push-button')).toHaveCount(1);

  animList.push({
    id: 'campfire',
    name: 'Campfire',
    description: 'Flickering flames',
    type: 'video',
    url: '',
  });
  doodleSocket!.send(JSON.stringify({ type: 'theater_animations_updated' }));
  await expect(page.locator('#animations-manager .asset-push-button')).toHaveCount(2);
});
