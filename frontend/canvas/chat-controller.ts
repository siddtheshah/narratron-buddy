/**
 * Chat Controller for Narratron Canvas.
 * Manages chat identity, message rendering, suggestions, Beyond20 roll cards, and message submission.
 */

import { initializeChatEmotes } from './chat-emotes.js';

declare global {
  interface Window {
    openAuthModal?: (tab?: string) => void;
  }
}

export interface ChatAuthUser {
  readonly id?: number;
  readonly username?: string;
  readonly profile_color?: string | null;
}

export interface ChatAuthState {
  readonly authenticated: boolean;
  readonly user?: ChatAuthUser | null;
}

export interface ChatRollDie {
  readonly rolls?: readonly number[];
}

export interface ChatAttackRoll {
  readonly total?: number | string;
  readonly result?: number | string;
  readonly is_crit?: boolean;
  readonly is_fail?: boolean;
  readonly formula?: string;
  readonly rolls?: readonly ChatRollDie[];
}

export interface ChatDamageRollValue {
  readonly total?: number | string;
  readonly result?: number | string;
}

export type ChatDamageRollEntry = readonly [
  string,
  ChatDamageRollValue | null | undefined,
];

export type ChatTotalDamageValue = number | string | { readonly total?: number | string };

export interface ChatRollData {
  readonly title?: string;
  readonly character?: string;
  readonly attack_rolls?: readonly ChatAttackRoll[];
  readonly damage_rolls?: readonly ChatDamageRollEntry[];
  readonly total_damages?: Readonly<Record<string, ChatTotalDamageValue>>;
  readonly html?: string | null;
  readonly description?: string | null;
  readonly whisper?: boolean | string | null;
  readonly forwarded_to_agent?: boolean;
}

export interface ChatSettingsChange {
  readonly setting: string;
  readonly enabled: boolean;
}

export interface ChatSettingsProposal {
  readonly config_digest?: string;
  readonly changes: readonly ChatSettingsChange[];
}

export interface ChatApplySettingsResult {
  readonly applied: boolean;
  readonly agent_running?: boolean;
  readonly message: string;
  readonly detail?: string;
}

export interface ChatMessage {
  readonly author?: string;
  readonly text?: string;
  readonly type?: string;
  readonly status?: string;
  readonly html?: string;
  readonly profile_color?: string | null;
  readonly profile_username?: string | null;
  readonly roll_data?: ChatRollData | null;
  readonly settings_proposal?: ChatSettingsProposal | null;
  readonly settings_summary?: readonly string[];
  readonly help_mode?: string;
  readonly help_question?: string;
  readonly can_personalize?: boolean;
  readonly personalized_credit_cost?: number;
}

export interface ChatAgentSocket {
  readonly readyState: number;
  send(data: string): void;
}

export type ChatBroadcastMessage =
  | { readonly type: 'name_updated'; readonly name: string }
  | { readonly type: 'chat_sent'; readonly text: string };

export interface ChatPopoutBroadcast {
  postMessage(message: ChatBroadcastMessage): void;
}

export interface ChatControllerOptions {
  readonly messagesContainer?: HTMLElement | null;
  readonly chatForm?: HTMLFormElement | null;
  readonly chatInput?: HTMLTextAreaElement | HTMLInputElement | null;
  readonly nameInput?: HTMLInputElement | null;
  readonly nameDisplay?: HTMLElement | null;
  readonly nameStatusIcon?: HTMLElement | null;
  readonly loginChip?: HTMLElement | null;
  readonly onOpenLogin?: (() => void) | null;
  readonly hideSuggestionsBtn?: HTMLElement | null;
  readonly theaterId?: string;
  readonly isCurrentOrator?: () => boolean;
  readonly isTheaterOwner?: () => boolean;
  readonly onSettingsApplied?: ((result: ChatApplySettingsResult) => Promise<void> | void) | null;
  readonly getAgentWs?: () => ChatAgentSocket | null;
  readonly getAuthState?: () => Promise<ChatAuthState>;
  readonly popoutBroadcast?: ChatPopoutBroadcast | null;
}

export interface ChatPostMessageOptions {
  readonly author?: string;
  readonly text: string;
  readonly rollData?: ChatRollData | null;
}

export interface ChatPostResult {
  readonly type?: string;
  readonly author?: string;
  readonly text?: string;
}

