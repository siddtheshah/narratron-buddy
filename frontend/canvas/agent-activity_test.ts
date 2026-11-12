import fs from 'node:fs';
import path from 'node:path';
import type { Page } from '@playwright/test';
import { expect, TEMPLATES_DIR, test } from '../../testing/ui/canvas_fixtures';

type AgentActivityModule = typeof import('/static/generated/canvas/agent-activity.js');
type AgentActivityController =
  import('/static/generated/canvas/agent-activity.js').AgentActivityController;

interface ActivityHarnessWindow extends Window {
  agentActivity: AgentActivityController;
  reconnectClicks: number;
  confirmTools?: () => void;
  confirmationTimer?: ReturnType<typeof setInterval>;
}

const ACTIVITY_MODULE_URL = '/static/generated/canvas/agent-activity.js';

function loadActivityTrayHtml(): string {
  const template = fs.readFileSync(path.join(TEMPLATES_DIR, 'canvas.html'), 'utf-8');
  const start = template.indexOf('            <div id="agent-activity-tray"');
  const endMarker = template.indexOf('id="agent-disconnected-indicator"', start);
  const iconEnd = template.indexOf('</div>', endMarker) + '</div>'.length;
  return `${template.slice(start, iconEnd)}</div>`;
}

const ACTIVITY_TRAY_HTML = loadActivityTrayHtml();

async function initActivityController(page: Page): Promise<void> {
  await page.evaluate(async (moduleUrl: string) => {
    const { createAgentActivityController } =
      await window.__importModule<AgentActivityModule>(moduleUrl);
    const win = window as unknown as ActivityHarnessWindow;
    win.reconnectClicks = 0;
    win.agentActivity = createAgentActivityController({
      pcmBroadcastIndicator: document.getElementById('pcm-broadcast-indicator'),
      drawingIndicator: document.getElementById('agent-drawing-indicator'),
      writingIndicator: document.getElementById('agent-writing-indicator'),
      musicIndicator: document.getElementById('agent-music-indicator'),
      diceIndicator: document.getElementById('agent-dice-indicator'),
      referenceIndicator: document.getElementById('agent-reference-indicator'),
      disconnectedIndicator: document.getElementById('agent-disconnected-indicator'),
      onReconnect: () => {
        win.reconnectClicks += 1;
      },
    });
  }, ACTIVITY_MODULE_URL);
}

test('long-running tool indicators keep their animation timelines across refreshes', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    stylesheets: ['/static/css/canvas.css'],
    html: ACTIVITY_TRAY_HTML,
  });
  await initActivityController(page);

  const result = await page.evaluate(async () => {
    const win = window as unknown as ActivityHarnessWindow;
    const activity = {
      image_generating: true,
      animation_generating: true,
      user_action_processing: true,
      music_generating: true,
      reference_generating: true,
      dice_rolling: true,
      dice_result: { tier: 'high', notation: '1d20', total: 20 },
    };
    win.agentActivity.update(activity);
    const icons = [...document.querySelectorAll('.agent-activity-icon.active')];
    const animations = icons.map((icon) => icon.getAnimations());
    await Promise.all(animations.flat().map((animation) => animation.ready));
    // Seek past many animation cycles without real-time waits.
    for (const animation of animations.flat()) {
      animation.currentTime = 120250;
    }
    const mutations: MutationRecord[] = [];
    const observer = new MutationObserver((records) => mutations.push(...records));
    for (const icon of icons) {
      observer.observe(icon, { attributes: true, attributeFilter: ['class', 'aria-hidden'] });
    }
    for (let refresh = 0; refresh < 10; refresh++) {
      win.agentActivity.update(activity);
      await new Promise<number>((resolve) => requestAnimationFrame(resolve));
    }
    observer.disconnect();
    return {
      count: icons.length,
      mutations: mutations.length,
      stable: icons.every((icon, index) => {
        const current = icon.getAnimations();
        return (
          current.length > 0 &&
          current.length === animations[index].length &&
          current.every(
            (animation, slot) =>
              animation === animations[index][slot] &&
              animation.playState === 'running' &&
              Number(animation.currentTime ?? 0) >= 120250 &&
              animation.effect?.getTiming().iterations === Infinity,
          )
        );
      }),
    };
  });

  expect(result).toEqual({ count: 5, mutations: 0, stable: true });
});

test('partial refreshes keep book and music active until explicit completion', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    stylesheets: ['/static/css/canvas.css'],
    html: ACTIVITY_TRAY_HTML,
  });
  await initActivityController(page);

  await page.evaluate(() => {
    const win = window as unknown as ActivityHarnessWindow;
    win.agentActivity.update({ user_action_processing: true, music_generating: true });
    win.agentActivity.update();
    win.agentActivity.update(null);
    win.agentActivity.update({ reference_generating: true });
  });

  const book = page.locator('#agent-writing-indicator');
  const music = page.locator('#agent-music-indicator');
  await expect(book).toBeVisible();
  await expect(music).toBeVisible();
  await expect(book).toHaveAttribute('aria-hidden', 'false');

  await page.evaluate(() => {
    (window as unknown as ActivityHarnessWindow).agentActivity.update({
      user_action_processing: false,
    });
  });
  await expect(book).toBeHidden();
  await expect(music).toBeVisible();

  await page.evaluate(() => {
    (window as unknown as ActivityHarnessWindow).agentActivity.update({
      music_generating: false,
    });
  });
  await expect(music).toBeHidden();
  await expect(music).toHaveAttribute('aria-hidden', 'true');
});

