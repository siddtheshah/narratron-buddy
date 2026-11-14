import fs from 'node:fs';
import path from 'node:path';
import { CANVAS_ORIGIN, expect, TEMPLATES_DIR, test } from '../../testing/ui/canvas_fixtures';

type CollabBarModule = typeof import('/static/generated/canvas/collab-bar.js');
type CollabBarController = import('/static/generated/canvas/collab-bar.js').CollabBarController;
type TheaterModalsModule = typeof import('/static/generated/canvas/theater-modals.js');
type TheaterModalsController =
  import('/static/generated/canvas/theater-modals.js').TheaterModalsController;

interface TheaterModalsHarnessWindow extends Window {
  collabBar?: CollabBarController;
  theaterModals?: TheaterModalsController;
  actionLog: string[];
  oratorCommandOpen: boolean;
  micConfigOpen: boolean;
  oratorHowtoOpen: boolean;
  clipShareOpen: boolean;
}

const COLLAB_BAR_URL = '/static/generated/canvas/collab-bar.js';
const THEATER_MODALS_URL = '/static/generated/canvas/theater-modals.js';

function loadModalsHtml(): string {
  const template = fs.readFileSync(path.join(TEMPLATES_DIR, 'canvas.html'), 'utf-8');
  const pulloutStart = template.indexOf('    <!-- Pullout Navigation & Help Menu Drawer -->');
  const pulloutEnd = template.indexOf('    <div id="report-theater-modal"', pulloutStart);
  const viewerGuideStart = template.indexOf('    <!-- Viewer Collaboration Guide Modal -->');
  const viewerGuideEnd = template.indexOf('    <!-- Baton Menu -->', viewerGuideStart);
  const configStart = template.indexOf('    <!-- Theater YAML Configuration Editor Modal -->');
  const configEnd = template.indexOf('    <link rel="stylesheet"', configStart);

  if (
    pulloutStart < 0 ||
    pulloutEnd <= pulloutStart ||
    viewerGuideStart < 0 ||
    viewerGuideEnd <= viewerGuideStart ||
    configStart < 0 ||
    configEnd <= configStart
  ) {
    throw new Error('Could not locate expected modal sections in templates/canvas.html');
  }

  return `
    <button id="menu-left-toggle-btn" type="button" aria-label="Open Menu">Menu</button>
    <button id="share-theater-btn" type="button">Share</button>
    <button id="clip-share-btn" type="button">Clip</button>
    <button id="obs-theater-btn" type="button">OBS</button>
    <div id="suggestions-toggle-btn" style="display: none;">Suggestions</div>
    <div id="sticky-notes-toggle-btn" style="display: none;">Stickies</div>
    <div id="viewer-collab-bar" style="display: none;">
      <span id="viewer-collab-bar-status"></span>
      <button id="viewer-collab-guide-btn" type="button">Guide</button>
    </div>
    ${template.slice(pulloutStart, pulloutEnd)}
    ${template.slice(viewerGuideStart, viewerGuideEnd)}
    ${template.slice(configStart, configEnd)}
  `;
}

const MODALS_HTML = loadModalsHtml();

