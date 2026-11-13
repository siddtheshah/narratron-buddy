import type { Page, Route } from '@playwright/test';
import { expect, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';

type StoryLogModule = typeof import('/static/generated/canvas/story-log-inspector.js');
type StoryLogInspectorController = import('/static/generated/canvas/story-log-inspector.js').StoryLogInspectorController;

interface FetchCallRecord {
  url: string;
  cache: RequestCache | undefined;
}

interface StoryLogHarnessWindow extends Window {
  storyLogFetchCalls: FetchCallRecord[];
  storyLogHidden: boolean;
  storyLogController?: StoryLogInspectorController;
}

const CANONICAL_URL = '/static/generated/canvas/story-log-inspector.js';
const SHIM_URL = '/static/js/story-log-inspector.js';

const INSPECTOR_HTML = `
  <div id="history-paging-controls">
    <button id="story-log-toggle-btn" aria-expanded="false">Log</button>
    <section id="story-log-inspector" role="dialog" aria-label="Story log">
      <button id="story-log-close-btn" aria-label="Close Story Log">Close</button>
      <div id="story-log-content">Click Log button to view the story log.</div>
    </section>
  </div>
  <button id="outside-btn">Outside</button>
`;

const EMBEDDED_HTML = `
  <div id="remote-story-log" role="log" aria-label="Story log">Loading story log…</div>
`;

function jsonl(entries: ReadonlyArray<Record<string, unknown>>): string {
  return `${entries.map((entry) => JSON.stringify(entry)).join('\n')}\n`;
}

async function mountInspector(
  canvas: CanvasHarness,
  page: Page,
  options: {
    moduleUrl?: string;
    theaterId?: string;
    html?: string;
    inlineStyles?: string[];
    embedded?: boolean;
    hidden?: boolean;
  } = {},
): Promise<void> {
  const {
    moduleUrl = CANONICAL_URL,
    theaterId = 'test theater',
    html = INSPECTOR_HTML,
    inlineStyles = [],
    embedded = false,
    hidden = false,
  } = options;

  await canvas.mount({ html, inlineStyles });
  await page.evaluate(
    async ({
      url,
      id,
      isEmbedded,
      initiallyHidden,
    }: {
      url: string;
      id: string;
      isEmbedded: boolean;
      initiallyHidden: boolean;
    }) => {
      const win = window as unknown as StoryLogHarnessWindow;
      win.storyLogFetchCalls = [];
      win.storyLogHidden = initiallyHidden;
      Object.defineProperty(document, 'hidden', {
        configurable: true,
        get: () => win.storyLogHidden,
      });

      const originalFetch = window.fetch.bind(window);
      window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        win.storyLogFetchCalls.push({
          url: typeof input === 'string' ? input : input instanceof URL ? input.href : input.url,
          cache: init?.cache,
        });
        return originalFetch(input, init);
      };

      const mod = await window.__importModule<StoryLogModule>(url);
      win.storyLogController = mod.initializeStoryLogInspector(id, { embedded: isEmbedded });
    },
    { url: moduleUrl, id: theaterId, isEmbedded: embedded, initiallyHidden: hidden },
  );
}

