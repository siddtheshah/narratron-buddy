import type { Page } from '@playwright/test';
import { expect, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';
import type {
  A2UICanvasRenderer,
  A2UISurface,
} from './a2ui-renderer.js';

type A2UIRendererModule = typeof import('./a2ui-renderer.js');

interface RecordedRequest {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: string | null;
}

interface A2UIHarnessWindow extends Window {
  editable: boolean;
  renderer: A2UICanvasRenderer;
  surfaces: A2UISurface[];
  originalHosts: Element[];
  originalButtons: Element[];
  originalHost: Element | null;
  originalAnimation: Animation | undefined;
  arrivals: string[];
}

const A2UI_MODULE_URL = '/static/generated/canvas/a2ui-renderer.js';
const A2UI_SHIM_URL = '/static/js/a2ui-canvas-renderer.js';

type DragEdge = 'top' | 'bottom' | 'left' | 'right' | 'control';
const DRAG_EDGES: readonly DragEdge[] = ['top', 'bottom', 'left', 'right', 'control'];

async function setupSurfacePage(
  canvas: CanvasHarness,
  page: Page,
  options: {
    actionStatus?: number;
    patchStatus?: number;
    deleteStatus?: number;
  } = {},
): Promise<RecordedRequest[]> {
  const requests: RecordedRequest[] = [];
  const actionStatus = options.actionStatus ?? 200;
  const patchStatus = options.patchStatus ?? 200;
  const deleteStatus = options.deleteStatus ?? 200;

  await canvas.serveRoute(
    (url) =>
      url.origin === canvas.origin &&
      (url.pathname === '/action' || url.pathname.startsWith('/surfaces/')),
    async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      const method = req.method();
      requests.push({
        url: url.pathname,
        method,
        headers: await req.allHeaders(),
        body: req.postData(),
      });
      if (url.pathname === '/action') {
        await route.fulfill({
          status: actionStatus,
          json: actionStatus >= 400 ? { error: 'Action rejected' } : { ok: true },
        });
        return;
      }
      const status = method === 'DELETE' ? deleteStatus : patchStatus;
      await route.fulfill({
        status,
        json: status >= 400 ? { error: 'Surface error' } : { ok: true },
      });
    },
  );

  await canvas.mount({
    viewport: { width: 1000, height: 800 },
    html: '<div id="stage"><div id="a2ui-canvas-layer"></div></div>',
    stylesheets: ['/static/css/canvas.css'],
    inlineStyles: [
      `body { margin: 0; padding: 0; display: block; }
       #stage { position: relative; width: 1000px; height: 800px; }`,
    ],
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { createA2UICanvasRenderer } =
      await window.__importModule<A2UIRendererModule>(moduleUrl);
    const win = window as unknown as A2UIHarnessWindow;
    win.editable = true;
    win.renderer = createA2UICanvasRenderer({
      container: document.querySelector('#a2ui-canvas-layer') as HTMLElement,
      actionUrl: () => '/action',
      surfaceUrl: (id: string) => `/surfaces/${id}`,
      canEdit: () => win.editable,
    });
    win.surfaces = [
      {
        surface_id: 'choice',
        placement: { left_pct: 50, top_pct: 50, width_pct: 28 },
        messages: [
          {
            createSurface: {
              surfaceId: 'choice',
              components: [
                { id: 'root', component: 'Card', child: 'button' },
                {
                  id: 'button',
                  component: 'Button',
                  child: 'label',
                  action: { event: { name: 'choose' } },
                },
                { id: 'label', component: 'Text', text: 'Choose' },
              ],
            },
          },
        ],
      },
    ];
    win.renderer.render(win.surfaces);
    await Promise.all(document.getAnimations().map((animation) => animation.finished));
  }, A2UI_MODULE_URL);

  return requests;
}

test('deprecated shim re-exports the same compiled createA2UICanvasRenderer', async ({
  canvas,
  page,
}) => {
  await canvas.mount();
  const sameReference = await page.evaluate(
    async ({ canonicalUrl, shimUrl }) => {
      const canonical = await window.__importModule<A2UIRendererModule>(canonicalUrl);
      const shim = await window.__importModule<A2UIRendererModule>(shimUrl);
      return canonical.createA2UICanvasRenderer === shim.createA2UICanvasRenderer;
    },
    { canonicalUrl: A2UI_MODULE_URL, shimUrl: A2UI_SHIM_URL },
  );
  expect(sameReference).toBe(true);
});

