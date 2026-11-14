import fs from 'node:fs';
import path from 'node:path';
import type { Dialog, Page, Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  expect,
  STATIC_DIR,
  TEMPLATES_DIR,
  test,
} from '../../testing/ui/canvas_fixtures';

interface TheaterRowFixture {
  theater_id: string;
  name: string;
  is_owner: boolean;
  join_key?: string;
  mounted_references?: readonly string[];
  mounted_playlists?: Readonly<Record<string, unknown>>;
  last_used_at?: string;
  created_at?: string;
}

interface DeployRouteState {
  theaters: TheaterRowFixture[];
  deletedTheaters: string[];
  deployments: Record<string, string>[];
  fetchLog: Array<{ type: 'stop_agent' | 'delete_theater'; url: string }>;
}

function buildPreMergeSharedShim(filename: string): string {
  const source = fs.readFileSync(path.join(STATIC_DIR, 'js', filename), 'utf-8');
  if (filename === 'device-navigation.js') {
    return [
      source,
      'export const isMobile = window.NarratronNavigation.isMobile;',
      'export const theaterUrl = window.NarratronNavigation.theaterUrl;',
    ].join('\n');
  }
  if (filename === 'auth-flow.js') {
    const names = [
      'getAuthState',
      'invalidateAuthState',
      'checkAuthStatus',
      'openAuthModal',
      'closeAuthModal',
      'switchAuthTab',
      'submitLogin',
      'submitRegister',
      'submitForgotPassword',
      'submitResetPassword',
      'submitLogout',
      'ensureAuthModalDOM',
      'checkResetTokenInURL',
    ];
    return [
      source,
      `Object.assign(window, { ${names.join(', ')} });`,
      `export { ${names.join(', ')} };`,
    ].join('\n');
  }
  if (filename === 'credit-purchase.js') {
    const names = [
      'openBuyCreditsModal',
      'closeBuyCreditsModal',
      'toggleCardFields',
      'selectCreditPackage',
      'submitBuyCredits',
      'ensureBuyCreditsModalDOM',
    ];
    return [
      source,
      `Object.assign(window, { ${names.join(', ')} });`,
      `export { ${names.join(', ')} };`,
    ].join('\n');
  }
  throw new Error(`No pre-merge shim for ${filename}`);
}

const PRE_MERGE_SHIM_FILES = new Set([
  'device-navigation.js',
  'auth-flow.js',
  'credit-purchase.js',
]);

function renderSharedTopbar(activePage: string, showPricing: boolean): string {
  let html = fs.readFileSync(path.join(TEMPLATES_DIR, 'shared_topbar.html'), 'utf-8');
  html = html.replace(
    "{% if active_page == 'join' %}\r\n    <a href=\"/privacy\" class=\"deploy-nav-btn\">Privacy Policy</a>\r\n    <a href=\"/terms\" class=\"deploy-nav-btn\">Terms of Service</a>\r\n    {% endif %}",
    '',
  );
  html = html.replace(
    "{% if active_page == 'join' %}\n    <a href=\"/privacy\" class=\"deploy-nav-btn\">Privacy Policy</a>\n    <a href=\"/terms\" class=\"deploy-nav-btn\">Terms of Service</a>\n    {% endif %}",
    '',
  );
  html = html.replace(
    /\{% if show_pricing %\}([\s\S]*?)\{% endif %\}/g,
    showPricing ? '$1' : '',
  );
  html = html.replace(/\{% if active_page == '([^']+)' %\}active\{% endif %\}/g, (_m, p: string) =>
    p === activePage ? 'active' : '',
  );
  html = html.replace(/\{% if active_page in \[[\s\S]*?\] %\}active\{% endif %\}/g, '');
  return html;
}

function prepareDeployHtml(): string {
  let html = fs.readFileSync(path.join(TEMPLATES_DIR, 'theater_creation.html'), 'utf-8');
  html = html.replace('<!-- SHARED_TOPBAR -->', renderSharedTopbar('deploy', true));
  html = html.replace(/<link[^>]+href="https:\/\/fonts\.googleapis\.com[^"]*"[^>]*>/g, '');
  html = html.replace(/<link[^>]+href="\/favicon\.png"[^>]*>/g, '');
  return html;
}

