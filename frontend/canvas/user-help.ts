/** Basic help and optional paid research; bank-style floating support chat widget. */

import type {} from './auth-flow.js';

declare global {
  interface Window {
    toggleVisitorHelp?: () => void;
    openVisitorHelp?: () => void;
    closeVisitorHelp?: () => void;
  }
}

interface VisitorHelpResponse {
  readonly html?: string;
  readonly help_mode?: string;
  readonly can_personalize?: boolean;
  readonly personalized_credit_cost?: number;
  readonly detail?: unknown;
}

export interface VisitorHelpController {
  open(): void;
  close(): void;
  toggle(): void;
  destroy(): void;
}

export function initializeVisitorHelp(): VisitorHelpController | null {
  const helpForm = document.getElementById('visitor-help-form');
  const rawInput = document.getElementById('visitor-help-question');
  const rawLog = document.getElementById('visitor-help-log');
  const rawStatus = document.getElementById('visitor-help-status');
  if (
    !(helpForm instanceof HTMLFormElement) ||
    !(rawInput instanceof HTMLInputElement) ||
    !rawLog ||
    !rawStatus
  ) {
    return null;
  }

  const rawButton = helpForm.querySelector('button');
  if (!(rawButton instanceof HTMLButtonElement)) {
    return null;
  }

  const formEl = helpForm;
  const inputEl = rawInput;
  const logEl = rawLog;
  const statusEl = rawStatus;
  const buttonEl = rawButton;
  const launcher = document.getElementById('visitor-help-launcher');
  const panel = document.getElementById('visitor-help-panel');
  const closeBtn = document.getElementById('visitor-help-close');
  const welcome = document.getElementById('visitor-help-welcome');
  const widget =
    document.getElementById('visitor-help-widget') ?? (panel ? panel.closest('.visitor-help') : null);

  function openHelp(): void {
    if (!panel) return;
    panel.classList.add('is-open');
    if (widget) widget.classList.add('is-open');
    if (launcher) {
      launcher.setAttribute('aria-expanded', 'true');
      launcher.classList.add('is-hidden');
      launcher.style.display = 'none';
    }
    window.setTimeout(() => inputEl.focus(), 60);
  }

  function closeHelp(): void {
    if (!panel) return;
    panel.classList.remove('is-open');
    if (widget) widget.classList.remove('is-open');
    if (launcher) {
      launcher.setAttribute('aria-expanded', 'false');
      launcher.classList.remove('is-hidden');
      launcher.style.display = '';
      launcher.focus();
    }
  }

  function toggleHelp(): void {
    if (panel && panel.classList.contains('is-open')) {
      closeHelp();
    } else {
      openHelp();
    }
  }

  window.toggleVisitorHelp = toggleHelp;
  window.openVisitorHelp = openHelp;
  window.closeVisitorHelp = closeHelp;

  async function askHelp(
    question: string,
    personalized = false,
    quotedCreditCost: number | null = null,
  ): Promise<void> {
    if (!question || buttonEl.disabled) return;
    if (panel && !panel.classList.contains('is-open')) {
      openHelp();
    }
    buttonEl.disabled = true;
    statusEl.textContent = 'Looking up your question…';
    try {
      const response = await fetch('/api/user-help', {
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
      const answer = (await response.json()) as VisitorHelpResponse;
      if (!response.ok) {
        const detail = typeof answer.detail === 'string' ? answer.detail : '';
        throw new Error(detail || 'Help is unavailable. Please try again.');
      }
      if (welcome) {
        welcome.style.display = 'none';
      }
      const article = document.createElement('article');
      const promptContainer = document.createElement('div');
      promptContainer.className = 'visitor-help-user-query';
      const prompt = document.createElement('strong');
      prompt.textContent = question;
      promptContainer.append(prompt);

      const body = document.createElement('div');
      body.className = 'visitor-help-bot-reply';
      // The server renders Markdown with HTML escaping and safe links.
      body.innerHTML = answer.html ?? '';
      article.append(promptContainer, body);

      if (answer.help_mode === 'basic') {
        const canPersonalize = Boolean(answer.can_personalize);
        const creditCost = answer.personalized_credit_cost ?? null;
        const upgrade = document.createElement('button');
        upgrade.type = 'button';
        upgrade.className = 'join-btn visitor-help-upgrade-btn';
        upgrade.textContent = canPersonalize
          ? `Personalized help · ${creditCost} cr`
          : 'Sign in for personalized help';
        if (canPersonalize) {
          upgrade.title = `Research this question for ${creditCost} credits from your account`;
        }
        upgrade.addEventListener('click', async () => {
          if (!canPersonalize) {
            inputEl.value = question;
            if (typeof window.openAuthModal === 'function') window.openAuthModal('login');
            statusEl.textContent = 'Sign in, then ask again to request personalized help.';
            return;
          }
          upgrade.disabled = true;
          try {
            await askHelp(question, true, creditCost);
          } finally {
            upgrade.disabled = false;
          }
        });
        article.appendChild(upgrade);
      }

      logEl.appendChild(article);
      logEl.scrollTop = logEl.scrollHeight;
      inputEl.value = '';
      statusEl.textContent = '';
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : '';
      statusEl.textContent = message || 'Help is unavailable. Please try again.';
    } finally {
      buttonEl.disabled = false;
      inputEl.focus();
    }
  }

  const onKeyDown = (event: KeyboardEvent): void => {
    if (event.key === 'Escape' && panel && panel.classList.contains('is-open')) {
      closeHelp();
    }
  };

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    void askHelp(inputEl.value.trim());
  };

  if (launcher) {
    launcher.addEventListener('click', toggleHelp);
  }
  if (closeBtn) {
    closeBtn.addEventListener('click', closeHelp);
  }
  document.addEventListener('keydown', onKeyDown);
  formEl.addEventListener('submit', onSubmit);

  // Quick suggestion chips
  const chips = Array.from(document.querySelectorAll('.help-chip'));
  const chipListeners: Array<{ element: Element; handler: () => void }> = [];
  for (const chip of chips) {
    const handler = (): void => {
      const query = chip.getAttribute('data-help-query') || chip.textContent;
      if (query) {
        inputEl.value = query.trim();
        void askHelp(query.trim());
      }
    };
    chip.addEventListener('click', handler);
    chipListeners.push({ element: chip, handler });
  }

  return {
    open: openHelp,
    close: closeHelp,
    toggle: toggleHelp,
    destroy(): void {
      if (launcher) {
        launcher.removeEventListener('click', toggleHelp);
      }
      if (closeBtn) {
        closeBtn.removeEventListener('click', closeHelp);
      }
      document.removeEventListener('keydown', onKeyDown);
      formEl.removeEventListener('submit', onSubmit);
      for (const { element, handler } of chipListeners) {
        element.removeEventListener('click', handler);
      }
      if (window.toggleVisitorHelp === toggleHelp) {
        delete window.toggleVisitorHelp;
      }
      if (window.openVisitorHelp === openHelp) {
        delete window.openVisitorHelp;
      }
      if (window.closeVisitorHelp === closeHelp) {
        delete window.closeVisitorHelp;
      }
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initializeVisitorHelp();
  });
} else {
  initializeVisitorHelp();
}
