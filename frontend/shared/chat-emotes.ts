const CHAT_EMOTES: ReadonlyArray<readonly [string, string]> = [
  ['😀', 'Smile'],
  ['😂', 'Laugh'],
  ['🥹', 'Touched'],
  ['😍', 'Love'],
  ['😎', 'Cool'],
  ['🤔', 'Thinking'],
  ['😮', 'Surprised'],
  ['😭', 'Crying'],
  ['😱', 'Shocked'],
  ['😈', 'Mischievous'],
  ['👀', 'Eyes'],
  ['💀', 'Skull'],
  ['👍', 'Thumbs up'],
  ['👏', 'Applause'],
  ['🙌', 'Celebrate'],
  ['❤️', 'Heart'],
  ['🔥', 'Fire'],
  ['✨', 'Sparkles'],
  ['🎉', 'Party'],
  ['💡', 'Idea'],
  ['🎲', 'Dice'],
  ['⚔️', 'Swords'],
  ['🛡️', 'Shield'],
  ['🐉', 'Dragon'],
];

/** Shared emote picker for the canvas and popout chat composers. */
export function initializeChatEmotes(
  chatForm: HTMLFormElement,
  chatInput: HTMLTextAreaElement | HTMLInputElement,
): void {
  const toggle = chatForm.querySelector<HTMLButtonElement>('[data-chat-emotes-toggle]');
  const palette = chatForm.querySelector<HTMLElement>('#chat-emotes-palette');
  if (!toggle || !palette) {
    throw new Error('Chat emotes require [data-chat-emotes-toggle] and #chat-emotes-palette elements.');
  }

  let closeTimer: number | undefined;

  function resetCloseTimer(): void {
    window.clearTimeout(closeTimer);
    if (!palette!.hidden) {
      closeTimer = window.setTimeout(() => {
        if (palette!.contains(document.activeElement)) {
          chatInput.focus();
        }
        setOpen(false);
      }, 10000);
    }
  }

  function setOpen(open: boolean): void {
    palette!.hidden = !open;
    toggle!.setAttribute('aria-expanded', String(open));
    resetCloseTimer();
  }

  chatInput.addEventListener('input', resetCloseTimer);

  for (const [emote, label] of CHAT_EMOTES) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = emote;
    button.title = label;
    button.setAttribute('aria-label', label);
    button.addEventListener('click', () => {
      const start = chatInput.selectionStart ?? 0;
      const end = chatInput.selectionEnd ?? 0;
      chatInput.setRangeText(emote, start, end, 'end');
      chatInput.dispatchEvent(new Event('input', { bubbles: true }));
      chatInput.focus();
    });
    palette.appendChild(button);
  }

  toggle.addEventListener('click', () => {
    const open = Boolean(palette.hidden);
    setOpen(open);
    if (open) {
      palette.querySelector<HTMLButtonElement>('button')?.focus();
    }
  });

  chatForm.addEventListener('keydown', (event: KeyboardEvent) => {
    if (event.key === 'Escape' && !palette.hidden) {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      toggle.focus();
    }
  });

  document.addEventListener('pointerdown', (event: PointerEvent) => {
    if (
      !palette.hidden &&
      (!(event.target instanceof Node) || !chatForm.contains(event.target))
    ) {
      setOpen(false);
    }
  });

  chatForm.addEventListener('submit', resetCloseTimer);
}
