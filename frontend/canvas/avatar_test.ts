import path from 'node:path';
import type { WebSocketRoute } from '@playwright/test';
import { expect, REPO_ROOT, test } from '../testing/canvas_fixtures';

test.use({ launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] } });
test.describe.configure({ mode: 'serial' });

const avatarPath = path.join(REPO_ROOT, 'adventures', 'example_adventure', 'avatars', 'light-cube');
const avatarRoute = '/theaters/stage/avatars/light-cube';

test('the adventure avatar turns, brightens on hover, and dims when the pointer leaves', async ({ canvas, page }) => {
  await canvas.openCanvas({
    viewport: { width: 1440, height: 900 },
    apiRoutes: {
      '/api/theaters/stage': { metadata: { config: { avatar: { enabled: true, package: 'light-cube' } }, is_active_orator: true } },
      '/api/theaters/stage/agent/status': { agent_running: true },
      '/api/latest': { agent_running: true },
      [`${avatarRoute}/avatar.json`]: (route) => route.fulfill({ path: path.join(avatarPath, 'avatar.json') }),
      [`${avatarRoute}/model.glb`]: (route) => route.fulfill({ path: path.join(avatarPath, 'model.glb') }),
      [`${avatarRoute}/shaders/dust.vert`]: (route) => route.fulfill({ path: path.join(avatarPath, 'shaders', 'dust.vert') }),
      [`${avatarRoute}/shaders/dust.frag`]: (route) => route.fulfill({ path: path.join(avatarPath, 'shaders', 'dust.frag') }),
    },
  });
  const mount = page.getByRole('img', { name: 'Cube of Light' });
  const model = mount.locator('canvas');
  await expect(model).toBeVisible();
  const avatarBox = (await mount.boundingBox())!;
  const controlsBox = (await page.locator('.canvas-corner-bottom-right').boundingBox())!;
  expect(avatarBox.width).toBeCloseTo(9 * 16 * 0.8, 1);
  expect(avatarBox.y + avatarBox.height).toBeLessThan(controlsBox.y);
  const turning = await model.screenshot();
  await expect.poll(async () => (await model.screenshot()).equals(turning)).toBe(false);

  // Hold the angle steady so only the emissive materials and shader glow can change the canvas.
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const resting = await model.screenshot();
  await mount.hover();
  await expect.poll(async () => (await model.screenshot()).equals(resting)).toBe(false);
  await page.mouse.move(0, 0);
  await expect.poll(async () => (await model.screenshot()).equals(resting)).toBe(true);
});

