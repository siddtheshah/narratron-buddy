import { expect, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';
import type { Page } from '@playwright/test';
import type { AnnotationNode, DoodleAnnotation, DoodleRenderer } from './doodle-renderer.js';

type RendererModule = typeof import('./doodle-renderer.js');
interface SelectionHarness {
  renderer: DoodleRenderer;
  readonly actions: DoodleAnnotation[];
  removedSegments: DoodleAnnotation[];
  removedStamps: DoodleAnnotation[];
  removedTexts: DoodleAnnotation[];
  add(actions: DoodleAnnotation[]): void;
  setPermissions(selectable: boolean, pagedBack: boolean): void;
}
declare global { interface Window { selectionHarness: SelectionHarness } }

async function mount(canvas: CanvasHarness, page: Page): Promise<void> {
  await canvas.mount({
    viewport: { width: 1000, height: 700 },
    html: `<div id="image-container"><canvas id="doodle-canvas"></canvas>
      <div id="canvas-text-layer"></div><div id="canvas-stamp-layer"></div>
      <div class="canvas-selection-box"></div></div>`,
    stylesheets: ['/static/css/canvas.css', '/static/css/chat.css', '/static/css/canvas-navigation.css'],
    inlineStyles: [`body {margin:0;padding:0;display:block}
      #image-container {width:800px;height:500px;flex:none;border:0;border-radius:0}`],
  });
  await page.evaluate(async () => {
    const { createDoodleRenderer } = await window.__importModule<RendererModule>('/static/generated/canvas/doodle-renderer.js');
    let selectable = true;
    let pagedBack = false;
    let actions: DoodleAnnotation[] = [
      { id: 'seg-1a', stroke_id: 'stroke-1', type: 'draw', x0: .1, y0: .2, x1: .3, y1: .2, color: '#ffffff', size: 4 },
      { id: 'seg-1b', stroke_id: 'stroke-1', type: 'draw', x0: .3, y0: .2, x1: .5, y1: .2, color: '#ffffff', size: 4 },
      { id: 'seg-2', stroke_id: 'stroke-2', type: 'draw', x0: .1, y0: .7, x1: .5, y1: .7, color: '#ff0000', size: 4 },
      { id: 'stamp-1', type: 'stamp', url: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"/>', name: 'Stamp 1', x: .7, y: .2, size: 80, rotation: 0 },
    ];
    const removedSegments: DoodleAnnotation[] = [];
    const removedStamps: DoodleAnnotation[] = [];
    const removedTexts: DoodleAnnotation[] = [];
    const renderer = createDoodleRenderer({
      canvas: document.getElementById('doodle-canvas') as HTMLCanvasElement,
      textLayer: document.getElementById('canvas-text-layer'),
      stampLayer: document.getElementById('canvas-stamp-layer'),
      canEditText: () => selectable && !pagedBack,
      canMoveStamp: () => true,
      canSelectSegment: () => selectable && !pagedBack,
      onRemoveText(text) {
        removedTexts.push(text);
        actions = actions.filter(action => action.id !== text.id);
        renderer.redraw(actions);
      },
      onRemoveStamp(stamp) {
        removedStamps.push(stamp);
        actions = actions.filter(action => action.id !== stamp.id);
        renderer.redraw(actions);
      },
      onRemoveSegment(removed) {
        removedSegments.push(...removed);
        const ids = new Set(removed.map(action => action.id));
        actions = actions.filter(action => !ids.has(action.id));
        renderer.redraw(actions);
      },
    });
    window.selectionHarness = {
      renderer, get actions() { return actions; }, removedSegments, removedStamps, removedTexts,
      add(added) { actions.push(...added); renderer.redraw(actions); },
      setPermissions(allowed, historical) { selectable = allowed; pagedBack = historical; },
    };
    renderer.resize(actions);
  });
}

async function drag(page: Page, x0: number, y0: number, x1: number, y1: number): Promise<void> {
  await page.mouse.move(x0, y0);
  await page.mouse.down();
  await page.mouse.move(x1, y1);
  await page.mouse.up();
}

async function selected(page: Page): Promise<{ segments: number; stamps: number; texts: number }> {
  return page.evaluate(() => {
    const r = window.selectionHarness.renderer;
    return { segments: r.getSelectedSegments().length, stamps: r.getSelectedStamps().length, texts: r.getSelectedTexts().length };
  });
}

async function addSecondStamp(page: Page): Promise<void> {
  await page.evaluate(() => window.selectionHarness.add([
    { id: 'stamp-2', type: 'stamp', url: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"/>', x: .85, y: .2, size: 80 },
  ]));
}

async function addText(page: Page, joint = false): Promise<void> {
  await page.evaluate((joint) => window.selectionHarness.add([
    { id: 'text-1', type: 'text', text: joint ? 'Joint Selection' : 'Secret Clue',
      x: joint ? .35 : .2, y: joint ? .15 : .5, size: joint ? 24 : 32, color: '#ffffff', font: 'Outfit' },
  ]), joint);
}

test.beforeEach(async ({ canvas, page }) => mount(canvas, page));

test('shim shares the typed renderer and selection APIs operate on real annotations', async ({ page }) => {
  await addText(page);
  expect(await page.evaluate(async () => {
    const module = await window.__importModule<RendererModule>('/static/generated/canvas/doodle-renderer.js');
    const shim = await window.__importModule<RendererModule>('/static/js/canvas-renderers.js');
    const { renderer: r, actions } = window.selectionHarness;
    r.selectSegment('seg-1a');
    const grouped = r.getSelectedSegments().map(action => action.id);
    const hit = r.findSegmentAt(160, 100)?.id;
    r.deselectSegment('stroke-1');
    const deselected = r.getSelectedSegments().length;
    const rect = r.selectSegmentsInRect(.05, .1, .55, .3).map(action => action.id);
    r.selectText('text-1');
    const byId = r.isTextSelected('text-1');
    const node = document.querySelector<AnnotationNode>('.canvas-text-annotation')!;
    const byNode = r.isTextSelected(node);
    const byAction = r.isTextSelected(actions.find(action => action.id === 'text-1')!);
    const texts = r.getTextsInRect(.1, .4, .5, .65).map(item => item.annotation.id);
    const legacyTexts = r.getSelectedText().map(item => item.id);
    r.clearSelectedTexts();
    const cleared = r.getSelectedTexts().length;
    const stamps = r.getStampsInRect(500, 40, 630, 160);
    r.selectStamp(stamps);
    const selectedStamp = r.getSelectedStamps()[0].id;
    r.cancelInteraction();
    return { same: module.createDoodleRenderer === shim.createDoodleRenderer, grouped, hit, deselected,
      rect, byId, byNode, byAction, texts, legacyTexts, cleared, selectedStamp,
      afterCancel: [r.getSelectedSegments().length, r.getSelectedStamps().length, r.getSelectedTexts().length] };
  })).toEqual({ same: true, grouped: ['seg-1a', 'seg-1b'], hit: 'seg-1a', deselected: 0,
    rect: ['seg-1a', 'seg-1b'], byId: true, byNode: true, byAction: true, texts: ['text-1'],
    legacyTexts: ['text-1'], cleared: 0, selectedStamp: 'stamp-1', afterCancel: [0, 0, 0] });
});

for (const [scale, panX, panY] of [[2, -100, -50], [.75, 80, 50], [1.5, -120, 30]]) {
  for (const reverse of [false, true]) {
    test(`selection box follows transformed screen drag (${scale}, reverse=${reverse})`, async ({ page }) => {
      await page.locator('#image-container').evaluate((container, view) => {
        container.style.margin = '35px 0 0 25px';
        container.style.border = '3px solid black';
        container.style.setProperty('--canvas-view-scale', String(view.scale));
        container.style.setProperty('--canvas-view-x', `${view.panX}px`);
        container.style.setProperty('--canvas-view-y', `${view.panY}px`);
      }, { scale, panX, panY });
      const scene = (await page.locator('#doodle-canvas').boundingBox())!;
      const left = scene.x + scene.width * .13;
      const right = scene.x + scene.width * .52;
      const top = scene.y + scene.height * .15;
      const bottom = scene.y + scene.height * .25;
      await page.mouse.move(reverse ? right : left, reverse ? bottom : top);
      await page.mouse.down();
      await page.mouse.move(reverse ? left : right, reverse ? top : bottom);
      const box = (await page.locator('.canvas-selection-box').boundingBox())!;
      for (const [actual, expected] of [[box.x, left], [box.y, top], [box.width, right - left], [box.height, bottom - top]]) {
        expect(Math.abs(actual - expected)).toBeLessThanOrEqual(.1);
      }
      await page.mouse.up();
      expect(await page.evaluate(() => window.selectionHarness.renderer.getSelectedSegments().map(action => action.id)))
        .toEqual(['seg-1a', 'seg-1b']);
      expect((await selected(page)).stamps).toBe(0);
    });
  }
}

test('clicking a stroke selects and deletes every segment from the same mouse press', async ({ page }) => {
  await page.mouse.click(160, 100);
  expect((await selected(page)).segments).toBe(2);
  expect(await page.evaluate(() => window.selectionHarness.actions.filter(action => action.type === 'draw')
    .map(action => window.selectionHarness.renderer.isSegmentSelected(action)))).toEqual([true, true, false]);
  await page.keyboard.press('Delete');
  expect(await page.evaluate(() => window.selectionHarness.actions.filter(action => action.type === 'draw').map(action => action.id)))
    .toEqual(['seg-2']);
});

test('marquee is visible during dragging and selects both strokes when released', async ({ page }) => {
  await page.mouse.move(50, 50);
  await page.mouse.down();
  await page.mouse.move(450, 400);
  await expect(page.locator('.canvas-selection-box')).toBeVisible();
  await page.mouse.up();
  await expect(page.locator('.canvas-selection-box')).toBeHidden();
  expect((await selected(page)).segments).toBe(3);
});

test('marquee selects stamps alone and together with strokes', async ({ page }) => {
  await drag(page, 500, 40, 630, 160);
  await expect(page.locator('.canvas-stamp-annotation')).toHaveClass(/movable/);
  expect((await selected(page)).stamps).toBe(1);
  await drag(page, 50, 40, 640, 160);
  expect(await selected(page)).toEqual({ segments: 2, stamps: 1, texts: 0 });
  await expect(page.locator('.canvas-stamp-annotation')).toHaveClass(/movable/);
});

test('selection drag across the center suppresses accidental stamp drag-over highlighting', async ({ canvas, page }) => {
  await canvas.openCanvas();
  const scene = (await page.locator('#doodle-canvas').boundingBox())!;
  const y = scene.y + scene.height * .5;
  await page.mouse.move(scene.x + scene.width * .2, y);
  await page.mouse.down();
  await page.mouse.move(scene.x + scene.width * .5, y);
  await page.mouse.move(scene.x + scene.width * .8, y);
  expect(await page.evaluate(() => window.isCanvasSelectionActive)).toBe(true);
  await expect(page.locator('#image-container')).not.toHaveClass(/stamp-drag-over/);
  await page.locator('#image-container').evaluate(container => {
    const dataTransfer = new DataTransfer();
    dataTransfer.setData('application/json', '{}');
    container.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer }));
  });
  await expect(page.locator('#image-container')).not.toHaveClass(/stamp-drag-over/);
  await page.mouse.up();
});

