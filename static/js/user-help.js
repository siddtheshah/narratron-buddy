/** Free front-page help; answers stay in the visitor's current page. */
const helpForm = document.getElementById('visitor-help-form');
if (helpForm) {
    const input = document.getElementById('visitor-help-question');
    const log = document.getElementById('visitor-help-log');
    const status = document.getElementById('visitor-help-status');
    const button = helpForm.querySelector('button');
    helpForm.addEventListener('submit', async event => {
        event.preventDefault();
        const question = input.value.trim();
        if (!question || button.disabled) return;
        button.disabled = true;
        status.textContent = 'Looking up your question…';
        try {
            const response = await fetch('/api/user-help', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question }),
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
    });
}
