import { expect, test } from '../../testing/ui/canvas_fixtures';
import { loadGameSnapshots } from '../../testing/ui/game_fixtures';
import type { GamePayload, GameRenderer } from './game-renderer.js';
import type {
  GameClientProgram,
  GameRecord,
  LocalGameActionInput,
  LocalGameRuntime,
  LocalGameSnapshot,
} from './game-runtime.js';

type GameRuntimeModule = typeof import('./game-runtime.js');
type GameRendererModule = typeof import('./game-renderer.js');

interface RuntimeTestWindow extends Window {
  runtime: LocalGameRuntime;
  previewState: GameRecord;
  renderer: GameRenderer;
}

const RUNTIME_MODULE_URL = '/static/generated/game/game-runtime.js';
const RENDERER_MODULE_URL = '/static/generated/game/game-renderer.js';

const snapshots = loadGameSnapshots();

for (const pkg of ['counter', 'checkers'] as const) {
  test(`browser preview matches CEL state and view (${pkg})`, async ({ canvas, page }) => {
    const testCase = pkg === 'counter' ? snapshots.runtime_counter : snapshots.runtime_checkers;
    const payload = testCase.payload;
    expect(payload.client).toBeTruthy();
    expect(payload.state).toBeDefined();

    await canvas.mount({ html: '<title>Game preview</title>' });

    await page.evaluate(
      async ({ moduleUrl, payloadJson }) => {
        const { client, initialState } = JSON.parse(payloadJson) as {
          client: GameClientProgram;
          initialState: GameRecord;
        };
        const { createLocalGame } = await window.__importModule<GameRuntimeModule>(moduleUrl);
        const win = window as unknown as RuntimeTestWindow;
        win.runtime = createLocalGame(client);
        win.previewState = initialState;
      },
      {
        moduleUrl: RUNTIME_MODULE_URL,
        payloadJson: JSON.stringify({
          client: payload.client,
          initialState: payload.state,
        }),
      },
    );

    for (const expected of testCase.steps) {
      const previewJson = await page.evaluate((stepJson: string): string => {
        const action = JSON.parse(stepJson) as LocalGameActionInput;
        const win = window as unknown as RuntimeTestWindow;
        const result = win.runtime.apply(win.previewState, action);
        win.previewState = result.state;
        return JSON.stringify(result);
      }, JSON.stringify(expected.step));
      const preview = JSON.parse(previewJson) as LocalGameSnapshot;

      expect(preview.state).toEqual(expected.state);
      expect(preview.view).toEqual(expected.view);

      if (pkg === 'counter') {
        const rootProps = preview.view[0].props;
        expect(rootProps.negativeDivision).toBe(-3);
        expect(rootProps.remainder).toBe(-1);
        expect(rootProps.boolean).toBe(true);
        expect(rootProps.membership).toBe(true);
        expect(rootProps.deepEquality).toBe(true);
        expect(rootProps.filteredMap).toEqual([4, 6]);
        expect(rootProps.filtered).toEqual([1, 3]);
        expect(rootProps.all).toBe(true);
        expect(rootProps.exists).toBe(true);
        expect(rootProps.unicodeSize).toBe(1);
        expect(rootProps.converted).toBe(4);
        expect(rootProps.nullValue).toBeNull();
      }
    }
  });
}

for (const failure of ['guard', 'invariant', 'evaluation'] as const) {
  test(`failed preview retains position and shows feedback (${failure})`, async ({
    canvas,
    page,
  }) => {
    const payload =
      failure === 'guard'
        ? snapshots.fail_guard
        : failure === 'invariant'
          ? snapshots.fail_invariant
          : snapshots.fail_evaluation;

    await canvas.mount({
      html: '<button id="toggle"></button><div id="game"></div>',
    });

    await page.evaluate(
      async ({ moduleUrl, payloadJson }) => {
        const gamePayload = JSON.parse(payloadJson) as GamePayload;
        const { createGameRenderer } = await window.__importModule<GameRendererModule>(moduleUrl);
        const win = window as unknown as RuntimeTestWindow;
        win.renderer = createGameRenderer({
          container: document.querySelector('#game') as HTMLElement,
          toggleButton: document.querySelector('#toggle') as HTMLElement,
          canEdit: () => true,
          actionUrl: () => '/action',
          refresh: () => {},
          initiallyOpen: true,
        });
        win.renderer.render(gamePayload);
      },
      { moduleUrl: RENDERER_MODULE_URL, payloadJson: JSON.stringify(payload) },
    );

    await page.getByRole('button', { name: 'Add one' }).click();

    await expect(page.getByRole('alert')).toHaveText(
      failure === 'guard'
        ? 'This action is not permitted in the current game state.'
        : 'Game action could not be completed; the action was undone. Try another action.',
    );
    await expect(page.locator('[data-game-id="counter"]')).toHaveText('Count: 0');
    await expect(page.getByRole('button', { name: 'Submit', exact: true })).toHaveCount(0);
  });
}
