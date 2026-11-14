import { CANVAS_ORIGIN, expect, test } from '../../testing/ui/canvas_fixtures';

type ClipShareModule = typeof import('/static/generated/canvas/clip-share.js');
type ClipShareController = import('/static/generated/canvas/clip-share.js').ClipShareController;

interface ClipShareHarnessWindow extends Window {
  clipShare?: ClipShareController;
  copiedTexts: string[];
  openedWindows: Array<{ url: string; target: string; features: string }>;
  clipboardMode: 'resolve' | 'reject' | 'missing';
  popupReturnNull: boolean;
}

const CLIP_SHARE_URL = '/static/generated/canvas/clip-share.js';

const CLIP_SHARE_HTML = `
  <header>
    <button id="share-theater-btn" type="button">Share</button>
    <button id="clip-share-btn" type="button">Clip</button>
    <button id="obs-theater-btn" type="button">OBS</button>
  </header>
  <div id="clip-share-modal" class="clip-share-modal" role="dialog" aria-modal="true" aria-hidden="true">
    <div class="clip-share-card">
      <button id="clip-record-btn" type="button">Open clean clip stage</button>
      <button id="clip-cancel-btn" class="clip-cancel" type="button">Cancel</button>
    </div>
  </div>
`;

test('share-theater-btn copies join-key link, ignores Owner key, and handles clipboard/API failures', async ({
  canvas,
  page,
}) => {
  let metadataPayload: { status: number; body: Record<string, unknown> } = {
    status: 200,
    body: { metadata: { join_key: 'invite-abc' } },
  };

  await canvas.serveRoute(
    (url) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/theaters/quest-7',
    async (route) => {
      await route.fulfill({
        status: metadataPayload.status,
        json: metadataPayload.body,
      });
    },
  );

  await canvas.mount({
    urlPath: '/canvas',
    query: '?theater_id=quest-7&join_key=url-fallback',
    html: CLIP_SHARE_HTML,
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { createClipShareController } = await window.__importModule<ClipShareModule>(moduleUrl);
    const win = window as unknown as ClipShareHarnessWindow;
    win.copiedTexts = [];
    win.openedWindows = [];
    win.clipboardMode = 'resolve';
    win.popupReturnNull = false;

    win.clipShare = createClipShareController({
      theaterId: 'quest-7',
      shareTheaterBtn: document.getElementById('share-theater-btn'),
      clipShareBtn: document.getElementById('clip-share-btn'),
      clipShareModal: document.getElementById('clip-share-modal'),
      clipRecordBtn: document.getElementById('clip-record-btn'),
      clipCancelBtn: document.getElementById('clip-cancel-btn'),
      obsTheaterBtn: document.getElementById('obs-theater-btn'),
      writeClipboard: async (text: string) => {
        if (win.clipboardMode === 'reject') {
          throw new Error('clipboard denied');
        }
        if (win.clipboardMode === 'missing') {
          throw new Error('no clipboard');
        }
        win.copiedTexts.push(text);
      },
    });
  }, CLIP_SHARE_URL);

  // Uses metadata.join_key when non-Owner
  await page.locator('#share-theater-btn').click();
  await expect(page.locator('.share-toast')).toHaveText('✨ Share link copied to clipboard!');
  await expect(page.locator('.share-toast')).toHaveClass(/show/);
  expect(
    await page.evaluate(() => (window as unknown as ClipShareHarnessWindow).copiedTexts),
  ).toEqual([`${CANVAS_ORIGIN}/canvas?theater_id=quest-7&join_key=invite-abc`]);

  // Ignores Owner placeholder join_key and falls back to URL ?join_key
  metadataPayload = { status: 200, body: { metadata: { join_key: 'Owner (hidden)' } } };
  await page.locator('#share-theater-btn').click();
  await expect
    .poll(() =>
      page.evaluate(() => (window as unknown as ClipShareHarnessWindow).copiedTexts.length),
    )
    .toBe(2);
  expect(
    await page.evaluate(() => (window as unknown as ClipShareHarnessWindow).copiedTexts[1]),
  ).toBe(`${CANVAS_ORIGIN}/canvas?theater_id=quest-7&join_key=url-fallback`);

  // Clipboard rejection still shows ready toast
  await page.evaluate(() => {
    (window as unknown as ClipShareHarnessWindow).clipboardMode = 'reject';
  });
  await page.locator('#share-theater-btn').click();
  await expect(page.locator('.share-toast')).toHaveText('🔗 Link ready to share!');

  // API error shows failure toast
  metadataPayload = { status: 500, body: { detail: 'Server failed' } };
  await page.locator('#share-theater-btn').click();
  await expect(page.locator('.share-toast')).toHaveText('Unable to create share link.');
});

