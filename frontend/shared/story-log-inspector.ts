/** Render and load the theater-local story log shown from canvas paging controls. */

export interface StoryLogDieRoll {
    notation?: string;
    total?: number | string;
    tier?: string;
    reason?: string;
}

export interface StoryLogDialogueLine {
    speaker?: string;
    text?: string;
}

export interface StoryLogOutput {
    die_rolls?: Array<StoryLogDieRoll | null>;
    narration?: string;
    dialogue?: Array<StoryLogDialogueLine | null>;
}

export interface StoryLogEntry extends StoryLogDieRoll {
    type?: string;
    timestamp?: string | number | null;
    action?: string | null;
    result?: StoryLogDieRoll | null;
    output?: StoryLogOutput | null;
}

export interface StoryLogTurn {
    action: StoryLogEntry | null;
    plan: StoryLogEntry | null;
}

export interface StoryLogInspectorOptions {
    embedded?: boolean;
}

export interface StoryLogInspectorController {
    setOpen(force?: boolean): void;
}

function element<K extends keyof HTMLElementTagNameMap>(
    tag: K,
    className?: string,
    text?: string,
): HTMLElementTagNameMap[K] {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
}

function formatTime(timestamp?: string | number | null): string {
    if (!timestamp) return 'Unknown time';
    const date = new Date(timestamp);
    return Number.isNaN(date.getTime()) ? 'Unknown time' : date.toLocaleString([], {
        month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    });
}

function formatDieRoll(roll: StoryLogDieRoll): string {
    const notation = roll.notation || 'Roll';
    const total = roll.total !== undefined ? roll.total : '';
    const tier = roll.tier ? ` (${roll.tier})` : '';
    const reason = roll.reason ? ` - ${roll.reason}` : '';
    return `🎲 ${notation}: ${total}${tier}${reason}`;
}

function parseStoryLogEntry(line: string): StoryLogEntry {
    const parsed: unknown = JSON.parse(line);
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Invalid story log entry');
    }
    return parsed as StoryLogEntry;
}

function createCard(entry: StoryLogEntry): HTMLElement | null {
    const isAction = entry.type === 'user_action';
    const isDieRoll = entry.type === 'die_roll';
    const output: StoryLogOutput = entry.output || {};
    const card = element('article', `story-log-entry ${isAction ? 'action' : 'plan'}`);
    const label = element('div', 'story-log-label');
    label.append(
        element('span', '', isAction ? 'Player action' : isDieRoll ? 'Die roll' : 'Story plan'),
        element('time', 'story-log-time', formatTime(entry.timestamp)),
    );
    card.appendChild(label);

    if (isAction) {
        card.appendChild(element('div', 'story-log-text', entry.action || 'No action recorded.'));
        return card;
    }

    if (isDieRoll) {
        const roll = entry.result || entry;
        card.appendChild(element('div', 'story-log-text', formatDieRoll(roll)));
        return card;
    }

    const dieRolls = output.die_rolls;
    if (Array.isArray(dieRolls) && dieRolls.length) {
        card.appendChild(element('div', 'story-log-section', 'Die Rolls'));
        dieRolls.forEach((roll) => {
            if (!roll) return;
            card.appendChild(element('div', 'story-log-text', formatDieRoll(roll)));
        });
    }

    if (output.narration) card.appendChild(element('div', 'story-log-text', output.narration));
    if (Array.isArray(output.dialogue) && output.dialogue.length) {
        card.appendChild(element('div', 'story-log-section', 'Dialogue'));
        output.dialogue.forEach((line) => {
            const speaker = line && line.speaker ? `${line.speaker}: ` : '';
            card.appendChild(element('div', 'story-log-text', `${speaker}${line?.text || ''}`));
        });
    }
    return (output.narration || output.dialogue?.length || (Array.isArray(dieRolls) && dieRolls.length)) ? card : null;
}

function groupTurns(entries: StoryLogEntry[]): StoryLogTurn[] {
    const turns: StoryLogTurn[] = [];
    entries.forEach((entry) => {
        if (entry.type === 'user_action') {
            turns.push({ action: entry, plan: null });
            return;
        }
        const turn = [...turns].reverse().find((candidate) => !candidate.plan && candidate.action);
        if (turn) turn.plan = entry;
        else turns.push({ action: null, plan: entry });
    });
    // JSONL is append-only, so its natural order is oldest to newest.  Keep
    // that order in the inspector: reading down should move forward in time.
    return turns;
}

