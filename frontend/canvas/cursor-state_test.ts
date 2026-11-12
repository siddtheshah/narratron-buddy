import { expect, test } from '../../testing/ui/canvas_fixtures';
import type {
  CanvasCursorSelection,
  CanvasCursorState,
  CanvasCursorStore,
} from '/static/generated/canvas/cursor-state.js';

type CursorStateModule = typeof import('/static/generated/canvas/cursor-state.js');

const CURSOR_MODULE_URL = '/static/generated/canvas/cursor-state.js';
const LEGACY_SHIM_URL = '/static/js/canvas-cursor-state.js';

test.describe('cursor-state module store', () => {
  test('starts in frozen select state and transitions through all valid cursor modes', async ({
    canvas,
    page,
  }) => {
    await canvas.mount({ html: '<div id="root"></div>' });

    const result = await page.evaluate(async (moduleUrl: string) => {
      const { createCanvasCursorState } = await window.__importModule<CursorStateModule>(moduleUrl);
      const store: CanvasCursorStore = createCanvasCursorState();

      const initial = store.current;
      const initialFrozen = Object.isFrozen(initial);
      const seen: CanvasCursorState[] = [];
      const unsubscribe = store.subscribe((state) => seen.push(state));

      store.select({ type: 'marker', color: '#ef4444' });
      const afterFirstMarker = store.current;
      // Same marker + color is a no-op and must not notify listeners.
      store.select({ type: 'marker', color: '#ef4444' });
      // Changing marker color must notify listeners.
      store.select({ type: 'marker', color: '#3b82f6' });
      store.select({ type: 'text' });
      store.select({ type: 'text' });
      store.select({ type: 'navigate' });
      store.select({ type: 'navigate' });
      store.select({ type: 'select' });

      unsubscribe();
      store.select({ type: 'marker', color: 'erase' });

      return {
        initial,
        initialFrozen,
        afterFirstMarkerFrozen: Object.isFrozen(afterFirstMarker),
        seen,
        final: store.current,
      };
    }, CURSOR_MODULE_URL);

    expect(result.initial).toEqual({ type: 'select', color: null });
    expect(result.initialFrozen).toBe(true);
    expect(result.afterFirstMarkerFrozen).toBe(true);
    expect(result.seen).toEqual([
      { type: 'marker', color: '#ef4444' },
      { type: 'marker', color: '#3b82f6' },
      { type: 'text', color: null },
      { type: 'navigate', color: null },
      { type: 'select', color: null },
    ]);
    expect(result.final).toEqual({ type: 'marker', color: 'erase' });
  });

  test('validates cursor types and marker color without mutating state on error', async ({
    canvas,
    page,
  }) => {
    await canvas.mount({ html: '<div id="root"></div>' });

    const errors = await page.evaluate(async (moduleUrl: string) => {
      const { createCanvasCursorState } = await window.__importModule<CursorStateModule>(moduleUrl);
      const store = createCanvasCursorState();
      store.select({ type: 'text' });
      let notifications = 0;
      store.subscribe(() => {
        notifications += 1;
      });

      const messages: string[] = [];
      const invalidInputs: Array<Record<string, unknown>> = [
        { type: 'brush' },
        { type: '' },
        { type: 'marker', color: '' },
        { type: 'marker' },
      ];

      for (const candidate of invalidInputs) {
        try {
          store.select(candidate as unknown as CanvasCursorSelection);
          messages.push('NO_ERROR');
        } catch (err) {
          messages.push(err instanceof Error ? err.message : String(err));
        }
      }

      return {
        messages,
        notifications,
        current: store.current,
      };
    }, CURSOR_MODULE_URL);

    expect(errors.messages).toEqual([
      'Unknown canvas cursor state: brush',
      'Unknown canvas cursor state: ',
      'A marker cursor requires a color',
      'A marker cursor requires a color',
    ]);
    expect(errors.notifications).toBe(0);
    expect(errors.current).toEqual({ type: 'text', color: null });
  });

  test('supports multiple independent subscribers and isolated unsubscribe callbacks', async ({
    canvas,
    page,
  }) => {
    await canvas.mount({ html: '<div id="root"></div>' });

    const counts = await page.evaluate(async (moduleUrl: string) => {
      const { createCanvasCursorState } = await window.__importModule<CursorStateModule>(moduleUrl);
      const store = createCanvasCursorState();
      const logA: string[] = [];
      const logB: string[] = [];

      const unsubA = store.subscribe((state) => logA.push(state.type));
      const unsubB = store.subscribe((state) => logB.push(state.type));

      store.select({ type: 'navigate' });
      unsubA();
      store.select({ type: 'text' });
      unsubB();
      store.select({ type: 'select' });

      return { logA, logB };
    }, CURSOR_MODULE_URL);

    expect(counts.logA).toEqual(['navigate']);
    expect(counts.logB).toEqual(['navigate', 'text']);
  });
});