test('open inspector fetches appended entries without reopening and bypasses cached output', async ({
  canvas,
  page,
}) => {
  await page.clock.install();
  let responseBody = jsonl([{ type: 'user_action', action: 'First' }]);
  let serverRequestCount = 0;

  await page.route('**/theaters/test%20theater/output/story_log.jsonl', async (route: Route) => {
    serverRequestCount += 1;
    await route.fulfill({ status: 200, contentType: 'text/plain; charset=utf-8', body: responseBody });
  });

  await mountInspector(canvas, page);

  const initialCalls = await page.evaluate(
    () => (window as unknown as StoryLogHarnessWindow).storyLogFetchCalls.length,
  );
  expect(initialCalls).toBe(0);

  await page.locator('#story-log-toggle-btn').click();
  const content = page.locator('#story-log-content');
  await expect(content.locator('.story-log-turn')).toHaveCount(1);
  await expect(content).toContainText('First');

  const fetchCalls = await page.evaluate(
    () => (window as unknown as StoryLogHarnessWindow).storyLogFetchCalls,
  );
  expect(fetchCalls).toHaveLength(1);
  expect(fetchCalls[0]).toEqual({
    url: '/theaters/test%20theater/output/story_log.jsonl',
    cache: 'no-store',
  });

  // Mark the rendered turn node to verify unchanged polls do not re-render DOM.
  await content.locator('.story-log-turn').first().evaluate((el) => {
    el.setAttribute('data-render-marker', 'original');
  });

  // Refresh interval is 2000ms: advancing 1999ms does not poll; 1ms more triggers poll #2.
  await page.clock.runFor(1999);
  expect(serverRequestCount).toBe(1);
  await page.clock.runFor(1);
  await expect.poll(() => serverRequestCount).toBe(2);
  await page.evaluate(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  await expect(content.locator('.story-log-turn').first()).toHaveAttribute(
    'data-render-marker',
    'original',
  );

  responseBody = jsonl([
    { type: 'user_action', action: 'First' },
    { type: 'user_action', action: 'Second' },
  ]);
  await page.clock.runFor(2000);
  await expect(content.locator('.story-log-turn')).toHaveCount(2);
  await expect(content.locator('.story-log-turn').nth(1).locator('.story-log-text')).toHaveText('Second');
});

test('refresh pauses when closed or hidden and catches up on visibility and reopening', async ({
  canvas,
  page,
}) => {
  await page.clock.install();
  let serverRequestCount = 0;

  await page.route('**/theaters/test%20theater/output/story_log.jsonl', async (route: Route) => {
    serverRequestCount += 1;
    await route.fulfill({
      status: 200,
      contentType: 'text/plain; charset=utf-8',
      body: jsonl([{ type: 'user_action', action: `Entry ${serverRequestCount}` }]),
    });
  });

  await mountInspector(canvas, page);
  const toggleBtn = page.locator('#story-log-toggle-btn');
  const closeBtn = page.locator('#story-log-close-btn');
  const content = page.locator('#story-log-content');

  await toggleBtn.click();
  await expect(content).toContainText('Entry 1');
  expect(serverRequestCount).toBe(1);

  await page.evaluate(() => {
    (window as unknown as StoryLogHarnessWindow).storyLogHidden = true;
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.clock.runFor(6000);
  expect(serverRequestCount).toBe(1);

  await page.evaluate(() => {
    (window as unknown as StoryLogHarnessWindow).storyLogHidden = false;
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(content).toContainText('Entry 2');
  expect(serverRequestCount).toBe(2);

  await closeBtn.click();
  await expect(toggleBtn).toHaveAttribute('aria-expanded', 'false');
  await page.clock.runFor(6000);
  expect(serverRequestCount).toBe(2);

  await toggleBtn.click();
  await expect(content).toContainText('Entry 3');
  expect(serverRequestCount).toBe(3);
});

test('missing logs and transient errors are retried, preserving already rendered entries', async ({
  canvas,
  page,
}) => {
  await page.clock.install();
  let nextReply: { status: number; body: string } = { status: 404, body: '' };

  await page.route('**/theaters/test%20theater/output/story_log.jsonl', async (route: Route) => {
    await route.fulfill({
      status: nextReply.status,
      contentType: 'text/plain; charset=utf-8',
      body: nextReply.body,
    });
  });

  await mountInspector(canvas, page);
  const content = page.locator('#story-log-content');

  await page.locator('#story-log-toggle-btn').click();
  await expect(content).toHaveText('No story actions have been recorded yet.');

  nextReply = { status: 200, body: jsonl([{ type: 'user_action', action: 'New action' }]) };
  await page.clock.runFor(2000);
  await expect(content.locator('.story-log-turn')).toHaveCount(1);
  await expect(content).toContainText('New action');

  nextReply = { status: 503, body: 'Service Unavailable' };
  await page.clock.runFor(2000);
  await page.evaluate(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  await expect(content.locator('.story-log-turn')).toHaveCount(1);
  await expect(content).toContainText('New action');

  nextReply = { status: 200, body: jsonl([{ type: 'user_action', action: 'Recovered' }]) };
  await page.clock.runFor(2000);
  await expect(content.locator('.story-log-turn')).toHaveCount(1);
  await expect(content.locator('.story-log-text')).toHaveText('Recovered');
});

test('initial load error displays fallback message until a subsequent retry succeeds', async ({
  canvas,
  page,
}) => {
  await page.clock.install();
  let status = 503;
  let body = 'Down';

  await page.route('**/theaters/test%20theater/output/story_log.jsonl', async (route: Route) => {
    await route.fulfill({ status, contentType: 'text/plain; charset=utf-8', body });
  });

  await mountInspector(canvas, page);
  const content = page.locator('#story-log-content');

  await page.locator('#story-log-toggle-btn').click();
  await expect(content).toHaveText('The story log could not be loaded.');

  status = 200;
  body = jsonl([{ type: 'user_action', action: 'Back online' }]);
  await page.clock.runFor(2000);
  await expect(content).toContainText('Back online');
});

test('slow requests never overlap', async ({ canvas, page }) => {
  await page.clock.install();
  let requestCount = 0;
  let releaseFirstFetch: (() => void) | null = null;

  await page.route('**/theaters/test%20theater/output/story_log.jsonl', async (route: Route) => {
    requestCount += 1;
    if (requestCount === 1) {
      await new Promise<void>((resolve) => {
        releaseFirstFetch = resolve;
      });
    }
    await route.fulfill({
      status: 200,
      contentType: 'text/plain; charset=utf-8',
      body: jsonl([{ type: 'user_action', action: `Request ${requestCount}` }]),
    });
  });

  await mountInspector(canvas, page);
  await page.locator('#story-log-toggle-btn').click();
  await expect.poll(() => requestCount).toBe(1);

  await page.clock.runFor(2000);
  await page.clock.runFor(2000);
  expect(requestCount).toBe(1);

  if (releaseFirstFetch) {
    (releaseFirstFetch as () => void)();
  }
  await expect(page.locator('#story-log-content')).toContainText('Request 1');

  await page.clock.runFor(2000);
  await expect.poll(() => requestCount).toBe(2);
});

test('renders chronological turns, die rolls, dialogue, and XSS-safe text', async ({
  canvas,
  page,
}) => {
  await page.route('**/theaters/test%20theater/output/story_log.jsonl', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/plain; charset=utf-8',
      body: jsonl([
        {
          type: 'user_action',
          timestamp: '2026-10-10T14:30:00Z',
          action: 'Inspect the altar',
        },
        {
          type: 'story_plan',
          timestamp: '2026-10-10T14:30:05Z',
          output: {
            die_rolls: [{ notation: '1d20+4', total: 19, tier: 'success', reason: 'Perception' }],
            narration: 'Dust settles. <script>window.__xss = true;</script>',
            dialogue: [{ speaker: 'Oracle', text: 'Look closer.' }],
          },
        },
        {
          type: 'die_roll',
          timestamp: 'invalid-date',
          result: { notation: '2d6', total: 8 },
        },
        {
          type: 'story_plan',
          output: {},
        },
      ]),
    });
  });

  await mountInspector(canvas, page);
  await page.locator('#story-log-toggle-btn').click();

  const turns = page.locator('#story-log-content .story-log-turn');
  await expect(turns).toHaveCount(2);

  await expect(turns.nth(0)).toContainText('Player action');
  await expect(turns.nth(0)).toContainText('Inspect the altar');
  await expect(turns.nth(0)).toContainText('Die Rolls');
  await expect(turns.nth(0)).toContainText('🎲 1d20+4: 19 (success) - Perception');
  await expect(turns.nth(0)).toContainText("Dust settles. <script>window.__xss = true;</script>");
  await expect(turns.nth(0)).toContainText('Oracle: Look closer.');
  expect(await page.locator('#story-log-content script').count()).toBe(0);

  await expect(turns.nth(1)).toContainText('Die roll');
  await expect(turns.nth(1)).toContainText('Unknown time');
  await expect(turns.nth(1)).toContainText('🎲 2d6: 8');
});

test('keeps the latest action visible when a long scene response overflows', async ({
  canvas,
  page,
}) => {
  await page.route('**/theaters/test%20theater/output/story_log.jsonl', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/plain; charset=utf-8',
      body: jsonl([
        { type: 'user_action', action: 'Earlier action' },
        { type: 'story_plan', output: { narration: 'Earlier scene '.repeat(40) } },
        { type: 'user_action', action: 'Latest player action' },
        { type: 'story_plan', output: { narration: 'Very long scene response. '.repeat(120) } },
      ]),
    });
  });

  await mountInspector(canvas, page, {
    inlineStyles: [
      `#story-log-inspector {
        display: none;
        width: 320px;
        height: 220px;
        overflow-y: auto;
        border: 1px solid #444;
      }
      #story-log-inspector.open { display: block; }
      .story-log-entry { padding: 8px; margin-bottom: 8px; }`,
    ],
  });

  await page.locator('#story-log-toggle-btn').click();
  await expect(page.locator('#story-log-content')).toContainText('Latest player action');

  await page.waitForFunction(() => {
    const inspector = document.getElementById('story-log-inspector');
    const actions = document.querySelectorAll('.story-log-entry.action');
    const latest = actions[actions.length - 1];
    if (!inspector || !latest) return false;
    const iBox = inspector.getBoundingClientRect();
    const aBox = latest.getBoundingClientRect();
    return aBox.top >= iBox.top - 1 && aBox.bottom <= iBox.bottom + 1;
  });
});

