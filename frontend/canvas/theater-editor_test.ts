import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  expect,
  STATIC_DIR,
  TEMPLATES_DIR,
  test,
} from '../../testing/ui/canvas_fixtures';

interface DraftFileFixture {
  path: string;
  kind: 'text' | 'image' | 'audio';
}

interface DraftStateFixture {
  draft: {
    theater_id: string;
    name: string;
    revision: number;
    saved_revision: number;
  };
  files: DraftFileFixture[];
  rates: {
    image_credit_rate: number;
    music_credit_rate: number;
    layered_animation_credit_rate: number;
    theater_editor_assistant_credit_rate: number;
  };
}

interface AssistantProposalFixture {
  message: string;
  writes: Array<{ path: string; content: string }>;
  moves: Array<{ source: string; destination: string }>;
  deletions?: string[];
  generations: Array<{
    kind: string;
    name: string;
    prompt: string;
    playlist?: string;
    references?: string[];
  }>;
}

interface SaveRequestPayload {
  name: string;
  revision: number;
  persist?: boolean;
  writes: Array<{ path: string; content: string }>;
}

interface GenerationRequestPayload {
  kind: string;
  name: string;
  prompt: string;
  animation_type?: string;
  references?: string[];
}

interface MoveRequestPayload {
  source: string;
  destination: string;
}

interface PurchaseRequestPayload {
  package_id: string;
  payment_method: string;
  checkout_mode: boolean;
}

interface AttachGameRequestPayload {
  revision: number;
  game_id: string;
  enable_game_tool: boolean;
}

declare global {
  interface Window {
    copiedAuthoringPrompt?: string;
  }
}

interface EditorRouteState {
  accountCredits: number;
  paymentMode: 'success' | 'failure' | 'checkout';
  assistantMode: 'success' | 'insufficient';
  assistantProposal: AssistantProposalFixture | null;
  draft: DraftStateFixture;
  savedFiles: Record<string, string>;
  holdGenerate: boolean;
  releaseGenerate: (() => void) | null;
  holdGenerations: Record<string, boolean>;
  releaseGenerations: Record<string, () => void>;
  uploadError: string | null;
  discardedFiles: string[];
  purchaseRequest: PurchaseRequestPayload | null;
  savedAtPurchase: Record<string, string> | null;
  lastSaveRequest: SaveRequestPayload | null;
  lastMoveRequest: MoveRequestPayload | null;
  lastGenerationRequest: GenerationRequestPayload | null;
  lastAttachGameRequest: AttachGameRequestPayload | null;
  lastUploadEndpoint: string | null;
  lastUploadedFilenames: string[] | null;
  createdDefaultDraft: boolean | undefined;
  resetTheater: boolean;
  downloadedDraft: boolean;
  googleLinkRequest: Record<string, unknown> | null;
}

const PRE_MERGE_SHIM_FILES = new Set([
  'auth-flow.js',
  'credit-purchase.js',
  'device-navigation.js',
]);

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

function prepareEditorHtml(): string {
  let html = fs.readFileSync(path.join(TEMPLATES_DIR, 'theater_editor.html'), 'utf-8');
  html = html.replace('<!-- SHARED_TOPBAR -->', renderSharedTopbar('deploy', false));
  html = html.replace(/<link[^>]+href="https:\/\/fonts\.googleapis\.com[^"]*"[^>]*>/g, '');
  html = html.replace(/<link[^>]+href="\/favicon\.png"[^>]*>/g, '');
  return html;
}

interface ParsedMultipartUpload {
  fields: Record<string, string>;
  filenames: string[];
}

function parseMultipartUpload(postDataBuffer: Buffer | null): ParsedMultipartUpload {
  const fields: Record<string, string> = {};
  const filenames: string[] = [];
  if (!postDataBuffer) return { fields, filenames };

  const raw = postDataBuffer.toString('latin1');
  const firstLineEnd = raw.indexOf('\r\n');
  if (firstLineEnd <= 0) return { fields, filenames };
  const boundary = raw.slice(0, firstLineEnd);

  for (const part of raw.split(boundary)) {
    const headerEnd = part.indexOf('\r\n\r\n');
    if (headerEnd === -1) continue;
    const header = part.slice(0, headerEnd);
    const filenameMatch = /filename="([^"]*)"/.exec(header);
    const nameMatch = /name="([^"]+)"/.exec(header);
    if (filenameMatch) {
      filenames.push(Buffer.from(filenameMatch[1], 'latin1').toString('utf-8'));
    } else if (nameMatch) {
      let body = part.slice(headerEnd + 4);
      if (body.endsWith('\r\n')) {
        body = body.slice(0, -2);
      }
      fields[nameMatch[1]] = Buffer.from(body, 'latin1').toString('utf-8');
    }
  }
  return { fields, filenames };
}

function createInitialFiles(): DraftFileFixture[] {
  return [
    { path: 'references/people/captain.png', kind: 'image' },
    { path: 'lore/guide.txt', kind: 'text' },
    { path: 'playlists/Boss fight/chapter 1/theme#1.aac', kind: 'audio' },
    { path: 'lore/characters/crew/rookie.txt', kind: 'text' },
    { path: 'theater.yaml', kind: 'text' },
    { path: 'playlists/Boss fight/description.txt', kind: 'text' },
    { path: 'lore/characters/captain.txt', kind: 'text' },
  ];
}

function structuredCloneDraft(draft: DraftStateFixture): DraftStateFixture {
  return JSON.parse(JSON.stringify(draft)) as DraftStateFixture;
}

