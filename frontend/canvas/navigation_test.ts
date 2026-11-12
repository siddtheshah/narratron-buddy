import fs from 'node:fs';
import path from 'node:path';
import { expect, REPO_ROOT, test } from '../../testing/ui/canvas_fixtures';

type CursorStateModule = typeof import('/static/generated/canvas/cursor-state.js');
type NavigationModule = typeof import('/static/generated/canvas/navigation.js');

interface DoodleAction {
  type: string;
  id: string;
  x0?: number;
  y0?: number;
  x1?: number;
  y1?: number;
  x?: number;
  y?: number;
  size?: number;
  color?: string;
  url?: string;
}

interface ImageRendererInstance {
  resize(): void;
  applyTransition(url: string, transition: string, effect: string): Promise<void>;
}

interface DoodleRendererInstance {
  resize(actions: DoodleAction[]): void;
  redraw(actions: DoodleAction[]): void;
  getSelectedSegments(): unknown[];
}

interface RenderersModule {
  createImageRenderer(options: {
    canvas: HTMLCanvasElement;
    image: HTMLImageElement;
    fitMode?: string;
  }): ImageRendererInstance;
  createDoodleRenderer(options: {
    canvas: HTMLCanvasElement;
    stampLayer: HTMLElement;
    canSelectSegment: () => boolean;
    canMoveStamp: () => boolean;
    onMoveStamp: (action: DoodleAction) => void;
  }): DoodleRendererInstance;
}

interface NavigationTestGlobals {
  scene?: ImageRendererInstance;
  annotations?: DoodleRendererInstance;
  actions?: DoodleAction[];
  originalActions?: string;
  cursorState?: CursorStateModule['canvasCursorState'];
}

const CURSOR_MODULE_URL = '/static/generated/canvas/cursor-state.js';
const NAVIGATION_MODULE_URL = '/static/generated/canvas/navigation.js';

async function readNavigationToggleMarkup(): Promise<string> {
  const templateHtml = await fs.promises.readFile(
    path.join(REPO_ROOT, 'templates', 'canvas.html'),
    'utf-8',
  );
  const match = templateHtml.match(/<button id="canvas-navigation-toggle"[\s\S]*?<\/button>/);
  if (!match) {
    throw new Error('Could not find #canvas-navigation-toggle in templates/canvas.html');
  }
  return match[0];
}

