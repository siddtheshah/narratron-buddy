/**
 * Chat Controller for Narratron Canvas.
 * Manages chat identity, message rendering, suggestions, Beyond20 roll cards, and message submission.
 */

const ADJECTIVES = [
    "Cosmic", "Starlight", "Mystic", "Neon", "Quantum", "Shadow",
    "Solar", "Lunar", "Cyber", "Astral", "Velvet", "Radiant", "Echo", "Ethereal"
];
const NOUNS = [
    "Wanderer", "Voyager", "Doodler", "Scout", "Bard", "Seeker",
    "Explorer", "Weaver", "Dreamer", "Pilot", "Scribe", "Nomad"
];

export function generateFunUsername() {
    const adj = ADJECTIVES[Math.floor(Math.random() * ADJECTIVES.length)];
    const noun = NOUNS[Math.floor(Math.random() * NOUNS.length)];
    const num = Math.floor(Math.random() * 90) + 10;
    return `${adj} ${noun} ${num}`;
}

export function getAuthorColor(author) {
    if (!author || author.toLowerCase() === 'agent' || author.toLowerCase() === 'narratron') {
        return '#ffffff'; // Reserved white exclusively for Narratron
    }
    let hash = 0;
    for (let i = 0; i < author.length; i++) {
        hash = author.charCodeAt(i) + ((hash << 5) - hash);
    }
    const hue = Math.abs(hash) % 360;
    return `hsl(${hue}, 85%, 68%)`;
}

