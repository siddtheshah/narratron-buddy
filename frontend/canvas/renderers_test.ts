import { expect, test } from '../../testing/ui/canvas_fixtures';

type FitMode = 'contain' | 'cover';
interface Plane { x: number; y: number; width: number; height: number }
interface Point { x: number; y: number }
interface RenderAction {
  type: string;
  id: string;
  [key: string]: string | number;
}
interface DoodleRenderer {
  resize(actions: RenderAction[]): void;
  imagePlane(action: RenderAction): Plane;
  normalizedPoint(event: { clientX: number; clientY: number }): Point;
  findSegmentAt(x: number, y: number): RenderAction | null;
}
interface ImageRenderer {
  resize(): void;
  applyTransition(url: string, transition: string, effect: string): Promise<void>;
}
// Explicit boundaries for the renderer while its implementation remains JavaScript.
interface RenderersModule {
  createDoodleRenderer(options: {
    canvas: HTMLCanvasElement;
    fitMode: FitMode;
    getPage: () => { width: number; height: number };
    stampLayer: HTMLElement;
    textLayer: HTMLElement;
  }): DoodleRenderer;
  createImageRenderer(options: {
    canvas: HTMLCanvasElement;
    image: HTMLImageElement;
    fitMode: FitMode;
  }): ImageRenderer;
}

declare global {
  interface Window {
    geometryScene: { renderer: DoodleRenderer; actions: RenderAction[] };
    effectScene: ImageRenderer;
  }
}

test.use({ deviceScaleFactor: 2 });