test('navigation keeps rendered content aligned, converts stamp coordinates, handles touch pinch, clamps zoom, and resets locally', async ({
  canvas,
  page,
}) => {
  const toggleMarkup = await readNavigationToggleMarkup();

  await canvas.mount({
    viewport: { width: 1000, height: 800 },
    stylesheets: ['/static/css/canvas-navigation.css'],
    inlineStyles: [
      `
        body { margin: 0; }
        #image-container { position: relative; width: 600px; height: 400px; overflow: hidden; }
        canvas, #canvas-stamp-layer, #canvas-text-layer { position: absolute; inset: 0; width: 100%; height: 100%; }
        #doodle-canvas, #canvas-text-layer, #canvas-stamp-layer { pointer-events: none; }
        .canvas-stamp-annotation { position: absolute; pointer-events: auto; }
        .canvas-stamp-annotation img { width: 100%; height: 100%; }
      `,
    ],
    html: `
      <div id="image-container">
        <canvas id="canvas-renderer"></canvas>
        <img id="current-image" hidden alt="">
        <div id="canvas-stamp-layer"></div>
        <canvas id="doodle-canvas"></canvas>
        <div id="canvas-text-layer"></div>
        <div id="canvas-navigation-surface" aria-hidden="true"></div>
        ${toggleMarkup}
      </div>
    `,
  });

  await page.evaluate(
    async ({ cursorUrl, navUrl }) => {
      const globals = window as unknown as NavigationTestGlobals;
      const { canvasCursorState } = await window.__importModule<CursorStateModule>(cursorUrl);
      await window.__importModule<NavigationModule>(navUrl);
      globals.cursorState = canvasCursorState;

      const { createImageRenderer, createDoodleRenderer } =
        await window.__importModule<RenderersModule>('/static/js/canvas-renderers.js');

      const rendererCanvas = document.getElementById('canvas-renderer') as HTMLCanvasElement;
      const currentImage = document.getElementById('current-image') as HTMLImageElement;
      const doodleCanvas = document.getElementById('doodle-canvas') as HTMLCanvasElement;
      const stampLayer = document.getElementById('canvas-stamp-layer') as HTMLElement;

      const scene = createImageRenderer({ canvas: rendererCanvas, image: currentImage });
      scene.resize();
      globals.scene = scene;

      const source = document.createElement('canvas');
      source.width = 600;
      source.height = 400;
      const ctx = source.getContext('2d');
      if (!ctx) {
        throw new Error('Missing 2D canvas context');
      }
      ctx.fillStyle = '#00ff00';
      ctx.fillRect(0, 0, 600, 400);
      await scene.applyTransition(source.toDataURL(), 'none', 'none');

      const actions: DoodleAction[] = [
        { type: 'draw', id: 'line', x0: 0.2, y0: 0.25, x1: 0.3, y1: 0.25, color: '#ff0000', size: 6 },
        { type: 'stamp', id: 'stamp', x: 0.5, y: 0.5, size: 100, url: source.toDataURL() },
      ];
      globals.actions = actions;

      const annotations = createDoodleRenderer({
        canvas: doodleCanvas,
        stampLayer,
        canSelectSegment: () => canvasCursorState.current.type === 'select',
        canMoveStamp: () => canvasCursorState.current.type === 'select',
        onMoveStamp: (action) => {
          actions[1] = action;
          annotations.redraw(actions);
        },
      });
      globals.annotations = annotations;
      annotations.resize(actions);
      globals.originalActions = JSON.stringify(actions);
    },
    { cursorUrl: CURSOR_MODULE_URL, navUrl: NAVIGATION_MODULE_URL },
  );

  const button = page.getByRole('button', { name: 'Zoom and pan' });
  const surface = page.locator('#canvas-navigation-surface');
  const stamp = page.locator('.canvas-stamp-annotation');
  const doodleCanvas = page.locator('#doodle-canvas');

  const originalStamp = await stamp.boundingBox();
  expect(originalStamp).not.toBeNull();
  await expect(surface).toBeHidden();

  // Disabled mode leaves wheel input alone.
  await page.mouse.move(208, 158);
  await page.mouse.wheel(0, -400);
  expect((await doodleCanvas.boundingBox())?.width).toBe(600);

  // Enable navigation and zoom in with the wheel.
  await button.click();
  await expect(button).toHaveAttribute('aria-pressed', 'true');
  await expect(surface).toBeVisible();
  await page.mouse.move(208, 158);
  await page.mouse.wheel(0, -400);
  await page.waitForFunction(
    () =>
      Number(
        document.getElementById('image-container')?.style.getPropertyValue('--canvas-view-scale'),
      ) > 1,
  );

  const scale = await page.evaluate(() =>
    Number(
      document.getElementById('image-container')?.style.getPropertyValue('--canvas-view-scale'),
    ),
  );
  const zoomed = await stamp.boundingBox();
  expect(zoomed).not.toBeNull();
  expect(zoomed!.width).toBeCloseTo(originalStamp!.width * scale, 1);

  // Both canvases and stamps share a transform, including after incoming redraws/resizes.
  await page.evaluate(() => {
    const globals = window as unknown as NavigationTestGlobals;
    globals.annotations!.redraw(globals.actions!);
    globals.scene!.resize();
    globals.annotations!.resize(globals.actions!);
  });
  expect((await stamp.boundingBox())!.width).toBeCloseTo(zoomed!.width, 1);
  expect(
    await page.evaluate(
      () => (document.getElementById('doodle-canvas') as HTMLCanvasElement).width,
    ),
  ).toBe(600);
  expect(
    await page.evaluate(() => {
      const ctx = (document.getElementById('doodle-canvas') as HTMLCanvasElement).getContext('2d');
      return Array.from(ctx!.getImageData(150, 100, 1, 1).data);
    }),
  ).toEqual([255, 0, 0, 255]);
  expect(
    await page.evaluate(() => {
      const ctx = (
        document.getElementById('canvas-renderer') as HTMLCanvasElement
      ).getContext('2d');
      return Array.from(ctx!.getImageData(599, 399, 1, 1).data);
    }),
  ).toEqual([0, 255, 0, 255]);

  // Dragging on the navigation surface pans visually without mutating actions or selecting segments.
  await page.mouse.move(208, 158);
  await page.mouse.down();
  await page.mouse.move(248, 188);
  await page.mouse.up();
  const moved = await stamp.boundingBox();
  expect(moved).not.toBeNull();
  expect(moved!.x - zoomed!.x).toBeCloseTo(40, 1);
  expect(moved!.y - zoomed!.y).toBeCloseTo(30, 1);
  expect(
    await page.evaluate(() => {
      const globals = window as unknown as NavigationTestGlobals;
      return JSON.stringify(globals.actions) === globals.originalActions;
    }),
  ).toBe(true);
  expect(
    await page.evaluate(() => {
      const globals = window as unknown as NavigationTestGlobals;
      return globals.annotations!.getSelectedSegments().length;
    }),
  ).toBe(0);

  // Switching to marker exits navigation without changing framing.
  await page.evaluate(async (cursorUrl: string) => {
    const { canvasCursorState } = await window.__importModule<CursorStateModule>(cursorUrl);
    canvasCursorState.select({ type: 'marker', color: '#ff0000' });
  }, CURSOR_MODULE_URL);
  await expect(surface).toBeHidden();
  await expect(button).toHaveAttribute('aria-pressed', 'false');
  expect((await stamp.boundingBox())!.x).toBeCloseTo(moved!.x, 1);

  // Stamp dragging in select mode converts retained zoomed/panned view coords back to theater coords.
  const drawingRect = await doodleCanvas.boundingBox();
  expect(drawingRect).not.toBeNull();
  await page.evaluate(async (cursorUrl: string) => {
    const { canvasCursorState } = await window.__importModule<CursorStateModule>(cursorUrl);
    canvasCursorState.select({ type: 'select' });
  }, CURSOR_MODULE_URL);
  const stampCenterX = moved!.x + moved!.width / 2;
  const stampCenterY = moved!.y + moved!.height / 2;
  await page.mouse.move(stampCenterX, stampCenterY);
  await page.mouse.down();
  await page.mouse.move(stampCenterX + 20, stampCenterY + 10);
  await page.mouse.up();
  const updatedStamp = await page.evaluate(() => {
    const globals = window as unknown as NavigationTestGlobals;
    return globals.actions![1];
  });
  expect(updatedStamp.x).toBeCloseTo(0.5 + 20 / drawingRect!.width, 5);
  expect(updatedStamp.y).toBeCloseTo(0.5 + 10 / drawingRect!.height, 5);

  // Re-enabling navigation preserves the current framing.
  await button.click();
  expect((await stamp.boundingBox())!.width).toBeCloseTo(moved!.width, 1);

  // Real touch pointers exercise pinch zoom and touch cancellation without emulating a wheel.
  const touch = await page.context().newCDPSession(page);
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [
      { x: 200, y: 200, id: 1 },
      { x: 300, y: 200, id: 2 },
    ],
  });
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchMove',
    touchPoints: [
      { x: 150, y: 200, id: 1 },
      { x: 350, y: 200, id: 2 },
    ],
  });
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchCancel',
    touchPoints: [],
  });
  const pinchedScale = await page.evaluate(() =>
    Number(
      document.getElementById('image-container')?.style.getPropertyValue('--canvas-view-scale'),
    ),
  );
  expect(pinchedScale).toBeCloseTo(scale * 2, 5);
  await expect(surface).not.toHaveClass(/\bdragging\b/);

  // Double-clicking the surface resets framing, and wheel zoom clamps to [0.5, 5].
  await surface.dblclick({ position: { x: 100, y: 100 } });
  expect((await doodleCanvas.boundingBox())!.width).toBe(600);

  await page.mouse.move(208, 158);
  await page.mouse.wheel(0, -100000);
  await page.waitForFunction(
    () =>
      document.getElementById('image-container')?.style.getPropertyValue('--canvas-view-scale') ===
      '5',
  );
  await page.mouse.wheel(0, 100000);
  await page.waitForFunction(
    () =>
      document.getElementById('image-container')?.style.getPropertyValue('--canvas-view-scale') ===
      '0.5',
  );

  // Toggling navigation off keeps the clamped 0.5 framing.
  await button.click();
  await expect(surface).toBeHidden();
  await expect(button).toHaveAttribute('aria-pressed', 'false');
  expect((await stamp.boundingBox())!.width).toBeCloseTo(originalStamp!.width * 0.5, 1);
  expect((await doodleCanvas.boundingBox())!.width).toBe(300);

  // Reset also works via double-click on #image-container while navigation is off.
  await page.locator('#image-container').dblclick({ position: { x: 100, y: 100 } });
  expect((await stamp.boundingBox())!.width).toBeCloseTo(originalStamp!.width, 1);
  expect((await doodleCanvas.boundingBox())!.width).toBe(600);
});