test('clicking off a stamp clears its selection and focus and prevents subsequent deletion', async ({ page }) => {
  await drag(page, 500, 40, 630, 160);
  const stamp = page.locator('.canvas-stamp-annotation');
  await expect(stamp).toBeFocused();
  expect((await selected(page)).stamps).toBe(1);
  await page.mouse.click(200, 200);
  expect((await selected(page)).stamps).toBe(0);
  await expect(stamp).not.toHaveClass(/movable/);
  await expect(stamp).not.toBeFocused();
  await page.keyboard.press('Delete');
  await expect(stamp).toHaveCount(1);
});

for (const withStrokes of [true, false]) {
  test(`multiple stamps delete together while preserving unselected strokes (withStrokes=${withStrokes})`, async ({ page }) => {
    await addSecondStamp(page);
    expect(await page.evaluate(() => window.selectionHarness.actions.filter(action => action.type === 'stamp').length)).toBe(2);
    expect(await page.evaluate(() => window.selectionHarness.actions.filter(action => action.type === 'draw').length)).toBe(3);
    await drag(page, withStrokes ? 50 : 500, 40, 760, 160);
    expect(await selected(page)).toEqual({ stamps: 2, segments: withStrokes ? 2 : 0, texts: 0 });
    await page.keyboard.press('Delete');
    await expect(page.locator('.canvas-stamp-annotation')).toHaveCount(0);
    expect(await page.evaluate(() => window.selectionHarness.removedStamps.length)).toBe(2);
    expect(await page.evaluate(() => window.selectionHarness.actions.filter(action => action.type === 'draw').map(action => action.id)))
      .toEqual(withStrokes ? ['seg-2'] : ['seg-1a', 'seg-1b', 'seg-2']);
  });
}