async function mountModalsHarness(
  canvas: Parameters<Parameters<typeof test>[2]>[0]['canvas'],
  options: {
    theaterId?: string | null;
    localStorage?: Readonly<Record<string, string>>;
    adventureDelayMs?: number;
  } = {},
): Promise<void> {
  const theaterId = options.theaterId ?? 'stage-alpha';
  await canvas.mount({
    urlPath: '/canvas',
    query: theaterId ? `?theater_id=${encodeURIComponent(theaterId)}` : '',
    stylesheets: ['/static/css/canvas.css'],
    inlineStyles: ['* { transition: none !important; }'],
    localStorage: options.localStorage,
    html: MODALS_HTML,
  });

  await canvas.page.evaluate(
    async ({ collabUrl, modalsUrl, tid, adventureDelayMs }) => {
      const { createCollabBarController } =
        await window.__importModule<CollabBarModule>(collabUrl);
      const { createTheaterModalsController } =
        await window.__importModule<TheaterModalsModule>(modalsUrl);

      const win = window as unknown as TheaterModalsHarnessWindow;
      win.actionLog = [];
      win.oratorCommandOpen = false;
      win.micConfigOpen = false;
      win.oratorHowtoOpen = false;
      win.clipShareOpen = false;

      document.getElementById('share-theater-btn')?.addEventListener('click', () => {
        win.actionLog.push('share-btn-clicked');
      });
      document.getElementById('clip-share-btn')?.addEventListener('click', () => {
        win.actionLog.push('clip-btn-clicked');
      });
      document.getElementById('obs-theater-btn')?.addEventListener('click', () => {
        win.actionLog.push('obs-btn-clicked');
      });

      const collabBar = createCollabBarController({
        theaterId: tid,
        bar: document.getElementById('viewer-collab-bar'),
        barStatus: document.getElementById('viewer-collab-bar-status'),
        guideBtn: document.getElementById('viewer-collab-guide-btn'),
        guideModal: document.getElementById('viewer-collab-guide-modal'),
        guideCloseBtn: document.getElementById('viewer-collab-guide-close-btn'),
        guideAckBtn: document.getElementById('viewer-collab-guide-ack-btn'),
        guideStatus: document.getElementById('viewer-collab-status'),
        isOrator: () => false,
        isCollabEnabled: () => true,
      });
      win.collabBar = collabBar;

      win.theaterModals = createTheaterModalsController({
        theaterId: tid,
        pulloutOverlay: document.getElementById('pullout-overlay'),
        menuLeftToggleBtn: document.getElementById('menu-left-toggle-btn'),
        pulloutCloseBtn: document.getElementById('pullout-close-btn'),
        menuItemHowto: document.getElementById('menu-item-howto'),
        menuItemShare: document.getElementById('menu-item-share'),
        menuItemClip: document.getElementById('menu-item-clip'),
        menuItemObs: document.getElementById('menu-item-obs'),
        menuItemTheaterBuilder: document.getElementById(
          'menu-item-theater-builder',
        ) as HTMLAnchorElement | null,
        suggestionsToggleBtn: document.getElementById('suggestions-toggle-btn'),
        stickyNotesToggleBtn: document.getElementById('sticky-notes-toggle-btn'),
        shareTheaterBtn: document.getElementById('share-theater-btn'),
        clipShareBtn: document.getElementById('clip-share-btn'),
        obsTheaterBtn: document.getElementById('obs-theater-btn'),
        onOpenOratorHowto: () => win.actionLog.push('open-howto'),
        collabGuide: collabBar,
        adventureGuideModal: document.getElementById('adventure-guide-modal'),
        menuItemAdventureGuide: document.getElementById('menu-item-adventure-guide'),
        adventureGuideCloseBtn: document.getElementById('adventure-guide-close-btn'),
        adventureGuideAckBtn: document.getElementById('adventure-guide-ack-btn'),
        adventureGuideAutoOpenDelayMs: adventureDelayMs,
        theaterConfigModal: document.getElementById('theater-config-modal'),
        menuItemTheaterConfig: document.getElementById('menu-item-theater-config'),
        configModalCloseBtn: document.getElementById('config-modal-close-btn'),
        configModalCancelBtn: document.getElementById('config-modal-cancel-btn'),
        configModalFormatBtn: document.getElementById('config-modal-format-btn'),
        configModalSaveBtn: document.getElementById(
          'config-modal-save-btn',
        ) as HTMLButtonElement | null,
        theaterConfigEditor: document.getElementById(
          'theater-config-editor',
        ) as HTMLTextAreaElement | null,
        theaterConfigStatus: document.getElementById('theater-config-status'),
        isOratorCommandOpen: () => win.oratorCommandOpen,
        closeOratorCommand: () => {
          win.oratorCommandOpen = false;
          win.actionLog.push('close-orator-command');
        },
        isMicConfigOpen: () => win.micConfigOpen,
        closeMicConfigModal: () => {
          win.micConfigOpen = false;
          win.actionLog.push('close-mic-config');
        },
        isOratorHowtoOpen: () => win.oratorHowtoOpen,
        closeOratorHowtoModal: () => {
          win.oratorHowtoOpen = false;
          win.actionLog.push('close-orator-howto');
        },
        isClipShareOpen: () => win.clipShareOpen,
        closeClipShareModal: () => {
          win.clipShareOpen = false;
          win.actionLog.push('close-clip-share');
        },
      });
    },
    {
      collabUrl: COLLAB_BAR_URL,
      modalsUrl: THEATER_MODALS_URL,
      tid: theaterId,
      adventureDelayMs: options.adventureDelayMs ?? 30,
    },
  );
}

