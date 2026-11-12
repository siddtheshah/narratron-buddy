import { expect, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';
import type { Locator, Page, WebSocketRoute } from '@playwright/test';
import type { TextAnnotation } from './text-annotations.js';

interface DoodleMessage {
  type: string;
  id?: string;
  x?: number;
  y?: number;
  text?: string;
  client_message_id?: string;
}

async function openTextCanvas(canvas: CanvasHarness): Promise<{
  sent: DoodleMessage[];
  replay(): void;
  clear(): void;
  setEditable(value: boolean): void;
}> {
  const sent: DoodleMessage[] = [];
  const sockets: WebSocketRoute[] = [];
  let editable = true;
  let annotations: TextAnnotation[] = [{
    type: 'text', id: 'clue', x: .1, y: .1, text: 'Original clue', color: '#ffffff', size: 32, font: 'Outfit',
  }];
  const snapshot = () => ({ type: 'doodle_snapshot', page_id: 'page-1', enabled: true,
    batches: [], stamps: [], annotations });
  const baton = () => ({ owner: { id: 1 }, active_orator: { id: editable ? 1 : 999 }, viewer_collab_enabled: false });
  await canvas.openCanvas({
    isOrator: true, collabEnabled: false, viewport: { width: 1400, height: 900 },
    apiRoutes: {
      '/api/latest': { viewer_collab_enabled: false, orator_cursor: 0,
        latest: '/static/narratron-avatar.png', time: 1,
        history: [{ url: '/static/narratron-avatar.png', page_id: 'page-1', time: 1 }] },
      '/api/doodles': route => route.fulfill({ json: snapshot() }),
      '/api/theaters/stage/baton': route => route.fulfill({ json: baton() }),
    },
    onWebSocket(ws) {
      if (!ws.url().includes('/ws/doodle')) return;
      sockets.push(ws);
      ws.send(JSON.stringify(snapshot()));
      ws.onMessage(raw => {
        const message = JSON.parse(String(raw)) as DoodleMessage;
        if (message.type === 'subscribe_doodles') {
          ws.send(JSON.stringify(snapshot()));
          return;
        }
        sent.push(message);
        if (message.type === 'text') {
          const action = message as TextAnnotation;
          annotations = [...annotations.filter(item => item.id !== action.id), action];
        }
        if (message.client_message_id) ws.send(JSON.stringify({ type: 'doodle_ack', client_message_id: message.client_message_id }));
      });
    },
  });
  await expect(canvas.page.locator('.canvas-text-annotation')).toHaveText('Original clue');
  return {
    sent,
    replay() { for (const ws of sockets) ws.send(JSON.stringify(snapshot())); },
    clear() { annotations = []; for (const ws of sockets) ws.send(JSON.stringify(snapshot())); },
    setEditable(value) {
      editable = value;
      for (const ws of sockets) ws.send(JSON.stringify({ type: 'baton_state', baton_state: baton(), active_viewers: [] }));
    },
  };
}

async function pixels(page: Page): Promise<string> {
  return page.locator('#doodle-canvas').evaluate((canvas: HTMLCanvasElement) => canvas.toDataURL());
}

async function expectLeftFraction(label: Locator, planeWidth: number, fraction: number): Promise<void> {
  await expect.poll(async () => label.evaluate(element => parseFloat(getComputedStyle(element).left)))
    .toBeCloseTo(planeWidth * fraction, 1);
}

async function startDrag(page: Page, label: Locator, dx: number, dy: number): Promise<void> {
  const bounds = (await label.boundingBox())!;
  await page.mouse.move(bounds.x + 15, bounds.y + 15);
  await page.mouse.down();
  await page.mouse.move(bounds.x + 15 + dx, bounds.y + 15 + dy, { steps: 5 });
}

test('persisted text supports selection, editing, Escape cancellation, replay, and permission revocation', async ({ canvas, page }) => {
  const server = await openTextCanvas(canvas);
  const label = page.locator('.canvas-text-annotation');
  const editor = page.getByLabel('Text annotation', { exact: true });
  await label.focus();
  expect(await label.evaluate(element => {
    const range = document.createRange();
    range.selectNodeContents(element);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    return selection.toString();
  })).toBe('Original clue');
  const before = await pixels(page);
  await label.dblclick();
  await expect(editor).toHaveValue('Original clue');
  await editor.fill('Updated clue');
  await editor.press('Enter');
  await expect(label).toHaveText('Updated clue');
  await expect.poll(() => server.sent.filter(message => message.type === 'text').length).toBe(1);
  expect(server.sent.find(message => message.type === 'text')?.id).toBe('clue');
  await expect(label).toHaveCount(1);
  expect(await pixels(page)).not.toBe(before);
  await label.focus();
  await label.press('Enter');
  await editor.fill('Cancelled');
  await editor.press('Escape');
  await expect(editor).toHaveCount(0);
  await expect(label).toHaveText('Updated clue');
  expect(server.sent.filter(message => message.type === 'text')).toHaveLength(1);
  server.replay();
  await expect(label).toHaveCount(1);
  await expect(label).toHaveText('Updated clue');
  server.setEditable(false);
  await expect(page.locator('#text-annotation-toggle')).toBeDisabled();
  await label.dblclick();
  await expect(editor).toHaveCount(0);
  server.clear();
  await expect(label).toHaveCount(0);
});

test('drag previews stay local, commit once, retain position through edits and replay, and renew message IDs', async ({ canvas, page }) => {
  const server = await openTextCanvas(canvas);
  const label = page.locator('.canvas-text-annotation');
  const editor = page.getByLabel('Text annotation', { exact: true });
  await label.click();
  await expect(label).toHaveClass(/movable/);
  await expect(editor).toHaveCount(0);
  const plane = (await page.locator('#doodle-canvas').boundingBox())!;
  const before = await pixels(page);
  await startDrag(page, label, plane.width * .2, plane.height * .2);
  expect(server.sent.filter(message => message.type === 'text')).toHaveLength(0);
  await page.mouse.up();
  await expect.poll(() => server.sent.filter(message => message.type === 'text').length).toBe(1);
  const messages = () => server.sent.filter(message => message.type === 'text');
  expect(messages()[0].id).toBe('clue');
  expect(messages()[0].x).toBeCloseTo(.3, 5);
  expect(messages()[0].y).toBeCloseTo(.3, 5);
  expect(await pixels(page)).not.toBe(before);
  server.replay();
  await expect(label).toHaveCount(1);
  await expectLeftFraction(label, plane.width, .3);
  await label.dblclick();
  await expect(editor).toHaveValue('Original clue');
  await editor.fill('Moved clue');
  await editor.press('Enter');
  await expect(label).toHaveText('Moved clue');
  await expect.poll(() => messages().length).toBe(2);
  expect(messages()[1].x).toBeCloseTo(.3, 5);
  expect(messages()[1].y).toBeCloseTo(.3, 5);
  await label.click();
  await startDrag(page, label, plane.width * .1, plane.height * .1);
  await page.mouse.up();
  await expect.poll(() => messages().length).toBe(3);
  expect(messages()[2].x).toBeCloseTo(.4, 5);
  expect(messages()[2].client_message_id).toBeTruthy();
  expect(messages()[2].client_message_id).not.toBe(messages()[1].client_message_id);
  await page.mouse.click(plane.x + plane.width * .85, plane.y + plane.height * .85);
  await expect(label).not.toHaveClass(/movable/);
});

test('pointer cancellation restores text position without sending and lost permissions prevent dragging', async ({ canvas, page }) => {
  const server = await openTextCanvas(canvas);
  const label = page.locator('.canvas-text-annotation');
  await label.click();
  const plane = (await page.locator('#doodle-canvas').boundingBox())!;
  await startDrag(page, label, plane.width * .2, plane.height * .2);
  await label.dispatchEvent('pointercancel', { pointerId: 1 });
  await page.mouse.up();
  await expectLeftFraction(label, plane.width, .1);
  expect(server.sent.filter(message => message.type === 'text')).toHaveLength(0);
  server.setEditable(false);
  await expect(page.locator('#text-annotation-toggle')).toBeDisabled();
  await label.click();
  await startDrag(page, label, plane.width * .2, plane.height * .2);
  await page.mouse.up();
  await expectLeftFraction(label, plane.width, .1);
  expect(server.sent.filter(message => message.type === 'text')).toHaveLength(0);
});
