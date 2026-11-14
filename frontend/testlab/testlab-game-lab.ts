/** Game Testing Lab page controller: session lifecycle, role simulation, manual AI moves, and scenarios. */

import {
  createGameRenderer,
  type GamePayload,
  type GameRenderer,
} from '../game/game-renderer.js';

export type GameLabRole = 'orator' | 'contributor' | 'viewer';

export interface GameLabTurnResponse {
  readonly response: string;
}

export interface GameLabTurnTrace {
  readonly ai_actions: readonly unknown[];
  readonly responses: readonly GameLabTurnResponse[];
}

export interface GameLabLegalMove {
  readonly source: number | string;
  readonly target: number | string;
}

export interface GameLabStorySnapshot {
  readonly actions?: Readonly<Record<string, unknown>>;
  readonly state?: {
    readonly legal_moves?: readonly GameLabLegalMove[];
    readonly [key: string]: unknown;
  };
  readonly [key: string]: unknown;
}

export interface GameLabSessionSnapshot {
  readonly session_id: string;
  readonly game: GamePayload;
  readonly state: GameLabStorySnapshot;
  readonly turns: readonly GameLabTurnTrace[];
  readonly ai_play?: boolean;
}

export interface GameLabProviderSpec {
  readonly id: string;
  readonly name?: string;
  readonly status: string;
}

export interface GameLabCatalog {
  readonly games: readonly string[];
  readonly providers: readonly GameLabProviderSpec[];
}

export interface GameLabScenarioReport {
  readonly passed: boolean;
  readonly scenario: string;
  readonly [key: string]: unknown;
}