async function setupEditorHarness(
  canvas: {
    serveRoute: (
      url: (url: URL) => boolean,
      handler: (route: Route) => Promise<void>,
    ) => Promise<void>;
  },
  page: import('@playwright/test').Page,
): Promise<EditorRouteState> {
  const editorHtml = prepareEditorHtml();
  const state: EditorRouteState = {
    accountCredits: 100,
    paymentMode: 'success',
    assistantMode: 'success',
    assistantProposal: null,
    draft: {
      draft: { theater_id: 'theater_tree', name: 'Harbor', revision: 0, saved_revision: 0 },
      files: createInitialFiles(),
      rates: {
        image_credit_rate: 4,
        music_credit_rate: 6,
        layered_animation_credit_rate: 8,
        theater_editor_assistant_credit_rate: 0.1,
      },
    },
    savedFiles: {},
    holdGenerate: false,
    releaseGenerate: null,
    holdGenerations: {},
    releaseGenerations: {},
    uploadError: null,
    discardedFiles: [],
    purchaseRequest: null,
    savedAtPurchase: null,
    lastSaveRequest: null,
    lastMoveRequest: null,
    lastGenerationRequest: null,
    lastAttachGameRequest: null,
    lastUploadEndpoint: null,
    lastUploadedFilenames: null,
    createdDefaultDraft: undefined,
    resetTheater: false,
    downloadedDraft: false,
    googleLinkRequest: null,
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
      const requestUrl = new URL(route.request().url());
      const pathname = requestUrl.pathname;
      const method = route.request().method();

      if (pathname.startsWith('/static/generated/canvas/')) {
        const filename = pathname.slice('/static/generated/canvas/'.length);
        await route.fulfill({
          contentType: 'application/javascript; charset=utf-8',
          body: buildPreMergeSharedShim(filename),
        });
        return;
      }

      if (pathname === '/theater-editor') {
        await route.fulfill({ contentType: 'text/html; charset=utf-8', body: editorHtml });
      } else if (pathname === '/checkout') {
        await route.fulfill({
          contentType: 'text/html; charset=utf-8',
          body: '<!doctype html><html><body>Checkout Stub</body></html>',
        });
      } else if (pathname === '/api/auth/me') {
        await route.fulfill({
          json: {
            authenticated: true,
            user: { id: 7, username: 'harbor_owner', credits: state.accountCredits },
          },
        });
      } else if (pathname === '/api/payments/buy-credits') {
        const body = JSON.parse(route.request().postData() ?? '{}') as PurchaseRequestPayload;
        state.purchaseRequest = body;
        state.savedAtPurchase = { ...state.savedFiles };
        if (state.paymentMode === 'failure') {
          await route.fulfill({
            status: 400,
            json: { detail: 'Checkout is unavailable. Please try again.' },
          });
          return;
        }
        if (state.paymentMode === 'checkout') {
          await route.fulfill({
            json: {
              status: 'ok',
              mode: 'stripe_checkout',
              checkout_url: `${CANVAS_ORIGIN}/checkout`,
            },
          });
          return;
        }
        const packageCredits: Readonly<Record<string, number>> = {
          starter: 100,
          pro: 400,
          ultra: 1000,
        };
        const credits = packageCredits[body.package_id] ?? 0;
        state.accountCredits += credits;
        await route.fulfill({
          json: {
            status: 'ok',
            credits_added: credits,
            user: { credits: state.accountCredits },
          },
        });
      } else if (pathname.endsWith('/file')) {
        const filePath = requestUrl.searchParams.get('path') ?? '';
        const text = state.savedFiles[filePath] ?? `Contents of ${filePath}`;
        if (filePath.endsWith('.png')) {
          await route.fulfill({
            contentType: 'image/png',
            body: Buffer.from(
              'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=',
              'base64',
            ),
          });
        } else if (filePath.endsWith('.svg')) {
          await route.fulfill({ contentType: 'image/svg+xml; charset=utf-8', body: text });
        } else {
          await route.fulfill({ contentType: 'text/plain; charset=utf-8', body: text });
        }
      } else if (pathname.endsWith('/save')) {
        const body = JSON.parse(route.request().postData() ?? '{}') as SaveRequestPayload;
        state.lastSaveRequest = body;
        state.draft.draft.name = body.name;
        state.draft.draft.revision += 1;
        if (body.persist) {
          state.draft.draft.saved_revision = state.draft.draft.revision;
        }
        for (const write of body.writes) {
          state.savedFiles[write.path] = write.content;
          if (!state.draft.files.some((file) => file.path === write.path)) {
            state.draft.files.push({ path: write.path, kind: 'text' });
          }
        }
        await route.fulfill({ json: structuredCloneDraft(state.draft) });
      } else if (pathname.endsWith('/delete')) {
        const body = JSON.parse(route.request().postData() ?? '{}') as { path: string };
        state.draft.files = state.draft.files.filter((file) => file.path !== body.path);
        state.draft.draft.revision += 1;
        delete state.savedFiles[body.path];
        await route.fulfill({ json: structuredCloneDraft(state.draft) });
      } else if (pathname.endsWith('/move')) {
        const body = JSON.parse(route.request().postData() ?? '{}') as MoveRequestPayload;
        state.lastMoveRequest = body;
        const target = state.draft.files.find((file) => file.path === body.source);
        if (target) target.path = body.destination;
        if (state.savedFiles[body.source] !== undefined) {
          state.savedFiles[body.destination] = state.savedFiles[body.source];
          delete state.savedFiles[body.source];
        }
        state.draft.draft.revision += 1;
        await route.fulfill({ json: structuredCloneDraft(state.draft) });
      } else if (pathname.endsWith('/apply')) {
        const body = JSON.parse(route.request().postData() ?? '{}') as {
          proposal: AssistantProposalFixture;
        };
        const proposal = body.proposal;
        for (const write of proposal.writes ?? []) {
          state.savedFiles[write.path] = write.content;
          if (!state.draft.files.some((file) => file.path === write.path)) {
            state.draft.files.push({ path: write.path, kind: 'text' });
          }
        }
        for (const move of proposal.moves ?? []) {
          const target = state.draft.files.find((file) => file.path === move.source);
          if (target) target.path = move.destination;
        }
        for (const deletion of proposal.deletions ?? []) {
          state.draft.files = state.draft.files.filter((file) => file.path !== deletion);
          delete state.savedFiles[deletion];
        }
        state.draft.draft.revision += 1;
        await route.fulfill({ json: structuredCloneDraft(state.draft) });
      } else if (pathname.endsWith('/drafts') && method === 'POST') {
        state.createdDefaultDraft = true;
        state.draft.draft.theater_id = 'theater_new';
        state.draft.files = createInitialFiles();
        await route.fulfill({ json: structuredCloneDraft(state.draft) });
      } else if (pathname.endsWith('/generate')) {
        if (state.holdGenerate) {
          await new Promise<void>((resolve) => {
            state.releaseGenerate = resolve;
          });
        }
        const body = JSON.parse(route.request().postData() ?? '{}') as GenerationRequestPayload;
        if (state.holdGenerations[body.name]) {
          await new Promise<void>((resolve) => {
            state.releaseGenerations[body.name] = resolve;
          });
        }
        state.lastGenerationRequest = body;
        const isStamp = body.kind === 'stamp';
        const assetPath = isStamp
          ? `stamps/${body.name || 'goblin'}.png`
          : `references/${body.name || 'harbor'}.png`;
        if (!state.draft.files.some((file) => file.path === assetPath)) {
          state.draft.files.push({ path: assetPath, kind: 'image' });
        }
        state.draft.draft.revision += 1;
        state.accountCredits -= 4;
        await route.fulfill({
          json: {
            state: structuredCloneDraft(state.draft),
            path: assetPath,
            credits: state.accountCredits,
            credits_charged: 4,
          },
        });
      } else if (pathname.endsWith('/assistant')) {
        if (state.assistantMode === 'insufficient') {
          await route.fulfill({
            status: 402,
            json: {
              detail:
                'Each assistant turn requires 0.1 credits. Buy credits using the balance at the top of this page.',
            },
          });
          return;
        }
        state.accountCredits -= 0.1;
        const proposal: AssistantProposalFixture = state.assistantProposal ?? {
          message: 'Here are ideas for your world.',
          writes: [],
          moves: [],
          generations: [],
        };
        await route.fulfill({
          json: {
            revision: state.draft.draft.revision,
            proposal,
            credits: state.accountCredits,
            credits_charged: 0.1,
          },
        });
      } else if (pathname.endsWith('/google-link')) {
        const body = JSON.parse(route.request().postData() ?? '{}') as {
          harvest?: boolean;
          target_name?: string;
        };
        state.googleLinkRequest = body;
        if (body.harvest) {
          state.accountCredits -= 0.1;
          await route.fulfill({
            json: {
              kind: 'doc',
              harvested: true,
              revision: state.draft.draft.revision,
              proposal: {
                message: 'Harvested Google Doc into theater.',
                writes: [{ path: 'lore/harvested.txt', content: 'Harvested' }],
                moves: [],
                generations: [],
              },
              credits: state.accountCredits,
              credits_charged: 0.1,
              state: structuredCloneDraft(state.draft),
            },
          });
          return;
        }
        const assetPath = body.target_name
          ? `references/${body.target_name}.png`
          : 'references/imported_gdrive.png';
        state.draft.files.push({ path: assetPath, kind: 'image' });
        state.draft.draft.revision += 1;
        await route.fulfill({
          json: {
            kind: 'image',
            harvested: false,
            path: assetPath,
            revision: state.draft.draft.revision,
            state: structuredCloneDraft(state.draft),
            message: `Imported ${assetPath} from Google.`,
          },
        });
      } else if (pathname.startsWith('/api/games/') && pathname.endsWith('/cover')) {
        await route.fulfill({
          contentType: 'image/svg+xml; charset=utf-8',
          body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="#6366f1"/></svg>',
        });
      } else if (pathname === '/api/games') {
        await route.fulfill({
          json: [
            {
              id: 'example-game',
              title: 'Crown & Corners Checkers',
              description: 'Classic American Checkers on an 8x8 board.',
              genre: 'Board Game / Strategy',
              tags: ['Checkers', 'Board Game'],
              declaration_file: 'game/game.yaml',
              has_panel: true,
              svg_count: 1,
              stamp_count: 1,
              cover_image: 'game/crest.svg',
            },
            {
              id: 'wacky-chess',
              title: 'Wacky Chess',
              description: 'Multiplayer chess with dragons and cake shields.',
              genre: 'Comedy / Strategy',
              tags: ['Chess', 'Chaos'],
              declaration_file: 'game/game.yaml',
              has_panel: false,
              svg_count: 17,
              stamp_count: 0,
            },
          ],
        });
      } else if (pathname.endsWith('/game')) {
        const body = JSON.parse(route.request().postData() ?? '{}') as AttachGameRequestPayload;
        state.lastAttachGameRequest = body;
        state.draft.files = state.draft.files.filter((file) => !file.path.startsWith('game/'));
        if (body.game_id === 'example-game') {
          state.draft.files.push(
            { path: 'game/game.yaml', kind: 'text' },
            { path: 'game/panel.html', kind: 'text' },
            { path: 'game/crest.svg', kind: 'image' },
            { path: 'stamps/crown.png', kind: 'image' },
          );
          state.savedFiles['game/game.yaml'] = 'version: 2\n# Checkers\n';
          state.savedFiles['game/panel.html'] = '<div>Checkers Panel</div>';
        } else {
          state.draft.files.push({ path: 'game/game.yaml', kind: 'text' });
          state.savedFiles['game/game.yaml'] = 'version: 2\n# Wacky Chess\n';
        }
        state.draft.draft.revision += 1;
        await route.fulfill({ json: structuredCloneDraft(state.draft) });
      } else if (pathname.endsWith('/reset')) {
        state.resetTheater = true;
        await route.fulfill({ json: structuredCloneDraft(state.draft) });
      } else if (pathname.endsWith('/download')) {
        state.downloadedDraft = true;
        await route.fulfill({
          contentType: 'application/zip',
          body: Buffer.from('mock zip', 'utf-8'),
        });
      } else if (pathname.endsWith('/upload') || pathname.endsWith('/drafts/import')) {
        state.lastUploadEndpoint = pathname;
        if (state.uploadError) {
          await route.fulfill({ status: 400, json: { detail: state.uploadError } });
          return;
        }
        const parsed = parseMultipartUpload(route.request().postDataBuffer());
        if (pathname.endsWith('/drafts/import')) {
          state.draft.draft.theater_id = 'theater_imported';
          state.draft.draft.name = parsed.fields['name'] ?? 'Imported';
          state.draft.files = [{ path: 'theater.yaml', kind: 'text' }];
        }
        state.lastUploadedFilenames = parsed.filenames;
        const discarded = state.discardedFiles;
        for (const filename of parsed.filenames.filter((name) => !discarded.includes(name))) {
          const destPath = filename.includes('/') ? filename : `references/${filename}`;
          if (!state.draft.files.some((file) => file.path === destPath)) {
            state.draft.files.push({
              path: destPath,
              kind: destPath.endsWith('.txt') ? 'text' : 'image',
            });
          }
        }
        state.draft.draft.revision += 1;
        await route.fulfill({
          json: {
            ...structuredCloneDraft(state.draft),
            imported_files: parsed.filenames.length - discarded.length,
            discarded_files: discarded,
          },
        });
      } else if (pathname.startsWith('/api/theater-editor/')) {
        await route.fulfill({ json: structuredCloneDraft(state.draft) });
      } else {
        await route.abort('failed');
        throw new Error(`Unexpected request: ${method} ${pathname}`);
      }
    },
  );

  await page.goto(`${CANVAS_ORIGIN}/theater-editor?theater_id=theater_tree`);
  await expect(page.locator('#workspace')).toBeVisible();
  return state;
}

