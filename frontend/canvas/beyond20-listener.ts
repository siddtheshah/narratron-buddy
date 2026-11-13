/**
 * Beyond20 Listener for Narratron.
 * Connects Beyond20 browser extension roll events to Narratron chat and the Live AI Narrator.
 */

export interface Beyond20AttackRoll {
    total?: number | string;
    result?: number | string;
    is_crit?: boolean;
    is_fail?: boolean;
    formula?: string;
}

export interface Beyond20DamageRollValue {
    total?: number | string;
    result?: number | string;
}

export type Beyond20DamageRollEntry = [string, Beyond20DamageRollValue | null | undefined];

export type Beyond20TotalDamageValue = number | string | { total?: number | string };

export interface Beyond20RollPayload {
    character?: { name?: string } | null;
    title?: string;
    request?: { name?: string } | null;
    attack_rolls?: Beyond20AttackRoll[];
    damage_rolls?: Beyond20DamageRollEntry[];
    total_damages?: Record<string, Beyond20TotalDamageValue>;
    html?: string | null;
    description?: string | null;
    whisper?: boolean | string | null;
}

export interface Beyond20RollData {
    title: string;
    character: string;
    attack_rolls: Beyond20AttackRoll[];
    damage_rolls: Beyond20DamageRollEntry[];
    total_damages: Record<string, Beyond20TotalDamageValue>;
    html: string | null;
    description: string | null;
    whisper: boolean | string | null;
    forwarded_to_agent: boolean;
}

export interface Beyond20ChatPostPayload {
    author: string;
    text: string;
    rollData?: Beyond20RollData;
}

export interface Beyond20ChatController {
    getCurrentUsername(): string;
    postChatMessage(payload: Beyond20ChatPostPayload): Promise<unknown>;
}

export interface Beyond20AgentSocket {
    readonly readyState: number;
    send(data: string): void;
}

export type Beyond20SettingsPayload = Record<string, unknown>;

export interface Beyond20ListenerOptions {
    chatController?: Beyond20ChatController | null;
    theaterId?: string;
    isContributor?: (() => boolean) | null;
    isCollabModeEnabled?: (() => boolean) | null;
    isCurrentOrator?: (() => boolean) | null;
    getAgentWs?: (() => Beyond20AgentSocket | null) | null;
    forwardRollToAgent?: ((summary: string, roll: Beyond20RollPayload) => void) | null;
    onConnected?: ((settings: Beyond20SettingsPayload | undefined) => void) | null;
    forwardToNarrator?: boolean;
    notifyHPUpdates?: boolean;
}

export interface Beyond20ListenerController {
    disconnect(): void;
    isDetected(): boolean;
}

type Beyond20HpDetail = [unknown?, string?, number?, number?, number?];

/**
 * Formats a clean, descriptive roll summary from Beyond20's rendered roll payload.
 * Suitable for both the chat timeline and the Live AI Narrator prompt.
 */
export function formatBeyond20RollSummary(roll: Beyond20RollPayload | null | undefined): string {
    if (!roll) return 'Unknown roll';

    const charName = roll.character?.name || 'Adventurer';
    const title = roll.title || roll.request?.name || 'Roll';
    const parts = [`${charName} rolled ${title}`];

    // Attack / Check rolls (e.g., d20)
    if (Array.isArray(roll.attack_rolls) && roll.attack_rolls.length > 0) {
        const rollDetails = roll.attack_rolls.map((r) => {
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
            const val = (v !== null && typeof v === 'object' && v.total !== undefined) ? v.total : v;
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
export function initializeBeyond20Listener(
    options: Beyond20ListenerOptions = {},
): Beyond20ListenerController {
    const {
        chatController = null,
        theaterId: _theaterId = '',
        isContributor = () => false,
        isCollabModeEnabled = () => false,
        isCurrentOrator = () => false,
        getAgentWs = () => null,
        forwardRollToAgent = null,
        onConnected = () => { },
        forwardToNarrator = true,
        notifyHPUpdates = true,
    } = options;

    let isBeyond20Detected = false;

    function checkPermission(): boolean {
        if (typeof isContributor === 'function') {
            return isContributor();
        }
        if (typeof isCurrentOrator === 'function') {
            return isCurrentOrator();
        }
        return false;
    }

    // 1. Listen for rendered rolls from Beyond20
    function handleRenderedRoll(event: Event): void {
        // Dice rolls are forwarded and rendered ONLY by people who have the 'contributors' permission
        if (!checkPermission()) {
            return;
        }

        const detail: unknown = event instanceof CustomEvent ? event.detail : undefined;
        const roll = Array.isArray(detail) ? (detail[0] as Beyond20RollPayload | undefined) : undefined;
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
                const ws = typeof getAgentWs === 'function' ? getAgentWs() : null;
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({
                        type: 'text',
                        text: `[D&D Dice Roll] ${summary}`,
                    }));
                    forwardedToAgent = true;
                }
            }
        }

        // Normalize roll data structure for Narratron storage & rendering
        const rollData: Beyond20RollData = {
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

        // Post into chat (renders in Narratron chat timeline for contributors)
        if (chatController && typeof chatController.postChatMessage === 'function') {
            chatController.postChatMessage({
                author: charName,
                text: `🎲 ${summary}`,
                rollData: rollData,
            }).catch((err: unknown) => {
                console.error('Beyond20: Failed to post roll to Narratron chat:', err);
            });
        }
    }

    // 2. Listen for character HP updates from D&D Beyond
    function handleHPUpdate(event: Event): void {
        if (!notifyHPUpdates) return;
        if (!checkPermission()) return;
        const detail: unknown = event instanceof CustomEvent ? event.detail : undefined;
        const [, name, hp, maxHp, tempHp] = Array.isArray(detail) ? (detail as Beyond20HpDetail) : ([] as Beyond20HpDetail);
        if (!name || hp === undefined) return;

        let hpText = `❤️ ${name}'s HP: ${hp}/${maxHp}`;
        if (tempHp) hpText += ` (+${tempHp} temp)`;

        if (chatController && typeof chatController.postChatMessage === 'function') {
            chatController.postChatMessage({
                author: name,
                text: hpText,
            }).catch(() => { });
        }
    }

    // 3. Detect Beyond20 injection
    function handleBeyond20Loaded(event: Event): void {
        if (!isBeyond20Detected) {
            isBeyond20Detected = true;
            console.log('Beyond20: Successfully connected to Narratron session.');
            const detail: unknown = event instanceof CustomEvent ? event.detail : undefined;
            const settings = Array.isArray(detail) ? (detail[0] as Beyond20SettingsPayload | undefined) : undefined;
            onConnected?.(settings);
        }
    }

    document.addEventListener('Beyond20_RenderedRoll', handleRenderedRoll);
    document.addEventListener('Beyond20_UpdateHP', handleHPUpdate);
    document.addEventListener('Beyond20_Loaded', handleBeyond20Loaded);
    document.addEventListener('Beyond20_NewSettings', handleBeyond20Loaded);

    return {
        disconnect(): void {
            document.removeEventListener('Beyond20_RenderedRoll', handleRenderedRoll);
            document.removeEventListener('Beyond20_UpdateHP', handleHPUpdate);
            document.removeEventListener('Beyond20_Loaded', handleBeyond20Loaded);
            document.removeEventListener('Beyond20_NewSettings', handleBeyond20Loaded);
        },
        isDetected(): boolean {
            return isBeyond20Detected;
        },
    };
}
