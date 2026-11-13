import { CANVAS_ORIGIN, expect, test } from '../../testing/ui/canvas_fixtures';
import type { Route, WebSocketRoute } from '@playwright/test';

interface StickyNotePayload {
  topic: string;
  info: string;
  hidden?: boolean;
  is_hidden?: boolean;
}

interface StickyNotesWindow extends Window {
  isStickiesRevealedInCache?: (tid: string) => boolean;
  setStickiesRevealedInCache?: (tid: string, revealed: boolean) => void;
  STICKIES_REVEAL_CACHE_TIMEOUT_MS?: number;
  updateCanvasStickyNotes?: (
    notes: StickyNotePayload[],
    hidden?: StickyNotePayload[],
  ) => void;
}

test('sticky notes panel renders elements, z-index, counts, and falls back to /api/sticky-notes', async ({
  canvas,
  page,
}) => {
  let primaryRequested = false;
  let fallbackRequested = false;

  await canvas.openCanvas({
    isOrator: true,
    apiRoutes: {
      '/theaters/stage/sticky-notes': async (route: Route) => {
        primaryRequested = true;
        await route.fulfill({ status: 404, body: 'Not found' });
      },
      '/api/sticky-notes': async (route: Route) => {
        fallbackRequested = true;
        await route.fulfill({
          json: {
            sticky_notes: [
              { topic: 'Tavern', info: 'Ask the innkeeper' },
              { topic: 'Vault', info: 'Code is 42', hidden: true },
            ],
            hidden_stickies: ['Vault'],
          },
        });
      },
    },
  });

  await expect.poll(() => primaryRequested && fallbackRequested).toBe(true);

  const btn = page.locator('#sticky-notes-toggle-btn');
  await expect(btn).toBeVisible();
  await expect(page.locator('#sticky-notes-loading')).toHaveCount(1);
  await expect(page.locator('#sticky-notes-list')).toHaveCount(1);
  // Before reveal, badge counts visible items ('1'); once revealed it shows total ('2').
  await expect(page.locator('#sticky-notes-count-badge')).toHaveText('1');
  await expect(btn.locator('.sticky-note-card')).toHaveCount(2);
  await expect(btn.locator('.sticky-note-card.is-hidden-sticky')).toHaveCount(1);
  await expect(btn.locator('.sticky-note-hidden-badge')).toHaveText('Hidden');
  await expect(btn.locator('.sticky-notes-hidden-section')).toHaveCount(1);
  await expect(page.locator('#sticky-notes-hidden-toggle-btn')).toContainText(
    'Show 1 hidden sticky note',
  );
  await expect(page.locator('#sticky-notes-hidden-container')).toHaveCSS('display', 'none');

  // Revealing hidden stickies updates badge count to total ('2') and shows hidden container.
  await btn.dispatchEvent('mouseenter');
  await expect(btn).toHaveClass(/expanded/);
  await page.locator('#sticky-notes-hidden-toggle-btn').click();
  await expect(page.locator('#sticky-notes-count-badge')).toHaveText('2');
  await expect(page.locator('#sticky-notes-hidden-container')).toHaveCSS('display', 'flex');
  await expect(page.locator('#sticky-notes-hidden-toggle-btn')).toContainText(
    'Hide 1 hidden sticky note',
  );

  await expect(btn).toHaveCSS('width', '320px');
  await expect(btn).toHaveCSS('border-radius', '16px');
  const layout = await page.evaluate(() => {
    const panel = document.getElementById('sticky-notes-toggle-btn')!;
    const doodleCanvas = document.getElementById('doodle-canvas')!;
    const panelStyle = getComputedStyle(panel);
    const canvasStyle = getComputedStyle(doodleCanvas);
    return {
      panelZ: Number(panelStyle.zIndex),
      canvasZ: Number(canvasStyle.zIndex) || 0,
      inBottomLeftCorner: Boolean(panel.closest('.canvas-corner-bottom-left')),
    };
  });
  expect(layout.inBottomLeftCorner).toBe(true);
  expect(layout.panelZ).toBeGreaterThan(layout.canvasZ);

  // window.updateCanvasStickyNotes dynamically updates visible + hidden stickies.
  await page.evaluate(() => {
    (window as StickyNotesWindow).updateCanvasStickyNotes?.(
      [
        { topic: 'Map', info: 'North gate' },
        { topic: 'Ally', info: 'Mira waits inside' },
      ],
      [],
    );
  });
  await expect(page.locator('#sticky-notes-count-badge')).toHaveText('2');
  await expect(btn.locator('.sticky-note-card')).toHaveCount(2);
  await expect(btn.locator('.sticky-note-card.is-hidden-sticky')).toHaveCount(0);
});