test('minimal mode skips avatar shaders on load and disables them when toggled on', async ({ canvas, page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const shaderRequests: string[] = [];
  await canvas.openCanvas({
    viewport: { width: 1440, height: 900 },
    localStorage: { 'narratron.canvas.performance.minimalMode': 'true' },
    apiRoutes: {
      '/api/theaters/stage': { metadata: { config: { avatar: { enabled: true, package: 'light-cube' } }, is_active_orator: true } },
      '/api/theaters/stage/agent/status': { agent_running: true },
      '/api/latest': { agent_running: true },
      [`${avatarRoute}/avatar.json`]: (route) => route.fulfill({ path: path.join(avatarPath, 'avatar.json') }),
      [`${avatarRoute}/model.glb`]: (route) => route.fulfill({ path: path.join(avatarPath, 'model.glb') }),
      [`${avatarRoute}/shaders/dust.vert`]: (route) => {
        shaderRequests.push('dust.vert');
        return route.fulfill({ path: path.join(avatarPath, 'shaders', 'dust.vert') });
      },
      [`${avatarRoute}/shaders/dust.frag`]: (route) => {
        shaderRequests.push('dust.frag');
        return route.fulfill({ path: path.join(avatarPath, 'shaders', 'dust.frag') });
      },
    },
  });

  const mount = page.getByRole('img', { name: 'Cube of Light' });
  const model = mount.locator('canvas');
  await expect(model).toBeVisible();
  expect(shaderRequests).toEqual([]);
  const withoutShaders = await model.screenshot();

  await page.click('#menu-left-toggle-btn');
  const minimalInput = page.locator('#minimal-mode-input');
  await minimalInput.uncheck();
  await page.click('#pullout-close-btn');
  await expect.poll(() => shaderRequests.slice().sort()).toEqual(['dust.frag', 'dust.vert']);
  await expect.poll(async () => (await model.screenshot()).equals(withoutShaders)).toBe(false);

  await page.click('#menu-left-toggle-btn');
  await minimalInput.check();
  await page.click('#pullout-close-btn');
  await expect.poll(async () => (await model.screenshot()).equals(withoutShaders)).toBe(true);
});

test('light-cube playback supports selectColor and playAction backwards-compatibly', async ({ canvas, page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await canvas.openCanvas({
    viewport: { width: 1440, height: 900 },
    apiRoutes: {
      '/api/theaters/stage': { metadata: { config: { avatar: { enabled: true, package: 'light-cube' } }, is_active_orator: true } },
      '/api/theaters/stage/agent/status': { agent_running: true },
      '/api/latest': { agent_running: true },
      [`${avatarRoute}/avatar.json`]: (route) => route.fulfill({ path: path.join(avatarPath, 'avatar.json') }),
      [`${avatarRoute}/model.glb`]: (route) => route.fulfill({ path: path.join(avatarPath, 'model.glb') }),
      [`${avatarRoute}/shaders/dust.vert`]: (route) => route.fulfill({ path: path.join(avatarPath, 'shaders', 'dust.vert') }),
      [`${avatarRoute}/shaders/dust.frag`]: (route) => route.fulfill({ path: path.join(avatarPath, 'shaders', 'dust.frag') }),
    },
  });

  const model = page.getByRole('img', { name: 'Cube of Light' }).locator('canvas');
  await expect(model).toBeVisible();
  await expect.poll(() => page.evaluate(() => Boolean(window.__avatarPlayback))).toBe(true);

  const goldFrame = await model.screenshot();
  const selectedCyan = await page.evaluate(() => window.__avatarPlayback?.selectColor('cyan') ?? false);
  expect(selectedCyan).toBe(true);
  await expect.poll(async () => (await model.screenshot()).equals(goldFrame)).toBe(false);

  const cyanFrame = await model.screenshot();
  const playedEmber = await page.evaluate(() => window.__avatarPlayback?.playAction('ember') ?? false);
  expect(playedEmber).toBe(true);
  await expect.poll(async () => (await model.screenshot()).equals(cyanFrame)).toBe(false);
});

test('avatar action playback switches clips, returns one-shots to idle, and triggers activation reaction', async ({ canvas, page }) => {
  const cubeModelPath = path.join(REPO_ROOT, 'testlab', 'assets', 'avatar_packages', 'cube', 'model.glb');
  const stageAvatarRoute = '/theaters/stage/avatars/multi-action';
  const manifest = {
    id: 'multi-action',
    title: 'Multi Action Avatar',
    model: 'model.glb',
    idle: 'idle',
    reactions: { activate: 'greeting' },
    actions: [
      { id: 'idle', label: 'Idle', clip: 'Hover', loop: true, lightColor: '#33ccee' },
      { id: 'greeting', label: 'Greeting', clip: 'PokeBounce', loop: false, lightColor: '#ffaa44' },
      { id: 'at-work', label: 'At Work', clip: 'Chatter', loop: true, lightColor: '#44ff88' },
    ],
    shaders: [
      {
        geometry: { type: 'plane', width: 0.5, height: 0.5 },
        position: [0, -0.75, 0],
        vertex: 'shaders/badge.vert',
        fragment: 'shaders/badge.frag',
        transparent: false,
        uniforms: {
          pulseStrength: { source: 'actionPulse' },
        },
      },
    ],
  };

  await canvas.openCanvas({
    viewport: { width: 1440, height: 900 },
    apiRoutes: {
      '/api/theaters/stage': { metadata: { config: { avatar: { enabled: true, package: 'multi-action' } }, is_active_orator: true } },
      '/api/theaters/stage/agent/status': { agent_running: true },
      '/api/latest': { agent_running: true },
      [`${stageAvatarRoute}/avatar.json`]: manifest,
      [`${stageAvatarRoute}/model.glb`]: (route) => route.fulfill({ path: cubeModelPath }),
      [`${stageAvatarRoute}/shaders/badge.vert`]: (route) => route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: 'void main() { gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
      }),
      [`${stageAvatarRoute}/shaders/badge.frag`]: (route) => route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: 'uniform vec3 uColor; uniform float pulseStrength; void main() { gl_FragColor = vec4(clamp(uColor + vec3(pulseStrength * 0.4), 0.0, 1.0), 1.0); }',
      }),
    },
  });

  const mount = page.getByRole('img', { name: 'Multi Action Avatar' });
  await expect(mount.locator('canvas')).toBeVisible();
  await expect.poll(() => page.evaluate(() => Boolean(window.__avatarPlayback))).toBe(true);

  const initialInfo = await page.evaluate(() => {
    const playback = window.__avatarPlayback!;
    const events: string[] = [];
    (window as unknown as { __avatarEvents?: string[] }).__avatarEvents = events;
    playback.onActionChange((id) => events.push(id));
    return {
      idle: playback.idle,
      current: playback.currentAction,
      actionIds: playback.actions.map((action) => action.id),
      unknownOk: playback.playAction('nonexistent'),
    };
  });
  expect(initialInfo).toEqual({
    idle: 'idle',
    current: 'idle',
    actionIds: ['idle', 'greeting', 'at-work'],
    unknownOk: false,
  });

  // Looping action stays active rather than returning to idle.
  const startedWork = await page.evaluate(() => window.__avatarPlayback?.playAction('at-work') ?? false);
  expect(startedWork).toBe(true);
  await page.waitForTimeout(1300);
  expect(await page.evaluate(() => window.__avatarPlayback?.currentAction)).toBe('at-work');

  // Clicking the mount triggers reactions.activate ('greeting'), which is a one-shot (~0.95s) that returns to idle.
  await mount.click();
  await expect.poll(() => page.evaluate(() => window.__avatarPlayback?.currentAction)).toBe('greeting');
  await expect.poll(() => page.evaluate(() => window.__avatarPlayback?.currentAction), { timeout: 5000 }).toBe('idle');

  const events = await page.evaluate(() => (window as unknown as { __avatarEvents?: string[] }).__avatarEvents ?? []);
  expect(events).toEqual(['at-work', 'greeting', 'idle']);
});