test('drawing stays active until both visual tools complete', async ({ canvas, page }) => {
  await canvas.mount({
    stylesheets: ['/static/css/canvas.css'],
    html: ACTIVITY_TRAY_HTML,
  });
  await initActivityController(page);

  const drawing = page.locator('#agent-drawing-indicator');
  await page.evaluate(() => {
    (window as unknown as ActivityHarnessWindow).agentActivity.update({
      image_generating: true,
      animation_generating: true,
    });
    (window as unknown as ActivityHarnessWindow).agentActivity.update({
      image_generating: false,
    });
  });

  await expect(drawing).toBeVisible();
  await expect(drawing).toHaveAttribute('title', 'Agent is generating an animation');

  await page.evaluate(() => {
    (window as unknown as ActivityHarnessWindow).agentActivity.update({
      animation_generating: false,
    });
  });
  await expect(drawing).toBeHidden();
});

test('unconfirmed indicators expire and can recover', async ({ canvas, page }) => {
  await page.clock.install();
  await canvas.mount({
    stylesheets: ['/static/css/canvas.css'],
    html: ACTIVITY_TRAY_HTML,
  });
  await initActivityController(page);

  await page.evaluate(() => {
    (window as unknown as ActivityHarnessWindow).agentActivity.update({
      user_action_processing: true,
      music_generating: true,
    });
  });
  await page.clock.runFor(29000);

  const book = page.locator('#agent-writing-indicator');
  const music = page.locator('#agent-music-indicator');
  await expect(book).toBeVisible();
  await expect(music).toBeVisible();

  // Confirming music or the live connection must not renew the book's lease.
  await page.evaluate(() => {
    (window as unknown as ActivityHarnessWindow).agentActivity.update({
      music_generating: true,
      live_ready: true,
    });
  });
  await page.clock.runFor(1000);
  await expect(book).toBeHidden();
  await expect(music).toBeVisible();

  await page.clock.runFor(29000);
  await expect(music).toBeHidden();

  await page.evaluate(() => {
    (window as unknown as ActivityHarnessWindow).agentActivity.update({
      user_action_processing: true,
    });
  });
  await expect(book).toBeVisible();
});

test('confirmed activity loops until hard timeout without reappearing', async ({ canvas, page }) => {
  await page.clock.install();
  await canvas.mount({
    stylesheets: ['/static/css/canvas.css'],
    html: ACTIVITY_TRAY_HTML,
  });
  await initActivityController(page);

  await page.evaluate(() => {
    const win = window as unknown as ActivityHarnessWindow;
    win.confirmTools = () =>
      win.agentActivity.update({ user_action_processing: true, music_generating: true });
    win.confirmTools();
    win.confirmationTimer = setInterval(win.confirmTools, 10000);
  });

  const book = page.locator('#agent-writing-indicator');
  const music = page.locator('#agent-music-indicator');
  await page.clock.runFor(599000);
  await expect(book).toBeVisible();
  await expect(music).toBeVisible();

  await page.clock.runFor(1000);
  await expect(book).toBeHidden();
  await expect(music).toBeHidden();

  await page.clock.runFor(60000);
  await expect(book).toBeHidden();
  await expect(music).toBeHidden();

  await page.evaluate(() => {
    const win = window as unknown as ActivityHarnessWindow;
    if (win.confirmationTimer !== undefined) {
      clearInterval(win.confirmationTimer);
    }
    win.agentActivity.update({ user_action_processing: false, music_generating: false });
    win.confirmTools?.();
  });
  await expect(book).toBeVisible();
  await expect(music).toBeVisible();
});

test('disconnected indicator toggles visibility and triggers reconnect callback', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    stylesheets: ['/static/css/canvas.css'],
    html: ACTIVITY_TRAY_HTML,
  });
  await initActivityController(page);

  const disconnected = page.locator('#agent-disconnected-indicator');
  await expect(disconnected).toBeHidden();

  await page.evaluate(() => {
    (window as unknown as ActivityHarnessWindow).agentActivity.setDisconnected(true);
  });
  await expect(disconnected).toBeVisible();
  await expect(disconnected).toHaveAttribute('aria-hidden', 'false');

  await disconnected.click({ force: true });
  expect(await page.evaluate(() => (window as unknown as ActivityHarnessWindow).reconnectClicks)).toBe(1);

  await page.evaluate(() => {
    (window as unknown as ActivityHarnessWindow).agentActivity.setDisconnected(false);
  });
  await expect(disconnected).toBeHidden();
  await expect(disconnected).toHaveAttribute('aria-hidden', 'true');
});
