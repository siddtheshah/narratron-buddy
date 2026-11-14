/** Decision Provider Lab page controller: catalog, scenario editor, evaluation inspector, and multi-provider benchmarks. */

export type DecisionQuestionType = 'predicate' | 'choice' | 'score';

export interface DecisionProviderSpec {
  readonly id: string;
  readonly name: string;
  readonly model: string;
  readonly status: string;
  readonly model_options?: readonly string[];
}

export type DecisionChoiceSpec =
  | string
  | { readonly value: string; readonly label?: string };

export type DecisionLevelSpec =
  | string
  | { readonly label: string; readonly description?: string };

export interface DecisionScenarioQuestion {
  readonly type?: string;
  readonly name?: string;
  readonly instructions?: string;
  readonly choices?: readonly DecisionChoiceSpec[];
  readonly levels?: readonly DecisionLevelSpec[];
}

export interface DecisionScenarioSpec {
  readonly id: string;
  readonly title: string;
  readonly category: string;
  readonly subsystem: string;
  readonly description: string;
  readonly decision_rule: string;
  readonly input_text: string;
  readonly questions: readonly DecisionScenarioQuestion[];
}

export interface DecisionCatalogResponse {
  readonly providers?: readonly DecisionProviderSpec[];
  readonly scenarios?: readonly DecisionScenarioSpec[];
}

export interface EditorQuestion {
  type: DecisionQuestionType;
  name: string;
  instructions: string;
  choices: string[];
  levels: string[];
  level_descriptions: Record<string, string> | null;
}

export interface OutcomePreviewSegment {
  readonly text: string;
  readonly highlight?: boolean;
}

export interface OutcomeCandidateRanking {
  readonly text: string;
  readonly line: number;
  readonly probability: number;
  readonly reason: string;
}

export interface NarratronOutcome {
  readonly status?: string;
  readonly badge?: string;
  readonly headline: string;
  readonly explanation: string;
  readonly trace?: readonly string[];
  readonly preview_segments?: readonly OutcomePreviewSegment[];
  readonly candidate_ranking?: readonly OutcomeCandidateRanking[];
}

export interface PredicateAnswerPayload {
  readonly type: 'predicate';
  readonly name: string;
  readonly probability?: number;
  readonly passed?: boolean;
}

export interface ChoiceAnswerPayload {
  readonly type: 'choice';
  readonly name: string;
  readonly choice: string;
  readonly confidence?: number | null;
  readonly probabilities?: Readonly<Record<string, number>>;
}

export interface ScoreAnswerPayload {
  readonly type: 'score';
  readonly name: string;
  readonly score: number;
  readonly confidence?: number | null;
  readonly probabilities?: Readonly<Record<string, number>>;
}

export type DecisionAnswerPayload =
  | PredicateAnswerPayload
  | ChoiceAnswerPayload
  | ScoreAnswerPayload;

export interface DecisionEvaluateResponse {
  readonly mode: string;
  readonly provider: string;
  readonly model: string;
  readonly latency_ms: number;
  readonly request_wire: unknown;
  readonly answers?: readonly DecisionAnswerPayload[];
  readonly narratron_outcome?: NarratronOutcome | null;
  readonly usage?: unknown;
  readonly detail?: string;
}

export interface BenchmarkProviderStats {
  readonly provider_id?: string;
  readonly total?: number;
  readonly completed?: number;
  readonly failed?: number;
  readonly mean_ms?: number | null;
  readonly p50_ms?: number | null;
  readonly p95_ms?: number | null;
  readonly min_ms?: number | null;
  readonly max_ms?: number | null;
  readonly mean_input_tokens?: number;
  readonly mean_output_tokens?: number;
  readonly outcome_match_rate?: number | null;
  readonly speedup_vs_slowest?: number | null;
}

export interface BenchmarkScenarioCellStats {
  readonly completed?: number;
  readonly mean_ms?: number | null;
  readonly matches_expected?: boolean | null;
  readonly outcome_badge?: string | null;
  readonly speedup_vs_slowest?: number | null;
}

export interface BenchmarkScenarioSummary {
  readonly scenario_id: string;
  readonly title: string;
  readonly expected_badge?: string;
  readonly providers?: Readonly<Record<string, BenchmarkScenarioCellStats>>;
}

export interface BenchmarkItem {
  readonly provider_id: string;
  readonly scenario_id: string;
  readonly status: string;
  readonly error?: string;
  readonly narratron_outcome?: NarratronOutcome | null;
}

export interface BenchmarkRunPayload {
  readonly id: string;
  readonly status: string;
  readonly total?: number;
  readonly provider_ids?: readonly string[];
  readonly items?: readonly BenchmarkItem[];
  readonly summary?: {
    readonly providers?: Readonly<Record<string, BenchmarkProviderStats>>;
    readonly by_scenario?: Readonly<Record<string, BenchmarkScenarioSummary>>;
  };
  readonly detail?: string;
}

