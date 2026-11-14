import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  REPO_ROOT,
  expect,
  test,
} from '../../testing/ui/canvas_fixtures';

type TestlabAdventureRunnerModule = typeof import('./testlab-adventure-runner.js');

const ADVENTURE_RUNNER_HTML_PATH = path.join(REPO_ROOT, 'testlab', 'adventure_runner.html');
const GAME_RENDERER_CSS_PATH = path.join(REPO_ROOT, 'frontend', 'canvas', 'game-renderer.css');

interface RecordedRequest {
  readonly method: string;
  readonly pathname: string;
  readonly body: string | null;
}

interface MockAdventureRoutes {
  readonly requests: RecordedRequest[];
  setAdventuresError(message: string | null): void;
  setMessageError(message: string | null): void;
}

async function setupAdventureRunnerMocks(page: import('@playwright/test').Page): Promise<MockAdventureRoutes> {
  const requests: RecordedRequest[] = [];
  let adventuresError: string | null = null;
  let messageError: string | null = null;
  let toolLogs: Array<Record<string, unknown>> = [];

  await page.route(`${CANVAS_ORIGIN}/adventure-runner`, async (route: Route): Promise<void> => {
    await route.fulfill({
      status: 200,
      contentType: 'text/html; charset=utf-8',
      body: fs.readFileSync(ADVENTURE_RUNNER_HTML_PATH, 'utf8'),
    });
  });

  await page.route(`${CANVAS_ORIGIN}/game-lab-assets/css/*`, async (route: Route): Promise<void> => {
    await route.fulfill({
      status: 200,
      contentType: 'text/css; charset=utf-8',
      body: fs.existsSync(GAME_RENDERER_CSS_PATH)
        ? fs.readFileSync(GAME_RENDERER_CSS_PATH, 'utf8')
        : '',
    });
  });

  await page.route(`${CANVAS_ORIGIN}/api/adventure-runner/**`, async (route: Route): Promise<void> => {
    const req = route.request();
    const url = new URL(req.url());
    requests.push({
      method: req.method(),
      pathname: url.pathname,
      body: req.postData(),
    });

    if (req.method() === 'GET' && url.pathname === '/api/adventure-runner/adventures') {
      if (adventuresError !== null) {
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: adventuresError }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          adventures: [
            { id: 'neon-vault', title: 'Neon Vault', lore_count: 2 },
            { id: 'clockwork-bay', title: 'Clockwork Bay', lore_count: 1 },
          ],
        }),
      });
      return;
    }

    if (req.method() === 'POST' && url.pathname === '/api/adventure-runner/sessions') {
      toolLogs = [];
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'sess-adv-1',
          adventure_id: 'neon-vault',
          state: {
            turns_count: 0,
            sticky_notes: [
              { topic: 'Vault Code', info: 'Three chimes at midnight.' },
            ],
            characters: [
              {
                name: 'Mira Vance',
                voice_tags: ['alto', 'calm'],
                description: 'Archivist on deck 4.',
                quirk: 'Taps her brass compass.',
              },
            ],
            lore_documents: ['premise.txt', 'vault_blueprints.txt'],
            game: null,
          },
          mock_canvas: {
            current_image: 'vault_door.png',
            current_image_prompt: 'Neon vault corridor',
            current_image_effect: 'pulse2',
            current_music: 'synth_drip',
            music_status: 'playing',
            current_thought: 'Check the chimes.',
            last_interactive_canvas_request: 'keypad_ui',
          },
          tool_logs: toolLogs,
        }),
      });
      return;
    }

    if (req.method() === 'GET' && url.pathname === '/api/adventure-runner/sessions/sess-adv-1') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'sess-adv-1',
          tool_logs: toolLogs,
          state: {
            turns_count: 1,
            sticky_notes: [],
            characters: [],
            lore_documents: ['premise.txt', 'vault_blueprints.txt'],
          },
        }),
      });
      return;
    }

    if (req.method() === 'POST' && url.pathname === '/api/adventure-runner/sessions/sess-adv-1/messages') {
      if (messageError !== null) {
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: messageError }),
        });
        return;
      }
      toolLogs = [
        {
          tool: 'process_user_action',
          timestamp: 1700000000,
          args: { action: 'inspect keypad', urgent: true },
          result: { outcome: 'sparks', code_hint: 7 },
        },
        {
          tool: 'stage_image',
          timestamp: 1700000001,
          args: { asset: 'keypad_closeup.png' },
          result: 'staged',
        },
      ];
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          turn: {
            turn_index: 1,
            user_message: 'Inspect the vault keypad',
            agent_response: 'You brush dust off the glowing keypad.',
            dialogue: [
              { speaker: 'Mira Vance', text: 'Careful, the latch is live.', kind: 'speech' },
              { speaker: 'Narrator', text: 'Something hums inside.', kind: 'thought' },
            ],
            tool_calls: [
              {
                tool: 'process_user_action',
                args: { action: 'inspect keypad', urgent: true },
                result: { outcome: 'sparks', code_hint: 7 },
              },
              {
                tool: 'stage_image',
                args: { asset: 'keypad_closeup.png' },
                result: 'staged',
              },
            ],
            lore_docs_browsed: ['vault_blueprints.txt'],
            lore_activity: [
              {
                type: 'read',
                document: 'vault_blueprints.txt',
                summary: 'Keypad schematic',
              },
            ],
          },
          state: {
            turns_count: 1,
            sticky_notes: [
              { topic: 'Vault Code', info: 'Three chimes at midnight.' },
              { topic: 'Keypad Spark', info: 'Digit 7 glows.' },
            ],
            characters: [],
            lore_documents: ['premise.txt', 'vault_blueprints.txt'],
            game: null,
          },
          mock_canvas: {
            current_image: 'keypad_closeup.png',
            current_image_prompt: 'Glowing keypad close-up',
            current_image_effect: 'gleam3',
            current_music: 'synth_drip',
            music_status: 'playing',
            current_thought: 'Digit 7 is warm.',
            last_interactive_canvas_request: 'keypad_ui',
          },
        }),
      });
      return;
    }

    if (
      req.method() === 'GET' &&
      url.pathname.startsWith('/api/adventure-runner/sessions/sess-adv-1/lore/')
    ) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          content: 'Blueprint Line 1\nKeypad requires digit 7.',
        }),
      });
      return;
    }

    await route.fulfill({ status: 404, body: 'Not found' });
  });

  return {
    requests,
    setAdventuresError(message: string | null): void {
      adventuresError = message;
    },
    setMessageError(message: string | null): void {
      messageError = message;
    },
  };
}

