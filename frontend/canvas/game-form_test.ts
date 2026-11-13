import { expect, test } from '../../testing/ui/canvas_fixtures';
import {
  loadGameSnapshots,
  parseSubmittedAction,
  type SubmittedGameAction,
} from '../../testing/ui/game_fixtures';
import type { GamePayload, GameRenderer } from './game-renderer.js';

type GameRendererModule = typeof import('./game-renderer.js');

interface FormTestWindow extends Window {
  editable: boolean;
  renderer: GameRenderer;
  compromised?: boolean;
}

const RENDERER_MODULE_URL = '/static/generated/canvas/game-renderer.js';
const snapshots = loadGameSnapshots();

test('form text, numbers, bounds, XSS safety, and permissions', async ({ canvas, page }) => {
  const submissions: SubmittedGameAction[] = [];

  await canvas.serveRoute(
    (url) =>
      url.origin === canvas.origin && (url.pathname === '/latest' || url.pathname === '/action'),
    async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/latest') {
        await route.fulfill({ json: snapshots.form_0 });
        return;
      }
      const action = parseSubmittedAction(route.request().postData());
      submissions.push(action);
      await route.fulfill({ json: snapshots.form_1 });
    },
  );

  await canvas.mount({
    html: '<button id="toggle"></button><div id="root"></div>',
  });

  await page.evaluate(
    async ({ moduleUrl }) => {
      const { createGameRenderer } = await window.__importModule<GameRendererModule>(moduleUrl);
      const win = window as unknown as FormTestWindow;
      win.editable = true;
      win.renderer = createGameRenderer({
        container: document.querySelector('#root') as HTMLElement,
        toggleButton: document.querySelector('#toggle') as HTMLElement,
        actionUrl: () => '/action',
        canEdit: () => win.editable,
        initiallyOpen: true,
        refresh: () => {},
      });
      const response = await fetch('/latest');
      const payload = (await response.json()) as GamePayload;
      win.renderer.render(payload);
    },
    { moduleUrl: RENDERER_MODULE_URL },
  );

  const text = page.getByLabel('Text', { exact: true });
  await expect(text).toHaveAttribute('maxlength', '1000');
  expect(await page.locator('label.game-input-field', { has: text }).count()).toBe(0);

  await page.getByRole('button', { name: 'Save', exact: true }).click();
  expect(submissions).toEqual([]);

  const entered = '<img src=x onerror="window.compromised=true">';
  await text.fill(entered);
  await page.getByLabel('Number', { exact: true }).fill('-2');
  await text.press('Enter');

  await expect(page.getByText(entered, { exact: true })).toBeVisible();
  expect(submissions).toHaveLength(1);
  expect(submissions[0].arguments).toEqual({ text: entered, number: -2 });
  expect(await page.locator('#root img').count()).toBe(0);
  expect(await page.evaluate(() => (window as unknown as FormTestWindow).compromised === undefined)).toBe(true);

  await text.fill('Draft text');
  await page.getByLabel('Number', { exact: true }).fill('5');
  await page.getByRole('button', { name: 'Clear text', exact: true }).click();
  await expect(text).toHaveValue('');
  await expect(page.getByLabel('Number', { exact: true })).toHaveValue('5');
  expect(submissions).toHaveLength(1);

  await text.fill('x'.repeat(1001));
  await expect(text).toHaveValue('x'.repeat(1000));
  await text.evaluate((input: HTMLInputElement) => {
    input.value = 'x'.repeat(1001);
  });
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  expect(submissions).toHaveLength(1);

  await page.evaluate(() => {
    const win = window as unknown as FormTestWindow;
    win.editable = false;
    win.renderer.render();
  });
  await expect(text).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Clear text', exact: true })).toBeDisabled();
});