for (const edge of DRAG_EDGES) {
  test(`drag from ${edge} preserves grab offset and saves position`, async ({
    canvas,
    page,
  }) => {
    const requests = await setupSurfacePage(canvas, page);
    const surface = page.locator('.a2ui-surface');
    const bounds = (await surface.boundingBox())!;

    let x = bounds.x + bounds.width / 2;
    let y = bounds.y + bounds.height / 2;
    if (edge === 'top') {
      y = bounds.y + 10;
    } else if (edge === 'bottom') {
      y = bounds.y + bounds.height - 10;
    } else if (edge === 'left') {
      x = bounds.x + 10;
    } else if (edge === 'right') {
      x = bounds.x + bounds.width - 10;
    } else {
      const control = (await page.locator('.a2ui-move').boundingBox())!;
      x = control.x + control.width / 2;
      y = control.y + control.height / 2;
    }

    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + 100, y + 80);

    await expect(surface).toHaveClass(/dragging/);
    await expect(page.locator('.a2ui-move')).toHaveClass(/dragging/);
    expect(await surface.evaluate((el: HTMLElement) => el.style.left)).toBe('60%');
    expect(await surface.evaluate((el: HTMLElement) => el.style.top)).toBe('60%');

    await page.mouse.up();
    await expect.poll(() => requests.length).toBe(1);
    expect(requests[0].url).toBe('/surfaces/choice');
    expect(requests[0].method).toBe('PATCH');
    expect(requests[0].headers['content-type']).toBe('application/json');
    expect(JSON.parse(requests[0].body || '{}')).toEqual({
      left_pct: 60,
      top_pct: 60,
    });
    await expect(surface).not.toHaveClass(/dragging/);
    await expect(page.locator('.a2ui-move')).not.toHaveClass(/dragging/);
  });
}

test('drag clamps surface coordinates to [2, 98] and skips PATCH when unmoved', async ({
  canvas,
  page,
}) => {
  const requests = await setupSurfacePage(canvas, page);
  const surface = page.locator('.a2ui-surface');
  const control = (await page.locator('.a2ui-move').boundingBox())!;
  const cx = control.x + control.width / 2;
  const cy = control.y + control.height / 2;

  // Clicking move handle without dragging does not emit a PATCH.
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.up();
  expect(requests).toHaveLength(0);

  // Dragging far beyond container bounds clamps to 98% / 2%.
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx + 800, cy - 800);
  expect(await surface.evaluate((el: HTMLElement) => el.style.left)).toBe('98%');
  expect(await surface.evaluate((el: HTMLElement) => el.style.top)).toBe('2%');
  await page.mouse.up();

  await expect.poll(() => requests.length).toBe(1);
  expect(JSON.parse(requests[0].body || '{}')).toEqual({
    left_pct: 98,
    top_pct: 2,
  });
});

test('content button still sends action and re-enables on error', async ({
  canvas,
  page,
}) => {
  const requests = await setupSurfacePage(canvas, page);
  const button = page.locator('.a2ui-button');
  await button.click();

  await expect.poll(() => requests.length).toBe(1);
  expect(requests.map((req) => req.url)).toEqual(['/action']);
  expect(requests[0].method).toBe('POST');
  const payload = JSON.parse(requests[0].body || '{}') as {
    version: string;
    action: { name: string; surfaceId: string; sourceComponentId: string; wantResponse: boolean };
  };
  expect(payload.version).toBe('v1.0');
  expect(payload.action).toMatchObject({
    name: 'choose',
    surfaceId: 'choice',
    sourceComponentId: 'button',
    wantResponse: false,
  });
  await expect(page.locator('.a2ui-surface')).toHaveClass(/a2ui-surface-used/);
  await expect(button).toBeDisabled();

  // Failed action request re-enables the button and does not mark surface used.
  const failedRequests = await setupSurfacePage(canvas, page, { actionStatus: 500 });
  const retryButton = page.locator('.a2ui-button');
  await retryButton.click();
  await expect.poll(() => failedRequests.length).toBe(1);
  await expect(retryButton).toBeEnabled();
  await expect(page.locator('.a2ui-surface')).not.toHaveClass(/a2ui-surface-used/);
});