test('prefers-reduced-motion keeps idle steady and snaps action state without continuous motion', async ({ canvas, page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const cubeModelPath = path.join(REPO_ROOT, 'testlab', 'assets', 'avatar_packages', 'cube', 'model.glb');
  const stageAvatarRoute = '/theaters/stage/avatars/reduced-motion';
  const manifest = {
    id: 'reduced-motion',
    title: 'Reduced Motion Avatar',
    model: 'model.glb',
    idle: 'idle',
    actions: [
      { id: 'idle', label: 'Idle', clip: 'Hover', loop: true, lightColor: '#2266ff' },
      { id: 'greeting', label: 'Greeting', clip: 'Bow', loop: false, lightColor: '#ff3344' },
    ],
    shaders: [
      {
        geometry: { type: 'plane', width: 0.7, height: 0.7 },
        position: [0, -0.7, 0],
        vertex: 'shaders/swatch.vert',
        fragment: 'shaders/swatch.frag',
      },
    ],
  };

  await canvas.openCanvas({
    viewport: { width: 1440, height: 900 },
    apiRoutes: {
      '/api/theaters/stage': { metadata: { config: { avatar: { enabled: true, package: 'reduced-motion' } }, is_active_orator: true } },
      '/api/theaters/stage/agent/status': { agent_running: true },
      '/api/latest': { agent_running: true },
      [`${stageAvatarRoute}/avatar.json`]: manifest,
      [`${stageAvatarRoute}/model.glb`]: (route) => route.fulfill({ path: cubeModelPath }),
      [`${stageAvatarRoute}/shaders/swatch.vert`]: (route) => route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: 'void main() { gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
      }),
      [`${stageAvatarRoute}/shaders/swatch.frag`]: (route) => route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: 'uniform vec3 uColor; void main() { gl_FragColor = vec4(uColor, 1.0); }',
      }),
    },
  });

  const model = page.getByRole('img', { name: 'Reduced Motion Avatar' }).locator('canvas');
  await expect(model).toBeVisible();
  await expect.poll(() => page.evaluate(() => Boolean(window.__avatarPlayback))).toBe(true);

  const idleFrame = await model.screenshot();
  await page.waitForTimeout(350);
  expect((await model.screenshot()).equals(idleFrame)).toBe(true);

  await page.evaluate(() => window.__avatarPlayback?.playAction('greeting'));
  expect(await page.evaluate(() => window.__avatarPlayback?.currentAction)).toBe('greeting');
  await expect.poll(async () => (await model.screenshot()).equals(idleFrame)).toBe(false);

  const greetingFrame = await model.screenshot();
  await page.waitForTimeout(350);
  expect((await model.screenshot()).equals(greetingFrame)).toBe(true);
});