test('pullout drawer opens/closes via button, backdrop, Escape, and triggers menu actions and role visibility', async ({
  canvas,
  page,
}) => {
  await mountModalsHarness(canvas);

  const overlay = page.locator('#pullout-overlay');
  const toggleBtn = page.locator('#menu-left-toggle-btn');

  // Open & close via close button
  await toggleBtn.click();
  await expect(overlay).toHaveClass(/active/);
  await expect(overlay).toHaveAttribute('aria-hidden', 'false');
  await page.locator('#pullout-close-btn').click();
  await expect(overlay).not.toHaveClass(/active/);
  await expect(overlay).toHaveAttribute('aria-hidden', 'true');

  // Open & close via backdrop click
  await toggleBtn.click();
  await expect(overlay).toHaveClass(/active/);
  await overlay.click({ position: { x: 1180, y: 20 } });
  await expect(overlay).not.toHaveClass(/active/);

  // Open & close via Escape
  await toggleBtn.click();
  await expect(overlay).toHaveClass(/active/);
  await page.keyboard.press('Escape');
  await expect(overlay).not.toHaveClass(/active/);

  // Menu items close pullout and delegate to actions
  for (const itemId of ['#menu-item-howto', '#menu-item-share', '#menu-item-clip', '#menu-item-obs']) {
    await toggleBtn.click();
    await expect(overlay).toHaveClass(/active/);
    await page.locator(itemId).click();
    await expect(overlay).not.toHaveClass(/active/);
  }

  expect(
    await page.evaluate(() => (window as unknown as TheaterModalsHarnessWindow).actionLog),
  ).toEqual(['open-howto', 'share-btn-clicked', 'clip-btn-clicked', 'obs-btn-clicked']);

  // Owner menu visibility & corner-controls role visibility helpers
  await page.evaluate(() => {
    const modals = (window as unknown as TheaterModalsHarnessWindow).theaterModals!;
    modals.applyOwnerMenuVisibility(true);
    modals.applyCornerControlsRoleVisibility(true);
  });
  const builderLink = page.locator('#menu-item-theater-builder');
  await expect(builderLink).toHaveCSS('display', 'flex');
  await expect(builderLink).toHaveAttribute('href', '/theater-editor?theater_id=stage-alpha');
  await expect(page.locator('#suggestions-toggle-btn')).toHaveCSS('display', 'flex');
  await expect(page.locator('#sticky-notes-toggle-btn')).toHaveCSS('display', 'flex');

  await page.evaluate(() => {
    const modals = (window as unknown as TheaterModalsHarnessWindow).theaterModals!;
    modals.applyOwnerMenuVisibility(false);
    modals.applyCornerControlsRoleVisibility(false);
  });
  await expect(builderLink).toHaveCSS('display', 'none');
  await expect(page.locator('#suggestions-toggle-btn')).toHaveCSS('display', 'none');
  await expect(page.locator('#sticky-notes-toggle-btn')).toHaveCSS('display', 'none');
});

