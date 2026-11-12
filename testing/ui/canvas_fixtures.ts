import fs from 'node:fs';
import path from 'node:path';
import {
  expect,
  test as base,
  type BrowserContext,
  type BrowserContextOptions,
  type Page,
  type Route,
  type WebSocketRoute,
} from '@playwright/test';

export const CANVAS_ORIGIN = 'https://canvas.test';
export const CANVAS_WS_ORIGIN = 'wss://canvas.test';
export const REPO_ROOT = path.resolve(__dirname, '..', '..');
export const STATIC_DIR = path.join(REPO_ROOT, 'static');
export const TEMPLATES_DIR = path.join(REPO_ROOT, 'templates');

const IMPORT_HELPER_INIT_SCRIPT =
  'window.__importModule = (moduleUrl) => import(moduleUrl);';

const CONTENT_TYPES: Readonly<Record<string, string>> = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.jpeg': 'image/jpeg',
  '.jpg': 'image/jpeg',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.txt': 'text/plain; charset=utf-8',
  '.webp': 'image/webp',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
};

declare global {
  interface Window {
    __importModule: <TModule = Record<string, unknown>>(moduleUrl: string) => Promise<TModule>;
    _isActiveOratorState?: boolean;
  }
}

export interface MountCanvasOptions {
  /** Pathname on https://canvas.test to serve the HTML shell at. Defaults to '/'. */
  urlPath?: string;
  /** Optional query string (including leading '?') when navigating to urlPath. */
  query?: string;
  /** Body HTML markup to render inside <body>. */
  html?: string;
  /** Extra raw HTML to append inside <head>. */
  headHtml?: string;
  /** Stylesheet URLs (for example '/static/css/canvas-navigation.css') linked in <head>. */
  stylesheets?: string[];
  /** Inline CSS strings placed in <style> tags inside <head>. */
  inlineStyles?: string[];
  /** Inline <script type="module"> blocks rendered at the end of <body>. */
  moduleScripts?: string[];
  /** Initial localStorage key/value pairs seeded before page scripts execute. */
  localStorage?: Readonly<Record<string, string>>;
  /** Optional viewport override applied before navigation. */
  viewport?: { width: number; height: number };
}

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type ApiRouteCallback = (route: Route, url: URL) => Promise<void> | void;
export type ApiRouteHandler = JsonValue | ApiRouteCallback;

export interface OpenCanvasOptions {
  /** Theater identifier passed as ?theater_id=<id>. Defaults to 'stage'. */
  theaterId?: string;
  /** Whether the mocked user is the active orator and theater owner. Defaults to true. */
  isOrator?: boolean;
  /** Whether viewer collaboration is enabled in mocked baton/latest responses. Defaults to true. */
  collabEnabled?: boolean;
  /** Mocked authenticated user identity. */
  user?: { id: number; username: string };
  /** Mocked theater owner id. Defaults to 1. */
  ownerId?: number;
  /** LocalStorage entries merged after default canvas tutorial flags. */
  localStorage?: Readonly<Record<string, string>>;
  /** Disable default how-to / collab guide localStorage flags. */
  skipDefaultLocalStorage?: boolean;
  /** Per-pathname API overrides (JSON value or custom Playwright Route handler). */
  apiRoutes?: Readonly<Record<string, ApiRouteHandler>>;
  /** Fulfill unhandled /api/* requests on https://canvas.test with `{}`. Defaults to true. */
  allowFallbackApiEmptyJson?: boolean;
  /** Optional handler for wss://canvas.test/ws/** connections. */
  onWebSocket?: (ws: WebSocketRoute) => void;
  /** Optional viewport override applied before navigation. */
  viewport?: { width: number; height: number };
}

export interface RecordedApiRequest {
  method: string;
  pathname: string;
  url: string;
  postData: string | null;
}

export interface OpenCanvasResult {
  theaterId: string;
  apiRequests: RecordedApiRequest[];
}