export interface PrivateHelpResult {
  readonly type?: string;
  readonly text?: string;
  readonly html?: string;
  readonly help_mode?: string;
  readonly can_personalize?: boolean;
  readonly personalized_credit_cost?: number;
  readonly detail?: string;
}

export interface ChatController {
  initChatUser(): Promise<void>;
  updateChatName(): void;
  setChatUsername(newName: string): void;
  getCurrentUsername(): string;
  fetchChat(forceScrollToBottom?: boolean): Promise<void>;
  fetchSuggestions(): Promise<void>;
  postChatMessage(
    options: ChatPostMessageOptions,
  ): Promise<ChatPostResult | PrivateHelpResult | null>;
  renderChatMessage(msg: ChatMessage): HTMLElement;
  setSuggestionsHidden(hidden: boolean): void;
}

interface SuggestionVoteSummary {
  readonly author: string;
  readonly upvote_count: number;
  readonly upvoters?: readonly string[];
}

interface SettingsActionState {
  pending: boolean;
  applied: boolean;
  message: string;
}

interface ChatPostRequestBody {
  author: string;
  text: string;
  roll_data?: ChatRollData;
}

const ADJECTIVES: readonly string[] = [
  'Cosmic', 'Starlight', 'Mystic', 'Neon', 'Quantum', 'Shadow',
  'Solar', 'Lunar', 'Cyber', 'Astral', 'Velvet', 'Radiant', 'Echo', 'Ethereal',
];

const NOUNS: readonly string[] = [
  'Wanderer', 'Voyager', 'Doodler', 'Scout', 'Bard', 'Seeker',
  'Explorer', 'Weaver', 'Dreamer', 'Pilot', 'Scribe', 'Nomad',
];

const ROLL_ALLOWED_TAGS: ReadonlySet<string> = new Set([
  'div', 'span', 'b', 'strong', 'i', 'em', 'u', 's', 'small', 'sub', 'sup',
  'br', 'hr', 'p', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td',
  'ul', 'ol', 'li', 'code', 'pre', 'details', 'summary', 'h3', 'h4', 'h5', 'h6',
  'blockquote', 'dl', 'dt', 'dd',
]);

const ROLL_STRIP_CONTENT_TAGS: ReadonlySet<string> = new Set([
  'script', 'style', 'svg', 'math', 'iframe', 'frame', 'frameset', 'object',
  'embed', 'applet', 'template', 'noscript', 'noembed', 'textarea', 'title',
  'xmp', 'head', 'audio', 'video', 'canvas', 'form', 'button', 'select', 'option',
]);

const ROLL_CLASS_TOKEN_RE = /^[A-Za-z0-9_-]{1,64}$/;

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Generate a random whimsical guest username. */
export function generateFunUsername(): string {
  const adj = ADJECTIVES[Math.floor(Math.random() * ADJECTIVES.length)];
  const noun = NOUNS[Math.floor(Math.random() * NOUNS.length)];
  const num = Math.floor(Math.random() * 90) + 10;
  return `${adj} ${noun} ${num}`;
}

