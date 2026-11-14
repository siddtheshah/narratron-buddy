import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  REPO_ROOT,
  expect,
  test,
} from '../../testing/ui/canvas_fixtures';

const TEXT_BENCHMARK_HTML_PATH = path.join(REPO_ROOT, 'testlab', 'text_response_benchmark.html');

interface RecordedTextRunRequest {
  readonly provider_ids: readonly string[];
  readonly prompt_ids: readonly string[];
  readonly custom_prompts: ReadonlyArray<Record<string, unknown>>;
  readonly repetitions: number;
  readonly provider_options: Readonly<Record<string, Record<string, string>>>;
}

test.describe('Testlab Text Response Provider Bench TS module', () => {
  test('loads catalog, runs benchmark with custom prompt, paginates gallery, and saves review notes', async ({
    canvas,
    page,
  }): Promise<void> => {
    const html = await fs.promises.readFile(TEXT_BENCHMARK_HTML_PATH, 'utf8');
    const runRequests: RecordedTextRunRequest[] = [];
    const noteUpdates: Array<{ readonly itemId: string; readonly note: string }> = [];
    let noteError: string | null = null;
    const dialogs: string[] = [];

    page.on('dialog', (dialog): void => {
      dialogs.push(dialog.message());
      void dialog.dismiss();
    });

    await canvas.serveRoute('https://fonts.googleapis.com/**', async (route: Route): Promise<void> => {
      await route.fulfill({ status: 200, contentType: 'text/css; charset=utf-8', body: '' });
    });

    await canvas.serveRoute(
      (url: URL) =>
        url.origin === CANVAS_ORIGIN &&
        (url.pathname === '/text-benchmark' ||
          url.pathname.startsWith('/api/text-benchmark/')),
      async (route: Route): Promise<void> => {
        const req = route.request();
        const url = new URL(req.url());

        if (url.pathname === '/text-benchmark') {
          await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
          return;
        }

        if (req.method() === 'GET' && url.pathname === '/api/text-benchmark/catalog') {
          await route.fulfill({
            json: {
              providers: [
                {
                  id: 'gemini-3',
                  name: 'Gemini 3',
                  model: 'gemini-3.8-flash',
                  status: 'available',
                  notes: 'Fast narrative generation',
                  model_options: ['gemini-3.8-flash', 'gemini-3.8-pro'],
                },
                {
                  id: 'openai-gpt',
                  name: 'OpenAI GPT',
                  model: 'gpt-6-luna',
                  status: 'unconfigured',
                  notes: 'API key missing',
                },
              ],
              prompts: [
                {
                  id: 'dm-adventure-intro',
                  title: 'DM Adventure Intro',
                  dimension: 'Narration Quality',
                  prompt: 'Open the campaign in a flooded library.',
                  temperature: 0.7,
                },
                {
                  id: 'dialogue-duel',
                  title: 'Dialogue Duel',
                  dimension: 'Character Voice',
                  prompt: 'Two rivals barter over a cracked compass.',
                  temperature: 0.8,
                },
              ],
            },
          });
          return;
        }

        if (req.method() === 'POST' && url.pathname === '/api/text-benchmark/runs') {
          const payload = JSON.parse(req.postData() || '{}') as RecordedTextRunRequest;
          runRequests.push(payload);
          await route.fulfill({
            json: {
              id: 'text-run-1',
              status: 'running',
              total: 14,
              items: [],
            },
          });
          return;
        }

        if (req.method() === 'GET' && url.pathname === '/api/text-benchmark/runs/text-run-1') {
          const items = Array.from({ length: 14 }, (_unused, idx: number) => ({
            id: `item-${idx + 1}`,
            provider_id: idx < 13 ? 'gemini-3' : 'openai-gpt',
            prompt_id: idx === 0 ? 'custom' : 'dm-adventure-intro',
            repetition: idx + 1,
            status: idx === 13 ? 'failed' : 'completed',
            latency_ms: 400 + idx * 50,
            text: idx === 13 ? undefined : `Generated passage #${idx + 1}`,
            model: idx < 13 ? 'gemini-3.8-pro' : 'gpt-6-luna',
            finish_reason: idx === 0 ? 'STOP' : undefined,
            error: idx === 13 ? 'Timeout waiting for provider' : undefined,
            note: idx === 0 ? 'initial note' : '',
          }));
          await route.fulfill({
            json: {
              id: 'text-run-1',
              status: 'completed',
              total: 14,
              prompts: [
                {
                  id: 'dm-adventure-intro',
                  title: 'DM Adventure Intro',
                  dimension: 'Narration Quality',
                  prompt: 'Open the campaign in a flooded library.',
                  temperature: 0.7,
                },
                {
                  id: 'custom',
                  title: 'Custom Text Prompt',
                  dimension: 'Manual Prompt',
                  prompt: 'Describe a clockwork city.',
                  temperature: 0.5,
                },
              ],
              items,
            },
          });
          return;
        }

        const noteMatch = url.pathname.match(
          /^\/api\/text-benchmark\/runs\/text-run-1\/items\/([^/]+)\/note$/,
        );
        if (req.method() === 'PUT' && noteMatch) {
          const body = JSON.parse(req.postData() || '{}') as { note: string };
          if (noteError !== null) {
            await route.fulfill({ status: 400, json: { detail: noteError } });
            return;
          }
          noteUpdates.push({ itemId: noteMatch[1], note: body.note });
          await route.fulfill({ json: { id: noteMatch[1], note: body.note.trim() } });
          return;
        }

        await route.fulfill({ status: 404, body: 'Not found' });
      },
    );

    await page.goto(`${CANVAS_ORIGIN}/text-benchmark`);

    await expect(page.locator('#providers .choice')).toHaveCount(2);
    await expect(page.locator('#prompts .choice')).toHaveCount(2);

    // Validation alert when nothing selected
    await page.locator('#deselectAll').click();
    await page.locator('#run').click();
    expect(dialogs.at(-1)).toContain('Select at least one provider and one prompt');

    // Configure model + custom prompt + repetitions
    await page.locator('[data-provider-id="gemini-3"][data-provider-option="model"]').selectOption(
      'gemini-3.8-pro',
    );
    await page.locator('#selectAll').click();
    await page.locator('#repetitions').fill('2');
    await page.locator('#customPromptText').fill('Describe a clockwork city.');
    await expect(page.locator('#useCustomPrompt')).toBeChecked();
    await page.locator('#customSystemInstruction').fill('Be vivid.');
    await page.locator('#customTemperature').fill('0.5');
    await page.locator('#customMaxTokens').fill('800');

    await page.locator('#run').click();

    await expect(page.locator('#status')).toHaveText('Run completed: 14/14 responses generated');
    await expect(page.locator('[data-provider-stats="gemini-3"]')).toContainText('Done 13/13');
    await expect(page.locator('[data-provider-stats="openai-gpt"]')).toContainText('Failed 1');

    expect(runRequests).toHaveLength(1);
    expect(runRequests[0]).toEqual({
      provider_ids: ['gemini-3'],
      prompt_ids: ['dm-adventure-intro', 'dialogue-duel'],
      custom_prompts: [
        {
          prompt: 'Describe a clockwork city.',
          title: 'Custom Text Prompt',
          dimension: 'Manual Prompt',
          system_instruction: 'Be vivid.',
          temperature: 0.5,
          max_output_tokens: 800,
        },
      ],
      repetitions: 2,
      provider_options: {
        'gemini-3': { model: 'gemini-3.8-pro' },
      },
    });

    // Pagination & gallery view slicing
    await expect(page.locator('#galleryPage')).toHaveText('Page 1 of 2 · 14 responses');
    await expect(page.locator('#galleryResults .result')).toHaveCount(12);
    await page.locator('#nextPage').click();
    await expect(page.locator('#galleryPage')).toHaveText('Page 2 of 2 · 14 responses');
    await expect(page.locator('#galleryResults .result')).toHaveCount(2);

    await page.locator('#galleryView').selectOption('prompt');
    await expect(page.locator('#galleryPromptDetail')).toBeVisible();
    await expect(page.locator('#galleryPromptDetail')).toContainText('Custom Text Prompt');

    // Review note save and error surfacing
    const firstNote = page.locator('#galleryResults .generation-note').first();
    await firstNote.fill('Great pacing');
    await firstNote.dispatchEvent('change');
    await expect.poll(() => noteUpdates.length).toBe(1);
    expect(noteUpdates[0]).toEqual({ itemId: 'item-1', note: 'Great pacing' });

    noteError = 'Note too long';
    await firstNote.fill('Invalid note');
    await firstNote.dispatchEvent('change');
    await expect.poll(() => dialogs.at(-1)).toBe('Note too long');
  });
});
