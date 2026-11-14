import { expect, test } from '../../testing/ui/canvas_fixtures';

type BatonManagerModule = typeof import('/static/generated/canvas/baton-manager.js');
type BatonManagerController =
  import('/static/generated/canvas/baton-manager.js').BatonManagerController;
type BatonState = import('/static/generated/canvas/baton-manager.js').BatonState;
type BatonUpdateContext = import('/static/generated/canvas/baton-manager.js').BatonUpdateContext;
type BatonUser = import('/static/generated/canvas/baton-manager.js').BatonUser;

interface BatonHarnessWindow extends Window {
  batonManager: BatonManagerController;
  roleChanges: boolean[];
  updates: BatonUpdateContext[];
  currentTestUser: BatonUser | null;
}

const BATON_MODULE_URL = '/static/generated/canvas/baton-manager.js';

const BATON_HTML = `
  <button id="baton-status-badge" type="button"></button>
  <button id="menu-take-back-baton-btn" type="button" style="display: none;">Take Back</button>
  <div id="orators-modal" aria-hidden="true">
    <button id="orators-close-btn" type="button">Close</button>
    <div id="active-viewers-list"></div>
    <div id="contributors-list"></div>
  </div>
  <div id="baton-request-modal" aria-hidden="true">
    <div id="baton-timer-bar" style="width: 100%;"></div>
    <button id="accept-baton-btn" type="button">Accept</button>
    <button id="decline-baton-btn" type="button">Decline</button>
  </div>
`;

test('owner baton UI enables management modal, escapes usernames, and handles contributor/pass actions', async ({
  canvas,
  page,
}) => {
  const requests: { method: string; url: string; body: Record<string, unknown> | null }[] = [];
  let serverState: BatonState = {
    owner: { id: 1, username: 'owner_alice' },
    active_orator: { id: 1, username: 'owner_alice' },
    contributors: [],
    active_viewers: [
      { id: 1, username: 'owner_alice' },
      { id: 2, username: '<img src=x onerror=alert(1)>' },
    ],
  };

  await canvas.serveRoute('/api/theaters/th-baton/baton**', async (route) => {
    const req = route.request();
    const url = req.url();
    const method = req.method();
    const postBody = req.postData() ? (JSON.parse(req.postData()!) as Record<string, unknown>) : null;
    requests.push({ method, url, body: postBody });

    if (url.endsWith('/baton') && method === 'GET') {
      await route.fulfill({ json: serverState });
      return;
    }
    if (url.endsWith('/baton/contributors') && method === 'POST') {
      serverState = {
        ...serverState,
        contributors: [{ id: 2, username: '<img src=x onerror=alert(1)>' }],
      };
      await route.fulfill({ json: serverState });
      return;
    }
    if (url.endsWith('/baton/request') && method === 'POST') {
      serverState = {
        ...serverState,
        baton_request: { target_user_id: 2, timeout_seconds: 30 },
      };
      await route.fulfill({ json: serverState });
      return;
    }
    if (url.endsWith('/baton/contributors/2') && method === 'DELETE') {
      serverState = {
        ...serverState,
        contributors: [],
        baton_request: null,
      };
      await route.fulfill({ json: serverState });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: 'unhandled' } });
  });

  await canvas.mount({ html: BATON_HTML });

  await page.evaluate(async (moduleUrl: string) => {
    const { createBatonManager } = await window.__importModule<BatonManagerModule>(moduleUrl);
    const win = window as unknown as BatonHarnessWindow;
    win.roleChanges = [];
    win.updates = [];
    win.currentTestUser = { id: 1, username: 'owner_alice' };
    win.batonManager = createBatonManager({
      theaterId: 'th-baton',
      getCurrentUser: () => win.currentTestUser,
      batonStatusBadge: document.getElementById('baton-status-badge') as HTMLButtonElement | null,
      menuTakeBackBatonBtn: document.getElementById('menu-take-back-baton-btn'),
      oratorsModal: document.getElementById('orators-modal'),
      oratorsCloseBtn: document.getElementById('orators-close-btn'),
      acceptBatonBtn: document.getElementById('accept-baton-btn'),
      declineBatonBtn: document.getElementById('decline-baton-btn'),
      onRoleChange: (isNowOrator) => win.roleChanges.push(isNowOrator),
      onBatonStateUpdated: (ctx) => win.updates.push(ctx),
    });
    await win.batonManager.checkBatonState();
  }, BATON_MODULE_URL);

  const badge = page.locator('#baton-status-badge');
  await expect(badge).toHaveText('👑 @owner_alice');
  await expect(badge).toBeEnabled();
  await expect(page.locator('#menu-take-back-baton-btn')).toBeHidden();

  await badge.click();
  await expect(page.locator('#orators-modal')).toHaveClass(/active/);
  await expect(page.locator('#active-viewers-list img')).toHaveCount(0);
  await expect(page.locator('#active-viewers-list')).toContainText('@<img src=x onerror=alert(1)>');

  await page.locator('[data-baton-action="add-contributor"]').click();
  await expect(page.locator('#active-viewers-list')).toContainText('Allowed');
  await expect(page.locator('#contributors-list')).toContainText('@<img src=x onerror=alert(1)>');

  await page.locator('[data-baton-action="pass-baton"]').click();
  await expect(page.locator('#contributors-list')).toContainText('Pending 30s...');

  await page.locator('[data-baton-action="remove-contributor"]').click();
  await expect(page.locator('#contributors-list')).toContainText('No contributor added yet.');

  await page.locator('#orators-close-btn').click();
  await expect(page.locator('#orators-modal')).not.toHaveClass(/active/);

  expect(requests.map((r) => `${r.method} ${new URL(r.url).pathname}`)).toEqual([
    'GET /api/theaters/th-baton/baton',
    'POST /api/theaters/th-baton/baton/contributors',
    'POST /api/theaters/th-baton/baton/request',
    'DELETE /api/theaters/th-baton/baton/contributors/2',
  ]);
});

