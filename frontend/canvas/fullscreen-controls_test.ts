import type { Page } from '@playwright/test';
import { expect, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';

type FullscreenControlsModule = typeof import('/static/generated/canvas/fullscreen-controls.js');
type FullscreenControlsController =
  import('/static/generated/canvas/fullscreen-controls.js').FullscreenControlsController;

interface FullscreenHarnessWindow extends Window {
  fullscreenControls?: FullscreenControlsController;
  mockFullscreenMode: 'none' | 'standard' | 'webkit' | 'moz' | 'ms';
  fullscreenRequests: string[];
  fullscreenExits: string[];
  canvasResizeCalls: number;
}

const FULLSCREEN_CONTROLS_URL = '/static/generated/canvas/fullscreen-controls.js';

const FULLSCREEN_SHELL_HTML = `
  <header>
    <button id="fullscreen-btn" type="button" title="Full Screen (F11)">
      <svg id="fullscreen-icon" width="18" height="18" viewBox="0 0 24 24">
        <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
      </svg>
    </button>
  </header>
  <button id="popout-expand-btn" type="button" style="display: none;">Expand</button>
  <div id="main-content" style="display: flex; flex: 1; min-width: 0; height: 600px;">
    <div id="image-container" style="flex: 1; position: relative;"></div>
  </div>
  <div id="chat-sidebar" style="width: 340px;">
    <button id="chat-side-toggle-btn" class="chat-side-toggle-tab" type="button">Toggle</button>
  </div>
`;

async function mountFullscreenHarness(
  canvas: CanvasHarness,
  page: Page,
  options: {
    readonly viewport?: { width: number; height: number };
    readonly withoutContainer?: boolean;
    readonly vendorOnly?: 'webkit' | 'moz' | 'ms';
  } = {},
): Promise<void> {
  await canvas.mount({
    viewport: options.viewport ?? { width: 1400, height: 900 },
    stylesheets: ['/static/css/canvas.css'],
    inlineStyles: ['body { display: flex; margin: 0; }'],
    html: FULLSCREEN_SHELL_HTML,
    moduleScripts: [
      `
      window.mockFullscreenMode = 'none';
      window.fullscreenRequests = [];
      window.fullscreenExits = [];
      window.canvasResizeCalls = 0;

      const defineGetter = (target, prop, getter) => {
        Object.defineProperty(target, prop, { configurable: true, get: getter });
      };

      defineGetter(document, 'fullscreenElement', () =>
        window.mockFullscreenMode === 'standard' ? document.documentElement : null,
      );
      defineGetter(document, 'webkitFullscreenElement', () =>
        window.mockFullscreenMode === 'webkit' ? document.documentElement : null,
      );
      defineGetter(document, 'mozFullScreenElement', () =>
        window.mockFullscreenMode === 'moz' ? document.documentElement : null,
      );
      defineGetter(document, 'msFullscreenElement', () =>
        window.mockFullscreenMode === 'ms' ? document.documentElement : null,
      );
    `,
    ],
  });

  await page.evaluate(
    async ({
      moduleUrl,
      withoutContainer,
      vendorOnly,
    }: {
      moduleUrl: string;
      withoutContainer: boolean;
      vendorOnly: 'webkit' | 'moz' | 'ms' | null;
    }) => {
      const win = window as unknown as FullscreenHarnessWindow;
      const docEl = document.documentElement as unknown as Record<string, unknown>;
      const doc = document as unknown as Record<string, unknown>;

      if (vendorOnly) {
        for (const reqProp of [
          'requestFullscreen',
          'webkitRequestFullscreen',
          'mozRequestFullScreen',
          'msRequestFullscreen',
        ]) {
          Object.defineProperty(docEl, reqProp, { configurable: true, writable: true, value: undefined });
        }
        for (const exitProp of [
          'exitFullscreen',
          'webkitExitFullscreen',
          'mozCancelFullScreen',
          'msExitFullscreen',
        ]) {
          Object.defineProperty(doc, exitProp, { configurable: true, writable: true, value: undefined });
        }
        if (vendorOnly === 'webkit') {
          docEl.webkitRequestFullscreen = () => {
            win.fullscreenRequests.push('webkit');
            win.mockFullscreenMode = 'webkit';
            document.dispatchEvent(new Event('webkitfullscreenchange'));
          };
          doc.webkitExitFullscreen = () => {
            win.fullscreenExits.push('webkit');
            win.mockFullscreenMode = 'none';
            document.dispatchEvent(new Event('webkitfullscreenchange'));
          };
        } else if (vendorOnly === 'moz') {
          docEl.mozRequestFullScreen = () => {
            win.fullscreenRequests.push('moz');
            win.mockFullscreenMode = 'moz';
            document.dispatchEvent(new Event('mozfullscreenchange'));
          };
          doc.mozCancelFullScreen = () => {
            win.fullscreenExits.push('moz');
            win.mockFullscreenMode = 'none';
            document.dispatchEvent(new Event('mozfullscreenchange'));
          };
        } else if (vendorOnly === 'ms') {
          docEl.msRequestFullscreen = () => {
            win.fullscreenRequests.push('ms');
            win.mockFullscreenMode = 'ms';
            document.dispatchEvent(new Event('MSFullscreenChange'));
          };
          doc.msExitFullscreen = () => {
            win.fullscreenExits.push('ms');
            win.mockFullscreenMode = 'none';
            document.dispatchEvent(new Event('MSFullscreenChange'));
          };
        }
      } else {
        docEl.requestFullscreen = async () => {
          win.fullscreenRequests.push('standard');
          win.mockFullscreenMode = 'standard';
          document.dispatchEvent(new Event('fullscreenchange'));
        };
        doc.exitFullscreen = async () => {
          win.fullscreenExits.push('standard');
          win.mockFullscreenMode = 'none';
          document.dispatchEvent(new Event('fullscreenchange'));
        };
      }

      const { createFullscreenControlsController } =
        await window.__importModule<FullscreenControlsModule>(moduleUrl);
      win.fullscreenControls = createFullscreenControlsController({
        fullscreenBtn: document.getElementById('fullscreen-btn'),
        fullscreenIcon: document.getElementById('fullscreen-icon'),
        container: withoutContainer ? null : document.getElementById('image-container'),
        sidebar: document.getElementById('chat-sidebar'),
        expandButton: document.getElementById('popout-expand-btn'),
        sideToggleButton: document.getElementById('chat-side-toggle-btn'),
        onResizeCanvases: () => {
          win.canvasResizeCalls += 1;
        },
      });
    },
    {
      moduleUrl: FULLSCREEN_CONTROLS_URL,
      withoutContainer: options.withoutContainer ?? false,
      vendorOnly: options.vendorOnly ?? null,
    },
  );
}

test('fullscreen toggle updates icon, title, sidebar collapse, resize callback, and cinematic timer', async ({
  canvas,
  page,
}) => {
  await page.clock.install();
  await mountFullscreenHarness(canvas, page, { viewport: { width: 1400, height: 900 } });

  const fullscreenBtn = page.locator('#fullscreen-btn');
  const fullscreenIcon = page.locator('#fullscreen-icon');
  const sidebar = page.locator('#chat-sidebar');
  const expandBtn = page.locator('#popout-expand-btn');

  const initialIcon = await fullscreenIcon.innerHTML();
  await expect(fullscreenBtn).toHaveAttribute('title', 'Full Screen (F11)');
  await expect(sidebar).not.toHaveClass(/collapsed/);

  // Enter fullscreen via button click.
  await fullscreenBtn.click();
  await expect(page.locator('body')).toHaveClass(/fullscreen-cinematic/);
  await expect(page.locator('body')).toHaveClass(/ui-visible/);
  await expect(sidebar).toHaveClass(/collapsed/);
  await expect
    .poll(() => expandBtn.evaluate((el) => (el as HTMLElement).style.display))
    .toBe('flex');
  await expect(fullscreenBtn).toHaveAttribute('title', 'Exit Full Screen (Esc)');
  expect(await fullscreenIcon.innerHTML()).not.toBe(initialIcon);

  // 100ms deferred canvas resize fires after fullscreen transition.
  await page.clock.fastForward(150);
  expect(
    await page.evaluate(() => (window as unknown as FullscreenHarnessWindow).canvasResizeCalls),
  ).toBeGreaterThanOrEqual(1);

  // Cinematic UI hides after 2500ms of inactivity; mousemove restores it and restarts timer.
  await page.clock.fastForward(2400);
  await expect(page.locator('body')).not.toHaveClass(/ui-visible/);

  await page.evaluate(() => window.dispatchEvent(new MouseEvent('mousemove')));
  await expect(page.locator('body')).toHaveClass(/ui-visible/);
  await page.clock.fastForward(2000);
  await expect(page.locator('body')).toHaveClass(/ui-visible/);
  await page.clock.fastForward(600);
  await expect(page.locator('body')).not.toHaveClass(/ui-visible/);

  // Toggling side panel while in fullscreen reveals cinematic UI again.
  await page.locator('#chat-side-toggle-btn').click();
  await expect(sidebar).not.toHaveClass(/collapsed/);
  await expect
    .poll(() => expandBtn.evaluate((el) => (el as HTMLElement).style.display))
    .toBe('none');
  await expect(page.locator('body')).toHaveClass(/ui-visible/);

  // Repeated updateFullscreenUI while already active does not re-collapse expanded panel.
  await page.evaluate(() => {
    (window as unknown as FullscreenHarnessWindow).fullscreenControls?.updateFullscreenUI();
  });
  await expect(sidebar).not.toHaveClass(/collapsed/);

  // Exiting fullscreen clears cinematic classes, stops mousemove listener, and restores icon.
  await fullscreenBtn.click();
  await expect(page.locator('body')).not.toHaveClass(/fullscreen-cinematic/);
  await expect(page.locator('body')).not.toHaveClass(/ui-visible/);
  await expect(fullscreenBtn).toHaveAttribute('title', 'Full Screen (F11)');
  expect(await fullscreenIcon.innerHTML()).toContain('M8 3H5');

  await page.evaluate(() => window.dispatchEvent(new MouseEvent('mousemove')));
  await expect(page.locator('body')).not.toHaveClass(/ui-visible/);
});

test('supports vendor-prefixed fullscreen APIs and events', async ({ canvas, page }) => {
  for (const vendor of ['webkit', 'moz', 'ms'] as const) {
    await mountFullscreenHarness(canvas, page, { vendorOnly: vendor });

    await page.evaluate(() => {
      (window as unknown as FullscreenHarnessWindow).fullscreenControls?.toggleFullScreen();
    });
    expect(
      await page.evaluate(() =>
        (window as unknown as FullscreenHarnessWindow).fullscreenControls?.isFullscreenActive(),
      ),
    ).toBe(true);
    await expect(page.locator('body')).toHaveClass(/fullscreen-cinematic/);

    await page.evaluate(() => {
      (window as unknown as FullscreenHarnessWindow).fullscreenControls?.toggleFullScreen();
    });
    expect(
      await page.evaluate(() => {
        const win = window as unknown as FullscreenHarnessWindow;
        return {
          active: win.fullscreenControls?.isFullscreenActive(),
          requests: win.fullscreenRequests,
          exits: win.fullscreenExits,
        };
      }),
    ).toEqual({ active: false, requests: [vendor], exits: [vendor] });
    await expect(page.locator('body')).not.toHaveClass(/fullscreen-cinematic/);
  }
});

test('coordinates responsive sidebar collapse, manual toggle, controller API, and destroy cleanup', async ({
  canvas,
  page,
}) => {
  await mountFullscreenHarness(canvas, page, { viewport: { width: 1280, height: 800 } });

  const sidebar = page.locator('#chat-sidebar');
  const expandBtn = page.locator('#popout-expand-btn');

  // Auto-collapses at <= 1280px and auto-expands at > 1280px before manual toggle.
  await expect(sidebar).toHaveClass(/collapsed/);
  await expect
    .poll(() => expandBtn.evaluate((el) => (el as HTMLElement).style.display))
    .toBe('flex');

  await page.setViewportSize({ width: 1400, height: 800 });
  await page.evaluate(() => window.dispatchEvent(new Event('resize')));
  await expect(sidebar).not.toHaveClass(/collapsed/);
  await expect
    .poll(() => expandBtn.evaluate((el) => (el as HTMLElement).style.display))
    .toBe('none');

  // Controller togglePanel marks manual toggle so resize no longer overrides it.
  await page.evaluate(() => {
    (window as unknown as FullscreenHarnessWindow).fullscreenControls?.togglePanel();
  });
  await expect(sidebar).toHaveClass(/collapsed/);
  expect(
    await page.evaluate(() =>
      (window as unknown as FullscreenHarnessWindow).fullscreenControls?.isPanelCollapsed(),
    ),
  ).toBe(true);

  await page.setViewportSize({ width: 1500, height: 800 });
  await page.evaluate(() => window.dispatchEvent(new Event('resize')));
  await expect(sidebar).toHaveClass(/collapsed/);

  // Side toggle button restores panel.
  await page.locator('#chat-side-toggle-btn').click();
  await expect(sidebar).not.toHaveClass(/collapsed/);
  expect(
    await page.evaluate(() =>
      (window as unknown as FullscreenHarnessWindow).fullscreenControls?.isPanelCollapsed(),
    ),
  ).toBe(false);

  // Fallback without container still manages panel collapse, expand button click, and destroy detaches listeners.
  await canvas.mount({
    viewport: { width: 1400, height: 900 },
    html: FULLSCREEN_SHELL_HTML,
  });
  await page.evaluate(async (moduleUrl: string) => {
    const win = window as unknown as FullscreenHarnessWindow;
    win.fullscreenRequests = [];
    const { createFullscreenControlsController } =
      await window.__importModule<FullscreenControlsModule>(moduleUrl);
    win.fullscreenControls = createFullscreenControlsController({
      fullscreenBtn: document.getElementById('fullscreen-btn'),
      fullscreenIcon: document.getElementById('fullscreen-icon'),
      container: null,
      sidebar: document.getElementById('chat-sidebar'),
      expandButton: document.getElementById('popout-expand-btn'),
      sideToggleButton: document.getElementById('chat-side-toggle-btn'),
    });
    win.fullscreenControls.togglePanel();
  }, FULLSCREEN_CONTROLS_URL);
  await expect(sidebar).toHaveClass(/collapsed/);
  await expect(expandBtn).toHaveCSS('display', 'flex');
  await expandBtn.click();
  await expect(sidebar).not.toHaveClass(/collapsed/);
  await expect(expandBtn).toHaveCSS('display', 'none');

  await page.evaluate(() => {
    (window as unknown as FullscreenHarnessWindow).fullscreenControls?.destroy();
  });
  await page.locator('#fullscreen-btn').click();
  expect(
    await page.evaluate(() => (window as unknown as FullscreenHarnessWindow).fullscreenRequests),
  ).toEqual([]);
});