function parseMultipartFieldStrings(postDataBuffer: Buffer | null): Record<string, string> {
  if (!postDataBuffer) return {};
  const raw = postDataBuffer.toString('utf-8');
  const firstLineEnd = raw.indexOf('\r\n');
  if (firstLineEnd <= 0) return {};
  const boundary = raw.slice(0, firstLineEnd);
  const result: Record<string, string> = {};
  for (const part of raw.split(boundary)) {
    const headerEnd = part.indexOf('\r\n\r\n');
    if (headerEnd === -1) continue;
    const header = part.slice(0, headerEnd);
    const nameMatch = /name="([^"]+)"/.exec(header);
    if (!nameMatch) continue;
    let body = part.slice(headerEnd + 4);
    if (body.endsWith('\r\n')) {
      body = body.slice(0, -2);
    }
    result[nameMatch[1]] = body.replace(/\r\n/g, '\n');
  }
  return result;
}

interface DeployHarness {
  state: DeployRouteState;
  defaultConfigYaml: string;
}

async function setupDeployHarness(
  canvas: {
    serveRoute: (
      url: (url: URL) => boolean,
      handler: (route: Route) => Promise<void>,
    ) => Promise<void>;
  },
  page: Page,
): Promise<DeployHarness> {
  const defaultConfigYaml = fs.readFileSync(
    path.join(TEMPLATES_DIR, '..', 'theater_default.yaml'),
    'utf-8',
  );
  const deployHtml = prepareDeployHtml();
  const state: DeployRouteState = {
    theaters: [],
    deletedTheaters: [],
    deployments: [],
    fetchLog: [],
  };

  await canvas.serveRoute(
    (url: URL) => {
      if (url.origin !== CANVAS_ORIGIN) return false;
      if (url.pathname.startsWith('/static/generated/canvas/')) {
        const filename = url.pathname.slice('/static/generated/canvas/'.length);
        const compiledPath = path.join(STATIC_DIR, 'generated', 'canvas', filename);
        return !fs.existsSync(compiledPath) && PRE_MERGE_SHIM_FILES.has(filename);
      }
      return !url.pathname.startsWith('/static/');
    },
    async (route: Route) => {
      const url = new URL(route.request().url());
      const pathname = url.pathname;
      const method = route.request().method();

      if (pathname.startsWith('/static/generated/canvas/')) {
        const filename = pathname.slice('/static/generated/canvas/'.length);
        await route.fulfill({
          contentType: 'application/javascript; charset=utf-8',
          body: buildPreMergeSharedShim(filename),
        });
        return;
      }

      if (pathname === '/deploy') {
        await route.fulfill({ contentType: 'text/html; charset=utf-8', body: deployHtml });
      } else if (pathname === '/remote') {
        await route.fulfill({
          contentType: 'text/html; charset=utf-8',
          body: '<!doctype html><html><body>Remote Stub</body></html>',
        });
      } else if (pathname === '/api/auth/me') {
        await route.fulfill({
          json: {
            authenticated: true,
            user: { id: 'owner', username: 'owner', credits: 100 },
          },
        });
      } else if (pathname === '/api/adventures') {
        await route.fulfill({ json: [] });
      } else if (pathname === '/api/theaters/default-config') {
        await route.fulfill({ json: { config_yaml: defaultConfigYaml } });
      } else if (pathname === '/api/theaters/create-and-deploy' && method === 'POST') {
        const fields = parseMultipartFieldStrings(route.request().postDataBuffer());
        state.deployments.push(fields);
        await route.fulfill({
          status: 400,
          json: { detail: 'Deployment captured for test' },
        });
      } else if (pathname === '/api/theaters') {
        await route.fulfill({ json: state.theaters });
      } else if (pathname.startsWith('/api/theaters/') && pathname.endsWith('/agent/stop') && method === 'POST') {
        state.fetchLog.push({ type: 'stop_agent', url: pathname });
        await route.fulfill({ json: { status: 'stopped' } });
      } else if (pathname.startsWith('/api/theaters/') && method === 'DELETE') {
        const theaterId = pathname.split('/').pop() ?? '';
        state.fetchLog.push({ type: 'delete_theater', url: pathname });
        state.deletedTheaters.push(theaterId);
        state.theaters = state.theaters.filter((t) => t.theater_id !== theaterId);
        await route.fulfill({ json: { status: 'ok', theater_id: theaterId } });
      } else if (pathname === '/api/pricing') {
        await route.fulfill({
          json: {
            usd_per_credit: 0.05,
            live_agent_tool_call_credit_rate: 0.1,
            story_plan_credit_rate: 1.0,
            character_voiced_turn_credit_rate: 0.5,
            interactive_canvas_credit_rate: 1.0,
            image_credit_rate: 4.0,
            music_credit_rate: 2.0,
            layered_animation_credit_rate: 6.5,
            storage_gb_daily_rate: 0.1,
            calculation: { usage_credits: 10, storage_credits: 3 },
          },
        });
      } else {
        await route.abort('failed');
        throw new Error(`Unexpected request: ${method} ${pathname}`);
      }
    },
  );

  await page.addInitScript(() => {
    localStorage.setItem('narratron.deployAcknowledgement.v1', 'accepted');
    window.alert = () => {};
  });

  await page.goto(`${CANVAS_ORIGIN}/deploy`);
  await expect(page.locator('#userAccountBar .credit-badge')).toBeVisible();
  return { state, defaultConfigYaml };
}