test('cancelled drag restores position on pointercancel and lostpointercapture', async ({
  canvas,
  page,
}) => {
  const requests = await setupSurfacePage(canvas, page);
  const surface = page.locator('.a2ui-surface');
  const bounds = (await surface.boundingBox())!;
  const startX = bounds.x + 10;
  const startY = bounds.y + bounds.height / 2;

  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(startX + 100, startY + 80);
  expect(await surface.evaluate((el: HTMLElement) => el.style.left)).toBe('60%');

  await surface.evaluate((el: HTMLElement) => {
    el.dispatchEvent(new PointerEvent('pointercancel', { pointerId: 1 }));
  });
  await page.mouse.up();

  expect(await surface.evaluate((el: HTMLElement) => el.style.left)).toBe('50%');
  expect(await surface.evaluate((el: HTMLElement) => el.style.top)).toBe('50%');
  await expect(surface).not.toHaveClass(/dragging/);
  expect(requests).toHaveLength(0);

  // Also verify lostpointercapture restores the original coordinates.
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(startX + 100, startY + 80);
  expect(await surface.evaluate((el: HTMLElement) => el.style.left)).toBe('60%');

  await surface.evaluate((el: HTMLElement) => {
    el.dispatchEvent(new PointerEvent('lostpointercapture', { pointerId: 1 }));
  });
  await page.mouse.up();

  expect(await surface.evaluate((el: HTMLElement) => el.style.left)).toBe('50%');
  expect(await surface.evaluate((el: HTMLElement) => el.style.top)).toBe('50%');
  expect(requests).toHaveLength(0);
});

test('read-only surface hides edit controls and cannot be dragged', async ({
  canvas,
  page,
}) => {
  const requests = await setupSurfacePage(canvas, page);
  await page.evaluate(() => {
    const win = window as unknown as A2UIHarnessWindow;
    win.editable = false;
    win.renderer.render([]);
    win.renderer.render(win.surfaces);
  });

  await expect(page.locator('.a2ui-surface-editable')).toHaveCount(0);
  await expect(page.locator('.a2ui-move')).toHaveCount(0);

  const surface = page.locator('.a2ui-surface');
  const bounds = (await surface.boundingBox())!;
  const startX = bounds.x + 10;
  const startY = bounds.y + bounds.height / 2;
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(startX + 100, startY + 80);
  await page.mouse.up();

  expect(await surface.evaluate((el: HTMLElement) => el.style.left)).toBe('50%');
  expect(requests).toHaveLength(0);
});

test('position update preserves surfaces and does not replay arrival animation', async ({
  canvas,
  page,
}) => {
  const requests = await setupSurfacePage(canvas, page);
  await page.evaluate(async () => {
    const win = window as unknown as A2UIHarnessWindow;
    const other = structuredClone(win.surfaces[0]);
    const nextOther: A2UISurface = {
      ...other,
      surface_id: 'other',
      placement: { ...other.placement, left_pct: 80 },
      messages: [
        {
          createSurface: {
            surfaceId: 'other',
            components: other.messages?.[0]?.createSurface?.components,
          },
        },
      ],
    };
    win.surfaces.push(nextOther);
    win.renderer.render(win.surfaces);
    win.originalHosts = [...document.querySelectorAll('.a2ui-surface')];
    win.originalButtons = [...document.querySelectorAll('.a2ui-button')];
    await Promise.all(document.getAnimations().map((animation) => animation.finished));
    win.arrivals = [];
    document.addEventListener('animationstart', (event: AnimationEvent) => {
      win.arrivals.push(event.animationName);
    });
  });

  const move = page.locator('[data-surface-id="choice"] .a2ui-move');
  const bounds = (await move.boundingBox())!;
  const x = bounds.x + bounds.width / 2;
  const y = bounds.y + bounds.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 100, y + 80);
  await page.mouse.up();

  await page.evaluate(async () => {
    const win = window as unknown as A2UIHarnessWindow;
    win.surfaces[0] = {
      ...win.surfaces[0],
      placement: { ...win.surfaces[0].placement, left_pct: 60, top_pct: 60 },
    };
    win.renderer.render(win.surfaces);
  });

  const preserved = await page.evaluate(() => {
    const win = window as unknown as A2UIHarnessWindow;
    const hosts = [...document.querySelectorAll('.a2ui-surface')];
    const buttons = [...document.querySelectorAll('.a2ui-button')];
    return {
      hostsMatch: win.originalHosts.every((host, index) => host === hosts[index]),
      buttonsMatch: win.originalButtons.every((button, index) => button === buttons[index]),
    };
  });
  expect(preserved).toEqual({ hostsMatch: true, buttonsMatch: true });

  await page.evaluate(
    () => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))),
  );
  expect(await page.evaluate(() => (window as unknown as A2UIHarnessWindow).arrivals)).toEqual([]);
  await expect.poll(() => requests.map((req) => req.method)).toEqual(['PATCH']);
});

