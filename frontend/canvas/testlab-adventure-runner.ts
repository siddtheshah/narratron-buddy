/** Adventure Runner Lab page controller: session management, chat turns, tool inspection, lore viewer, and game panel integration. */

import {
  createGameRenderer,
  type GamePayload,
  type GameRenderer,
} from './game-renderer.js';

export interface AdventureListEntry {
  readonly id: string;
  readonly title: string;
  readonly lore_count: number;
}

export interface AdventuresListResponse {
  readonly adventures?: readonly AdventureListEntry[];
}

export interface AdventureStickyNote {
  readonly topic?: string;
  readonly name?: string;
  readonly info?: string;
  readonly content?: string;
}

export interface AdventureCharacter {
  readonly name: string;
  readonly voice_tags?: readonly string[];
  readonly description?: string;
  readonly personality?: string;
  readonly quirk?: string;
}

export interface AdventureDialogueLine {
  readonly speaker?: string;
  readonly text?: string;
  readonly kind?: string;
}

export interface AdventureLoreActivityItem {
  readonly type?: string;
  readonly query?: string;
  readonly document: string;
  readonly summary?: string;
}

export interface AdventureToolCall {
  readonly tool: string;
  readonly args?: Readonly<Record<string, unknown>>;
  readonly result?: unknown;
}

export interface AdventureToolLogEntry {
  readonly tool: string;
  readonly timestamp: number;
  readonly args?: unknown;
  readonly result?: unknown;
}

export interface AdventureTurn {
  readonly turn_index?: number;
  readonly user_message?: string;
  readonly agent_response?: string;
  readonly dialogue?: readonly AdventureDialogueLine[];
  readonly tool_calls?: readonly AdventureToolCall[];
  readonly lore_docs_browsed?: readonly string[];
  readonly lore_activity?: readonly AdventureLoreActivityItem[];
}

export interface AdventureMockCanvasState {
  readonly current_image?: string;
  readonly current_image_prompt?: string;
  readonly current_image_effect?: string;
  readonly current_music?: string;
  readonly music_status?: string;
  readonly current_thought?: string;
  readonly last_interactive_canvas_request?: string;
}

export interface AdventureSessionState {
  readonly turns_count?: number;
  readonly game?: GamePayload | null;
  readonly sticky_notes?: readonly AdventureStickyNote[];
  readonly characters?: readonly AdventureCharacter[];
  readonly lore_documents?: readonly string[];
}

export interface AdventureSessionPayload {
  readonly id: string;
  readonly adventure_id?: string;
  readonly state?: AdventureSessionState;
  readonly mock_canvas?: AdventureMockCanvasState;
  readonly history?: readonly AdventureTurn[];
  readonly tool_logs?: readonly AdventureToolLogEntry[];
}

export interface AdventureMessageResponse {
  readonly turn: AdventureTurn;
  readonly state: AdventureSessionState;
  readonly mock_canvas?: AdventureMockCanvasState;
}

export interface LoreDocumentResponse {
  readonly content?: string;
}

interface TurnLoreData {
  readonly docs: readonly string[];
  readonly activity: readonly AdventureLoreActivityItem[];
}

export interface AdventureRunnerController {
  loadAdventures(): Promise<void>;
  switchTab(tabId: string, btn?: HTMLElement | null): void;
  setQuickAction(text: string): void;
  destroy(): void;
}

interface AdventureRunnerWindow extends Window {
  switchTab?: (tabId: string, btn?: HTMLElement | null) => void;
  setQuickAction?: (text: string) => void;
  toggleToolArgs?: (turnId: string, toolIdx: number) => void;
  toggleLoreDrawer?: (turnId: string) => void;
  viewLoreDocument?: (docPath: string) => Promise<void>;
  closeLoreModal?: () => void;
  closeLoreModalOnBackdrop?: (event: MouseEvent) => void;
  copyModalLoreContent?: (btn: HTMLButtonElement) => void;
  copyToolArgs?: (turnId: string, toolIdx: number, btn: HTMLButtonElement) => void;
  copyToolResult?: (turnId: string, toolIdx: number, btn: HTMLButtonElement) => void;
  closeToolArgs?: (turnId: string) => void;
  formatJsonHtml?: (obj: unknown) => string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function extractErrorDetail(body: unknown, fallback: string): string {
  if (isRecord(body) && typeof body.detail === 'string' && body.detail.length > 0) {
    return body.detail;
  }
  return fallback;
}

export function escapeHtml(str: string): string {
  if (typeof str !== 'string') {
    return String(str);
  }
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function formatJsonHtml(obj: unknown): string {
  if (obj === undefined || obj === null) {
    return '<span class="json-null">null</span>';
  }
  if (typeof obj === 'string') {
    return `<span class="json-string">${escapeHtml(JSON.stringify(obj))}</span>`;
  }
  if (typeof obj === 'number') {
    return `<span class="json-number">${obj}</span>`;
  }
  if (typeof obj === 'boolean') {
    return `<span class="json-boolean">${obj}</span>`;
  }

  try {
    const jsonStr = JSON.stringify(obj, null, 2);
    if (jsonStr === undefined) {
      return escapeHtml(String(obj));
    }
    return escapeHtml(jsonStr).replace(
      /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
      (match: string): string => {
        let cls = 'json-number';
        if (/^"/.test(match)) {
          cls = /:$/.test(match) ? 'json-key' : 'json-string';
        } else if (/true|false/.test(match)) {
          cls = 'json-boolean';
        } else if (/null/.test(match)) {
          cls = 'json-null';
        }
        return `<span class="${cls}">${match}</span>`;
      },
    );
  } catch {
    return escapeHtml(String(obj));
  }
}

function copyTextWithFeedback(text: string, btn: HTMLButtonElement): void {
  if (!navigator.clipboard) {
    return;
  }
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.innerHTML;
    btn.innerHTML = '✓ Copied!';
    btn.classList.add('copied');
    window.setTimeout(() => {
      btn.innerHTML = orig;
      btn.classList.remove('copied');
    }, 1500);
  }).catch(() => {});
}