test('mobile theater actions open remote instead of canvas', async ({ canvas, page }) => {
  const { state } = await setupDeployHarness(canvas, page);
  state.theaters = [
    { theater_id: 'stage', name: 'Stage', is_owner: true, join_key: 'SECRET' },
  ];
  await page.evaluate(() => window.loadTheaters?.());

  const canvasBtn = page.getByRole('button', { name: 'Launch Canvas', exact: true });
  const remoteLink = page.getByRole('link', { name: 'Open Mobile', exact: true });
  const exportBtn = page.getByRole('link', { name: 'Export Assets', exact: true });
  const builderBtn = page.getByRole('link', { name: 'Edit in Builder', exact: true });
  const destroyBtn = page.getByRole('button', { name: 'Destroy', exact: true });

  await expect(canvasBtn).toBeVisible();
  await expect(remoteLink).not.toBeVisible();
  await expect(exportBtn).toBeVisible();
  await expect(builderBtn).toBeVisible();
  await expect(builderBtn).toHaveAttribute('href', '/theater-editor?theater_id=stage');
  await expect(destroyBtn).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(canvasBtn).not.toBeVisible();
  await expect(remoteLink).toBeVisible();
  await expect(exportBtn).not.toBeVisible();
  await expect(builderBtn).not.toBeVisible();
  await expect(destroyBtn).not.toBeVisible();

  await remoteLink.scrollIntoViewIfNeeded();
  await expect(remoteLink).toBeInViewport();
  const fitsViewport = await page
    .locator('#activeTheatersPanel .table-responsive')
    .evaluate((el) => el.scrollWidth <= el.clientWidth);
  expect(fitsViewport).toBe(true);
  await expect(page.locator('.mobile-canvas-note')).toBeVisible();

  await remoteLink.click();
  await expect(page).toHaveURL(`${CANVAS_ORIGIN}/remote?theater_id=stage`);
});

test('personal theater listing excludes theaters owned by others', async ({ canvas, page }) => {
  const { state } = await setupDeployHarness(canvas, page);
  state.theaters = [
    { theater_id: 'owned-stage', name: 'Owned Stage', is_owner: true },
    { theater_id: 'shared-stage', name: 'Shared Stage', is_owner: false },
  ];
  await page.evaluate(() => window.loadTheaters?.());
  await expect(page.locator('#activeTheatersPanel')).toContainText('Owned Stage');
  await expect(page.locator('#activeTheatersPanel')).not.toContainText('Shared Stage');
  await expect(page.getByRole('link', { name: 'Edit in Builder', exact: true })).toHaveCount(1);
});