for (const width of [1280, 390] as const) {
  test(`external authoring copies prompt without changing theater (width=${width})`, async ({
    canvas,
    page,
  }) => {
    const state = await setupEditorHarness(canvas, page);
    await page.setViewportSize({ width, height: 900 });

    const trigger = page.getByRole('button', { name: 'Build in your favorite AI app' });
    const dialog = page.getByRole('dialog', { name: 'Build in your favorite AI app' });
    await expect(trigger).toBeVisible();
    await expect(dialog).not.toBeVisible();
    expect(
      await trigger.evaluate((el) => el.getBoundingClientRect().right <= window.innerWidth),
    ).toBe(true);

    await trigger.click();
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('link', { name: 'Download authoring kit' })).toHaveAttribute(
      'href',
      '/theater-authoring-kit',
    );
    await expect(dialog.getByRole('link', { name: 'Open ChatGPT' })).toHaveAttribute(
      'href',
      'https://chatgpt.com/',
    );

    await page.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: {
          writeText: async (text: string) => {
            window.copiedAuthoringPrompt = text;
          },
        },
      });
    });

    await dialog.getByRole('button', { name: 'Copy starter prompt' }).click();
    await expect(page.locator('#authoring-copy-feedback')).toContainText('Copied');
    await expect(page.locator('#builder-status')).toContainText('Starter prompt copied');
    const copied = await page.evaluate(() => window.copiedAuthoringPrompt);
    expect(copied).toBe(await page.locator('#authoring-prompt').inputValue());
    expect(state.savedFiles).toEqual({});
    expect(
      await dialog.evaluate(
        (el) =>
          el.scrollWidth <= el.clientWidth &&
          el.getBoundingClientRect().right <= window.innerWidth,
      ),
    ).toBe(true);

    await dialog.getByRole('button', { name: 'Done' }).click();
    await expect(dialog).not.toBeVisible();
  });
}

test('external authoring selects prompt when clipboard unavailable', async ({ canvas, page }) => {
  await setupEditorHarness(canvas, page);
  await page.getByRole('button', { name: 'Build in your favorite AI app' }).click();
  const dialog = page.getByRole('dialog', { name: 'Build in your favorite AI app' });
  await expect(dialog).toBeVisible();

  await page.evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
  });
  await dialog.getByRole('button', { name: 'Copy starter prompt' }).click();
  await expect(page.locator('#authoring-copy-feedback')).toContainText('Selected');
  await expect(page.locator('#builder-status')).toContainText('Copy the selected starter prompt');
  await expect(page.locator('#authoring-prompt')).toBeFocused();
  const promptLen = (await page.locator('#authoring-prompt').inputValue()).length;
  const selLen = await page
    .locator('#authoring-prompt')
    .evaluate((el) => (el as HTMLTextAreaElement).selectionEnd - (el as HTMLTextAreaElement).selectionStart);
  expect(selLen).toBe(promptLen);

  await dialog.getByRole('button', { name: 'Close' }).click();
  await expect(dialog).not.toBeVisible();
});

for (const width of [1280, 390] as const) {
  test(`nested folders expand with mouse and keyboard (width=${width})`, async ({
    canvas,
    page,
  }) => {
    await setupEditorHarness(canvas, page);
    await page.setViewportSize({ width, height: 900 });

    await expect(page.locator('#file-count')).toHaveText('7 files');
    await expect(page.locator('.file-button[title="theater.yaml"]')).toBeVisible();
    expect(
      await page.locator('#file-list > details > summary .folder-name').allTextContents(),
    ).toEqual(['lore', 'playlists', 'references']);

    await page.locator('details[data-path="references"] > summary').click();
    const charsFolder = page.locator('details[data-path="references/characters"]');
    await expect(charsFolder).toBeVisible();
    await expect(charsFolder.locator(':scope > summary .folder-count')).toHaveText('0');
    await charsFolder.locator(':scope > summary').click();
    await expect(charsFolder.locator('.empty-folder-hint')).toHaveText(
      'No character portraits yet (references/characters/<Name>/1.webp)',
    );

    const scenesFolder = page.locator('details[data-path="references/scenes"]');
    await expect(scenesFolder).toBeVisible();
    await expect(scenesFolder.locator(':scope > summary .folder-count')).toHaveText('0');
    await scenesFolder.locator(':scope > summary').click();
    await expect(scenesFolder.locator('.empty-folder-hint')).toHaveText(
      'No scene images yet (references/scenes/<Scene>/1.webp)',
    );

    const lore = page.locator('details[data-path="lore"]');
    const captain = page.locator('.file-button[title="lore/characters/captain.txt"]');
    await expect(captain).not.toBeVisible();
    await expect(lore.locator(':scope > summary .folder-count')).toHaveText('3');
    await lore.locator(':scope > summary').focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('.file-button[title="lore/guide.txt"]')).toBeVisible();

    const characters = page.locator('details[data-path="lore/characters"]');
    await characters.locator(':scope > summary').click();
    await expect(captain).toHaveText('≡ captain.txt');
    await captain.click();
    await expect(captain).toBeFocused();
    await expect(captain).toHaveAttribute('aria-current', 'true');
    await expect(page.locator('#selected-path')).toHaveText('lore/characters/captain.txt');
    await expect(page.locator('#file-editor')).toHaveValue('Contents of lore/characters/captain.txt');

    await page.locator('details[data-path="lore/characters/crew"] > summary').click();
    await expect(page.locator('.file-button[title="lore/characters/crew/rookie.txt"]')).toBeVisible();
    await characters.locator(':scope > summary').click();
    await expect(captain).not.toBeVisible();
    await characters.locator(':scope > summary').click();
    await expect(page.locator('.file-button[title="lore/characters/crew/rookie.txt"]')).toBeVisible();
    expect(
      await page.locator('.files-panel').evaluate((panel) => panel.scrollWidth <= panel.clientWidth),
    ).toBe(true);
  });
}

test('edits and folder choices survive save and reload', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  await page.locator('details[data-path="lore"] > summary').click();
  await page.locator('details[data-path="lore/characters"] > summary').click();
  const captain = page.locator('.file-button[title="lore/characters/captain.txt"]');
  await captain.click();
  await page.locator('#file-editor').fill('An experienced harbor captain.');
  await page.locator('.file-button[title="lore/guide.txt"]').click();
  await captain.click();
  await expect(page.locator('#file-editor')).toHaveValue('An experienced harbor captain.');

  await page.locator('details[data-path="lore"] > summary').click();
  await page.locator('#save-draft').click();
  await expect(page.locator('#builder-status')).toHaveText('Theater saved.');
  await expect(captain).not.toBeVisible();
  await page.locator('details[data-path="lore"] > summary').click();
  await expect(captain).toBeVisible();
  await expect(captain).toHaveAttribute('aria-current', 'true');
  expect(state.savedFiles).toEqual({
    'lore/characters/captain.txt': 'An experienced harbor captain.',
  });

  await page.locator('#reload-draft').click();
  await expect(page.locator('#builder-status')).toHaveText(
    'Theater loaded. Select a file or ask your assistant to begin.',
  );
  await expect(captain).toBeVisible();
  await captain.click();
  await expect(page.locator('#file-editor')).toHaveValue('An experienced harbor captain.');

  await page.locator('#new-draft').click();
  await page.locator('#start-default').click();
  await expect(page.locator('#builder-status')).toHaveText('Default theater ready. Make it your own.');
  await expect(captain).not.toBeVisible();
});