test('viewer baton prompt handles accept, takeback, and 30s auto-decline countdown', async ({
  canvas,
  page,
}) => {
  await page.clock.install();
  const actions: string[] = [];

  await canvas.serveRoute('/api/theaters/th-baton/baton/*', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    actions.push(pathname);
    if (pathname.endsWith('/accept')) {
      await route.fulfill({
        json: {
          owner: { id: 1, username: 'owner_alice' },
          active_orator: { id: 2, username: 'viewer_bob' },
          contributors: [{ id: 2, username: 'viewer_bob' }],
          baton_request: null,
          active_viewers: [],
        },
      });
      return;
    }
    if (pathname.endsWith('/decline')) {
      await route.fulfill({
        json: {
          owner: { id: 1, username: 'owner_alice' },
          active_orator: { id: 1, username: 'owner_alice' },
          contributors: [{ id: 2, username: 'viewer_bob' }],
          baton_request: null,
          active_viewers: [],
        },
      });
      return;
    }
    if (pathname.endsWith('/takeback')) {
      await route.fulfill({
        json: {
          owner: { id: 1, username: 'owner_alice' },
          active_orator: { id: 1, username: 'owner_alice' },
          contributors: [{ id: 2, username: 'viewer_bob' }],
          baton_request: null,
          active_viewers: [],
        },
      });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: 'unhandled' } });
  });

  await canvas.mount({ html: BATON_HTML });

  await page.evaluate(async (moduleUrl: string) => {
    const { createBatonManager } = await window.__importModule<BatonManagerModule>(moduleUrl);
    const win = window as unknown as BatonHarnessWindow;
    win.roleChanges = [];
    win.updates = [];
    win.currentTestUser = { id: 2, username: 'viewer_bob' };
    win.batonManager = createBatonManager({
      theaterId: 'th-baton',
      getCurrentUser: () => win.currentTestUser,
      batonStatusBadge: document.getElementById('baton-status-badge') as HTMLButtonElement | null,
      menuTakeBackBatonBtn: document.getElementById('menu-take-back-baton-btn'),
      oratorsModal: document.getElementById('orators-modal'),
      oratorsCloseBtn: document.getElementById('orators-close-btn'),
      acceptBatonBtn: document.getElementById('accept-baton-btn'),
      declineBatonBtn: document.getElementById('decline-baton-btn'),
      onRoleChange: (isNowOrator) => win.roleChanges.push(isNowOrator),
      onBatonStateUpdated: (ctx) => win.updates.push(ctx),
    });

    win.batonManager.updateBatonStateUI({
      owner: { id: 1, username: 'owner_alice' },
      active_orator: { id: 1, username: 'owner_alice' },
      contributors: [{ id: 2, username: 'viewer_bob' }],
      baton_request: { target_user_id: 2, timeout_seconds: 30 },
    });
  }, BATON_MODULE_URL);

  const badge = page.locator('#baton-status-badge');
  const reqModal = page.locator('#baton-request-modal');
  await expect(badge).toBeDisabled();
  await expect(badge).toHaveText('👑 @owner_alice');
  await expect(reqModal).toHaveClass(/active/);

  // Accept baton pass -> viewer becomes active orator.
  await page.locator('#accept-baton-btn').click();
  await expect(reqModal).not.toHaveClass(/active/);
  await expect(badge).toHaveText('👑 @viewer_bob');

  // Switch identity to owner: owner sees Take Back button when viewer holds baton.
  await page.evaluate(() => {
    const win = window as unknown as BatonHarnessWindow;
    win.currentTestUser = { id: 1, username: 'owner_alice' };
    win.batonManager.updateBatonStateUI({
      owner: { id: 1, username: 'owner_alice' },
      active_orator: { id: 2, username: 'viewer_bob' },
      contributors: [{ id: 2, username: 'viewer_bob' }],
      baton_request: null,
    });
  });
  await expect(page.locator('#menu-take-back-baton-btn')).toBeVisible();
  await page.locator('#menu-take-back-baton-btn').click();
  await expect(page.locator('#menu-take-back-baton-btn')).toBeHidden();
  await expect(badge).toHaveText('👑 @owner_alice');

  // Switch back to viewer and let the 30-second countdown auto-decline.
  await page.evaluate(() => {
    const win = window as unknown as BatonHarnessWindow;
    win.currentTestUser = { id: 2, username: 'viewer_bob' };
    win.batonManager.updateBatonStateUI({
      owner: { id: 1, username: 'owner_alice' },
      active_orator: { id: 1, username: 'owner_alice' },
      contributors: [{ id: 2, username: 'viewer_bob' }],
      baton_request: { target_user_id: 2, timeout_seconds: 30 },
    });
  });
  await expect(reqModal).toHaveClass(/active/);

  await page.clock.fastForward(15000);
  const midWidth = await page.locator('#baton-timer-bar').evaluate((el) => el.style.width);
  expect(parseFloat(midWidth)).toBeLessThanOrEqual(55);

  await page.clock.fastForward(16000);
  await expect(reqModal).not.toHaveClass(/active/);

  expect(actions).toEqual([
    '/api/theaters/th-baton/baton/accept',
    '/api/theaters/th-baton/baton/takeback',
    '/api/theaters/th-baton/baton/decline',
  ]);
});