test('drawing under zoomed navigation in canvas.html converts coordinates, preserves navigate across tool refreshes, and ends stroke on tool switch', async ({
  canvas,
  page,
}) => {
  const sentMessages: Array<Record<string, unknown>> = [];
  let doodleWs: import('@playwright/test').WebSocketRoute | null = null;

  await canvas.openCanvas({
    isOrator: true,
    collabEnabled: true,
    viewport: { width: 1200, height: 800 },
    onWebSocket: (ws) => {
      if (ws.url().includes('/ws/doodle')) {
        doodleWs = ws;
        ws.onMessage((raw) => {
          const text = typeof raw === 'string' ? raw : raw.toString('utf-8');
          const parsed = JSON.parse(text) as Record<string, unknown>;
          sentMessages.push(parsed);
          if (typeof parsed.client_message_id === 'string') {
            ws.send(
              JSON.stringify({
                type: 'doodle_ack',
                client_message_id: parsed.client_message_id,
              }),
            );
          }
        });
        queueMicrotask(() => {
          ws.send(
            JSON.stringify({
              type: 'doodle_snapshot',
              page_id: 'nav-page',
              enabled: true,
              doodles: [],
            }),
          );
        });
      }
    },
  });

  await expect.poll(() => doodleWs !== null).toBe(true);

  const container = page.locator('#image-container');
  const navToggle = page.locator('#canvas-navigation-toggle');
  const surface = page.locator('#canvas-navigation-surface');
  const doodleCanvas = page.locator('#doodle-canvas');
  const blueMarker = page.locator('.color-btn[data-color="#3b82f6"]');
  const greenMarker = page.locator('.color-btn[data-color="#22c55e"]');
  const textToggle = page.locator('#text-annotation-toggle');

  const containerBox = await container.boundingBox();
  const initialBox = await doodleCanvas.boundingBox();
  expect(containerBox).not.toBeNull();
  expect(initialBox).not.toBeNull();

  // Zoom in and pan using real navigation controls on canvas.html.
  await navToggle.click();
  await expect(surface).toBeVisible();
  const centerX = containerBox!.x + containerBox!.width / 2;
  const centerY = containerBox!.y + containerBox!.height / 2;
  await page.mouse.move(centerX, centerY);
  await page.mouse.wheel(0, -400);
  await page.waitForFunction(
    () =>
      Number(
        document.getElementById('image-container')?.style.getPropertyValue('--canvas-view-scale'),
      ) > 1,
  );
  await page.mouse.move(centerX, centerY);
  await page.mouse.down();
  await page.mouse.move(centerX + 30, centerY + 20);
  await page.mouse.up();

  // Routine canvas refreshes (e.g. incoming doodles_toggle snapshot/sync) must not cancel navigate mode.
  doodleWs!.send(JSON.stringify({ type: 'doodles_toggle', page_id: 'nav-page', enabled: true }));
  await expect(navToggle).toHaveAttribute('aria-pressed', 'true');
  await expect(surface).toBeVisible();

  // Choosing text exits navigation, and toggling navigation back clears text and survives refreshes.
  await textToggle.click();
  await page.keyboard.press('Escape');
  await expect(navToggle).toHaveAttribute('aria-pressed', 'false');
  await expect(textToggle).toHaveAttribute('aria-pressed', 'true');
  await navToggle.click();
  doodleWs!.send(JSON.stringify({ type: 'doodles_toggle', page_id: 'nav-page', enabled: true }));
  await expect(navToggle).toHaveAttribute('aria-pressed', 'true');
  await expect(textToggle).toHaveAttribute('aria-pressed', 'false');

  // Switching between markers exits navigation without losing framing.
  await greenMarker.click();
  await expect(greenMarker).toHaveAttribute('aria-pressed', 'true');
  await expect(navToggle).toHaveAttribute('aria-pressed', 'false');
  await blueMarker.click();
  await expect(surface).toBeHidden();
  await expect(navToggle).toHaveAttribute('aria-pressed', 'false');
  await expect(greenMarker).toHaveAttribute('aria-pressed', 'false');
  await expect(blueMarker).toHaveAttribute('aria-pressed', 'true');

  const zoomedBox = await doodleCanvas.boundingBox();
  expect(zoomedBox).not.toBeNull();
  expect(zoomedBox!.width).toBeGreaterThan(initialBox!.width);

  // Draw in the clear interior of #image-container away from corner controls.
  const startX = Math.round(containerBox!.x + containerBox!.width * 0.5);
  const startY = Math.round(containerBox!.y + containerBox!.height * 0.5);
  const endX = startX + 40;
  const endY = startY + 30;

  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(endX, endY);
  await page.mouse.up();

  await expect
    .poll(() => sentMessages.some((msg) => msg.type === 'draw' || msg.type === 'draw_batch'))
    .toBe(true);

  const drawMsg = sentMessages
    .filter((msg) => msg.type === 'draw' || msg.type === 'draw_batch')
    .at(-1)!;
  expect(drawMsg.page_id).toBe('nav-page');
  const points =
    drawMsg.type === 'draw_batch'
      ? (drawMsg.points as number[])
      : [drawMsg.x0 as number, drawMsg.y0 as number, drawMsg.x1 as number, drawMsg.y1 as number];
  expect(points[0]).toBeCloseTo((startX - zoomedBox!.x) / zoomedBox!.width, 2);
  expect(points[1]).toBeCloseTo((startY - zoomedBox!.y) / zoomedBox!.height, 2);
  expect(points.at(-2)!).toBeCloseTo((endX - zoomedBox!.x) / zoomedBox!.width, 2);
  expect(points.at(-1)!).toBeCloseTo((endY - zoomedBox!.y) / zoomedBox!.height, 2);

  // Mid-stroke cursor transition to navigate flushes/finishes the stroke and ignores further moves.
  const countBeforeInterrupt = sentMessages.length;
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(startX + 12, startY + 12);
  await page.evaluate(async (cursorUrl: string) => {
    const { canvasCursorState } = await window.__importModule<CursorStateModule>(cursorUrl);
    canvasCursorState.select({ type: 'navigate' });
  }, CURSOR_MODULE_URL);
  await expect(surface).toBeVisible();
  await expect(navToggle).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => sentMessages.length).toBeGreaterThan(countBeforeInterrupt);

  const countAfterInterrupt = sentMessages.length;
  await page.mouse.move(startX + 50, startY + 50);
  await page.mouse.up();
  expect(sentMessages.length).toBe(countAfterInterrupt);
});

