import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  REPO_ROOT,
  expect,
  test,
} from '../../testing/ui/canvas_fixtures';

const SPEECH_BENCHMARK_HTML_PATH = path.join(REPO_ROOT, 'testlab', 'speech_benchmark.html');

interface RecordedRunRequest {
  readonly provider_ids: readonly string[];
  readonly prompt_ids: readonly string[];
  readonly custom_prompts: ReadonlyArray<Record<string, unknown>>;
  readonly provider_options: Readonly<Record<string, Record<string, string>>>;
}

test.describe('Testlab Speech Provider Bench TS module', () => {
  test('renders catalog, validates selection, runs custom dialogue, and displays audio results', async ({
    canvas,
    page,
  }): Promise<void> => {
    const html = await fs.promises.readFile(SPEECH_BENCHMARK_HTML_PATH, 'utf8');
    const runRequests: RecordedRunRequest[] = [];
    let runStartError: string | null = null;
    let pollCount = 0;

    await canvas.serveRoute(
      (url: URL) =>
        url.origin === CANVAS_ORIGIN &&
        (url.pathname === '/speech-benchmark' ||
          url.pathname.startsWith('/api/speech-benchmark/') ||
          url.pathname.startsWith('/benchmark-speech/')),
      async (route: Route): Promise<void> => {
        const req = route.request();
        const url = new URL(req.url());

        if (url.pathname === '/speech-benchmark') {
          await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
          return;
        }

        if (url.pathname.startsWith('/benchmark-speech/')) {
          await route.fulfill({
            status: 200,
            contentType: 'audio/wav',
            body: Buffer.from(''),
          });
          return;
        }

        if (req.method() === 'GET' && url.pathname === '/api/speech-benchmark/catalog') {
          await route.fulfill({
            json: {
              providers: [
                {
                  id: 'gemini-flash-tts',
                  name: 'Gemini Flash TTS',
                  model: 'gemini-3.8-flash-tts',
                  notes: 'Expressive multi-speaker TTS',
                  estimated_cost_usd_1k_chars: 0.015,
                  status: 'available',
                  model_options: ['gemini-3.8-flash-tts', 'gemini-3.8-flash-lite-tts'],
                },
                {
                  id: 'google-chirp-3-hd',
                  name: 'Google Chirp 3 HD',
                  model: 'chirp-3-hd',
                  notes: 'Missing API key',
                  estimated_cost_usd_1k_chars: null,
                  status: 'unconfigured',
                },
              ],
              prompts: [
                {
                  id: 'heroic-rally',
                  title: 'Heroic Rally',
                  dimension: 'Emotional Range',
                  text: 'Stand fast, dawn is breaking!',
                  voice_instruction: 'Bold and inspiring',
                },
                {
                  id: 'nervous-alchemist',
                  title: 'Nervous Alchemist',
                  dimension: 'Character Performance',
                  text: 'Do not shake that vial!',
                  voice_instruction: 'Hushed, frantic whisper',
                },
              ],
            },
          });
          return;
        }

        if (req.method() === 'POST' && url.pathname === '/api/speech-benchmark/runs') {
          const payload = JSON.parse(req.postData() || '{}') as RecordedRunRequest;
          runRequests.push(payload);
          if (runStartError !== null) {
            await route.fulfill({ status: 400, json: { detail: runStartError } });
            return;
          }
          pollCount = 0;
          await route.fulfill({ json: { id: 'speech-run-1', status: 'running', total: 2 } });
          return;
        }

        if (req.method() === 'GET' && url.pathname === '/api/speech-benchmark/runs/speech-run-1') {
          pollCount += 1;
          const completed = pollCount >= 2;
          await route.fulfill({
            json: {
              id: 'speech-run-1',
              status: completed ? 'completed' : 'running',
              total: 2,
              prompts: [
                {
                  id: 'custom',
                  title: 'Custom Dialogue',
                  dimension: 'Manual Dialogue',
                  text: 'Open the hidden gate.',
                  voice_instruction: 'Low and urgent',
                },
              ],
              items: completed
                ? [
                    {
                      id: 'item-1',
                      provider_id: 'gemini-flash-tts',
                      prompt_id: 'custom',
                      started_at: 100,
                      status: 'completed',
                      latency_ms: 820,
                      model: 'gemini-3.8-flash-lite-tts',
                      selected_voice: 'Aoede',
                      voice_tags: ['female', 'accent=General American'],
                      audio_url: '/benchmark-speech/sample.wav',
                      estimated_output_cost_usd: 0.0012,
                    },
                    {
                      id: 'item-2',
                      provider_id: 'google-chirp-3-hd',
                      prompt_id: 'custom',
                      started_at: 200,
                      status: 'failed',
                      latency_ms: 150,
                      error: 'Provider credentials not set',
                    },
                  ]
                : [
                    {
                      id: 'item-1',
                      provider_id: 'gemini-flash-tts',
                      prompt_id: 'custom',
                      started_at: 100,
                      status: 'completed',
                      latency_ms: 820,
                      model: 'gemini-3.8-flash-lite-tts',
                      audio_url: '/benchmark-speech/sample.wav',
                    },
                  ],
            },
          });
          return;
        }

        await route.fulfill({ status: 404, body: 'Not found' });
      },
    );

    await page.goto(`${CANVAS_ORIGIN}/speech-benchmark`);

    await expect(page.locator('#providers .choice')).toHaveCount(2);
    await expect(page.locator('#providers')).toContainText('Voice tags / filters (selects a voice)');
    await expect(page.locator('#prompts .choice')).toHaveCount(2);
    await expect(page.locator('#providers input[value="gemini-flash-tts"]')).toBeChecked();
    await expect(page.locator('#providers input[value="google-chirp-3-hd"]')).toBeDisabled();

    // Validation when deselecting all prompts
    await page.locator('#selectAll').click();
    await expect(page.locator('#prompts input:checked')).toHaveCount(2);
    await page.locator('#deselectAll').click();
    await expect(page.locator('#prompts input:checked')).toHaveCount(0);
    await page.locator('#run').click();
    await expect(page.locator('#status')).toContainText('Select at least one available provider');
    expect(runRequests).toHaveLength(0);

    // Custom dialogue auto-checks #useCustomPrompt and forwards options
    await page.locator('[data-provider="gemini-flash-tts"][data-option="model"]').selectOption(
      'gemini-3.8-flash-lite-tts',
    );
    await page.locator('[data-provider="gemini-flash-tts"][data-option="voice_tags"]').fill(
      'female, accent=General American',
    );
    await page.locator('#customDialogueText').fill('Open the hidden gate.');
    await expect(page.locator('#useCustomPrompt')).toBeChecked();
    await page.locator('#customVoiceInstruction').fill('Low and urgent');

    // Run start error surfaces in #status
    runStartError = 'Quota limit reached';
    await page.locator('#run').click();
    await expect(page.locator('#status')).toHaveText('Quota limit reached');
    await expect(page.locator('#run')).toBeEnabled();
    runStartError = null;

    // Successful run + polling to completion
    await page.locator('#run').click();
    await expect(page.locator('#status')).toHaveText('Completed 2 speech comparisons.');
    await expect(page.locator('#run')).toBeEnabled();
    await expect(page.locator('#results .result')).toHaveCount(2);
    await expect(page.locator('#results')).toContainText(
      'Selected voice: Aoede · tags: female, accent=General American',
    );
    await expect(page.locator('#results')).toContainText('Estimated output cost: $0.0012');
    await expect(page.locator('#results .result.error .error-text')).toHaveText(
      'Provider credentials not set',
    );
    expect(runRequests.at(-1)).toEqual({
      provider_ids: ['gemini-flash-tts'],
      prompt_ids: [],
      custom_prompts: [
        {
          text: 'Open the hidden gate.',
          voice_instruction: 'Low and urgent',
          title: 'Custom Dialogue',
          dimension: 'Manual Dialogue',
        },
      ],
      provider_options: {
        'gemini-flash-tts': {
          model: 'gemini-3.8-flash-lite-tts',
          voice_tags: 'female, accent=General American',
        },
      },
    });
  });
});
