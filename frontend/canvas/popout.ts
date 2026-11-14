import { initializeChatEmotes } from './chat-emotes.js';
import { startCanvasStatePolling } from './state-polling.js';

export interface PopoutAttackRoll {
  readonly total?: number;
  readonly result?: number;
  readonly is_crit?: boolean;
  readonly is_fail?: boolean;
}

export type PopoutDamageTotal = number | { readonly total?: number };

export interface PopoutRollData {
  readonly html?: string;
  readonly attack_rolls?: readonly PopoutAttackRoll[];
  readonly damage_rolls?: ReadonlyArray<readonly [string, PopoutAttackRoll | null | undefined]>;
  readonly total_damages?: Readonly<Record<string, PopoutDamageTotal>>;
}

export interface PopoutChatMessage {
  readonly type?: string;
  readonly status?: string;
  readonly author?: string;
  readonly text?: string;
  readonly html?: string;
  readonly roll_data?: PopoutRollData;
}

interface PopoutAuthMeResponse {
  readonly authenticated?: boolean;
  readonly user?: {
    readonly username?: string;
  };
}

interface PopoutLatestResponse {
  readonly agent_thought?: {
    readonly text?: string;
    readonly highlighted?: boolean;
  } | null;
  readonly prompt?: string | null;
  readonly music?: {
    readonly playlist?: string | null;
    readonly paused?: boolean;
  } | null;
}

interface PopoutBroadcastMessage {
  readonly type?: string;
  readonly name?: string;
  readonly text?: string;
}

interface PopoutStateSocketMessage {
  readonly type: string;
  readonly domains?: readonly string[];
}

export interface PopoutChatController {
  fetchChat(): Promise<void>;
  fetchLatestState(): Promise<void>;
  renderChatMessage(msg: PopoutChatMessage): HTMLDivElement;
  dispose(): void;
}

const ADJECTIVES: readonly string[] = [
  'Cosmic',
  'Starlight',
  'Mystic',
  'Neon',
  'Quantum',
  'Shadow',
  'Solar',
  'Lunar',
  'Cyber',
  'Astral',
  'Velvet',
  'Radiant',
  'Echo',
  'Ethereal',
];

const NOUNS: readonly string[] = [
  'Wanderer',
  'Voyager',
  'Doodler',
  'Scout',
  'Bard',
  'Seeker',
  'Explorer',
  'Weaver',
  'Dreamer',
  'Pilot',
  'Scribe',
  'Nomad',
];

const ROLL_ALLOWED_TAGS = new Set([
  'div',
  'span',
  'b',
  'strong',
  'i',
  'em',
  'u',
  's',
  'small',
  'sub',
  'sup',
  'br',
  'hr',
  'p',
  'table',
  'thead',
  'tbody',
  'tfoot',
  'tr',
  'th',
  'td',
  'ul',
  'ol',
  'li',
  'code',
  'pre',
  'details',
  'summary',
  'h3',
  'h4',
  'h5',
  'h6',
  'blockquote',
  'dl',
  'dt',
  'dd',
]);

const ROLL_STRIP_CONTENT_TAGS = new Set([
  'script',
  'style',
  'svg',
  'math',
  'iframe',
  'frame',
  'frameset',
  'object',
  'embed',
  'applet',
  'template',
  'noscript',
  'noembed',
  'textarea',
  'title',
  'xmp',
  'head',
  'audio',
  'video',
  'canvas',
  'form',
  'button',
  'select',
  'option',
]);

const ROLL_CLASS_TOKEN_RE = /^[A-Za-z0-9_-]{1,64}$/;