export interface GameLabController {
  loadCatalog(): Promise<void>;
  refresh(): Promise<void>;
  destroy(): void;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function extractErrorDetail(result: unknown): string {
  if (isRecord(result) && 'detail' in result) {
    const detail = result.detail;
    if (typeof detail === 'string') {
      return detail;
    }
    return JSON.stringify(detail ?? result);
  }
  return JSON.stringify(result);
}

function parseRole(rawRole: string | undefined): GameLabRole {
  if (rawRole === 'contributor' || rawRole === 'viewer') {
    return rawRole;
  }
  return 'orator';
}

/** Wires the Game Testing Lab DOM controls, renderer, and API interactions. */
export function initGameLab(): GameLabController | null {
  const rawGamePreview = document.getElementById('game-preview');
  const rawToggle = document.getElementById('toggle');
  const rawLoadForm = document.getElementById('load-form');
  const rawGameInput = document.getElementById('game');
  const rawGamesList = document.getElementById('games');
  const rawProviderSelect = document.getElementById('provider');
  const rawModelInput = document.getElementById('model');
  const rawLoadBtn = document.getElementById('load');
  const rawCloseBtn = document.getElementById('close');
  const rawAiPlay = document.getElementById('ai-play');
  const rawStatusEl = document.getElementById('status');
  const rawRoleHint = document.getElementById('role-hint');
  const rawManualAiBox = document.getElementById('manual-ai-box');
  const rawManualAiActions = document.getElementById('manual-ai-actions');
  const rawPlaceholder = document.getElementById('placeholder');
  const rawStatePre = document.getElementById('state');
  const rawTranscriptPre = document.getElementById('transcript');
  const rawTurnSummary = document.getElementById('turn-summary');
  const rawReplies = document.getElementById('replies');
  const rawDownloadBtn = document.getElementById('download');
  const rawScenarioForm = document.getElementById('scenario-form');
  const rawScenarioTextarea = document.getElementById('scenario');
  const rawRunBtn = document.getElementById('run');
  const rawReportPre = document.getElementById('report');

  if (
    !(rawGamePreview instanceof HTMLElement) ||
    !(rawToggle instanceof HTMLButtonElement) ||
    !(rawLoadForm instanceof HTMLFormElement) ||
    !(rawGameInput instanceof HTMLInputElement) ||
    !(rawGamesList instanceof HTMLDataListElement) ||
    !(rawProviderSelect instanceof HTMLSelectElement) ||
    !(rawModelInput instanceof HTMLInputElement) ||
    !(rawLoadBtn instanceof HTMLButtonElement) ||
    !(rawCloseBtn instanceof HTMLButtonElement) ||
    !(rawAiPlay instanceof HTMLInputElement) ||
    !(rawStatusEl instanceof HTMLElement) ||
    !(rawRoleHint instanceof HTMLElement) ||
    !(rawManualAiBox instanceof HTMLElement) ||
    !(rawManualAiActions instanceof HTMLElement) ||
    !(rawPlaceholder instanceof HTMLElement) ||
    !(rawStatePre instanceof HTMLElement) ||
    !(rawTranscriptPre instanceof HTMLElement) ||
    !(rawTurnSummary instanceof HTMLElement) ||
    !(rawReplies instanceof HTMLElement) ||
    !(rawDownloadBtn instanceof HTMLButtonElement) ||
    !(rawScenarioForm instanceof HTMLFormElement) ||
    !(rawScenarioTextarea instanceof HTMLTextAreaElement) ||
    !(rawRunBtn instanceof HTMLButtonElement) ||
    !(rawReportPre instanceof HTMLElement)
  ) {
    return null;
  }

  const gamePreview = rawGamePreview;
  const toggleBtn = rawToggle;
  const loadForm = rawLoadForm;
  const gameInput = rawGameInput;
  const gamesList = rawGamesList;
  const providerSelect = rawProviderSelect;
  const modelInput = rawModelInput;
  const loadBtn = rawLoadBtn;
  const closeBtn = rawCloseBtn;
  const aiPlayCheckbox = rawAiPlay;
  const statusEl = rawStatusEl;
  const roleHint = rawRoleHint;
  const manualAiBox = rawManualAiBox;
  const manualAiActions = rawManualAiActions;
  const placeholder = rawPlaceholder;
  const statePre = rawStatePre;
  const transcriptPre = rawTranscriptPre;
  const turnSummary = rawTurnSummary;
  const repliesEl = rawReplies;
  const downloadBtn = rawDownloadBtn;
  const scenarioForm = rawScenarioForm;
  const scenarioTextarea = rawScenarioTextarea;
  const runBtn = rawRunBtn;
  const reportPre = rawReportPre;

  let sessionId: string | null = null;
  let snapshot: GameLabSessionSnapshot | null = null;
  let loading = false;
  let currentRole: GameLabRole = 'orator';

  function isAiPlayEnabled(): boolean {
    return aiPlayCheckbox.checked;
  }

  function canEdit(): boolean {
    return Boolean(sessionId) && !loading && currentRole === 'orator';
  }

  function canContribute(): boolean {
    return Boolean(sessionId) && (currentRole === 'orator' || currentRole === 'contributor');
  }

  function setStatus(message: string, failed = false): void {
    statusEl.textContent = message;
    statusEl.className = failed ? 'fail' : '';
  }

  function updateRoleUI(): void {
    const aiText = isAiPlayEnabled() ? 'AI play: ON' : 'AI play: OFF (manual simulation)';
    if (currentRole === 'orator') {
      roleHint.textContent = `Simulating Orator (full control & reset) · ${aiText}`;
    } else if (currentRole === 'contributor') {
      roleHint.textContent = `Simulating Contributor (board actions enabled, orator actions locked) · ${aiText}`;
    } else {
      roleHint.textContent = `Simulating Viewer (read-only spectator) · ${aiText}`;
    }
  }

  async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
    const response = await fetch(url, options);
    const result: unknown = await response.json();
    if (!response.ok) {
      throw new Error(extractErrorDetail(result));
    }
    return result as T;
  }