test('adventure guide auto-opens once per theater, persists seen flags, and reopens from pullout menu', async ({
  canvas,
  page,
}) => {
  await mountModalsHarness(canvas, { theaterId: 'adventure-keep', adventureDelayMs: 20 });

  await page.evaluate(() => {
    (window as unknown as TheaterModalsHarnessWindow).theaterModals?.initAdventureGuide(
      true,
      'adventure-keep',
    );
  });

  const modal = page.locator('#adventure-guide-modal');
  await expect(modal).toHaveClass(/active/, { timeout: 2000 });
  await expect(modal).toHaveAttribute('aria-hidden', 'false');

  await page.locator('#adventure-guide-ack-btn').click();
  await expect(modal).not.toHaveClass(/active/);
  await expect(modal).toHaveAttribute('aria-hidden', 'true');

  expect(
    await page.evaluate(() => ({
      globalSeen: localStorage.getItem('narratron_adventure_guide_seen'),
      theaterSeen: localStorage.getItem('narratron_adventure_guide_seen:adventure-keep'),
    })),
  ).toEqual({ globalSeen: 'true', theaterSeen: 'true' });

  // Calling initAdventureGuide again does not auto-open once seen
  await page.evaluate(() => {
    (window as unknown as TheaterModalsHarnessWindow).theaterModals?.initAdventureGuide(
      true,
      'adventure-keep',
    );
  });
  await page.waitForTimeout(80);
  await expect(modal).not.toHaveClass(/active/);

  // Reopening from pullout menu closes pullout and opens adventure modal; Escape closes it
  await page.locator('#menu-left-toggle-btn').click();
  await expect(page.locator('#pullout-overlay')).toHaveClass(/active/);
  await page.locator('#menu-item-adventure-guide').click();
  await expect(page.locator('#pullout-overlay')).not.toHaveClass(/active/);
  await expect(modal).toHaveClass(/active/);
  await page.keyboard.press('Escape');
  await expect(modal).not.toHaveClass(/active/);

  // Non-adventure mode hides the pullout menu item
  await page.evaluate(() => {
    (window as unknown as TheaterModalsHarnessWindow).theaterModals?.initAdventureGuide(
      false,
      'adventure-keep',
    );
  });
  await page.locator('#menu-left-toggle-btn').click();
  await expect(page.locator('#menu-item-adventure-guide')).toBeHidden();
});

test('viewer collab guide auto-opens when unseen and respects Escape priority', async ({
  canvas,
  page,
}) => {
  await mountModalsHarness(canvas, { theaterId: 'collab-stage' });

  const collabModal = page.locator('#viewer-collab-guide-modal');
  await page.evaluate(() => {
    (window as unknown as TheaterModalsHarnessWindow).theaterModals?.maybeAutoOpenViewerCollabGuide(
      'collab-stage',
      20,
    );
  });

  await expect(collabModal).toHaveClass(/active/, { timeout: 2000 });
  await expect(page.locator('#viewer-collab-status')).toContainText('Collaboration Mode is on');

  // Higher-priority orator command consumes Escape before viewer collab guide
  await page.evaluate(() => {
    (window as unknown as TheaterModalsHarnessWindow).oratorCommandOpen = true;
  });
  await page.keyboard.press('Escape');
  await expect(collabModal).toHaveClass(/active/);
  expect(
    await page.evaluate(() => (window as unknown as TheaterModalsHarnessWindow).actionLog),
  ).toContain('close-orator-command');

  // Next Escape closes the viewer collab guide and marks localStorage seen
  await page.keyboard.press('Escape');
  await expect(collabModal).not.toHaveClass(/active/);
  expect(
    await page.evaluate(() =>
      localStorage.getItem('narratron_viewer_collab_guide_seen:collab-stage'),
    ),
  ).toBe('true');

  // Re-running auto-open after seen is a no-op
  const scheduled = await page.evaluate(() =>
    (window as unknown as TheaterModalsHarnessWindow).theaterModals?.maybeAutoOpenViewerCollabGuide(
      'collab-stage',
      10,
    ),
  );
  expect(scheduled).toBe(false);
});