test('marquee selects text and excludes stamps and strokes outside its bounds', async ({ page }) => {
  await addText(page);
  const label = page.locator('.canvas-text-annotation');
  await expect(label).toHaveText('Secret Clue');
  await expect(label).not.toHaveClass(/movable/);
  await drag(page, 120, 220, 320, 300);
  await expect(label).toHaveClass(/movable/);
  expect(await selected(page)).toEqual({ segments: 0, stamps: 0, texts: 1 });
  expect(await page.evaluate(() => window.selectionHarness.renderer.getSelectedTexts()[0].id)).toBe('text-1');
});

test('marquee selects every annotation kind and clicking empty canvas clears them all', async ({ page }) => {
  await addText(page, true);
  await drag(page, 50, 40, 650, 160);
  expect(await selected(page)).toEqual({ segments: 2, stamps: 1, texts: 1 });
  await page.mouse.click(200, 400);
  expect(await selected(page)).toEqual({ segments: 0, stamps: 0, texts: 0 });
});

test('Escape clears text selection and its movable class', async ({ page }) => {
  await addText(page);
  await drag(page, 120, 220, 320, 300);
  expect((await selected(page)).texts).toBe(1);
  await page.keyboard.press('Escape');
  expect((await selected(page)).texts).toBe(0);
  await expect(page.locator('.canvas-text-annotation')).not.toHaveClass(/movable/);
});