test.describe('Testlab Adventure Runner TS module', () => {
  test('loads adventures, starts session, inspects tool args, browses lore, and switches tabs', async ({
    canvas,
    page,
  }): Promise<void> => {
    void canvas;
    const mocks = await setupAdventureRunnerMocks(page);

    await page.goto(`${CANVAS_ORIGIN}/adventure-runner`);

    await expect(page.locator('#adv-select option')).toHaveCount(2);
    await expect(page.locator('#adv-select')).toHaveValue('neon-vault');

    await page.locator('#btn-start').click();
    await expect(page.locator('#status-text')).toHaveText('Session Active (neon-vault)');
    await expect(page.locator('#stickies-count')).toHaveText('1');
    await expect(page.locator('#stickies-list')).toContainText('Vault Code');
    await expect(page.locator('#chars-list')).toContainText('Mira Vance');
    await expect(page.locator('#lore-count')).toHaveText('2');

    // Quick-action pill populates input via global window.setQuickAction
    await page.getByText('Inspect Area').click();
    await expect(page.locator('#user-input')).toHaveValue(
      'I look around the room and inspect the surroundings.',
    );

    // Send player action via Send button
    await page.locator('#user-input').fill('Inspect the vault keypad');
    await page.locator('#btn-send').click();

    await expect(page.locator('#chat-stream')).toContainText('You brush dust off the glowing keypad.');
    await expect(page.locator('#chat-stream')).toContainText('Mira Vance');
    await expect(page.locator('#chat-stream')).toContainText('(thought)');
    await expect(page.locator('#stickies-count')).toHaveText('2');

    // Inspect tool arguments via inline onclick="toggleToolArgs(...)"
    await page.locator('#tool-chip-turn_1-0').click();
    const details = page.locator('#tool-details-turn_1');
    await expect(details).toBeVisible();
    await expect(details).toContainText('process_user_action');
    await expect(details).toContainText('code_hint');
    await expect(details.locator('.json-boolean')).toHaveCount(1);
    await expect(details.locator('.json-number')).toHaveCount(1);

    // Toggle lore drawer via inline onclick="toggleLoreDrawer(...)"
    await page.locator('#tool-chip-turn_1-lore').click();
    await expect(details).toContainText('Lore Documents Browsed by Story Planner');
    await expect(details).toContainText('vault_blueprints.txt');

    // Open lore modal from drawer link via inline onclick="viewLoreDocument(...)"
    await details.getByRole('button', { name: /View Text/ }).click();
    await expect(page.locator('#lore-modal')).toBeVisible();
    await expect(page.locator('#modal-lore-title')).toHaveText('vault_blueprints.txt');
    await expect(page.locator('#modal-lore-content')).toContainText('Keypad requires digit 7.');
    await expect(page.locator('#modal-lore-badge')).toContainText('chars');

    // Close modal via close button
    await page.locator('#lore-modal .tool-close-btn').click();
    await expect(page.locator('#lore-modal')).toBeHidden();

    // Switch tabs via inline onclick="switchTab(...)"
    await page.locator('.tab-btn', { hasText: 'Mock Canvas' }).click();
    await expect(page.locator('#tab-canvas')).toHaveClass(/active/);
    await expect(page.locator('#canvas-img-val')).toHaveText('keypad_closeup.png');
    await expect(page.locator('#canvas-music-val')).toHaveText('synth_drip');

    await page.locator('.tab-btn', { hasText: 'Lore Docs' }).click();
    await expect(page.locator('#tab-lore')).toHaveClass(/active/);
    await expect(page.locator('#lore-docs-list')).toContainText('Browsed This Session');

    await page.locator('.tab-btn', { hasText: 'Tool Activity' }).click();
    await expect(page.locator('#tab-logs')).toHaveClass(/active/);
    await expect(page.locator('#log-count')).toHaveText('2');
    await expect(page.locator('#tool-logs-list')).toContainText('stage_image');

    // Message error surfaces system error bubble and restores controls
    mocks.setMessageError('Planner timed out');
    await page.locator('#user-input').fill('Push digit 7');
    await page.locator('#btn-send').click();
    await expect(page.locator('#status-text')).toContainText('Turn error: Planner timed out');
    await expect(page.locator('#chat-stream')).toContainText('System Error');
    await expect(page.locator('#user-input')).toBeEnabled();
  });

  test('formatJsonHtml formats primitives and nested JSON and escapes HTML', async ({
    canvas,
    page,
  }): Promise<void> => {
    await canvas.mount({ html: '<div></div>' });
    const results = await page.evaluate(async (): Promise<{
      nullHtml: string;
      strHtml: string;
      numHtml: string;
      boolHtml: string;
      objHtml: string;
      escaped: string;
    }> => {
      const mod = await window.__importModule<TestlabAdventureRunnerModule>(
        '/static/generated/canvas/testlab-adventure-runner.js',
      );
      return {
        nullHtml: mod.formatJsonHtml(null),
        strHtml: mod.formatJsonHtml('hello'),
        numHtml: mod.formatJsonHtml(42),
        boolHtml: mod.formatJsonHtml(true),
        objHtml: mod.formatJsonHtml({ tag: '<script>', ok: false }),
        escaped: mod.escapeHtml('<a href="x">A & B</a>'),
      };
    });

    expect(results.nullHtml).toBe('<span class="json-null">null</span>');
    expect(results.strHtml).toBe('<span class="json-string">&quot;hello&quot;</span>');
    expect(results.numHtml).toBe('<span class="json-number">42</span>');
    expect(results.boolHtml).toBe('<span class="json-boolean">true</span>');
    expect(results.objHtml).toContain('&quot;tag&quot;');
    expect(results.objHtml).toContain('&lt;script&gt;');
    expect(results.objHtml).toContain('<span class="json-boolean">false</span>');
    expect(results.escaped).toBe('&lt;a href=&quot;x&quot;&gt;A &amp; B&lt;/a&gt;');
  });
});
