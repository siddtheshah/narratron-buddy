/** Basic help and optional paid research; answers stay in the visitor's current page. */
const helpForm = document.getElementById('visitor-help-form');
if (helpForm) {
    const input = document.getElementById('visitor-help-question');
    const log = document.getElementById('visitor-help-log');
    const status = document.getElementById('visitor-help-status');
    const button = helpForm.querySelector('button');
    async function askHelp(question, personalized = false, quotedCreditCost = null) {
        if (!question || button.disabled) return;
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
            const article = document.createElement('article');
            const prompt = document.createElement('strong');
            prompt.textContent = question;
            const body = document.createElement('div');
            // The server renders Markdown with HTML escaping and safe links.
            body.innerHTML = answer.html;
            article.append(prompt, body);
            if (answer.help_mode === 'basic') {
                const upgrade = document.createElement('button');
                upgrade.type = 'button';
                upgrade.className = 'join-btn';
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