function getEl(id: string): HTMLElement | null {
  return document.getElementById(id);
}

/** Initializes the Adventure Runner Lab UI, game renderer, and global onclick handlers. */
export function initAdventureRunner(): AdventureRunnerController | null {
  const advSelectRaw = getEl('adv-select');
  const agentModelRaw = getEl('agent-model');
  const plannerModelRaw = getEl('planner-model');
  const nodesCountRaw = getEl('nodes-count');
  const btnStartRaw = getEl('btn-start');
  const btnResetRaw = getEl('btn-reset');
  const btnSendRaw = getEl('btn-send');
  const userInputRaw = getEl('user-input');
  const chatStream = getEl('chat-stream');
  const gamePreview = getEl('game-preview');
  const gameToggle = getEl('game-toggle');

  if (
    !(advSelectRaw instanceof HTMLSelectElement) ||
    !(agentModelRaw instanceof HTMLSelectElement) ||
    !(plannerModelRaw instanceof HTMLSelectElement) ||
    !(nodesCountRaw instanceof HTMLInputElement) ||
    !(btnStartRaw instanceof HTMLButtonElement) ||
    !(btnResetRaw instanceof HTMLButtonElement) ||
    !(btnSendRaw instanceof HTMLButtonElement) ||
    !(userInputRaw instanceof HTMLTextAreaElement) ||
    !chatStream ||
    !gamePreview ||
    !gameToggle
  ) {
    return null;
  }

  const advSelect = advSelectRaw;
  const agentModel = agentModelRaw;
  const plannerModel = plannerModelRaw;
  const nodesCount = nodesCountRaw;
  const btnStart = btnStartRaw;
  const btnReset = btnResetRaw;
  const btnSend = btnSendRaw;
  const userInput = userInputRaw;

  let currentSessionId: string | null = null;
  let adventures: readonly AdventureListEntry[] = [];
  let lastKnownState: AdventureSessionState | null = null;
  const sessionBrowsedLoreDocs = new Set<string>();
  const turnToolCallsRegistry = new Map<string, readonly AdventureToolCall[]>();
  const turnLoreRegistry = new Map<string, TurnLoreData>();
  let gamePending = false;
  let gameHistoryCount = 0;
  let turnCounter = 0;

  const setStatus = (text: string, type: 'idle' | 'busy' | 'success' = 'idle'): void => {
    const badge = getEl('status-badge');
    const statusText = getEl('status-text');
    if (badge) {
      badge.className = `status-badge ${type}`;
    }
    if (statusText) {
      statusText.textContent = text;
    }
  };

  const appendUserMessage = (text: string): void => {
    const card = document.createElement('div');
    card.className = 'message-card user';
    card.innerHTML = `
      <div class="message-header">Player 👤</div>
      <div class="bubble">${escapeHtml(text)}</div>
    `;
    chatStream.appendChild(card);
    chatStream.scrollTop = chatStream.scrollHeight;
  };

  const appendErrorMessage = (err: string): void => {
    const card = document.createElement('div');
    card.className = 'message-card agent';
    card.innerHTML = `
      <div class="message-header" style="color: #f87171;">⚠️ System Error</div>
      <div class="bubble" style="border-color: #f87171; background: rgba(248, 113, 113, 0.1);">${escapeHtml(err)}</div>
    `;
    chatStream.appendChild(card);
    chatStream.scrollTop = chatStream.scrollHeight;
  };

  const renderLoreTab = (documents: readonly string[] = []): void => {
    const loreCountEl = getEl('lore-count');
    const loreTotalEl = getEl('lore-total-count');
    if (loreCountEl) {
      loreCountEl.textContent = String(documents.length);
    }
    if (loreTotalEl) {
      loreTotalEl.textContent = String(documents.length);
    }
    const listEl = getEl('lore-docs-list');
    if (!listEl) {
      return;
    }

    if (!documents.length) {
      listEl.innerHTML = '<div class="empty-state">No lore documents in this adventure.</div>';
      return;
    }

    listEl.innerHTML = documents
      .map((doc: string): string => {
        const wasBrowsed = sessionBrowsedLoreDocs.has(doc);
        const tagHtml = wasBrowsed
          ? '<span class="lore-tag browsed">⚡ Browsed This Session</span>'
          : '<span class="lore-tag available">📄 Available</span>';

        return `
          <div class="lore-item-card" style="${wasBrowsed ? 'border-color: rgba(74, 222, 128, 0.4);' : ''}">
            <div class="lore-item-left">
              ${tagHtml}
              <span class="lore-doc-name" onclick="viewLoreDocument('${escapeHtml(doc)}')">${escapeHtml(doc)}</span>
            </div>
            <div>
              <button type="button" class="tool-action-btn" onclick="viewLoreDocument('${escapeHtml(doc)}')">👁️ View Text</button>
            </div>
          </div>
        `;
      })
      .join('');
  };

  const renderLoreActivityCardsHtml = (
    activity: readonly AdventureLoreActivityItem[] = [],
    docs: readonly string[] = [],
  ): string => {
    if (!activity.length && !docs.length) {
      return '<div class="tool-json-empty">No lore documents were explicitly browsed on this turn.</div>';
    }

    if (activity.length) {
      return activity
        .map((item: AdventureLoreActivityItem): string => {
          let tagCls = 'read';
          let tagLabel = '📖 Read Lore';
          if (item.type === 'preloaded') {
            tagCls = 'preloaded';
            tagLabel = '⚡ Preloaded Premise';
          } else if (item.type === 'search') {
            tagCls = 'search';
            tagLabel = `🔍 Search: "${escapeHtml(item.query || item.document)}"`;
          } else if (item.type === 'read_dir' || item.type === 'list') {
            tagCls = 'available';
            tagLabel = '📁 Lore Directory';
          }

          const docName = item.document;
          const isViewable = Boolean(docName && docName.endsWith('.txt'));

          return `
            <div class="lore-item-card">
              <div class="lore-item-left">
                <span class="lore-tag ${tagCls}">${tagLabel}</span>
                <span class="lore-doc-name" ${isViewable ? `onclick="viewLoreDocument('${escapeHtml(docName)}')"` : ''}>
                  ${escapeHtml(docName)}
                </span>
                ${item.summary && item.summary !== docName ? `<span style="color: var(--muted); font-size: 0.72rem;">· ${escapeHtml(item.summary)}</span>` : ''}
              </div>
              <div>
                ${isViewable ? `<button type="button" class="tool-action-btn" onclick="viewLoreDocument('${escapeHtml(docName)}')">👁️ View Text</button>` : ''}
              </div>
            </div>
          `;
        })
        .join('');
    }

    return docs
      .map(
        (doc: string): string => `
        <div class="lore-item-card">
          <div class="lore-item-left">
            <span class="lore-tag read">📖 Referenced</span>
            <span class="lore-doc-name" onclick="viewLoreDocument('${escapeHtml(doc)}')">${escapeHtml(doc)}</span>
          </div>
          <div>
            <button type="button" class="tool-action-btn" onclick="viewLoreDocument('${escapeHtml(doc)}')">👁️ View Text</button>
          </div>
        </div>
      `,
      )
      .join('');
  };

  const fetchToolLogs = async (): Promise<void> => {
    if (!currentSessionId) {
      return;
    }
    try {
      const res = await fetch(`/api/adventure-runner/sessions/${currentSessionId}`);
      if (!res.ok) {
        return;
      }
      const data = (await res.json()) as AdventureSessionPayload;
      const logs = data.tool_logs || [];
      const logCountEl = getEl('log-count');
      if (logCountEl) {
        logCountEl.textContent = String(logs.length);
      }
      const logContainer = getEl('tool-logs-list');
      if (!logContainer) {
        return;
      }
      if (logs.length) {
        logContainer.innerHTML = logs
          .slice()
          .reverse()
          .map(
            (entry: AdventureToolLogEntry): string => `
            <div class="log-item">
              <div class="log-head">
                <span>⚡ ${escapeHtml(entry.tool)}</span>
                <span class="log-time">${new Date(entry.timestamp * 1000).toLocaleTimeString()}</span>
              </div>
              <pre class="log-json">ARGS: ${escapeHtml(JSON.stringify(entry.args ?? {}, null, 2))}</pre>
              <pre class="log-json">RESULT: ${escapeHtml(typeof entry.result === 'string' ? entry.result : JSON.stringify(entry.result ?? null, null, 2))}</pre>
            </div>
          `,
          )
          .join('');
      } else {
        logContainer.innerHTML = '<div class="empty-state">No tool activity logged yet.</div>';
      }
    } catch {
      // Ignore background log refresh errors
    }
  };

  let gameRenderer: GameRenderer | null = null;

  const renderState = (
    state: AdventureSessionState,
    canvas: AdventureMockCanvasState = {},
  ): void => {
    lastKnownState = state;
    gameHistoryCount = state.turns_count || 0;
    gameRenderer?.render(state.game ?? null);
    const gameEmptyEl = getEl('game-empty');
    if (gameEmptyEl) {
      gameEmptyEl.hidden = Boolean(state.game);
    }

    const stickies = state.sticky_notes || [];
    const stickiesCountEl = getEl('stickies-count');
    if (stickiesCountEl) {
      stickiesCountEl.textContent = String(stickies.length);
    }
    const stickiesList = getEl('stickies-list');
    if (stickiesList) {
      if (stickies.length) {
        stickiesList.innerHTML = stickies
          .map(
            (note: AdventureStickyNote): string => `
          <div class="sticky-item">
            <div class="sticky-topic">${escapeHtml(note.topic || note.name || 'Untitled Note')}</div>
            <div class="sticky-info">${escapeHtml(note.info || note.content || '')}</div>
          </div>
        `,
          )
          .join('');
      } else {
        stickiesList.innerHTML = '<div class="empty-state">No sticky notes active.</div>';
      }
    }

    const chars = state.characters || [];
    const charsCountEl = getEl('chars-count');
    if (charsCountEl) {
      charsCountEl.textContent = String(chars.length);
    }
    const charsList = getEl('chars-list');
    if (charsList) {
      if (chars.length) {
        charsList.innerHTML = chars
          .map(
            (character: AdventureCharacter): string => `
          <div class="char-item">
            <div class="char-name">
              <span>${escapeHtml(character.name)}</span>
              ${character.voice_tags && character.voice_tags.length ? `<span style="font-size: 0.7rem; color: var(--muted);">[${escapeHtml(character.voice_tags.join(', '))}]</span>` : ''}
            </div>
            <div style="color: var(--muted); margin-top: 0.25rem;">${escapeHtml(character.description || character.personality || '')}</div>
            ${character.quirk ? `<div style="color: var(--pink); font-size: 0.78rem; margin-top: 0.2rem;">✨ Quirk: ${escapeHtml(character.quirk)}</div>` : ''}
          </div>
        `,
          )
          .join('');
      } else {
        charsList.innerHTML = '<div class="empty-state">No active characters.</div>';
      }
    }

    const imgVal = getEl('canvas-img-val');
    if (imgVal) imgVal.textContent = canvas.current_image || 'No image staged';
    const imgSub = getEl('canvas-img-sub');
    if (imgSub) imgSub.textContent = canvas.current_image_prompt || '—';
    const imgEffect = getEl('canvas-img-effect');
    if (imgEffect) imgEffect.textContent = canvas.current_image_effect || 'gleam3';
    const musicVal = getEl('canvas-music-val');
    if (musicVal) musicVal.textContent = canvas.current_music || 'Silent';
    const musicStatus = getEl('canvas-music-status');
    if (musicStatus) musicStatus.textContent = canvas.music_status || 'stopped';
    const thoughtVal = getEl('canvas-thought-val');
    if (thoughtVal) thoughtVal.textContent = canvas.current_thought || 'No thought pinned';
    const a2uiVal = getEl('canvas-a2ui-val');
    if (a2uiVal) a2uiVal.textContent = canvas.last_interactive_canvas_request || 'No UI requests';

    renderLoreTab(state.lore_documents || []);
    void fetchToolLogs();
  };

  const renderSession = (sessionData: AdventureSessionPayload, freshGame = false): void => {
    const state = sessionData.state || {};
    const canvas = sessionData.mock_canvas || {};
    if (freshGame) {
      gameRenderer?.render(null);
    }
    renderState(state, canvas);
    if (freshGame && state.game && gamePreview.hidden) {
      gameToggle.click();
    }
  };

  const appendAgentTurn = (turn: AdventureTurn): void => {
    turnCounter += 1;
    const turnId = `turn_${turn.turn_index || turnCounter}`;
    const card = document.createElement('div');
    card.className = 'message-card agent';
    card.id = `turn-card-${turnId}`;

    const loreBrowsed = turn.lore_docs_browsed || [];
    const loreActivity = turn.lore_activity || [];
    loreBrowsed.forEach((docPath: string): void => {
      sessionBrowsedLoreDocs.add(docPath);
    });

    const chips: string[] = [];
    turnToolCallsRegistry.set(turnId, turn.tool_calls || []);
    turnLoreRegistry.set(turnId, {
      docs: loreBrowsed,
      activity: loreActivity,
    });

    if (turn.tool_calls && turn.tool_calls.length) {
      turn.tool_calls.forEach((tc: AdventureToolCall, idx: number): void => {
        let cls = 'planner';
        let icon = '⚙️';
        if (tc.tool.includes('image')) {
          cls = 'image';
          icon = '🖼️';
        } else if (tc.tool.includes('music')) {
          cls = 'music';
          icon = '🎵';
        } else if (tc.tool.includes('canvas')) {
          cls = 'canvas';
          icon = '🖥️';
        } else if (tc.tool.includes('chat')) {
          cls = 'planner';
          icon = '💬';
        }
        chips.push(
          `<button type="button" class="tool-chip ${cls}" id="tool-chip-${turnId}-${idx}" onclick="toggleToolArgs('${turnId}', ${idx})" title="Click to inspect tool arguments & results">
            <span>${icon}</span>
            <span>${escapeHtml(tc.tool)}</span>
            <span class="tool-chip-badge">args</span>
            <span class="tool-chip-arrow">▾</span>
          </button>`,
        );
      });
    }

    if (loreBrowsed.length > 0) {
      chips.push(
        `<button type="button" class="tool-chip lore" id="tool-chip-${turnId}-lore" onclick="toggleLoreDrawer('${turnId}')" title="Click to inspect lore documents browsed on this turn">
          <span>📚</span>
          <span>${loreBrowsed.length} Lore Doc${loreBrowsed.length === 1 ? '' : 's'}</span>
          <span class="tool-chip-badge">browsed</span>
          <span class="tool-chip-arrow">▾</span>
        </button>`,
      );
    }

    let toolChipsHtml = '';
    if (chips.length) {
      toolChipsHtml =
        `<div class="tools-tray">${chips.join('')}</div>` +
        `<div class="tool-details-container" id="tool-details-${turnId}" style="display: none;"></div>`;
    }

    let dialogueHtml = '';
    if (turn.dialogue && turn.dialogue.length > 0) {
      const lines = turn.dialogue
        .map((entry: AdventureDialogueLine): string => {
          const spk = escapeHtml(entry.speaker || 'NPC');
          const txt = escapeHtml(entry.text || '');
          const isThought = (entry.kind || '').toLowerCase() === 'thought';
          return `<div style="margin-top: 0.35rem; padding: 0.4rem 0.6rem; border-left: 3px solid var(--accent); background: rgba(155, 135, 245, 0.08); border-radius: 4px; font-size: 0.9em;">
            <strong style="color: var(--cyan);">${spk}${isThought ? ' <em>(thought)</em>' : ''}:</strong> "${txt}"
          </div>`;
        })
        .join('');
      dialogueHtml = `<div class="dialogue-block" style="margin-top: 0.5rem;"><div style="font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); margin-bottom: 0.2rem;">💬 Dialogue</div>${lines}</div>`;
    }

    card.innerHTML = `
      <div class="message-header">🤖 Narratron Agent · Turn ${turn.turn_index || turnCounter}</div>
      <div class="bubble">${escapeHtml(turn.agent_response || '(No response text emitted)')}${dialogueHtml}</div>
      ${toolChipsHtml}
    `;
    chatStream.appendChild(card);
    chatStream.scrollTop = chatStream.scrollHeight;

    if (lastKnownState) {
      renderLoreTab(lastKnownState.lore_documents || []);
    }
  };

  gameRenderer = createGameRenderer({
    container: gamePreview,
    toggleButton: gameToggle,
    actionUrl: (): string => `/api/adventure-runner/sessions/${currentSessionId}/game/action`,
    canEdit: (): boolean => Boolean(currentSessionId) && !userInput.disabled,
    stylesheetUrl: '/game-lab-assets/css/game-renderer.css?v=12',
    initiallyOpen: true,
    onPendingChange: (pending: boolean): void => {
      gamePending = pending;
      btnSend.disabled = pending;
      btnReset.disabled = pending;
      btnStart.disabled = pending;
    },
    refresh: async (): Promise<void> => {
      const sessionId = currentSessionId;
      try {
        const res = await fetch(`/api/adventure-runner/sessions/${sessionId}`);
        if (!res.ok) {
          throw new Error('Could not refresh game state.');
        }
        const data = (await res.json()) as AdventureSessionPayload;
        if (currentSessionId !== sessionId) {
          return;
        }
        for (const turn of (data.history || []).slice(gameHistoryCount)) {
          appendUserMessage(turn.user_message || '');
          appendAgentTurn(turn);
        }
        renderSession(data);
      } catch (err: unknown) {
        appendErrorMessage(errorMessage(err));
      }
    },
  });

  const switchTab = (tabId: string, btn?: HTMLElement | null): void => {
    document.querySelectorAll('.tab-btn').forEach((buttonEl: Element): void => {
      buttonEl.classList.remove('active');
    });
    document.querySelectorAll('.tab-content').forEach((pane: Element): void => {
      pane.classList.remove('active');
    });
    if (btn) {
      btn.classList.add('active');
    }
    const targetTab = getEl(tabId);
    if (targetTab) {
      targetTab.classList.add('active');
    }
  };

  const setQuickAction = (text: string): void => {
    userInput.value = text;
    userInput.focus();
  };

  const toggleToolArgs = (turnId: string, toolIdx: number): void => {
    const detailsContainer = getEl(`tool-details-${turnId}`);
    if (!detailsContainer) {
      return;
    }

    const toolCalls = turnToolCallsRegistry.get(turnId) || [];
    const tc = toolCalls[toolIdx];
    if (!tc) {
      return;
    }

    const currentActive = detailsContainer.dataset.activeIdx;
    if (detailsContainer.style.display !== 'none' && currentActive === String(toolIdx)) {
      detailsContainer.style.display = 'none';
      detailsContainer.dataset.activeIdx = '';
      document.querySelectorAll(`#turn-card-${turnId} .tool-chip`).forEach((el: Element): void => {
        el.classList.remove('active');
      });
      return;
    }

    document.querySelectorAll(`#turn-card-${turnId} .tool-chip`).forEach((el: Element): void => {
      el.classList.remove('active');
    });
    const activeChip = getEl(`tool-chip-${turnId}-${toolIdx}`);
    if (activeChip) {
      activeChip.classList.add('active');
    }

    let icon = '⚙️';
    let category = 'Story Planning Tool';
    if (tc.tool.includes('image')) {
      icon = '🖼️';
      category = 'Visual Canvas Tool';
    } else if (tc.tool.includes('music')) {
      icon = '🎵';
      category = 'Audio Tool';
    } else if (tc.tool.includes('canvas')) {
      icon = '🖥️';
      category = 'Interactive UI Tool';
    } else if (tc.tool.includes('chat')) {
      icon = '💬';
      category = 'Thought / Chat Tool';
    }

    const argsEmpty = !tc.args || Object.keys(tc.args).length === 0;
    const argsHtml = argsEmpty
      ? '<div class="tool-json-empty">No arguments passed (empty payload)</div>'
      : `<pre class="tool-json-box"><code>${formatJsonHtml(tc.args)}</code></pre>`;

    const resultHtml =
      tc.result !== undefined && tc.result !== null
        ? typeof tc.result === 'object'
          ? `<pre class="tool-json-box"><code>${formatJsonHtml(tc.result)}</code></pre>`
          : `<pre class="tool-json-box"><code>${escapeHtml(String(tc.result))}</code></pre>`
        : '<div class="tool-json-empty">No return value recorded</div>';

    const loreData = turnLoreRegistry.get(turnId) || { docs: [], activity: [] };
    let loreSubSection = '';
    if (tc.tool === 'process_user_action' && (loreData.docs.length || loreData.activity.length)) {
      loreSubSection = `
        <div>
          <div class="tool-section-head">
            <span>📚 Lore Documents Browsed (Story Planner)</span>
          </div>
          <div class="lore-list">
            ${renderLoreActivityCardsHtml(loreData.activity, loreData.docs)}
          </div>
        </div>
      `;
    }

    detailsContainer.innerHTML = `
      <div class="tool-detail-card">
        <div class="tool-detail-header">
          <div class="tool-detail-title">
            <span>${icon}</span>
            <span>${escapeHtml(tc.tool)}</span>
            <span class="tool-chip-badge">${escapeHtml(category)}</span>
          </div>
          <div class="tool-detail-actions">
            <button type="button" class="tool-action-btn" onclick="copyToolArgs('${turnId}', ${toolIdx}, this)">📋 Copy Args</button>
            <button type="button" class="tool-action-btn" onclick="copyToolResult('${turnId}', ${toolIdx}, this)">📋 Copy Result</button>
            <button type="button" class="tool-close-btn" onclick="closeToolArgs('${turnId}')" title="Close">×</button>
          </div>
        </div>
        <div class="tool-detail-body">
          <div>
            <div class="tool-section-head">
              <span>📦 Arguments (args)</span>
            </div>
            ${argsHtml}
          </div>
          <div>
            <div class="tool-section-head">
              <span>⚡ Return Value (result)</span>
            </div>
            ${resultHtml}
          </div>
          ${loreSubSection}
        </div>
      </div>
    `;

    detailsContainer.style.display = 'block';
    detailsContainer.dataset.activeIdx = String(toolIdx);
  };

  const toggleLoreDrawer = (turnId: string): void => {
    const detailsContainer = getEl(`tool-details-${turnId}`);
    if (!detailsContainer) {
      return;
    }

    const loreData = turnLoreRegistry.get(turnId) || { docs: [], activity: [] };
    const currentActive = detailsContainer.dataset.activeIdx;

    if (detailsContainer.style.display !== 'none' && currentActive === 'lore') {
      detailsContainer.style.display = 'none';
      detailsContainer.dataset.activeIdx = '';
      document.querySelectorAll(`#turn-card-${turnId} .tool-chip`).forEach((el: Element): void => {
        el.classList.remove('active');
      });
      return;
    }

    document.querySelectorAll(`#turn-card-${turnId} .tool-chip`).forEach((el: Element): void => {
      el.classList.remove('active');
    });
    const activeChip = getEl(`tool-chip-${turnId}-lore`);
    if (activeChip) {
      activeChip.classList.add('active');
    }

    const itemsHtml = renderLoreActivityCardsHtml(loreData.activity, loreData.docs);

    detailsContainer.innerHTML = `
      <div class="tool-detail-card" style="border-color: rgba(192, 132, 252, 0.4);">
        <div class="tool-detail-header" style="background: rgba(192, 132, 252, 0.08);">
          <div class="tool-detail-title">
            <span>📚</span>
            <span>Lore Documents Browsed by Story Planner</span>
            <span class="tool-chip-badge" style="background: rgba(192, 132, 252, 0.2); color: #c084fc;">Turn ${turnId.replace('turn_', '')}</span>
          </div>
          <div class="tool-detail-actions">
            <button type="button" class="tool-close-btn" onclick="closeToolArgs('${turnId}')" title="Close">×</button>
          </div>
        </div>
        <div class="tool-detail-body">
          <div style="font-size: 0.74rem; color: var(--muted); margin-bottom: 0.2rem;">
            The Story Planner referenced or searched the following documents while resolving this player action:
          </div>
          <div class="lore-list">
            ${itemsHtml}
          </div>
        </div>
      </div>
    `;

    detailsContainer.style.display = 'block';
    detailsContainer.dataset.activeIdx = 'lore';
  };

  const viewLoreDocument = async (docPath: string): Promise<void> => {
    if (!currentSessionId || !docPath) {
      return;
    }
    const modalTitle = getEl('modal-lore-title');
    const modalContent = getEl('modal-lore-content');
    const loreModal = getEl('lore-modal');
    const modalBadge = getEl('modal-lore-badge');

    if (modalTitle) modalTitle.textContent = docPath;
    if (modalContent) modalContent.textContent = 'Loading document content…';
    if (loreModal) loreModal.style.display = 'flex';

    try {
      const res = await fetch(
        `/api/adventure-runner/sessions/${currentSessionId}/lore/${encodeURIComponent(docPath)}`,
      );
      if (!res.ok) {
        const errBody: unknown = await res.json();
        throw new Error(extractErrorDetail(errBody, 'Could not fetch lore document.'));
      }
      const data = (await res.json()) as LoreDocumentResponse;
      const content = data.content || '';
      if (modalContent) modalContent.textContent = content || '(Empty document)';
      if (modalBadge) modalBadge.textContent = `${content.length} chars`;
    } catch (err: unknown) {
      if (modalContent) modalContent.textContent = `Error loading lore: ${errorMessage(err)}`;
    }
  };

  const closeLoreModal = (): void => {
    const loreModal = getEl('lore-modal');
    if (loreModal) {
      loreModal.style.display = 'none';
    }
  };

  const closeLoreModalOnBackdrop = (event: MouseEvent): void => {
    const loreModal = getEl('lore-modal');
    if (loreModal && event.target === loreModal) {
      closeLoreModal();
    }
  };

  const copyModalLoreContent = (btn: HTMLButtonElement): void => {
    const text = getEl('modal-lore-content')?.textContent || '';
    copyTextWithFeedback(text, btn);
  };

  const closeToolArgs = (turnId: string): void => {
    const detailsContainer = getEl(`tool-details-${turnId}`);
    if (detailsContainer) {
      detailsContainer.style.display = 'none';
      detailsContainer.dataset.activeIdx = '';
    }
    document.querySelectorAll(`#turn-card-${turnId} .tool-chip`).forEach((el: Element): void => {
      el.classList.remove('active');
    });
  };

  const copyToolArgs = (turnId: string, toolIdx: number, btn: HTMLButtonElement): void => {
    const tc = (turnToolCallsRegistry.get(turnId) || [])[toolIdx];
    if (!tc) {
      return;
    }
    copyTextWithFeedback(JSON.stringify(tc.args || {}, null, 2), btn);
  };

  const copyToolResult = (turnId: string, toolIdx: number, btn: HTMLButtonElement): void => {
    const tc = (turnToolCallsRegistry.get(turnId) || [])[toolIdx];
    if (!tc) {
      return;
    }
    const textToCopy =
      typeof tc.result === 'object'
        ? JSON.stringify(tc.result, null, 2)
        : String(tc.result);
    copyTextWithFeedback(textToCopy, btn);
  };

  const loadAdventures = async (): Promise<void> => {
    try {
      const res = await fetch('/api/adventure-runner/adventures');
      if (!res.ok) {
        throw new Error('Failed to load adventures.');
      }
      const data = (await res.json()) as AdventuresListResponse;
      adventures = data.adventures || [];

      advSelect.innerHTML = '';
      if (!adventures.length) {
        advSelect.innerHTML = '<option value="">No adventures found in adventures/</option>';
        return;
      }

      adventures.forEach((adv: AdventureListEntry): void => {
        const opt = document.createElement('option');
        opt.value = adv.id;
        opt.textContent = `${adv.title} (${adv.lore_count} lore docs)`;
        advSelect.appendChild(opt);
      });

      advSelect.selectedIndex = 0;
    } catch (err: unknown) {
      setStatus(`Error: ${errorMessage(err)}`, 'busy');
    }
  };

  const handleStartClick = async (): Promise<void> => {
    const advId = advSelect.value;
    if (!advId) {
      return;
    }

    btnStart.disabled = true;
    setStatus('Initializing text agent session…', 'busy');
    sessionBrowsedLoreDocs.clear();
    turnCounter = 0;
    turnToolCallsRegistry.clear();
    turnLoreRegistry.clear();

    try {
      const res = await fetch('/api/adventure-runner/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          adventure_id: advId,
          agent_model: agentModel.value,
          planner_model: plannerModel.value,
          nodes_ahead: Number(nodesCount.value),
        }),
      });

      if (!res.ok) {
        const errBody: unknown = await res.json();
        throw new Error(extractErrorDetail(errBody, 'Could not start session.'));
      }

      const data = (await res.json()) as AdventureSessionPayload;
      currentSessionId = data.id;

      btnReset.disabled = false;
      userInput.disabled = false;
      btnSend.disabled = false;
      const quickActions = getEl('quick-actions');
      if (quickActions) {
        quickActions.style.display = 'flex';
      }
      setStatus(`Session Active (${advId})`, 'success');

      renderSession(data, true);

      chatStream.innerHTML = `
        <div class="message-card agent">
          <div class="message-header">🤖 Narratron Agent · Initialized</div>
          <div class="bubble">✨ Welcome to <strong>${escapeHtml(advId)}</strong>! The narrative engine and mocked peripherals are ready. Submit your player action below to begin your adventure.</div>
        </div>
      `;
    } catch (err: unknown) {
      const msg = errorMessage(err);
      setStatus(`Start failed: ${msg}`, 'busy');
      window.alert(msg);
    } finally {
      btnStart.disabled = false;
    }
  };

  const handleResetClick = async (): Promise<void> => {
    if (!currentSessionId) {
      return;
    }
    if (!window.confirm('Are you sure you want to reset this adventure session?')) {
      return;
    }

    setStatus('Resetting session…', 'busy');
    sessionBrowsedLoreDocs.clear();
    turnCounter = 0;
    turnToolCallsRegistry.clear();
    turnLoreRegistry.clear();

    try {
      const res = await fetch(`/api/adventure-runner/sessions/${currentSessionId}/reset`, {
        method: 'POST',
      });
      if (!res.ok) {
        throw new Error('Reset failed.');
      }
      const data = (await res.json()) as AdventureSessionPayload;
      renderSession(data, true);
      chatStream.innerHTML = `
        <div class="message-card agent">
          <div class="message-header">🤖 Narratron Agent · Reset</div>
          <div class="bubble">🔄 Session reset to the beginning of the adventure.</div>
        </div>
      `;
      setStatus('Session Reset', 'success');
    } catch (err: unknown) {
      setStatus(`Reset error: ${errorMessage(err)}`, 'busy');
    }
  };

  const handleSendClick = async (): Promise<void> => {
    const text = userInput.value.trim();
    if (!text || !currentSessionId || gamePending) {
      return;
    }

    appendUserMessage(text);
    userInput.value = '';
    userInput.disabled = true;
    btnSend.disabled = true;
    gameRenderer?.render(lastKnownState?.game ?? null);
    setStatus('Agent & Story Planner thinking…', 'busy');

    try {
      const res = await fetch(`/api/adventure-runner/sessions/${currentSessionId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });

      if (!res.ok) {
        const errBody: unknown = await res.json();
        throw new Error(extractErrorDetail(errBody, 'Failed to send message.'));
      }

      const data = (await res.json()) as AdventureMessageResponse;
      appendAgentTurn(data.turn);
      renderState(data.state, data.mock_canvas || {});
      setStatus('Ready for next action', 'success');
    } catch (err: unknown) {
      const msg = errorMessage(err);
      setStatus(`Turn error: ${msg}`, 'busy');
      appendErrorMessage(msg);
    } finally {
      userInput.disabled = false;
      btnSend.disabled = false;
      gameRenderer?.render(lastKnownState?.game ?? null);
      userInput.focus();
    }
  };

  const onStartClick = (): void => {
    void handleStartClick();
  };
  const onResetClick = (): void => {
    void handleResetClick();
  };
  const onSendClick = (): void => {
    void handleSendClick();
  };
  const onUserInputKeydown = (event: KeyboardEvent): void => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      btnSend.click();
    }
  };

  btnStart.addEventListener('click', onStartClick);
  btnReset.addEventListener('click', onResetClick);
  btnSend.addEventListener('click', onSendClick);
  userInput.addEventListener('keydown', onUserInputKeydown);

  const win = window as AdventureRunnerWindow;
  win.switchTab = switchTab;
  win.setQuickAction = setQuickAction;
  win.toggleToolArgs = toggleToolArgs;
  win.toggleLoreDrawer = toggleLoreDrawer;
  win.viewLoreDocument = viewLoreDocument;
  win.closeLoreModal = closeLoreModal;
  win.closeLoreModalOnBackdrop = closeLoreModalOnBackdrop;
  win.copyModalLoreContent = copyModalLoreContent;
  win.copyToolArgs = copyToolArgs;
  win.copyToolResult = copyToolResult;
  win.closeToolArgs = closeToolArgs;
  win.formatJsonHtml = formatJsonHtml;

  void loadAdventures();

  return {
    loadAdventures,
    switchTab,
    setQuickAction,
    destroy(): void {
      btnStart.removeEventListener('click', onStartClick);
      btnReset.removeEventListener('click', onResetClick);
      btnSend.removeEventListener('click', onSendClick);
      userInput.removeEventListener('keydown', onUserInputKeydown);
      gameRenderer?.close();
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', (): void => {
    initAdventureRunner();
  });
} else {
  initAdventureRunner();
}
