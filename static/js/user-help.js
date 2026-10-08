/** Basic help and optional paid research; bank-style floating support chat widget. */
const helpForm = document.getElementById('visitor-help-form');
if (helpForm) {
    const input = document.getElementById('visitor-help-question');
    const log = document.getElementById('visitor-help-log');
    const status = document.getElementById('visitor-help-status');
    const button = helpForm.querySelector('button');
    const launcher = document.getElementById('visitor-help-launcher');
    const panel = document.getElementById('visitor-help-panel');
    const closeBtn = document.getElementById('visitor-help-close');
    const welcome = document.getElementById('visitor-help-welcome');

    const widget = document.getElementById('visitor-help-widget') || (panel ? panel.closest('.visitor-help') : null);

    function openHelp() {
        if (!panel) return;
        panel.classList.add('is-open');
        if (widget) widget.classList.add('is-open');
        if (launcher) {
            launcher.setAttribute('aria-expanded', 'true');
            launcher.classList.add('is-hidden');
            launcher.style.display = 'none';
        }
        if (input) {
            setTimeout(() => input.focus(), 60);
        }
    }

    function closeHelp() {
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

    function toggleHelp() {
        if (panel && panel.classList.contains('is-open')) {
            closeHelp();
        } else {
            openHelp();
        }
    }

    window.toggleVisitorHelp = toggleHelp;
    window.openVisitorHelp = openHelp;
    window.closeVisitorHelp = closeHelp;

    if (launcher) {
        launcher.addEventListener('click', toggleHelp);
    }
    if (closeBtn) {
        closeBtn.addEventListener('click', closeHelp);
    }

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && panel && panel.classList.contains('is-open')) {
            closeHelp();
        }
    });

    // Quick suggestion chips
    const chips = document.querySelectorAll('.help-chip');
    chips.forEach(chip => {
        chip.addEventListener('click', () => {
            const query = chip.getAttribute('data-help-query') || chip.textContent;
            if (query && input) {
                input.value = query.trim();
                askHelp(query.trim());
            }
        });
    });

    async function askHelp(question, personalized = false, quotedCreditCost = null) {
        if (!question || button.disabled) return;
        if (panel && !panel.classList.contains('is-open')) {
            openHelp();
        }
        button.disabled = true;
        status.textContent = 'Looking up your question…';
        try {
            const response = await fetch('/api/user-help', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question, ...(personalized ? {
                    personalized: true, quoted_credit_cost: quotedCreditCost,
                } : {}) }),
            });
            const answer = await response.json();
            if (!response.ok) throw new Error(answer.detail || 'Help is unavailable. Please try again.');
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
            body.innerHTML = answer.html;
            article.append(promptContainer, body);
            if (answer.help_mode === 'basic') {
                const upgrade = document.createElement('button');
                upgrade.type = 'button';
                upgrade.className = 'join-btn visitor-help-upgrade-btn';
                upgrade.textContent = answer.can_personalize
                    ? `Personalized help · ${answer.personalized_credit_cost} cr`
                    : 'Sign in for personalized help';
                if (answer.can_personalize) {
                    upgrade.title = `Research this question for ${answer.personalized_credit_cost} credits from your account`;
                }
                upgrade.addEventListener('click', async () => {
                    if (!answer.can_personalize) {
                        input.value = question;
                        if (typeof window.openAuthModal === 'function') window.openAuthModal('login');
                        status.textContent = 'Sign in, then ask again to request personalized help.';
                        return;
                    }
                    upgrade.disabled = true;
                    try {
                        await askHelp(question, true, answer.personalized_credit_cost);
                    } finally {
                        upgrade.disabled = false;
                    }
                });
                article.appendChild(upgrade);
            }
            log.appendChild(article);
            log.scrollTop = log.scrollHeight;
            input.value = '';
            status.textContent = '';
        } catch (error) {
            status.textContent = error.message;
        } finally {
            button.disabled = false;
            input.focus();
        }
    }
    helpForm.addEventListener('submit', event => {
        event.preventDefault();
        askHelp(input.value.trim());
    });
}
