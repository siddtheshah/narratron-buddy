import { expect, test } from '../../testing/ui/canvas_fixtures';

type NarrationPaneModule = typeof import('/static/generated/canvas/narration-pane.js');

const NARRATION_PANE_MODULE_URL = '/static/generated/canvas/narration-pane.js';
const NARRATION_PANE_SHIM_URL = '/static/js/narration-pane.js';

test('narration-pane module and deprecated shim share identity and toggle interactive reading state', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    stylesheets: ['/static/css/narration-pane.css'],
    html: '<div id="stage" style="position:relative;width:640px;height:360px;"><div id="narration-pane-mount"></div></div>',
  });

  const result = await page.evaluate(
    async ({ moduleUrl, shimUrl }) => {
      const mod = await window.__importModule<NarrationPaneModule>(moduleUrl);
      const shim = await window.__importModule<NarrationPaneModule>(shimUrl);

      const mount = document.getElementById('narration-pane-mount');
      const pane = await mod.createNarrationPane(mount);

      // Replacing mount removes the placeholder and inserts #scene-dialogue-overlay.
      const mountStillPresent = document.getElementById('narration-pane-mount') !== null;
      const overlayId = pane.element.id;
      const overlayAriaLive = pane.element.getAttribute('aria-live');

      // Null/undefined prepare calls are safe no-ops.
      pane.prepare(null);
      pane.prepare(undefined);

      const caption = document.createElement('div');
      caption.className = 'scene-description';
      caption.textContent = Array.from(
        { length: 20 },
        (_, index) => `Line ${index + 1}: Mist drifts across the stone courtyard.`,
      ).join(' ');
      pane.element.appendChild(caption);

      // Default state treats drawing color as selected (non-interactive overlay).
      pane.prepare(caption);
      const initialInteractive = pane.element.classList.contains('narration-interactive');
      const initialRole = caption.getAttribute('role');
      const initialAriaLabel = caption.getAttribute('aria-label');
      const initialTabIndex = caption.tabIndex;
      const initialTitle = caption.title;

      // Deselecting drawing color enables interactive scrolling and updates existing caption.
      pane.setDrawingColorSelected(false);
      const interactiveAfterDeselect = pane.element.classList.contains('narration-interactive');
      const tabIndexAfterDeselect = caption.tabIndex;
      const titleAfterDeselect = caption.title;

      // Subsequent prepare() calls inherit the current deselected state.
      const nextCaption = document.createElement('div');
      nextCaption.className = 'scene-description';
      nextCaption.textContent = 'A new scene begins.';
      pane.prepare(nextCaption);
      const nextTabIndexWhileDeselected = nextCaption.tabIndex;

      // Re-selecting drawing color disables interactive scrolling again.
      pane.setDrawingColorSelected(true);
      const interactiveAfterReselect = pane.element.classList.contains('narration-interactive');
      const tabIndexAfterReselect = caption.tabIndex;
      const titleAfterReselect = caption.title;

      return {
        sameFactory: mod.createNarrationPane === shim.createNarrationPane,
        mountStillPresent,
        overlayId,
        overlayAriaLive,
        initialInteractive,
        initialRole,
        initialAriaLabel,
        initialTabIndex,
        initialTitle,
        interactiveAfterDeselect,
        tabIndexAfterDeselect,
        titleAfterDeselect,
        nextTabIndexWhileDeselected,
        interactiveAfterReselect,
        tabIndexAfterReselect,
        titleAfterReselect,
      };
    },
    { moduleUrl: NARRATION_PANE_MODULE_URL, shimUrl: NARRATION_PANE_SHIM_URL },
  );

  expect(result).toEqual({
    sameFactory: true,
    mountStillPresent: false,
    overlayId: 'scene-dialogue-overlay',
    overlayAriaLive: 'polite',
    initialInteractive: false,
    initialRole: 'region',
    initialAriaLabel: 'Scene narration',
    initialTabIndex: -1,
    initialTitle: 'Deselect the active drawing color to scroll the full narration',
    interactiveAfterDeselect: true,
    tabIndexAfterDeselect: 0,
    titleAfterDeselect: 'Scroll to read the full narration',
    nextTabIndexWhileDeselected: 0,
    interactiveAfterReselect: false,
    tabIndexAfterReselect: -1,
    titleAfterReselect: 'Deselect the active drawing color to scroll the full narration',
  });
});

test('createNarrationPane enforces mount, fetch status, and non-empty template errors', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    html: '<div id="mount-a"></div><div id="mount-b"></div>',
  });

  const missingMountError = await page.evaluate(async (moduleUrl: string) => {
    const { createNarrationPane } = await window.__importModule<NarrationPaneModule>(moduleUrl);
    try {
      await createNarrationPane(null);
      return null;
    } catch (error) {
      return error instanceof Error ? error.message : String(error);
    }
  }, NARRATION_PANE_MODULE_URL);
  expect(missingMountError).toBe('Narration pane mount point is missing.');

  await canvas.serveRoute(
    (url: URL) => url.pathname === '/static/html/narration-pane.html',
    async (route) => {
      await route.fulfill({ status: 500, body: 'Server error' });
    },
  );
  const fetchError = await page.evaluate(async (moduleUrl: string) => {
    const { createNarrationPane } = await window.__importModule<NarrationPaneModule>(moduleUrl);
    try {
      await createNarrationPane(document.getElementById('mount-a'));
      return null;
    } catch (error) {
      return error instanceof Error ? error.message : String(error);
    }
  }, NARRATION_PANE_MODULE_URL);
  expect(fetchError).toBe('Unable to load narration pane (500).');

  await canvas.serveRoute(
    (url: URL) => url.pathname === '/static/html/narration-pane.html',
    async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: '   ' });
    },
  );
  const emptyTemplateError = await page.evaluate(async (moduleUrl: string) => {
    const { createNarrationPane } = await window.__importModule<NarrationPaneModule>(moduleUrl);
    try {
      await createNarrationPane(document.getElementById('mount-b'));
      return null;
    } catch (error) {
      return error instanceof Error ? error.message : String(error);
    }
  }, NARRATION_PANE_MODULE_URL);
  expect(emptyTemplateError).toBe('Narration pane template is empty.');
});