test('nested assets preview and new files join the tree', async ({ canvas, page }) => {
  await setupEditorHarness(canvas, page);
  await page.locator('details[data-path="references"] > summary').click();
  await page.locator('details[data-path="references/people"] > summary').click();
  await page.locator('.file-button[title="references/people/captain.png"]').click();
  await expect(page.locator('#media-preview img')).toHaveAttribute(
    'alt',
    'references/people/captain.png',
  );
  await expect(page.locator('#media-preview')).toBeVisible();

  await page.locator('details[data-path="playlists"] > summary').click();
  await page.locator('details[data-path="playlists/Boss fight"] > summary').click();
  await page.locator('details[data-path="playlists/Boss fight/chapter 1"] > summary').click();
  await page.locator('.file-button[title="playlists/Boss fight/chapter 1/theme#1.aac"]').click();
  await expect(page.locator('#media-preview audio')).toHaveAttribute('controls', '');
  await expect(page.locator('#download-file')).toHaveAttribute(
    'href',
    '/api/theater-editor/theater_tree/file?path=playlists%2FBoss%20fight%2Fchapter%201%2Ftheme%231.aac&revision=0',
  );

  await page.locator('#new-file').click();
  await page.locator('#new-file-path').fill('lore/locations/harbor.txt');
  await page.locator('#new-file-dialog button[value="create"]').click();
  await expect(page.locator('#file-count')).toHaveText('8 files');
  await expect(
    page.locator('.file-button[title="playlists/Boss fight/chapter 1/theme#1.aac"]'),
  ).toBeVisible();
  await page.locator('details[data-path="lore"] > summary').click();
  await page.locator('details[data-path="lore/locations"] > summary').click();
  await page.locator('.file-button[title="lore/locations/harbor.txt"]').click();
  await expect(page.locator('#file-editor')).toHaveValue('');
});

for (const kind of ['reference', 'scene', 'character', 'stamp'] as const) {
  test(`regenerating selected asset refreshes preview (kind=${kind})`, async ({
    canvas,
    page,
  }) => {
    await setupEditorHarness(canvas, page);
    const assetPath = kind === 'stamp' ? 'stamps/harbor.png' : 'references/harbor.png';
    await page.locator('.manual-generation > summary').click();
    await page.locator('#generation-kind').selectOption(kind);
    await page.locator('#generation-name').fill('harbor');
    await page.locator('#generation-prompt').fill('A misty harbor.');
    await page.locator('#generation-submit').click();
    await expect(page.locator('#builder-status')).toHaveText(
      `Added ${assetPath} to your working theater.`,
    );
    await page.getByRole('button', { name: `Open ${assetPath}`, exact: true }).click();
    const preview = page.locator('#media-preview img');
    const firstUrl = await preview.getAttribute('src');
    expect(firstUrl).not.toBeNull();
    expect(firstUrl?.endsWith('&revision=1')).toBe(true);

    for (const revision of [2, 3]) {
      await page.locator('#generation-prompt').fill(`A harbor variation ${revision}.`);
      await page.locator('#generation-submit').click();
      await expect(preview).toHaveAttribute('src', new RegExp(`&revision=${revision}$`));
      await expect(page.locator('#builder-status')).toHaveText(
        `Added ${assetPath} to your working theater.`,
      );
      const currentUrl = await preview.getAttribute('src');
      expect(currentUrl).not.toBe(firstUrl);
      if (currentUrl) {
        await expect(page.locator('#download-file')).toHaveAttribute('href', currentUrl);
      }
    }

    await page.getByRole('button', { name: `Open ${assetPath}`, exact: true }).first().click();
    await expect(preview).toHaveAttribute('src', /&revision=3$/);
  });
}

for (const width of [1280, 390] as const) {
  test(`topbar shows credits and refreshes after generation (width=${width})`, async ({
    canvas,
    page,
  }) => {
    await setupEditorHarness(canvas, page);
    await page.setViewportSize({ width, height: 900 });
    if (width < 761) {
      await page.locator('.mobile-menu-toggle').click();
    }
    const badge = page.locator('.narratron-topbar .credit-badge');
    await expect(badge).toBeVisible();
    await expect(badge).toHaveText('⚡ 100.0 + Buy');
    expect(await badge.evaluate((el) => getComputedStyle(el).color)).toBe('rgb(52, 211, 153)');
    await expect(page.locator('#credit-balance, .credit-link')).toHaveCount(0);

    const userMenu = page.locator('.user-menu');
    await expect(userMenu.locator('.user-pill')).toHaveText('👤 harbor_owner');
    if (width >= 761) {
      await userMenu.hover();
    }
    await expect(userMenu.locator('.user-menu-item')).toHaveText('Log out');
    await expect(page.locator('.user-account-bar > .auth-nav-btn')).toHaveCount(0);
    if (width < 761) {
      await page.keyboard.press('Escape');
    }

    await page.locator('.manual-generation > summary').click();
    await page.locator('#generation-name').fill('harbor');
    await page.locator('#generation-prompt').fill('A misty harbor.');
    await page.locator('#generation-submit').click();
    await expect(page.locator('#builder-status')).toHaveText(
      'Added references/harbor.png to your working theater.',
    );
    await expect(badge).toHaveText('⚡ 96.0 + Buy');
  });
}

for (const trigger of ['send', 'organize-assets', 'suggest-world'] as const) {
  test(`assistant discloses price and refreshes balance (trigger=${trigger})`, async ({
    canvas,
    page,
  }) => {
    await setupEditorHarness(canvas, page);
    await expect(page.locator('#assistant-send')).toHaveText('Send · 0.1 Cr →');
    await expect(page.locator('#assistant-cost')).toContainText(
      '0.1 Cr per assistant turn, including shortcuts',
    );
    if (trigger === 'send') {
      await page.locator('#assistant-input').fill('Develop my world.');
      await page.locator('#assistant-send').click();
    } else {
      await page.locator(`#${trigger}`).click();
    }
    await expect(page.locator('#assistant-messages')).toContainText(
      'Here are ideas for your world.',
    );
    await expect(page.locator('.credit-badge')).toHaveText('⚡ 99.9 + Buy');
    await expect(page.locator('#builder-status')).toContainText('Charged 0.1 credits');
  });
}

test('pricing info hover button and rates', async ({ canvas, page }) => {
  await setupEditorHarness(canvas, page);
  const infoBtn = page.locator('#assistant-pricing-info');
  await expect(infoBtn).toBeVisible();
  await expect(infoBtn).toHaveText('i');
  await expect(page.locator('#generation-rates')).toHaveText(
    'Image, playlist, and animation pricing follows standard live pricing.',
  );
  await expect(page.locator('#assistant-cost')).toContainText(
    '0.1 Cr per assistant turn, including shortcuts',
  );
  await infoBtn.hover();
  await expect(page.locator('#pricing-tooltip')).toBeVisible();
});

test('unaffordable assistant preserves prompt for retry', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  state.assistantMode = 'insufficient';
  await page.locator('#assistant-input').fill('Develop my world.');
  await page.locator('#assistant-send').click();
  await expect(page.locator('#builder-status')).toContainText(
    'Each assistant turn requires 0.1 credits',
  );
  await expect(page.locator('#assistant-input')).toHaveValue('Develop my world.');
  await expect(page.locator('.credit-badge')).toHaveText('⚡ 100.0 + Buy');
  await expect(page.locator('#assistant-send')).toBeEnabled();

  state.assistantMode = 'success';
  await page.locator('#assistant-send').click();
  await expect(page.locator('.credit-badge')).toHaveText('⚡ 99.9 + Buy');
});

for (const width of [1280, 390] as const) {
  test(`credit badge opens purchase modal without leaving draft (width=${width})`, async ({
    canvas,
    page,
  }) => {
    await setupEditorHarness(canvas, page);
    await page.setViewportSize({ width, height: 900 });
    if (width < 761) {
      await page.locator('.mobile-menu-toggle').click();
    }
    const badge = page.locator('.credit-badge');
    await badge.focus();
    await page.keyboard.press('Enter');
    const modal = page.locator('#buyCreditsModal');
    await expect(modal).toBeVisible();
    await expect(page.locator('#buyCreditsCurrentBalance')).toHaveText('Balance: 100.0 Cr');
    await expect(modal.locator('.package-card')).toHaveCount(3);
    await expect(page.locator('#btnCompletePurchase')).toHaveText('Pay $18.00 & Add 400 Credits');
    await page.locator('#pkgStarter').click();
    await expect(page.locator('#btnCompletePurchase')).toHaveText('Pay $5.00 & Add 100 Credits');
    await page.locator('#pkgUltra').click();
    await expect(page.locator('#btnCompletePurchase')).toHaveText('Pay $40.00 & Add 1000 Credits');
    expect(
      await modal.locator('.modal-card').evaluate((card) => card.scrollWidth <= card.clientWidth),
    ).toBe(true);
    expect(page.url()).toContain('theater_id=theater_tree');

    await modal.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(modal).not.toBeVisible();
    if (width < 761) {
      await page.locator('.mobile-menu-toggle').click();
    }
    await badge.click();
    await expect(modal).toBeVisible();
    await expect(page.locator('#btnCompletePurchase')).toHaveText('Pay $18.00 & Add 400 Credits');
    await page.keyboard.press('Escape');
    await expect(modal).not.toBeVisible();
  });
}

