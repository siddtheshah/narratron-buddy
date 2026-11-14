import { expect, test } from '../../testing/ui/canvas_fixtures';
import type { GamePayload } from './game-renderer.js';
import type { RemoteGameController } from './remote-game.js';

type RemoteGameModule = typeof import('./remote-game.js');

interface RemoteGameTestWindow extends Window {
    gameController: RemoteGameController;
    canEdit: boolean;
    canContribute: boolean;
    refreshCount: number;
}

const CANONICAL_URL = '/static/generated/canvas/remote-game.js';

const GAME_HTML = `
  <div style="width: 340px;">
    <div class="game-zoom-controls" role="group" aria-label="Game zoom">
      <button id="game-zoom-out" type="button" aria-label="Zoom game out">−</button>
      <output id="game-zoom-level" aria-live="polite">100%</output>
      <button id="game-zoom-in" type="button" aria-label="Zoom game in">+</button>
      <button id="game-zoom-fit" type="button">Fit</button>
    </div>
    <div id="remote-game-viewport" tabindex="0" aria-label="Scrollable game frame" style="width: 340px; height: 300px; overflow: auto;">
      <div id="remote-game-stage" style="position: relative; overflow: hidden;">
        <div id="remote-game-content" style="position: absolute; top: 0; left: 0; width: var(--remote-game-width, 510px); transform-origin: top left;"></div>
      </div>
    </div>
  </div>
`;

const COUNTER_GAME: GamePayload = {
    definition_hash: 'counter',
    revision: 1,
    panel_html:
        '<style>body {width:620px;height:540px;margin:0;background:#182436;color:white}</style>' +
        '<p data-panel-text="count"></p><button data-panel-action="add">Add one</button>',
    view: [
        {
            kind: 'column',
            props: { count: 0 },
            action: null,
            children: [
                {
                    kind: 'button',
                    props: { id: 'add' },
                    action: {
                        name: 'increment',
                        arguments: {},
                        permission: 'contributor',
                    },
                    children: [],
                },
            ],
        },
    ],
};

test('throws explicit error when required DOM elements are missing', async ({ canvas, page }) => {
    await canvas.mount({ html: '<div id="remote-game-viewport"></div>' });
    const message = await page.evaluate(async (url: string) => {
        const mod = await window.__importModule<RemoteGameModule>(url);
        try {
            mod.initializeRemoteGame({
                actionUrl: () => '/api/game/action',
                canEdit: () => true,
                canContribute: () => false,
                refresh: () => {},
            });
            return '';
        } catch (err: unknown) {
            return err instanceof Error ? err.message : String(err);
        }
    }, CANONICAL_URL);
    expect(message).toBe('Remote game DOM elements are missing.');
});

