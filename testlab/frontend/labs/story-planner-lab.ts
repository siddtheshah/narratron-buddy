/** Story Planner Lab page controller: isolated session creation, action submission, and callback polling. */

export interface StoryPlannerAcknowledgement {
  readonly status?: string;
  readonly message?: string;
}

export interface StoryPlannerStatePayload {
  readonly characters?: readonly unknown[];
  readonly notepad?: Readonly<Record<string, unknown>>;
  readonly last_scene_reaction?: Readonly<Record<string, unknown>>;
}

export interface StoryPlannerSessionPayload {
  readonly id: string;
  readonly created_at?: number;
  readonly events?: readonly unknown[];
  readonly state?: StoryPlannerStatePayload;
  readonly detail?: string;
}

export interface StoryPlannerActionResponse {
  readonly acknowledgement?: StoryPlannerAcknowledgement;
  readonly session?: StoryPlannerSessionPayload;
  readonly detail?: string;
}

export interface StoryPlannerLabController {
  refresh(): Promise<void>;
  destroy(): void;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function getDetail(payload: { readonly detail?: string }, fallback: string): string {
  return typeof payload.detail === 'string' && payload.detail.length > 0 ? payload.detail : fallback;
}

/** Initializes the Story Planner Lab DOM bindings and polling lifecycle. */
export function initStoryPlannerLab(): StoryPlannerLabController | null {
  const modelInput = document.getElementById('model');
  const nodesInput = document.getElementById('nodes');
  const newSessionBtn = document.getElementById('new-session');
  const actionInput = document.getElementById('action');
  const nudgeInput = document.getElementById('nudge');
  const submitBtn = document.getElementById('submit');
  const ackEl = document.getElementById('ack');
  const charactersEl = document.getElementById('characters');
  const notepadEl = document.getElementById('notepad');
  const eventsEl = document.getElementById('events');

  if (
    !(modelInput instanceof HTMLInputElement) ||
    !(nodesInput instanceof HTMLInputElement) ||
    !(newSessionBtn instanceof HTMLButtonElement) ||
    !(actionInput instanceof HTMLTextAreaElement) ||
    !(nudgeInput instanceof HTMLTextAreaElement) ||
    !(submitBtn instanceof HTMLButtonElement) ||
    !(ackEl instanceof HTMLElement) ||
    !(charactersEl instanceof HTMLElement) ||
    !(notepadEl instanceof HTMLElement) ||
    !(eventsEl instanceof HTMLElement)
  ) {
    return null;
  }

  let sessionId: string | null = null;
  let poller: number | null = null;
  let lastEventCount = 0;

  const show = (target: HTMLElement, value: string, empty = '—'): void => {
    target.textContent = value || empty;
    target.classList.toggle('empty', !value);
  };

  const render = (payload: StoryPlannerSessionPayload): void => {
    const state = payload.state ?? {};
    show(charactersEl, JSON.stringify(state.characters ?? [], null, 2));
    show(notepadEl, JSON.stringify(state.notepad ?? {}, null, 2));
    const events = payload.events ?? [];
    if (events.length !== lastEventCount && events.length > 0) {
      ackEl.textContent = 'Planner result received through callback queue.';
    }
    lastEventCount = events.length;
    show(eventsEl, events.length > 0 ? JSON.stringify(events, null, 2) : '');
  };

  const refresh = async (): Promise<void> => {
    if (!sessionId) {
      return;
    }
    const response = await fetch(`/api/story-planner/sessions/${sessionId}`);
    if (response.ok) {
      const payload = (await response.json()) as StoryPlannerSessionPayload;
      render(payload);
    }
  };

  const onNewSessionClick = async (): Promise<void> => {
    newSessionBtn.disabled = true;
    try {
      const response = await fetch('/api/story-planner/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          planner_model: modelInput.value,
          nodes_ahead: Number(nodesInput.value),
        }),
      });
      const payload = (await response.json()) as StoryPlannerSessionPayload;
      if (!response.ok) {
        throw new Error(getDetail(payload, 'Could not create session.'));
      }
      sessionId = payload.id;
      lastEventCount = 0;
      render(payload);
      actionInput.disabled = false;
      nudgeInput.disabled = false;
      submitBtn.disabled = false;
      ackEl.textContent = `Session ${sessionId.slice(0, 8)} is ready.`;
      if (poller !== null) {
        window.clearInterval(poller);
      }
      poller = window.setInterval((): void => {
        void refresh();
      }, 500);
    } catch (error) {
      ackEl.textContent = errorMessage(error);
    } finally {
      newSessionBtn.disabled = false;
    }
  };

  const onSubmitClick = async (): Promise<void> => {
    const action = actionInput.value.trim();
    const nudge = nudgeInput.value.trim();
    if (!action || !sessionId) {
      return;
    }
    submitBtn.disabled = true;
    try {
      const response = await fetch(`/api/story-planner/sessions/${sessionId}/actions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, nudge }),
      });
      const payload = (await response.json()) as StoryPlannerActionResponse;
      if (!response.ok) {
        throw new Error(getDetail(payload, 'Could not submit action.'));
      }
      const ack = payload.acknowledgement;
      ackEl.textContent = ack?.message || ack?.status || '';
      actionInput.value = '';
      nudgeInput.value = '';
      if (payload.session) {
        render(payload.session);
      }
    } catch (error) {
      ackEl.textContent = errorMessage(error);
    } finally {
      submitBtn.disabled = false;
    }
  };

  const handleNewSession = (): void => {
    void onNewSessionClick();
  };
  const handleSubmit = (): void => {
    void onSubmitClick();
  };

  newSessionBtn.addEventListener('click', handleNewSession);
  submitBtn.addEventListener('click', handleSubmit);

  return {
    refresh,
    destroy(): void {
      if (poller !== null) {
        window.clearInterval(poller);
        poller = null;
      }
      newSessionBtn.removeEventListener('click', handleNewSession);
      submitBtn.removeEventListener('click', handleSubmit);
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', (): void => {
    initStoryPlannerLab();
  });
} else {
  initStoryPlannerLab();
}