export function initializeChatController(options = {}) {
    const {
        messagesContainer,
        chatForm,
        chatInput,
        nameInput,
        nameBadge,
        hideSuggestionsBtn,
        theaterId = '',
        isCurrentOrator = () => false,
        getAgentWs = () => null,
        getAuthState = () => Promise.resolve({ authenticated: false }),
        popoutBroadcast = null,
    } = options;

    let currentChatUsername = "";
    let isAuthenticatedUser = false;
    let lastChatFingerprint = "";
    let suggestionsHidden = false;

    // --- Identity Management ---
    async function initChatUser() {
        try {
            const data = await getAuthState();
            if (data && data.authenticated && data.user && data.user.username) {
                currentChatUsername = data.user.username;
                isAuthenticatedUser = true;
                if (nameBadge) {
                    nameBadge.textContent = 'Member';
                    nameBadge.style.background = 'rgba(34, 197, 94, 0.2)';
                    nameBadge.style.color = '#4ade80';
                }
            }
        } catch (e) {
            console.warn("Could not fetch auth state for chat identity:", e);
        }

        if (!currentChatUsername) {
            const savedAnonName = localStorage.getItem('narratron_anon_chat_name');
            if (savedAnonName && savedAnonName.trim()) {
                currentChatUsername = savedAnonName.trim();
            } else {
                currentChatUsername = generateFunUsername();
                localStorage.setItem('narratron_anon_chat_name', currentChatUsername);
            }
        }

        if (nameInput) {
            nameInput.value = currentChatUsername;
            nameInput.addEventListener('change', updateChatName);
            nameInput.addEventListener('blur', updateChatName);
        }
    }

    function updateChatName() {
        if (!nameInput) return;
        const newName = nameInput.value.trim();
        if (newName) {
            currentChatUsername = newName;
            if (!isAuthenticatedUser) {
                localStorage.setItem('narratron_anon_chat_name', newName);
            }
            if (popoutBroadcast) {
                popoutBroadcast.postMessage({ type: 'name_updated', name: newName });
            }
        } else {
            nameInput.value = currentChatUsername;
        }
    }

    function getCurrentUsername() {
        return currentChatUsername;
    }

    function setChatUsername(newName) {
        if (!newName || !newName.trim()) return;
        currentChatUsername = newName.trim();
        if (nameInput) {
            nameInput.value = currentChatUsername;
        }
    }

    // --- Message Rendering ---
    function renderChatMessage(msg) {
        const div = document.createElement('div');
        div.className = 'chat-message';

        const rawAuthor = msg.author || 'Narratron';
        const isNarratron = (rawAuthor.toLowerCase() === 'agent' || rawAuthor.toLowerCase() === 'narratron');
        const displayAuthor = isNarratron ? 'Narratron' : rawAuthor;
        const color = (!isNarratron && /^#[0-9a-fA-F]{6}$/.test(msg.profile_color || ''))
            ? msg.profile_color
            : getAuthorColor(displayAuthor);

        function makeAuthorElement(className) {
            const element = msg.profile_username ? document.createElement('a') : document.createElement('span');
            element.className = className;
            if (msg.profile_username) {
                element.href = `/users/${encodeURIComponent(msg.profile_username)}`;
                element.title = `View ${displayAuthor}'s profile`;
            }
            return element;
        }

        // 1. Suggestion messages
        if (msg.type === 'suggestion') {
            div.classList.add('suggestion-message');
            div.dataset.suggestionAuthor = rawAuthor;

            const prefixSpan = makeAuthorElement('suggestion-prefix');
            prefixSpan.textContent = `💡 ${displayAuthor} suggests:`;
            div.appendChild(prefixSpan);

            const textSpan = document.createElement('span');
            textSpan.className = 'chat-text';
            textSpan.textContent = msg.text;
            div.appendChild(textSpan);

            const actions = document.createElement('div');
            actions.className = 'suggestion-actions';

            const upvoteBtn = document.createElement('button');
            upvoteBtn.className = 'suggestion-upvote-btn';
            upvoteBtn.textContent = '👍 +1';
            upvoteBtn.addEventListener('click', () => upvoteSuggestion(rawAuthor));
            actions.appendChild(upvoteBtn);

            const countSpan = document.createElement('span');
            countSpan.className = 'suggestion-vote-count';
            countSpan.dataset.voteCountFor = rawAuthor;
            countSpan.textContent = '';
            actions.appendChild(countSpan);

            if (rawAuthor === currentChatUsername) {
                const withdrawBtn = document.createElement('button');
                withdrawBtn.className = 'suggestion-withdraw-btn';
                withdrawBtn.textContent = '✕ Withdraw';
                withdrawBtn.addEventListener('click', () => withdrawMySuggestion());
                actions.appendChild(withdrawBtn);
            }

            div.appendChild(actions);
            return div;
        }

        // 2. Dice Roll messages (Beyond20 or custom dice)
        if (msg.roll_data) {
            div.classList.add('chat-roll-message');

            const card = document.createElement('div');
            card.className = 'chat-roll-card';

            if (msg.roll_data.html) {
                // Render only the card directly (no extra wrapper headers)
                card.innerHTML = msg.roll_data.html;
            } else {
                // Fallback for roll_data without pre-rendered HTML: render results without header
                const resultsRow = document.createElement('div');
                resultsRow.className = 'chat-roll-results';

                // Attack / Check Rolls (e.g. d20)
                if (Array.isArray(msg.roll_data.attack_rolls) && msg.roll_data.attack_rolls.length > 0) {
                    msg.roll_data.attack_rolls.forEach(r => {
                        const rollBadge = document.createElement('span');
                        rollBadge.className = 'roll-badge attack-roll';
                        const totalVal = r.total !== undefined ? r.total : r.result;
                        const isCrit = r.is_crit || (r.rolls && r.rolls.some && r.rolls.some(die => die.rolls && die.rolls.includes(20)));
                        const isFail = r.is_fail || (r.rolls && r.rolls.some && r.rolls.some(die => die.rolls && die.rolls.includes(1)));

                        if (isCrit) rollBadge.classList.add('crit-hit');
                        if (isFail) rollBadge.classList.add('crit-fail');

                        rollBadge.textContent = `🎯 ${totalVal}`;
                        if (r.formula) rollBadge.title = `Formula: ${r.formula}`;
                        resultsRow.appendChild(rollBadge);
                    });
                }

                // Damage Rolls
                if (Array.isArray(msg.roll_data.damage_rolls) && msg.roll_data.damage_rolls.length > 0) {
                    msg.roll_data.damage_rolls.forEach(([dmgType, dmgRoll]) => {
                        const dmgBadge = document.createElement('span');
                        dmgBadge.className = 'roll-badge damage-roll';
                        const dmgVal = dmgRoll ? (dmgRoll.total !== undefined ? dmgRoll.total : dmgRoll.result) : '';
                        dmgBadge.textContent = `💥 ${dmgVal} ${dmgType || ''}`.trim();
                        resultsRow.appendChild(dmgBadge);
                    });
                } else if (msg.roll_data.total_damages && Object.keys(msg.roll_data.total_damages).length > 0) {
                    Object.entries(msg.roll_data.total_damages).forEach(([label, total]) => {
                        const dmgBadge = document.createElement('span');
                        dmgBadge.className = 'roll-badge damage-roll';
                        const totalVal = (total && typeof total === 'object' && total.total !== undefined) ? total.total : total;
                        dmgBadge.textContent = `💥 ${totalVal} ${label === 'Total' ? '' : label}`.trim();
                        resultsRow.appendChild(dmgBadge);
                    });
                }

                if (resultsRow.children.length > 0) {
                    card.appendChild(resultsRow);
                } else if (msg.text) {
                    const summaryDiv = document.createElement('div');
                    summaryDiv.className = 'chat-roll-summary-text';
                    summaryDiv.textContent = msg.text.replace(/^🎲\s*/, '');
                    card.appendChild(summaryDiv);
                }
            }

            div.appendChild(card);
            return div;
        }

        // 3. Standard text chat
        const prefixSpan = makeAuthorElement('chat-prefix');
        prefixSpan.style.color = color;
        prefixSpan.style.fontWeight = '600';
        prefixSpan.style.marginRight = '6px';
        prefixSpan.textContent = `${displayAuthor}:`;

        const textSpan = document.createElement('span');
        textSpan.className = 'chat-text';
        textSpan.textContent = msg.text;

        div.appendChild(prefixSpan);
        div.appendChild(textSpan);
        return div;
    }

    // --- Suggestion Helpers ---
    async function upvoteSuggestion(targetAuthor) {
        try {
            await fetch('/api/suggestions/upvote' + (theaterId ? `?theater_id=${encodeURIComponent(theaterId)}` : ''), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ voter: currentChatUsername, target_author: targetAuthor })
            });
            fetchSuggestions();
        } catch (err) {
            console.error('Failed to upvote suggestion:', err);
        }
    }

    async function withdrawMySuggestion() {
        try {
            await fetch('/api/suggestions/withdraw' + (theaterId ? `?theater_id=${encodeURIComponent(theaterId)}` : ''), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ author: currentChatUsername })
            });
            fetchChat();
            fetchSuggestions();
        } catch (err) {
            console.error('Failed to withdraw suggestion:', err);
        }
    }

    async function fetchSuggestions() {
        try {
            const res = await fetch('/api/suggestions' + (theaterId ? `?theater_id=${encodeURIComponent(theaterId)}` : ''));
            const suggestions = await res.json();
            const activeAuthors = new Set(suggestions.map(s => s.author));
            document.querySelectorAll('.suggestion-message').forEach(el => {
                if (!activeAuthors.has(el.dataset.suggestionAuthor)) el.remove();
            });

            for (const s of suggestions) {
                const countEls = document.querySelectorAll(`[data-vote-count-for="${CSS.escape(s.author)}"]`);
                countEls.forEach(el => {
                    el.textContent = s.upvote_count > 0 ? `${s.upvote_count} vote${s.upvote_count !== 1 ? 's' : ''}` : '';
                });
                if (s.upvoters && s.upvoters.includes(currentChatUsername)) {
                    document.querySelectorAll(`.suggestion-message[data-suggestion-author="${CSS.escape(s.author)}"] .suggestion-upvote-btn`)
                        .forEach(btn => btn.classList.add('voted'));
                }
            }
        } catch (err) {
            console.error('Failed to fetch suggestions:', err);
        }
    }

    function setSuggestionsHidden(hidden) {
        suggestionsHidden = hidden;
        if (messagesContainer) {
            messagesContainer.classList.toggle('suggestions-hidden', suggestionsHidden);
        }
    }

    // --- Chat Fetching ---
    async function fetchChat(forceScrollToBottom = false) {
        if (!messagesContainer) return;
        try {
            const res = await fetch('/api/chat' + (theaterId ? `?theater_id=${encodeURIComponent(theaterId)}` : ''));
            if (!res.ok) return;
            const messages = await res.json();
            const fingerprint = JSON.stringify(messages);
            if (fingerprint !== lastChatFingerprint) {
                const isNearBottom = (messagesContainer.scrollHeight - messagesContainer.scrollTop - messagesContainer.clientHeight) < 120;
                const isInitial = (lastChatFingerprint === '');
                messagesContainer.innerHTML = '';
                messages.forEach(msg => {
                    messagesContainer.appendChild(renderChatMessage(msg));
                });
                lastChatFingerprint = fingerprint;

                if (forceScrollToBottom || isInitial || isNearBottom) {
                    requestAnimationFrame(() => {
                        messagesContainer.scrollTop = messagesContainer.scrollHeight;
                    });
                }
            }
        } catch (err) {
            console.warn('Chat fetch failed:', err);
        }
    }

    // --- Message Submission Pipeline ---
    async function postChatMessage({ author, text, rollData = null }) {
        if (!text || !text.trim()) return null;

        const effectiveAuthor = author || currentChatUsername || 'Adventurer';
        const body = {
            author: effectiveAuthor,
            text: text.trim(),
        };
        if (rollData) {
            body.roll_data = rollData;
        }

        const res = await fetch('/api/chat' + (theaterId ? `?theater_id=${encodeURIComponent(theaterId)}` : ''), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        if (!res.ok) {
            throw new Error(`Chat request failed: ${res.status}`);
        }

        const result = await res.json();
        await fetchChat(true);

        if (result.type === 'suggestion') {
            await fetchSuggestions();
        }

        // Notify popout window if active
        if (popoutBroadcast) {
            popoutBroadcast.postMessage({ type: 'chat_sent', text: text.trim() });
        }

        return result;
    }

    // Bind form submit listener if provided
    if (chatForm && chatInput) {
        chatForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const text = chatInput.value.trim();
            if (!text) return;

            chatInput.value = '';
            try {
                const result = await postChatMessage({
                    author: currentChatUsername,
                    text: text
                });

                // Forward non-suggestion message to Live Agent if orator
                const agentWs = getAgentWs();
                if (result && result.type !== 'suggestion' && isCurrentOrator() && agentWs && agentWs.readyState === WebSocket.OPEN) {
                    agentWs.send(JSON.stringify({
                        type: "text",
                        text: text
                    }));
                }
            } catch (err) {
                console.error("Failed to submit chat:", err);
            }
        });
    }

    // Initialize Identity and initial chat fetch
    initChatUser();
    fetchChat(true);

    return {
        initChatUser,
        updateChatName,
        setChatUsername,
        getCurrentUsername,
        fetchChat,
        fetchSuggestions,
        postChatMessage,
        renderChatMessage,
        setSuggestionsHidden,
    };
}
