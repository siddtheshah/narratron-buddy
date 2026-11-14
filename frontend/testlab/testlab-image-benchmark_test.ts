import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  REPO_ROOT,
  expect,
  test,
} from '../../testing/ui/canvas_fixtures';

const IMAGE_BENCHMARK_HTML_PATH = path.join(REPO_ROOT, 'testlab', 'image_benchmark.html');
const SAMPLE_PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z2lQAAAAASUVORK5CYII=';

interface RecordedImageRunRequest {
  readonly provider_ids: readonly string[];
  readonly prompt_ids: readonly string[];
  readonly custom_prompts: ReadonlyArray<Record<string, unknown>>;
  readonly repetitions: number;
  readonly provider_options: Readonly<Record<string, Record<string, string>>>;
}

test.describe('Testlab Image Provider Bench TS module', () => {
  test('renders catalog options, routing badges, provider detail stats, lightbox, and notes', async ({
    canvas,
    page,
  }): Promise<void> => {
    const html = await fs.promises.readFile(IMAGE_BENCHMARK_HTML_PATH, 'utf8');
    const runRequests: RecordedImageRunRequest[] = [];
    const noteUpdates: Array<{ readonly itemId: string; readonly note: string }> = [];
    let pollCount = 0;

    await canvas.serveRoute('https://fonts.googleapis.com/**', async (route: Route): Promise<void> => {
      await route.fulfill({ status: 200, contentType: 'text/css; charset=utf-8', body: '' });
    });

    await canvas.serveRoute(
      (url: URL) =>
        url.origin === CANVAS_ORIGIN &&
        (url.pathname === '/image-benchmark' ||
          url.pathname.startsWith('/api/image-benchmark/') ||
          url.pathname.startsWith('/benchmark-images/')),
      async (route: Route): Promise<void> => {
        const req = route.request();
        const url = new URL(req.url());

        if (url.pathname === '/image-benchmark') {
          await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
          return;
        }

        if (url.pathname.startsWith('/benchmark-images/')) {
          await route.fulfill({
            status: 200,
            contentType: 'image/png',
            body: Buffer.from(SAMPLE_PNG_BASE64, 'base64'),
          });
          return;
        }

        if (req.method() === 'GET' && url.pathname === '/api/image-benchmark/catalog') {
          await route.fulfill({
            json: {
              providers: [
                {
                  id: 'hybrid-flux-gemini',
                  name: 'Hybrid Flux + Gemini',
                  model: 'flux-2-pro',
                  status: 'available',
                  notes: 'Routes reference-heavy scenes to Gemini',
                  estimated_cost_usd_1mp: 0.04,
                  model_options: ['flux-2-pro', 'flux-2-dev'],
                  classifier_model_options: ['gemini-2.5-flash-lite', 'gemini-3.5-flash-lite'],
                },
                {
                  id: 'openai-gpt-image-flare',
                  name: 'OpenAI GPT Image Flare',
                  model: 'gpt-image-2.5-flare',
                  status: 'available',
                  notes: 'High-detail fantasy illustrations',
                  estimated_cost_usd_1mp: 0.055,
                  model_options: ['gpt-image-2.5-flare', 'gpt-image-2.5-sunburst'],
                  quality_options: ['medium', 'high'],
                },
              ],
              prompts: [
                {
                  id: 'cinematic-scene',
                  title: 'Cinematic Scene',
                  dimension: 'Composition',
                  prompt: 'A moonlit caravan crossing a misty stone bridge.',
                  reference_count: 2,
                },
                {
                  id: 'dynamic-worm-angle',
                  title: 'Dynamic Worm Angle',
                  dimension: 'Dynamic perspective',
                  prompt: "A worm's-eye view of towering clockwork spires.",
                  reference_count: 0,
                },
              ],
            },
          });
          return;
        }

        if (req.method() === 'POST' && url.pathname === '/api/image-benchmark/runs') {
          const payload = JSON.parse(req.postData() || '{}') as RecordedImageRunRequest;
          runRequests.push(payload);
          pollCount = 0;
          await route.fulfill({ json: { id: 'img-run-1', status: 'running', total: 2 } });
          return;
        }

        if (req.method() === 'GET' && url.pathname === '/api/image-benchmark/runs/img-run-1') {
          pollCount += 1;
          const completed = pollCount >= 2;
          await route.fulfill({
            json: {
              id: 'img-run-1',
              status: completed ? 'completed' : 'running',
              total: 2,
              prompts: [
                {
                  id: 'custom',
                  title: 'Custom Prompt',
                  dimension: 'Manual Prompt',
                  prompt: 'Brass astrolabe over misty falls.',
                },
              ],
              items: completed
                ? [
                    {
                      id: 'img-item-1',
                      provider_id: 'hybrid-flux-gemini',
                      prompt_id: 'custom',
                      repetition: 1,
                      status: 'completed',
                      latency_ms: 1420,
                      model: 'gemini-3-image',
                      image_url: '/benchmark-images/sample-1.png',
                      estimated_output_cost_usd: 0.0336,
                      usage: {
                        routing: {
                          route_to_fallback: true,
                          selected_provider: 'gemini',
                          reasons: ['reference_terms'],
                          ambiguous_terms: ['astrolabe'],
                        },
                      },
                    },
                    {
                      id: 'img-item-2',
                      provider_id: 'openai-gpt-image-flare',
                      prompt_id: 'custom',
                      repetition: 1,
                      status: 'completed',
                      latency_ms: 2100,
                      model: 'gpt-image-2.5-flare',
                      image_url: '/benchmark-images/sample-2.png',
                      estimated_output_cost_usd: 0.055,
                      usage: {
                        routing: {
                          route_to_fallback: false,
                          selected_provider: 'openai-gpt-image-flare',
                        },
                      },
                    },
                  ]
                : [],
            },
          });
          return;
        }

        const noteMatch = url.pathname.match(
          /^\/api\/image-benchmark\/runs\/img-run-1\/items\/([^/]+)\/note$/,
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

    await page.goto(`${CANVAS_ORIGIN}/image-benchmark`);

    await expect(page.locator('#selectAll')).toHaveText('Select all 2');
    await expect(page.locator('#referenceCountStatus')).toHaveText(
      'Includes 1 reference-aware prompts.',
    );
    await expect(
      page.locator('[data-provider-id="openai-gpt-image-flare"][data-provider-option="quality"]'),
    ).toHaveValue('high');
    await expect(
      page.locator('[data-provider-id="hybrid-flux-gemini"][data-provider-option="classifier_model"]'),
    ).toHaveValue('gemini-2.5-flash-lite');

    // Validation message when no prompts selected
    await page.locator('#deselectAll').click();
    await page.locator('#run').click();
    await expect(page.locator('#status')).toContainText('Select an available provider');

    // Custom prompt auto-checks #useCustomPrompt and submits run
    await page.locator('#customPromptText').fill('Brass astrolabe over misty falls.');
    await expect(page.locator('#useCustomPrompt')).toBeChecked();
    await page.locator('#run').click();

    await expect(page.locator('#status')).toHaveText('Completed 2 provider/prompt comparisons.');
    await expect(page.locator('#galleryResults .result')).toHaveCount(2);
    await expect(page.locator('#galleryResults')).toContainText('Rerouted → gemini');
    await expect(page.locator('#galleryResults')).toContainText('Reason: reference_terms (astrolabe)');
    await expect(page.locator('#galleryResults')).toContainText('Stayed → openai-gpt-image-flare');

    expect(runRequests).toHaveLength(1);
    expect(runRequests[0].custom_prompts).toEqual([
      {
        prompt: 'Brass astrolabe over misty falls.',
        title: 'Custom Prompt',
        dimension: 'Manual Prompt',
      },
    ]);

    // Filter by specific provider to verify provider detail stats card
    await page.locator('#galleryProvider').selectOption('hybrid-flux-gemini');
    await expect(page.locator('#galleryPromptDetail')).toBeVisible();
    await expect(page.locator('#galleryPromptDetail')).toContainText('Hybrid Flux + Gemini');
    await expect(page.locator('#galleryPromptDetail')).toContainText('Est. output cost $0.034');

    // Switch to prompt view
    await page.locator('#galleryView').selectOption('prompt');
    await expect(page.locator('#galleryPromptDetail')).toContainText('Custom Prompt');

    // Lightbox open and Escape close
    await page.locator('#galleryResults .result img').first().click();
    await expect(page.locator('#lightbox')).toHaveAttribute('aria-hidden', 'false');
    await page.keyboard.press('Escape');
    await expect(page.locator('#lightbox')).toHaveAttribute('aria-hidden', 'true');

    // Review note persistence
    const noteBox = page.locator('#galleryResults .generation-note').first();
    await noteBox.fill('Accurate brass details');
    await noteBox.dispatchEvent('change');
    await expect.poll(() => noteUpdates.length).toBe(1);
    expect(noteUpdates[0]).toEqual({ itemId: 'img-item-1', note: 'Accurate brass details' });
  });
});