test('remote position update is used for next drag and deferred while dragging', async ({
  canvas,
  page,
}) => {
  const requests = await setupSurfacePage(canvas, page);
  await page.evaluate(() => {
    const win = window as unknown as A2UIHarnessWindow;
    win.surfaces[0] = {
      ...win.surfaces[0],
      placement: { ...win.surfaces[0].placement, left_pct: 60, top_pct: 60 },
    };
    win.renderer.render(win.surfaces);
  });

  const bounds = (await page.locator('.a2ui-move').boundingBox())!;
  const x = bounds.x + bounds.width / 2;
  const y = bounds.y + bounds.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 100, y + 80);

  // A concurrent remote placement poll during an active drag must not overwrite the drag.
  await page.evaluate(() => {
    const win = window as unknown as A2UIHarnessWindow;
    win.surfaces[0] = {
      ...win.surfaces[0],
      placement: { ...win.surfaces[0].placement, left_pct: 20, top_pct: 20 },
    };
    win.renderer.render(win.surfaces);
  });
  expect(await page.locator('.a2ui-surface').evaluate((el: HTMLElement) => el.style.left)).toBe('70%');
  expect(await page.locator('.a2ui-surface').evaluate((el: HTMLElement) => el.style.top)).toBe('70%');

  await page.mouse.up();
  await expect.poll(() => requests.length).toBe(1);
  expect(JSON.parse(requests[0].body || '{}')).toEqual({
    left_pct: 70,
    top_pct: 70,
  });
});

test('content updates and surface lifecycle preserve existing host and handle delete/data updates', async ({
  canvas,
  page,
}) => {
  const requests = await setupSurfacePage(canvas, page);
  await page.evaluate(() => {
    const win = window as unknown as A2UIHarnessWindow;
    win.originalHost = document.querySelector('.a2ui-surface');
    win.originalAnimation = win.originalHost?.getAnimations()[0];
    const existingMessages = [...(win.surfaces[0].messages || [])];
    existingMessages.push({
      updateComponents: {
        components: [{ id: 'label', component: 'Text', text: 'Updated choice' }],
      },
    });
    win.surfaces[0] = {
      ...win.surfaces[0],
      persistent: true,
      messages: existingMessages,
    };

    const added: A2UISurface = {
      surface_id: 'added',
      placement: { left_pct: 25, top_pct: 25, width_pct: 24 },
      messages: [
        {
          createSurface: {
            surfaceId: 'added',
            dataModel: { hp: 40 },
            components: [
              { id: 'root', component: 'Card', child: 'col' },
              { id: 'col', component: 'Column', children: ['hdr', 'hp', 'div', 'grid'] },
              { id: 'hdr', component: 'Row', children: ['ico', 'title'] },
              { id: 'ico', component: 'Icon', name: 'heart' },
              { id: 'title', component: 'Text', variant: 'heading', text: 'Status' },
              {
                id: 'hp',
                component: 'Progress',
                variant: 'health',
                label: 'HP',
                value: { path: '/hp' },
                max: 100,
              },
              { id: 'div', component: 'Divider' },
              { id: 'grid', component: 'Grid', columns: 2, gap: 'small', children: ['b'] },
              { id: 'b', component: 'Button', child: 'bt', action: { event: { name: 'ok' } } },
              { id: 'bt', component: 'Text', text: 'OK' },
            ],
          },
        },
        { updateDataModel: { path: '/hp', value: 75 } },
      ],
    };
    win.surfaces.push(added);
    win.renderer.render(win.surfaces);
  });

  await expect(page.locator('[data-surface-id="choice"] .a2ui-button')).toHaveText('Updated choice');
  await expect(page.locator('[data-surface-id="choice"]')).toHaveClass(/persistent/);
  await expect(page.locator('[data-surface-id="choice"] .a2ui-move')).toHaveText('📌');

  const lifecycleState = await page.evaluate(() => {
    const win = window as unknown as A2UIHarnessWindow;
    const choiceHost = document.querySelector('[data-surface-id="choice"]');
    const addedHost = document.querySelector('[data-surface-id="added"]');
    return {
      sameHost: win.originalHost === choiceHost,
      sameAnimation: choiceHost?.getAnimations()[0] === win.originalAnimation,
      addedArriving: Boolean(
        addedHost?.getAnimations().some(
          (animation) =>
            'animationName' in animation &&
            (animation as CSSAnimation).animationName === 'a2ui-arrive',
        ),
      ),
    };
  });
  expect(lifecycleState).toEqual({
    sameHost: true,
    sameAnimation: true,
    addedArriving: true,
  });

  await expect(page.locator('[data-surface-id="added"] .a2ui-progress-reading')).toHaveText('75 / 100');
  await expect(page.locator('[data-surface-id="added"] .a2ui-icon')).toHaveText('♥');

  await page.evaluate(() => {
    const win = window as unknown as A2UIHarnessWindow;
    win.surfaces.splice(0, 1);
    win.renderer.render(win.surfaces);
  });

  await expect(page.locator('[data-surface-id="choice"]')).toHaveCount(0);
  await expect(page.locator('[data-surface-id="added"]')).toHaveCount(1);

  // Clicking the delete button issues DELETE and removes the host.
  await page.locator('[data-surface-id="added"] .a2ui-delete').click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].url).toBe('/surfaces/added');
  expect(requests[0].method).toBe('DELETE');
  await expect(page.locator('[data-surface-id="added"]')).toHaveCount(0);
});

