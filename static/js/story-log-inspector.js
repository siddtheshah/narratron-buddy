/** Render and load the theater-local story log shown from canvas paging controls. */

function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
}

function formatTime(timestamp) {
    if (!timestamp) return 'Unknown time';
    const date = new Date(timestamp);
    return Number.isNaN(date.getTime()) ? 'Unknown time' : date.toLocaleString([], {
        month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    });
}

function createCard(entry) {
    const isAction = entry.type === 'user_action';
    const output = entry.output || {};
    const card = element('article', `story-log-entry ${isAction ? 'action' : 'plan'}`);
    const label = element('div', 'story-log-label');
    label.append(
        element('span', '', isAction ? 'Player action' : 'Story plan'),
        element('time', 'story-log-time', formatTime(entry.timestamp)),
    );
    card.appendChild(label);

    if (isAction) {
        card.appendChild(element('div', 'story-log-text', entry.action || 'No action recorded.'));
        return card;
    }

    if (output.narration) card.appendChild(element('div', 'story-log-text', output.narration));
    if (Array.isArray(output.dialogue) && output.dialogue.length) {
        card.appendChild(element('div', 'story-log-section', 'Dialogue'));
        output.dialogue.forEach((line) => {
            const speaker = line && line.speaker ? `${line.speaker}: ` : '';
            card.appendChild(element('div', 'story-log-text', `${speaker}${line?.text || ''}`));
        });
    }
    return output.narration || output.dialogue?.length ? card : null;
}

function groupTurns(entries) {
    const turns = [];
    entries.forEach((entry) => {
        if (entry.type === 'user_action') {
            turns.push({ action: entry, plan: null });
            return;
        }
        const turn = [...turns].reverse().find((candidate) => !candidate.plan && candidate.action);
        if (turn) turn.plan = entry;
        else turns.push({ action: null, plan: entry });
    });
    return turns.reverse();
}

export function initializeStoryLogInspector(theaterId) {
    const controls = document.getElementById('history-paging-controls');
    const content = document.getElementById('story-log-content');
    if (!controls || !content || !theaterId) return;

    async function loadStoryLog() {
        content.textContent = 'Loading story log…';
        try {
            const response = await fetch(`/theaters/${encodeURIComponent(theaterId)}/output/story_log.jsonl`);
            if (response.status === 404) {
                content.textContent = 'No story actions have been recorded yet.';
                return;
            }
            if (!response.ok) throw new Error(`Unable to load story log (${response.status})`);
            const entries = (await response.text()).split('\n').filter(Boolean).map((line) => JSON.parse(line));
            content.replaceChildren();
            if (!entries.length) {
                content.textContent = 'No story actions have been recorded yet.';
                return;
            }
            groupTurns(entries).forEach((turn) => {
                const turnElement = element('div', 'story-log-turn');
                if (turn.action) turnElement.appendChild(createCard(turn.action));
                const planCard = turn.plan && createCard(turn.plan);
                if (planCard) turnElement.appendChild(planCard);
                if (turnElement.childElementCount) content.appendChild(turnElement);
            });
        } catch (error) {
            console.error('Failed to load story log:', error);
            content.textContent = 'The story log could not be loaded.';
        }
    }

    controls.addEventListener('mouseenter', loadStoryLog);
    controls.addEventListener('focusin', loadStoryLog);
}
