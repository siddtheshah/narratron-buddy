export function setupTheaterReport(theaterId, closeMenu) {
    const modal = document.getElementById('report-theater-modal');
    const trigger = document.getElementById('menu-item-report');
    const form = document.getElementById('report-theater-form');
    const reason = document.getElementById('report-theater-reason');
    const details = document.getElementById('report-theater-details');
    const result = document.getElementById('report-theater-result');
    const description = document.getElementById('report-theater-description');
    const submit = document.getElementById('report-theater-submit');
    const cancel = document.getElementById('report-theater-close');
    let pending = false;
    let completed = false;
    let previousFocus;
    function isFeedback() {
        return reason.value === 'bug' || reason.value === 'suggestion';
    }
    function updateDescription() {
        description.textContent = isFeedback()
            ? 'Your feedback will be saved with this theater as context.'
            : 'Malicious activity reports include a theater snapshot for review.';
        details.placeholder = reason.value === 'bug'
            ? 'Steps to reproduce, what you expected, and what happened instead.'
            : reason.value === 'suggestion'
                ? 'Describe your idea and how it would help.'
                : 'Describe the activity you want us to review.';
    }
    reason.addEventListener('change', updateDescription);
    function close() {
        if (pending) return;
        modal.classList.remove('active');
        modal.setAttribute('aria-hidden', 'true');
        modal.inert = true;
        previousFocus?.focus();
    }
    trigger.addEventListener('click', () => {
        previousFocus = document.getElementById('menu-left-toggle-btn') || document.activeElement;
        closeMenu();
        form.reset();
        updateDescription();
        completed = false;
        submit.disabled = false;
        submit.hidden = false;
        reason.disabled = false;
        details.disabled = false;
        result.textContent = '';
        cancel.textContent = 'Cancel';
        modal.inert = false;
        modal.classList.add('active');
        modal.setAttribute('aria-hidden', 'false');
        reason.focus();
    });
    cancel.addEventListener('click', close);
    modal.addEventListener('click', event => { if (event.target === modal) close(); });
    modal.addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
        if (event.key === 'Tab') {
            const focusable = [...form.querySelectorAll('select, textarea, button')]
                .filter(element => !element.disabled && !element.hidden);
            const first = focusable[0], last = focusable[focusable.length - 1];
            if (!first) { event.preventDefault(); return; }
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
    });
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (pending || completed) return;
        if (!details.value.trim()) { result.textContent = 'Describe the problem before sending your report.'; details.focus(); return; }
        pending = true;
        submit.disabled = true;
        cancel.disabled = true;
        result.textContent = 'Sending report…';
        try {
            const feedback = isFeedback();
            const endpoint = feedback ? '/api/reports' : `/api/theaters/${encodeURIComponent(theaterId)}/reports`;
            const payload = feedback
                ? { category: reason.value, details: details.value.trim(), theater_id: theaterId }
                : { reason: reason.value, details: details.value.trim() };
            const response = await fetch(endpoint, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const body = await response.json();
            if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Could not save your report. Please try again.');
            completed = true;
            result.textContent = feedback ? 'Feedback sent. Thank you for helping improve Narratron.'
                : 'Report sent. Thank you for helping keep theaters safe.';
            submit.hidden = true;
            reason.disabled = true;
            details.disabled = true;
            cancel.textContent = 'Done';
        } catch (error) {
            result.textContent = error.message || 'Could not save your report. Please try again.';
        } finally {
            pending = false;
            submit.disabled = completed;
            cancel.disabled = false;
            if (completed) cancel.focus();
        }
    });
}