test('credit purchase saves edits and refreshes the balance', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  await page.locator('.file-button[title="theater.yaml"]').click();
  await page.locator('#file-editor').fill('live_agent: {}\n');
  await page.locator('.credit-badge').click();
  await page.locator('#pkgStarter').click();
  await page.locator('#btnCompletePurchase').click();

  await expect(page.locator('.credit-badge')).toHaveText('⚡ 200.0 + Buy');
  expect(state.savedAtPurchase).toEqual({ 'theater.yaml': 'live_agent: {}\n' });
  expect(state.purchaseRequest).toEqual({
    package_id: 'starter',
    payment_method: 'stripe_checkout',
    checkout_mode: true,
  });
  await expect(page.locator('#buyCreditsModalSuccess')).toContainText('Added +100.0 Credits');
  await expect(page.locator('#buyCreditsCurrentBalance')).toHaveText('Balance: 200.0 Cr');
  await expect(page.locator('#draft-state')).toHaveText('Unsaved changes');
});

test('applied changes warn on navigation until theater is saved', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  state.assistantProposal = {
    message: 'Add lore',
    writes: [{ path: 'lore/new.txt', content: 'Harbor' }],
    moves: [],
    generations: [],
  };
  await page.locator('#assistant-input').fill('Develop the harbor');
  await page.locator('#assistant-send').click();
  await page.getByRole('button', { name: 'Apply file changes' }).click();
  await expect(page.locator('#draft-state')).toHaveText('Unsaved changes');

  const warnedBeforeSave = await page.evaluate(() => {
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(warnedBeforeSave).toBe(true);

  await page.locator('#save-draft').click();
  await expect(page.locator('#draft-state')).toHaveText('Saved theater');
  expect(state.lastSaveRequest?.persist).toBe(true);

  const warnedAfterSave = await page.evaluate(() => {
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(warnedAfterSave).toBe(false);
});

test('credit checkout failure can be retried', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  state.paymentMode = 'failure';
  await page.locator('.credit-badge').click();
  await page.locator('#btnCompletePurchase').click();
  await expect(page.locator('#buyCreditsModalError')).toHaveText(
    'Checkout is unavailable. Please try again.',
  );
  await expect(page.locator('#btnCompletePurchase')).toBeEnabled();
  await expect(page.locator('.credit-badge')).toHaveText('⚡ 100.0 + Buy');

  state.paymentMode = 'checkout';
  await page.locator('#btnCompletePurchase').click();
  await page.waitForURL(`${CANVAS_ORIGIN}/checkout`);
});

test('import google drive image ui', async ({ canvas, page }) => {
  await setupEditorHarness(canvas, page);
  await page.locator('#import-google-link').click();
  const dialog = page.locator('#google-link-dialog');
  await expect(dialog).toBeVisible();
  await expect(page.locator('#google-doc-options')).not.toBeVisible();

  await page.locator('#google-link-url').fill('https://drive.google.com/file/d/IMG_HERO/view');
  await page.locator('#google-link-name').fill('hero_art');
  await page.locator('#google-link-submit').click();

  await expect(dialog).not.toBeVisible();
  await expect(page.locator('#builder-status')).toContainText(
    'Imported references/hero_art.png from Google',
  );
  await expect(page.locator('.file-button[title="references/hero_art.png"]')).toBeVisible();
  await expect(page.locator('#selected-path')).toHaveText('references/hero_art.png');
});

test('harvest google doc ui', async ({ canvas, page }) => {
  await setupEditorHarness(canvas, page);
  await page.locator('#harvest-doc-shortcut').click();
  const dialog = page.locator('#google-link-dialog');
  await expect(dialog).toBeVisible();
  await expect(page.locator('#google-doc-options')).toBeVisible();
  await expect(page.locator('#google-link-submit')).toHaveText('Harvest with AI');

  await page.locator('#google-link-url').fill('https://docs.google.com/document/d/DOC_WORLD/edit');
  await page.locator('#google-harvest-prompt').fill('Focus on factions and mysteries');
  await page.locator('#google-link-submit').click();

  await expect(dialog).not.toBeVisible();
  await expect(page.locator('#assistant-proposal')).toBeVisible();
  await expect(page.locator('#assistant-proposal')).toContainText('Review proposed changes');
  await expect(page.locator('#assistant-messages')).toContainText(
    'Harvested Google Doc into theater.',
  );
});

test('assistant proposes and generates stamp ui', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  const kindOptions = await page.locator('#generation-kind option').allTextContents();
  expect(kindOptions).toContain('Stamp token');

  state.assistantProposal = {
    message: 'I propose a goblin scout token for your tactical battlemap.',
    writes: [],
    moves: [],
    generations: [
      {
        kind: 'stamp',
        name: 'goblin',
        prompt: 'A goblin scout mini',
        playlist: 'ambient',
        references: [],
      },
    ],
  };
  await page.locator('#assistant-input').fill('Set up stamps for our encounter.');
  await page.locator('#assistant-send').click();

  await expect(page.locator('#assistant-proposal')).toBeVisible();
  const card = page.locator('.generation-card');
  await expect(card).toBeVisible();
  await expect(card.locator('strong')).toHaveText('Stamp token: goblin');
  await expect(card.locator('p')).toHaveText('A goblin scout mini');
  await expect(card.locator('button')).toHaveText('Generate · 4 Cr');

  await card.locator('button').click();
  await expect(page.locator('#builder-status')).toHaveText(
    'Added stamps/goblin.png to your working theater.',
  );
  await expect(card.locator('button')).toHaveText('Generated');
  await expect(card.locator('.open-asset-link')).toHaveText('Open asset →');
  await expect(page.locator('#assistant-messages .open-asset-link')).toHaveText('Open asset →');

  const stampsFolder = page.locator('details[data-path="stamps"]');
  await expect(stampsFolder).toBeVisible();
  await stampsFolder.locator(':scope > summary').click();
  const stampBtn = page.locator('.file-button[title="stamps/goblin.png"]');
  await expect(stampBtn).toBeVisible();
  await expect(stampBtn).toHaveText('🏷️ goblin.png');
});

test('starting proposal does not block exploring theater and links to open asset', async ({
  canvas,
  page,
}) => {
  const state = await setupEditorHarness(canvas, page);
  state.assistantProposal = {
    message: 'I propose creating a harbor reference image.',
    writes: [],
    moves: [],
    generations: [
      {
        kind: 'reference',
        name: 'harbor',
        prompt: 'A misty harbor scene',
        playlist: 'ambient',
        references: [],
      },
    ],
  };
  await page.locator('#assistant-input').fill('Propose key scene art.');
  await page.locator('#assistant-send').click();

  await expect(page.locator('#assistant-proposal')).toBeVisible();
  const card = page.locator('.generation-card');
  await expect(card).toBeVisible();
  await expect(card.locator('strong')).toHaveText('Reference: harbor');
  await expect(card.locator('button')).toHaveText('Generate · 4 Cr');

  state.holdGenerate = true;
  await card.locator('button').click();

  await expect(card.locator('button')).toHaveText('Generating…');
  await expect(card.locator('button')).toBeDisabled();

  await page.locator('details[data-path="lore"] > summary').click();
  const guideBtn = page.locator('.file-button[title="lore/guide.txt"]');
  await expect(guideBtn).toBeVisible();
  await guideBtn.click();
  await expect(page.locator('#selected-path')).toHaveText('lore/guide.txt');
  await expect(page.locator('#file-editor')).toHaveValue('Contents of lore/guide.txt');

  state.releaseGenerate?.();

  await expect(page.locator('#builder-status')).toHaveText(
    'Added references/harbor.png to your working theater.',
  );
  await expect(card.locator('button')).toHaveText('Generated');
  await expect(page.locator('#selected-path')).toHaveText('lore/guide.txt');

  const cardOpenLink = card.locator('.open-asset-link');
  await expect(cardOpenLink).toBeVisible();
  await expect(cardOpenLink).toHaveText('Open asset →');
  const msgOpenLink = page.locator('#assistant-messages .open-asset-link').last();
  await expect(msgOpenLink).toBeVisible();
  await expect(msgOpenLink).toHaveText('Open asset →');

  await cardOpenLink.click();
  await expect(page.locator('#selected-path')).toHaveText('references/harbor.png');
  await expect(page.locator('#media-preview img')).toBeVisible();
  await expect(page.locator('#media-preview img')).toHaveAttribute('alt', 'references/harbor.png');
});

test('delete file from editor ui', async ({ canvas, page }) => {
  await setupEditorHarness(canvas, page);
  await expect(page.locator('#file-count')).toHaveText('7 files');
  await page.locator('details[data-path="lore"] > summary').click();
  const guide = page.locator('.file-button[title="lore/guide.txt"]');
  await guide.click();
  await expect(page.locator('#selected-path')).toHaveText('lore/guide.txt');
  const deleteBtn = page.locator('#delete-file');
  await expect(deleteBtn).toBeVisible();

  await deleteBtn.click();
  const dialog = page.locator('#delete-file-dialog');
  await expect(dialog).toBeVisible();
  await expect(page.locator('#delete-file-prompt')).toContainText('lore/guide.txt');

  await page.locator('#confirm-delete-btn').click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('#builder-status')).toHaveText('Deleted lore/guide.txt.');
  await expect(page.locator('#file-count')).toHaveText('6 files');
  await expect(page.locator('#selected-path')).toHaveText('Explore your theater');
  await expect(deleteBtn).not.toBeVisible();
  await expect(page.locator('.file-button[title="lore/guide.txt"]')).not.toBeVisible();
});

