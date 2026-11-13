import { expect, test } from '../../testing/ui/canvas_fixtures';

interface HideCanvasTextWindow extends Window {
  setCanvasTextHidden?: (hidden: boolean) => void;
}

test('hide canvas text button toggles all canvas text overlays and persists in localStorage', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({
    isOrator: true,
    collabEnabled: true,
    apiRoutes: {
      '/api/latest': async (route) => {
        await route.fulfill({
          json: {
            viewer_collab_enabled: true,
            orator_cursor: 0,
            latest: '/static/narratron-avatar.png',
            time: 1,
            history: [{ page_id: 'p1', url: '/static/narratron-avatar.png', time: 1 }],
            dialogue: 'A mysterious clue appears.',
          },
        });
      },
    },
    onWebSocket(ws) {
      if (!ws.url().includes('/ws/doodle')) return;
      ws.onMessage((raw) => {
        const msg = JSON.parse(String(raw)) as { type?: string; page_id?: string };
        if (msg.type === 'subscribe_doodles') {
          ws.send(
            JSON.stringify({
              type: 'doodle_snapshot',
              page_id: msg.page_id ?? 'p1',
              enabled: true,
              batches: [],
              annotations: [
                {
                  type: 'text',
                  id: 'note-1',
                  text: 'Hidden clue',
                  x: 0.25,
                  y: 0.25,
                  color: '#ffffff',
                  size: 28,
                },
              ],
            }),
          );
        }
      });
    },
  });

  const btn = page.locator('#hide-canvas-text-btn');
  const container = page.locator('#image-container');
  const visibleIcon = btn.locator('.hide-text-icon-visible');
  const hiddenIcon = btn.locator('.hide-text-icon-hidden');
  const textLayer = page.locator('#canvas-text-layer');
  const dialogueOverlay = page.locator('#scene-dialogue-overlay');

  await expect(btn).toBeVisible();
  await expect(btn).toHaveAttribute('aria-pressed', 'false');
  await expect(btn).toHaveAttribute('title', 'Hide all text on canvas');
  await expect(btn).toHaveAttribute('aria-label', 'Hide all text on canvas');
  await expect(visibleIcon).toHaveCSS('display', 'block');
  await expect(hiddenIcon).toHaveCSS('display', 'none');
  await expect(container).not.toHaveClass(/hide-canvas-text/);
  await expect(page.locator('.canvas-text-annotation')).toHaveText('Hidden clue');
  await expect(textLayer).not.toHaveCSS('display', 'none');
  await expect(dialogueOverlay).toHaveCount(1);

  // Inject an active .canvas-text-editor and show #scene-dialogue-overlay to verify CSS hides all targets.
  await page.evaluate(() => {
    const imgContainer = document.getElementById('image-container');
    if (!imgContainer) return;
    const editor = document.createElement('textarea');
    editor.className = 'canvas-text-editor';
    editor.value = 'Drafting';
    imgContainer.appendChild(editor);

    const dialogueOverlayEl = document.getElementById('scene-dialogue-overlay');
    if (dialogueOverlayEl) {
      dialogueOverlayEl.style.display = 'block';
    }
  });

  await btn.click();

  await expect(container).toHaveClass(/hide-canvas-text/);
  await expect(btn).toHaveClass(/active/);
  await expect(btn).toHaveAttribute('aria-pressed', 'true');
  await expect(btn).toHaveAttribute('title', 'Show all text on canvas');
  await expect(btn).toHaveAttribute('aria-label', 'Show all text on canvas');
  await expect(visibleIcon).toHaveCSS('display', 'none');
  await expect(hiddenIcon).toHaveCSS('display', 'block');
  await expect(textLayer).toHaveCSS('display', 'none');
  await expect(page.locator('.canvas-text-editor')).toHaveCSS('display', 'none');
  await expect(dialogueOverlay).toHaveCSS('display', 'none');
  expect(await page.evaluate(() => localStorage.getItem('narratron-hide-canvas-text'))).toBe(
    'true',
  );

  // Clicking again shows everything and updates localStorage.
  await btn.click();
  await expect(container).not.toHaveClass(/hide-canvas-text/);
  await expect(btn).not.toHaveClass(/active/);
  await expect(btn).toHaveAttribute('aria-pressed', 'false');
  await expect(btn).toHaveAttribute('title', 'Hide all text on canvas');
  await expect(visibleIcon).toHaveCSS('display', 'block');
  await expect(hiddenIcon).toHaveCSS('display', 'none');
  await expect(textLayer).not.toHaveCSS('display', 'none');
  await expect(dialogueOverlay).not.toHaveCSS('display', 'none');
  expect(await page.evaluate(() => localStorage.getItem('narratron-hide-canvas-text'))).toBe(
    'false',
  );

  // window.setCanvasTextHidden can also toggle state cleanly.
  await page.evaluate(() => {
    (window as HideCanvasTextWindow).setCanvasTextHidden?.(true);
  });
  await expect(container).toHaveClass(/hide-canvas-text/);
  expect(await page.evaluate(() => localStorage.getItem('narratron-hide-canvas-text'))).toBe(
    'true',
  );
});