test('group deletion removes selected text, stamps, and strokes and clears all selection', async ({ page }) => {
  await addText(page, true);
  expect(await page.evaluate(() => window.selectionHarness.actions.map(action => action.type)))
    .toEqual(['draw', 'draw', 'draw', 'stamp', 'text']);
  await drag(page, 50, 40, 650, 160);
  expect(await selected(page)).toEqual({ segments: 2, stamps: 1, texts: 1 });
  await page.keyboard.press('Delete');
  expect(await page.evaluate(() => {
    const h = window.selectionHarness;
    return { ids: h.actions.map(action => action.id), segments: h.removedSegments.length,
      stamps: h.removedStamps.length, texts: h.removedTexts.map(action => action.id) };
  })).toEqual({ ids: ['seg-2'], segments: 2, stamps: 1, texts: ['text-1'] });
  expect(await selected(page)).toEqual({ segments: 0, stamps: 0, texts: 0 });
  await expect(page.locator('.canvas-text-annotation')).toHaveCount(0);
});

for (const pagedBack of [false, true]) {
  test(`selection respects permissions and history paging (pagedBack=${pagedBack})`, async ({ page }) => {
    await page.evaluate((historical) => window.selectionHarness.setPermissions(historical, historical), pagedBack);
    await drag(page, 50, 50, 450, 400);
    expect(await selected(page)).toEqual({ segments: 0, stamps: 0, texts: 0 });
  });
}