test.describe('cursor-state singleton and ES module interoperability', () => {
  test('compiled module, deprecated shim, and canvas-navigation share one singleton', async ({
    canvas,
    page,
  }) => {
    await canvas.mount({
      html: `
        <div id="image-container">
          <div id="canvas-navigation-surface" aria-hidden="true"></div>
          <button id="canvas-navigation-toggle" type="button" aria-pressed="false">Zoom</button>
        </div>
      `,
      stylesheets: ['/static/css/canvas-navigation.css'],
    });

    const result = await page.evaluate(
      async ({ compiledUrl, shimUrl }) => {
        const compiled = await window.__importModule<CursorStateModule>(compiledUrl);
        const legacy = await window.__importModule<CursorStateModule>(shimUrl);
        await window.__importModule('/static/generated/canvas/navigation.js');

        const sameReference = compiled.canvasCursorState === legacy.canvasCursorState;
        const toggle = document.getElementById('canvas-navigation-toggle');
        const container = document.getElementById('image-container');
        if (!toggle || !container) {
          throw new Error('Missing expected navigation elements');
        }

        const initialPressed = toggle.getAttribute('aria-pressed');
        toggle.click();
        const afterToggleClick = {
          compiledState: compiled.canvasCursorState.current,
          legacyState: legacy.canvasCursorState.current,
          pressed: toggle.getAttribute('aria-pressed'),
          hasEnabledClass: container.classList.contains('canvas-navigation-enabled'),
          title: toggle.title,
        };

        // Selecting a drawing tool through the singleton exits navigation in canvas-navigation.js.
        legacy.canvasCursorState.select({ type: 'marker', color: '#3b82f6' });
        const afterMarkerSelect = {
          compiledState: compiled.canvasCursorState.current,
          pressed: toggle.getAttribute('aria-pressed'),
          hasEnabledClass: container.classList.contains('canvas-navigation-enabled'),
        };

        return {
          sameReference,
          initialPressed,
          afterToggleClick,
          afterMarkerSelect,
        };
      },
      { compiledUrl: CURSOR_MODULE_URL, shimUrl: LEGACY_SHIM_URL },
    );

    expect(result.sameReference).toBe(true);
    expect(result.initialPressed).toBe('false');
    expect(result.afterToggleClick.compiledState).toEqual({ type: 'navigate', color: null });
    expect(result.afterToggleClick.legacyState).toEqual({ type: 'navigate', color: null });
    expect(result.afterToggleClick.pressed).toBe('true');
    expect(result.afterToggleClick.hasEnabledClass).toBe(true);
    expect(result.afterToggleClick.title).toContain('Switch to selection');
    expect(result.afterMarkerSelect.compiledState).toEqual({ type: 'marker', color: '#3b82f6' });
    expect(result.afterMarkerSelect.pressed).toBe('false');
    expect(result.afterMarkerSelect.hasEnabledClass).toBe(false);
  });
});