export interface CanvasHarness {
  readonly page: Page;
  readonly origin: string;
  readonly unexpectedRequests: readonly string[];
  readonly pageErrors: readonly Error[];
  allowPageErrors(): void;
  clearUnexpectedRequests(): string[];
  mount(options?: MountCanvasOptions): Promise<void>;
  openCanvas(options?: OpenCanvasOptions): Promise<OpenCanvasResult>;
  exposeModule(modulePath: string, windowName: string): Promise<void>;
  addModuleScript(code: string): Promise<void>;
  serveRoute(
    urlOrPredicate: string | RegExp | ((url: URL) => boolean),
    handler: (route: Route) => Promise<void> | void,
  ): Promise<void>;
  createIsolatedContext(options?: BrowserContextOptions): Promise<BrowserContext>;
}

interface IsolationState {
  unexpectedRequests: string[];
  extraContexts: BrowserContext[];
}

export interface CanvasFixtures {
  isolatedContext: IsolationState;
  canvas: CanvasHarness;
}

function escapeHtmlAttr(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('"', '&quot;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

function buildDocumentHtml(options: MountCanvasOptions): string {
  const stylesheetTags = (options.stylesheets ?? [])
    .map((href) => `<link rel="stylesheet" href="${escapeHtmlAttr(href)}">`)
    .join('\n    ');
  const styleTags = (options.inlineStyles ?? [])
    .map((css) => `<style>\n${css}\n</style>`)
    .join('\n    ');
  const moduleTags = (options.moduleScripts ?? [])
    .map((code) => `<script type="module">\n${code}\n</script>`)
    .join('\n    ');
  const headExtra = options.headHtml ?? '';

  return `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8">
    ${stylesheetTags}
    ${styleTags}
    ${headExtra}
  </head>
  <body>
    ${options.html ?? ''}
    ${moduleTags}
  </body>
</html>`;
}

async function fulfillStaticAsset(
  route: Route,
  url: URL,
  unexpectedRequests: string[],
): Promise<void> {
  const encodedRelative = url.pathname.slice('/static/'.length);
  const relativePath = decodeURIComponent(encodedRelative);
  const resolvedPath = path.resolve(STATIC_DIR, relativePath);
  const staticPrefix = STATIC_DIR.endsWith(path.sep) ? STATIC_DIR : `${STATIC_DIR}${path.sep}`;

  if (resolvedPath !== STATIC_DIR && !resolvedPath.startsWith(staticPrefix)) {
    unexpectedRequests.push(`Blocked static path traversal: ${url.pathname}`);
    await route.abort('blockedbyclient');
    return;
  }

  if (!fs.existsSync(resolvedPath) || !fs.statSync(resolvedPath).isFile()) {
    unexpectedRequests.push(`Missing static asset: ${url.pathname}`);
    await route.fulfill({
      status: 404,
      contentType: 'text/plain; charset=utf-8',
      body: `Static asset not found: ${url.pathname}`,
    });
    return;
  }

  const body = await fs.promises.readFile(resolvedPath);
  const ext = path.extname(resolvedPath).toLowerCase();
  const contentType = CONTENT_TYPES[ext] ?? 'application/octet-stream';
  await route.fulfill({ body, contentType });
}

async function attachContextIsolation(
  context: BrowserContext,
  unexpectedRequests: string[],
): Promise<void> {
  await context.addInitScript({ content: IMPORT_HELPER_INIT_SCRIPT });

  // Registered first (oldest) so any test or static route runs before these fallbacks.
  await context.route('**/*', async (route: Route) => {
    unexpectedRequests.push(`HTTP ${route.request().method()} ${route.request().url()}`);
    await route.abort('blockedbyclient');
  });

  await context.routeWebSocket('**/*', (ws: WebSocketRoute) => {
    unexpectedRequests.push(`WS ${ws.url()}`);
    ws.close();
  });

  await context.route(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname.startsWith('/static/'),
    async (route: Route) => {
      const url = new URL(route.request().url());
      await fulfillStaticAsset(route, url, unexpectedRequests);
    },
  );
}

async function seedLocalStorage(
  page: Page,
  entries: Readonly<Record<string, string>>,
): Promise<void> {
  const pairs = Object.entries(entries);
  if (pairs.length === 0) {
    return;
  }
  await page.addInitScript((items: Array<[string, string]>) => {
    for (const [key, value] of items) {
      window.localStorage.setItem(key, value);
    }
  }, pairs);
}

export const test = base.extend<CanvasFixtures>({
  isolatedContext: [
    async ({ context }, use) => {
      const state: IsolationState = {
        unexpectedRequests: [],
        extraContexts: [],
      };
      await attachContextIsolation(context, state.unexpectedRequests);

      await use(state);

      for (const extra of state.extraContexts) {
        await extra.close();
      }
      expect(
        state.unexpectedRequests,
        `Unmocked browser requests: ${state.unexpectedRequests.join(', ')}`,
      ).toEqual([]);
    },
    { auto: true },
  ],

  canvas: async ({ page, browser, isolatedContext }, use) => {
    const pageErrors: Error[] = [];
    let allowErrors = false;
    page.on('pageerror', (error: Error) => {
      pageErrors.push(error);
    });

    const harness: CanvasHarness = {
      page,
      origin: CANVAS_ORIGIN,
      unexpectedRequests: isolatedContext.unexpectedRequests,
      pageErrors,

      allowPageErrors(): void {
        allowErrors = true;
      },

      clearUnexpectedRequests(): string[] {
        return isolatedContext.unexpectedRequests.splice(0);
      },

      async mount(options: MountCanvasOptions = {}): Promise<void> {
        const urlPath = options.urlPath ?? '/';
        if (!urlPath.startsWith('/')) {
          throw new Error(`MountCanvasOptions.urlPath must start with '/', got: ${urlPath}`);
        }
        const query = options.query ?? '';
        if (query && !query.startsWith('?')) {
          throw new Error(`MountCanvasOptions.query must start with '?', got: ${query}`);
        }
        if (options.viewport) {
          await page.setViewportSize(options.viewport);
        }
        if (options.localStorage) {
          await seedLocalStorage(page, options.localStorage);
        }
        const documentHtml = buildDocumentHtml(options);
        await page.route(
          (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === urlPath,
          async (route: Route) => {
            await route.fulfill({
              body: documentHtml,
              contentType: 'text/html; charset=utf-8',
            });
          },
        );
        await page.goto(`${CANVAS_ORIGIN}${urlPath}${query}`);
      },

      async openCanvas(options: OpenCanvasOptions = {}): Promise<OpenCanvasResult> {
        const theaterId = options.theaterId ?? 'stage';
        const isOrator = options.isOrator ?? true;
        const collabEnabled = options.collabEnabled ?? true;
        const user = options.user ?? {
          id: 1,
          username: isOrator ? 'orator' : 'viewer',
        };
        const ownerId = options.ownerId ?? 1;
        const allowFallbackApiEmptyJson = options.allowFallbackApiEmptyJson ?? true;

        if (options.viewport) {
          await page.setViewportSize(options.viewport);
        }

        const storageEntries: Record<string, string> = options.skipDefaultLocalStorage
          ? {}
          : {
              narratron_orator_howto_seen: 'true',
              [`narratron_viewer_collab_guide_seen:${theaterId}`]: 'true',
            };
        if (options.localStorage) {
          Object.assign(storageEntries, options.localStorage);
        }
        await seedLocalStorage(page, storageEntries);

        const templatePath = path.join(TEMPLATES_DIR, 'canvas.html');
        const templateHtml = await fs.promises.readFile(templatePath, 'utf-8');
        const apiRequests: RecordedApiRequest[] = [];
        const customApiRoutes = options.apiRoutes ?? {};
        const encodedTheaterId = encodeURIComponent(theaterId);

        await page.route(
          (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/canvas',
          async (route: Route) => {
            await route.fulfill({
              body: templateHtml,
              contentType: 'text/html; charset=utf-8',
            });
          },
        );

        await page.route('https://fonts.googleapis.com/**', async (route: Route) => {
          await route.fulfill({
            status: 200,
            contentType: 'text/css; charset=utf-8',
            body: '',
          });
        });

        await page.route(`${CANVAS_ORIGIN}/favicon.png`, async (route: Route) => {
          await route.fulfill({ status: 204 });
        });

        await page.route(
          (url: URL) =>
            url.origin === CANVAS_ORIGIN &&
            (url.pathname.startsWith('/api/') ||
              url.pathname.startsWith(`/theaters/${encodedTheaterId}/`)),
          async (route: Route) => {
            const url = new URL(route.request().url());
            apiRequests.push({
              method: route.request().method(),
              pathname: url.pathname,
              url: url.href,
              postData: route.request().postData(),
            });

            if (Object.hasOwn(customApiRoutes, url.pathname)) {
              const custom = customApiRoutes[url.pathname];
              if (typeof custom === 'function') {
                await custom(route, url);
                return;
              }
              await route.fulfill({ json: custom });
              return;
            }

            if (url.pathname === '/api/auth/me') {
              await route.fulfill({ json: { authenticated: true, user } });
              return;
            }
            if (url.pathname === `/api/theaters/${encodedTheaterId}`) {
              await route.fulfill({
                json: {
                  metadata: {
                    is_owner: isOrator,
                    is_active_orator: isOrator,
                  },
                },
              });
              return;
            }
            if (url.pathname === `/api/theaters/${encodedTheaterId}/baton`) {
              await route.fulfill({
                json: {
                  owner: { id: ownerId },
                  active_orator: { id: isOrator ? user.id : 999 },
                  viewer_collab_enabled: collabEnabled,
                },
              });
              return;
            }
            if (url.pathname === '/api/latest') {
              await route.fulfill({
                json: { viewer_collab_enabled: collabEnabled },
              });
              return;
            }
            if (url.pathname === `/theaters/${encodedTheaterId}/sticky-notes`) {
              await route.fulfill({ json: { sticky_notes: [] } });
              return;
            }
            if (url.pathname === `/theaters/${encodedTheaterId}/suggestions`) {
              await route.fulfill({ json: { suggestions: [] } });
              return;
            }

            if (allowFallbackApiEmptyJson) {
              await route.fulfill({ json: {} });
              return;
            }
            await route.fallback();
          },
        );

        await page.routeWebSocket(/wss?:\/\/canvas\.test\/ws\/.*/, (ws: WebSocketRoute) => {
          options.onWebSocket?.(ws);
        });

        await page.goto(`${CANVAS_ORIGIN}/canvas?theater_id=${encodedTheaterId}`);
        await page.waitForFunction(
          (expected: boolean) => window._isActiveOratorState === expected,
          isOrator,
          { timeout: 5000 },
        );

        return { theaterId, apiRequests };
      },

      async exposeModule(modulePath: string, windowName: string): Promise<void> {
        if (!modulePath.startsWith('/')) {
          throw new Error(`exposeModule requires a root-relative module path, got: ${modulePath}`);
        }
        await page.evaluate(
          async ({ url, name }) => {
            const mod = await window.__importModule(url);
            (window as unknown as Record<string, unknown>)[name] = mod;
          },
          { url: modulePath, name: windowName },
        );
      },

      async addModuleScript(code: string): Promise<void> {
        await page.addScriptTag({ type: 'module', content: code });
      },

      async serveRoute(
        urlOrPredicate: string | RegExp | ((url: URL) => boolean),
        handler: (route: Route) => Promise<void> | void,
      ): Promise<void> {
        await page.route(urlOrPredicate, handler);
      },

      async createIsolatedContext(
        options: BrowserContextOptions = {},
      ): Promise<BrowserContext> {
        const extra = await browser.newContext({
          baseURL: CANVAS_ORIGIN,
          ...options,
          serviceWorkers: 'block',
        });
        isolatedContext.extraContexts.push(extra);
        await attachContextIsolation(extra, isolatedContext.unexpectedRequests);
        return extra;
      },
    };

    await use(harness);

    if (!allowErrors) {
      expect(
        pageErrors,
        `Uncaught page errors: ${pageErrors.map((err) => err.message).join('; ')}`,
      ).toEqual([]);
    }
  },
});

export { expect };