/** Compute a deterministic display color for a chat author, reserving white for Narratron. */
export function getAuthorColor(author: string | null | undefined): string {
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

/** Sanitize Beyond20 roll HTML into a safe DOM DocumentFragment using an explicit tag/attribute allowlist. */
export function sanitizeRollHtmlToFragment(rawHtml: string | null | undefined): DocumentFragment {
  const fragment = document.createDocumentFragment();
  if (typeof rawHtml !== 'string' || !rawHtml.trim()) {
    return fragment;
  }
  const parsed = new DOMParser().parseFromString(rawHtml.slice(0, 16000), 'text/html');

  function cloneSafeNode(node: Node, depth: number): Node | null {
    if (depth > 32) return null;
    if (node.nodeType === Node.TEXT_NODE) {
      return document.createTextNode(node.nodeValue || '');
    }
    if (!(node instanceof Element)) {
      return null;
    }
    const tag = node.tagName.toLowerCase();
    if (ROLL_STRIP_CONTENT_TAGS.has(tag)) {
      return null;
    }
    if (!ROLL_ALLOWED_TAGS.has(tag)) {
      const passthrough = document.createDocumentFragment();
      for (const child of node.childNodes) {
        const safeChild = cloneSafeNode(child, depth + 1);
        if (safeChild) passthrough.appendChild(safeChild);
      }
      return passthrough;
    }
    const el = document.createElement(tag);
    const classAttr = node.getAttribute('class');
    if (classAttr) {
      const safeClasses = classAttr
        .split(/\s+/)
        .filter((tok) => ROLL_CLASS_TOKEN_RE.test(tok))
        .slice(0, 16);
      if (safeClasses.length > 0) {
        el.setAttribute('class', safeClasses.join(' '));
      }
    }
    const titleAttr = node.getAttribute('title');
    if (titleAttr && titleAttr.trim()) {
      el.setAttribute('title', titleAttr.trim().slice(0, 300));
    }
    if (tag === 'th' || tag === 'td') {
      for (const spanAttr of ['colspan', 'rowspan']) {
        const rawSpan = node.getAttribute(spanAttr);
        if (rawSpan && /^\d+$/.test(rawSpan.trim())) {
          const num = parseInt(rawSpan.trim(), 10);
          if (num >= 1 && num <= 20) {
            el.setAttribute(spanAttr, String(num));
          }
        }
      }
    }
    if (tag === 'details' && node.hasAttribute('open')) {
      el.setAttribute('open', '');
    }
    for (const child of node.childNodes) {
      const safeChild = cloneSafeNode(child, depth + 1);
      if (safeChild) el.appendChild(safeChild);
    }
    return el;
  }

  if (parsed.body) {
    for (const child of parsed.body.childNodes) {
      const safeChild = cloneSafeNode(child, 0);
      if (safeChild) fragment.appendChild(safeChild);
    }
  }
  return fragment;
}

/** Initialize the chat controller for identity, rendering, suggestions, help, and submission. */
export function initializeChatController(options: ChatControllerOptions = {}): ChatController {
  const {
    messagesContainer = null,
    chatForm = null,
    chatInput = null,
    nameInput = null,
    nameDisplay: initialNameDisplay = null,
    nameStatusIcon = null,
    loginChip: initialLoginChip = null,
    onOpenLogin = null,
    theaterId = '',
    isCurrentOrator = () => false,
    isTheaterOwner = () => false,
    onSettingsApplied = null,
    getAgentWs = () => null,
    getAuthState = () => Promise.resolve({ authenticated: false }),
    popoutBroadcast = null,
  } = options;

  let currentChatUsername = '';
  let isAuthenticatedUser = false;
  let lastChatFingerprint = '';
  const privateHelpMessages: ChatMessage[] = [];
  let helpPending = false;
  const settingsActions = new Map<string, SettingsActionState>();
  let suggestionsHidden = false;
  const heldScrollPointers = new Set<number>();
  let scrollInteractionVersion = 0;

  messagesContainer?.addEventListener(
    'pointerdown',
    (event: PointerEvent) => {
      heldScrollPointers.add(event.pointerId);
      scrollInteractionVersion++;
    },
    { passive: true },
  );
  const releaseScrollPointer = (event: PointerEvent): void => {
    heldScrollPointers.delete(event.pointerId);
  };
  window.addEventListener('pointerup', releaseScrollPointer, { passive: true });
  window.addEventListener('pointercancel', releaseScrollPointer, { passive: true });
  window.addEventListener('blur', () => heldScrollPointers.clear());
  for (const eventName of ['wheel', 'touchmove', 'keydown'] as const) {
    messagesContainer?.addEventListener(
      eventName,
      () => {
        scrollInteractionVersion++;
      },
      { passive: true },
    );
  }

  let nameDisplay: HTMLElement | null = initialNameDisplay || null;
  if (!nameDisplay && nameInput && nameInput.parentElement) {
    nameDisplay = nameInput.parentElement.querySelector<HTMLElement>('#chat-name-display');
    if (!nameDisplay) {
      nameDisplay = document.createElement('span');
      nameDisplay.id = 'chat-name-display';
      nameDisplay.style.display = 'none';
      nameDisplay.style.fontWeight = '600';
      nameDisplay.style.fontSize = '0.85rem';
      nameDisplay.style.overflow = 'hidden';
      nameDisplay.style.textOverflow = 'ellipsis';
      nameDisplay.style.whiteSpace = 'nowrap';
      nameInput.parentElement.insertBefore(nameDisplay, nameInput);
    }
  }

  let loginChip: HTMLElement | null = initialLoginChip || null;
  if (!loginChip && nameInput && nameInput.parentElement) {
    loginChip = nameInput.parentElement.querySelector<HTMLElement>('#chat-login-chip');
  }
  if (loginChip) {
    loginChip.addEventListener('click', (e: MouseEvent) => {
      e.preventDefault();
      if (typeof onOpenLogin === 'function') {
        onOpenLogin();
      } else if (typeof window.openAuthModal === 'function') {
        window.openAuthModal('login');
      }
    });
  }

  const helpButton = chatForm?.querySelector<HTMLButtonElement>('[data-chat-command="help"]') ?? null;
  function resetHelpButton(): void {
    if (!helpButton || helpPending) return;
    helpButton.textContent = '?';
    helpButton.title = 'Find basic help and documentation (free)';
    helpButton.setAttribute('aria-label', helpButton.title);
  }

  // --- Identity Management ---
  async function initChatUser(): Promise<void> {
    let profileColor: string | null = null;
    try {
      const data = await getAuthState();
      if (data && data.authenticated && data.user && data.user.username) {
        currentChatUsername = data.user.username;
        isAuthenticatedUser = true;
        const rawColor = data.user.profile_color;
        profileColor =
          rawColor && /^#[0-9a-fA-F]{6}$/.test(rawColor) ? rawColor : rawColor || '#818cf8';
        if (nameStatusIcon) {
          nameStatusIcon.textContent = '✓';
          nameStatusIcon.setAttribute('aria-label', 'Member');
          nameStatusIcon.title = 'Member';
          nameStatusIcon.style.color = '#4ade80';
        }
      }
    } catch (e: unknown) {
      console.warn('Could not fetch auth state for chat identity:', e);
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

    if (isAuthenticatedUser) {
      if (nameInput) {
        nameInput.style.display = 'none';
        nameInput.value = currentChatUsername;
      }
      if (nameDisplay) {
        nameDisplay.textContent = currentChatUsername;
        nameDisplay.style.color = profileColor || '#818cf8';
        nameDisplay.style.display = '';
      }
      if (loginChip) {
        loginChip.style.display = 'none';
      }
    } else {
      if (nameDisplay) {
        nameDisplay.style.display = 'none';
      }
      if (nameInput) {
        nameInput.style.display = '';
        nameInput.value = currentChatUsername;
        nameInput.addEventListener('change', updateChatName);
        nameInput.addEventListener('blur', updateChatName);
      }
      if (loginChip) {
        loginChip.style.display = 'inline-flex';
      }
    }
    refreshSuggestionVoting();
  }

  function updateChatName(): void {
    if (!nameInput) return;
    const newName = nameInput.value.trim();
    if (newName) {
      currentChatUsername = newName;
      if (nameDisplay) {
        nameDisplay.textContent = currentChatUsername;
      }
      refreshSuggestionVoting();
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

  function getCurrentUsername(): string {
    return currentChatUsername;
  }

  function setChatUsername(newName: string): void {
    if (!newName || !newName.trim()) return;
    currentChatUsername = newName.trim();
    refreshSuggestionVoting();
    if (nameInput) {
      nameInput.value = currentChatUsername;
    }
    if (nameDisplay) {
      nameDisplay.textContent = currentChatUsername;
    }
  }

  // --- Message Rendering ---
  function renderChatMessage(msg: ChatMessage): HTMLElement {
    const div = document.createElement('div');
    div.className = 'chat-message';
    if (msg.type === 'user_help') {
      div.classList.add('user-help-message');
    }

    const rawAuthor = msg.author || 'Narratron';
    const isNarratron =
      rawAuthor.toLowerCase() === 'agent' || rawAuthor.toLowerCase() === 'narratron';
    const displayAuthor = isNarratron ? 'Narratron' : rawAuthor;
    const profileColor = msg.profile_color ?? '';
    const color =
      !isNarratron && /^#[0-9a-fA-F]{6}$/.test(profileColor)
        ? profileColor
        : getAuthorColor(displayAuthor);

    function makeAuthorElement(className: string): HTMLAnchorElement | HTMLSpanElement {
      if (msg.profile_username) {
        const anchor = document.createElement('a');
        anchor.className = className;
        anchor.href = `/users/${encodeURIComponent(msg.profile_username)}`;
        anchor.title = `View ${displayAuthor}'s profile`;
        return anchor;
      }
      const span = document.createElement('span');
      span.className = className;
      return span;
    }

    // 1. Suggestion messages
    if (msg.type === 'suggestion') {
      div.classList.add('suggestion-message');
      div.dataset.suggestionAuthor = rawAuthor;
      div.dataset.suggestionStatus = msg.status || 'pending';

      const prefixSpan = makeAuthorElement('suggestion-prefix');
      prefixSpan.textContent = `💡 ${displayAuthor} suggests:`;
      div.appendChild(prefixSpan);

      const textSpan = document.createElement('span');
      textSpan.className = 'chat-text';
      textSpan.textContent = msg.text ?? '';
      div.appendChild(textSpan);

      const actions = document.createElement('div');
      actions.className = 'suggestion-actions';

      if (msg.status === 'accepted') {
        div.classList.add('suggestion-accepted');
        const badge = document.createElement('span');
        badge.className = 'suggestion-accepted-badge';
        badge.textContent = '✓ Accepted';
        actions.appendChild(badge);
        div.appendChild(actions);
        return div;
      }

      const upvoteBtn = document.createElement('button');
      upvoteBtn.className = 'suggestion-upvote-btn';
      upvoteBtn.textContent = '👍 +1';
      upvoteBtn.disabled = rawAuthor === currentChatUsername;
      upvoteBtn.title = upvoteBtn.disabled
        ? 'You cannot vote on your own suggestion'
        : 'Vote for this suggestion';
      upvoteBtn.addEventListener('click', () => {
        void upvoteSuggestion(rawAuthor);
      });
      actions.appendChild(upvoteBtn);

      const countSpan = document.createElement('span');
      countSpan.className = 'suggestion-vote-count';
      countSpan.dataset.voteCountFor = rawAuthor;
      countSpan.textContent = '1 vote';
      actions.appendChild(countSpan);

      if (rawAuthor === currentChatUsername) {
        const withdrawBtn = document.createElement('button');
        withdrawBtn.className = 'suggestion-withdraw-btn';
        withdrawBtn.textContent = '✕ Withdraw';
        withdrawBtn.addEventListener('click', () => {
          void withdrawMySuggestion();
        });
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
        const safeFragment = sanitizeRollHtmlToFragment(msg.roll_data.html);
        if (safeFragment.childNodes.length > 0) {
          card.appendChild(safeFragment);
        }
      }
      if (card.childNodes.length === 0) {
        // Fallback for roll_data without pre-rendered HTML: render results without header
        const resultsRow = document.createElement('div');
        resultsRow.className = 'chat-roll-results';

        // Attack / Check Rolls (e.g. d20)
        if (
          Array.isArray(msg.roll_data.attack_rolls) &&
          msg.roll_data.attack_rolls.length > 0
        ) {
          msg.roll_data.attack_rolls.forEach((r) => {
            const rollBadge = document.createElement('span');
            rollBadge.className = 'roll-badge attack-roll';
            const totalVal = r.total !== undefined ? r.total : r.result;
            const isCrit =
              Boolean(r.is_crit) ||
              Boolean(
                Array.isArray(r.rolls) &&
                  r.rolls.some((die: ChatRollDie) => Boolean(die.rolls?.includes(20))),
              );
            const isFail =
              Boolean(r.is_fail) ||
              Boolean(
                Array.isArray(r.rolls) &&
                  r.rolls.some((die: ChatRollDie) => Boolean(die.rolls?.includes(1))),
              );

            if (isCrit) rollBadge.classList.add('crit-hit');
            if (isFail) rollBadge.classList.add('crit-fail');

            rollBadge.textContent = `🎯 ${totalVal ?? ''}`;
            if (r.formula) rollBadge.title = `Formula: ${r.formula}`;
            resultsRow.appendChild(rollBadge);
          });
        }

        // Damage Rolls
        if (
          Array.isArray(msg.roll_data.damage_rolls) &&
          msg.roll_data.damage_rolls.length > 0
        ) {
          msg.roll_data.damage_rolls.forEach(([dmgType, dmgRoll]) => {
            const dmgBadge = document.createElement('span');
            dmgBadge.className = 'roll-badge damage-roll';
            const dmgVal = dmgRoll
              ? dmgRoll.total !== undefined
                ? dmgRoll.total
                : dmgRoll.result
              : '';
            dmgBadge.textContent = `💥 ${dmgVal ?? ''} ${dmgType || ''}`.trim();
            resultsRow.appendChild(dmgBadge);
          });
        } else if (
          msg.roll_data.total_damages &&
          Object.keys(msg.roll_data.total_damages).length > 0
        ) {
          Object.entries(msg.roll_data.total_damages).forEach(([label, total]) => {
            const dmgBadge = document.createElement('span');
            dmgBadge.className = 'roll-badge damage-roll';
            const totalVal =
              typeof total === 'object' && total !== null && total.total !== undefined
                ? total.total
                : total;
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

    const isHelp = msg.type === 'user_help';
    const textSpan = document.createElement(isHelp ? 'div' : 'span');
    textSpan.className = isHelp ? 'chat-text help-markdown' : 'chat-text';
    if (isHelp && msg.html) {
      // Help markup is escaped and rendered by the server.
      textSpan.innerHTML = msg.html;
      textSpan.querySelectorAll('a').forEach((anchor) => {
        anchor.setAttribute('target', '_blank');
        anchor.setAttribute('rel', 'noopener noreferrer');
      });
    } else {
      textSpan.textContent = msg.text ?? '';
    }

    div.appendChild(prefixSpan);
    div.appendChild(textSpan);
    if (isHelp && msg.settings_proposal && theaterId && isTheaterOwner()) {
      const proposal = msg.settings_proposal;
      const key = JSON.stringify(proposal);
      const state: SettingsActionState = settingsActions.get(key) || {
        pending: false,
        applied: false,
        message: '',
      };
      settingsActions.set(key, state);
      const review = document.createElement('div');
      review.className = 'help-settings-review';
      const summary = document.createElement('ul');
      for (const [index, change] of proposal.changes.entries()) {
        const item = document.createElement('li');
        item.textContent =
          msg.settings_summary?.[index] ||
          `${change.enabled ? 'Enable' : 'Disable'} ${change.setting.replaceAll('_', ' ').replace('.enabled', '')}`;
        summary.appendChild(item);
      }
      const apply = document.createElement('button');
      apply.type = 'button';
      apply.className = 'help-upgrade-btn help-settings-apply';
      apply.textContent = state.applied
        ? 'Settings applied'
        : 'Apply settings and restart agent';
      apply.disabled = state.pending || state.applied;
      const status = document.createElement('p');
      status.setAttribute('role', 'status');
      status.textContent = state.message;
      apply.addEventListener('click', async () => {
        if (state.pending || state.applied) return;
        state.pending = true;
        apply.disabled = true;
        state.message = 'Saving settings and restarting agent…';
        status.textContent = state.message;
        try {
          const response = await fetch(
            `/api/user-help/${encodeURIComponent(theaterId)}/apply-settings`,
            {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                'X-Narratron-Settings-Confirm': '1',
              },
              body: JSON.stringify(proposal),
            },
          );
          const result = (await response.json()) as ChatApplySettingsResult;
          if (!response.ok) {
            throw new Error(result.detail || 'Could not apply settings. Please try again.');
          }
          state.applied = result.applied;
          state.message = result.message;
          apply.textContent = 'Settings applied';
          if (onSettingsApplied) await onSettingsApplied(result);
        } catch (error: unknown) {
          state.message = getErrorMessage(error);
        } finally {
          state.pending = false;
          apply.disabled = state.applied;
          status.textContent = state.message;
          await fetchChat();
        }
      });
      review.append(summary, apply, status);
      div.appendChild(review);
    }
    if (isHelp && msg.help_mode === 'basic' && msg.help_question) {
      const helpQuestion = msg.help_question;
      const upgrade = document.createElement('button');
      upgrade.type = 'button';
      upgrade.className = 'help-upgrade-btn';
      upgrade.textContent = msg.can_personalize
        ? `Personalized help · ${msg.personalized_credit_cost} cr`
        : 'Sign in for personalized help';
      if (msg.can_personalize) {
        upgrade.title = `Research this question for ${msg.personalized_credit_cost} credits from your account`;
      }
      upgrade.addEventListener('click', async () => {
        if (!msg.can_personalize) {
          if (chatInput) chatInput.value = helpQuestion;
          if (onOpenLogin) onOpenLogin();
          return;
        }
        upgrade.disabled = true;
        try {
          await askPrivateHelp(
            helpQuestion,
            true,
            msg.personalized_credit_cost ?? null,
          );
        } catch (error: unknown) {
          privateHelpMessages.push({ author: 'Help', text: getErrorMessage(error) });
          await fetchChat(true);
        } finally {
          upgrade.disabled = false;
        }
      });
      div.appendChild(upgrade);
    }
    return div;
  }

  // --- Suggestion Helpers ---
  function refreshSuggestionVoting(): void {
    if (!messagesContainer) return;
    messagesContainer.querySelectorAll<HTMLElement>('.suggestion-message').forEach((el) => {
      const button = el.querySelector<HTMLButtonElement>('.suggestion-upvote-btn');
      if (!button) return;
      button.disabled = el.dataset.suggestionAuthor === currentChatUsername;
      button.title = button.disabled
        ? 'You cannot vote on your own suggestion'
        : 'Vote for this suggestion';
    });
  }

  async function upvoteSuggestion(targetAuthor: string): Promise<void> {
    if (targetAuthor === currentChatUsername) return;
    try {
      await fetch(
        '/api/suggestions/upvote' +
          (theaterId ? `?theater_id=${encodeURIComponent(theaterId)}` : ''),
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ voter: currentChatUsername, target_author: targetAuthor }),
        },
      );
      await fetchSuggestions();
    } catch (err: unknown) {
      console.error('Failed to upvote suggestion:', err);
    }
  }

  async function withdrawMySuggestion(): Promise<void> {
    try {
      await fetch(
        '/api/suggestions/withdraw' +
          (theaterId ? `?theater_id=${encodeURIComponent(theaterId)}` : ''),
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ author: currentChatUsername }),
        },
      );
      await fetchChat();
      await fetchSuggestions();
    } catch (err: unknown) {
      console.error('Failed to withdraw suggestion:', err);
    }
  }

  async function fetchSuggestions(): Promise<void> {
    try {
      const res = await fetch(
        '/api/suggestions' + (theaterId ? `?theater_id=${encodeURIComponent(theaterId)}` : ''),
      );
      const suggestions = (await res.json()) as SuggestionVoteSummary[];
      for (const s of suggestions) {
        const countEls = document.querySelectorAll<HTMLElement>(
          `[data-vote-count-for="${CSS.escape(s.author)}"]`,
        );
        countEls.forEach((el) => {
          el.textContent =
            s.upvote_count > 0
              ? `${s.upvote_count} vote${s.upvote_count !== 1 ? 's' : ''}`
              : '';
        });
        document
          .querySelectorAll<HTMLElement>(
            `.suggestion-message[data-suggestion-author="${CSS.escape(s.author)}"] .suggestion-upvote-btn`,
          )
          .forEach((btn) =>
            btn.classList.toggle(
              'voted',
              Boolean(s.upvoters && s.upvoters.includes(currentChatUsername)),
            ),
          );
      }
    } catch (err: unknown) {
      console.error('Failed to fetch suggestions:', err);
    }
  }

  function setSuggestionsHidden(hidden: boolean): void {
    suggestionsHidden = hidden;
    if (messagesContainer) {
      messagesContainer.classList.toggle('suggestions-hidden', suggestionsHidden);
    }
  }

  // --- Chat Fetching ---
  async function fetchChat(forceScrollToBottom: boolean = false): Promise<void> {
    if (!messagesContainer) return;
    try {
      const res = await fetch(
        '/api/chat' + (theaterId ? `?theater_id=${encodeURIComponent(theaterId)}` : ''),
      );
      if (!res.ok) return;
      const fetchedMessages = (await res.json()) as ChatMessage[];
      const messages: ChatMessage[] = [...fetchedMessages, ...privateHelpMessages];
      const fingerprint = JSON.stringify([isTheaterOwner(), messages, [...settingsActions]]);
      if (fingerprint !== lastChatFingerprint) {
        const previousScrollTop = messagesContainer.scrollTop;
        const isNearBottom =
          messagesContainer.scrollHeight -
            messagesContainer.scrollTop -
            messagesContainer.clientHeight <
          120;
        const isInitial = lastChatFingerprint === '';
        const interactionVersion = scrollInteractionVersion;
        const shouldFollow =
          heldScrollPointers.size === 0 &&
          (forceScrollToBottom || isInitial || isNearBottom);
        messagesContainer.innerHTML = '';
        messages.forEach((msg) => {
          messagesContainer.appendChild(renderChatMessage(msg));
        });
        // Rebuilding the log must not move someone reading older messages.
        messagesContainer.scrollTop = previousScrollTop;
        lastChatFingerprint = fingerprint;

        if (shouldFollow) {
          requestAnimationFrame(() => {
            if (
              heldScrollPointers.size === 0 &&
              interactionVersion === scrollInteractionVersion
            ) {
              messagesContainer.scrollTop = messagesContainer.scrollHeight;
            }
          });
        }
        await fetchSuggestions();
      }
    } catch (err: unknown) {
      console.warn('Chat fetch failed:', err);
    }
  }

  async function askPrivateHelp(
    question: string,
    personalized: boolean = false,
    quotedCreditCost: number | null = null,
  ): Promise<PrivateHelpResult> {
    if (helpPending) throw new Error('Please wait for your help answer.');
    helpPending = true;
    if (helpButton) {
      helpButton.disabled = true;
      helpButton.textContent = '…';
      helpButton.setAttribute('aria-label', 'Researching private help');
    }
    try {
      const res = await fetch(`/api/user-help?theater_id=${encodeURIComponent(theaterId)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          ...(personalized
            ? {
                personalized: true,
                quoted_credit_cost: quotedCreditCost,
              }
            : {}),
        }),
      });
      const result = (await res.json()) as PrivateHelpResult;
      if (!res.ok) {
        if (res.status === 401 && onOpenLogin) onOpenLogin();
        throw new Error(result.detail || 'Help is unavailable. Please try again.');
      }
      privateHelpMessages.push(
        { author: 'You (private help)', text: question },
        {
          ...result,
          text: result.text ?? '',
          author: 'Narratron User Help (only you)',
          help_question: question,
        },
      );
      await fetchChat(true);
      return result;
    } finally {
      helpPending = false;
      if (helpButton) {
        helpButton.disabled = false;
        resetHelpButton();
      }
    }
  }

  // --- Message Submission Pipeline ---
  async function postChatMessage({
    author,
    text,
    rollData = null,
  }: ChatPostMessageOptions): Promise<ChatPostResult | PrivateHelpResult | null> {
    if (!text || !text.trim()) return null;

    if (/^\/help(?:\s|$)/i.test(text)) {
      const question = text.replace(/^\/help\s*/i, '').trim();
      if (!question) throw new Error('Enter a question after /help.');
      return askPrivateHelp(question);
    }

    const effectiveAuthor = author || currentChatUsername || 'Adventurer';
    const body: ChatPostRequestBody = {
      author: effectiveAuthor,
      text: text.trim(),
    };
    if (rollData) {
      body.roll_data = rollData;
    }

    const res = await fetch(
      '/api/chat' + (theaterId ? `?theater_id=${encodeURIComponent(theaterId)}` : ''),
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    );

    if (!res.ok) {
      throw new Error(`Chat request failed: ${res.status}`);
    }

    const result = (await res.json()) as ChatPostResult;
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
    initializeChatEmotes(chatForm, chatInput);
    chatInput.addEventListener('keydown', (e: Event) => {
      if (!(e instanceof KeyboardEvent)) return;
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        if (e.ctrlKey) {
          chatForm.requestSubmit(
            chatForm.querySelector<HTMLElement>('[data-chat-command="suggest"]'),
          );
        } else {
          chatForm.requestSubmit();
        }
      }
    });
    chatForm.addEventListener('submit', async (e: SubmitEvent) => {
      e.preventDefault();
      let text = chatInput.value.trim();
      if (!text) return;
      if (
        e.submitter?.dataset.chatCommand === 'suggest' &&
        !/^\/suggest(?:\s|$)/i.test(text)
      ) {
        text = `/suggest ${text}`;
      }

      if (e.submitter?.dataset.chatCommand === 'help' && !/^\/help(?:\s|$)/i.test(text)) {
        text = `/help ${text}`;
      }
      resetHelpButton();
      chatInput.value = '';
      try {
        const result = await postChatMessage({
          author: currentChatUsername,
          text,
        });

        // Forward non-suggestion message to Live Agent if orator
        const agentWs = getAgentWs();
        if (
          result &&
          result.type !== 'suggestion' &&
          result.type !== 'user_help' &&
          isCurrentOrator() &&
          agentWs &&
          agentWs.readyState === WebSocket.OPEN
        ) {
          agentWs.send(
            JSON.stringify({
              type: 'text',
              text,
            }),
          );
        }
      } catch (err: unknown) {
        chatInput.value = text.replace(/^\/help\s*/i, '');
        privateHelpMessages.push({ author: 'Chat', text: getErrorMessage(err) });
        await fetchChat(true);
        console.error('Failed to submit chat:', err);
      }
    });
  }

  // Initialize Identity and initial chat fetch
  void initChatUser();
  void fetchChat(true);

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