test('clip-share modal opens, closes on cancel/backdrop/Escape, and opens /obs?clip=1 popup', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    urlPath: '/canvas',
    query: '?theater_id=stage-9&join_key=secret-key',
    stylesheets: ['/static/css/canvas.css'],
    inlineStyles: ['* { transition: none !important; }'],
    html: CLIP_SHARE_HTML,
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { createClipShareController } = await window.__importModule<ClipShareModule>(moduleUrl);
    const win = window as unknown as ClipShareHarnessWindow;
    win.openedWindows = [];
    win.popupReturnNull = false;

    win.clipShare = createClipShareController({
      theaterId: 'stage-9',
      shareTheaterBtn: document.getElementById('share-theater-btn'),
      clipShareBtn: document.getElementById('clip-share-btn'),
      clipShareModal: document.getElementById('clip-share-modal'),
      clipRecordBtn: document.getElementById('clip-record-btn'),
      clipCancelBtn: document.getElementById('clip-cancel-btn'),
      obsTheaterBtn: document.getElementById('obs-theater-btn'),
      openWindow: (url: string, target: string, features: string) => {
        win.openedWindows.push({ url, target, features });
        return win.popupReturnNull ? null : window;
      },
    });
  }, CLIP_SHARE_URL);

  const modal = page.locator('#clip-share-modal');

  // Open and close via cancel button
  await page.locator('#clip-share-btn').click();
  await expect(modal).toHaveClass(/show/);
  await expect(modal).toHaveAttribute('aria-hidden', 'false');
  await page.locator('#clip-cancel-btn').click();
  await expect(modal).not.toHaveClass(/show/);
  await expect(modal).toHaveAttribute('aria-hidden', 'true');

  // Open and close via backdrop click (without triggering #clip-record-btn)
  await page.locator('#clip-share-btn').click();
  await expect(modal).toHaveClass(/show/);
  await modal.click({ position: { x: 2, y: 2 } });
  await expect(modal).not.toHaveClass(/show/);
  expect(
    await page.evaluate(() => (window as unknown as ClipShareHarnessWindow).openedWindows),
  ).toEqual([]);

  // Open and close via Escape key
  await page.locator('#clip-share-btn').click();
  await expect(modal).toHaveClass(/show/);
  await page.keyboard.press('Escape');
  await expect(modal).not.toHaveClass(/show/);

  // Open clip stage popup (success)
  await page.locator('#clip-share-btn').click();
  await page.locator('#clip-record-btn').click();
  await expect(modal).not.toHaveClass(/show/);
  await expect(page.locator('.share-toast')).toHaveText(
    '🎬 The clean clip stage is ready in a new window.',
  );
  expect(
    await page.evaluate(() => (window as unknown as ClipShareHarnessWindow).openedWindows),
  ).toEqual([
    {
      url: `${CANVAS_ORIGIN}/obs?theater_id=stage-9&join_key=secret-key&clip=1`,
      target: 'narratron-tiktok-clip',
      features: 'popup=yes,width=960,height=640',
    },
  ]);

  // Blocked pop-up shows pop-up warning toast
  await page.evaluate(() => {
    (window as unknown as ClipShareHarnessWindow).popupReturnNull = true;
  });
  await page.locator('#clip-share-btn').click();
  await page.locator('#clip-record-btn').click();
  await expect(page.locator('.share-toast')).toHaveText(
    'Allow pop-ups to open the clean clip stage.',
  );
});

test('obs-theater-btn fetches join_key when absent, copies link, and destroy cleans up listeners and toast', async ({
  canvas,
  page,
}) => {
  let theaterApiCalls = 0;
  await canvas.serveRoute(
    (url) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/theaters/obs-stage',
    async (route) => {
      theaterApiCalls += 1;
      await route.fulfill({
        json: { metadata: { join_key: 'obs-join-42' } },
      });
    },
  );

  await canvas.mount({
    urlPath: '/canvas',
    query: '',
    html: CLIP_SHARE_HTML,
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { createClipShareController } = await window.__importModule<ClipShareModule>(moduleUrl);
    const win = window as unknown as ClipShareHarnessWindow;
    win.copiedTexts = [];

    win.clipShare = createClipShareController({
      getTheaterId: () => 'obs-stage',
      shareTheaterBtn: document.getElementById('share-theater-btn'),
      clipShareBtn: document.getElementById('clip-share-btn'),
      clipShareModal: document.getElementById('clip-share-modal'),
      clipRecordBtn: document.getElementById('clip-record-btn'),
      clipCancelBtn: document.getElementById('clip-cancel-btn'),
      obsTheaterBtn: document.getElementById('obs-theater-btn'),
      toastDurationMs: 120,
      writeClipboard: async (text: string) => {
        win.copiedTexts.push(text);
      },
    });
  }, CLIP_SHARE_URL);

  await page.locator('#obs-theater-btn').click();
  await expect(page.locator('.share-toast')).toHaveText('📹 OBS Browser Source link copied!');
  expect(theaterApiCalls).toBe(1);
  expect(
    await page.evaluate(() => (window as unknown as ClipShareHarnessWindow).copiedTexts),
  ).toEqual([`${CANVAS_ORIGIN}/obs?theater_id=obs-stage&join_key=obs-join-42`]);

  // Toast auto-hides after configured duration
  await expect(page.locator('.share-toast')).not.toHaveClass(/show/, { timeout: 1000 });

  // Destroy removes toast and detaches listeners
  await page.evaluate(() => {
    (window as unknown as ClipShareHarnessWindow).clipShare?.destroy();
  });
  await expect(page.locator('.share-toast')).toHaveCount(0);
  await page.locator('#clip-share-btn').click();
  await expect(page.locator('#clip-share-modal')).not.toHaveClass(/show/);
});