test('deploy uses the shared credit purchase modal', async ({ canvas, page }) => {
  await setupDeployHarness(canvas, page);

  await page.evaluate(() => window.openBuyCreditsModal?.());
  await expect(page.locator('#buyCreditsModal')).toBeVisible();
  await page.locator('#pkgUltra').click();
  await expect(page.locator('#btnCompletePurchase')).toHaveText('Pay $40.00 & Add 1000 Credits');
  await page.locator('#buyCreditsModal [data-credit-rates]').click();
  await expect(page.locator('#buyCreditsModal')).not.toBeVisible();
  await expect(page.locator('#pricingModal')).toHaveClass('modal-overlay active');
});

for (const width of [1280, 390] as const) {
  for (const customize of [false, true] as const) {
    test(`blank canvas settings are visible and submitted (width=${width}, customize=${String(customize)})`, async ({
      canvas,
      page,
    }) => {
      const { state } = await setupDeployHarness(canvas, page);
      await page.setViewportSize({ width, height: 900 });

      await expect(page.locator('.path-card')).toHaveCount(3);
      await expect(page.getByText('Configure with Assets', { exact: true })).toHaveCount(0);
      await expect(page.locator('#refFileInput, #playlistsContainer')).toHaveCount(0);
      await expect(page.locator('#cfgImageGeneration')).not.toBeVisible();

      const defaults: Readonly<Record<string, boolean>> = {
        cfgImageGeneration: true,
        cfgUseGeneratedMusic: false,
        cfgSceneAnimations: false,
        cfgAdventureMode: false,
        cfgInteractiveCanvas: false,
      };

      if (customize) {
        await page.locator('#simpleConfig summary').click();
        for (const [control, checked] of Object.entries(defaults)) {
          const locator = page.locator(`#${control}`);
          await expect(locator).toBeVisible();
          expect(await locator.isChecked()).toBe(checked);
          await locator.setChecked(!checked);
        }
        await page.locator('#theaterStyle').fill('Watercolor');
        await page.locator('#theaterSpecialInstructions').fill('Keep scenes calm.');
        await page.locator('#simpleConfig summary').click();
      }

      await page.locator('#deployBtn').click();
      await expect.poll(() => state.deployments.length).toBe(1);
      const data = state.deployments[0];
      expect(data['creation_mode']).toBe('blank');
      expect(data['enable_image_generation']).toBe(customize ? 'false' : 'true');
      for (const field of [
        'use_generated_music',
        'enable_scene_animations',
        'enable_adventure_mode',
        'enable_interactive_canvas',
      ]) {
        expect(data[field]).toBe(customize ? 'true' : 'false');
      }
      expect(data['agent_style']).toBe(customize ? 'Watercolor' : '');
      expect(data['agent_special_instructions']).toBe(customize ? 'Keep scenes calm.' : '');
      expect('advanced_config' in data).toBe(false);
    });
  }
}

test('retired creation path falls back to blank canvas', async ({ canvas, page }) => {
  await setupDeployHarness(canvas, page);

  await page.goto(`${CANVAS_ORIGIN}/deploy?creation_path=folder`);
  await expect(page.locator('#pathCardBlank')).toHaveClass('path-card active');
  await expect(page.locator('#simpleConfig summary')).toBeVisible();

  await page.goto(`${CANVAS_ORIGIN}/deploy?creation_path=individual`);
  await expect(page.locator('#pathCardBlank')).toHaveClass('path-card active');
  await expect(page.locator('#simpleConfig summary')).toBeVisible();
});