test('canvas_state avatar payload drives playback, deduplicates polls, and replays incremented sequences', async ({ canvas, page }) => {
  const cubeModelPath = path.join(REPO_ROOT, 'testlab', 'assets', 'avatar_packages', 'cube', 'model.glb');
  const stageAvatarRoute = '/theaters/stage/avatars/state-driven';
  const manifest = {
    id: 'state-driven',
    title: 'State Driven Avatar',
    model: 'model.glb',
    idle: 'idle',
    actions: [
      { id: 'idle', label: 'Idle', clip: 'Hover', loop: true, lightColor: '#33ccee' },
      { id: 'greeting', label: 'Greeting', clip: 'PokeBounce', loop: false, lightColor: '#ffaa44' },
      { id: 'at-work', label: 'At Work', clip: 'Chatter', loop: true, lightColor: '#44ff88' },
    ],
  };

  let avatarPayload: {
    enabled: boolean;
    package: string;
    action: string | null;
    sequence: number;
    time: number;
  } = {
    enabled: true,
    package: 'state-driven',
    action: 'at-work',
    sequence: 1,
    time: 100,
  };
  let stateSocket: WebSocketRoute | null = null;

  await canvas.openCanvas({
    viewport: { width: 1440, height: 900 },
    apiRoutes: {
      '/api/theaters/stage': { metadata: { config: { avatar: { enabled: true, package: 'state-driven' } }, is_active_orator: true } },
      '/api/theaters/stage/agent/status': { agent_running: true },
      '/api/latest': async (route) => {
        await route.fulfill({
          json: {
            agent_running: true,
            agent_thought: { text: 'Sketching the scene...', highlighted: false },
            avatar: avatarPayload,
          },
        });
      },
      [`${stageAvatarRoute}/avatar.json`]: manifest,
      [`${stageAvatarRoute}/model.glb`]: (route) => route.fulfill({ path: cubeModelPath }),
    },
    onWebSocket: (ws) => {
      if (new URL(ws.url()).pathname === '/ws/canvas-state') {
        stateSocket = ws;
        ws.send(JSON.stringify({ type: 'state_ready' }));
      }
    },
  });

  await expect.poll(() => stateSocket !== null).toBe(true);
  await expect(page.getByRole('img', { name: 'State Driven Avatar' }).locator('canvas')).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__avatarPlayback?.currentAction)).toBe('at-work');

  await page.evaluate(() => {
    const events: string[] = [];
    (window as unknown as { __stateAvatarEvents?: string[] }).__stateAvatarEvents = events;
    window.__avatarPlayback?.onActionChange((id) => events.push(id));
  });

  // Re-fetching /api/latest with the same sequence does not replay the action.
  stateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
  await page.waitForTimeout(250);
  expect(await page.evaluate(() => (window as unknown as { __stateAvatarEvents?: string[] }).__stateAvatarEvents ?? [])).toEqual([]);

  // Incrementing sequence plays the newly selected one-shot action, which returns to idle when finished.
  avatarPayload = { enabled: true, package: 'state-driven', action: 'greeting', sequence: 2, time: 200 };
  stateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
  await expect.poll(() => page.evaluate(() => window.__avatarPlayback?.currentAction)).toBe('greeting');
  await expect.poll(() => page.evaluate(() => window.__avatarPlayback?.currentAction), { timeout: 5000 }).toBe('idle');

  // Sending another poll with sequence 2 still does not replay greeting.
  stateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
  await page.waitForTimeout(250);
  expect(await page.evaluate(() => window.__avatarPlayback?.currentAction)).toBe('idle');

  // Incrementing sequence to 3 replays greeting even though the action ID matches the previous one.
  avatarPayload = { enabled: true, package: 'state-driven', action: 'greeting', sequence: 3, time: 300 };
  stateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
  await expect.poll(() => page.evaluate(() => window.__avatarPlayback?.currentAction)).toBe('greeting');
});