test('outside clicks close open inspector while inside clicks keep it open', async ({
  canvas,
  page,
}) => {
  await page.route('**/theaters/test%20theater/output/story_log.jsonl', async (route: Route) => {
    await route.fulfill({ status: 404, body: '' });
  });

  await mountInspector(canvas, page);
  const inspector = page.locator('#story-log-inspector');
  const toggleBtn = page.locator('#story-log-toggle-btn');

  await toggleBtn.click();
  await expect(inspector).toHaveClass(/open/);
  await expect(toggleBtn).toHaveAttribute('aria-expanded', 'true');

  await page.locator('#story-log-content').click();
  await expect(inspector).toHaveClass(/open/);

  await page.locator('#outside-btn').click();
  await expect(inspector).not.toHaveClass(/open/);
  await expect(toggleBtn).toHaveAttribute('aria-expanded', 'false');
});

test('embedded mode controls remote-story-log via setOpen', async ({ canvas, page }) => {
  let requests = 0;
  await page.route('**/theaters/remote-stage/output/story_log.jsonl', async (route: Route) => {
    requests += 1;
    await route.fulfill({
      status: 200,
      contentType: 'text/plain; charset=utf-8',
      body: jsonl([{ type: 'user_action', action: 'Remote action' }]),
    });
  });

  await mountInspector(canvas, page, {
    theaterId: 'remote-stage',
    html: EMBEDDED_HTML,
    embedded: true,
  });

  expect(requests).toBe(0);
  await page.evaluate(() => {
    (window as unknown as StoryLogHarnessWindow).storyLogController?.setOpen(true);
  });

  const log = page.locator('#remote-story-log');
  await expect(log).toHaveClass(/open/);
  await expect(log).toContainText('Remote action');
  expect(requests).toBe(1);

  await page.evaluate(() => {
    (window as unknown as StoryLogHarnessWindow).storyLogController?.setOpen(false);
  });
  await expect(log).not.toHaveClass(/open/);
});

test('deprecated shim re-exports the canonical initializeStoryLogInspector and works end-to-end', async ({
  canvas,
  page,
}) => {
  await page.route('**/theaters/shim-stage/output/story_log.jsonl', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/plain; charset=utf-8',
      body: jsonl([{ type: 'user_action', action: 'Loaded via shim' }]),
    });
  });

  await mountInspector(canvas, page, {
    moduleUrl: SHIM_URL,
    theaterId: 'shim-stage',
  });

  const sameReference = await page.evaluate(
    async ({ canonicalUrl, shimUrl }: { canonicalUrl: string; shimUrl: string }) => {
      const canonical = await window.__importModule<StoryLogModule>(canonicalUrl);
      const shim = await window.__importModule<StoryLogModule>(shimUrl);
      return canonical.initializeStoryLogInspector === shim.initializeStoryLogInspector;
    },
    { canonicalUrl: CANONICAL_URL, shimUrl: SHIM_URL },
  );
  expect(sameReference).toBe(true);

  await page.locator('#story-log-toggle-btn').click();
  await expect(page.locator('#story-log-content')).toContainText('Loaded via shim');
});