test('stickies revealed cache persists with 1-hour expiry and removes expired entries', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({
    isOrator: true,
    apiRoutes: {
      '/theaters/stage/sticky-notes': {
        sticky_notes: [
          { topic: 'Clue', info: 'Public note' },
          { topic: 'Spoiler', info: 'Secret door', hidden: true },
        ],
        hidden_stickies: ['Spoiler'],
      },
    },
  });

  const timeoutMs = await page.evaluate(
    () => (window as StickyNotesWindow).STICKIES_REVEAL_CACHE_TIMEOUT_MS,
  );
  expect(timeoutMs).toBe(60 * 60 * 1000);

  await page.evaluate(() => {
    (window as StickyNotesWindow).setStickiesRevealedInCache?.('stage', true);
  });

  const stored = await page.evaluate(() => {
    const raw = localStorage.getItem('narratron_stickies_revealed:stage');
    return raw
      ? (JSON.parse(raw) as { revealed: boolean; revealedAt: number; expiresAt: number })
      : null;
  });
  expect(stored).not.toBeNull();
  expect(stored!.revealed).toBe(true);
  expect(stored!.expiresAt - stored!.revealedAt).toBe(60 * 60 * 1000);
  expect(
    await page.evaluate(() => (window as StickyNotesWindow).isStickiesRevealedInCache?.('stage')),
  ).toBe(true);

  // Clearing removes the cache key.
  await page.evaluate(() => {
    (window as StickyNotesWindow).setStickiesRevealedInCache?.('stage', false);
  });
  expect(
    await page.evaluate(() => localStorage.getItem('narratron_stickies_revealed:stage')),
  ).toBeNull();

  // Expired cache entry returns false and cleans up localStorage.
  await page.evaluate(() => {
    localStorage.setItem(
      'narratron_stickies_revealed:stage',
      JSON.stringify({
        revealed: true,
        revealedAt: Date.now() - 7_200_000,
        expiresAt: Date.now() - 1_000,
      }),
    );
  });
  expect(
    await page.evaluate(() => (window as StickyNotesWindow).isStickiesRevealedInCache?.('stage')),
  ).toBe(false);
  expect(
    await page.evaluate(() => localStorage.getItem('narratron_stickies_revealed:stage')),
  ).toBeNull();

  // Fresh cache seeded before load reveals hidden stickies automatically.
  await canvas.openCanvas({
    isOrator: true,
    localStorage: {
      'narratron_stickies_revealed:stage': JSON.stringify({
        revealed: true,
        revealedAt: Date.now(),
        expiresAt: Date.now() + 60 * 60 * 1000,
      }),
    },
    apiRoutes: {
      '/theaters/stage/sticky-notes': {
        sticky_notes: [{ topic: 'Cached', info: 'Visible on reload', hidden: true }],
        hidden_stickies: ['Cached'],
      },
    },
  });
  await expect(page.locator('#sticky-notes-hidden-container')).toHaveCSS('display', 'flex');
});

test('sticky notes visibility follows active orator state, not URL role parameter', async ({
  canvas,
  page,
}) => {
  // URL says role=viewer, but baton API reports active orator -> sticky notes shown.
  await canvas.openCanvas({ isOrator: true });
  await page.goto(`${CANVAS_ORIGIN}/canvas?theater_id=stage&role=viewer`);
  await expect(page.locator('#sticky-notes-toggle-btn')).toHaveCSS('display', 'flex');

  // URL says role=orator, but baton API reports viewer -> sticky notes hidden,
  // and live baton_state toggles visibility dynamically.
  let doodleWs: WebSocketRoute | null = null;
  await canvas.openCanvas({
    isOrator: false,
    user: { id: 42, username: 'viewer' },
    ownerId: 1,
    onWebSocket(ws: WebSocketRoute) {
      if (ws.url().includes('/ws/doodle')) {
        doodleWs = ws;
      }
    },
  });
  await page.goto(`${CANVAS_ORIGIN}/canvas?theater_id=stage&role=orator`);

  const btn = page.locator('#sticky-notes-toggle-btn');
  await expect(btn).toHaveCSS('display', 'none');

  await expect.poll(() => Boolean(doodleWs)).toBe(true);
  doodleWs!.send(
    JSON.stringify({
      type: 'baton_state',
      baton_state: {
        owner: { id: 1, username: 'owner' },
        active_orator: { id: 42, username: 'viewer' },
        contributors: [],
      },
      active_viewers: [],
    }),
  );
  await expect(btn).toHaveCSS('display', 'flex');

  doodleWs!.send(
    JSON.stringify({
      type: 'baton_state',
      baton_state: {
        owner: { id: 1, username: 'owner' },
        active_orator: { id: 999, username: 'other-orator' },
        contributors: [],
      },
      active_viewers: [],
    }),
  );
  await expect(btn).toHaveCSS('display', 'none');
});

