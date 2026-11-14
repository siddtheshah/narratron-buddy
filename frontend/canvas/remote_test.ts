import fs from 'node:fs';
import path from 'node:path';
import type { Page, Route, WebSocketRoute } from '@playwright/test';
import {
    CANVAS_ORIGIN,
    TEMPLATES_DIR,
    expect,
    test,
    type CanvasHarness,
} from '../../testing/ui/canvas_fixtures';
import type { GamePayload } from './game-renderer.js';
import type {
    BatonState,
    RemotePlaylistGroup,
    RemoteSceneAsset,
    RemoteStatusState,
} from './remote.js';

type RemoteModule = typeof import('./remote.js');

const CANONICAL_URL = '/static/generated/canvas/remote.js';
const SHIM_URL = '/static/js/remote.js';

interface RemoteUser {
    id: number;
    username: string;
}

interface RemoteMountOptions {
    query?: string;
    theaterId?: string;
    remoteState?: RemoteStatusState | (() => RemoteStatusState);
    batonState?: BatonState | (() => BatonState);
    authUser?: RemoteUser | null;
    scenes?: RemoteSceneAsset[];
    playlists?: RemotePlaylistGroup[];
    onRoute?: (url: URL, route: Route) => Promise<boolean> | boolean;
    onWebSocket?: (ws: WebSocketRoute) => void;
    viewport?: { width: number; height: number };
}

interface RemoteMountHandle {
    remoteState: RemoteStatusState;
    batonState: BatonState;
    authUser: RemoteUser | null;
    setRemoteStatusOverride(handler: ((route: Route) => Promise<void> | void) | null): void;
}

