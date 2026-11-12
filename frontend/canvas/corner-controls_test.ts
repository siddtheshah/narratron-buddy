import fs from 'node:fs';
import path from 'node:path';
import { expect, TEMPLATES_DIR, test } from '../../testing/ui/canvas_fixtures';

function loadCornerControlsHtml(): string {
  const template = fs.readFileSync(path.join(TEMPLATES_DIR, 'canvas.html'), 'utf-8');
  const start = template.indexOf('            <div class="canvas-corner-controls');
  const end = template.indexOf('<!-- Image History Paging Controls -->', start);
  return `<div id="image-container">${template.slice(start, end)}</div>`;
}

const CORNER_CONTROLS_HTML = loadCornerControlsHtml();
const CORNERS = ['top-left', 'bottom-left', 'bottom-right'] as const;

for (const corner of CORNERS) {
  test(`visible ${corner} corner controls pack against edges`, async ({ canvas, page }) => {
    await canvas.mount({
      viewport: { width: 1200, height: 800 },
      stylesheets: [
        '/static/css/canvas-navigation.css',
        '/static/css/canvas-bookmarks.css',
        '/static/css/game-renderer.css',
        '/static/css/canvas.css',
      ],
      inlineStyles: [
        `
          #image-container { position: relative; width: 1000px; height: 600px; flex: none; border: 0; }
          * { transition: none !important; }
        `,
      ],
      html: CORNER_CONTROLS_HTML,
    });

    await page.locator('#canvas-bookmarks').evaluate((control: HTMLElement) => {
      control.innerHTML = '<button id="canvas-bookmark-toggle" aria-label="Bookmarks">1</button>';
    });
    await page.mouse.move(1190, 790);

    const group = page.locator(`.canvas-corner-${corner}`);

    // Exercise every visibility combination, including role changes and hidden game/prompt controls.
    for (let mask = 1; mask < 8; mask++) {
      await group.evaluate((groupEl: HTMLElement, activeMask: number) => {
        Array.from(groupEl.children).forEach((child, index) => {
          const control = child as HTMLElement;
          control.hidden = false;
          control.style.display = activeMask & (1 << index) ? 'flex' : 'none';
        });
      }, mask);

      const boxes = await group
        .locator(':scope > :visible')
        .evaluateAll((controls: Element[]) =>
          controls.map((control) => {
            const rect = control.getBoundingClientRect();
            return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
          }),
        );
      const containerBox = (await page.locator('#image-container').boundingBox())!;

      if (corner.endsWith('left')) {
        expect(boxes[0].left).toBeCloseTo(containerBox.x + 24, 2);
      } else {
        expect(boxes[boxes.length - 1].right).toBeCloseTo(
          containerBox.x + containerBox.width - 24,
          2,
        );
      }
      for (let i = 1; i < boxes.length; i++) {
        expect(boxes[i].left - boxes[i - 1].right).toBeCloseTo(8, 2);
      }
      const edge = corner.startsWith('top') ? 'top' : 'bottom';
      const expectedEdge =
        edge === 'top' ? containerBox.y + 24 : containerBox.y + containerBox.height - 24;
      for (const box of boxes) {
        expect(box[edge]).toBeCloseTo(expectedEdge, 2);
      }
    }

    // Expanded hover/click panels cover up subsequent controls without pushing them horizontally.
    const collapsedBoxes = await group
      .locator(':scope > :visible')
      .evaluateAll((controls: Element[]) =>
        controls.map((control) => {
          const rect = control.getBoundingClientRect();
          return { left: rect.left, right: rect.right, bottom: rect.bottom };
        }),
      );

    for (const controlId of ['prompt-toggle-btn', 'suggestions-toggle-btn', 'sticky-notes-toggle-btn']) {
      const control = group.locator(`#${controlId}`);
      if ((await control.count()) === 0) continue;
      await control.evaluate((el: HTMLElement) => el.classList.add('expanded'));
      const boxes = await group
        .locator(':scope > :visible')
        .evaluateAll((controls: Element[]) =>
          controls.map((item) => {
            const rect = item.getBoundingClientRect();
            return { left: rect.left, right: rect.right, bottom: rect.bottom };
          }),
        );
      const controlBox = await control.evaluate((el: HTMLElement) => {
        const rect = el.getBoundingClientRect();
        return { left: rect.left, right: rect.right };
      });
      for (let i = 0; i < collapsedBoxes.length; i++) {
        expect(boxes[i].left).toBeCloseTo(collapsedBoxes[i].left, 2);
        if (boxes[i].left > controlBox.left) {
          expect(controlBox.right).toBeGreaterThan(boxes[i].left);
        }
      }
      await control.evaluate((el: HTMLElement) => el.classList.remove('expanded'));
    }
  });
}
