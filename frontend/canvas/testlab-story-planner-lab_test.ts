import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  REPO_ROOT,
  expect,
  test,
} from '../../testing/ui/canvas_fixtures';

const STORY_PLANNER_HTML_PATH = path.join(REPO_ROOT, 'testlab', 'story_planner_lab.html');

interface RecordedActionRequest {
  readonly action: string;
  readonly nudge: string;
}

test.describe('Testlab Story Planner Lab TS module', () => {
  test('creates session, submits actions, polls callback events, and surfaces errors', async ({
    canvas,
    page,
  }): Promise<void> => {
    const html = await fs.promises.readFile(STORY_PLANNER_HTML_PATH, 'utf8');
    let createError: string | null = null;
    let actionError: string | null = null;
    let sessionPollCount = 0;
    const createdPayloads: Array<Record<string, unknown>> = [];
    const actionRequests: RecordedActionRequest[] = [];

    await canvas.serveRoute(
      (url: URL) =>
        url.origin === CANVAS_ORIGIN &&
        (url.pathname === '/story-planner' ||
          url.pathname.startsWith('/api/story-planner/')),
      async (route: Route): Promise<void> => {
        const req = route.request();
        const url = new URL(req.url());

        if (url.pathname === '/story-planner') {
          await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
          return;
        }

        if (req.method() === 'POST' && url.pathname === '/api/story-planner/sessions') {
          createdPayloads.push(JSON.parse(req.postData() || '{}') as Record<string, unknown>);
          if (createError !== null) {
            await route.fulfill({ status: 400, json: { detail: createError } });
            return;
          }
          await route.fulfill({
            json: {
              id: 'sess-12345678',
              events: [],
              state: { characters: [], notepad: {} },
            },
          });
          return;
        }

        if (req.method() === 'POST' && url.pathname === '/api/story-planner/sessions/sess-12345678/actions') {
          const body = JSON.parse(req.postData() || '{}') as RecordedActionRequest;
          actionRequests.push(body);
          if (actionError !== null) {
            await route.fulfill({ status: 400, json: { detail: actionError } });
            return;
          }
          await route.fulfill({
            json: {
              acknowledgement: { status: 'processing', message: 'Action queued for planner.' },
              session: {
                id: 'sess-12345678',
                events: [],
                state: { characters: [], notepad: {} },
              },
            },
          });
          return;
        }

        if (req.method() === 'GET' && url.pathname === '/api/story-planner/sessions/sess-12345678') {
          sessionPollCount += 1;
          const hasCallback = actionRequests.length > 0 && sessionPollCount >= 1;
          await route.fulfill({
            json: {
              id: 'sess-12345678',
              events: hasCallback
                ? [{ result: { narration: 'The spiral staircase groans beneath your boots.' } }]
                : [],
              state: {
                characters: hasCallback ? [{ name: 'Archivist Vela' }] : [],
                notepad: hasCallback ? { tower: 'lantern lit' } : {},
              },
            },
          });
          return;
        }

        await route.fulfill({ status: 404, body: 'Not found' });
      },
    );

    await page.goto(`${CANVAS_ORIGIN}/story-planner`);

    await expect(page.locator('#ack')).toHaveText('Create a session to begin.');
    await expect(page.locator('#action')).toBeDisabled();
    await expect(page.locator('#submit')).toBeDisabled();

    // Error during session creation surfaces cleanly
    createError = 'Planner backend offline';
    await page.locator('#new-session').click();
    await expect(page.locator('#ack')).toHaveText('Planner backend offline');
    createError = null;

    // Create session successfully
    await page.locator('#model').fill('gemini-3.8-pro');
    await page.locator('#nodes').fill('4');
    await page.locator('#new-session').click();

    await expect(page.locator('#ack')).toHaveText('Session sess-123 is ready.');
    await expect(page.locator('#action')).toBeEnabled();
    await expect(page.locator('#nudge')).toBeEnabled();
    await expect(page.locator('#submit')).toBeEnabled();
    expect(createdPayloads.at(-1)).toEqual({
      planner_model: 'gemini-3.8-pro',
      nodes_ahead: 4,
    });

    // Submit action + nudge, then wait for polled callback event
    await page.locator('#action').fill('I step onto the staircase.');
    await page.locator('#nudge').fill('Distant chimes echo above.');
    await page.locator('#submit').click();

    expect(actionRequests).toEqual([
      { action: 'I step onto the staircase.', nudge: 'Distant chimes echo above.' },
    ]);
    await expect(page.locator('#action')).toHaveValue('');
    await expect(page.locator('#nudge')).toHaveValue('');

    await expect(page.locator('#ack')).toHaveText('Planner result received through callback queue.');
    await expect(page.locator('#events')).toContainText('The spiral staircase groans beneath your boots.');
    await expect(page.locator('#characters')).toContainText('Archivist Vela');
    await expect(page.locator('#notepad')).toContainText('lantern lit');

    // Action submission error surfaces in #ack
    actionError = 'Action rejected';
    await page.locator('#action').fill('Retry move');
    await page.locator('#submit').click();
    await expect(page.locator('#ack')).toHaveText('Action rejected');
  });
});
