import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  REPO_ROOT,
  expect,
  test,
} from '../../testing/ui/canvas_fixtures';

const DECISION_LAB_HTML_PATH = path.join(REPO_ROOT, 'testlab', 'decision_lab.html');

interface RecordedEvalRequest {
  readonly mode: string;
  readonly provider_id: string;
  readonly scenario_id: string | null;
  readonly input_text: string;
  readonly questions: readonly Record<string, unknown>[];
}

interface MockDecisionLabRoutes {
  readonly evalRequests: RecordedEvalRequest[];
  readonly benchmarkRequests: Array<Record<string, unknown>>;
  setEvalError(message: string | null): void;
}

function buildSampleCatalog(): Record<string, unknown> {
  return {
    providers: [
      {
        id: 'openai-decisions',
        name: 'OpenAI Decisions',
        model: 'gpt-6-luna',
        status: 'available',
        model_options: ['gpt-6-luna', 'gpt-6-mini'],
      },
      {
        id: 'gemini-flash-lite',
        name: 'Gemini Flash-Lite',
        model: 'gemini-3.5-flash-lite',
        status: 'unconfigured',
        model_options: ['gemini-3.5-flash-lite'],
      },
    ],
    scenarios: [
      {
        id: 'scene-route-gate',
        title: 'Scene Route Gate',
        category: 'Routing',
        subsystem: 'StoryPlanner',
        description: 'Evaluates narration gate, technique choice, and drama score.',
        decision_rule: 'Wake responder when should_narrate >= 0.6',
        input_text: 'The lantern flickers across the flooded crypt.',
        questions: [
          {
            type: 'predicate',
            name: 'should_narrate',
            instructions: 'Should the narrator respond now?',
          },
          {
            type: 'choice',
            name: 'visual_technique',
            instructions: 'Pick rendering technique',
            choices: [
              { value: 'triframe', label: 'Triframe' },
              { value: 'layered', label: 'Layered' },
            ],
          },
          {
            type: 'score',
            name: 'drama_level',
            instructions: 'Rate dramatic intensity',
            levels: [
              { label: 'low', description: 'Quiet moment' },
              { label: 'medium', description: 'Tense pause' },
              { label: 'high', description: 'Climactic reveal' },
            ],
          },
        ],
      },
      {
        id: 'beautifier-ranked-1',
        title: 'Text Beautifier Span Selector',
        category: 'Beautifier',
        subsystem: 'TextBeautifier',
        description: 'Ranks dramatic spans in scene prose.',
        decision_rule: 'Highlight highest-scoring line.',
        input_text: 'Rain fell.\nThe iron door groaned open.',
        questions: [
          {
            type: 'choice',
            name: 'highlight_span',
            instructions: 'Choose span',
            choices: ['line_1', 'line_2'],
          },
        ],
      },
    ],
  };
}

