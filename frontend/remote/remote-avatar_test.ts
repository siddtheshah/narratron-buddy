import fs from 'node:fs';
import path from 'node:path';
import type { Route, WebSocketRoute } from '@playwright/test';
import {
    CANVAS_ORIGIN,
    REPO_ROOT,
    TEMPLATES_DIR,
    expect,
    test,
    type CanvasHarness,
} from '../../testing/ui/canvas_fixtures';
import type { RemoteStatusState } from './remote.js';

test.use({ launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] } });
test.describe.configure({ mode: 'serial' });

const CUBE_MODEL_PATH = path.join(REPO_ROOT, 'testlab', 'avatar_packages', 'cube', 'model.glb');
const STAGE_AVATAR_ROUTE = '/theaters/stage/avatars/mobile-companion';

const TEST_AVATAR_MANIFEST = {
    id: 'mobile-companion',
    title: 'Mobile Companion',
    model: 'model.glb',
    idle: 'idle',
    reactions: { activate: 'greeting' },
    actions: [
        { id: 'idle', label: 'Idle', clip: 'Hover', loop: true, lightColor: '#33ccee' },
        { id: 'greeting', label: 'Greeting', clip: 'PokeBounce', loop: false, lightColor: '#ffaa44' },
        { id: 'celebrate', label: 'Celebrate', clip: 'Bow', loop: false, lightColor: '#ff77cc' },
        { id: 'at-work', label: 'At Work', clip: 'Chatter', loop: true, lightColor: '#44ff88' },
    ],
};

interface MobileAvatarHarness {
    remoteState: RemoteStatusState;
    getStateSocket(): WebSocketRoute | null;
    getManifestRequests(): number;
}

async function mountMobileWithAvatar(
    canvas: CanvasHarness,
    initialState: RemoteStatusState,
    viewport = { width: 390, height: 844 },
): Promise<MobileAvatarHarness> {
    const templateHtml = await fs.promises.readFile(path.join(TEMPLATES_DIR, 'remote.html'), 'utf-8');
    let remoteState: RemoteStatusState = initialState;
    let stateSocket: WebSocketRoute | null = null;
    let manifestRequests = 0;

    await canvas.page.setViewportSize(viewport);

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
            if (url.pathname === `${STAGE_AVATAR_ROUTE}/avatar.json`) {
                manifestRequests += 1;
                await route.fulfill({ json: TEST_AVATAR_MANIFEST });
                return;
            }
            if (url.pathname === `${STAGE_AVATAR_ROUTE}/model.glb`) {
                await route.fulfill({ path: CUBE_MODEL_PATH });
                return;
            }
            if (url.pathname === '/api/theaters/stage/remote') {
                await route.fulfill({ json: remoteState });
                return;
            }
            if (url.pathname === '/api/auth/me') {
                await route.fulfill({ json: { authenticated: true, user: { id: 2, username: 'viewer' } } });
                return;
            }
            if (url.pathname === '/api/theaters/stage/baton') {
                await route.fulfill({
                    json: {
                        owner: { id: 1, username: 'owner' },
                        active_orator: { id: 1, username: 'owner' },
                        contributors: [],
                        active_viewers: [],
                        baton_request: null,
                    },
                });
                return;
            }
            if (url.pathname === '/api/chat') {
                await route.fulfill({ json: [] });
                return;
            }
            await route.fulfill({ json: {} });
        },
    );

    await canvas.page.routeWebSocket(/wss?:\/\/canvas\.test\/ws\/.*/, (ws: WebSocketRoute) => {
        const pathname = new URL(ws.url()).pathname;
        if (pathname === '/ws/canvas-state') {
            stateSocket = ws;
            ws.send(JSON.stringify({ type: 'state_ready' }));
        }
    });

    await canvas.page.goto(`${CANVAS_ORIGIN}/remote?theater_id=stage`);

    return {
        get remoteState() {
            return remoteState;
        },
        set remoteState(next: RemoteStatusState) {
            remoteState = next;
        },
        getStateSocket: () => stateSocket,
        getManifestRequests: () => manifestRequests,
    };
}