function generateFunUsername(): string {
  const adj = ADJECTIVES[Math.floor(Math.random() * ADJECTIVES.length)];
  const noun = NOUNS[Math.floor(Math.random() * NOUNS.length)];
  const num = Math.floor(Math.random() * 90) + 10;
  return `${adj} ${noun} ${num}`;
}

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
    if (node.nodeType !== Node.ELEMENT_NODE) {
      return null;
    }
    const element = node as Element;
    const tag = (element.tagName || '').toLowerCase();
    if (ROLL_STRIP_CONTENT_TAGS.has(tag)) {
      return null;
    }
    if (!ROLL_ALLOWED_TAGS.has(tag)) {
      const passthrough = document.createDocumentFragment();
      for (const child of element.childNodes) {
        const safeChild = cloneSafeNode(child, depth + 1);
        if (safeChild) passthrough.appendChild(safeChild);
      }
      return passthrough;
    }
    const el = document.createElement(tag);
    const classAttr = element.getAttribute('class');
    if (classAttr) {
      const safeClasses = classAttr
        .split(/\s+/)
        .filter((tok) => ROLL_CLASS_TOKEN_RE.test(tok))
        .slice(0, 16);
      if (safeClasses.length > 0) {
        el.setAttribute('class', safeClasses.join(' '));
      }
    }
    const titleAttr = element.getAttribute('title');
    if (titleAttr && titleAttr.trim()) {
      el.setAttribute('title', titleAttr.trim().slice(0, 300));
    }
    if (tag === 'th' || tag === 'td') {
      for (const spanAttr of ['colspan', 'rowspan']) {
        const rawSpan = element.getAttribute(spanAttr);
        if (rawSpan && /^\d+$/.test(rawSpan.trim())) {
          const num = parseInt(rawSpan.trim(), 10);
          if (num >= 1 && num <= 20) {
            el.setAttribute(spanAttr, String(num));
          }
        }
      }
    }
    if (tag === 'details' && element.hasAttribute('open')) {
      el.setAttribute('open', '');
    }
    for (const child of element.childNodes) {
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

function buildRollCard(msg: PopoutChatMessage, rollData: PopoutRollData): HTMLDivElement {
  const card = document.createElement('div');
  card.className = 'chat-roll-card';

  if (rollData.html) {
    const safeFragment = sanitizeRollHtmlToFragment(rollData.html);
    if (safeFragment.childNodes.length > 0) {
      card.appendChild(safeFragment);
    }
  }

  if (card.childNodes.length === 0) {
    // Fallback for roll_data without pre-rendered HTML: render results without header.
    const resultsRow = document.createElement('div');
    resultsRow.className = 'chat-roll-results';

    if (Array.isArray(rollData.attack_rolls) && rollData.attack_rolls.length > 0) {
      rollData.attack_rolls.forEach((roll) => {
        const rollBadge = document.createElement('span');
        rollBadge.className = 'roll-badge attack-roll';
        const totalVal = roll.total !== undefined ? roll.total : roll.result;
        if (roll.is_crit) rollBadge.classList.add('crit-hit');
        if (roll.is_fail) rollBadge.classList.add('crit-fail');
        rollBadge.textContent = `🎯 ${totalVal}`;
        resultsRow.appendChild(rollBadge);
      });
    }

    if (Array.isArray(rollData.damage_rolls) && rollData.damage_rolls.length > 0) {
      rollData.damage_rolls.forEach(([dmgType, dmgRoll]) => {
        const dmgBadge = document.createElement('span');
        dmgBadge.className = 'roll-badge damage-roll';
        const dmgVal = dmgRoll
          ? dmgRoll.total !== undefined
            ? dmgRoll.total
            : dmgRoll.result
          : '';
        dmgBadge.textContent = `💥 ${dmgVal} ${dmgType || ''}`.trim();
        resultsRow.appendChild(dmgBadge);
      });
    } else if (rollData.total_damages && Object.keys(rollData.total_damages).length > 0) {
      Object.entries(rollData.total_damages).forEach(([label, total]) => {
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

  return card;
}

export function renderChatMessage(msg: PopoutChatMessage): HTMLDivElement {
  const div = document.createElement('div');
  div.className = 'chat-message';
  if (msg.type === 'user_help') div.classList.add('user-help-message');

  const rawAuthor = msg.author || 'Narratron';
  const isNarratron =
    rawAuthor.toLowerCase() === 'agent' || rawAuthor.toLowerCase() === 'narratron';
  const displayAuthor = isNarratron ? 'Narratron' : rawAuthor;
  const color = getAuthorColor(displayAuthor);

  if (msg.type === 'suggestion' && msg.status === 'accepted') {
    div.classList.add('suggestion-message', 'suggestion-accepted');
    const text = document.createElement('span');
    text.textContent = `💡 ${displayAuthor} suggests: ${msg.text ?? ''} `;
    const badge = document.createElement('span');
    badge.className = 'suggestion-accepted-badge';
    badge.textContent = '✓ Accepted';
    div.append(text, badge);
    return div;
  }

  if (msg.roll_data) {
    div.classList.add('chat-roll-message');
    div.appendChild(buildRollCard(msg, msg.roll_data));
    return div;
  }

  const prefixSpan = document.createElement('span');
  prefixSpan.className = 'chat-prefix';
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
  return div;
}

/** Initialize the pop-out chat UI, BroadcastChannel sync, state polling, and WebSocket. */
export function initPopoutChat(): PopoutChatController {
  const urlParams = new URLSearchParams(window.location.search);
  const theaterId = urlParams.get('theater_id') || '';

  const theaterIdEl = document.getElementById('popout-narratron-theater-id');
  if (theaterIdEl) {
    theaterIdEl.textContent = theaterId ? `Theater: ${theaterId}` : 'No Theater Selected';
  }

  const chatMessages = document.getElementById('chat-messages');
  const chatForm = document.getElementById('chat-form') as HTMLFormElement | null;
  const chatInput = document.getElementById('chat-input') as HTMLTextAreaElement | null;
  const agentThoughtText = document.getElementById('agent-thought-text');
  if (!chatMessages || !chatForm || !chatInput || !agentThoughtText) {
    throw new Error('Required popout chat DOM elements are missing.');
  }

  const abortController = new AbortController();
  const { signal } = abortController;

  let currentChatUsername = '';
  let isAuthenticatedUser = false;
  const myUserId = `Popout_${Math.floor(Math.random() * 10000)}`;
  const broadcast = new BroadcastChannel('narratron_popout_channel');

  function updateChatName(): void {
    const nameInput = document.getElementById('chat-name-input') as HTMLInputElement | null;
    if (!nameInput) return;
    const newName = nameInput.value.trim();
    if (newName) {
      currentChatUsername = newName;
      if (!isAuthenticatedUser) {
        localStorage.setItem('narratron_anon_chat_name', newName);
      }
      broadcast.postMessage({ type: 'name_updated', name: newName });
    } else {
      nameInput.value = currentChatUsername;
    }
  }

  async function initChatUser(): Promise<void> {
    try {
      const res = await fetch('/api/auth/me');
      if (res.ok) {
        const data = (await res.json()) as PopoutAuthMeResponse;
        if (data.authenticated && data.user?.username) {
          currentChatUsername = data.user.username;
          isAuthenticatedUser = true;
          const badge = document.getElementById('chat-name-badge');
          if (badge) {
            badge.textContent = 'Member';
            badge.style.background = 'rgba(34, 197, 94, 0.2)';
            badge.style.color = '#4ade80';
          }
        }
      }
    } catch {
      // Fall back to guest display name if auth endpoint is unreachable.
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

    const nameInput = document.getElementById('chat-name-input') as HTMLInputElement | null;
    if (nameInput && !signal.aborted) {
      nameInput.value = currentChatUsername;
      nameInput.addEventListener('change', updateChatName, { signal });
      nameInput.addEventListener('blur', updateChatName, { signal });
    }
  }

  void initChatUser();

  broadcast.onmessage = (event: MessageEvent<PopoutBroadcastMessage>) => {
    if (event.data?.type === 'name_updated' && event.data.name) {
      currentChatUsername = event.data.name;
      const nameInput = document.getElementById('chat-name-input') as HTMLInputElement | null;
      if (nameInput) nameInput.value = currentChatUsername;
    }
  };

  // Tab switching
  document.querySelectorAll<HTMLButtonElement>('.tab-btn').forEach((btn) => {
    btn.addEventListener(
      'click',
      () => {
        document.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach((c) => c.classList.remove('active'));
        btn.classList.add('active');
        const tabId = btn.dataset.tab;
        if (tabId) {
          document.getElementById(tabId)?.classList.add('active');
        }
      },
      { signal },
    );
  });

  // Dock Back handler
  document.getElementById('dock-back-btn')?.addEventListener(
    'click',
    () => {
      broadcast.postMessage({ type: 'dock_panel' });
      window.close();
    },
    { signal },
  );

  let lastMsgCount = 0;

  async function fetchChat(): Promise<void> {
    if (!theaterId || signal.aborted) return;
    try {
      const res = await fetch(`/api/chat?theater_id=${encodeURIComponent(theaterId)}`);
      if (!res.ok) return;
      const messages = (await res.json()) as PopoutChatMessage[];
      if (signal.aborted || !Array.isArray(messages)) return;
      if (messages.length !== lastMsgCount) {
        chatMessages!.innerHTML = '';
        messages.forEach((msg) => {
          chatMessages!.appendChild(renderChatMessage(msg));
        });
        chatMessages!.scrollTop = chatMessages!.scrollHeight;
        lastMsgCount = messages.length;
      }
    } catch {
      // Transient fetch failures are retried by state polling/WS notifications.
    }
  }

  async function fetchLatestState(): Promise<void> {
    if (!theaterId || signal.aborted) return;
    try {
      const res = await fetch(`/api/latest?theater_id=${encodeURIComponent(theaterId)}`);
      if (!res.ok) return;
      const data = (await res.json()) as PopoutLatestResponse;
      if (signal.aborted) return;
      const thought = data.agent_thought?.text;
      agentThoughtText!.closest('.agent-thought-card')?.classList.toggle(
        'is-highlighted',
        Boolean(data.agent_thought?.highlighted && thought),
      );
      const isWandering = (thought || '').trim().toLowerCase() === 'wandering';
      agentThoughtText!.textContent = isWandering
        ? 'Narratron has wandered away. (Try resummoning?)'
        : thought || 'Waiting for Narratron to think…';
      agentThoughtText!.classList.toggle('is-wandering', isWandering);

      const promptEl = document.getElementById('popout-prompt-text');
      if (data.prompt && promptEl) {
        promptEl.textContent = data.prompt;
      }
      const musicEl = document.getElementById('popout-music-status');
      if (musicEl) {
        if (data.music?.playlist) {
          musicEl.textContent = `🎵 ${data.music.playlist}${data.music.paused ? ' (Paused)' : ''}`;
        } else {
          musicEl.textContent = '🎵 Silent';
        }
      }
    } catch {
      // Transient fetch failures are retried by state polling/WS notifications.
    }
  }

  initializeChatEmotes(chatForm, chatInput);

  chatInput.addEventListener(
    'keydown',
    (e: KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        if (e.ctrlKey) {
          const suggestBtn = chatForm.querySelector<HTMLElement>('[data-chat-command="suggest"]');
          chatForm.requestSubmit(suggestBtn);
        } else {
          chatForm.requestSubmit();
        }
      }
    },
    { signal },
  );

  chatForm.addEventListener(
    'submit',
    async (e: SubmitEvent) => {
      e.preventDefault();
      let text = chatInput.value.trim();
      if (!text) return;
      const submitter = e.submitter as HTMLElement | null;
      if (submitter?.dataset.chatCommand === 'suggest' && !/^\/suggest(?:\s|$)/i.test(text)) {
        text = `/suggest ${text}`;
      }
      chatInput.value = '';
      try {
        const chatUrl = theaterId
          ? `/api/chat?theater_id=${encodeURIComponent(theaterId)}`
          : '/api/chat';
        const res = await fetch(chatUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ author: currentChatUsername || myUserId, text }),
        });
        if (res.ok) {
          void fetchChat();
          broadcast.postMessage({ type: 'chat_sent', text });
        }
      } catch {
        // Ignore transient submission error; next poll keeps chat in sync.
      }
    },
    { signal },
  );

  void fetchChat();
  void fetchLatestState();

  let canvasStateWs: WebSocket | null = null;
  let canvasStateReconnectDelay = 1000;
  let canvasStateReconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let canvasStateRefreshTimer: ReturnType<typeof setTimeout> | null = null;
  const pendingCanvasStateDomains = new Set<string>();

  function refreshCanvasState(domains: readonly string[] = ['latest', 'chat']): void {
    if (signal.aborted) return;
    domains.forEach((domain) => pendingCanvasStateDomains.add(domain));
    if (canvasStateRefreshTimer) return;
    canvasStateRefreshTimer = setTimeout(() => {
      canvasStateRefreshTimer = null;
      const pending = new Set(pendingCanvasStateDomains);
      pendingCanvasStateDomains.clear();
      if (pending.has('latest')) void fetchLatestState();
      if (pending.has('chat')) void fetchChat();
    }, 50);
  }

  function connectCanvasStateSocket(): void {
    if (!theaterId || signal.aborted) return;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socketUrl = `${protocol}//${window.location.host}/ws/canvas-state?theater_id=${encodeURIComponent(theaterId)}`;
    canvasStateWs = new WebSocket(socketUrl);
    canvasStateWs.onopen = () => {
      canvasStateReconnectDelay = 1000;
    };
    canvasStateWs.onmessage = (event: MessageEvent<string>) => {
      try {
        const message = JSON.parse(event.data) as PopoutStateSocketMessage;
        if (message.type === 'state_ready') {
          refreshCanvasState();
        } else if (message.type === 'state_changed' && Array.isArray(message.domains)) {
          refreshCanvasState(message.domains);
        }
      } catch (error) {
        console.warn('Ignoring invalid canvas-state message.', error);
      }
    };
    canvasStateWs.onclose = () => {
      canvasStateWs = null;
      if (!signal.aborted && !canvasStateReconnectTimer) {
        canvasStateReconnectTimer = setTimeout(() => {
          canvasStateReconnectTimer = null;
          connectCanvasStateSocket();
        }, canvasStateReconnectDelay);
        canvasStateReconnectDelay = Math.min(canvasStateReconnectDelay * 2, 30000);
      }
    };
  }

  startCanvasStatePolling(refreshCanvasState);
  connectCanvasStateSocket();

  // Notify parent window on unload
  window.addEventListener(
    'beforeunload',
    () => {
      broadcast.postMessage({ type: 'popout_closed' });
    },
    { signal },
  );

  return {
    fetchChat,
    fetchLatestState,
    renderChatMessage,
    dispose(): void {
      abortController.abort();
      if (canvasStateReconnectTimer) {
        clearTimeout(canvasStateReconnectTimer);
        canvasStateReconnectTimer = null;
      }
      if (canvasStateRefreshTimer) {
        clearTimeout(canvasStateRefreshTimer);
        canvasStateRefreshTimer = null;
      }
      canvasStateWs?.close();
      broadcast.close();
    },
  };
}