for (const fitMode of ['contain', 'cover'] as const) {
  test(`canonical layout follows the image plane across viewports and zoom (${fitMode})`, async ({ canvas, page }) => {
    await canvas.mount({
      viewport: { width: 1200, height: 900 },
      html: '<div id="scene"><canvas></canvas><div id="stamps"></div><div id="texts"></div></div>',
      inlineStyles: [`
        #scene {position:relative;width:600px;height:400px;transform-origin:0 0}
        canvas {width:100%;height:100%}
        #stamps,#texts {position:absolute;inset:0}
        .canvas-stamp-annotation,.canvas-text-annotation {position:absolute}
        .canvas-stamp-annotation img {width:100%;height:100%}
        .canvas-text-annotation {color:transparent;white-space:pre}
      `],
    });
    await page.evaluate(async (fit) => {
      const { createDoodleRenderer } = await window.__importModule<RenderersModule>('/static/js/canvas-renderers.js');
      const bitmap = document.createElement('canvas');
      bitmap.width = 200;
      bitmap.height = 50;
      bitmap.getContext('2d')!.fillRect(0, 0, 200, 50);
      const actions: RenderAction[] = [
        { type: 'stamp', id: 'piece', url: bitmap.toDataURL(), x: .5, y: .5, size: 80, rotation: 90,
          canvas_width: 1000, canvas_height: 500, canvas_page_width: 2000, canvas_page_height: 1000 },
        { type: 'text', id: 'label', text: 'Hello', x: .1, y: .2, size: 32, canvas_font_size: 640,
          canvas_width: 1200, canvas_height: 736, canvas_page_width: 2000, canvas_page_height: 1000 },
        { type: 'draw', id: 'segment', stroke_id: 'stroke', x0: .1, y0: .2, x1: .8, y1: .7, size: 3, color: 'red',
          canvas_line_width: 30, canvas_page_width: 2000, canvas_page_height: 1000 },
      ];
      const renderer = createDoodleRenderer({
        canvas: document.querySelector('canvas')!, fitMode: fit,
        getPage: () => ({ width: 2000, height: 1000 }),
        stampLayer: document.getElementById('stamps')!, textLayer: document.getElementById('texts')!,
      });
      renderer.resize(actions);
      window.geometryScene = { renderer, actions };
    }, fitMode);

    for (const [width, height, zoom] of [[600, 400, 1], [400, 600, 1], [600, 400, 2], [800, 400, .5]]) {
      const layout = await page.evaluate(({ width, height, zoom }) => {
        const scene = document.getElementById('scene')!;
        const canvas = document.querySelector('canvas')!;
        const { renderer, actions } = window.geometryScene;
        scene.style.width = `${width}px`;
        scene.style.height = `${height}px`;
        scene.style.transform = `translate(30px,20px) scale(${zoom})`;
        renderer.resize(actions);
        const plane = renderer.imagePlane(actions[0]);
        const stamp = document.querySelector('.canvas-stamp-annotation')!.getBoundingClientRect();
        const label = document.querySelector<HTMLElement>('.canvas-text-annotation')!;
        const rect = canvas.getBoundingClientRect();
        return {
          plane,
          stamp: { width: stamp.width, height: stamp.height, cx: (stamp.left + stamp.right) / 2, cy: (stamp.top + stamp.bottom) / 2 },
          rect: { left: rect.left, top: rect.top },
          pointer: renderer.normalizedPoint({ clientX: rect.left + (plane.x + plane.width * .5) * zoom,
            clientY: rect.top + (plane.y + plane.height * .5) * zoom }),
          font: parseFloat(label.style.fontSize), labelWidth: parseFloat(label.style.width),
          hit: renderer.findSegmentAt((plane.x + plane.width * .1) * zoom, (plane.y + plane.height * .2) * zoom)?.stroke_id,
        };
      }, { width, height, zoom });
      const scale = (fitMode === 'cover' ? Math.max : Math.min)(width / 2000, height / 1000);
      const imageWidth = 2000 * scale;
      const imageHeight = 1000 * scale;
      expect(layout.plane.width).toBeCloseTo(imageWidth, 5);
      expect(layout.plane.height).toBeCloseTo(imageHeight, 5);
      expect(Math.abs(layout.stamp.width - imageHeight * .05 * zoom)).toBeLessThanOrEqual(.05);
      expect(Math.abs(layout.stamp.height - imageWidth * .1 * zoom)).toBeLessThanOrEqual(.05);
      expect(Math.abs(layout.stamp.cx - (layout.rect.left + width * zoom / 2))).toBeLessThanOrEqual(.05);
      expect(Math.abs(layout.stamp.cy - (layout.rect.top + height * zoom / 2))).toBeLessThanOrEqual(.05);
      expect(layout.font).toBeCloseTo(imageHeight * .064, 5);
      expect(layout.labelWidth).toBeCloseTo(imageWidth * .12, 5);
      expect(layout.pointer).toEqual({ x: .5, y: .5 });
      expect(layout.hit).toBe('stroke');
    }
  });

  for (const [sourceWidth, sourceHeight] of [[400, 400], [800, 200]]) {
    test(`image effects stay aligned during zoom and resize (${fitMode}, ${sourceWidth}x${sourceHeight})`, async ({ canvas, page }) => {
      await canvas.mount({
        html: '<div id="image-container"><canvas id="canvas-renderer"></canvas><img id="current-image"></div>',
        stylesheets: ['/static/css/canvas.css', '/static/css/image-effects.css', '/static/css/canvas-navigation.css'],
        inlineStyles: [`
          #image-container {position:relative;flex:none;width:600px;height:400px}
          #canvas-renderer {position:absolute;inset:0;width:100%;height:100%}
        `],
      });
      await page.evaluate(async ({ width, height, fitMode }) => {
        const { createImageRenderer } = await window.__importModule<RenderersModule>('/static/js/canvas-renderers.js');
        const image = document.getElementById('current-image') as HTMLImageElement;
        const renderer = createImageRenderer({
          canvas: document.getElementById('canvas-renderer') as HTMLCanvasElement, image, fitMode,
        });
        renderer.resize();
        const source = document.createElement('canvas');
        source.width = width;
        source.height = height;
        const context = source.getContext('2d')!;
        context.fillStyle = '#00ff00';
        context.fillRect(0, 0, width, height);
        context.fillStyle = '#ff0000';
        context.fillRect(0, 0, width / 2, height);
        await renderer.applyTransition(source.toDataURL(), 'none', 'sparkle');
        await image.decode();
        window.effectScene = renderer;
      }, { width: sourceWidth, height: sourceHeight, fitMode });

      for (const [scale, width] of [[1, 600], [.5, 600], [2, 600], [2, 500], [1, 500]]) {
        await page.evaluate(({ scale, width }) => {
          const container = document.getElementById('image-container')!;
          container.style.width = `${width}px`;
          container.style.setProperty('--canvas-view-scale', String(scale));
          container.style.setProperty('--canvas-view-x', '37px');
          container.style.setProperty('--canvas-view-y', '-23px');
          window.effectScene.resize();
        }, { scale, width });
        const canvasBox = await page.locator('#canvas-renderer').boundingBox();
        const effectBox = await page.locator('.canvas-image-effect-frame').boundingBox();
        expect(canvasBox).not.toBeNull();
        expect(effectBox).not.toBeNull();
        const fit = (fitMode === 'contain' ? Math.min : Math.max)(width / sourceWidth, 400 / sourceHeight);
        const drawnWidth = sourceWidth * fit;
        const drawnHeight = sourceHeight * fit;
        const expected = {
          x: canvasBox!.x + (width - drawnWidth) / 2 * scale,
          y: canvasBox!.y + (400 - drawnHeight) / 2 * scale,
          width: drawnWidth * scale, height: drawnHeight * scale,
        };
        for (const key of ['x', 'y', 'width', 'height'] as const) {
          expect(Math.abs(effectBox![key] - expected[key]), key).toBeLessThanOrEqual(.1);
        }
      }
    });
  }
}