test('canvas return suggestion selects blank canvas', async ({ canvas, page }) => {
  await setupDeployHarness(canvas, page);

  await page.goto(`${CANVAS_ORIGIN}/deploy?from=canvas`);
  await expect(page.locator('#canvasReturnBanner')).toBeVisible();
  await expect(page.locator('#pathCardBlank')).toHaveClass('path-card active');
  await expect(page.locator('#simpleConfig summary')).toBeVisible();
  await expect(page.locator('#canvasReturnBanner a')).toHaveAttribute('href', '/theater-editor');
});

test('blank canvas config does not override adventure', async ({ canvas, page }) => {
  const { state, defaultConfigYaml } = await setupDeployHarness(canvas, page);

  await page.locator('#advancedConfig summary').click();
  await expect(page.locator('#theaterConfigEditor')).toHaveValue(defaultConfigYaml);
  await page.locator('#theaterConfigEditor').fill('image_generation:\n  enabled: false\n');
  await page.locator('#pathCardAdventure').click();
  await expect(page.locator('#customConfigOptions')).not.toBeVisible();

  await page.locator('#deployBtn').click();
  await expect.poll(() => state.deployments.length).toBe(1);
  const adventureData = state.deployments[0];
  expect(adventureData['creation_mode']).toBe('adventure');
  expect(adventureData['enable_adventure_mode']).toBe('true');
  expect('advanced_config' in adventureData).toBe(false);
  expect('enable_image_generation' in adventureData).toBe(false);

  await page.locator('#pathCardBlank').click();
  await expect(page.locator('#theaterConfigEditor')).toBeVisible();
  await page.evaluate(() => window.deployNewTheater?.());
  await expect.poll(() => state.deployments.length).toBe(2);
  const blankData = state.deployments[1];
  expect(blankData['advanced_config_canonical']).toBe('true');
  expect(blankData['advanced_config']).toBe('image_generation:\n  enabled: false');
});

test('create new theater options and responsive stacking', async ({ canvas, page }) => {
  await setupDeployHarness(canvas, page);

  const cards = page.locator('.path-card');
  await expect(cards).toHaveCount(3);
  await expect(cards.nth(0)).toContainText('Fresh Canvas');
  await expect(cards.nth(1)).toContainText('New Adventure');
  await expect(cards.nth(2)).toContainText('Enter Theater Builder');
  await expect(page.locator('#pathCardFolder')).toHaveCount(0);
  await expect(page.locator('#pathSectionFolder')).toHaveCount(0);

  await page.setViewportSize({ width: 1280, height: 900 });
  const box0 = await cards.nth(0).boundingBox();
  const box1 = await cards.nth(1).boundingBox();
  const box2 = await cards.nth(2).boundingBox();
  expect(box0).not.toBeNull();
  expect(box1).not.toBeNull();
  expect(box2).not.toBeNull();
  if (box0 && box1 && box2) {
    expect(Math.abs(box0.y - box1.y)).toBeLessThanOrEqual(4);
    expect(Math.abs(box1.y - box2.y)).toBeLessThanOrEqual(4);
    expect(box0.x).toBeLessThan(box1.x);
    expect(box1.x).toBeLessThan(box2.x);
  }

  await page.setViewportSize({ width: 390, height: 844 });
  const mbox0 = await cards.nth(0).boundingBox();
  const mbox1 = await cards.nth(1).boundingBox();
  const mbox2 = await cards.nth(2).boundingBox();
  expect(mbox0).not.toBeNull();
  expect(mbox1).not.toBeNull();
  expect(mbox2).not.toBeNull();
  if (mbox0 && mbox1 && mbox2) {
    expect(mbox0.y).toBeLessThan(mbox1.y);
    expect(mbox1.y).toBeLessThan(mbox2.y);
    expect(Math.abs(mbox0.x - mbox1.x)).toBeLessThanOrEqual(4);
  }
});