test('sticky notes and suggestions controls are excluded from selection pointer capture and dragstart', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    html: `
      <div id="image-container" style="position: relative; width: 400px; height: 300px;">
        <canvas id="doodle-canvas" width="400" height="300" style="width: 400px; height: 300px; display: block;"></canvas>
        <div id="canvas-selection-box" class="canvas-selection-box" style="display: none;"></div>
        <div id="sticky-notes-toggle-btn" style="position: absolute; left: 10px; bottom: 80px; width: 40px; height: 40px;">
          <button id="sticky-notes-hidden-toggle-btn" type="button">Reveal</button>
        </div>
        <div id="suggestions-toggle-btn" style="position: absolute; left: 10px; bottom: 20px; width: 40px; height: 40px;">Suggestions</div>
      </div>
    `,
    moduleScripts: [
      `
      const mod = await window.__importModule('/static/generated/canvas/doodle-renderer.js');
      const canvasEl = document.getElementById('doodle-canvas');
      const renderer = mod.createDoodleRenderer({
        canvas: canvasEl,
        canSelectSegment: () => true,
      });
      renderer.resize();
      `,
    ],
  });

  for (const selector of ['#sticky-notes-toggle-btn', '#suggestions-toggle-btn']) {
    const result = await page.locator(selector).evaluate((el) => {
      let setCaptureCalled = false;
      const container = document.getElementById('image-container')!;
      const origSetCapture = container.setPointerCapture;
      container.setPointerCapture = () => {
        setCaptureCalled = true;
      };
      const rect = el.getBoundingClientRect();
      el.dispatchEvent(
        new PointerEvent('pointerdown', {
          bubbles: true,
          cancelable: true,
          button: 0,
          pointerId: 1,
          clientX: rect.left + 10,
          clientY: rect.top + 10,
        }),
      );
      const dragEvent = new Event('dragstart', { bubbles: true, cancelable: true });
      el.dispatchEvent(dragEvent);
      container.setPointerCapture = origSetCapture;
      return {
        setCaptureCalled,
        dragDefaultPrevented: dragEvent.defaultPrevented,
      };
    });

    expect(result.setCaptureCalled).toBe(false);
    expect(result.dragDefaultPrevented).toBe(false);
  }
  await expect(page.locator('#canvas-selection-box')).toBeHidden();

  // In full canvas, #sticky-notes-toggle-btn stops pointerdown/mousedown from reaching #image-container.
  await canvas.openCanvas({ isOrator: true });
  const observed = await page.evaluate(() => {
    const container = document.getElementById('image-container')!;
    const btn = document.getElementById('sticky-notes-toggle-btn')!;
    const events: string[] = [];
    container.addEventListener('pointerdown', () => events.push('pointerdown'));
    container.addEventListener('mousedown', () => events.push('mousedown'));
    btn.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
    btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0 }));
    return events;
  });
  expect(observed).toEqual([]);
});

test('hidden stickies toggle reveals hidden stickies without pinning panel open on mouseleave', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({
    isOrator: true,
    apiRoutes: {
      '/theaters/stage/sticky-notes': {
        sticky_notes: [
          { topic: 'Visible', info: 'Shown right away' },
          { topic: 'Hidden', info: 'Secret clue', hidden: true },
        ],
        hidden_stickies: ['Hidden'],
      },
    },
  });

  const panel = page.locator('#sticky-notes-toggle-btn');
  const hiddenToggle = page.locator('#sticky-notes-hidden-toggle-btn');
  const hiddenContainer = page.locator('#sticky-notes-hidden-container');

  await expect(panel).toBeVisible();
  await expect(hiddenToggle).toHaveCount(1);

  // Hover opens the panel temporarily.
  await panel.dispatchEvent('mouseenter');
  await expect(panel).toHaveClass(/expanded/);

  // Clicking hidden toggle reveals hidden stickies without pinning the panel.
  await hiddenToggle.click();
  await expect(hiddenToggle).toHaveClass(/revealed/);
  await expect(hiddenContainer).toHaveCSS('display', 'flex');
  expect(
    await page.evaluate(() => (window as StickyNotesWindow).isStickiesRevealedInCache?.('stage')),
  ).toBe(true);

  await panel.dispatchEvent('mouseleave');
  await expect(panel).not.toHaveClass(/expanded/);

  // Clicking the header bar itself pins the panel open even after mouseleave.
  await panel.locator('.sticky-notes-header-bar').click();
  await expect(panel).toHaveClass(/expanded/);
  await panel.dispatchEvent('mouseleave');
  await page.waitForTimeout(350);
  await expect(panel).toHaveClass(/expanded/);
});