async function setupDecisionLabMocks(page: import('@playwright/test').Page): Promise<MockDecisionLabRoutes> {
  const evalRequests: RecordedEvalRequest[] = [];
  const benchmarkRequests: Array<Record<string, unknown>> = [];
  let evalError: string | null = null;
  let benchmarkPollCount = 0;

  await page.route('https://fonts.googleapis.com/**', async (route: Route): Promise<void> => {
    await route.fulfill({
      status: 200,
      contentType: 'text/css; charset=utf-8',
      body: '',
    });
  });

  await page.route(`${CANVAS_ORIGIN}/decision-lab`, async (route: Route): Promise<void> => {
    await route.fulfill({
      status: 200,
      contentType: 'text/html; charset=utf-8',
      body: fs.readFileSync(DECISION_LAB_HTML_PATH, 'utf8'),
    });
  });

  await page.route(`${CANVAS_ORIGIN}/api/decision-lab/**`, async (route: Route): Promise<void> => {
    const req = route.request();
    const url = new URL(req.url());

    if (req.method() === 'GET' && url.pathname === '/api/decision-lab/catalog') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(buildSampleCatalog()),
      });
      return;
    }

    if (req.method() === 'POST' && url.pathname === '/api/decision-lab/evaluate') {
      const payload = JSON.parse(req.postData() || '{}') as RecordedEvalRequest;
      evalRequests.push(payload);
      if (evalError !== null) {
        await route.fulfill({
          status: 400,
          contentType: 'application/json',
          body: JSON.stringify({ detail: evalError }),
        });
        return;
      }

      const isBeautifier = payload.scenario_id === 'beautifier-ranked-1';
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          mode: payload.mode,
          provider: payload.mode === 'live' ? payload.provider_id : 'walkthrough-preview',
          model: 'gpt-6-luna',
          latency_ms: payload.mode === 'live' ? 48.5 : 1.2,
          request_wire: {
            model: 'gpt-6-luna',
            input: payload.input_text,
            questions: payload.questions,
          },
          answers: isBeautifier
            ? [
                {
                  type: 'choice',
                  name: 'highlight_span',
                  choice: 'line_2',
                  confidence: 0.91,
                  probabilities: { line_1: 0.09, line_2: 0.91 },
                },
              ]
            : [
                {
                  type: 'predicate',
                  name: 'should_narrate',
                  probability: 0.87,
                  passed: true,
                },
                {
                  type: 'choice',
                  name: 'visual_technique',
                  choice: 'layered',
                  confidence: 0.82,
                  probabilities: { triframe: 0.18, layered: 0.82 },
                },
                {
                  type: 'score',
                  name: 'drama_level',
                  score: 1.65,
                  confidence: 0.76,
                  probabilities: { low: 0.05, medium: 0.25, high: 0.7 },
                },
              ],
          narratron_outcome: isBeautifier
            ? {
                status: 'action',
                badge: 'SPAN: LINE_2',
                headline: 'Highlight Line 2',
                explanation: 'Selected the iron door line for kinetic styling.',
                trace: ['highlight_span: line_2'],
                preview_segments: [
                  { text: 'Rain fell.\n', highlight: false },
                  { text: 'The iron door groaned open.', highlight: true },
                ],
                candidate_ranking: [
                  {
                    text: 'The iron door groaned open.',
                    line: 2,
                    probability: 0.91,
                    reason: 'Highest dramatic verb intensity',
                  },
                ],
              }
            : {
                status: 'action',
                badge: 'NARRATION TRIGGERED',
                headline: 'Wake StoryResponderModule',
                explanation: 'Predicate and drama score crossed trigger thresholds.',
                trace: ['should_narrate: 87%', 'visual_technique: layered'],
              },
          usage: { input_tokens: 40, output_tokens: 6 },
        }),
      });
      return;
    }

    if (req.method() === 'POST' && url.pathname === '/api/decision-lab/benchmark/runs') {
      const payload = JSON.parse(req.postData() || '{}') as Record<string, unknown>;
      benchmarkRequests.push(payload);
      benchmarkPollCount = 0;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'run-12345678',
          status: 'running',
          total: 2,
          provider_ids: payload.provider_ids,
          items: [],
          summary: { providers: {}, by_scenario: {} },
        }),
      });
      return;
    }

    if (req.method() === 'GET' && url.pathname === '/api/decision-lab/benchmark/runs/run-12345678') {
      benchmarkPollCount += 1;
      const done = benchmarkPollCount >= 1;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'run-12345678',
          status: done ? 'completed' : 'running',
          total: 2,
          provider_ids: ['openai-decisions'],
          items: [
            {
              provider_id: 'openai-decisions',
              scenario_id: 'scene-route-gate',
              status: 'completed',
              narratron_outcome: null,
            },
            {
              provider_id: 'openai-decisions',
              scenario_id: 'beautifier-ranked-1',
              status: 'completed',
              narratron_outcome: {
                status: 'action',
                badge: 'SPAN: LINE_2',
                headline: 'Highlight Line 2',
                explanation: 'Span selected.',
                preview_segments: [{ text: 'The iron door groaned open.', highlight: true }],
                candidate_ranking: [
                  {
                    text: 'The iron door groaned open.',
                    line: 2,
                    probability: 0.91,
                    reason: 'Top candidate',
                  },
                ],
              },
            },
          ],
          summary: {
            providers: {
              'openai-decisions': {
                provider_id: 'openai-decisions',
                total: 2,
                completed: 2,
                failed: 0,
                mean_ms: 42.3,
                p50_ms: 41.0,
                p95_ms: 49.0,
                min_ms: 35.0,
                max_ms: 50.0,
                mean_input_tokens: 45,
                mean_output_tokens: 8,
                outcome_match_rate: 1.0,
                speedup_vs_slowest: 1.5,
              },
            },
            by_scenario: {
              'scene-route-gate': {
                scenario_id: 'scene-route-gate',
                title: 'Scene Route Gate',
                expected_badge: 'NARRATION TRIGGERED',
                providers: {
                  'openai-decisions': {
                    completed: 1,
                    mean_ms: 40.0,
                    matches_expected: true,
                    outcome_badge: 'NARRATION TRIGGERED',
                    speedup_vs_slowest: 1.5,
                  },
                },
              },
              'beautifier-ranked-1': {
                scenario_id: 'beautifier-ranked-1',
                title: 'Text Beautifier Span Selector',
                expected_badge: 'SPAN: LINE_2',
                providers: {
                  'openai-decisions': {
                    completed: 1,
                    mean_ms: 44.6,
                    matches_expected: true,
                    outcome_badge: 'SPAN: LINE_2',
                    speedup_vs_slowest: 1.5,
                  },
                },
              },
            },
          },
        }),
      });
      return;
    }

    await route.fulfill({ status: 404, body: 'Not found' });
  });

  return {
    evalRequests,
    benchmarkRequests,
    setEvalError(message: string | null): void {
      evalError = message;
    },
  };
}

