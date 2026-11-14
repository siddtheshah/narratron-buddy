import { expect, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';
import type { Page } from '@playwright/test';

type PollingModule = typeof import('./state-polling.js');

interface PollingHarness {
  calls: number[];
  timers: Map<number, { callback: () => void; delay: number }>;
  advance(ms: number, runTimers?: boolean): void;
  event(name: string): void;
  hidden(value: boolean): void;
  focus(value: boolean): void;
}

declare global {
  interface Window {
    pollingHarness: PollingHarness;
  }
}

async function mount(
  canvas: CanvasHarness,
  page: Page,
  options: { hidden?: boolean; focused?: boolean } = {},
): Promise<void> {
  await canvas.mount();
  await page.evaluate(async ({ hidden = false, focused = true }) => {
    const timers: PollingHarness['timers'] = new Map();
    const calls: number[] = [];
    let now = 0;
    let nextId = 0;
    Date.now = () => now;
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
    document.hasFocus = () => focused;
    window.setInterval = ((callback: () => void, delay: number): number => {
      const id = ++nextId;
      timers.set(id, { callback, delay });
      return id;
    }) as typeof window.setInterval;
    window.clearInterval = (id) => {
      if (typeof id === 'number') timers.delete(id);
    };
    window.pollingHarness = {
      calls, timers,
      advance(ms, runTimers = true) {
        now += ms;
        if (runTimers) [...timers.values()].forEach(timer => timer.callback());
      },
      event(name) {
        (name === 'focus' ? window : document).dispatchEvent(new Event(name));
      },
      hidden(value) { hidden = value; },
      focus(value) { focused = value; },
    };
    const module = await window.__importModule<PollingModule>('/static/generated/shared/state-polling.js');
    module.startCanvasStatePolling(() => { calls.push(now); });
  }, options);
}

test('active tabs refresh every second without requiring interaction', async ({ canvas, page }) => {
  await mount(canvas, page);
  expect(await page.evaluate(() => {
    const h = window.pollingHarness;
    const delay = [...h.timers.values()][0].delay;
    h.advance(1000);
    h.advance(1000);
    return { calls: h.calls, delay };
  })).toEqual({ calls: [0, 1000, 2000], delay: 1000 });
});

test('hidden tabs pause and catch up when visible, even without focus', async ({ canvas, page }) => {
  await mount(canvas, page);
  expect(await page.evaluate(() => {
    const h = window.pollingHarness;
    h.hidden(true);
    h.event('visibilitychange');
    const paused = h.timers.size;
    h.advance(5000);
    h.event('mousemove');
    const hiddenCalls = [...h.calls];
    h.hidden(false);
    h.focus(false);
    h.event('visibilitychange');
    const resumed = h.timers.size;
    h.advance(1000);
    h.focus(true);
    h.event('focus');
    return { paused, hiddenCalls, resumed, calls: h.calls };
  })).toEqual({ paused: 0, hiddenCalls: [0], resumed: 1, calls: [0, 5000, 6000] });
});

test('visible windows poll every second while another window has focus', async ({ canvas, page }) => {
  await mount(canvas, page, { focused: false });
  expect(await page.evaluate(() => {
    const h = window.pollingHarness;
    h.advance(1000);
    h.advance(1000);
    return { calls: h.calls, timers: h.timers.size };
  })).toEqual({ calls: [0, 1000, 2000], timers: 1 });
});

test('mouse movement wakes suspended polling without flooding requests', async ({ canvas, page }) => {
  await mount(canvas, page);
  expect(await page.evaluate(() => {
    const h = window.pollingHarness;
    h.advance(5000, false);
    for (let i = 0; i < 100; i++) h.event('mousemove');
    h.event('click');
    h.event('keydown');
    h.event('focus');
    h.advance(1000);
    return { calls: h.calls, timers: h.timers.size };
  })).toEqual({ calls: [0, 5000, 6000], timers: 1 });
});

test('initially inactive tabs start polling when selected', async ({ canvas, page }) => {
  await mount(canvas, page, { hidden: true, focused: false });
  expect(await page.evaluate(() => {
    const h = window.pollingHarness;
    const initial = { calls: [...h.calls], timers: h.timers.size };
    h.hidden(false);
    h.focus(true);
    h.event('visibilitychange');
    return { initial, calls: h.calls, timers: h.timers.size };
  })).toEqual({ initial: { calls: [], timers: 0 }, calls: [0], timers: 1 });
});

test('polling stops after 30 minutes of inactivity despite periodic refreshes', async ({ canvas, page }) => {
  await mount(canvas, page);
  expect(await page.evaluate(() => {
    const h = window.pollingHarness;
    for (let second = 1; second < 30 * 60; second++) h.advance(1000);
    const before = h.calls.length;
    h.advance(1000);
    h.advance(60000);
    return { before, after: h.calls.length, timers: h.timers.size };
  })).toEqual({ before: 1800, after: 1800, timers: 0 });
});

for (const event of ['mousemove', 'click', 'keydown', 'wheel', 'touchstart']) {
  test(`${event} resets inactivity and wakes paused polling`, async ({ canvas, page }) => {
    await mount(canvas, page);
    expect(await page.evaluate((eventName) => {
      const h = window.pollingHarness;
      h.advance(29 * 60 * 1000);
      h.event(eventName);
      h.advance(29 * 60 * 1000);
      const active = h.timers.size;
      h.advance(60 * 1000);
      const stopped = h.timers.size;
      const count = h.calls.length;
      h.event(eventName);
      const woken = { calls: h.calls.length - count, timers: h.timers.size };
      h.advance(1000);
      return { active, stopped, woken, subsequentCalls: h.calls.length - count };
    }, event)).toEqual({ active: 1, stopped: 0, woken: { calls: 1, timers: 1 }, subsequentCalls: 2 });
  });
}

test('returning to an idle tab restarts polling', async ({ canvas, page }) => {
  await mount(canvas, page);
  expect(await page.evaluate(() => {
    const h = window.pollingHarness;
    h.hidden(true);
    h.event('visibilitychange');
    h.advance(31 * 60 * 1000);
    h.event('mousemove');
    const hiddenTimers = h.timers.size;
    h.hidden(false);
    h.event('visibilitychange');
    return { hiddenTimers, timers: h.timers.size, calls: h.calls };
  })).toEqual({ hiddenTimers: 0, timers: 1, calls: [0, 31 * 60 * 1000] });
});