test.describe('canvas.html cursor-state integration', () => {
  test('toggles marker, text, and navigate tools via real canvas.js and canvas-navigation.js', async ({
    canvas,
    page,
  }) => {
    await canvas.openCanvas({ isOrator: true, collabEnabled: true });

    const container = page.locator('#image-container');
    const navToggle = page.locator('#canvas-navigation-toggle');
    const textToggle = page.locator('#text-annotation-toggle');
    const blueMarker = page.locator('.color-btn[data-color="#3b82f6"]');

    await expect(container).toHaveAttribute('data-cursor-mode', 'select');
    await expect(navToggle).toHaveAttribute('aria-pressed', 'false');
    await expect(textToggle).toHaveAttribute('aria-pressed', 'false');
    await expect(page.locator('.color-btn.active')).toHaveCount(0);

    // Selecting navigation updates canvas.js cursor mode and shared singleton.
    await navToggle.click();
    await expect(container).toHaveAttribute('data-cursor-mode', 'navigate');
    await expect(navToggle).toHaveAttribute('aria-pressed', 'true');

    // Selecting a marker exits navigation and activates that marker color.
    await blueMarker.click();
    await expect(container).toHaveAttribute('data-cursor-mode', 'marker');
    await expect(navToggle).toHaveAttribute('aria-pressed', 'false');
    await expect(blueMarker).toHaveAttribute('aria-pressed', 'true');

    // Clicking the active marker toggles back to select.
    await blueMarker.click();
    await expect(container).toHaveAttribute('data-cursor-mode', 'select');
    await expect(blueMarker).toHaveAttribute('aria-pressed', 'false');

    // Selecting text tool activates text mode and disables navigation.
    await navToggle.click();
    await expect(container).toHaveAttribute('data-cursor-mode', 'navigate');
    await textToggle.click();
    await page.keyboard.press('Escape');
    await expect(container).toHaveAttribute('data-cursor-mode', 'text');
    await expect(textToggle).toHaveAttribute('aria-pressed', 'true');
    await expect(navToggle).toHaveAttribute('aria-pressed', 'false');

    // Clicking outside canvas/toolbar exits drawing/text mode back to select.
    await page.getByRole('heading', { name: 'Narratron' }).click();
    await expect(container).toHaveAttribute('data-cursor-mode', 'select');
    await expect(textToggle).toHaveAttribute('aria-pressed', 'false');

    const singletonState = await page.evaluate(async (moduleUrl: string) => {
      const { canvasCursorState } = await window.__importModule<CursorStateModule>(moduleUrl);
      return canvasCursorState.current;
    }, CURSOR_MODULE_URL);
    expect(singletonState).toEqual({ type: 'select', color: null });
  });

  test('exercises every transition across select, markers, text, and navigate via real controls', async ({
    canvas,
    page,
  }) => {
    await canvas.openCanvas({ isOrator: true, collabEnabled: true });

    const container = page.locator('#image-container');
    const doodleCanvas = page.locator('#doodle-canvas');
    const navToggle = page.locator('#canvas-navigation-toggle');
    const textToggle = page.locator('#text-annotation-toggle');
    const header = page.getByRole('heading', { name: 'Narratron' });

    const states: CanvasCursorState[] = [
      { type: 'select', color: null },
      { type: 'marker', color: '#22c55e' },
      { type: 'marker', color: '#3b82f6' },
      { type: 'text', color: null },
      { type: 'navigate', color: null },
    ];

    for (const source of states) {
      for (const target of states) {
        await page.evaluate(
          async ({ moduleUrl, state }) => {
            const { canvasCursorState } = await window.__importModule<CursorStateModule>(moduleUrl);
            canvasCursorState.select(state);
          },
          { moduleUrl: CURSOR_MODULE_URL, state: source },
        );

        if (target.type === 'marker') {
          await page.locator(`.color-btn[data-color="${target.color}"]`).click();
        } else if (target.type === 'text') {
          await textToggle.click();
          await page.keyboard.press('Escape');
        } else if (target.type === 'navigate') {
          await navToggle.click();
        } else {
          // Clicking outside canvas/toolbar exits marker/text, or toggle off navigate.
          if (source.type === 'navigate') {
            await navToggle.click();
          } else {
            await header.click();
          }
        }

        const toggledOff = source.type === target.type && source.color === target.color;
        const expected: CanvasCursorState = toggledOff
          ? { type: 'select', color: null }
          : target;

        await expect(container).toHaveAttribute('data-cursor-mode', expected.type);
        await expect(navToggle).toHaveAttribute('aria-pressed', String(expected.type === 'navigate'));
        await expect(textToggle).toHaveAttribute('aria-pressed', String(expected.type === 'text'));
        await expect(page.locator('.color-btn.active')).toHaveCount(expected.type === 'marker' ? 1 : 0);
        if (expected.type === 'marker') {
          await expect(page.locator('.color-btn.active')).toHaveAttribute('data-color', expected.color);
        }
        const pointerEvents = await doodleCanvas.evaluate((el) => (el as HTMLElement).style.pointerEvents);
        expect(pointerEvents).toBe(
          expected.type === 'marker' || expected.type === 'text' ? 'auto' : 'none',
        );
      }
    }
  });

  test('disables text annotation cursor mode for viewers when collaboration is off', async ({
    canvas,
    page,
  }) => {
    await canvas.openCanvas({ isOrator: false, collabEnabled: false });

    const textToggle = page.locator('#text-annotation-toggle');
    await expect(textToggle).toBeDisabled();
    await expect(page.locator('#image-container')).toHaveAttribute('data-cursor-mode', 'select');
  });

  for (const isOrator of [true, false]) {
    for (const toolCase of [
      { selector: '.color-btn[data-color="#3b82f6"]', type: 'marker', color: '#3b82f6' },
      { selector: '.color-btn[data-color="erase"]', type: 'marker', color: 'erase' },
      { selector: '#text-annotation-toggle', type: 'text', color: null },
    ] as const) {
      test(`right-click cycles cursor modes consistently (orator=${isOrator}, tool=${toolCase.type}:${toolCase.color ?? 'none'})`, async ({
        canvas,
        page,
      }) => {
        await canvas.openCanvas({
          isOrator,
          collabEnabled: true,
          viewport: { width: 1200, height: 800 },
        });

        const tool = page.locator(toolCase.selector);
        if (toolCase.type === 'text') {
          await expect(tool).toBeEnabled();
        }
        await tool.click();
        await page.keyboard.press('Escape');

        const container = page.locator('#image-container');
        const wheel = page.locator('#orator-action-wheel');
        const navToggle = page.locator('#canvas-navigation-toggle');

        await expect(container).toHaveAttribute('data-cursor-mode', toolCase.type);

        const cycleOrder = ['select', 'navigate', toolCase.type, 'select', 'navigate', toolCase.type];
        for (const expected of cycleOrder) {
          await container.hover();
          await page.mouse.down({ button: 'right' });
          if (isOrator) {
            await expect(wheel).toBeVisible();
            await expect(wheel.locator('.wheel-center')).toContainText('Cycle');
          } else {
            await expect(wheel).toBeHidden();
          }
          await page.mouse.up({ button: 'right' });
          await expect(wheel).toBeHidden();
          await expect(container).toHaveAttribute('data-cursor-mode', expected);
          await expect(tool).toHaveAttribute('aria-pressed', String(expected === toolCase.type));
          await expect(navToggle).toHaveAttribute('aria-pressed', String(expected === 'navigate'));
          await expect(page.locator('.color-btn.active')).toHaveCount(expected === 'marker' ? 1 : 0);
          if (expected === 'marker' && toolCase.color) {
            await expect(page.locator('.color-btn.active')).toHaveAttribute('data-color', toolCase.color);
          }
        }
      });
    }
  }
});