test('hide canvas text state restores from localStorage on load', async ({ canvas, page }) => {
  await canvas.openCanvas({
    isOrator: false,
    localStorage: { 'narratron-hide-canvas-text': 'true' },
  });

  const btn = page.locator('#hide-canvas-text-btn');
  const container = page.locator('#image-container');
  await expect(container).toHaveClass(/hide-canvas-text/);
  await expect(btn).toHaveClass(/active/);
  await expect(btn).toHaveAttribute('aria-pressed', 'true');
  await expect(btn).toHaveAttribute('title', 'Show all text on canvas');
  await expect(page.locator('#canvas-text-layer')).toHaveCSS('display', 'none');
  await expect(page.locator('#scene-dialogue-overlay')).toHaveCSS('display', 'none');
});

test('canvas pin button has circular 44px dimensions and zero padding', async ({ canvas, page }) => {
  await canvas.openCanvas({ isOrator: true });

  const pinBtn = page.locator('#canvas-pin-btn');
  await expect(pinBtn).toBeVisible();
  const styles = await pinBtn.evaluate((el) => {
    const cs = getComputedStyle(el);
    return {
      width: cs.width,
      height: cs.height,
      borderRadius: cs.borderRadius,
      paddingTop: cs.paddingTop,
      paddingRight: cs.paddingRight,
      paddingBottom: cs.paddingBottom,
      paddingLeft: cs.paddingLeft,
    };
  });

  expect(styles).toEqual({
    width: '44px',
    height: '44px',
    borderRadius: '22px',
    paddingTop: '0px',
    paddingRight: '0px',
    paddingBottom: '0px',
    paddingLeft: '0px',
  });
});

test('hide canvas text button is excluded from pointer capture and drag selection box', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    html: `
      <div id="image-container" style="position: relative; width: 400px; height: 300px;">
        <canvas id="doodle-canvas" width="400" height="300" style="width: 400px; height: 300px; display: block;"></canvas>
        <div id="canvas-selection-box" class="canvas-selection-box" style="display: none;"></div>
        <button id="hide-canvas-text-btn" type="button" style="position: absolute; left: 20px; bottom: 20px; width: 40px; height: 40px;">
          <span class="hide-text-icon-visible">Aa</span>
        </button>
      </div>
    `,
    moduleScripts: [
      `
      const mod = await window.__importModule('/static/generated/canvas/doodle-renderer.js');
      const canvas = document.getElementById('doodle-canvas');
      const renderer = mod.createDoodleRenderer({
        canvas,
        canSelectSegment: () => true,
      });
      renderer.resize();
      `,
    ],
  });

  const btn = page.locator('#hide-canvas-text-btn');
  const selectionBox = page.locator('#canvas-selection-box');

  const btnDrag = await btn.evaluate((el) => {
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

  expect(btnDrag.setCaptureCalled).toBe(false);
  expect(btnDrag.dragDefaultPrevented).toBe(false);
  await expect(selectionBox).toBeHidden();

  // Control check: dragging on the canvas surface itself DOES capture pointer and prevent dragstart.
  const canvasDrag = await page.locator('#doodle-canvas').evaluate((el) => {
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
        pointerId: 2,
        clientX: rect.left + 200,
        clientY: rect.top + 150,
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

  expect(canvasDrag.setCaptureCalled).toBe(true);
  expect(canvasDrag.dragDefaultPrevented).toBe(true);
});

test('openCanvas hide-text button stops pointerdown/mousedown propagation', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({ isOrator: true });

  const containerEvents = await page.evaluate(() => {
    const container = document.getElementById('image-container')!;
    const btn = document.getElementById('hide-canvas-text-btn')!;
    const observed: string[] = [];
    container.addEventListener('pointerdown', () => observed.push('pointerdown'));
    container.addEventListener('mousedown', () => observed.push('mousedown'));
    btn.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
    btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0 }));
    return observed;
  });

  expect(containerEvents).toEqual([]);
});