test('auto-fits panel width, handles zoom bounds and fit reset, and dispatches actions with permissions', async ({
    canvas,
    page,
}) => {
    canvas.allowPageErrors();
    let revision = 1;
    let actionPayloads: Array<Record<string, unknown>> = [];

    await canvas.serveRoute(
        (url) => url.origin === canvas.origin && url.pathname === '/api/game/action',
        async (route) => {
            actionPayloads.push(JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>);
            revision += 1;
            await route.fulfill({
                json: {
                    ...COUNTER_GAME,
                    revision,
                    view: [
                        {
                            kind: 'column',
                            props: { count: revision - 1 },
                            children: COUNTER_GAME.view?.[0].children,
                        },
                    ],
                },
            });
        },
    );

    await canvas.mount({
        html: GAME_HTML,
        stylesheets: ['/static/css/remote.css'],
        viewport: { width: 390, height: 844 },
    });

    await page.evaluate(
        async ({ url, gameJson }: { url: string; gameJson: string }) => {
            const mod = await window.__importModule<RemoteGameModule>(url);
            const win = window as unknown as RemoteGameTestWindow;
            const game = JSON.parse(gameJson) as GamePayload;
            win.canEdit = true;
            win.canContribute = false;
            win.refreshCount = 0;
            win.gameController = mod.initializeRemoteGame({
                actionUrl: () => '/api/game/action?theater_id=stage',
                canEdit: () => win.canEdit,
                canContribute: () => win.canContribute,
                refresh: () => {
                    win.refreshCount += 1;
                },
            });
            win.gameController.render(game);
        },
        { url: CANONICAL_URL, gameJson: JSON.stringify(COUNTER_GAME) },
    );

    const frame = page.frameLocator('#remote-game-content iframe');
    await expect(frame.locator('p')).toHaveText('0');
    await expect(page.locator('#remote-game-content iframe')).toHaveAttribute('sandbox', 'allow-scripts');

    // Auto-fitted 620px panel inside 340px viewport -> 55% zoom.
    await expect(page.locator('#game-zoom-level')).toHaveText('55%');
    const initialWidth = await page.locator('#remote-game-content').evaluate((el) =>
        el.style.getPropertyValue('--remote-game-width'),
    );
    expect(initialWidth).toBe('620px');

    // Zoom in and out, verify clamping at 300% and 25%.
    await page.getByRole('button', { name: 'Zoom game in', exact: true }).click();
    await expect(page.locator('#game-zoom-level')).toHaveText('80%');
    expect(await page.locator('#remote-game-viewport').evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);

    for (let i = 0; i < 10; i += 1) {
        if (await page.getByRole('button', { name: 'Zoom game in', exact: true }).isDisabled()) break;
        await page.getByRole('button', { name: 'Zoom game in', exact: true }).click();
    }
    await expect(page.locator('#game-zoom-level')).toHaveText('300%');
    await expect(page.getByRole('button', { name: 'Zoom game in', exact: true })).toBeDisabled();

    for (let i = 0; i < 15; i += 1) {
        if (await page.getByRole('button', { name: 'Zoom game out', exact: true }).isDisabled()) break;
        await page.getByRole('button', { name: 'Zoom game out', exact: true }).click();
    }
    await expect(page.locator('#game-zoom-level')).toHaveText('25%');
    await expect(page.getByRole('button', { name: 'Zoom game out', exact: true })).toBeDisabled();

    // Fit resets zoom and scroll position.
    await page.locator('#remote-game-viewport').evaluate((el) => {
        el.scrollTop = 20;
        el.scrollLeft = 20;
    });
    await page.getByRole('button', { name: 'Fit', exact: true }).click();
    await expect(page.locator('#game-zoom-level')).toHaveText('55%');

    // Preserve iframe state across unchanged renders.
    await frame.locator('p').evaluate((el) => {
        el.dataset.preserved = 'yes';
    });
    await page.evaluate((gameJson: string) => {
        (window as unknown as RemoteGameTestWindow).gameController.render(JSON.parse(gameJson) as GamePayload);
    }, JSON.stringify(COUNTER_GAME));
    await expect(frame.locator('p')).toHaveAttribute('data-preserved', 'yes');

    // Execute action as orator.
    await frame.getByRole('button', { name: 'Add one' }).click();
    await expect(frame.locator('p')).toHaveText('1');
    expect(actionPayloads).toEqual([{ name: 'increment', arguments: {}, revision: 1, draft: [] }]);

    // Permissions: disable edit + contribute -> button disables; enable contribute -> button enables.
    const rev2Json = JSON.stringify({ ...COUNTER_GAME, revision: 2 });
    await page.evaluate((gameJson: string) => {
        const win = window as unknown as RemoteGameTestWindow;
        win.canEdit = false;
        win.canContribute = false;
        win.gameController.render(JSON.parse(gameJson) as GamePayload);
    }, rev2Json);
    await expect(frame.getByRole('button', { name: 'Add one' })).toBeDisabled();

    await page.evaluate((gameJson: string) => {
        const win = window as unknown as RemoteGameTestWindow;
        win.canContribute = true;
        win.gameController.render(JSON.parse(gameJson) as GamePayload);
    }, rev2Json);
    await expect(frame.getByRole('button', { name: 'Add one' })).toBeEnabled();

    // Null game removes iframe.
    await page.evaluate(() => {
        (window as unknown as RemoteGameTestWindow).gameController.render(null);
    });
    await expect(page.locator('#remote-game-content iframe')).toHaveCount(0);
});