test('failed delete re-enables the delete button and keeps the surface', async ({
  canvas,
  page,
}) => {
  const requests = await setupSurfacePage(canvas, page, { deleteStatus: 500 });
  const deleteBtn = page.locator('.a2ui-delete');
  await deleteBtn.click();

  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].method).toBe('DELETE');
  await expect(deleteBtn).toBeEnabled();
  await expect(page.locator('.a2ui-surface')).toHaveCount(1);
});

test('full canvas wires compiled a2ui-renderer from /api/latest and persists drags', async ({
  canvas,
  page,
}) => {
  const { apiRequests } = await canvas.openCanvas({
    isOrator: true,
    viewport: { width: 1400, height: 900 },
    apiRoutes: {
      '/api/latest': {
        viewer_collab_enabled: true,
        interactive_surfaces: [
          {
            surface_id: 'choice',
            placement: { left_pct: 50, top_pct: 50, width_pct: 28 },
            messages: [
              {
                createSurface: {
                  surfaceId: 'choice',
                  components: [
                    { id: 'root', component: 'Card', child: 'btn' },
                    {
                      id: 'btn',
                      component: 'Button',
                      child: 'lbl',
                      action: { event: { name: 'pick' } },
                    },
                    { id: 'lbl', component: 'Text', text: 'Pick me' },
                  ],
                },
              },
            ],
          },
        ],
      },
      '/api/a2ui/surfaces/choice': { ok: true },
      '/api/a2ui/action': { ok: true },
    },
  });

  const surface = page.locator('[data-surface-id="choice"]');
  await expect(surface).toBeVisible();
  await expect(surface.locator('.a2ui-button')).toHaveText('Pick me');

  const move = surface.locator('.a2ui-move');
  const bounds = (await move.boundingBox())!;
  const x = bounds.x + bounds.width / 2;
  const y = bounds.y + bounds.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 60, y + 40);
  await page.mouse.up();

  await expect
    .poll(() => apiRequests.filter((req) => req.method === 'PATCH').length)
    .toBe(1);
  const patchReq = apiRequests.find((req) => req.method === 'PATCH')!;
  expect(patchReq.url).toContain('/api/a2ui/surfaces/choice?theater_id=stage');

  await surface.locator('.a2ui-button').click();
  await expect
    .poll(() => apiRequests.filter((req) => req.method === 'POST' && req.pathname === '/api/a2ui/action').length)
    .toBe(1);
});