function positionLatestAction(inspector: HTMLElement | null, content: HTMLElement): void {
    if (!inspector) return;
    const actions = content.querySelectorAll<HTMLElement>('.story-log-entry.action');
    const latestAction = actions[actions.length - 1];
    if (!latestAction) return;

    const bottom = Math.max(0, inspector.scrollHeight - inspector.clientHeight);
    inspector.scrollTop = bottom;

    // Prefer the natural "at the bottom" position.  When a long scene
    // response has pushed the latest action out of view, move only far enough
    // to show that action completely (when it fits in the viewport).
    const actionBox = latestAction.getBoundingClientRect();
    const viewportBox = inspector.getBoundingClientRect();
    if (actionBox.top < viewportBox.top || actionBox.bottom > viewportBox.bottom) {
        const target = latestAction.offsetHeight <= inspector.clientHeight
            ? inspector.scrollTop + actionBox.bottom - viewportBox.bottom
            : inspector.scrollTop + actionBox.top - viewportBox.top;
        inspector.scrollTop = Math.max(0, Math.min(bottom, target));
    }
}

export function initializeStoryLogInspector(
    theaterId: string | null | undefined,
    { embedded = false }: StoryLogInspectorOptions = {},
): StoryLogInspectorController | undefined {
    const controls = document.getElementById('history-paging-controls');
    const rawContent = document.getElementById(embedded ? 'remote-story-log' : 'story-log-content');
    const inspector = document.getElementById(embedded ? 'remote-story-log' : 'story-log-inspector');
    const toggleBtn = document.getElementById('story-log-toggle-btn');
    const closeBtn = document.getElementById('story-log-close-btn');
    if (!rawContent || !theaterId) return undefined;
    const activeTheaterId: string = theaterId;
    const content: HTMLElement = rawContent;

    let isOpen = false;
    let refreshTimer: number | null = null;
    let loading = false;
    let lastLogText: string | null = null;

    async function loadStoryLog(): Promise<void> {
        if (!isOpen || document.hidden || loading) return;
        loading = true;
        if (lastLogText === null) content.textContent = 'Loading story log…';
        try {
            const response = await fetch(`/theaters/${encodeURIComponent(activeTheaterId)}/output/story_log.jsonl`, {
                cache: 'no-store',
            });
            if (!response.ok && response.status !== 404) throw new Error(`Unable to load story log (${response.status})`);
            const logText = response.status === 404 ? '' : await response.text();
            if (logText === lastLogText) return;
            const entries = logText.split('\n').filter((line) => line.trim()).map(parseStoryLogEntry);
            lastLogText = logText;
            content.replaceChildren();
            if (!entries.length) {
                content.textContent = 'No story actions have been recorded yet.';
                return;
            }
            groupTurns(entries).forEach((turn) => {
                const turnElement = element('div', 'story-log-turn');
                const actionCard = turn.action ? createCard(turn.action) : null;
                if (actionCard) turnElement.appendChild(actionCard);
                const planCard = turn.plan ? createCard(turn.plan) : null;
                if (planCard) turnElement.appendChild(planCard);
                if (turnElement.childElementCount) content.appendChild(turnElement);
            });
            // Wait for the appended cards to participate in layout before
            // calculating the scroll position.
            requestAnimationFrame(() => positionLatestAction(inspector, content));
        } catch (error) {
            console.error('Failed to load story log:', error);
            if (lastLogText === null) content.textContent = 'The story log could not be loaded.';
        } finally {
            loading = false;
        }
    }

    function syncRefresh(): void {
        if (refreshTimer !== null) clearInterval(refreshTimer);
        refreshTimer = null;
        if (isOpen && !document.hidden) {
            void loadStoryLog();
            refreshTimer = window.setInterval(() => {
                void loadStoryLog();
            }, 2000);
        }
    }

    function toggleStoryLog(force?: boolean): void {
        const nextOpen = typeof force === 'boolean' ? force : !isOpen;
        if (nextOpen === isOpen) return;
        isOpen = nextOpen;
        if (inspector) {
            inspector.classList.toggle('open', isOpen);
            inspector.classList.toggle('is-open', isOpen);
        }
        if (controls) {
            controls.classList.toggle('inspector-open', isOpen);
        }
        if (toggleBtn) {
            toggleBtn.setAttribute('aria-expanded', String(isOpen));
            toggleBtn.classList.toggle('active', isOpen);
        }
        syncRefresh();
    }

    document.addEventListener('visibilitychange', syncRefresh);

    if (embedded) return { setOpen: toggleStoryLog };

    if (toggleBtn) {
        toggleBtn.addEventListener('click', (e: MouseEvent) => {
            e.stopPropagation();
            toggleStoryLog();
        });
    }

    if (closeBtn) {
        closeBtn.addEventListener('click', (e: MouseEvent) => {
            e.stopPropagation();
            toggleStoryLog(false);
        });
    }

    document.addEventListener('click', (e: MouseEvent) => {
        const target = e.target instanceof Node ? e.target : null;
        if (isOpen && inspector && !inspector.contains(target) && toggleBtn && !toggleBtn.contains(target)) {
            toggleStoryLog(false);
        }
    });

    return undefined;
}