  async function sendManualAiAction(
    name: string,
    args: Readonly<Record<string, unknown>>,
  ): Promise<void> {
    if (!sessionId || !snapshot || loading) return;
    loading = true;
    setStatus(`Executing AI action ${name}…`);
    try {
      const response = await fetch(
        `/api/game-lab/sessions/${sessionId}/action?ai_play=false&actor=narratron`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name,
            arguments: args,
            revision: snapshot.game.revision,
          }),
        },
      );
      const result: unknown = await response.json();
      if (!response.ok) {
        const message =
          isRecord(result) && typeof result.detail === 'string'
            ? result.detail
            : 'Action rejected.';
        throw new Error(message);
      }
      await refresh();
    } catch (error: unknown) {
      const message = error instanceof Error && error.message ? error.message : 'Could not execute AI action.';
      setStatus(message, true);
    } finally {
      loading = false;
    }
  }

  function renderManualAiActions(data: GameLabSessionSnapshot): void {
    const actions = data.state?.actions ?? {};
    const actionNames = Object.keys(actions);
    if (isAiPlayEnabled() || actionNames.length === 0) {
      manualAiBox.hidden = true;
      manualAiActions.replaceChildren();
      return;
    }
    manualAiBox.hidden = false;
    manualAiActions.replaceChildren();
    const legalMoves = data.state?.state?.legal_moves ?? [];
    if (legalMoves.length > 0 && actions.narratron_move) {
      for (const move of legalMoves) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'manual-ai-btn';
        btn.textContent = `AI move: ${move.source} → ${move.target}`;
        btn.addEventListener('click', () => {
          void sendManualAiAction('narratron_move', {
            source: move.source,
            target: move.target,
          });
        });
        manualAiActions.append(btn);
      }
    } else {
      for (const name of actionNames) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'manual-ai-btn';
        btn.textContent = `Execute ${name}`;
        btn.addEventListener('click', () => {
          void sendManualAiAction(name, {});
        });
        manualAiActions.append(btn);
      }
    }
  }

  function show(data: GameLabSessionSnapshot): void {
    snapshot = data;
    renderer.render(data.game);
    placeholder.hidden = true;
    closeBtn.disabled = false;
    downloadBtn.hidden = false;
    statePre.textContent = JSON.stringify(data.state, null, 2);
    transcriptPre.textContent = JSON.stringify(data.turns, null, 2);
    const aiCount = data.turns.reduce((sum, turn) => sum + turn.ai_actions.length, 0);
    turnSummary.textContent = `Revision ${data.game.revision} · ${data.turns.length} player actions · ${aiCount} AI actions`;
    repliesEl.replaceChildren();
    for (const turn of data.turns) {
      for (const response of turn.responses) {
        const paragraph = document.createElement('p');
        paragraph.textContent = `Narratron: ${response.response}`;
        repliesEl.append(paragraph);
      }
    }
    setStatus(data.game.error || 'Ready for your next move.', Boolean(data.game.error));
    updateRoleUI();
    renderManualAiActions(data);
  }

  async function refresh(): Promise<void> {
    if (!sessionId) return;
    const currentSession = sessionId;
    try {
      const data = await request<GameLabSessionSnapshot>(
        `/api/game-lab/sessions/${currentSession}`,
      );
      if (sessionId === currentSession) {
        show(data);
      }
    } catch (error: unknown) {
      setStatus(error instanceof Error ? error.message : String(error), true);
    }
  }

  const renderer: GameRenderer = createGameRenderer({
    container: gamePreview,
    toggleButton: toggleBtn,
    actionUrl: (): string => {
      const aiParam = isAiPlayEnabled() ? 'true' : 'false';
      const actorParam = currentRole === 'contributor' ? 'contributor' : 'player';
      return `/api/game-lab/sessions/${sessionId}/action?ai_play=${aiParam}&actor=${actorParam}`;
    },
    canEdit,
    canContribute,
    refresh,
    stylesheetUrl: '/game-lab-assets/css/game-renderer.css?v=12',
    initiallyOpen: true,
    onPendingChange: (pending: boolean): void => {
      loadBtn.disabled = pending;
      closeBtn.disabled = pending;
      if (pending) setStatus('Applying move…');
    },
  });

  const roleButtons = Array.from(document.querySelectorAll<HTMLButtonElement>('.role-btn'));
  const roleListeners: Array<{ button: HTMLButtonElement; handler: () => void }> = [];
  for (const btn of roleButtons) {
    const handler = (): void => {
      roleButtons.forEach((other) => other.classList.remove('active'));
      btn.classList.add('active');
      currentRole = parseRole(btn.dataset.role);
      updateRoleUI();
      if (snapshot) renderer.render(snapshot.game);
    };
    btn.addEventListener('click', handler);
    roleListeners.push({ button: btn, handler });
  }

  const onAiPlayChange = async (): Promise<void> => {
    updateRoleUI();
    if (snapshot) renderManualAiActions(snapshot);
    if (sessionId) {
      try {
        await request<GameLabSessionSnapshot>(`/api/game-lab/sessions/${sessionId}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ai_play: isAiPlayEnabled() }),
        });
      } catch (error: unknown) {
        setStatus(error instanceof Error ? error.message : String(error), true);
      }
    }
  };
  const onAiPlayChangeHandler = (): void => {
    void onAiPlayChange();
  };
  aiPlayCheckbox.addEventListener('change', onAiPlayChangeHandler);

  async function loadCatalog(): Promise<void> {
    try {
      const data = await request<GameLabCatalog>('/api/game-lab/catalog');
      for (const path of data.games) {
        const option = document.createElement('option');
        option.value = path;
        gamesList.append(option);
      }
      for (const provider of data.providers) {
        const option = document.createElement('option');
        option.value = provider.id;
        option.textContent = `${provider.name || provider.id} (${provider.status})`;
        providerSelect.append(option);
      }
      providerSelect.value = 'gemini-3';
    } catch (error: unknown) {
      setStatus(error instanceof Error ? error.message : String(error), true);
    }
  }

  const onLoadSubmit = async (event: SubmitEvent): Promise<void> => {
    event.preventDefault();
    loading = true;
    loadBtn.disabled = true;
    if (snapshot) renderer.render(snapshot.game);
    setStatus('Loading game…');
    try {
      const data = await request<GameLabSessionSnapshot>('/api/game-lab/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          game_path: gameInput.value,
          provider_id: providerSelect.value,
          model: modelInput.value || null,
          ai_play: isAiPlayEnabled(),
        }),
      });
      if (sessionId) {
        await request<{ readonly closed: boolean }>(`/api/game-lab/sessions/${sessionId}`, {
          method: 'DELETE',
        });
      }
      sessionId = data.session_id;
      loading = false;
      renderer.render(null);
      show(data);
      if (gamePreview.hidden) toggleBtn.click();
    } catch (error: unknown) {
      setStatus(error instanceof Error ? error.message : String(error), true);
    } finally {
      loading = false;
      loadBtn.disabled = false;
      if (snapshot) renderer.render(snapshot.game);
    }
  };
  const onLoadSubmitHandler = (event: SubmitEvent): void => {
    void onLoadSubmit(event);
  };
  loadForm.addEventListener('submit', onLoadSubmitHandler);

  const onCloseClick = async (): Promise<void> => {
    if (!sessionId) return;
    try {
      await request<{ readonly closed: boolean }>(`/api/game-lab/sessions/${sessionId}`, {
        method: 'DELETE',
      });
      sessionId = null;
      snapshot = null;
      renderer.render(null);
      placeholder.hidden = false;
      closeBtn.disabled = true;
      downloadBtn.hidden = true;
      statePre.textContent = 'Session closed.';
      transcriptPre.textContent = '[]';
      repliesEl.replaceChildren();
      turnSummary.textContent = 'No moves yet.';
      manualAiBox.hidden = true;
      setStatus('Session closed. Load a game to start again.');
    } catch (error: unknown) {
      setStatus(error instanceof Error ? error.message : String(error), true);
    }
  };
  const onCloseClickHandler = (): void => {
    void onCloseClick();
  };
  closeBtn.addEventListener('click', onCloseClickHandler);

  const onScenarioSubmit = async (event: SubmitEvent): Promise<void> => {
    event.preventDefault();
    runBtn.disabled = true;
    setStatus('Running scenario…');
    try {
      const parsedScenario: unknown = JSON.parse(scenarioTextarea.value);
      const result = await request<GameLabScenarioReport>('/api/game-lab/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          game_path: gameInput.value,
          scenario: parsedScenario,
          provider_id: providerSelect.value,
          model: modelInput.value || null,
        }),
      });
      reportPre.textContent = JSON.stringify(result, null, 2);
      reportPre.hidden = false;
      setStatus(`${result.passed ? 'PASS' : 'FAIL'}: ${result.scenario}`, !result.passed);
    } catch (error: unknown) {
      setStatus(error instanceof Error ? error.message : String(error), true);
    } finally {
      runBtn.disabled = false;
    }
  };
  const onScenarioSubmitHandler = (event: SubmitEvent): void => {
    void onScenarioSubmit(event);
  };
  scenarioForm.addEventListener('submit', onScenarioSubmitHandler);

  const onDownloadClick = (): void => {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = 'game-session.json';
    link.click();
    URL.revokeObjectURL(url);
  };
  downloadBtn.addEventListener('click', onDownloadClick);

  void loadCatalog();

  return {
    loadCatalog,
    refresh,
    destroy(): void {
      for (const { button, handler } of roleListeners) {
        button.removeEventListener('click', handler);
      }
      aiPlayCheckbox.removeEventListener('change', onAiPlayChangeHandler);
      loadForm.removeEventListener('submit', onLoadSubmitHandler);
      closeBtn.removeEventListener('click', onCloseClickHandler);
      scenarioForm.removeEventListener('submit', onScenarioSubmitHandler);
      downloadBtn.removeEventListener('click', onDownloadClick);
      renderer.close();
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initGameLab();
  });
} else {
  initGameLab();
}