test('theater config modal loads, formats, validates before saving, saves, and handles errors', async ({
  canvas,
  page,
}) => {
  let getConfigResponse: { status: number; body: Record<string, unknown> } = {
    status: 200,
    body: { config_yaml: 'title: Alpha\nadventures: true\n' },
  };
  let formatResponse: { status: number; body: Record<string, unknown> } = {
    status: 200,
    body: { formatted_yaml: 'title: Alpha\nformatted: true\n' },
  };
  let saveResponse: { status: number; body: Record<string, unknown> } = {
    status: 200,
    body: { message: 'Config persisted.' },
  };
  let saveCallCount = 0;

  await canvas.serveRoute(
    (url) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/theaters/stage-alpha/config',
    async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ status: getConfigResponse.status, json: getConfigResponse.body });
        return;
      }
      saveCallCount += 1;
      await route.fulfill({ status: saveResponse.status, json: saveResponse.body });
    },
  );

  await canvas.serveRoute(
    (url) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/theaters/format-yaml',
    async (route) => {
      await route.fulfill({ status: formatResponse.status, json: formatResponse.body });
    },
  );

  await mountModalsHarness(canvas, { theaterId: 'stage-alpha' });

  // Open via pullout menu
  await page.locator('#menu-left-toggle-btn').click();
  await page.locator('#menu-item-theater-config').click();

  const modal = page.locator('#theater-config-modal');
  const editor = page.locator('#theater-config-editor');
  const status = page.locator('#theater-config-status');
  await expect(page.locator('#pullout-overlay')).not.toHaveClass(/active/);
  await expect(modal).toHaveClass(/active/);
  await expect(editor).toHaveValue('title: Alpha\nadventures: true\n');
  await expect(editor).toBeEnabled();

  // Format YAML success
  await page.locator('#config-modal-format-btn').click();
  await expect(editor).toHaveValue('title: Alpha\nformatted: true\n');
  await expect(status).toBeVisible();
  await expect(status).toContainText('YAML formatted & syntax validated successfully!');

  // Save aborts when pre-save format validation fails
  formatResponse = { status: 400, body: { detail: 'Bad indentation on line 2' } };
  await page.locator('#config-modal-save-btn').click();
  await expect(status).toContainText('❌ Bad indentation on line 2');
  expect(saveCallCount).toBe(0);

  // Save succeeds when format validation passes
  formatResponse = { status: 200, body: { formatted_yaml: 'title: Clean\n' } };
  await page.locator('#config-modal-save-btn').click();
  await expect(editor).toHaveValue('title: Clean\n');
  await expect(status).toContainText('✅ Config persisted.');
  await expect(page.locator('#config-modal-save-btn')).toBeEnabled();
  expect(saveCallCount).toBe(1);

  // Save failure displays error banner and re-enables save button
  saveResponse = { status: 500, body: { detail: 'Disk read-only' } };
  await page.locator('#config-modal-save-btn').click();
  await expect(status).toContainText('❌ Disk read-only');
  await expect(page.locator('#config-modal-save-btn')).toBeEnabled();

  // Close via cancel, reopen with GET error, close via Escape
  await page.locator('#config-modal-cancel-btn').click();
  await expect(modal).not.toHaveClass(/active/);

  getConfigResponse = { status: 404, body: { detail: 'Missing' } };
  await page.locator('#menu-left-toggle-btn').click();
  await page.locator('#menu-item-theater-config').click();
  await expect(status).toContainText('HTTP 404: Failed to load theater.yaml');
  await expect(editor).toBeEnabled();

  await page.keyboard.press('Escape');
  await expect(modal).not.toHaveClass(/active/);

  // Destroy detaches listeners
  await page.evaluate(() => {
    (window as unknown as TheaterModalsHarnessWindow).theaterModals?.destroy();
  });
  await page.locator('#menu-left-toggle-btn').click();
  await expect(page.locator('#pullout-overlay')).not.toHaveClass(/active/);
});