test.describe('Testlab Decision Lab TS module', () => {
  test('renders catalog, evaluates walkthrough & live scenarios, edits questions, and runs benchmarks', async ({
    canvas,
    page,
  }): Promise<void> => {
    void canvas;
    const mocks = await setupDecisionLabMocks(page);

    await page.goto(`${CANVAS_ORIGIN}/decision-lab`);

    // Initial catalog + walkthrough auto-evaluation
    await expect(page.locator('#providerStatus')).toContainText('OpenAI Decisions · available');
    await expect(page.locator('#scenarioGrid .scenario-card')).toHaveCount(2);
    await expect(page.locator('#activeScenarioTitle')).toHaveText('Scene Route Gate');
    await expect(page.locator('#outcomeContainer')).toContainText('Wake StoryResponderModule');
    await expect(page.locator('#answersContainer .answer-card')).toHaveCount(3);
    await expect(page.locator('#wireRequest')).toContainText('gpt-6-luna');
    await expect(page.locator('#runStatus')).toContainText('Completed (walkthrough');

    // Add a custom question and verify editor updates
    await page.locator('#addQuestionBtn').click();
    await expect(page.locator('#questionList .question-editor')).toHaveCount(4);

    // Run live provider evaluation
    await page.locator('#runLiveBtn').click();
    await expect(page.locator('#evalMeta')).toContainText('LIVE (openai-decisions)');
    await expect(page.locator('#runStatus')).toContainText('Completed (live');
    expect(mocks.evalRequests.at(-1)?.questions).toHaveLength(4);

    // Switch to beautifier scenario with span ranking
    await page.locator('.scenario-card', { hasText: 'Text Beautifier Span Selector' }).click();
    await expect(page.locator('#activeScenarioTitle')).toHaveText('Text Beautifier Span Selector');
    await expect(page.locator('#outcomeContainer mark')).toHaveText('The iron door groaned open.');
    await expect(page.locator('#outcomeContainer')).toContainText('Highest dramatic verb intensity');

    // Scenario selection checkboxes and benchmark run
    await page.locator('#clearAllScenariosBtn').click();
    await expect(page.locator('.bench-scenario-checkbox:checked')).toHaveCount(0);
    await page.locator('#selectAllScenariosBtn').click();
    await expect(page.locator('.bench-scenario-checkbox:checked')).toHaveCount(2);

    await page.locator('#benchRepetitions').fill('2');
    await page.locator('#runBenchmarkBtn').click();

    await expect(page.locator('#benchStatusBanner')).toContainText('COMPLETED (2 / 2 evaluations)');
    await expect(page.locator('#benchResultsContainer')).toContainText('42.3 ms');
    await expect(page.locator('#benchResultsContainer')).toContainText('1.50x faster');
    await expect(page.locator('#benchResultsContainer details summary')).toContainText(
      'Text Beautifier Span Selector',
    );
    expect(mocks.benchmarkRequests).toHaveLength(1);

    // Evaluation error surfaces in runStatus without crashing UI
    mocks.setEvalError('Bad question payload');
    await page.locator('#runWalkthroughBtn').click();
    await expect(page.locator('#runStatus')).toHaveText('Error: Bad question payload');
  });
});
