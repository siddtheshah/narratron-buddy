/**
 * Beyond20 Listener for Narratron.
 * Connects Beyond20 browser extension roll events to Narratron chat and the Live AI Narrator.
 */

/**
 * Formats a clean, descriptive roll summary from Beyond20's rendered roll payload.
 * Suitable for both the chat timeline and the Live AI Narrator prompt.
 */
export function formatBeyond20RollSummary(roll) {
    if (!roll) return 'Unknown roll';

    const charName = roll.character?.name || 'Adventurer';
    const title = roll.title || roll.request?.name || 'Roll';
    const parts = [`${charName} rolled ${title}`];

    // Attack / Check rolls (e.g., d20)
    if (Array.isArray(roll.attack_rolls) && roll.attack_rolls.length > 0) {
        const rollDetails = roll.attack_rolls.map(r => {
            const total = r.total !== undefined ? r.total : r.result;
            const crit = r.is_crit ? ' (Critical Hit!)' : r.is_fail ? ' (Critical Fail!)' : '';
            return `${total}${crit}`;
        }).join(' / ');
        parts.push(`[${rollDetails}]`);
    }

    // Damage rolls
    if (Array.isArray(roll.damage_rolls) && roll.damage_rolls.length > 0) {
        const dmgDetails = roll.damage_rolls.map(([type, r]) => {
            const total = r ? (r.total !== undefined ? r.total : r.result) : '';
            return `${total} ${type || ''}`.trim();
        }).filter(Boolean).join(' + ');
        if (dmgDetails) {
            parts.push(`dealing ${dmgDetails} damage`);
        }
    } else if (roll.total_damages && Object.keys(roll.total_damages).length > 0) {
        const totals = Object.entries(roll.total_damages).map(([k, v]) => {
            const val = (v && typeof v === 'object' && v.total !== undefined) ? v.total : v;
            return `${val} ${k === 'Total' ? '' : k}`.trim();
        }).filter(Boolean).join(', ');
        if (totals) {
            parts.push(`dealing ${totals} damage`);
        }
    }

    return parts.join(' ');
}

/**
 * Initializes listeners for Beyond20 CustomEvents dispatched on the document.
 */
export function initializeBeyond20Listener(options = {}) {
    const {
        chatController,
        theaterId = '',
        isAllowedOrator = () => false,
        isCollabModeEnabled = () => false,
        isCurrentOrator = () => false,
        getAgentWs = () => null,
        forwardRollToAgent = null,
        onConnected = () => {},
        forwardToNarrator = true,
        notifyHPUpdates = true,
    } = options;

    let isBeyond20Detected = false;

    function checkPermission() {
        if (typeof isAllowedOrator === 'function') {
            return isAllowedOrator();
        }
        if (typeof isCurrentOrator === 'function') {
            return isCurrentOrator();
        }
        return false;
    }

    // 1. Listen for rendered rolls from Beyond20
    function handleRenderedRoll(event) {
        // Dice rolls are forwarded and rendered ONLY by people who have the 'allowed_orators' permission
        if (!checkPermission()) {
            return;
        }

        const roll = event.detail && event.detail[0];
        if (!roll) return;

        const charName = roll.character?.name || (chatController ? chatController.getCurrentUsername() : 'Adventurer');
        const summary = formatBeyond20RollSummary(roll);

        // Die rolls should only be forwarded to the agent if collab_mode is enabled
        let forwardedToAgent = false;
        const collabEnabled = typeof isCollabModeEnabled === 'function' ? isCollabModeEnabled() : false;
        if (forwardToNarrator && collabEnabled) {
            if (typeof forwardRollToAgent === 'function') {
                forwardRollToAgent(summary, roll);
                forwardedToAgent = true;
            } else {
                const ws = getAgentWs();
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({
                        type: "text",
                        text: `[D&D Dice Roll] ${summary}`
                    }));
                    forwardedToAgent = true;
                }
            }
        }

        // Normalize roll data structure for Narratron storage & rendering
        const rollData = {
            title: roll.title || roll.request?.name || 'Dice Roll',
            character: charName,
            attack_rolls: roll.attack_rolls || [],
            damage_rolls: roll.damage_rolls || [],
            total_damages: roll.total_damages || {},
            html: roll.html || null,
            description: roll.description || null,
            whisper: roll.whisper || null,
            forwarded_to_agent: forwardedToAgent,
        };

        // Post into chat (renders in Narratron chat timeline for allowed orators)
        if (chatController && typeof chatController.postChatMessage === 'function') {
            chatController.postChatMessage({
                author: charName,
                text: `🎲 ${summary}`,
                rollData: rollData,
            }).catch(err => {
                console.error('Beyond20: Failed to post roll to Narratron chat:', err);
            });
        }
    }

    // 2. Listen for character HP updates from D&D Beyond
    function handleHPUpdate(event) {
        if (!notifyHPUpdates) return;
        if (!checkPermission()) return;
        const [request, name, hp, maxHp, tempHp] = event.detail || [];
        if (!name || hp === undefined) return;

        let hpText = `❤️ ${name}'s HP: ${hp}/${maxHp}`;
        if (tempHp) hpText += ` (+${tempHp} temp)`;

        if (chatController && typeof chatController.postChatMessage === 'function') {
            chatController.postChatMessage({
                author: name,
                text: hpText,
            }).catch(() => {});
        }
    }

    // 3. Detect Beyond20 injection
    function handleBeyond20Loaded(event) {
        if (!isBeyond20Detected) {
            isBeyond20Detected = true;
            console.log('Beyond20: Successfully connected to Narratron session.');
            onConnected(event.detail && event.detail[0]);
        }
    }

    document.addEventListener('Beyond20_RenderedRoll', handleRenderedRoll);
    document.addEventListener('Beyond20_UpdateHP', handleHPUpdate);
    document.addEventListener('Beyond20_Loaded', handleBeyond20Loaded);
    document.addEventListener('Beyond20_NewSettings', handleBeyond20Loaded);

    return {
        disconnect() {
            document.removeEventListener('Beyond20_RenderedRoll', handleRenderedRoll);
            document.removeEventListener('Beyond20_UpdateHP', handleHPUpdate);
            document.removeEventListener('Beyond20_Loaded', handleBeyond20Loaded);
            document.removeEventListener('Beyond20_NewSettings', handleBeyond20Loaded);
        },
        isDetected() {
            return isBeyond20Detected;
        }
    };
}