test('theater yaml delete button is hidden', async ({ canvas, page }) => {
  await setupEditorHarness(canvas, page);
  await page.locator('.file-button[title="theater.yaml"]').click();
  await expect(page.locator('#selected-path')).toHaveText('theater.yaml');
  await expect(page.locator('#delete-file')).not.toBeVisible();
});

test('assistant proposes deletions ui', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  await expect(page.locator('#file-count')).toHaveText('7 files');
  state.assistantProposal = {
    message: 'I recommend removing the outdated lore guide.',
    writes: [],
    moves: [],
    deletions: ['lore/guide.txt'],
    generations: [],
  };
  await page.locator('#assistant-input').fill('Clean up outdated files.');
  await page.locator('#assistant-send').click();

  await expect(page.locator('#assistant-proposal')).toBeVisible();
  await expect(page.locator('#assistant-proposal')).toContainText('Review proposed changes');
  await expect(page.locator('.proposal-deletion')).toContainText('Delete lore/guide.txt');

  const applyBtn = page.locator('#assistant-proposal button', { hasText: 'Apply file changes' });
  await expect(applyBtn).toBeVisible();
  await applyBtn.click();

  await expect(page.locator('#builder-status')).toHaveText(
    'File changes applied to your working theater.',
  );
  await expect(page.locator('#file-count')).toHaveText('6 files');
  await expect(page.locator('.file-button[title="lore/guide.txt"]')).not.toBeVisible();
});

test('scene proposal is labeled as a scene visual', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  state.assistantProposal = {
    message: 'Add the harbor scene.',
    writes: [],
    moves: [],
    deletions: [],
    generations: [{ kind: 'scene', name: 'Old Harbor', prompt: 'A harbor at dawn.' }],
  };
  await page.locator('#assistant-input').fill('Create the harbor scene');
  await page.locator('#assistant-send').click();
  await expect(page.locator('#assistant-proposal')).toContainText('Scene visual: Old Harbor');
});

test('concurrent image generations do not block each other in ui', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  state.assistantProposal = {
    message: 'I propose creating reference artwork for the harbor and a ship.',
    writes: [],
    moves: [],
    generations: [
      {
        kind: 'reference',
        name: 'harbor',
        prompt: 'A misty harbor scene',
        playlist: 'ambient',
        references: [],
      },
      {
        kind: 'reference',
        name: 'ship',
        prompt: 'A wooden sailing ship',
        playlist: 'ambient',
        references: [],
      },
    ],
  };
  await page.locator('#assistant-input').fill('Propose key scene art.');
  await page.locator('#assistant-send').click();

  await expect(page.locator('#assistant-proposal')).toBeVisible();
  const cards = page.locator('.generation-card');
  await expect(cards).toHaveCount(2);

  const harborCard = cards.nth(0);
  const shipCard = cards.nth(1);

  await expect(harborCard.locator('strong')).toHaveText('Reference: harbor');
  await expect(harborCard.locator('button')).toHaveText('Generate · 4 Cr');
  await expect(harborCard.locator('button')).toBeEnabled();

  await expect(shipCard.locator('strong')).toHaveText('Reference: ship');
  await expect(shipCard.locator('button')).toHaveText('Generate · 4 Cr');
  await expect(shipCard.locator('button')).toBeEnabled();

  state.holdGenerations = { harbor: true, ship: true };

  await harborCard.locator('button').click();
  await expect(harborCard.locator('button')).toHaveText('Generating…');
  await expect(harborCard.locator('button')).toBeDisabled();

  await expect(shipCard.locator('button')).toHaveText('Generate · 4 Cr');
  await expect(shipCard.locator('button')).toBeEnabled();

  const manualBtn = page.locator('#generation-submit');
  await expect(manualBtn).toBeEnabled();

  await shipCard.locator('button').click();
  await expect(shipCard.locator('button')).toHaveText('Generating…');
  await expect(shipCard.locator('button')).toBeDisabled();
  await expect(harborCard.locator('button')).toHaveText('Generating…');
  await expect(harborCard.locator('button')).toBeDisabled();

  await expect.poll(() => Boolean(state.releaseGenerations['harbor'])).toBe(true);
  await expect.poll(() => Boolean(state.releaseGenerations['ship'])).toBe(true);

  state.releaseGenerations['harbor']?.();
  await expect(harborCard.locator('button')).toHaveText('Generated');
  await expect(harborCard.locator('button')).toBeDisabled();
  await expect(harborCard.locator('.open-asset-link')).toHaveText('Open asset →');

  await expect(shipCard.locator('button')).toHaveText('Generating…');
  await expect(shipCard.locator('button')).toBeDisabled();

  state.releaseGenerations['ship']?.();
  await expect(shipCard.locator('button')).toHaveText('Generated');
  await expect(shipCard.locator('button')).toBeDisabled();
  await expect(shipCard.locator('.open-asset-link')).toHaveText('Open asset →');

  await expect(page.locator('#file-count')).toHaveText('9 files');
});

test('proposal and manual generation do not block each other', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  state.assistantProposal = {
    message: 'I propose creating a harbor reference image.',
    writes: [],
    moves: [],
    generations: [
      {
        kind: 'reference',
        name: 'harbor',
        prompt: 'A misty harbor scene',
        playlist: 'ambient',
        references: [],
      },
    ],
  };
  await page.locator('#assistant-input').fill('Propose key scene art.');
  await page.locator('#assistant-send').click();

  await expect(page.locator('#assistant-proposal')).toBeVisible();
  const harborCard = page.locator('.generation-card').first();

  await page.locator('.manual-generation > summary').click();
  await page.locator('#generation-name').fill('lighthouse');
  await page.locator('#generation-prompt').fill('A tall beacon lighthouse.');

  state.holdGenerations = { harbor: true, lighthouse: true };

  await harborCard.locator('button').click();
  await expect(harborCard.locator('button')).toHaveText('Generating…');

  const manualSubmit = page.locator('#generation-submit');
  await expect(manualSubmit).toBeEnabled();
  await manualSubmit.click();
  await expect(manualSubmit).toHaveText('Generating…');
  await expect(manualSubmit).toBeDisabled();

  await expect.poll(() => Boolean(state.releaseGenerations['harbor'])).toBe(true);
  await expect.poll(() => Boolean(state.releaseGenerations['lighthouse'])).toBe(true);

  state.releaseGenerations['harbor']?.();
  await expect(harborCard.locator('button')).toHaveText('Generated');

  await expect(manualSubmit).toHaveText('Generating…');
  await expect(manualSubmit).toBeDisabled();

  state.releaseGenerations['lighthouse']?.();
  await expect(manualSubmit).toHaveText('Generate · 4 Cr');
  await expect(manualSubmit).toBeEnabled();
});

test('create text file dialog can escape without creating file', async ({ canvas, page }) => {
  await setupEditorHarness(canvas, page);
  await expect(page.locator('#file-count')).toHaveText('7 files');

  await page.locator('#new-file').click();
  const dialog = page.locator('#new-file-dialog');
  await expect(dialog).toBeVisible();
  await expect(page.locator('#new-file-path')).toHaveValue('');

  await page.locator('#new-file-cancel').click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('#file-count')).toHaveText('7 files');

  await page.locator('#new-file').click();
  await expect(dialog).toBeVisible();
  await page.locator('#new-file-path').fill('lore/abandoned.txt');

  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('#file-count')).toHaveText('7 files');
  await expect(page.locator('.file-button[title="lore/abandoned.txt"]')).not.toBeVisible();

  await page.locator('#new-file').click();
  await expect(dialog).toBeVisible();
  await expect(page.locator('#new-file-path')).toHaveValue('');
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
});

test('download draft button triggers download', async ({ canvas, page }) => {
  await setupEditorHarness(canvas, page);
  const downloadBtn = page.locator('#download-draft');
  await expect(downloadBtn).toBeVisible();
  await expect(downloadBtn).toHaveText('Download theater');
  const downloadPromise = page.waitForEvent('download');
  await downloadBtn.click();
  const download = await downloadPromise;
  expect(download.url()).toBe(`${CANVAS_ORIGIN}/api/theater-editor/theater_tree/download`);
  expect(download.suggestedFilename()).toBe('Harbor.zip');
  await expect(page.locator('#builder-status')).toHaveText('Theater downloaded.');
});

test('reset theater button dialog flow', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  const resetBtn = page.locator('#reset-theater');
  await expect(resetBtn).toBeVisible();
  await expect(resetBtn).toHaveText('Reset Theater');

  const dialog = page.locator('#reset-theater-dialog');
  await expect(dialog).not.toBeVisible();

  await resetBtn.click();
  await expect(dialog).toBeVisible();
  await page.locator('#reset-theater-cancel').click();
  await expect(dialog).not.toBeVisible();
  expect(state.resetTheater).toBe(false);

  await resetBtn.click();
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  expect(state.resetTheater).toBe(false);

  await resetBtn.click();
  await expect(dialog).toBeVisible();
  await page.locator('#confirm-reset-theater-btn').click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('#builder-status')).toHaveText('Theater reset.');
  expect(state.resetTheater).toBe(true);
});

