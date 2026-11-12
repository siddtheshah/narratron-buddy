import { expect, test } from '../../testing/ui/canvas_fixtures';

type PinControlModule = typeof import('/static/generated/canvas/pin-control.js');
type CanvasPinState = import('/static/generated/canvas/pin-control.js').CanvasPinState;

interface PinHarnessWindow extends Window {
  pinState: CanvasPinState;
  orator: boolean;
  wheelPins: [boolean, boolean] | null;
  pinControl: { update(): void };
}

const PIN_MODULE_URL = '/static/generated/canvas/pin-control.js';

const COMBINATIONS: ReadonlyArray<{ visualPinned: boolean; musicPinned: boolean }> = [
  { visualPinned: false, musicPinned: false },
  { visualPinned: true, musicPinned: false },
  { visualPinned: false, musicPinned: true },
  { visualPinned: true, musicPinned: true },
];

for (const { visualPinned, musicPinned } of COMBINATIONS) {
  test(`pin button toggles image and music from (${visualPinned}, ${musicPinned})`, async ({
    canvas,
    page,
  }) => {
    const requests: Array<{ url: string; pinned: boolean }> = [];
    await canvas.serveRoute(
      (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/canvas-pin',
      async (route) => {
        const body = JSON.parse(route.request().postData() || '{}') as { pinned: boolean };
        requests.push({ url: '/api/theaters/stage/canvas-pin', pinned: body.pinned });
        await route.fulfill({
          json: { pinned: body.pinned, music_pinned: body.pinned },
        });
      },
    );

    await canvas.mount({
      html: '<button id="canvas-pin-btn"><span id="canvas-pin-label"></span></button>',
    });

    await page.evaluate(
      async ({ moduleUrl, initialVisual, initialMusic }) => {
        const { createCanvasPinControl } = await window.__importModule<PinControlModule>(moduleUrl);
        const win = window as unknown as PinHarnessWindow;
        win.pinState = { pinned: initialVisual, musicPinned: initialMusic };
        win.orator = true;
        win.wheelPins = null;
        win.pinControl = createCanvasPinControl({
          button: document.getElementById('canvas-pin-btn') as HTMLButtonElement | null,
          label: document.getElementById('canvas-pin-label'),
          theaterId: 'stage',
          isOrator: () => win.orator,
          getState: () => win.pinState,
          setState: (state) => {
            win.pinState = state;
          },
          onUpdated: (state) => {
            win.wheelPins = [state.pinned, state.musicPinned];
          },
        });
        win.pinControl.update();
      },
      { moduleUrl: PIN_MODULE_URL, initialVisual: visualPinned, initialMusic: musicPinned },
    );

    const button = page.locator('#canvas-pin-btn');
    const initialPressed =
      visualPinned !== musicPinned ? 'mixed' : String(visualPinned);
    await expect(button).toHaveAttribute('aria-pressed', initialPressed);

    await button.click();

    const expected = !(visualPinned && musicPinned);
    await expect(button).toHaveAttribute('aria-pressed', String(expected));
    await expect(button).toHaveClass(expected ? 'active' : '');
    await expect(button).toHaveAttribute(
      'title',
      expected
        ? 'Unpin image and music and allow changes'
        : 'Pin the current image and music and block changes',
    );
    await expect(page.locator('#canvas-pin-label')).toHaveText(
      expected ? 'Image and music pinned' : 'Pin image and music',
    );
    expect(requests).toEqual([{ url: '/api/theaters/stage/canvas-pin', pinned: expected }]);
    expect(await page.evaluate(() => (window as unknown as PinHarnessWindow).wheelPins)).toEqual([
      expected,
      expected,
    ]);
  });
}

test('pin button hides for viewers and surfaces server errors without mutating state', async ({
  canvas,
  page,
}) => {
  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/theaters/stage/canvas-pin',
    async (route) => {
      await route.fulfill({ status: 403, json: { detail: 'Only the active orator can pin.' } });
    },
  );

  await canvas.mount({
    html: '<button id="canvas-pin-btn"><span id="canvas-pin-label"></span></button>',
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { createCanvasPinControl } = await window.__importModule<PinControlModule>(moduleUrl);
    const win = window as unknown as PinHarnessWindow;
    win.pinState = { pinned: false, musicPinned: false };
    win.orator = false;
    win.pinControl = createCanvasPinControl({
      button: document.getElementById('canvas-pin-btn') as HTMLButtonElement | null,
      label: document.getElementById('canvas-pin-label'),
      theaterId: 'stage',
      isOrator: () => win.orator,
      getState: () => win.pinState,
      setState: (state) => {
        win.pinState = state;
      },
    });
    win.pinControl.update();
  }, PIN_MODULE_URL);

  const button = page.locator('#canvas-pin-btn');
  await expect(button).toBeHidden();

  await page.evaluate(() => {
    const win = window as unknown as PinHarnessWindow;
    win.orator = true;
    win.pinControl.update();
  });
  await expect(button).toBeVisible();

  await button.click();
  await expect(button).toBeEnabled();
  await expect(button).toHaveAttribute('title', 'Only the active orator can pin.');
  await expect(button).toHaveAttribute('aria-pressed', 'false');
});