test('canvas avatar stays hidden until agent is summoned and hides when banished', async ({ canvas, page }) => {
  const cubeModelPath = path.join(REPO_ROOT, 'testlab', 'assets', 'avatar_packages', 'cube', 'model.glb');
  const stageAvatarRoute = '/theaters/stage/avatars/summon-gated';
  const manifest = {
    id: 'summon-gated',
    title: 'Summon Gated Avatar',
    model: 'model.glb',
    idle: 'idle',
    reactions: { activate: 'greeting' },
    actions: [
      { id: 'idle', label: 'Idle', clip: 'Hover', loop: true, lightColor: '#33ccee' },
      { id: 'greeting', label: 'Greeting', clip: 'PokeBounce', loop: false, lightColor: '#ffaa44' },
    ],
  };

  let agentRunning = false;
  let stateSocket: WebSocketRoute | null = null;
  let manifestRequests = 0;

  await canvas.openCanvas({
    viewport: { width: 1440, height: 900 },
    isOrator: false,
    apiRoutes: {
      '/api/theaters/stage': { metadata: { config: { avatar: { enabled: true, package: 'summon-gated' } }, is_active_orator: false } },
      '/api/latest': async (route) => {
        await route.fulfill({
          json: {
            agent_running: agentRunning,
            avatar: {
              enabled: true,
              package: 'summon-gated',
              action: agentRunning ? 'greeting' : null,
              sequence: agentRunning ? 1 : 0,
              time: 100,
            },
          },
        });
      },
      [`${stageAvatarRoute}/avatar.json`]: async (route) => {
        manifestRequests += 1;
        await route.fulfill({ json: manifest });
      },
      [`${stageAvatarRoute}/model.glb`]: (route) => route.fulfill({ path: cubeModelPath }),
    },
    onWebSocket: (ws) => {
      if (new URL(ws.url()).pathname === '/ws/canvas-state') {
        stateSocket = ws;
        ws.send(JSON.stringify({ type: 'state_ready' }));
      }
    },
  });

  await expect.poll(() => stateSocket !== null).toBe(true);
  const avatarMount = page.locator('#canvas-avatar');
  await expect(avatarMount).toBeHidden();
  expect(manifestRequests).toBe(0);

  // Summoning makes the avatar mount and appear for viewers via /api/latest.
  agentRunning = true;
  stateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
  await expect(avatarMount.locator('canvas')).toBeVisible();
  expect(manifestRequests).toBe(1);
  await expect.poll(() => page.evaluate(() => window.__avatarPlayback?.currentAction)).toBe('greeting');

  // Banishing hides the avatar again.
  agentRunning = false;
  stateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
  await expect(avatarMount).toBeHidden();

  // Resummoning brings the existing avatar back without re-fetching its manifest.
  agentRunning = true;
  stateSocket!.send(JSON.stringify({ type: 'state_changed', domains: ['latest'] }));
  await expect(avatarMount.locator('canvas')).toBeVisible();
  expect(manifestRequests).toBe(1);
});
