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
    await page.getByRole('tab', { name: 'Watch', exact: true }).click();
    await expect(page.locator('#preview-story-tab')).toBeHidden();

    // Keyboard tab cycling skips hidden Story tab.
    await page.getByRole('tab', { name: 'Chat', exact: true }).press('ArrowRight');
    await expect(page.locator('#preview-thought-panel')).toBeVisible();
    await page.getByRole('tab', { name: 'Agent', exact: true }).press('ArrowRight');
    await expect(page.locator('#preview-chat-panel')).toBeVisible();

    // Enabling adventure mode reveals story direction and moves composer into Story tab.
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

    await page.getByRole('button', { name: 'Turn agent off' }).click();
    await waitForRemoteReady(page);
    await expect(page.locator('#connection-status')).toHaveText('Connected · Agent off');
    await expect(page.getByRole('button', { name: 'New image' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Keyboard' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Microphone' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Previous scene', exact: true })).toBeEnabled();
    await expect(page.getByRole('button', { name: 'Unpin scene' })).toHaveAttribute('aria-pressed', 'true');

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
    await agentTab.click();
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

    // Revoking access disconnects and clears preview image.
    handle.setRemoteStatusOverride(async (route) => {
        await route.fulfill({ status: 403, json: { detail: 'Only the active orator can use this remote.' } });
    });
    await expect(page.locator('#connection-status')).toHaveAttribute('data-connected', 'false');
    await expect(page.locator('#preview-placeholder')).toHaveText('Reconnect to see the canvas.');
    await expect(page.locator('#preview-image')).toBeHidden();
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

    expect(batonRequests.map((r) => r.path)).toEqual([
        '/api/theaters/stage/baton/accept',
        '/api/theaters/stage/baton/contributors',
        '/api/theaters/stage/baton/request',
        '/api/theaters/stage/baton/takeback',
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