test('remote.html places the Three.js importmap before any module script', async () => {
    const templateHtml = await fs.promises.readFile(path.join(TEMPLATES_DIR, 'remote.html'), 'utf-8');
    const importMapIndex = templateHtml.indexOf('<script type="importmap">');
    const firstModuleIndex = templateHtml.search(/<script\b[^>]*\btype=["']module["']/i);
    expect(importMapIndex).toBeGreaterThan(-1);
    expect(firstModuleIndex).toBeGreaterThan(importMapIndex);
});

test('watch agent subtab displays avatar, plays latest animation on open, and reacts on tap', async ({
    canvas,
    page,
}) => {
    const handle = await mountMobileWithAvatar(canvas, {
        agent_running: true,
        is_owner: false,
        is_orator: false,
        is_contributor: false,
        agent_thought: { text: 'Planning the next twist…', highlighted: false },
        avatar: {
            enabled: true,
            package: 'mobile-companion',
            action: 'celebrate',
            sequence: 2,
            time: 200,
        },
    });

    await expect.poll(() => handle.getStateSocket() !== null).toBe(true);
    // Pure viewer lands on Watch -> Chat subtab; avatar is not mounted until Agent subtab opens.
    await expect(page.locator('#preview-chat-panel')).toBeVisible();
    await expect(page.locator('#remote-avatar')).toBeHidden();
    expect(handle.getManifestRequests()).toBe(0);

    // Opening Watch -> Agent subtab displays the avatar in the bottom box and plays its latest animation.
    await page.getByRole('tab', { name: 'Agent', exact: true }).click();
    const avatarMount = page.locator('#remote-avatar');
    await expect(avatarMount.locator('canvas')).toBeVisible();
    expect(handle.getManifestRequests()).toBe(1);

    await expect.poll(() => page.evaluate(() => window.__avatarPlayback?.currentAction)).toBe('celebrate');
    // One-shot animation finishes and settles back to idle.
    await expect.poll(() => page.evaluate(() => window.__avatarPlayback?.currentAction), { timeout: 5000 }).toBe('idle');

    // Attach action event recorder to verify deduplication, live updates, tap reaction, and reopen replay.
    await page.evaluate(() => {
        const events: string[] = [];
        (window as unknown as { __mobileAvatarEvents?: string[] }).__mobileAvatarEvents = events;
        window.__avatarPlayback?.onActionChange((id) => events.push(id));
    });

    // Polling with the same sequence does not replay the animation while staying on the subtab.
    handle.getStateSocket()!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
    await page.waitForTimeout(250);
    expect(await page.evaluate(() => (window as unknown as { __mobileAvatarEvents?: string[] }).__mobileAvatarEvents ?? [])).toEqual([]);

    // Tapping the avatar plays the standard onclick reaction ('greeting'), then returns to idle.
    await avatarMount.click();
    await expect.poll(() => page.evaluate(() => window.__avatarPlayback?.currentAction)).toBe('greeting');
    await expect.poll(() => page.evaluate(() => window.__avatarPlayback?.currentAction), { timeout: 5000 }).toBe('idle');

    // A newly pushed sequence while the subtab is open plays immediately.
    handle.remoteState = {
        ...handle.remoteState,
        avatar: {
            enabled: true,
            package: 'mobile-companion',
            action: 'celebrate',
            sequence: 3,
            time: 300,
        },
    };
    handle.getStateSocket()!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
    await expect.poll(() => page.evaluate(() => window.__avatarPlayback?.currentAction)).toBe('celebrate');
    await expect.poll(() => page.evaluate(() => window.__avatarPlayback?.currentAction), { timeout: 5000 }).toBe('idle');

    // Leaving the Agent subtab and reopening it replays the latest animation ('celebrate').
    await page.getByRole('tab', { name: 'Chat', exact: true }).click();
    await expect(avatarMount).toBeHidden();

    await page.getByRole('tab', { name: 'Agent', exact: true }).click();
    await expect(avatarMount.locator('canvas')).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.__avatarPlayback?.currentAction)).toBe('celebrate');
    expect(handle.getManifestRequests()).toBe(1);
});

test('mobile avatar is only visible while the agent is summoned and fits mobile viewports', async ({
    canvas,
    page,
}) => {
    const handle = await mountMobileWithAvatar(canvas, {
        agent_running: false,
        is_owner: false,
        is_orator: false,
        is_contributor: false,
        agent_thought: { text: '', highlighted: false },
        avatar: {
            enabled: true,
            package: 'mobile-companion',
            action: 'at-work',
            sequence: 1,
            time: 100,
        },
    });

    await expect.poll(() => handle.getStateSocket() !== null).toBe(true);
    await page.getByRole('tab', { name: 'Agent', exact: true }).click();
    const avatarStage = page.locator('#remote-avatar-stage');
    const avatarMount = page.locator('#remote-avatar');

    // Hidden while the agent is not summoned.
    await expect(avatarStage).toBeHidden();
    await expect(avatarMount).toBeHidden();
    expect(handle.getManifestRequests()).toBe(0);

    // Summoning shows the avatar in the Agent subtab and plays its latest animation.
    handle.remoteState = { ...handle.remoteState, agent_running: true };
    handle.getStateSocket()!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
    await expect(avatarMount.locator('canvas')).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.__avatarPlayback?.currentAction)).toBe('at-work');

    // Verify clean square sizing without horizontal overflow across mobile portrait & landscape viewports.
    for (const [width, height] of [
        [390, 844],
        [320, 568],
        [390, 480],
        [844, 390],
    ] as const) {
        await page.setViewportSize({ width, height });
        const box = await avatarMount.boundingBox();
        expect(box).not.toBeNull();
        expect(box!.width).toBeGreaterThan(0);
        expect(box!.width).toBeCloseTo(box!.height, 1);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }

    // Banishing hides the avatar again.
    handle.remoteState = { ...handle.remoteState, agent_running: false };
    handle.getStateSocket()!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
    await expect(avatarStage).toBeHidden();
    await expect(avatarMount).toBeHidden();
});