export interface DecisionLabState {
  catalog: DecisionCatalogResponse | null;
  activeScenarioId: string | null;
  questions: EditorQuestion[];
  benchmarkPollTimer: number | null;
}

export interface DecisionLabController {
  loadCatalog(): Promise<void>;
  selectScenario(scenarioId: string): Promise<void>;
  evaluateCurrent(mode: 'walkthrough' | 'live'): Promise<void>;
  startBenchmarkRun(): Promise<void>;
  destroy(): void;
}

type ElementAttrs<K extends keyof HTMLElementTagNameMap> = Omit<
  Partial<HTMLElementTagNameMap[K]>,
  'style'
> & {
  readonly style?: string;
};

type ChildItem = Node | string | null | undefined;

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs?: ElementAttrs<K>,
  children: readonly ChildItem[] = [],
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (attrs !== undefined) {
    const { style, ...rest } = attrs;
    if (style !== undefined) {
      node.setAttribute('style', style);
    }
    Object.assign(node, rest);
  }
  for (const child of children) {
    if (child !== null && child !== undefined) {
      node.append(child);
    }
  }
  return node;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function parseQuestionType(rawType: string | undefined): DecisionQuestionType {
  if (rawType === 'choice' || rawType === 'score') {
    return rawType;
  }
  return 'predicate';
}

function normalizeQuestionForEditor(q: DecisionScenarioQuestion): EditorQuestion {
  let levels: string[] = [];
  let levelDescriptions: Record<string, string> | null = null;
  if (Array.isArray(q.levels)) {
    levels = q.levels.map((item: DecisionLevelSpec): string =>
      typeof item === 'object' ? item.label : String(item),
    );
    const descEntries = q.levels.filter(
      (item): item is { readonly label: string; readonly description: string } =>
        typeof item === 'object' && typeof item.description === 'string' && item.description.length > 0,
    );
    if (descEntries.length) {
      levelDescriptions = Object.fromEntries(
        descEntries.map((item): [string, string] => [item.label, item.description]),
      );
    }
  }
  return {
    type: parseQuestionType(q.type),
    name: q.name || 'question_1',
    instructions: q.instructions || '',
    choices: Array.isArray(q.choices)
      ? q.choices.map((item: DecisionChoiceSpec): string =>
          typeof item === 'object' ? item.value : String(item),
        )
      : ['option_a', 'option_b'],
    levels: levels.length ? levels : ['low', 'medium', 'high'],
    level_descriptions: levelDescriptions,
  };
}

function renderSpanRanking(outcome: NarratronOutcome): HTMLDivElement {
  const preview = el('div', { style: 'white-space:pre-wrap;line-height:1.9;margin:12px 0' });
  (outcome.preview_segments || []).forEach((segment: OutcomePreviewSegment): void => {
    preview.append(el(segment.highlight ? 'mark' : 'span', { textContent: segment.text }));
  });
  return el('div', {}, [
    preview,
    el('p', { textContent: outcome.explanation }),
    el('div', { className: 'bench-table-wrap' }, [
      el('table', { className: 'bench-table' }, [
        el('thead', {}, [
          el(
            'tr',
            {},
            ['Candidate', 'Line', 'Probability', 'Selection'].map((label: string) =>
              el('th', { textContent: label }),
            ),
          ),
        ]),
        el(
          'tbody',
          {},
          (outcome.candidate_ranking || []).map((candidate: OutcomeCandidateRanking) =>
            el('tr', {}, [
              el('td', { textContent: candidate.text }),
              el('td', { textContent: String(candidate.line) }),
              el('td', { textContent: `${(candidate.probability * 100).toFixed(1)}%` }),
              el('td', { textContent: candidate.reason }),
            ]),
          ),
        ),
      ]),
    ]),
  ]);
}