async function mountRemote(
    canvas: CanvasHarness,
    options: RemoteMountOptions = {},
): Promise<RemoteMountHandle> {
    const theaterId = options.theaterId ?? 'stage';
    const query = options.query ?? `?theater_id=${encodeURIComponent(theaterId)}`;
    const templateHtml = await fs.promises.readFile(path.join(TEMPLATES_DIR, 'remote.html'), 'utf-8');

    const handle: RemoteMountHandle = {
        remoteState:
            typeof options.remoteState === 'function'
                ? options.remoteState()
                : (options.remoteState ?? {
                      agent_running: true,
                      pinned: false,
                      music_pinned: false,
                      viewer_collab_enabled: false,
                      is_owner: true,
                      is_orator: true,
                      is_contributor: true,
                      is_adventure_mode: true,
                  }),
        batonState:
            typeof options.batonState === 'function'
                ? options.batonState()
                : (options.batonState ?? {
                      owner: { id: 1, username: 'owner' },
                      active_orator: { id: 1, username: 'owner' },
                      contributors: [],
                      active_viewers: [],
                      baton_request: null,
                  }),
        authUser: options.authUser === undefined ? { id: 1, username: 'owner' } : options.authUser,
        setRemoteStatusOverride: (handler) => {
            remoteStatusOverride = handler;
        },
    };

    let remoteStatusOverride: ((route: Route) => Promise<void> | void) | null = null;

    if (options.viewport) {
        await canvas.page.setViewportSize(options.viewport);
    }

    await canvas.serveRoute(
        (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/remote',
        async (route: Route) => {
            await route.fulfill({
                body: templateHtml,
                contentType: 'text/html; charset=utf-8',
            });
        },
    );

    await canvas.serveRoute(`${CANVAS_ORIGIN}/favicon.png`, async (route: Route) => {
        await route.fulfill({ status: 204 });
    });

    await canvas.serveRoute(
        (url: URL) =>
            url.origin === CANVAS_ORIGIN &&
            !url.pathname.startsWith('/static/') &&
            url.pathname !== '/remote' &&
            url.pathname !== '/favicon.png',
        async (route: Route) => {
            const url = new URL(route.request().url());

            if (options.onRoute && (await options.onRoute(url, route))) {
                return;
            }

            if (url.pathname.endsWith('/remote')) {
                if (remoteStatusOverride) {
                    await remoteStatusOverride(route);
                    return;
                }
                const payload =
                    typeof options.remoteState === 'function' ? options.remoteState() : handle.remoteState;
                await route.fulfill({ json: payload });
                return;
            }

            if (url.pathname.endsWith('/baton')) {
                const payload =
                    typeof options.batonState === 'function' ? options.batonState() : handle.batonState;
                await route.fulfill({ json: payload });
                return;
            }

            if (url.pathname === '/api/auth/me') {
                await route.fulfill({
                    json: handle.authUser
                        ? { authenticated: true, user: handle.authUser }
                        : { authenticated: false, user: null },
                });
                return;
            }

            if (url.pathname.endsWith('/scenes')) {
                await route.fulfill({ json: options.scenes ?? [] });
                return;
            }

            if (url.pathname.endsWith('/playlists')) {
                await route.fulfill({ json: options.playlists ?? [] });
                return;
            }

            if (url.pathname === '/api/chat' || url.pathname === '/api/suggestions') {
                await route.fulfill({ json: [] });
                return;
            }

            if (url.pathname.endsWith('/output/story_log.jsonl')) {
                await route.fulfill({ status: 404, body: '' });
                return;
            }

            await route.fulfill({ json: { status: 'accepted' } });
        },
    );

    await canvas.page.routeWebSocket(/wss?:\/\/canvas\.test\/ws\/.*/, (ws: WebSocketRoute) => {
        options.onWebSocket?.(ws);
    });

    await canvas.page.goto(`${CANVAS_ORIGIN}/remote${query}`);
    return handle;
}

async function waitForRemoteReady(page: Page): Promise<void> {
    await page.waitForFunction(() => {
        const controls = document.getElementById('controls') as HTMLFieldSetElement | null;
        return Boolean(controls && !controls.disabled);
    });
}

async function swipePanel(page: Page, dx: number, dy: number, selector = '#remote-views'): Promise<void> {
    await page.locator(selector).evaluate(
        (element, delta) => {
            const start = new Event('touchstart', { bubbles: true }) as unknown as {
                touches: Array<{ clientX: number; clientY: number }>;
            };
            start.touches = [{ clientX: 200, clientY: 500 }];
            element.dispatchEvent(start as unknown as Event);
            const end = new Event('touchend', { bubbles: true }) as unknown as {
                touches: Array<{ clientX: number; clientY: number }>;
                changedTouches: Array<{ clientX: number; clientY: number }>;
            };
            end.touches = [];
            end.changedTouches = [{ clientX: 200 + delta.dx, clientY: 500 + delta.dy }];
            element.dispatchEvent(end as unknown as Event);
        },
        { dx, dy },
    );
}

test('deprecated remote shim re-exports initializeRemote without auto-init on bare pages', async ({
    canvas,
    page,
}) => {
    await canvas.mount();
    const sameExport = await page.evaluate(
        async ({ canonicalUrl, shimUrl }) => {
            const canonical = await window.__importModule<RemoteModule>(canonicalUrl);
            const shim = await window.__importModule<RemoteModule>(shimUrl);
            return canonical.initializeRemote === shim.initializeRemote;
        },
        { canonicalUrl: CANONICAL_URL, shimUrl: SHIM_URL },
    );
    expect(sameExport).toBe(true);
});

test('missing theater_id shows error status and toast without crashing', async ({ canvas, page }) => {
    await mountRemote(canvas, { query: '' });
    await expect(page.locator('#connection-status')).toHaveText('Open the remote from your theater.');
    await expect(page.locator('#toast')).toBeVisible();
    await expect(page.locator('#toast-message')).toHaveText('Open the remote from your theater.');
    await expect(page.locator('#canvas-link')).toHaveAttribute('hidden', '');
});

test('header displays theater id and join key while canvas link omits join key', async ({ canvas, page }) => {
    await mountRemote(canvas, {
        theaterId: 'other',
        query: '?theater_id=other&join_key=secret',
        viewport: { width: 390, height: 844 },
    });
    await waitForRemoteReady(page);
    await expect(page.locator('#canvas-link')).toHaveAttribute('href', '/canvas?theater_id=other');
    await expect(page.locator('#theater')).toHaveCount(0);
    await expect(page.locator('header #theater-id')).toHaveText('other');
    await expect(page.locator('header #theater-key')).toHaveText('secret');
    await expect(page.locator('header #theater-key')).toBeVisible();
});

test('adventure controls, preview story tab, and composer slotting follow theater mode', async ({
    canvas,
    page,
}) => {
    const handle = await mountRemote(canvas, {
        viewport: { width: 390, height: 844 },
        remoteState: {
            agent_running: true,
            is_owner: true,
            is_orator: true,
            is_adventure_mode: false,
        },
    });
    await waitForRemoteReady(page);

    await expect(page.locator('#story-direction')).toBeHidden();
    await expect(page.getByRole('button', { name: 'Update story', exact: true })).toBeHidden();
    await page.getByRole('tab', { name: 'Watch', exact: true }).click();
    await expect(page.locator('#preview-story-tab')).toBeHidden();

    // Keyboard tab cycling skips hidden Story tab.
    await page.getByRole('tab', { name: 'Chat', exact: true }).press('ArrowRight');
    await expect(page.locator('#preview-thought-panel')).toBeVisible();
    await page.getByRole('tab', { name: 'Agent', exact: true }).press('ArrowRight');
    await expect(page.locator('#preview-chat-panel')).toBeVisible();
    await expect(page.locator('#preview-story-panel')).toBeHidden();

    // Enabling adventure mode reveals story direction, Update story, and moves composer into Story tab.
    handle.remoteState = {
        agent_running: true,
        is_owner: true,
        is_orator: true,
        is_adventure_mode: true,
    };
    await page.getByRole('tab', { name: 'Story', exact: true }).waitFor({ state: 'visible' });
    await page.getByRole('tab', { name: 'Story', exact: true }).click();
    await expect(page.locator('#story-composer-slot #remote-composer')).toBeVisible();
    await page.getByRole('tab', { name: 'Remote', exact: true }).click();
    await expect(page.locator('#inputs-composer-slot #remote-composer')).toBeVisible();
    await expect(page.locator('#story-direction')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Update story', exact: true })).toBeVisible();
});

test('actions trace pending button, surface errors as dismissable toasts, and preserve drafts', async ({
    canvas,
    page,
}) => {
    let pendingAction: Route | null = null;
    let rejectNextAction = false;
    let rejectCommand = false;
    const requests: Array<{ path: string; body: unknown }> = [];

    await mountRemote(canvas, {
        viewport: { width: 390, height: 844 },
        onRoute: async (url, route) => {
            if (url.pathname.endsWith('/orator-action')) {
                requests.push({ path: url.pathname, body: JSON.parse(route.request().postData() ?? '{}') });
                if (rejectNextAction) {
                    await route.fulfill({ status: 409, json: { detail: 'Unable to switch scene right now.' } });
                    return true;
                }
                pendingAction = route;
                return true;
            }
            if (url.pathname === '/api/orator/command') {
                requests.push({ path: url.pathname, body: JSON.parse(route.request().postData() ?? '{}') });
                if (rejectCommand) {
                    await route.fulfill({ status: 409, json: { detail: 'The live agent is not connected.' } });
                    return true;
                }
                await route.fulfill({ json: { status: 'accepted' } });
                return true;
            }
            return false;
        },
    });
    await waitForRemoteReady(page);

    const newImageBtn = page.getByRole('button', { name: 'New image' });
    await newImageBtn.click();
    await expect(newImageBtn).toHaveAttribute('aria-busy', 'true');
    await expect(newImageBtn).toBeDisabled();
    expect(await page.locator('#controls').evaluate((el) => (el as HTMLFieldSetElement).disabled)).toBe(true);
    expect(pendingAction).not.toBeNull();
    await pendingAction!.fulfill({ json: { status: 'accepted' } });
    await waitForRemoteReady(page);
    await expect(newImageBtn).not.toHaveAttribute('aria-busy', 'true');

    // Error marks button and opens dismissable toast.
    rejectNextAction = true;
    await newImageBtn.click();
    await waitForRemoteReady(page);
    await expect(newImageBtn).toHaveAttribute('data-error', 'Unable to switch scene right now.');
    await expect(newImageBtn).toHaveAttribute('aria-describedby', 'feedback');
    await expect(page.locator('#toast')).toBeVisible();
    await expect(page.locator('#toast-message')).toHaveText('Unable to switch scene right now.');
    await page.getByRole('button', { name: 'Dismiss error' }).click();
    await expect(page.locator('#toast')).toBeHidden();

    // Command modal: empty instruction shows Escape-dismissable toast.
    await page.getByRole('button', { name: 'Keyboard' }).click();
    await expect(page.locator('#command-modal')).toBeVisible();
    await expect(page.locator('#command')).toBeFocused();
    await page.getByRole('button', { name: 'Send instruction' }).click();
    await expect(page.locator('#toast-message')).toHaveText('Enter an instruction first.');
    await page.keyboard.press('Escape');
    await expect(page.locator('#toast')).toBeHidden();

    // Failed instruction preserves draft.
    rejectCommand = true;
    await page.locator('#command').fill('Keep this draft');
    await page.getByRole('button', { name: 'Send instruction' }).click();
    await expect(page.locator('#toast-message')).toHaveText('The live agent is not connected.');
    await expect(page.locator('#command')).toHaveValue('Keep this draft');

    // Successful instruction clears draft and closes modal.
    rejectCommand = false;
    await page.getByRole('button', { name: 'Send instruction' }).click();
    await waitForRemoteReady(page);
    await expect(page.locator('#command')).toHaveValue('');
    await expect(page.locator('#command-modal')).toBeHidden();
    expect(requests.some((r) => r.path === '/api/orator/command')).toBe(true);
});

test('stopped agent disables live controls while keeping history, pins, and collab enabled', async ({
    canvas,
    page,
}) => {
    let agentRunning = true;
    let collabEnabled = true;

    await mountRemote(canvas, {
        viewport: { width: 390, height: 844 },
        remoteState: () => ({
            agent_running: agentRunning,
            pinned: true,
            music_pinned: true,
            viewer_collab_enabled: collabEnabled,
            is_owner: true,
            is_orator: true,
        }),
        onRoute: async (url, route) => {
            if (url.pathname.endsWith('/agent/stop')) {
                agentRunning = false;
                await route.fulfill({ json: { agent_running: false } });
                return true;
            }
            if (url.pathname.endsWith('/agent/start')) {
                agentRunning = true;
                await route.fulfill({ json: { agent_running: true } });
                return true;
            }
            if (url.pathname.endsWith('/collab')) {
                const payload = JSON.parse(route.request().postData() ?? '{}') as { enabled: boolean };
                collabEnabled = payload.enabled;
                await route.fulfill({ json: { viewer_collab_enabled: collabEnabled } });
                return true;
            }
            return false;
        },
    });
    await waitForRemoteReady(page);

    const runningColor = await page.locator('#agent-toggle').evaluate((button) => getComputedStyle(button).color);
    await page.getByRole('button', { name: 'Turn agent off' }).click();
    await waitForRemoteReady(page);
    await expect(page.locator('#connection-status')).toHaveText('Connected · Agent off');
    expect(await page.locator('#agent-toggle').evaluate((button) => getComputedStyle(button).color)).not.toBe(
        runningColor,
    );
    await expect(page.getByRole('button', { name: 'New image' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Keyboard' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Microphone' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Previous scene', exact: true })).toBeEnabled();
    await expect(page.getByRole('button', { name: 'Unpin scene' })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('button', { name: 'Unpin music' })).toBeEnabled();

    await page.getByRole('button', { name: 'Turn collaboration off' }).click();
    await waitForRemoteReady(page);
    await expect(page.getByRole('button', { name: 'Turn collaboration on' })).toHaveAttribute('aria-pressed', 'false');

    await page.getByRole('button', { name: 'Turn agent on' }).click();
    await waitForRemoteReady(page);
    await expect(page.getByRole('button', { name: 'New image' })).toBeEnabled();
});

test('tabs support keyboard, back/forward history, and horizontal swipe gestures', async ({
    canvas,
    page,
}) => {
    await mountRemote(canvas, { viewport: { width: 390, height: 844 } });
    await waitForRemoteReady(page);

    await page.getByRole('tab', { name: 'Remote' }).focus();
    await page.keyboard.press('ArrowLeft');
    await expect(page.getByRole('tab', { name: 'Assets' })).toBeFocused();
    await page.keyboard.press('End');
    await expect(page.getByRole('tab', { name: 'Party', exact: true })).toBeFocused();

    // Small / vertical swipes do not change tab.
    await swipePanel(page, 0, -140);
    await expect(page.getByRole('tab', { name: 'Party', exact: true })).toHaveAttribute('aria-selected', 'true');
    await swipePanel(page, 140, 0);
    await expect(page.getByRole('tab', { name: 'Watch', exact: true })).toHaveAttribute('aria-selected', 'true');
    await swipePanel(page, 140, 10);
    await expect(page.getByRole('tab', { name: 'Remote' })).toHaveAttribute('aria-selected', 'true');

    await page.goBack();
    await expect(page.getByRole('tab', { name: 'Watch', exact: true })).toHaveAttribute('aria-selected', 'true');
    await page.goForward();
    await expect(page.getByRole('tab', { name: 'Remote' })).toHaveAttribute('aria-selected', 'true');
});

test('preview tracks scenes, retries failed images, and displays agent thoughts', async ({
    canvas,
    page,
}) => {
    let failImage = true;
    const imageRequests: string[] = [];

    const handle = await mountRemote(canvas, {
        viewport: { width: 390, height: 844 },
        remoteState: {
            agent_running: true,
            is_owner: true,
            is_orator: true,
            preview_url: '/scenes/first.svg',
            agent_thought: {
                text: 'Pondering the ancient ruins...',
                time: 100,
                highlighted: true,
            },
        },
        onRoute: async (url, route) => {
            if (url.pathname.startsWith('/scenes/')) {
                imageRequests.push(url.pathname);
                if (failImage) {
                    await route.fulfill({ status: 404, body: 'Missing' });
                    return true;
                }
                await route.fulfill({
                    body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="navy"/></svg>',
                    contentType: 'image/svg+xml',
                });
                return true;
            }
            return false;
        },
    });
    await waitForRemoteReady(page);
    expect(imageRequests).toEqual([]);

    await page.getByRole('tab', { name: 'Watch', exact: true }).click();
    await expect(page.locator('#preview-placeholder')).toHaveText('Scene could not load. Retrying…');
    expect(imageRequests).toEqual(['/scenes/first.svg']);

    // Agent thought tab & wandering transition.
    const agentTab = page.getByRole('tab', { name: 'Agent', exact: true });
    await expect(agentTab).toHaveClass(/is-highlighted/);
    await expect(page.locator('#preview-chat-panel')).toBeVisible();
    await expect(page.locator('#preview-thought-panel')).toBeHidden();
    await agentTab.click();
    await expect(page.locator('#preview-thought-panel')).toBeVisible();
    await expect(page.locator('#preview-chat-panel')).toBeHidden();
    const panelText = await page.locator('#preview-thought-panel').innerText();
    expect(panelText).not.toContain("Narratron's current thought");
    expect(panelText).not.toContain('Narratron Current Thought');
    expect(panelText).not.toContain('Thought');
    await expect(page.locator('#agent-thought-card')).toHaveClass(/is-highlighted/);
    await expect(page.locator('#agent-thought-text')).toHaveText('Pondering the ancient ruins...');

    failImage = false;
    handle.remoteState = {
        agent_running: true,
        is_owner: true,
        is_orator: true,
        preview_url: '/scenes/first.svg',
        agent_thought: {
            text: 'wandering',
            time: 200,
            highlighted: false,
        },
    };
    await expect(page.locator('#agent-thought-text')).toHaveText(
        'Narratron has wandered away. (Try resummoning?)',
    );
    await expect(page.locator('#agent-thought-text')).toHaveClass(/is-wandering/);
    await expect(agentTab).not.toHaveClass(/is-highlighted/);
    await expect(page.locator('#preview-image')).toBeVisible();
    await expect(page.locator('#preview-status')).toHaveText('Current scene');
    expect(
        await page.locator('#preview-image').evaluate((image) => getComputedStyle(image).objectFit),
    ).toBe('contain');

    // Pinning keeps unchanged scene image without reloading; switching scene loads new URL once.
    const countAfterFirstLoad = imageRequests.length;
    await page.getByRole('tab', { name: 'Remote', exact: true }).click();
    await page.getByRole('button', { name: 'Pin scene', exact: true }).click();
    await waitForRemoteReady(page);
    expect(imageRequests.length).toBe(countAfterFirstLoad);

    handle.remoteState = {
        ...handle.remoteState,
        preview_url: '/scenes/second.svg',
    };
    await page.getByRole('button', { name: 'Next scene', exact: true }).click();
    await waitForRemoteReady(page);
    await page.getByRole('tab', { name: 'Watch', exact: true }).click();
    await expect(page.locator('#preview-image')).toHaveAttribute('src', '/scenes/second.svg');
    expect(imageRequests).toContain('/scenes/second.svg');

    // Revoking access disconnects and clears preview image.
    handle.setRemoteStatusOverride(async (route) => {
        await route.fulfill({ status: 403, json: { detail: 'Only the active orator can use this remote.' } });
    });
    await expect(page.locator('#connection-status')).toHaveAttribute('data-connected', 'false');
    await expect(page.locator('#preview-placeholder')).toHaveText('Reconnect to see the canvas.');
    await expect(page.locator('#preview-image')).toBeHidden();
    expect(await page.locator('#preview-image').getAttribute('src')).toBeNull();
});

test('assets carousel loads scenes/playlists, pushes on double tap, and retries on failure', async ({
    canvas,
    page,
}) => {
    let failScenes = true;
    const pushed: Array<{ path: string; body: unknown }> = [];

    await mountRemote(canvas, {
        viewport: { width: 390, height: 844 },
        playlists: [
            {
                id: 'playlist:rain',
                name: 'Rain',
                tracks: [{ id: 'track:storm', name: 'Storm', url: '/storm.mp3' }],
            },
        ],
        onRoute: async (url, route) => {
            if (url.pathname.endsWith('/scenes')) {
                if (failScenes) {
                    await route.fulfill({ status: 503, json: { detail: 'Scenes failed to load.' } });
                    return true;
                }
                await route.fulfill({
                    json: [{ id: 'forest', name: 'Forest', description: 'A misty wood', url: '/forest.svg' }],
                });
                return true;
            }
            if (url.pathname === '/forest.svg') {
                await route.fulfill({
                    body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"/>',
                    contentType: 'image/svg+xml',
                });
                return true;
            }
            if (url.pathname.endsWith('/scenes/push') || url.pathname.endsWith('/playlists/push')) {
                pushed.push({ path: url.pathname, body: JSON.parse(route.request().postData() ?? '{}') });
                await route.fulfill({ json: { status: 'accepted' } });
                return true;
            }
            return false;
        },
    });
    await waitForRemoteReady(page);

    await page.getByRole('tab', { name: 'Assets' }).click();
    await expect(page.locator('#asset-status')).toHaveText('Scenes failed to load.');
    await expect(page.locator('#toast-message')).toHaveText('Scenes failed to load.');
    await page.getByRole('button', { name: 'Dismiss error' }).click();

    failScenes = false;
    await page.getByRole('button', { name: 'Retry loading assets' }).click();
    await expect(page.getByRole('heading', { name: 'Forest' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Rain' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Storm' })).toBeVisible();

    const forestCard = page.locator('.asset-card').filter({
        has: page.getByRole('heading', { name: 'Forest', exact: true }),
    });
    await forestCard.dblclick();
    await waitForRemoteReady(page);

    const rainCard = page.locator('.asset-card').filter({
        has: page.getByRole('heading', { name: 'Rain', exact: true }),
    });
    await rainCard.dblclick();
    await waitForRemoteReady(page);

    expect(pushed).toEqual([
        { path: '/api/theaters/stage/scenes/push', body: { scene_id: 'forest' } },
        { path: '/api/theaters/stage/playlists/push', body: { id: 'playlist:rain' } },
    ]);

    // Swiping inside carousel does not switch tabs.
    await swipePanel(page, -150, 0, '#scenes-carousel');
    await expect(page.getByRole('tab', { name: 'Assets' })).toHaveAttribute('aria-selected', 'true');
});

test('role and baton visibility gates pure viewer, idle owner, and contributor flows', async ({
    canvas,
    page,
}) => {
    const batonRequests: Array<{ path: string; method: string; body: unknown }> = [];

    const handle = await mountRemote(canvas, {
        viewport: { width: 390, height: 844 },
        query: '?theater_id=stage&join_key=party_key',
        authUser: { id: 5, username: 'viewer5' },
        remoteState: {
            agent_running: true,
            is_owner: false,
            is_orator: false,
            is_contributor: false,
        },
        batonState: {
            owner: { id: 1, username: 'alice' },
            active_orator: { id: 1, username: 'alice' },
            contributors: [{ id: 2, username: 'bob' }],
            active_viewers: [{ id: 5, username: 'viewer5' }],
            baton_request: null,
        },
        onRoute: async (url, route) => {
            if (url.pathname.includes('/baton/')) {
                batonRequests.push({
                    path: url.pathname,
                    method: route.request().method(),
                    body: route.request().postData() ? JSON.parse(route.request().postData()!) : null,
                });
                if (url.pathname.endsWith('/baton/accept')) {
                    handle.batonState = {
                        ...handle.batonState,
                        active_orator: { id: 5, username: 'viewer5' },
                        baton_request: null,
                    };
                    handle.remoteState = {
                        ...handle.remoteState,
                        is_orator: true,
                        is_contributor: true,
                    };
                } else if (url.pathname.endsWith('/baton/contributors') && route.request().method() === 'POST') {
                    handle.batonState = {
                        ...handle.batonState,
                        contributors: [...(handle.batonState.contributors ?? []), { id: 3, username: 'charlie' }],
                    };
                }
                await route.fulfill({ json: handle.batonState });
                return true;
            }
            return false;
        },
    });

    // Pure viewer only sees Watch tab and cannot navigate to #inputs.
    await expect(page.locator('#canvas-preview')).toBeVisible();
    await expect(page.locator('#assets-tab')).toBeHidden();
    await expect(page.locator('#inputs-tab')).toBeHidden();
    await expect(page.locator('#party-tab')).toBeHidden();

    await page.evaluate(() => {
        location.hash = 'inputs';
    });
    await expect(page.locator('#canvas-preview')).toBeVisible();
    await expect(page.locator('#remote-controls')).toBeHidden();

    // Incoming baton request modal allows contributor to accept baton and gain orator controls.
    handle.batonState = {
        ...handle.batonState,
        contributors: [{ id: 5, username: 'viewer5' }],
        baton_request: { target_user_id: 5, target_username: 'viewer5', timeout_seconds: 30 },
    };
    await page.locator('#baton-request-modal').waitFor({ state: 'visible' });
    await page.locator('#accept-baton-btn').click();
    await expect(page.locator('#baton-request-modal')).toBeHidden();
    await expect(page.locator('#assets-tab')).toBeVisible();
    await expect(page.locator('#inputs-tab')).toBeVisible();
    await expect(page.locator('#party-tab')).toBeHidden();

    // Owner when idle sees Party and Watch tabs, can take back baton, add contributor, pass baton, and copy key.
    handle.authUser = { id: 1, username: 'alice' };
    handle.remoteState = {
        agent_running: true,
        is_owner: true,
        is_orator: false,
    };
    handle.batonState = {
        owner: { id: 1, username: 'alice' },
        active_orator: { id: 2, username: 'bob' },
        contributors: [{ id: 2, username: 'bob' }],
        active_viewers: [{ id: 3, username: 'charlie' }],
        baton_request: null,
    };
    await page.reload();
    await expect(page.locator('#party-tab')).toBeVisible();
    await expect(page.locator('#preview-tab')).toBeVisible();
    await expect(page.locator('#assets-tab')).toBeHidden();
    await expect(page.locator('#inputs-tab')).toBeHidden();
    await expect(page.locator('#menu-take-back-baton-btn')).toBeVisible();
    await expect(page.locator('#baton-theater-key')).toHaveText('party_key');

    await page.getByRole('button', { name: '+ Contributor' }).click();
    await page.getByRole('button', { name: 'Pass Baton 🎙️' }).click();
    await page.locator('#menu-take-back-baton-btn').click();

    expect(batonRequests).toEqual([
        { path: '/api/theaters/stage/baton/accept', method: 'POST', body: null },
        {
            path: '/api/theaters/stage/baton/contributors',
            method: 'POST',
            body: { target_user_id: 3 },
        },
        {
            path: '/api/theaters/stage/baton/request',
            method: 'POST',
            body: { target_user_id: 3, timeout_seconds: 30 },
        },
        { path: '/api/theaters/stage/baton/takeback', method: 'POST', body: null },
    ]);
});

test('microphone streams audio via WebSocket and handles denied permissions', async ({
    canvas,
    page,
}) => {
    const agentMessages: Array<string | Uint8Array> = [];

    await mountRemote(canvas, {
        viewport: { width: 390, height: 844 },
        onWebSocket: (ws) => {
            if (ws.url().includes('/agent')) {
                ws.onMessage((msg) => {
                    if (typeof msg === 'string') {
                        agentMessages.push(msg);
                    } else {
                        agentMessages.push(new Uint8Array(msg));
                    }
                });
                ws.send(JSON.stringify({ setupComplete: true }));
            }
        },
    });
    await waitForRemoteReady(page);

    const micBtn = page.getByRole('button', { name: 'Microphone' });

    // Denied getUserMedia surfaces permission toast.
    await page.evaluate(() => {
        Object.defineProperty(navigator, 'mediaDevices', {
            value: {
                getUserMedia: () => Promise.reject(new DOMException('Denied', 'NotAllowedError')),
            },
            configurable: true,
        });
    });
    await micBtn.click();
    await expect(page.locator('#toast-message')).toHaveText('Microphone permission denied.');
    await expect(micBtn).toHaveAttribute('aria-pressed', 'false');

    // Mock WebCodecs + AudioWorklet + getUserMedia and verify streaming lifecycle.
    await page.evaluate(() => {
        interface MockWin {
            AudioEncoder: unknown;
            AudioData: unknown;
            AudioContext: unknown;
            AudioWorkletNode: unknown;
        }
        const win = window as unknown as MockWin;
        win.AudioEncoder = class MockEncoder {
            state = 'configured';
            private readonly init: { output: (chunk: { byteLength: number; copyTo: (arr: Uint8Array) => void }) => void };
            constructor(init: { output: (chunk: { byteLength: number; copyTo: (arr: Uint8Array) => void }) => void }) {
                this.init = init;
            }
            configure(): void {}
            encode(): void {
                this.init.output({
                    byteLength: 4,
                    copyTo: (arr: Uint8Array) => arr.set([1, 2, 3, 4]),
                });
            }
            flush(): Promise<void> {
                return Promise.resolve();
            }
            close(): void {
                this.state = 'closed';
            }
        };
        win.AudioData = class MockAudioData {
            close(): void {}
        };
        Object.defineProperty(navigator, 'mediaDevices', {
            value: {
                getUserMedia: () =>
                    Promise.resolve({
                        getTracks: () => [{ stop(): void {} }],
                    }),
            },
            configurable: true,
        });
        win.AudioContext = class MockAudioContext {
            state = 'running';
            destination = {};
            audioWorklet = {
                addModule: () => Promise.resolve(),
            };
            createMediaStreamSource(): { connect(): void } {
                return { connect(): void {} };
            }
            close(): Promise<void> {
                this.state = 'closed';
                return Promise.resolve();
            }
        };
        win.AudioWorkletNode = class MockAudioWorkletNode {
            port: { onmessage: ((ev: { data: Float32Array }) => void) | null } = { onmessage: null };
            constructor() {
                setTimeout(() => {
                    this.port.onmessage?.({ data: new Float32Array(480) });
                }, 10);
            }
            connect(): void {}
            disconnect(): void {}
        };
    });

    await micBtn.click();
    await expect(micBtn).toHaveAttribute('aria-pressed', 'true');
    await expect(micBtn).toHaveText('Listening…');

    await expect.poll(() => agentMessages.some((m) => m instanceof Uint8Array)).toBe(true);

    await micBtn.click();
    await expect(micBtn).toHaveAttribute('aria-pressed', 'false');
    await expect(micBtn).toHaveText('Microphone');

    const jsonTypes = agentMessages
        .filter((m): m is string => typeof m === 'string')
        .map((m) => (JSON.parse(m) as { type?: string }).type);
    expect(jsonTypes).toContain('activity_start');
    expect(jsonTypes).toContain('activity_end');
});

test('watch chat, watch audio websocket, watch game tab, and party copy-key/decline-baton flows', async ({
    canvas,
    page,
}) => {
    canvas.allowPageErrors();
    const chatMessages: Array<{ author: string; text: string; type?: string }> = [
        { author: 'bob', text: 'Welcome!' },
    ];
    let audioSocket: WebSocketRoute | null = null;
    const speechGen = 1;

    await page.addInitScript(() => {
        const created: Array<{ src: string; muted: boolean; paused: boolean; getAttribute(name: string): string | null; removeAttribute(name: string): void; play(): Promise<void>; pause(): void; load(): void }> = [];
        (window as unknown as { __audioStubs: typeof created }).__audioStubs = created;
        class MockAudio {
            src = '';
            muted = false;
            paused = true;
            volume = 1;
            constructor() {
                created.push(this);
            }
            getAttribute(name: string): string | null {
                return name === 'src' ? (this.src || null) : null;
            }
            removeAttribute(name: string): void {
                if (name === 'src') this.src = '';
            }
            play(): Promise<void> {
                this.paused = false;
                return Promise.resolve();
            }
            pause(): void {
                this.paused = true;
            }
            load(): void {}
        }
        (window as unknown as { Audio: typeof MockAudio }).Audio = MockAudio;
    });

    const handle = await mountRemote(canvas, {
        viewport: { width: 390, height: 844 },
        query: '?theater_id=stage&join_key=copy_secret',
        remoteState: {
            agent_running: true,
            is_owner: true,
            is_orator: true,
            committed_scene_speech_generation: speechGen,
            game_state: {
                definition_hash: 'counter',
                revision: 1,
                panel_html: '<p data-panel-text="count"></p>',
                view: [{ kind: 'column', props: { count: 42 }, action: null, children: [] }],
            },
        },
        onRoute: async (url, route) => {
            if (url.pathname === '/api/chat') {
                if (route.request().method() === 'POST') {
                    const body = JSON.parse(route.request().postData() ?? '{}') as { author: string; text: string };
                    const type = body.text.startsWith('/suggest ') ? 'suggestion' : 'chat';
                    chatMessages.push({ ...body, type });
                    await route.fulfill({ json: { type } });
                    return true;
                }
                await route.fulfill({ json: chatMessages });
                return true;
            }
            if (url.pathname === '/api/user-help') {
                await route.fulfill({
                    json: { type: 'user_help', text: 'Help text', html: '<p><strong>Help</strong> answer</p>' },
                });
                return true;
            }
            if (url.pathname.endsWith('/speech/live.mp3')) {
                await route.fulfill({ status: 204 });
                return true;
            }
            return false;
        },
        onWebSocket: (ws) => {
            if (ws.url().includes('/ws/canvas-state')) {
                audioSocket = ws;
            }
        },
    });
    await waitForRemoteReady(page);

    // Copy theater key in Party tab.
    await page.getByRole('tab', { name: 'Party', exact: true }).click();
    await expect(page.locator('#baton-theater-key')).toHaveText('copy_secret');
    await page.locator('#copy-theater-key-btn').click();
    await expect(page.locator('#copy-theater-key-btn')).toContainText('Copied!');
    await expect(page.locator('#toast-message')).toHaveText('Theater key copied to clipboard!');

    // Watch tab: Chat, Game, and Audio.
    await page.getByRole('tab', { name: 'Watch', exact: true }).click();
    await expect(page.getByRole('log', { name: 'Theater chat' })).toContainText('Welcome!');

    const chatInput = page.getByRole('textbox', { name: 'Chat message' });
    await chatInput.fill('Hello Watch');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect(page.getByRole('log', { name: 'Theater chat' })).toContainText('Hello Watch');

    await chatInput.fill('How do I play?');
    await page.getByRole('button', { name: 'Find basic help and documentation (free)' }).click();
    await expect(page.locator('.user-help-message strong')).toHaveText('Help');

    // Game tab renders embedded game frame and hides when game_state becomes null.
    const gameTab = page.getByRole('tab', { name: 'Game', exact: true });
    await expect(gameTab).toBeVisible();
    await gameTab.click();
    await expect(page.frameLocator('#remote-game-content iframe').locator('p')).toHaveText('42');

    handle.remoteState = {
        agent_running: true,
        is_owner: true,
        is_orator: true,
        committed_scene_speech_generation: speechGen,
        game_state: null,
    };
    await expect(gameTab).toBeHidden();
    await expect(page.locator('#preview-chat-panel')).toBeVisible();

    // Audio WebSocket speech delivery when Watch is visible.
    expect(audioSocket).not.toBeNull();
    audioSocket!.send(JSON.stringify({ type: 'scene_speech_ready', speech_generation: 1, audio_url: '/speech/live.mp3' }));
    await expect.poll(async () => page.evaluate(() => {
        const stubs = (window as unknown as { __audioStubs: Array<{ src: string }> }).__audioStubs;
        return stubs[1]?.src ?? '';
    })).toBe('/speech/live.mp3');
    await page.locator('.preview-frame').click();
    await expect(page.locator('.preview-frame')).toHaveAttribute('aria-pressed', 'true');
});

test('watch game frame zooms, handles permissions across viewports, preserves polls, and restores after removal', async ({
    canvas,
    page,
}) => {
    canvas.allowPageErrors();
    let game: GamePayload = {
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
                        action: { name: 'increment', arguments: {}, permission: 'contributor' },
                        children: [],
                    },
                ],
            },
        ],
    };

    const handle = await mountRemote(canvas, {
        viewport: { width: 390, height: 844 },
        query: '?theater_id=stage&join_key=secret#preview',
        remoteState: {
            agent_running: false,
            is_owner: false,
            is_orator: true,
            viewer_collab_enabled: false,
            is_contributor: false,
            game_state: game,
        },
        onRoute: async (url, route) => {
            if (url.pathname === '/api/game/action') {
                expect(JSON.parse(route.request().postData() ?? '{}')).toEqual({
                    name: 'increment',
                    arguments: {},
                    revision: 1,
                    draft: [],
                });
                game = {
                    ...game,
                    revision: 2,
                    view: [
                        {
                            kind: 'column',
                            props: { count: 1 },
                            action: null,
                            children: game.view?.[0].children ?? [],
                        },
                    ],
                };
                handle.remoteState = { ...handle.remoteState, game_state: game };
                await route.fulfill({ json: game });
                return true;
            }
            return false;
        },
    });

    const tab = page.getByRole('tab', { name: 'Game', exact: true });
    await tab.click();
    const frame = page.frameLocator('#remote-game-content iframe');
    await expect(frame.locator('p')).toHaveText('0');
    await expect(page.locator('#remote-game-content iframe')).toHaveAttribute('sandbox', 'allow-scripts');
    await expect(page.locator('#game-zoom-level')).not.toHaveText('100%');

    const initial = await page.locator('#remote-game-content iframe').boundingBox();
    expect(initial).not.toBeNull();
    expect(initial!.width).toBeLessThanOrEqual(390);

    await page.getByRole('button', { name: 'Zoom game in', exact: true }).click();
    const enlarged = await page.locator('#remote-game-content iframe').boundingBox();
    expect(enlarged).not.toBeNull();
    expect(enlarged!.width).toBeGreaterThan(initial!.width);
    expect(
        await page.locator('#remote-game-viewport').evaluate((el) => el.scrollWidth > el.clientWidth),
    ).toBe(true);

    await page.getByRole('button', { name: 'Fit', exact: true }).click();

    // Polls preserve the iframe and its DOM state when unchanged.
    await frame.locator('p').evaluate((el) => {
        el.dataset.preserved = 'yes';
    });
    await page.waitForTimeout(2200);
    await expect(frame.locator('p')).toHaveAttribute('data-preserved', 'yes');

    await frame.getByRole('button', { name: 'Add one' }).click();
    await expect(frame.locator('p')).toHaveText('1');

    handle.remoteState = { ...handle.remoteState, is_orator: false };
    await expect(frame.getByRole('button', { name: 'Add one' })).toBeDisabled();

    handle.remoteState = {
        ...handle.remoteState,
        viewer_collab_enabled: true,
        is_contributor: true,
    };
    await expect(frame.getByRole('button', { name: 'Add one' })).toBeEnabled();

    for (const [width, height] of [
        [320, 568],
        [390, 480],
        [844, 390],
    ] as const) {
        await page.setViewportSize({ width, height });
        await page.getByRole('button', { name: 'Fit', exact: true }).click();
        const box = await page.locator('#remote-game-stage').boundingBox();
        expect(box).not.toBeNull();
        expect(box!.width).toBeLessThanOrEqual(width);
        expect(await page.locator('#remote-game-viewport').evaluate((el) => el.clientHeight > 0)).toBe(true);
    }

    handle.remoteState = { ...handle.remoteState, game_state: null };
    await expect(tab).toBeHidden();
    await expect(page.locator('#preview-chat-panel')).toBeVisible();

    handle.remoteState = { ...handle.remoteState, game_state: game };
    await expect(tab).toBeVisible();
    await tab.click();
    await expect(frame.locator('p')).toHaveText('1');
});

test('watch composers and agent thought panel fit short viewports without horizontal overflow', async ({
    canvas,
    page,
}) => {
    await mountRemote(canvas, { viewport: { width: 390, height: 844 } });
    await waitForRemoteReady(page);
    await page.getByRole('tab', { name: 'Watch', exact: true }).click();

    for (const [width, height] of [
        [390, 844],
        [320, 568],
        [390, 480],
        [844, 390],
    ] as const) {
        await page.setViewportSize({ width, height });
        for (const [tabName, composerSelector, logSelector] of [
            ['Chat', '#chat-form', '#chat-messages'],
            ['Story', '#remote-composer', '#remote-story-log'],
        ] as const) {
            await page.getByRole('tab', { name: tabName, exact: true }).click();
            const composer = page.locator(composerSelector);
            await expect(composer).toBeInViewport({ ratio: 1 });
            expect(
                await composer.evaluate((element) => element.getBoundingClientRect().bottom <= innerHeight),
            ).toBe(true);
            expect(await page.locator(logSelector).evaluate((element) => element.clientHeight > 0)).toBe(true);
            expect(
                await page.locator('.preview-tabs').evaluate((element) => element.clientHeight <= 32),
            ).toBe(true);
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        }
        await page.getByRole('tab', { name: 'Agent', exact: true }).click();
        await expect(page.locator('#preview-thought-panel')).toBeInViewport({ ratio: 1 });
    }
});

test('story preview reads sanitized story log updates, sends instructions, and handles empty/error/non-baton states', async ({
    canvas,
    page,
}) => {
    let logStatus = 200;
    const sentCommands: Array<Record<string, string>> = [];
    const entries: Array<Record<string, unknown>> = [
        { type: 'user_action', action: 'Open the gate' },
        {
            type: 'story_plan',
            output: {
                narration: "The gate opens. <script>alert('unsafe')</script>",
                dialogue: [{ speaker: 'Guard', text: 'Welcome, traveler.' }],
                die_rolls: [{ notation: '1d20', total: 17 }],
            },
        },
    ];

    const handle = await mountRemote(canvas, {
        viewport: { width: 390, height: 844 },
        onRoute: async (url, route) => {
            if (url.pathname.endsWith('/output/story_log.jsonl')) {
                if (logStatus !== 200) {
                    await route.fulfill({ status: logStatus, body: '' });
                } else {
                    await route.fulfill({
                        body: entries.map((e) => JSON.stringify(e)).join('\n'),
                        contentType: 'text/plain',
                    });
                }
                return true;
            }
            if (url.pathname === '/api/orator/command') {
                sentCommands.push(JSON.parse(route.request().postData() ?? '{}') as Record<string, string>);
                await route.fulfill({ json: { status: 'accepted' } });
                return true;
            }
            return false;
        },
    });
    await waitForRemoteReady(page);

    await page.getByRole('tab', { name: 'Watch', exact: true }).click();
    await page.getByRole('tab', { name: 'Story', exact: true }).click();
    const log = page.getByRole('log', { name: 'Story log', exact: true });
    await expect(log).toContainText('Open the gate');
    await expect(log).toContainText('Guard: Welcome, traveler.');
    await expect(log).toContainText('1d20: 17');
    await expect(page.locator('.preview-frame')).toBeVisible();
    await expect(page.locator('#preview-chat-panel')).toBeHidden();
    const frameBox = await page.locator('.preview-frame').boundingBox();
    const logBox = await log.boundingBox();
    expect(frameBox).not.toBeNull();
    expect(logBox).not.toBeNull();
    expect(frameBox!.y).toBeLessThan(logBox!.y);
    expect(await log.locator('script').count()).toBe(0);

    await page.getByRole('button', { name: 'Keyboard', exact: true }).click();
    await page.locator('#command').fill('Ask the guard for directions');
    await page.getByRole('button', { name: 'Send instruction' }).click();
    await expect(page.locator('#command-modal')).toBeHidden();
    expect(sentCommands).toEqual([{ text: 'Ask the guard for directions' }]);
    await expect(page.getByRole('button', { name: 'Microphone', exact: true })).toBeEnabled();

    entries.push({ type: 'story_plan', output: { narration: 'The guard points north.' } });
    await expect(log).toContainText('The guard points north.');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

    await page.getByRole('tab', { name: 'Remote', exact: true }).click();
    await expect(page.locator('#controls #mic-btn')).toBeVisible();
    await page.getByRole('tab', { name: 'Watch', exact: true }).click();
    await expect(log).toBeVisible();

    // Non-baton viewer story readability.
    handle.remoteState = {
        agent_running: true,
        is_owner: false,
        is_orator: false,
        is_contributor: false,
        is_adventure_mode: true,
    };
    handle.batonState = {
        owner: { id: 2, username: 'other' },
        active_orator: { id: 2, username: 'other' },
        contributors: [],
        active_viewers: [],
        baton_request: null,
    };
    await expect(log).toContainText('The guard points north.');
    await expect(page.locator('#story-input-status')).toHaveText('The baton holder controls story input.');
    await expect(page.locator('#mic-btn')).toBeHidden();
    await expect(page.locator('#keyboard-btn')).toBeHidden();
    await expect(page.locator('#story-input-controls')).toHaveAttribute('disabled', '');

    await page.getByRole('tab', { name: 'Chat', exact: true }).click();
    await expect(log).toBeHidden();
    await expect(page.locator('.preview-frame')).toBeVisible();
    await expect(page.locator('#preview-chat-panel')).toBeVisible();
});

test('story preview shows 503 error, 404 empty message, and disables controls when agent is off', async ({
    canvas,
    page,
}) => {
    let logStatus = 503;

    const handle = await mountRemote(canvas, {
        viewport: { width: 390, height: 844 },
        onRoute: async (url, route) => {
            if (url.pathname.endsWith('/output/story_log.jsonl')) {
                await route.fulfill({ status: logStatus, body: '' });
                return true;
            }
            return false;
        },
    });
    await waitForRemoteReady(page);

    await page.getByRole('tab', { name: 'Watch', exact: true }).click();
    await page.getByRole('tab', { name: 'Story', exact: true }).click();
    const log = page.getByRole('log', { name: 'Story log', exact: true });
    await expect(log).toHaveText('The story log could not be loaded.');

    logStatus = 404;
    await expect(log).toHaveText('No story actions have been recorded yet.');

    handle.remoteState = {
        agent_running: false,
        is_owner: true,
        is_orator: true,
        is_adventure_mode: true,
    };
    await expect(page.getByRole('button', { name: 'Microphone', exact: true })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Keyboard', exact: true })).toBeDisabled();
    await expect(page.locator('#story-input-status')).toContainText('Turn the agent on');

    await page.getByRole('tab', { name: 'Chat', exact: true }).click();
    await expect(log).toBeHidden();
    await expect(page.locator('.preview-frame')).toBeVisible();
    await expect(page.locator('#preview-chat-panel')).toBeVisible();
});

for (const isOrator of [true, false]) {
    test(`preview chat sends emotes, suggestions, Ctrl+Enter, Shift+Enter, private help, and fits mobile (isOrator=${isOrator})`, async ({
        canvas,
        page,
    }) => {
        const errors: string[] = [];
        page.on('pageerror', (error) => errors.push(String(error)));

        const messages: Array<{ author: string; text: string; type?: string }> = [
            { author: 'bob', text: 'Welcome to the theater!' },
        ];
        const sent: Array<{ author: string; text: string }> = [];
        const questions: Array<{ question: string }> = [];

        await mountRemote(canvas, {
            viewport: { width: 390, height: 844 },
            query: '?theater_id=stage#preview',
            remoteState: isOrator
                ? {
                      agent_running: true,
                      is_owner: true,
                      is_orator: true,
                      is_contributor: true,
                      is_adventure_mode: true,
                  }
                : {
                      agent_running: false,
                      is_owner: false,
                      is_orator: false,
                  },
            batonState: isOrator
                ? undefined
                : {
                      owner: { id: 2, username: 'bob' },
                      active_orator: { id: 2, username: 'bob' },
                      contributors: [],
                      active_viewers: [],
                      baton_request: null,
                  },
            onRoute: async (url, route) => {
                if (url.pathname === '/api/chat') {
                    if (route.request().method() === 'POST') {
                        const payload = JSON.parse(route.request().postData() ?? '{}') as {
                            author: string;
                            text: string;
                        };
                        sent.push(payload);
                        const type = payload.text.startsWith('/suggest ') ? 'suggestion' : 'chat';
                        messages.push({ ...payload, type });
                        await route.fulfill({ json: { type } });
                    } else {
                        await route.fulfill({ json: messages });
                    }
                    return true;
                }
                if (url.pathname === '/api/user-help') {
                    questions.push(JSON.parse(route.request().postData() ?? '{}') as { question: string });
                    await route.fulfill({
                        json: {
                            type: 'user_help',
                            text: 'Use the Watch tab to chat.',
                            html: '<p>Use the <strong>Watch</strong> tab to chat.</p>',
                        },
                    });
                    return true;
                }
                return false;
            },
        });

        await expect(page.getByRole('log', { name: 'Theater chat' })).toContainText('Welcome to the theater!');
        await expect(page.locator('#chat-name-display')).toHaveText('owner');

        const chatInput = page.getByRole('textbox', { name: 'Chat message' });
        await chatInput.fill('Hello ');
        await page.getByRole('button', { name: 'Emotes', exact: true }).click();
        await page.getByRole('button', { name: 'Dragon', exact: true }).click();
        await expect(chatInput).toHaveValue('Hello 🐉');
        await page.getByRole('button', { name: 'Send', exact: true }).click();
        await expect(page.getByRole('log')).toContainText('Hello 🐉');
        expect(sent).toEqual([{ author: 'owner', text: 'Hello 🐉' }]);
        await expect(chatInput).toHaveValue('');
        await chatInput.press('Escape');

        await chatInput.fill('Explore the castle');
        await page.getByRole('button', { name: 'Send suggestion' }).click();
        await expect(page.locator('.suggestion-message')).toContainText('Explore the castle');
        expect(sent[sent.length - 1].text).toBe('/suggest Explore the castle');
        await expect(page.locator('.suggestion-upvote-btn')).toBeDisabled();

        await chatInput.fill('Follow the trail');
        await chatInput.press('Control+Enter');
        await expect(page.locator('.suggestion-message')).toHaveCount(2);
        expect(sent[sent.length - 1].text).toBe('/suggest Follow the trail');

        await chatInput.fill('How do I chat?');
        await page.getByRole('button', { name: 'Find basic help and documentation (free)' }).click();
        await expect(page.locator('.user-help-message strong')).toHaveText('Watch');
        expect(questions).toEqual([{ question: 'How do I chat?' }]);
        expect(sent.length).toBe(3);
        expect(messages.every((m) => !m.text.includes('How do I chat?'))).toBe(true);

        await chatInput.fill('First line');
        await chatInput.press('Shift+Enter');
        await chatInput.pressSequentially('Second line');
        await expect(chatInput).toHaveValue('First line\nSecond line');
        await chatInput.press('Enter');
        await expect(page.getByRole('log')).toContainText('Second line');
        expect(sent[sent.length - 1].text).toBe('First line\nSecond line');

        messages.push({ author: 'bob', text: 'A new message from canvas' });
        await expect(page.getByRole('log')).toContainText('A new message from canvas');
        await expect(page.locator('.user-help-message strong')).toHaveText('Watch');
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        expect(
            await page.locator('#chat-form').evaluate((form) => {
                const bounds = form.getBoundingClientRect();
                return [
                    ...form.querySelectorAll('.chat-composer-actions > button, [data-chat-emotes-toggle]'),
                ].every((button) => {
                    const rect = button.getBoundingClientRect();
                    return rect.left >= bounds.left && rect.right <= bounds.right && rect.height >= 44;
                });
            }),
        ).toBe(true);

        if (!isOrator) {
            await expect(page.getByRole('tab', { name: 'Remote', exact: true })).toBeHidden();
            await expect(chatInput).toBeEnabled();
        }
        expect(errors).toEqual([]);
    });
}

for (const releaseEvent of ['pointerup', 'pointercancel'] as const) {
    test(`preview chat follows story updates unless scrolled up or pointer is held (${releaseEvent})`, async ({
        canvas,
        page,
    }) => {
        const messages = Array.from({ length: 12 }, (_, index) => ({
            author: 'Narratron',
            text: `Story beat ${index}. `.repeat(12),
        }));

        await mountRemote(canvas, {
            viewport: { width: 390, height: 844 },
            query: '?theater_id=stage#preview',
            onRoute: async (url, route) => {
                if (url.pathname === '/api/chat') {
                    await route.fulfill({ json: messages });
                    return true;
                }
                return false;
            },
        });

        const log = page.getByRole('log', { name: 'Theater chat' });
        await expect(log.locator('.chat-message')).toHaveCount(12);
        const waitForAtBottom = async (): Promise<void> => {
            await page.waitForFunction(() => {
                const el = document.getElementById('chat-messages');
                return Boolean(el && el.scrollHeight - el.scrollTop - el.clientHeight < 1);
            });
        };
        await waitForAtBottom();

        messages.push({ author: 'Narratron', text: 'A new story update. '.repeat(30) });
        await expect(log.locator('.chat-message')).toHaveCount(13);
        await waitForAtBottom();

        await log.evaluate((el) => {
            el.scrollTop = 80;
        });
        messages.push({ author: 'Narratron', text: 'The story continues. '.repeat(30) });
        await expect(log.locator('.chat-message')).toHaveCount(14);
        expect(await log.evaluate((el) => el.scrollTop)).toBe(80);

        await log.evaluate((el) => {
            el.scrollTop = el.scrollHeight;
        });
        const heldPosition = await log.evaluate((el) => el.scrollTop);
        await log.dispatchEvent('pointerdown', { pointerId: 7, pointerType: 'touch' });
        messages.push({ author: 'Narratron', text: 'An update while holding. '.repeat(30) });
        await expect(log.locator('.chat-message')).toHaveCount(15);
        expect(await log.evaluate((el) => el.scrollTop)).toBe(heldPosition);

        await page.locator('body').dispatchEvent(releaseEvent, { pointerId: 7, pointerType: 'touch' });
        await log.evaluate((el) => {
            el.scrollTop = el.scrollHeight;
        });
        messages.push({ author: 'Narratron', text: 'Following the story again. '.repeat(30) });
        await expect(log.locator('.chat-message')).toHaveCount(16);
        await waitForAtBottom();
    });
}

test('remote disables controls on 403 revocation and keeps controls available in preview empty state', async ({
    canvas,
    page,
}) => {
    const handle = await mountRemote(canvas, { viewport: { width: 390, height: 844 } });
    await waitForRemoteReady(page);

    await page.getByRole('tab', { name: 'Watch', exact: true }).click();
    await expect(page.locator('#preview-placeholder')).toHaveText('No scene on the canvas yet.');
    await expect(page.locator('#preview-image')).toBeHidden();
    await page.getByRole('tab', { name: 'Remote', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Next scene', exact: true })).toBeEnabled();

    handle.setRemoteStatusOverride(async (route) => {
        await route.fulfill({ status: 403, json: { detail: 'Only the active orator can use this remote.' } });
    });
    await expect(page.getByText('Only the active orator can use this remote.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'New image' })).toBeDisabled();
});

for (const width of [320, 390, 700] as const) {
    test(`preview separate view back/forward/reload preserves draft and focus at width=${width}`, async ({
        canvas,
        page,
    }) => {
        await mountRemote(canvas, { viewport: { width, height: 568 } });
        await waitForRemoteReady(page);

        await page.getByRole('button', { name: 'Keyboard' }).click();
        await page.locator('#command').fill('Keep my direction draft');
        await page.locator('#command-modal-close').click();

        await page.getByRole('tab', { name: 'Watch', exact: true }).click();
        await page.waitForURL('**#preview');
        await expect(page.locator('#remote-controls')).toBeHidden();
        await expect(page.locator('#canvas-preview')).toBeVisible();
        expect(
            await page.locator('#preview-tab').evaluate((button) => button === document.activeElement),
        ).toBe(true);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

        await page.goBack();
        await expect(page.locator('#canvas-preview')).toBeHidden();
        await expect(page.locator('#command')).toHaveValue('Keep my direction draft');
        expect(
            await page.locator('#inputs-tab').evaluate((button) => button === document.activeElement),
        ).toBe(true);

        await page.goForward();
        await expect(page.locator('#canvas-preview')).toBeVisible();
        await page.reload();
        await expect(page.locator('#canvas-preview')).toBeVisible();

        await page.getByRole('tab', { name: 'Remote', exact: true }).click();
        await expect(page.locator('#remote-controls')).toBeVisible();
        expect(page.url()).toBe(`${CANVAS_ORIGIN}/remote?theater_id=stage#inputs`);
    });

    test(`remote header branding, key hierarchy, and control bars fit on one line at width=${width}`, async ({
        canvas,
        page,
    }) => {
        await mountRemote(canvas, {
            viewport: { width, height: 568 },
            query: '?theater_id=stage&join_key=mykey123',
        });
        await waitForRemoteReady(page);

        const brand = page.locator("header a[aria-label='Narratron home']");
        await expect(brand).toContainText('Narratron Mobile');
        await expect(page.locator('header a img.brand-avatar')).toBeVisible();
        await expect(page.locator('header a .brand-text')).toHaveText('Narratron Mobile');
        const gradient = await page
            .locator('header a .brand-text')
            .evaluate((el) => getComputedStyle(el).backgroundImage);
        expect(gradient).toContain('linear-gradient');
        expect(await page.locator('header .badge').count()).toBe(0);

        const theaterIdEl = page.locator('header #theater-id');
        const theaterKeyEl = page.locator('header #theater-key');
        await expect(theaterIdEl).toHaveText('stage');
        await expect(theaterKeyEl).toHaveText('mykey123');
        await expect(theaterKeyEl).toBeVisible();
        const idBox = await theaterIdEl.boundingBox();
        const keyBox = await theaterKeyEl.boundingBox();
        expect(idBox).not.toBeNull();
        expect(keyBox).not.toBeNull();
        expect(keyBox!.y).toBeGreaterThanOrEqual(idBox!.y);
        const idFont = await theaterIdEl.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
        const keyFont = await theaterKeyEl.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
        expect(keyFont).toBeLessThan(idFont);

        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        expect(
            await page.evaluate(() =>
                [...document.querySelectorAll('.bar')].every((bar) => {
                    const children = [...bar.children].filter((child) => !child.classList.contains('sr-only'));
                    const bounds = children.map((child) => child.getBoundingClientRect());
                    return (
                        bounds.every(
                            (rect) => Math.abs(rect.top + rect.height / 2 - bounds[0].top - bounds[0].height / 2) < 1,
                        ) && bar.scrollWidth <= bar.clientWidth
                    );
                }),
            ),
        ).toBe(true);

        const controlsBox = await page.locator('#controls').boundingBox();
        expect(controlsBox).not.toBeNull();
        expect(controlsBox!.height).toBeLessThan(400);
        await expect(page.locator('#canvas-preview')).toBeHidden();
        await expect(page.locator('#inputs-tab')).toHaveAttribute('aria-selected', 'true');
        const tablistBox = await page
            .getByRole('tablist', { name: 'Remote screens', exact: true })
            .boundingBox();
        expect(tablistBox).not.toBeNull();
        expect(tablistBox!.width).toBeLessThanOrEqual(width);
        expect(await page.locator('.desk-heading').count()).toBe(0);
        expect(await page.locator('[aria-labelledby="agent-title"] #connection-status').count()).toBe(1);

        for (const power of await page.locator('.power-button:visible').all()) {
            const box = await power.boundingBox();
            expect(box).not.toBeNull();
            expect(box!.width).toBeGreaterThanOrEqual(60);
            expect(box!.height).toBeGreaterThanOrEqual(52);
        }

        const collabPower = page.locator('#collaboration');
        await expect(collabPower).toBeVisible();
        const collabBox = await collabPower.boundingBox();
        expect(collabBox).not.toBeNull();
        expect(collabBox!.width).toBeGreaterThanOrEqual(60);
        expect(collabBox!.height).toBeGreaterThanOrEqual(52);

        for (const [media, pin, newAction] of [
            ['scene', 'scene-pin', 'new_image'],
            ['music', 'music-pin', 'new_music'],
        ] as const) {
            const buttons = page.locator(`[aria-labelledby="${media}-title"] button`);
            await expect(buttons.nth(1)).toHaveAttribute('id', pin);
            await expect(buttons.last()).toHaveAttribute('data-action', newAction);
            expect((await buttons.nth(0).getAttribute('aria-label'))?.startsWith('Previous')).toBe(true);
            expect((await buttons.nth(2).getAttribute('aria-label'))?.startsWith('Next')).toBe(true);
        }
    });
}

for (const [selector, routeEnding] of [
    ['[data-action="new_image"]', '/orator-action'],
    ['#scene-pin', '/orator-action'],
    ['#agent-toggle', '/agent/stop'],
    ['#collaboration', '/collab'],
    ['#command-form button', '/api/orator/command'],
] as const) {
    test(`remote traces only pending button with pending-trace animation for ${selector}`, async ({
        canvas,
        page,
    }) => {
        const pending: Route[] = [];

        await mountRemote(canvas, {
            viewport: { width: 390, height: 844 },
            onRoute: async (url, route) => {
                if (url.pathname.endsWith(routeEnding)) {
                    pending.push(route);
                    return true;
                }
                return false;
            },
        });
        await waitForRemoteReady(page);

        if (selector === '#command-form button') {
            await page.getByRole('button', { name: 'Keyboard' }).click();
            await page.locator('#command').fill('A quiet moment.');
        }

        const button = page.locator(selector);
        await button.click();
        await page.waitForFunction(() => document.querySelectorAll('button[aria-busy=true]').length === 1);
        await expect(button).toHaveAttribute('aria-busy', 'true');
        expect(
            await button.evaluate((el) => getComputedStyle(el, '::before').animationName),
        ).toBe('pending-trace');
        const feedbackBox = await page.locator('#feedback').boundingBox();
        expect(feedbackBox).not.toBeNull();
        expect(feedbackBox!.height).toBeLessThanOrEqual(1);
        expect(await page.locator('#controls').evaluate((el) => (el as HTMLFieldSetElement).disabled)).toBe(
            true,
        );
        expect(pending).toHaveLength(1);

        await pending[0].fulfill({ json: { status: 'accepted' } });
        await page.waitForFunction(() => !document.querySelector('button[aria-busy=true]'));
        await waitForRemoteReady(page);
    });
}

test('assets carousels omit descriptions/buttons, retry empty state, and preserve DOM across polls', async ({
    canvas,
    page,
}) => {
    let failScenes = true;

    await mountRemote(canvas, {
        viewport: { width: 390, height: 844 },
        playlists: [{ id: 'playlist:rain', name: 'Rain', tracks: [] }],
        onRoute: async (url, route) => {
            if (url.pathname.endsWith('/scenes')) {
                if (failScenes) {
                    await route.fulfill({ status: 503, json: { detail: 'Try again later.' } });
                } else {
                    await route.fulfill({
                        json: [{ id: 'forest', name: 'Forest', description: 'A misty wood', url: '/forest.svg' }],
                    });
                }
                return true;
            }
            if (url.pathname === '/forest.svg') {
                await route.fulfill({
                    body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"/>',
                    contentType: 'image/svg+xml',
                });
                return true;
            }
            return false;
        },
    });
    await waitForRemoteReady(page);

    await page.getByRole('tab', { name: 'Assets' }).click();
    await expect(page.locator('#asset-status')).toHaveText('Try again later.');

    failScenes = false;
    await page.getByRole('button', { name: 'Retry loading assets' }).click();
    const forestCard = page.locator('.asset-card').filter({
        has: page.getByRole('heading', { name: 'Forest', exact: true }),
    });
    await expect(forestCard).toBeVisible();
    await expect(page.getByText('A misty wood')).toHaveCount(0);
    expect(await page.locator('.asset-card button').count()).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

    // Mark DOM node and verify 2.5s polling does not recreate the card.
    await page.evaluate(() => {
        document.querySelector('.asset-card')?.setAttribute('data-preserved-marker', 'active');
    });
    await page.waitForTimeout(2500);
    await expect(page.locator('.asset-card[data-preserved-marker="active"]')).toBeVisible();
    await expect(page.locator('#asset-status')).not.toHaveText('Loading assets…');
});

test('keyboard modal closes cleanly via cancel button and Escape and tab nav omits #open-preview', async ({
    canvas,
    page,
}) => {
    await mountRemote(canvas, { viewport: { width: 390, height: 844 } });
    await waitForRemoteReady(page);
    expect(await page.locator('#open-preview').count()).toBe(0);

    const modal = page.locator('#command-modal');
    const keyboardBtn = page.getByRole('button', { name: 'Keyboard' });
    await expect(modal).toBeHidden();
    await keyboardBtn.click();
    await expect(modal).toBeVisible();
    await expect(keyboardBtn).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('#command')).toBeFocused();

    await page.locator('#command-cancel-btn').click();
    await expect(modal).toBeHidden();
    await expect(keyboardBtn).toHaveAttribute('aria-expanded', 'false');

    await keyboardBtn.click();
    await expect(modal).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(modal).toBeHidden();
});

test('microphone streams audio via WebSocket from Watch Story preview too', async ({ canvas, page }) => {
    const agentMessages: string[] = [];

    await mountRemote(canvas, {
        viewport: { width: 390, height: 844 },
        onWebSocket: (ws) => {
            if (ws.url().includes('/agent')) {
                ws.onMessage((msg) => {
                    if (typeof msg === 'string') {
                        agentMessages.push(msg);
                    }
                });
                ws.send(JSON.stringify({ setupComplete: true }));
            }
        },
    });
    await waitForRemoteReady(page);

    await page.getByRole('tab', { name: 'Watch', exact: true }).click();
    await page.getByRole('tab', { name: 'Story', exact: true }).click();

    await page.evaluate(() => {
        interface MockWin {
            AudioEncoder: unknown;
            AudioData: unknown;
            AudioContext: unknown;
            AudioWorkletNode: unknown;
        }
        const win = window as unknown as MockWin;
        win.AudioEncoder = class {
            state = 'configured';
            configure(): void {}
            encode(): void {}
            flush(): Promise<void> {
                return Promise.resolve();
            }
            close(): void {
                this.state = 'closed';
            }
        };
        win.AudioData = class {
            close(): void {}
        };
        Object.defineProperty(navigator, 'mediaDevices', {
            value: {
                getUserMedia: () => Promise.resolve({ getTracks: () => [{ stop(): void {} }] }),
            },
            configurable: true,
        });
        win.AudioContext = class {
            state = 'running';
            destination = {};
            audioWorklet = { addModule: () => Promise.resolve() };
            createMediaStreamSource(): { connect(): void } {
                return { connect(): void {} };
            }
            close(): Promise<void> {
                this.state = 'closed';
                return Promise.resolve();
            }
        };
        win.AudioWorkletNode = class {
            port: { onmessage: ((ev: { data: Float32Array }) => void) | null } = { onmessage: null };
            connect(): void {}
            disconnect(): void {}
        };
    });

    const micBtn = page.getByRole('button', { name: 'Microphone' });
    await micBtn.click();
    await expect(micBtn).toHaveAttribute('aria-pressed', 'true');
    await expect(micBtn).toHaveText('Listening…');
    await micBtn.click();
    await expect(micBtn).toHaveAttribute('aria-pressed', 'false');
    await expect(micBtn).toHaveText('Microphone');

    const types = agentMessages
        .filter((m) => m.startsWith('{'))
        .map((m) => (JSON.parse(m) as { type?: string }).type);
    expect(types).toContain('activity_start');
    expect(types).toContain('activity_end');
});

test('party tab renders orator/lists, declines baton requests, and gates contributor transitions', async ({
    canvas,
    page,
}) => {
    const declinePayloads: string[] = [];
    const handle = await mountRemote(canvas, {
        viewport: { width: 390, height: 844 },
        authUser: { id: 2, username: 'bob' },
        remoteState: {
            agent_running: true,
            is_owner: false,
            is_orator: false,
            is_contributor: true,
        },
        batonState: {
            owner: { id: 1, username: 'alice' },
            active_orator: { id: 1, username: 'alice' },
            contributors: [{ id: 2, username: 'bob' }],
            active_viewers: [{ id: 3, username: 'charlie' }],
            baton_request: {
                target_user_id: 2,
                target_username: 'bob',
                timeout_seconds: 30,
            },
        },
        onRoute: async (url, route) => {
            if (url.pathname.endsWith('/baton/decline')) {
                declinePayloads.push(route.request().method());
                handle.batonState = { ...handle.batonState, baton_request: null };
                await route.fulfill({ json: handle.batonState });
                return true;
            }
            return false;
        },
    });

    const modal = page.locator('#baton-request-modal');
    await expect(modal).toBeVisible();
    await expect(page.locator('#baton-request-modal-title')).toHaveText('Baton Pass Request!');
    await expect(page.locator('#baton-timer-bar')).toBeVisible();
    await page.locator('#decline-baton-btn').click();
    await expect(modal).toBeHidden();
    expect(declinePayloads).toEqual(['POST']);

    // Switch to owner holding baton and inspect Party display details.
    handle.authUser = { id: 1, username: 'alice' };
    handle.remoteState = {
        agent_running: true,
        is_owner: true,
        is_orator: true,
        is_contributor: true,
    };
    await page.reload();
    await waitForRemoteReady(page);
    await page.getByRole('tab', { name: 'Party', exact: true }).click();
    await expect(page.locator('#remote-baton')).toBeVisible();
    await expect(page.locator('#baton-holder-display')).toHaveText('👑 Orator: @alice');
    await expect(page.locator('#menu-take-back-baton-btn')).toBeHidden();
    await expect(page.locator('#active-viewers-list')).toContainText('@charlie');
    await expect(page.locator('#contributors-list')).toContainText('@bob');
});

test('login gate opens login modal, authenticates, and reloads original theater URL', async ({
    canvas,
    page,
}) => {
    const loginHtml = await fs.promises.readFile(path.join(TEMPLATES_DIR, 'remote_login.html'), 'utf-8');
    let loggedIn = false;

    await canvas.serveRoute(
        (url: URL) => url.origin === CANVAS_ORIGIN && !url.pathname.startsWith('/static/'),
        async (route: Route) => {
            const url = new URL(route.request().url());
            if (url.pathname === '/remote') {
                await route.fulfill({
                    body: loggedIn ? 'Signed in' : loginHtml,
                    contentType: 'text/html; charset=utf-8',
                });
                return;
            }
            if (url.pathname === '/api/auth/me') {
                await route.fulfill({
                    json: {
                        authenticated: loggedIn,
                        user: loggedIn ? { id: 2, username: 'Orator' } : null,
                    },
                });
                return;
            }
            if (url.pathname === '/api/auth/login') {
                loggedIn = true;
                await route.fulfill({ json: { user: { id: 2, username: 'Orator' } } });
                return;
            }
            await route.abort();
        },
    );

    await page.goto(`${CANVAS_ORIGIN}/remote?theater_id=stage`);
    await page.locator('#loginUserEmail').fill('Orator');
    await page.locator('#loginPassword').fill('password');
    await page.getByRole('button', { name: 'Log In', exact: true }).click();
    await expect(page.getByText('Signed in', { exact: true })).toBeVisible();
    expect(page.url()).toBe(`${CANVAS_ORIGIN}/remote?theater_id=stage`);
});

for (const [width, userAgent, shouldRedirect] of [
    [390, 'Desktop', true],
    [1280, 'Desktop', false],
    [900, 'iPhone Mobile', true],
] as const) {
    test(`canvas device navigation redirects mobile/narrow (${width}, ${userAgent}) -> ${shouldRedirect}`, async ({
        browser,
    }) => {
        const templateHtml = await fs.promises.readFile(path.join(TEMPLATES_DIR, 'canvas.html'), 'utf-8');
        const head = templateHtml.slice(0, templateHtml.indexOf('<link rel="icon"'));
        const navigationJs = await fs.promises.readFile(
            path.resolve(TEMPLATES_DIR, '..', 'static', 'js', 'device-navigation.js'),
            'utf-8',
        );
        const generatedNavigationJs = await fs.promises.readFile(
            path.resolve(TEMPLATES_DIR, '..', 'static', 'generated', 'canvas', 'device-navigation.js'),
            'utf-8',
        );

        const context = await browser.newContext({ viewport: { width, height: 844 }, userAgent });
        const page = await context.newPage();
        try {
            await page.route('**/*', async (route: Route) => {
                const url = new URL(route.request().url());
                if (url.pathname === '/static/js/device-navigation.js') {
                    await route.fulfill({ body: navigationJs, contentType: 'text/javascript; charset=utf-8' });
                } else if (url.pathname === '/static/generated/canvas/device-navigation.js') {
                    await route.fulfill({
                        body: generatedNavigationJs,
                        contentType: 'text/javascript; charset=utf-8',
                    });
                } else if (url.pathname === '/canvas') {
                    await route.fulfill({
                        body: `${head}</head><body>Canvas</body>`,
                        contentType: 'text/html; charset=utf-8',
                    });
                } else {
                    await route.fulfill({ body: 'Remote', contentType: 'text/html; charset=utf-8' });
                }
            });

            await page.goto(`${CANVAS_ORIGIN}/canvas?theater_id=stage&join_key=SECRET`);
            if (shouldRedirect) {
                await page.waitForURL(`${CANVAS_ORIGIN}/remote?theater_id=stage`);
            } else {
                expect(page.url()).toBe(`${CANVAS_ORIGIN}/canvas?theater_id=stage&join_key=SECRET`);
            }
        } finally {
            await context.close();
        }
    });
}

test.describe('mobile touch gestures and integrated watch audio', () => {
    test.use({ hasTouch: true, isMobile: true });

    async function swipePreview(page: Page, dx: number, dy: number, cancel = false): Promise<void> {
        const previewFrame = page.locator('.preview-frame');
        await expect(previewFrame).toBeVisible();
        const frame = await previewFrame.boundingBox();
        expect(frame).not.toBeNull();
        const x = frame!.x + frame!.width / 2;
        const y = frame!.y + frame!.height / 2;
        const session = await page.context().newCDPSession(page);
        try {
            await session.send('Input.dispatchTouchEvent', {
                type: 'touchStart',
                touchPoints: [{ x, y }],
            });
            await session.send('Input.dispatchTouchEvent', {
                type: 'touchMove',
                touchPoints: [{ x: x + dx, y: y + dy }],
            });
            await session.send('Input.dispatchTouchEvent', {
                type: cancel ? 'touchCancel' : 'touchEnd',
                touchPoints: [],
            });
        } finally {
            await session.detach();
        }
    }

    test('CDP touch swipes on preview frame switch tabs, preserve draft, and ignore vertical/diagonal/small/cancelled gestures', async ({
        canvas,
        page,
    }) => {
        await mountRemote(canvas, { viewport: { width: 390, height: 844 } });
        await waitForRemoteReady(page);

        await page.getByRole('button', { name: 'Keyboard' }).click();
        await page.locator('#command').fill('Keep my draft');
        await page.locator('#command-modal-close').click();

        await page.getByRole('tab', { name: 'Watch', exact: true }).click();
        for (const [dx, dy, cancel] of [
            [0, 30, false],
            [0, -140, false],
            [10, 140, false],
            [100, 100, false],
            [140, 0, true],
        ] as const) {
            await swipePreview(page, dx, dy, cancel);
            await expect(page.getByRole('tab', { name: 'Watch', exact: true })).toHaveAttribute(
                'aria-selected',
                'true',
            );
        }

        await swipePreview(page, 140, 10);
        await expect(page.getByRole('tab', { name: 'Remote' })).toHaveAttribute('aria-selected', 'true');
        await swipePanel(page, 140, 10);
        await expect(page.getByRole('tab', { name: 'Assets' })).toHaveAttribute('aria-selected', 'true');
        await swipePanel(page, -140, 10);
        await expect(page.getByRole('tab', { name: 'Remote' })).toHaveAttribute('aria-selected', 'true');
        await expect(page.locator('#command')).toHaveValue('Keep my draft');
        await swipePanel(page, -140, 10);
        await expect(page.getByRole('tab', { name: 'Watch', exact: true })).toHaveAttribute(
            'aria-selected',
            'true',
        );
        await swipePanel(page, -140, 10);
        await expect(page.getByRole('tab', { name: 'Party', exact: true })).toHaveAttribute(
            'aria-selected',
            'true',
        );
    });

    test('watch audio integrates WS speech queueing, music transitions across Remote/Watch tabs, and real media playback', async ({
        canvas,
        page,
    }) => {
        const desertMp3 = await fs.promises.readFile(
            path.resolve(TEMPLATES_DIR, '..', 'testing', 'testdata', 'playlists', 'cold desert.mp3'),
        );
        let canvasSocket: WebSocketRoute | null = null;
        let speechGen = 1;

        await page.addInitScript(() => {
            interface WatchAudioRecord {
                src: string;
                muted: boolean;
                paused: boolean;
                currentTime: number;
                getAttribute(name: string): string | null;
            }
            const created: WatchAudioRecord[] = [];
            (window as unknown as { watchAudio: WatchAudioRecord[] }).watchAudio = created;
            const NativeAudio = window.Audio;
            window.Audio = function (this: HTMLAudioElement, ...args: [string?]): HTMLAudioElement {
                const audio = new NativeAudio(...args);
                created.push(audio as unknown as WatchAudioRecord);
                return audio;
            } as unknown as typeof Audio;
        });

        const handle = await mountRemote(canvas, {
            viewport: { width: 390, height: 844 },
            query: '?theater_id=stage&audio_test=1#preview',
            remoteState: () => ({
                agent_running: true,
                is_owner: true,
                is_orator: true,
                music: { music_id: 'desert', tracks: ['/audio/desert.mp3'], paused: false },
                committed_scene_speech_generation: speechGen,
            }),
            onRoute: async (url, route) => {
                if (url.pathname === '/audio/desert.mp3' || url.pathname.startsWith('/speech/')) {
                    await route.fulfill({ body: desertMp3, contentType: 'audio/mpeg' });
                    return true;
                }
                return false;
            },
            onWebSocket: (ws) => {
                if (ws.url().includes('/ws/canvas-state')) {
                    canvasSocket = ws;
                }
            },
        });

        await page.waitForFunction(() => {
            const win = window as unknown as { watchAudio: Array<{ getAttribute(name: string): string | null }> };
            return win.watchAudio[0]?.getAttribute('src') === '/audio/desert.mp3';
        });
        expect(
            await page.evaluate(() =>
                (window as unknown as { watchAudio: Array<{ muted: boolean }> }).watchAudio.every((a) => a.muted),
            ),
        ).toBe(true);

        // Tap unmutes and plays real audio; Space/Enter toggle state.
        await page.getByRole('button', { name: 'Unmute canvas audio' }).tap();
        await page.waitForFunction(() => {
            const win = window as unknown as {
                watchAudio: Array<{ muted: boolean; paused: boolean; currentTime: number }>;
            };
            const music = win.watchAudio[0];
            return Boolean(music && !music.muted && !music.paused && music.currentTime > 0);
        });

        // Unmuted audio stays playing across Remote -> Watch tab switches.
        await page.getByRole('tab', { name: 'Remote', exact: true }).click();
        expect(
            await page.evaluate(() => {
                const win = window as unknown as { watchAudio: Array<{ muted: boolean; paused: boolean }> };
                return !win.watchAudio[0].paused && !win.watchAudio[0].muted;
            }),
        ).toBe(true);
        await page.getByRole('tab', { name: 'Watch', exact: true }).click();

        // Pre-commit WS speech waits until committed generation advances via state_changed.
        expect(canvasSocket).not.toBeNull();
        canvasSocket!.send(
            JSON.stringify({ type: 'scene_speech_ready', speech_generation: 2, audio_url: '/speech/first.mp3' }),
        );
        await page.waitForTimeout(100);
        expect(
            await page.evaluate(() =>
                (window as unknown as { watchAudio: Array<{ getAttribute(name: string): string | null }> })
                    .watchAudio[1]?.getAttribute('src'),
            ),
        ).toBeNull();

        speechGen = 2;
        canvasSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
        await page.waitForFunction(() => {
            const win = window as unknown as {
                watchAudio: Array<{ getAttribute(n: string): string | null; paused: boolean; muted: boolean; currentTime: number }>;
            };
            const speech = win.watchAudio[1];
            return Boolean(
                speech &&
                    speech.getAttribute('src') === '/speech/first.mp3' &&
                    !speech.muted &&
                    !speech.paused &&
                    speech.currentTime > 0,
            );
        });

        // Advancing generation clears active speech; muted hidden speech stays paused and unqueued.
        speechGen = 3;
        canvasSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
        await page.waitForFunction(() => {
            const win = window as unknown as {
                watchAudio: Array<{ getAttribute(n: string): string | null; paused: boolean }>;
            };
            const speech = win.watchAudio[1];
            return Boolean(speech && speech.paused && !speech.getAttribute('src'));
        });

        await page.getByRole('button', { name: 'Mute canvas audio' }).tap();
        expect(
            await page.evaluate(() =>
                (window as unknown as { watchAudio: Array<{ muted: boolean }> }).watchAudio.every((a) => a.muted),
            ),
        ).toBe(true);
        await page.getByRole('tab', { name: 'Remote', exact: true }).click();
        await page.waitForFunction(() =>
            (window as unknown as { watchAudio: Array<{ paused: boolean }> }).watchAudio.every((a) => a.paused),
        );
        canvasSocket!.send(
            JSON.stringify({ type: 'scene_speech_ready', speech_generation: 3, audio_url: '/speech/muted-hidden.mp3' }),
        );
        await page.getByRole('tab', { name: 'Watch', exact: true }).click();
        expect(
            await page.evaluate(() => {
                const win = window as unknown as {
                    watchAudio: Array<{ getAttribute(n: string): string | null; paused: boolean }>;
                };
                const speech = win.watchAudio[1];
                return Boolean(speech && speech.paused && !speech.getAttribute('src'));
            }),
        ).toBe(true);
        expect(handle.remoteState.agent_running).toBe(true);
    });
});