test('deleting theater asks for confirmation once until reloaded', async ({ canvas, page }) => {
  const { state } = await setupDeployHarness(canvas, page);
  const seedTheaters: TheaterRowFixture[] = [
    { theater_id: 'theater-alpha', name: 'Alpha Theater', is_owner: true, join_key: 'KEY-ALPHA' },
    { theater_id: 'theater-beta', name: 'Beta Theater', is_owner: true, join_key: 'KEY-BETA' },
    { theater_id: 'theater-gamma', name: 'Gamma Theater', is_owner: true, join_key: 'KEY-GAMMA' },
  ];
  state.theaters = [...seedTheaters];
  await page.evaluate(() => window.loadTheaters?.());
  await expect(page.locator('#theatersTableBody tr')).toHaveCount(3);

  const dialogMessages: string[] = [];

  page.once('dialog', async (dialog: Dialog) => {
    dialogMessages.push(dialog.message());
    await dialog.dismiss();
  });
  await page.locator('#theatersTableBody tr').first().locator('button.btn-danger').click();

  expect(dialogMessages).toHaveLength(1);
  expect(dialogMessages[0]).toContain('theater-alpha');
  expect(state.deletedTheaters).toEqual([]);
  expect(await page.evaluate(() => window.theaterDeletionConfirmed)).toBe(false);

  page.once('dialog', async (dialog: Dialog) => {
    dialogMessages.push(dialog.message());
    await dialog.accept();
  });
  await page.locator('#theatersTableBody tr').first().locator('button.btn-danger').click();
  await expect.poll(() => state.deletedTheaters).toContain('theater-alpha');

  expect(dialogMessages).toHaveLength(2);
  expect(await page.evaluate(() => window.theaterDeletionConfirmed)).toBe(true);
  await expect(page.locator('#theatersTableBody tr')).toHaveCount(2);

  const unexpectedDialogs: string[] = [];
  const handleUnexpected = async (dialog: Dialog): Promise<void> => {
    unexpectedDialogs.push(dialog.message());
    await dialog.accept();
  };
  page.on('dialog', handleUnexpected);
  await page.locator('#theatersTableBody tr').first().locator('button.btn-danger').click();
  await expect.poll(() => state.deletedTheaters).toContain('theater-beta');

  expect(unexpectedDialogs).toEqual([]);
  await expect(page.locator('#theatersTableBody tr')).toHaveCount(1);

  page.off('dialog', handleUnexpected);
  state.theaters = [...seedTheaters];
  state.deletedTheaters = [];
  await page.reload();
  await expect(page.locator('#userAccountBar .credit-badge')).toBeVisible();
  expect(await page.evaluate(() => window.theaterDeletionConfirmed)).toBe(false);
  await expect(page.locator('#theatersTableBody tr')).toHaveCount(3);

  const reloadedDialogMessages: string[] = [];
  page.once('dialog', async (dialog: Dialog) => {
    reloadedDialogMessages.push(dialog.message());
    await dialog.accept();
  });
  await page.locator('#theatersTableBody tr').first().locator('button.btn-danger').click();
  await expect.poll(() => state.deletedTheaters).toContain('theater-alpha');

  expect(reloadedDialogMessages).toHaveLength(1);
  expect(reloadedDialogMessages[0]).toContain('theater-alpha');
});

test('theater deletion stops agent session first', async ({ canvas, page }) => {
  const { state } = await setupDeployHarness(canvas, page);
  state.theaters = [
    { theater_id: 'theater-alpha', name: 'Alpha Theater', is_owner: true, join_key: 'KEY-ALPHA' },
  ];
  await page.evaluate(() => {
    window.theaterDeletionConfirmed = true;
    return window.loadTheaters?.();
  });

  await expect(page.locator('#theatersTableBody tr')).toHaveCount(1);
  await page.locator('#theatersTableBody tr').first().locator('button.btn-danger').click();
  await expect.poll(() => state.fetchLog.some((item) => item.type === 'delete_theater')).toBe(true);

  expect(state.fetchLog).toHaveLength(2);
  expect(state.fetchLog[0].type).toBe('stop_agent');
  expect(state.fetchLog[0].url).toContain('theater-alpha');
  expect(state.fetchLog[1].type).toBe('delete_theater');
  expect(state.fetchLog[1].url).toContain('theater-alpha');
});