test('partial folder import creates theater without populating defaults', async ({
  canvas,
  page,
}, testInfo) => {
  const state = await setupEditorHarness(canvas, page);
  const sourceDir = testInfo.outputPath('partial');
  fs.mkdirSync(sourceDir, { recursive: true });
  fs.writeFileSync(path.join(sourceDir, 'guide.txt'), 'Harbor guide', 'utf-8');

  await page.locator('#new-draft').click();
  await page.locator('#new-name').fill('Imported Harbor');
  await page.locator('#folder-input').setInputFiles(sourceDir);

  await expect(page.locator('#workspace')).toBeVisible();
  await expect(page.locator('#draft-name')).toHaveValue('Imported Harbor');
  await expect(page.locator('#builder-status')).toContainText('Uploaded 1 files.');
  expect(state.createdDefaultDraft).toBeUndefined();
  expect(state.lastUploadEndpoint).toBe('/api/theater-editor/drafts/import');
  expect(state.lastUploadedFilenames).toEqual(['partial/guide.txt']);
});

test('failed import keeps start panel without creating default theater', async ({
  canvas,
  page,
}) => {
  const state = await setupEditorHarness(canvas, page);
  await page.locator('#new-draft').click();
  state.uploadError = 'Invalid theater configuration.';
  await page.locator('#assets-input').setInputFiles([
    { name: 'theater.yaml', mimeType: 'text/yaml', buffer: Buffer.from('live_agent: [') },
  ]);

  await expect(page.locator('#builder-status')).toHaveText('Invalid theater configuration.');
  await expect(page.locator('#builder-status')).toHaveClass('error');
  await expect(page.locator('#start-panel')).toBeVisible();
  await expect(page.locator('#workspace')).not.toBeVisible();
  expect(state.createdDefaultDraft).toBeUndefined();
  expect(state.lastUploadEndpoint).toBe('/api/theater-editor/drafts/import');
  expect(page.url()).toBe(`${CANVAS_ORIGIN}/theater-editor`);

  state.uploadError = null;
  await page.locator('#assets-input').setInputFiles([
    { name: 'guide.txt', mimeType: 'text/plain', buffer: Buffer.from('guide') },
  ]);
  await expect(page.locator('#workspace')).toBeVisible();
  await expect(page.locator('#builder-status')).toContainText('Uploaded 1 files.');
  expect(page.url()).toContain('theater_id=theater_imported');
  expect(state.createdDefaultDraft).toBeUndefined();
});

test('upload reports discarded ineligible files', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  state.discardedFiles = ['helper.py'];
  await page.locator('#assets-input').setInputFiles([
    { name: 'guide.txt', mimeType: 'text/plain', buffer: Buffer.from('guide') },
    { name: 'helper.py', mimeType: 'text/plain', buffer: Buffer.from('ignored') },
  ]);
  await expect(page.locator('#builder-status')).toContainText(
    'Uploaded 1 files. Discarded 1 ineligible files: helper.py.',
  );
  await expect(page.locator('.file-button[data-path="references/helper.py"]')).toHaveCount(0);
});

test('failed upload preserves existing draft', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  state.uploadError = 'No eligible theater files were found to import.';
  await page.locator('#assets-input').setInputFiles([
    { name: 'helper.py', mimeType: 'text/plain', buffer: Buffer.from('ignored') },
  ]);
  await expect(page.locator('#builder-status')).toHaveText(
    'No eligible theater files were found to import.',
  );
  await expect(page.locator('#workspace')).toBeVisible();
  await expect(page.locator('#file-count')).toHaveText('7 files');
  expect(page.url()).toContain('theater_id=theater_tree');
  expect(state.createdDefaultDraft).toBeUndefined();
});

test('upload non png files does not open png dialog', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  const dialog = page.locator('#png-warning-dialog');
  await expect(dialog).not.toBeVisible();

  await page.locator('#assets-input').setInputFiles([
    {
      name: 'landscape.webp',
      mimeType: 'image/webp',
      buffer: Buffer.from('RIFF\x1a\x00\x00\x00WEBPVP8 ', 'latin1'),
    },
  ]);
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('#builder-status')).toContainText('Uploaded 1 files.');
  expect(state.lastUploadedFilenames).toEqual(['landscape.webp']);
});

test('upload png file shows warning dialog and can cancel', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  const dialog = page.locator('#png-warning-dialog');
  await expect(dialog).not.toBeVisible();

  await page.locator('#assets-input').setInputFiles([
    {
      name: 'hero_portrait.png',
      mimeType: 'image/png',
      buffer: Buffer.from('\x89PNG\r\n\x1a\n', 'latin1'),
    },
  ]);

  await expect(dialog).toBeVisible();
  await expect(page.locator('#png-warning-prompt')).toContainText(
    'PNG images stream much slower than WebP or JPG images',
  );
  await expect(page.locator('#png-warning-files')).toContainText('hero_portrait.png');

  await page.locator('#png-warning-cancel').click();
  await expect(dialog).not.toBeVisible();
  expect(state.lastUploadedFilenames).toBeNull();

  await page.locator('#assets-input').setInputFiles([
    {
      name: 'hero_portrait.png',
      mimeType: 'image/png',
      buffer: Buffer.from('\x89PNG\r\n\x1a\n', 'latin1'),
    },
  ]);
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  expect(state.lastUploadedFilenames).toBeNull();
});

test('upload png file can skip conversion and upload as png', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  const dialog = page.locator('#png-warning-dialog');

  await page.locator('#assets-input').setInputFiles([
    {
      name: 'hero_portrait.png',
      mimeType: 'image/png',
      buffer: Buffer.from('\x89PNG\r\n\x1a\n', 'latin1'),
    },
  ]);
  await expect(dialog).toBeVisible();

  await page.locator('#png-warning-skip').click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('#builder-status')).toContainText('Uploaded 1 files.');
  expect(state.lastUploadedFilenames).toEqual(['hero_portrait.png']);
});

test('upload png file converts to webp by default', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  const dialog = page.locator('#png-warning-dialog');

  await page.locator('#assets-input').setInputFiles([
    {
      name: 'hero_portrait.png',
      mimeType: 'image/png',
      buffer: Buffer.from('\x89PNG\r\n\x1a\n', 'latin1'),
    },
  ]);
  await expect(dialog).toBeVisible();
  await expect(page.locator('#png-convert-format')).toHaveValue('image/webp');

  await page.locator('#png-warning-convert').click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('#builder-status')).toContainText('Uploaded 1 files.');
  expect(state.lastUploadedFilenames).toEqual(['hero_portrait.webp']);
});

test('upload png file can convert to jpg', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  const dialog = page.locator('#png-warning-dialog');

  await page.locator('#assets-input').setInputFiles([
    {
      name: 'battlemap.png',
      mimeType: 'image/png',
      buffer: Buffer.from('\x89PNG\r\n\x1a\n', 'latin1'),
    },
  ]);
  await expect(dialog).toBeVisible();
  await page.locator('#png-convert-format').selectOption('image/jpeg');

  await page.locator('#png-warning-convert').click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('#builder-status')).toContainText('Uploaded 1 files.');
  expect(state.lastUploadedFilenames).toEqual(['battlemap.jpg']);
});

test('upload png file remember convert choice prevents future popup', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  const dialog = page.locator('#png-warning-dialog');

  await page.locator('#assets-input').setInputFiles([
    {
      name: 'first.png',
      mimeType: 'image/png',
      buffer: Buffer.from('\x89PNG\r\n\x1a\n', 'latin1'),
    },
  ]);
  await expect(dialog).toBeVisible();
  const rememberCb = page.locator('#png-remember-choice');
  await expect(rememberCb).not.toBeChecked();
  await rememberCb.check();
  await page.locator('#png-warning-convert').click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('#builder-status')).toContainText('Uploaded 1 files.');
  expect(state.lastUploadedFilenames).toEqual(['first.webp']);

  const pref = await page.evaluate(
    () =>
      JSON.parse(
        localStorage.getItem('narratron.builder.png_upload_preference') ?? 'null',
      ) as { action: string; format: string | null } | null,
  );
  expect(pref).toEqual({ action: 'convert', format: 'image/webp' });

  await page.locator('#assets-input').setInputFiles([
    {
      name: 'second.png',
      mimeType: 'image/png',
      buffer: Buffer.from('\x89PNG\r\n\x1a\n', 'latin1'),
    },
  ]);
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('#builder-status')).toContainText('Uploaded 1 files.');
  expect(state.lastUploadedFilenames).toEqual(['second.webp']);
});

test('upload png file remember skip choice prevents future popup', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  const dialog = page.locator('#png-warning-dialog');

  await page.locator('#assets-input').setInputFiles([
    {
      name: 'raw1.png',
      mimeType: 'image/png',
      buffer: Buffer.from('\x89PNG\r\n\x1a\n', 'latin1'),
    },
  ]);
  await expect(dialog).toBeVisible();
  await page.locator('#png-remember-choice').check();
  await page.locator('#png-warning-skip').click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('#builder-status')).toContainText('Uploaded 1 files.');
  expect(state.lastUploadedFilenames).toEqual(['raw1.png']);

  const pref = await page.evaluate(
    () =>
      JSON.parse(
        localStorage.getItem('narratron.builder.png_upload_preference') ?? 'null',
      ) as { action: string; format: string | null } | null,
  );
  expect(pref).toEqual({ action: 'skip', format: null });

  await page.locator('#assets-input').setInputFiles([
    {
      name: 'raw2.png',
      mimeType: 'image/png',
      buffer: Buffer.from('\x89PNG\r\n\x1a\n', 'latin1'),
    },
  ]);
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('#builder-status')).toContainText('Uploaded 1 files.');
  expect(state.lastUploadedFilenames).toEqual(['raw2.png']);
});

