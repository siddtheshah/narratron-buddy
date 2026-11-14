import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  REPO_ROOT,
  expect,
  test,
} from '../../testing/ui/canvas_fixtures';

const MUSIC_BENCHMARK_HTML_PATH = path.join(REPO_ROOT, 'testlab', 'music_benchmark.html');

interface RecordedMusicRunRequest {
  readonly provider_ids: readonly string[];
  readonly prompt_ids: readonly string[];
  readonly custom_prompts: ReadonlyArray<Record<string, unknown>>;
  readonly repetitions: number;
  readonly provider_options: Readonly<Record<string, unknown>>;
}

test.describe('Testlab Music Provider Bench TS module', () => {
  test('renders adapters, streams SSE progress, polls results, slices gallery, and saves notes', async ({
    canvas,
    page,
  }): Promise<void> => {
    const html = await fs.promises.readFile(MUSIC_BENCHMARK_HTML_PATH, 'utf8');
    const runRequests: RecordedMusicRunRequest[] = [];
    const noteUpdates: Array<{ readonly itemId: string; readonly note: string }> = [];
    let pollCount = 0;

    await canvas.serveRoute('https://fonts.googleapis.com/**', async (route: Route): Promise<void> => {
      await route.fulfill({ status: 200, contentType: 'text/css; charset=utf-8', body: '' });
    });

    await canvas.serveRoute(
      (url: URL) =>
        url.origin === CANVAS_ORIGIN &&
        (url.pathname === '/music-benchmark' ||
          url.pathname.startsWith('/api/music-benchmark/') ||
          url.pathname.startsWith('/benchmark-audio/')),
      async (route: Route): Promise<void> => {
        const req = route.request();
        const url = new URL(req.url());

        if (url.pathname === '/music-benchmark') {
          await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
          return;
        }

        if (url.pathname.startsWith('/benchmark-audio/')) {
          await route.fulfill({
            status: 200,
            contentType: url.pathname.endsWith('.wav') ? 'audio/wav' : 'audio/mpeg',
            body: Buffer.from(''),
          });
          return;
        }

        if (req.method() === 'GET' && url.pathname === '/api/music-benchmark/catalog') {
          await route.fulfill({
            json: {
              providers: [
                {
                  id: 'lyria',
                  name: 'Google Lyria',
                  model: 'lyria-2',
                  status: 'available',
                  notes: 'Rich orchestral scores',
                  estimated_cost_usd_30s: 0.06,
                },
                {
                  id: 'test-base-plus-adapter',
                  name: 'Base + Audio Adapter',
                  model: 'composed',
                  status: 'available',
                  notes: 'Two-stage base + style adapter',
                  estimated_cost_usd_per_generation: 0.11,
                },
              ],
              adapters: [
                {
                  id: 'fal-stable-audio-3-base-a2a',
                  name: 'Stable Audio Adapter',
                  status: 'available',
                  estimated_cost_usd_per_generation: 0.05,
                },
              ],
              prompts: [
                {
                  id: 'cinematic-orchestral',
                  title: 'Cinematic Orchestral',
                  dimension: 'Epic Score',
                  prompt: 'Sweeping brass fanfare over timpani.',
                  duration_seconds: 30,
                  genre: 'Orchestral',
                  tempo: 'Maestoso',
                },
              ],
            },
          });
          return;
        }

        if (req.method() === 'POST' && url.pathname === '/api/music-benchmark/runs') {
          const payload = JSON.parse(req.postData() || '{}') as RecordedMusicRunRequest;
          runRequests.push(payload);
          pollCount = 0;
          await route.fulfill({
            json: { id: 'music-run-1', status: 'running', total: 2, items: [] },
          });
          return;
        }

        if (req.method() === 'GET' && url.pathname === '/api/music-benchmark/runs/music-run-1/events') {
          await route.fulfill({
            status: 200,
            contentType: 'text/event-stream; charset=utf-8',
            body:
              'data: {"stage":"adapter_generating","prompt_id":"custom","repetition":1}\n\n' +
              'event: complete\ndata: {}\n\n',
          });
          return;
        }

        if (req.method() === 'GET' && url.pathname === '/api/music-benchmark/runs/music-run-1') {
          pollCount += 1;
          const completed = pollCount >= 2;
          await route.fulfill({
            json: {
              id: 'music-run-1',
              status: completed ? 'completed' : 'running',
              total: 2,
              prompts: [
                {
                  id: 'custom',
                  title: 'Custom Music',
                  dimension: 'Manual Music',
                  prompt: 'Synthwave neon pulse.',
                  duration_seconds: 20,
                },
              ],
              items: completed
                ? [
                    {
                      id: 'music-item-1',
                      provider_id: 'test-base-plus-adapter',
                      prompt_id: 'custom',
                      repetition: 1,
                      status: 'completed',
                      latency_ms: 2450,
                      model: 'lyria-2 -> stable-audio',
                      audio_url: '/benchmark-audio/adapted.mp3',
                      base_audio_url: '/benchmark-audio/base.wav',
                      base_model: 'lyria-2',
                      base_latency_ms: 1200,
                      adapter_latency_ms: 1250,
                      estimated_output_cost_usd: 0.112,
                      note: '',
                    },
                    {
                      id: 'music-item-2',
                      provider_id: 'lyria',
                      prompt_id: 'custom',
                      repetition: 1,
                      status: 'failed',
                      latency_ms: 300,
                      error: 'Upstream busy',
                    },
                  ]
                : [],
            },
          });
          return;
        }

        const noteMatch = url.pathname.match(
          /^\/api\/music-benchmark\/runs\/music-run-1\/items\/([^/]+)\/note$/,
        );
        if (req.method() === 'PUT' && noteMatch) {
          const body = JSON.parse(req.postData() || '{}') as { note: string };
          noteUpdates.push({ itemId: noteMatch[1], note: body.note });
          await route.fulfill({ json: { id: noteMatch[1], note: body.note.trim() } });
          return;
        }

        await route.fulfill({ status: 404, body: 'Not found' });
      },
    );

    await page.goto(`${CANVAS_ORIGIN}/music-benchmark`);

    await expect(page.locator('#providers .choice')).toHaveCount(2);
    await expect(
      page.locator('[data-provider-id="test-base-plus-adapter"][data-provider-option="base_provider"]'),
    ).toHaveValue('lyria');
    await expect(
      page.locator('[data-provider-id="test-base-plus-adapter"][data-provider-option="adapter"]'),
    ).toHaveValue('fal-stable-audio-3-base-a2a');

    // Validation when deselecting all prompts
    await page.locator('#deselectAll').click();
    await page.locator('#run').click();
    await expect(page.locator('#status')).toContainText('Select an available music provider');

    // Configure adapter noise + custom music prompt
    await page
      .locator('[data-provider-id="test-base-plus-adapter"][data-adapter-option="init_noise_level"]')
      .fill('0.45');
    await page.locator('#customPromptText').fill('Synthwave neon pulse.');
    await expect(page.locator('#useCustomPrompt')).toBeChecked();
    await page.locator('#customDuration').fill('20');
    await page.locator('#customGenre').fill('Synthwave');
    await page.locator('#customTempo').fill('Allegro');

    await page.locator('#run').click();

    await expect(page.locator('#status')).toHaveText(
      'Completed 2 provider/prompt music comparisons.',
    );
    await expect(page.locator('[data-provider-stats="test-base-plus-adapter"]')).toContainText(
      'Est. output cost $0.112',
    );
    await expect(page.locator('#galleryResults .result')).toHaveCount(2);
    await expect(page.locator('#galleryResults')).toContainText('Base track · lyria-2');
    await expect(page.locator('#galleryResults')).toContainText('Adapted track');
    await expect(page.locator('#galleryResults')).toContainText(
      'Base latency: 1.20 s · Adapter latency: 1.25 s',
    );
    await expect(page.locator('#galleryResults .result.error .error-text')).toHaveText(
      'Upstream busy',
    );

    expect(runRequests).toHaveLength(1);
    expect(runRequests[0]).toEqual({
      provider_ids: ['lyria', 'test-base-plus-adapter'],
      prompt_ids: [],
      custom_prompts: [
        {
          prompt: 'Synthwave neon pulse.',
          title: 'Custom Music',
          dimension: 'Manual Music',
          duration_seconds: 20,
          genre: 'Synthwave',
          tempo: 'Allegro',
        },
      ],
      repetitions: 1,
      provider_options: {
        'test-base-plus-adapter': {
          base_provider: 'lyria',
          adapter: 'fal-stable-audio-3-base-a2a',
          adapter_options: { init_noise_level: 0.45 },
        },
      },
    });

    // Switch gallery to prompt view
    await page.locator('#galleryView').selectOption('prompt');
    await expect(page.locator('#galleryPromptDetail')).toBeVisible();
    await expect(page.locator('#galleryPromptDetail')).toContainText('Custom Music');

    // Save note on test-base-plus-adapter card (sorted after 'lyria')
    const noteBox = page.locator('#galleryResults .generation-note').nth(1);
    await noteBox.fill('Clean transition');
    await noteBox.dispatchEvent('change');
    await expect.poll(() => noteUpdates.length).toBe(1);
    expect(noteUpdates[0]).toEqual({ itemId: 'music-item-1', note: 'Clean transition' });
  });
});
