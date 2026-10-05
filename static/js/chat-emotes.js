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

    function setOpen(open) {
        palette.hidden = !open;
        toggle.setAttribute('aria-expanded', String(open));
    }

    for (const [emote, label] of emotes) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = emote;
        button.title = label;
        button.setAttribute('aria-label', label);
        button.addEventListener('click', () => {
            chatInput.setRangeText(emote, chatInput.selectionStart, chatInput.selectionEnd, 'end');
            chatInput.dispatchEvent(new Event('input', { bubbles: true }));
            setOpen(false);
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
        if (!palette.hidden && !palette.contains(event.target) && !toggle.contains(event.target)) {
            setOpen(false);
        }
    });
    chatForm.addEventListener('submit', () => setOpen(false));
}
