/** Shared emote picker for the canvas and popout chat composers. */
export function initializeChatEmotes(chatForm, chatInput) {
    const toggle = chatForm.querySelector('[data-chat-emotes-toggle]');
    const palette = chatForm.querySelector('#chat-emotes-palette');
    const emotes = [
        ['😀', 'Smile'], ['😂', 'Laugh'], ['🥹', 'Touched'], ['😍', 'Love'],
        ['😎', 'Cool'], ['🤔', 'Thinking'], ['😮', 'Surprised'], ['😭', 'Crying'],
        ['😱', 'Shocked'], ['😈', 'Mischievous'], ['👀', 'Eyes'], ['💀', 'Skull'],
        ['👍', 'Thumbs up'], ['👏', 'Applause'], ['🙌', 'Celebrate'], ['❤️', 'Heart'],
        ['🔥', 'Fire'], ['✨', 'Sparkles'], ['🎉', 'Party'], ['💡', 'Idea'],
        ['🎲', 'Dice'], ['⚔️', 'Swords'], ['🛡️', 'Shield'], ['🐉', 'Dragon'],
    ];

    let closeTimer;

    function resetCloseTimer() {
        clearTimeout(closeTimer);
        if (!palette.hidden) {
            closeTimer = setTimeout(() => {
                if (palette.contains(document.activeElement)) chatInput.focus();
                setOpen(false);
            }, 10000);
        }
    }

    function setOpen(open) {
        palette.hidden = !open;
        toggle.setAttribute('aria-expanded', String(open));
        resetCloseTimer();
    }

    chatInput.addEventListener('input', resetCloseTimer);

    for (const [emote, label] of emotes) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = emote;
        button.title = label;
        button.setAttribute('aria-label', label);
        button.addEventListener('click', () => {
            chatInput.setRangeText(emote, chatInput.selectionStart, chatInput.selectionEnd, 'end');
            chatInput.dispatchEvent(new Event('input', { bubbles: true }));
            chatInput.focus();
        });
        palette.appendChild(button);
    }

    toggle.addEventListener('click', () => {
        const open = palette.hidden;
        setOpen(open);
        if (open) palette.querySelector('button').focus();
    });

    chatForm.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !palette.hidden) {
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
            toggle.focus();
        }
    });

    document.addEventListener('pointerdown', (event) => {
        if (!palette.hidden && !chatForm.contains(event.target)) {
            setOpen(false);
        }
    });
    chatForm.addEventListener('submit', resetCloseTimer);
}