for (const action of ['add-contributor', 'remove-contributor', 'pass-baton']) {
  test(`canvas handles rejected ${action} requests from modal and globals`, async ({ canvas, page }) => {
    const failures: string[] = [];
    page.on('console', msg => { if (msg.type() === 'error') failures.push(msg.text()); });
    const state = { owner: { id: 1, username: 'Owner' }, active_orator: { id: 1, username: 'Owner' },
      contributors: [{ id: 3, username: 'Contributor' }], active_viewers: [{ id: 2, username: 'Viewer' }] };
    await canvas.openCanvas({ isOrator: true, user: { id: 1, username: 'Owner' }, apiRoutes: {
      '/api/theaters/stage/baton': state,
      '/api/theaters/stage/baton/contributors': async route => { await route.fulfill({ status: 403, json: { detail: 'Denied' } }); },
      '/api/theaters/stage/baton/contributors/3': async route => { await route.fulfill({ status: 403, json: { detail: 'Denied' } }); },
      '/api/theaters/stage/baton/request': async route => { await route.fulfill({ status: 403, json: { detail: 'Denied' } }); },
    } });
    await page.locator('#baton-status-badge').click();
    await page.locator(`[data-baton-action="${action}"]`).first().click();
    await expect.poll(() => failures.filter(msg => msg.includes('Denied')).length).toBe(1);
    await page.evaluate(action => {
      if (action === 'add-contributor') window.addContributor?.(2);
      else if (action === 'remove-contributor') window.removeContributor?.(3);
      else window.requestBatonPass?.(2);
    }, action);
    await expect.poll(() => failures.filter(msg => msg.includes('Denied')).length).toBe(2);
  });
}