test('svg rendering and source toggle in theater editor', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  await expect(page.locator('#workspace')).toBeVisible();

  await page.locator('#new-file').click();
  await page.locator('#new-file-path').fill('game/crest.svg');
  await page.locator('#new-file-dialog button[value="create"]').click();

  const svgCode =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="40" fill="#6366f1"/></svg>';
  state.savedFiles['game/crest.svg'] = svgCode;

  await page.locator('.folder-button', { hasText: 'game' }).click();
  const svgButton = page.locator('#file-list button', { hasText: 'crest.svg' });
  await expect(svgButton).toBeVisible();
  const btnText = await svgButton.textContent();
  expect(btnText).toContain('▧');
  await svgButton.click();

  await expect(page.locator('#media-preview')).toBeVisible();
  const img = page.locator('#media-preview img');
  await expect(img).toBeVisible();
  await expect(img).toHaveAttribute('alt', 'game/crest.svg');
  await expect(page.locator('#file-editor')).toBeHidden();

  const toggleBtn = page.locator('#toggle-source');
  await expect(toggleBtn).toBeVisible();
  await expect(toggleBtn).toHaveText('Source');

  await toggleBtn.click();
  await expect(page.locator('#file-editor')).toBeVisible();
  await expect(page.locator('#media-preview')).toBeHidden();
  await expect(toggleBtn).toHaveText('Preview');

  await page.locator('#file-editor').fill(svgCode);

  await toggleBtn.click();
  await expect(page.locator('#media-preview')).toBeVisible();
  await expect(page.locator('#file-editor')).toBeHidden();
  await expect(toggleBtn).toHaveText('Source');
  await expect(page.locator('#media-preview img')).toHaveAttribute('src', /^data:image\/svg\+xml/);

  await toggleBtn.click();
  await expect(page.locator('#file-editor')).toBeVisible();
  await expect(page.locator('#file-editor')).toHaveValue(svgCode);

  await page.locator('#file-list button', { hasText: 'theater.yaml' }).click();
  await expect(toggleBtn).toBeHidden();
  await expect(page.locator('#file-editor')).toBeVisible();
});

for (const technique of ['video', 'layered', 'triframe'] as const) {
  test(`manual animation presets send selected technique and show price (technique=${technique})`, async ({
    canvas,
    page,
  }) => {
    const state = await setupEditorHarness(canvas, page);
    await page.locator('.manual-generation summary').click();
    await page.locator('#generation-kind').selectOption('animation');
    await page.locator('#generation-animation-type').selectOption(technique);
    await expect(page.locator('#generation-submit')).toHaveText('Generate · 8 Cr');
    await page.locator('#generation-name').fill('harbor');
    await page.locator('#generation-prompt').fill('Rain over the harbor');
    await page.locator('#generation-submit').click();
    await expect.poll(() => state.lastGenerationRequest?.kind).toBe('animation');
    expect(state.lastGenerationRequest?.animation_type).toBe(technique);
  });
}

test('reference tagging autocomplete and submission in editor', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  await page.locator('.manual-generation summary').click();
  const prompt = page.locator('#generation-prompt');

  await prompt.focus();
  await page.keyboard.type('Show @cap');
  const menu = page.locator('.reference-mention-menu:visible');
  await expect(menu).toBeVisible();
  await expect(menu.locator('.reference-mention-option')).toHaveCount(1);
  await expect(menu.locator('.reference-mention-name')).toHaveText('@captain');

  await page.keyboard.press('Tab');
  await expect(prompt).toHaveValue('Show @captain ');
  await expect(menu).toBeHidden();

  for (const kind of ['scene', 'character', 'animation', 'stamp'] as const) {
    await page.locator('#generation-kind').selectOption(kind);
    await page.locator('#generation-name').fill(`test_${kind}`);
    await prompt.fill('Show @captain at the helm');
    await page.locator('#generation-submit').click();
    await expect.poll(() => state.lastGenerationRequest?.kind).toBe(kind);
    expect(state.lastGenerationRequest?.references).toEqual(['references/people/captain.png']);
  }

  const assistantInput = page.locator('#assistant-input');
  await assistantInput.focus();
  await page.keyboard.type('Create a scene with @');
  const assistantMenu = page.locator('.reference-mention-menu:visible');
  await expect(assistantMenu).toBeVisible();
  await assistantMenu.locator('.reference-mention-option', { hasText: 'captain' }).click();
  await expect(assistantInput).toHaveValue('Create a scene with @captain ');
});

test('drag and drop files between folders', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  await page.locator('details[data-path="lore"] > summary').click();
  await page.locator('details[data-path="lore/characters"] > summary').click();
  await page.locator('details[data-path="lore/characters/crew"] > summary').click();

  const rookieBtn = page.locator('.file-button[data-path="lore/characters/crew/rookie.txt"]');
  await expect(rookieBtn).toBeVisible();
  await expect(rookieBtn).toHaveAttribute('draggable', 'true');

  const theaterYamlBtn = page.locator('.file-button[data-path="theater.yaml"]');
  await expect(theaterYamlBtn).not.toHaveAttribute('draggable', 'true');

  const loreFolderSummary = page.locator('details[data-path="lore"] > summary');
  await rookieBtn.dragTo(loreFolderSummary);

  await expect(page.locator('#builder-status')).toHaveText('Moved rookie.txt to lore.');
  const movedRookieBtn = page.locator('.file-button[data-path="lore/rookie.txt"]');
  await expect(movedRookieBtn).toBeVisible();
  await expect(page.locator('.file-button[data-path="lore/characters/crew/rookie.txt"]')).toHaveCount(
    0,
  );
  await expect(page.locator('#selected-path')).toHaveText('lore/rookie.txt');

  const charsFolderSummary = page.locator('details[data-path="lore/characters"] > summary');
  await movedRookieBtn.dragTo(charsFolderSummary);

  await expect(page.locator('#builder-status')).toHaveText('Moved rookie.txt to lore/characters.');
  const nestedRookieBtn = page.locator('.file-button[data-path="lore/characters/rookie.txt"]');
  await expect(nestedRookieBtn).toBeVisible();
  await expect(page.locator('#selected-path')).toHaveText('lore/characters/rookie.txt');

  state.lastMoveRequest = null;
  await nestedRookieBtn.dragTo(charsFolderSummary);
  expect(state.lastMoveRequest).toBeNull();

  await nestedRookieBtn.click();
  await expect(page.locator('#selected-path')).toHaveText('lore/characters/rookie.txt');
  await expect(nestedRookieBtn).toHaveClass(/\bactive\b/);

  await page.locator('#new-file').click();
  await page.locator('#new-file-path').fill('lore/characters/guide.txt');
  await page.locator('#new-file-submit').click();
  await expect(page.locator('.file-button[data-path="lore/characters/guide.txt"]')).toBeVisible();

  await page.locator('.file-button[data-path="lore/guide.txt"]').dragTo(charsFolderSummary);
  await expect(page.locator('#builder-status')).toHaveText(
    'A file named "guide.txt" already exists in lore/characters.',
  );
});

test('attach predefined game from library ui', async ({ canvas, page }) => {
  const state = await setupEditorHarness(canvas, page);
  await page.locator('#attach-game').click();
  const dialog = page.locator('#game-library-dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('.game-library-card')).toHaveCount(2);

  await page.locator('#game-library-search').fill('Checkers');
  await expect(dialog.locator('.game-library-card')).toHaveCount(1);
  const card = dialog.locator('.game-library-card[data-game-id="example-game"]');
  await expect(card).toBeVisible();
  await expect(card.locator('.game-card-title')).toHaveText('Crown & Corners Checkers');
  await expect(card.locator('.game-card-badge')).toHaveText([
    'game/game.yaml',
    'HTML Panel',
    '1 SVG',
    '1 Stamp',
  ]);

  await page.locator('#game-enable-tool').check();
  await page.locator('#game-library-submit').click();

  await expect(dialog).not.toBeVisible();
  await expect(page.locator('#builder-status')).toHaveText(
    'Attached "Crown & Corners Checkers" to your working theater.',
  );
  expect(state.lastAttachGameRequest).toEqual({
    revision: 0,
    game_id: 'example-game',
    enable_game_tool: true,
  });
  await expect(page.locator('.file-button[title="game/game.yaml"]')).toBeVisible();
  await expect(page.locator('.file-button[title="game/panel.html"]')).toBeVisible();
  await expect(page.locator('.file-button[title="game/crest.svg"]')).toBeVisible();
  await expect(page.locator('#selected-path')).toHaveText('game/game.yaml');
  await expect(page.locator('#file-editor')).toHaveValue('version: 2\n# Checkers\n');
});