test('clears dragging state on window blur and exposes reset/destroy controller API', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    stylesheets: ['/static/css/canvas-navigation.css'],
    inlineStyles: [
      `
        body { margin: 0; }
        #image-container { position: relative; width: 400px; height: 300px; }
        #outside { width: 100px; height: 40px; }
      `,
    ],
    html: `
      <div id="image-container">
        <div id="canvas-navigation-surface" aria-hidden="true"></div>
        <button id="canvas-navigation-toggle" type="button" aria-pressed="false">Zoom</button>
      </div>
      <div id="outside">Outside</div>
    `,
  });

  await page.evaluate(
    async ({ cursorUrl, navUrl }) => {
      const { canvasCursorState } = await window.__importModule<CursorStateModule>(cursorUrl);
      await window.__importModule<NavigationModule>(navUrl);
      canvasCursorState.select({ type: 'navigate' });
    },
    { cursorUrl: CURSOR_MODULE_URL, navUrl: NAVIGATION_MODULE_URL },
  );

  const surface = page.locator('#canvas-navigation-surface');
  await expect(surface).toBeVisible();
  await page.mouse.move(100, 100);
  await page.mouse.down();
  await expect(surface).toHaveClass(/\bdragging\b/);

  await page.evaluate(() => window.dispatchEvent(new Event('blur')));
  await expect(surface).not.toHaveClass(/\bdragging\b/);
  await page.mouse.up();

  // Verify explicit initializeCanvasNavigation({ container, toggle, surface }) reset and destroy.
  const controllerResult = await page.evaluate(
    async ({ cursorUrl, navUrl }) => {
      const { canvasCursorState } = await window.__importModule<CursorStateModule>(cursorUrl);
      const { initializeCanvasNavigation } = await window.__importModule<NavigationModule>(navUrl);

      const customContainer = document.createElement('div');
      customContainer.style.cssText = 'position: relative; width: 200px; height: 150px;';
      const customSurface = document.createElement('div');
      customSurface.style.cssText = 'position: absolute; inset: 0;';
      const customToggle = document.createElement('button');
      const customOutside = document.createElement('div');
      customContainer.append(customSurface, customToggle, customOutside);
      document.body.append(customContainer);

      const controller = initializeCanvasNavigation({
        container: customContainer,
        toggle: customToggle,
        surface: customSurface,
      });
      if (!controller) {
        throw new Error('Expected navigation controller');
      }

      canvasCursorState.select({ type: 'navigate' });
      customSurface.dispatchEvent(
        new WheelEvent('wheel', { deltaY: -200, clientX: 50, clientY: 50 }),
      );
      const scaleAfterWheel = Number(
        customContainer.style.getPropertyValue('--canvas-view-scale'),
      );

      // Double-clicking an unrelated child does not reset framing.
      customOutside.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
      const scaleAfterOutsideDblClick = Number(
        customContainer.style.getPropertyValue('--canvas-view-scale'),
      );

      controller.reset();
      const scaleAfterReset = Number(
        customContainer.style.getPropertyValue('--canvas-view-scale'),
      );

      controller.destroy();
      customSurface.dispatchEvent(
        new WheelEvent('wheel', { deltaY: -200, clientX: 50, clientY: 50 }),
      );
      const scaleAfterDestroy = Number(
        customContainer.style.getPropertyValue('--canvas-view-scale'),
      );

      return {
        scaleAfterWheel,
        scaleAfterOutsideDblClick,
        scaleAfterReset,
        scaleAfterDestroy,
      };
    },
    { cursorUrl: CURSOR_MODULE_URL, navUrl: NAVIGATION_MODULE_URL },
  );

  expect(controllerResult.scaleAfterWheel).toBeGreaterThan(1);
  expect(controllerResult.scaleAfterOutsideDblClick).toBe(controllerResult.scaleAfterWheel);
  expect(controllerResult.scaleAfterReset).toBe(1);
  expect(controllerResult.scaleAfterDestroy).toBe(1);
});