/** Wires the Decision Provider Lab DOM controls, scenario editor, evaluator, and benchmarks. */
export function initDecisionLab(): DecisionLabController | null {
  const providerStatus = document.querySelector<HTMLElement>('#providerStatus');
  const liveProviderSelect = document.querySelector<HTMLSelectElement>('#liveProviderSelect');
  const benchProviderList = document.querySelector<HTMLElement>('#benchProviderList');
  const benchScenarioChecks = document.querySelector<HTMLElement>('#benchScenarioChecks');
  const benchStatusBanner = document.querySelector<HTMLElement>('#benchStatusBanner');
  const benchResultsContainer = document.querySelector<HTMLElement>('#benchResultsContainer');
  const benchRepetitionsInput = document.querySelector<HTMLInputElement>('#benchRepetitions');
  const scenarioGrid = document.querySelector<HTMLElement>('#scenarioGrid');
  const activeScenarioTitle = document.querySelector<HTMLElement>('#activeScenarioTitle');
  const activeSubsystem = document.querySelector<HTMLElement>('#activeSubsystem');
  const activeDescription = document.querySelector<HTMLElement>('#activeDescription');
  const activeDecisionRule = document.querySelector<HTMLElement>('#activeDecisionRule');
  const inputTextEl = document.querySelector<HTMLTextAreaElement>('#inputText');
  const questionList = document.querySelector<HTMLElement>('#questionList');
  const runStatusEl = document.querySelector<HTMLElement>('#runStatus');
  const evalMetaEl = document.querySelector<HTMLElement>('#evalMeta');
  const outcomeContainer = document.querySelector<HTMLElement>('#outcomeContainer');
  const answersContainer = document.querySelector<HTMLElement>('#answersContainer');
  const wireRequestEl = document.querySelector<HTMLElement>('#wireRequest');
  const wireResponseEl = document.querySelector<HTMLElement>('#wireResponse');
  const addQuestionBtn = document.querySelector<HTMLButtonElement>('#addQuestionBtn');
  const runWalkthroughBtn = document.querySelector<HTMLButtonElement>('#runWalkthroughBtn');
  const runLiveBtn = document.querySelector<HTMLButtonElement>('#runLiveBtn');
  const runBenchmarkBtn = document.querySelector<HTMLButtonElement>('#runBenchmarkBtn');
  const selectAllScenariosBtn = document.querySelector<HTMLButtonElement>('#selectAllScenariosBtn');
  const clearAllScenariosBtn = document.querySelector<HTMLButtonElement>('#clearAllScenariosBtn');

  if (
    !providerStatus ||
    !liveProviderSelect ||
    !benchProviderList ||
    !benchScenarioChecks ||
    !benchStatusBanner ||
    !benchResultsContainer ||
    !benchRepetitionsInput ||
    !scenarioGrid ||
    !activeScenarioTitle ||
    !activeSubsystem ||
    !activeDescription ||
    !activeDecisionRule ||
    !inputTextEl ||
    !questionList ||
    !runStatusEl ||
    !evalMetaEl ||
    !outcomeContainer ||
    !answersContainer ||
    !wireRequestEl ||
    !wireResponseEl ||
    !addQuestionBtn ||
    !runWalkthroughBtn ||
    !runLiveBtn ||
    !runBenchmarkBtn ||
    !selectAllScenariosBtn ||
    !clearAllScenariosBtn
  ) {
    return null;
  }

  const state: DecisionLabState = {
    catalog: null,
    activeScenarioId: null,
    questions: [],
    benchmarkPollTimer: null,
  };

  const renderProviderBadges = (providers: readonly DecisionProviderSpec[]): void => {
    providerStatus.innerHTML = '';
    liveProviderSelect.innerHTML = '';

    providers.forEach((p: DecisionProviderSpec): void => {
      const badge = el('span', {
        className: `pill ${p.status}`,
        textContent: `${p.name} · ${p.status}`,
      });
      providerStatus.append(badge);

      liveProviderSelect.append(
        el('option', {
          value: p.id,
          textContent: `${p.name} (${p.model})`,
        }),
      );
    });
  };

  const renderBenchmarkConfig = (): void => {
    const providers = state.catalog?.providers || [];
    const scenarios = state.catalog?.scenarios || [];

    benchProviderList.innerHTML = '';
    providers.forEach((p: DecisionProviderSpec): void => {
      const checkbox = el('input', {
        type: 'checkbox',
        className: 'bench-provider-checkbox',
        value: p.id,
        checked:
          p.status === 'available' ||
          p.id === 'openai-decisions' ||
          p.id === 'gemini-2-5-decisions',
      });
      checkbox.dataset.providerId = p.id;

      const modelSelect = el('select', {
        className: 'bench-provider-model',
        style: 'margin-top:8px;font-size:.79rem;padding:6px 8px',
      });
      modelSelect.dataset.providerId = p.id;
      const models =
        Array.isArray(p.model_options) && p.model_options.length ? p.model_options : [p.model];
      models.forEach((m: string): void => {
        modelSelect.append(el('option', { value: m, textContent: m, selected: m === p.model }));
      });

      const card = el('div', { className: 'bench-provider-card' }, [
        el('label', { className: 'check-row' }, [
          el('span', { style: 'display:flex;align-items:center;gap:7px' }, [
            checkbox,
            el('span', { textContent: p.name }),
          ]),
          el('span', { className: `pill ${p.status}`, textContent: p.status }),
        ]),
        el('div', { className: 'code-tag', style: 'margin-top:4px', textContent: p.id }),
        modelSelect,
      ]);
      benchProviderList.append(card);
    });

    benchScenarioChecks.innerHTML = '';
    scenarios.forEach((sc: DecisionScenarioSpec): void => {
      const cb = el('input', {
        type: 'checkbox',
        className: 'bench-scenario-checkbox',
        value: sc.id,
        checked: true,
      });
      benchScenarioChecks.append(
        el('label', { className: 'bench-scenario-item' }, [
          cb,
          el('span', { textContent: sc.title }),
        ]),
      );
    });
  };

  const renderBenchmarkRun = (run: BenchmarkRunPayload): void => {
    const completedCount = (run.items || []).length;
    const totalCount = run.total || 0;
    benchStatusBanner.textContent = `Benchmark Run ${run.id.slice(0, 8)} · Status: ${run.status.toUpperCase()} (${completedCount} / ${totalCount} evaluations)`;

    const summary = run.summary || {};
    const providerStatsMap: Readonly<Record<string, BenchmarkProviderStats>> =
      summary.providers || {};
    const byScenario: Readonly<Record<string, BenchmarkScenarioSummary>> =
      summary.by_scenario || {};
    const providerCatalogById: Record<string, DecisionProviderSpec> = Object.fromEntries(
      (state.catalog?.providers || []).map((p: DecisionProviderSpec): [string, DecisionProviderSpec] => [
        p.id,
        p,
      ]),
    );

    const providerIds = run.provider_ids || Object.keys(providerStatsMap);
    const maxMean = Math.max(
      1,
      ...providerIds.map((pid: string): number => Number(providerStatsMap[pid]?.mean_ms || 0)),
    );

    const summaryRows = providerIds.map((pid: string) => {
      const st = providerStatsMap[pid] || {};
      const spec = providerCatalogById[pid];
      const meanMs = st.mean_ms != null ? Number(st.mean_ms) : null;
      const barPct = meanMs != null ? Math.max(4, Math.round((meanMs / maxMean) * 100)) : 0;
      const speedup = st.speedup_vs_slowest != null ? Number(st.speedup_vs_slowest) : null;
      const matchPct =
        st.outcome_match_rate != null ? `${Math.round(Number(st.outcome_match_rate) * 100)}%` : '—';

      return el('tr', {}, [
        el('td', {}, [
          el('div', { style: 'font-weight:600', textContent: spec?.name || pid }),
          el('div', { className: 'code-tag', textContent: pid }),
        ]),
        el('td', {
          className: 'code-tag',
          textContent: `${st.completed || 0}/${st.total || 0}${st.failed ? ` (${st.failed} err)` : ''}`,
        }),
        el('td', { className: 'latency-cell' }, [
          el('div', {
            className: 'latency-val',
            textContent: meanMs != null ? `${meanMs.toFixed(1)} ms` : '—',
          }),
          el('div', { className: 'bar-track', style: 'margin-top:4px;height:7px' }, [
            el('div', {
              className: `bar-fill ${speedup && speedup > 1.2 ? 'good' : ''}`,
              style: `width:${barPct}%`,
            }),
          ]),
        ]),
        el('td', {
          className: 'code-tag',
          textContent: st.p50_ms != null ? `${Number(st.p50_ms).toFixed(1)} ms` : '—',
        }),
        el('td', {
          className: 'code-tag',
          textContent: st.p95_ms != null ? `${Number(st.p95_ms).toFixed(1)} ms` : '—',
        }),
        el('td', {
          className: 'code-tag',
          textContent:
            st.min_ms != null && st.max_ms != null
              ? `${Number(st.min_ms).toFixed(0)} / ${Number(st.max_ms).toFixed(0)} ms`
              : '—',
        }),
        el('td', {
          className: 'code-tag',
          textContent: `${st.mean_input_tokens ?? 0} in / ${st.mean_output_tokens ?? 0} out`,
        }),
        el('td', {}, [el('span', { className: 'pill available', textContent: matchPct })]),
        el('td', {}, [
          speedup != null
            ? el('span', {
                className: `pill ${speedup > 1.05 ? 'speedup' : ''}`,
                textContent: speedup > 1.01 ? `${speedup.toFixed(2)}x faster` : '1.00x (baseline)',
              })
            : el('span', { className: 'code-tag', textContent: '—' }),
        ]),
      ]);
    });

    const summaryTable = el('div', { className: 'bench-table-wrap' }, [
      el('table', { className: 'bench-table' }, [
        el('thead', {}, [
          el('tr', {}, [
            el('th', { textContent: 'Provider' }),
            el('th', { textContent: 'Runs' }),
            el('th', { textContent: 'Mean Latency' }),
            el('th', { textContent: 'p50' }),
            el('th', { textContent: 'p95' }),
            el('th', { textContent: 'Min / Max' }),
            el('th', { textContent: 'Avg Tokens' }),
            el('th', { textContent: 'Route Agreement' }),
            el('th', { textContent: 'Speedup vs Slowest' }),
          ]),
        ]),
        el('tbody', {}, summaryRows),
      ]),
    ]);

    const scenarioHeaderCells = [
      el('th', { textContent: 'Scenario' }),
      el('th', { textContent: 'Expected Route' }),
      ...providerIds.map((pid: string) =>
        el('th', { textContent: providerCatalogById[pid]?.name || pid }),
      ),
    ];

    const scenarioRows = Object.values(byScenario).map((scEntry: BenchmarkScenarioSummary) => {
      const provCells = providerIds.map((pid: string) => {
        const cell = (scEntry.providers || {})[pid] || {};
        if (!cell.completed) {
          const failedItem = (run.items || []).find(
            (it: BenchmarkItem): boolean =>
              it.scenario_id === scEntry.scenario_id && it.provider_id === pid && Boolean(it.error),
          );
          return el('td', {
            className: 'code-tag',
            style: 'color:var(--bad)',
            title: failedItem ? String(failedItem.error) : '',
            textContent: failedItem ? `Error: ${String(failedItem.error).slice(0, 48)}` : '—',
          });
        }
        const sp =
          cell.speedup_vs_slowest != null && Number(cell.speedup_vs_slowest) > 1.05
            ? ` (${Number(cell.speedup_vs_slowest).toFixed(2)}x)`
            : '';
        const matchIcon = cell.matches_expected ? '✓' : '≠';
        return el('td', {}, [
          el('div', {
            className: 'latency-val',
            textContent: `${Number(cell.mean_ms).toFixed(1)} ms${sp}`,
          }),
          el('div', {
            className: 'code-tag',
            style: `margin-top:3px;color:${cell.matches_expected ? 'var(--good)' : 'var(--warn)'}`,
            textContent: `${matchIcon} ${cell.outcome_badge || ''}`,
          }),
        ]);
      });

      return el('tr', {}, [
        el('td', { style: 'font-weight:500', textContent: scEntry.title }),
        el('td', {}, [el('span', { className: 'pill', textContent: scEntry.expected_badge || '' })]),
        ...provCells,
      ]);
    });

    const scenarioTable = el('div', { className: 'bench-table-wrap', style: 'margin-top:16px' }, [
      el('table', { className: 'bench-table' }, [
        el('thead', {}, [el('tr', {}, scenarioHeaderCells)]),
        el('tbody', {}, scenarioRows),
      ]),
    ]);

    benchResultsContainer.innerHTML = '';
    benchResultsContainer.append(summaryTable, scenarioTable);
    providerIds.forEach((pid: string): void => {
      Object.values(byScenario).forEach((scEntry: BenchmarkScenarioSummary): void => {
        const item = [...(run.items || [])].reverse().find(
          (it: BenchmarkItem): boolean =>
            it.provider_id === pid &&
            it.scenario_id === scEntry.scenario_id &&
            it.status === 'completed' &&
            Boolean(it.narratron_outcome?.candidate_ranking),
        );
        if (item && item.narratron_outcome) {
          benchResultsContainer.append(
            el('details', { style: 'margin-top:16px' }, [
              el('summary', {
                textContent: `${providerCatalogById[pid]?.name || pid} · ${scEntry.title} · latest result`,
              }),
              renderSpanRanking(item.narratron_outcome),
            ]),
          );
        }
      });
    });
  };

  const pollBenchmarkRun = async (runId: string): Promise<void> => {
    try {
      const res = await fetch(`/api/decision-lab/benchmark/runs/${runId}`);
      const run = (await res.json()) as BenchmarkRunPayload;
      if (!res.ok) {
        throw new Error(run.detail || 'Failed to poll benchmark run.');
      }
      renderBenchmarkRun(run);
      if (run.status === 'completed') {
        runBenchmarkBtn.disabled = false;
        return;
      }
      state.benchmarkPollTimer = window.setTimeout((): void => {
        void pollBenchmarkRun(runId);
      }, 450);
    } catch (err: unknown) {
      benchStatusBanner.textContent = `Polling error: ${errorMessage(err)}`;
      runBenchmarkBtn.disabled = false;
    }
  };

  const startBenchmarkRun = async (): Promise<void> => {
    if (state.benchmarkPollTimer !== null) {
      window.clearTimeout(state.benchmarkPollTimer);
      state.benchmarkPollTimer = null;
    }

    const providerIds = Array.from(
      document.querySelectorAll<HTMLInputElement>('.bench-provider-checkbox:checked'),
    ).map((cb: HTMLInputElement): string => cb.value);
    const scenarioIds = Array.from(
      document.querySelectorAll<HTMLInputElement>('.bench-scenario-checkbox:checked'),
    ).map((cb: HTMLInputElement): string => cb.value);
    const repetitions = Math.max(
      1,
      Math.min(20, Number(benchRepetitionsInput.value) || 3),
    );

    const providerOptions: Record<string, { readonly model: string }> = {};
    document.querySelectorAll<HTMLSelectElement>('.bench-provider-model').forEach((sel): void => {
      const pid = sel.dataset.providerId;
      if (pid && sel.value) {
        providerOptions[pid] = { model: sel.value };
      }
    });

    runBenchmarkBtn.disabled = true;
    benchStatusBanner.textContent = 'Starting multi-provider benchmark run…';

    try {
      const res = await fetch('/api/decision-lab/benchmark/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider_ids: providerIds,
          scenario_ids: scenarioIds,
          repetitions,
          provider_options: providerOptions,
        }),
      });
      const run = (await res.json()) as BenchmarkRunPayload;
      if (!res.ok) {
        throw new Error(run.detail || 'Failed to start benchmark run.');
      }
      renderBenchmarkRun(run);
      await pollBenchmarkRun(run.id);
    } catch (err: unknown) {
      benchStatusBanner.textContent = `Benchmark Error: ${errorMessage(err)}`;
      runBenchmarkBtn.disabled = false;
    }
  };

  const renderEvaluation = (data: DecisionEvaluateResponse): void => {
    evalMetaEl.textContent = `${data.mode === 'live' ? `LIVE (${data.provider})` : 'WALKTHROUGH'} · ${data.model} · ${data.latency_ms} ms`;

    outcomeContainer.innerHTML = '';
    const outcome = data.narratron_outcome;
    if (outcome) {
      const traceNodes = (outcome.trace || []).map((t: string) =>
        el('span', { className: 'trace-item', textContent: t }),
      );
      outcomeContainer.append(
        el('div', { className: `outcome-banner ${outcome.status || 'action'}` }, [
          el('span', { className: 'pill', textContent: outcome.badge || 'DECISION' }),
          el('h3', { textContent: outcome.headline }),
          el('p', { textContent: outcome.explanation }),
          el('div', { className: 'trace-list' }, traceNodes),
        ]),
      );
    }

    if (outcome?.candidate_ranking) {
      outcomeContainer.append(renderSpanRanking(outcome));
    }

    answersContainer.innerHTML = '';
    (data.answers || []).forEach((ans: DecisionAnswerPayload): void => {
      const cardChildren: HTMLElement[] = [];
      if (ans.type === 'predicate') {
        const pct = Math.round((ans.probability || 0) * 100);
        cardChildren.push(
          el('div', { className: 'answer-header' }, [
            el('span', { className: 'answer-name', textContent: `${ans.name} (predicate)` }),
            el('span', {
              className: `pill ${ans.passed ? 'available' : 'unconfigured'}`,
              textContent: `${ans.passed ? 'TRUE' : 'FALSE'} · ${pct}%`,
            }),
          ]),
          el('div', { className: 'prob-row' }, [
            el('span', { className: 'prob-label', textContent: 'Condition probability' }),
            el('div', { className: 'bar-track' }, [
              el('div', { className: `bar-fill ${ans.passed ? 'good' : ''}`, style: `width:${pct}%` }),
            ]),
            el('span', { className: 'code-tag', textContent: `${pct}%` }),
          ]),
        );
      } else if (ans.type === 'choice') {
        const confText = ans.confidence != null ? ` · conf ${Math.round(ans.confidence * 100)}%` : '';
        cardChildren.push(
          el('div', { className: 'answer-header' }, [
            el('span', { className: 'answer-name', textContent: `${ans.name} (choice)` }),
            el('span', { className: 'pill available', textContent: `Selected: ${ans.choice}${confText}` }),
          ]),
        );
        const probs = ans.probabilities || {};
        Object.entries(probs).forEach(([opt, p]: [string, number]): void => {
          const pct = Math.round((p || 0) * 100);
          const isWinner = opt === ans.choice;
          cardChildren.push(
            el('div', { className: 'prob-row' }, [
              el('span', {
                className: `prob-label ${isWinner ? 'winner' : ''}`,
                textContent: isWinner ? `★ ${opt}` : opt,
              }),
              el('div', { className: 'bar-track' }, [
                el('div', { className: `bar-fill ${isWinner ? 'good' : ''}`, style: `width:${pct}%` }),
              ]),
              el('span', { className: 'code-tag', textContent: `${pct}%` }),
            ]),
          );
        });
      } else if (ans.type === 'score') {
        const levels = Object.keys(ans.probabilities || {});
        const maxIndex = Math.max(1, levels.length - 1);
        const confText = ans.confidence != null ? ` · conf ${Math.round(ans.confidence * 100)}%` : '';
        cardChildren.push(
          el('div', { className: 'answer-header' }, [
            el('span', { className: 'answer-name', textContent: `${ans.name} (score)` }),
            el('span', {
              className: 'pill',
              textContent: `Weighted Score: ${Number(ans.score).toFixed(2)} / ${maxIndex}.00${confText}`,
            }),
          ]),
        );
        Object.entries(ans.probabilities || {}).forEach(([lvl, p]: [string, number], idx: number): void => {
          const pct = Math.round((p || 0) * 100);
          cardChildren.push(
            el('div', { className: 'prob-row' }, [
              el('span', { className: 'prob-label', textContent: `[${idx}] ${lvl}` }),
              el('div', { className: 'bar-track' }, [
                el('div', { className: 'bar-fill cyan', style: `width:${pct}%` }),
              ]),
              el('span', { className: 'code-tag', textContent: `${pct}%` }),
            ]),
          );
        });
      }
      answersContainer.append(el('div', { className: 'answer-card' }, cardChildren));
    });

    wireRequestEl.textContent = JSON.stringify(data.request_wire, null, 2);
    wireResponseEl.textContent = JSON.stringify(
      {
        provider: data.provider,
        model: data.model,
        latency_ms: data.latency_ms,
        answers: data.answers,
        usage: data.usage,
      },
      null,
      2,
    );
  };

  const evaluateCurrent = async (mode: 'walkthrough' | 'live'): Promise<void> => {
    const selectedProviderId = liveProviderSelect.value || 'openai-decisions';
    runStatusEl.textContent =
      mode === 'live' ? `Calling ${selectedProviderId}…` : 'Evaluating walkthrough…';
    const payload = {
      provider_id: selectedProviderId,
      mode,
      scenario_id: state.activeScenarioId,
      input_text: inputTextEl.value,
      questions: state.questions.map((q: EditorQuestion) => ({
        type: q.type,
        name: q.name,
        instructions: q.instructions,
        choices: q.type === 'choice' ? q.choices : [],
        levels: q.type === 'score' ? q.levels : [],
        level_descriptions: q.type === 'score' ? q.level_descriptions : null,
      })),
    };

    try {
      const res = await fetch('/api/decision-lab/evaluate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = (await res.json()) as DecisionEvaluateResponse;
      if (!res.ok) {
        throw new Error(data.detail || 'Evaluation failed.');
      }
      renderEvaluation(data);
      runStatusEl.textContent = `Completed (${data.mode} · ${data.latency_ms} ms)`;
    } catch (err: unknown) {
      runStatusEl.textContent = `Error: ${errorMessage(err)}`;
    }
  };

  const renderQuestionEditors = (): void => {
    questionList.innerHTML = '';
    state.questions.forEach((q: EditorQuestion, idx: number): void => {
      const typeSelect = el('select', {}, [
        el('option', { value: 'predicate', textContent: 'predicate (0..1)', selected: q.type === 'predicate' }),
        el('option', { value: 'choice', textContent: 'choice (options)', selected: q.type === 'choice' }),
        el('option', { value: 'score', textContent: 'score (rubric)', selected: q.type === 'score' }),
      ]);
      typeSelect.addEventListener('change', (): void => {
        q.type = parseQuestionType(typeSelect.value);
        renderQuestionEditors();
      });

      const nameInput = el('input', {
        type: 'text',
        value: q.name,
        placeholder: 'question_name',
      });
      nameInput.addEventListener('input', (): void => {
        q.name = nameInput.value;
      });

      const removeBtn = el('button', {
        type: 'button',
        className: 'btn danger small',
        textContent: 'Remove',
        disabled: state.questions.length <= 1,
        onclick: (): void => {
          state.questions.splice(idx, 1);
          renderQuestionEditors();
        },
      });

      const instrInput = el('input', {
        type: 'text',
        value: q.instructions,
        placeholder: 'Instructions for this question...',
      });
      instrInput.addEventListener('input', (): void => {
        q.instructions = instrInput.value;
      });

      const children: HTMLElement[] = [
        el('div', { className: 'question-editor-row' }, [typeSelect, nameInput, removeBtn]),
        instrInput,
      ];

      if (q.type === 'choice') {
        const choicesInput = el('input', {
          type: 'text',
          style: 'margin-top:7px;font-family:"Fira Code",monospace;font-size:.79rem',
          value: (q.choices || []).join(', '),
          placeholder: 'Comma-separated choices (e.g. triframe, layered, video)',
        });
        choicesInput.addEventListener('input', (): void => {
          q.choices = choicesInput.value
            .split(',')
            .map((s: string): string => s.trim())
            .filter(Boolean);
        });
        children.push(choicesInput);
      } else if (q.type === 'score') {
        const levelsInput = el('input', {
          type: 'text',
          style: 'margin-top:7px;font-family:"Fira Code",monospace;font-size:.79rem',
          value: (q.levels || []).join(', '),
          placeholder: 'Ordered rubric levels (e.g. weak, moderate, strong)',
        });
        levelsInput.addEventListener('input', (): void => {
          q.levels = levelsInput.value
            .split(',')
            .map((s: string): string => s.trim())
            .filter(Boolean);
          q.level_descriptions = null;
        });
        children.push(levelsInput);
      }

      questionList.append(el('div', { className: 'question-editor' }, children));
    });
  };

  let selectScenario: (scenarioId: string) => Promise<void>;

  const renderScenarioGrid = (): void => {
    scenarioGrid.innerHTML = '';
    (state.catalog?.scenarios || []).forEach((sc: DecisionScenarioSpec): void => {
      const card = el(
        'button',
        {
          type: 'button',
          className: `scenario-card ${sc.id === state.activeScenarioId ? 'active' : ''}`,
          onclick: (): void => {
            void selectScenario(sc.id);
          },
        },
        [
          el('div', { className: 'scenario-meta' }, [
            el('span', { className: 'pill', textContent: sc.category }),
            el('span', { className: 'code-tag', textContent: `${sc.questions.length} questions` }),
          ]),
          el('h3', { textContent: sc.title }),
          el('p', { textContent: sc.description }),
          el('div', { className: 'code-tag', style: 'margin-top:8px', textContent: sc.subsystem }),
        ],
      );
      scenarioGrid.append(card);
    });
  };

  selectScenario = async (scenarioId: string): Promise<void> => {
    const sc = (state.catalog?.scenarios || []).find(
      (item: DecisionScenarioSpec): boolean => item.id === scenarioId,
    );
    if (!sc) {
      return;
    }
    state.activeScenarioId = sc.id;
    renderScenarioGrid();
    activeScenarioTitle.textContent = sc.title;
    activeSubsystem.textContent = sc.subsystem;
    activeDescription.textContent = sc.description;
    activeDecisionRule.textContent = sc.decision_rule;
    inputTextEl.value = sc.input_text;
    state.questions = sc.questions.map(normalizeQuestionForEditor);
    renderQuestionEditors();
    await evaluateCurrent('walkthrough');
  };

  const onAddQuestion = (): void => {
    state.questions.push({
      type: 'predicate',
      name: `custom_check_${state.questions.length + 1}`,
      instructions: 'Does this condition hold for the input?',
      choices: ['option_a', 'option_b'],
      levels: ['low', 'medium', 'high'],
      level_descriptions: null,
    });
    renderQuestionEditors();
  };
  const onRunWalkthrough = (): void => {
    void evaluateCurrent('walkthrough');
  };
  const onRunLive = (): void => {
    void evaluateCurrent('live');
  };
  const onRunBenchmark = (): void => {
    void startBenchmarkRun();
  };
  const onSelectAllScenarios = (): void => {
    document.querySelectorAll<HTMLInputElement>('.bench-scenario-checkbox').forEach((cb): void => {
      cb.checked = true;
    });
  };
  const onClearAllScenarios = (): void => {
    document.querySelectorAll<HTMLInputElement>('.bench-scenario-checkbox').forEach((cb): void => {
      cb.checked = false;
    });
  };

  addQuestionBtn.addEventListener('click', onAddQuestion);
  runWalkthroughBtn.addEventListener('click', onRunWalkthrough);
  runLiveBtn.addEventListener('click', onRunLive);
  runBenchmarkBtn.addEventListener('click', onRunBenchmark);
  selectAllScenariosBtn.addEventListener('click', onSelectAllScenarios);
  clearAllScenariosBtn.addEventListener('click', onClearAllScenarios);

  const loadCatalog = async (): Promise<void> => {
    const res = await fetch('/api/decision-lab/catalog');
    state.catalog = (await res.json()) as DecisionCatalogResponse;
    renderProviderBadges(state.catalog.providers || []);
    renderBenchmarkConfig();
    renderScenarioGrid();
    if (state.catalog.scenarios?.length) {
      await selectScenario(state.catalog.scenarios[0].id);
    }
  };

  void loadCatalog();

  return {
    loadCatalog,
    selectScenario,
    evaluateCurrent,
    startBenchmarkRun,
    destroy(): void {
      if (state.benchmarkPollTimer !== null) {
        window.clearTimeout(state.benchmarkPollTimer);
        state.benchmarkPollTimer = null;
      }
      addQuestionBtn.removeEventListener('click', onAddQuestion);
      runWalkthroughBtn.removeEventListener('click', onRunWalkthrough);
      runLiveBtn.removeEventListener('click', onRunLive);
      runBenchmarkBtn.removeEventListener('click', onRunBenchmark);
      selectAllScenariosBtn.removeEventListener('click', onSelectAllScenarios);
      clearAllScenariosBtn.removeEventListener('click', onClearAllScenarios);
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', (): void => {
    initDecisionLab();
  });
} else {
  initDecisionLab();
}
