import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  REPO_ROOT,
  expect,
  test,
} from '../../testing/ui/canvas_fixtures';

interface CounterSnapshot {
  readonly runtime_counter: {
    readonly payload: Record<string, unknown>;
  };
}

function makeCounterPayload(count: number, revision: number): Record<string, unknown> {
  return {
    definition_hash: '0de763b885a56be2362f8c9c23b23103933339bdf467d7131aac594d563da056',
    revision,
    state: { count },
    error: '',
    view: [
      {
        kind: 'column',
        props: {},
        action: null,
        children: [
          {
            kind: 'text',
            props: { text: `Count: ${count}`, id: 'counter' },
            children: [],
            action: null,
          },
          {
            kind: 'button',
            props: { text: 'Add one', disabled: count >= 10 },
            children: [],
            action: {
              name: 'add',
              arguments: { amount: 1 },
              confirmation: '',
              permission: 'orator',
            },
          },
        ],
      },
    ],
  };
}

test.describe('Adventure Runner Game UI', () => {
  test('renders game panel, submits actions, updates chat stream, and resets', async ({
    canvas,
  }) => {
    const snapshotsRaw = await fs.promises.readFile(
      path.join(REPO_ROOT, 'testing', 'testdata', 'game_snapshots.json'),
      'utf-8',
    );
    const snapshots = JSON.parse(snapshotsRaw) as CounterSnapshot;
    const runnerHtml = await fs.promises.readFile(
      path.join(REPO_ROOT, 'testlab', 'adventure_runner.html'),
      'utf-8',
    );

    let count = 0;
    let revision = 0;
    let history: Array<Record<string, unknown>> = [];
    let currentPayload: Record<string, unknown> = {
      ...snapshots.runtime_counter.payload,
      state: { count: 0 },
    };

    const buildSessionResponse = () => ({
      id: 'session-counter-1',
      adventure_id: 'counter',
      state: {
        game: currentPayload,
        turns_count: history.length,
        sticky_notes: [],
        characters: [],
        lore_documents: [],
      },
      mock_canvas: {},
      tool_logs: [],
      history,
    });

    const page = canvas.page;
    await page.setViewportSize({ width: 1400, height: 1000 });

    await page.route(
      (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/adventure-runner',
      async (route: Route) => {
        await route.fulfill({
          body: runnerHtml,
          contentType: 'text/html; charset=utf-8',
        });
      },
    );

    await page.route(
      (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname.startsWith('/game-lab-assets/'),
      async (route: Route) => {
        const url = new URL(route.request().url());
        const parts = url.pathname.split('/').filter(Boolean);
        const kind = parts[1];
        const file = parts[2];
        if (kind === 'js' && file) {
          const body = await fs.promises.readFile(
            path.join(REPO_ROOT, 'static', 'generated', 'canvas', file),
            'utf-8',
          );
          await route.fulfill({ body, contentType: 'application/javascript; charset=utf-8' });
          return;
        }
        if (kind === 'css' && file) {
          const body = await fs.promises.readFile(
            path.join(REPO_ROOT, 'static', 'css', file),
            'utf-8',
          );
          await route.fulfill({ body, contentType: 'text/css; charset=utf-8' });
          return;
        }
        await route.fulfill({ status: 404, body: 'Not found' });
      },
    );

    await page.route(
      (url: URL) =>
        url.origin === CANVAS_ORIGIN && url.pathname.startsWith('/api/adventure-runner/'),
      async (route: Route) => {
        const url = new URL(route.request().url());
        const method = route.request().method();

        if (url.pathname === '/api/adventure-runner/adventures' && method === 'GET') {
          await route.fulfill({
            json: { adventures: [{ id: 'counter', title: 'Counter', lore_count: 0 }] },
          });
          return;
        }
        if (url.pathname === '/api/adventure-runner/sessions' && method === 'POST') {
          await route.fulfill({ json: buildSessionResponse() });
          return;
        }
        if (url.pathname === '/api/adventure-runner/sessions/session-counter-1' && method === 'GET') {
          await route.fulfill({ json: buildSessionResponse() });
          return;
        }
        if (
          url.pathname === '/api/adventure-runner/sessions/session-counter-1/game/action' &&
          method === 'POST'
        ) {
          const raw = route.request().postData() ?? '{}';
          const body = JSON.parse(raw) as {
            readonly name?: string;
            readonly arguments?: { readonly amount?: number };
          };
          expect(body.name).toBe('add');
          const amount = body.arguments?.amount ?? 1;
          count += amount;
          revision += 1;
          currentPayload = makeCounterPayload(count, revision);
          history = [
            ...history,
            {
              turn_index: history.length + 1,
              user_message: 'Game action: add {"amount": 1}',
              agent_response: '',
              tool_calls: [],
              lore_docs_browsed: [],
              lore_activity: [],
              dialogue: [],
            },
          ];
          await route.fulfill({ json: currentPayload });
          return;
        }
        if (
          url.pathname === '/api/adventure-runner/sessions/session-counter-1/reset' &&
          method === 'POST'
        ) {
          count = 0;
          revision = 0;
          history = [];
          currentPayload = makeCounterPayload(0, 0);
          await route.fulfill({ json: buildSessionResponse() });
          return;
        }
        await route.fulfill({ status: 404, json: { detail: 'Unhandled test route' } });
      },
    );

    await page.goto(`${CANVAS_ORIGIN}/adventure-runner`);
    await expect(page.locator('#adv-select')).toHaveValue('counter');
    await page.getByRole('button', { name: 'Start Session', exact: true }).click();
    await page.getByRole('button', { name: 'Game', exact: true }).click();
    await expect(page.locator('[data-game-id="counter"]')).toHaveText('Count: 0');

    await page.getByRole('button', { name: 'Add one', exact: true }).click();
    await expect(page.locator('[data-game-id="counter"]')).toHaveText('Count: 1');
    await expect(page.locator('#chat-stream')).toContainText('Game action: add');

    page.once('dialog', async (dialog) => {
      await dialog.accept();
    });
    await page.getByRole('button', { name: 'Reset', exact: true }).click();
    await expect(page.locator('[data-game-id="counter"]')).toHaveText('Count: 0');
    await expect(page.getByRole('button', { name: 'Add one', exact: true })).toBeEnabled();
  });
});
