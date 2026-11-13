type FocusableFormControl = HTMLSelectElement | HTMLTextAreaElement | HTMLButtonElement;

interface ReportErrorResponse {
  readonly detail?: unknown;
}

type TheaterReportPayload =
  | {
      readonly category: string;
      readonly details: string;
      readonly theater_id: string | null;
    }
  | {
      readonly reason: string;
      readonly details: string;
    };

/** Wire up the pullout menu trigger, modal controls, and submission flow for theater reports. */
export function setupTheaterReport(theaterId: string | null, closeMenu: () => void): void {
  const modal = document.getElementById('report-theater-modal');
  const trigger = document.getElementById('menu-item-report');
  const rawForm = document.getElementById('report-theater-form');
  const rawReason = document.getElementById('report-theater-reason');
  const rawDetails = document.getElementById('report-theater-details');
  const result = document.getElementById('report-theater-result');
  const description = document.getElementById('report-theater-description');
  const rawSubmit = document.getElementById('report-theater-submit');
  const rawCancel = document.getElementById('report-theater-close');

  if (
    !modal ||
    !trigger ||
    !(rawForm instanceof HTMLFormElement) ||
    !(rawReason instanceof HTMLSelectElement) ||
    !(rawDetails instanceof HTMLTextAreaElement) ||
    !result ||
    !description ||
    !(rawSubmit instanceof HTMLButtonElement) ||
    !(rawCancel instanceof HTMLButtonElement)
  ) {
    return;
  }

  const modalEl = modal;
  const formEl = rawForm;
  const reasonEl = rawReason;
  const detailsEl = rawDetails;
  const resultEl = result;
  const descriptionEl = description;
  const submitEl = rawSubmit;
  const cancelEl = rawCancel;

  let pending = false;
  let completed = false;
  let previousFocus: HTMLElement | null = null;

  function isFeedback(): boolean {
    return reasonEl.value === 'bug' || reasonEl.value === 'suggestion';
  }

  function updateDescription(): void {
    descriptionEl.textContent = isFeedback()
      ? 'Your feedback will be saved with this theater as context.'
      : 'Malicious activity reports include a theater snapshot for review.';
    detailsEl.placeholder =
      reasonEl.value === 'bug'
        ? 'Steps to reproduce, what you expected, and what happened instead.'
        : reasonEl.value === 'suggestion'
          ? 'Describe your idea and how it would help.'
          : 'Describe the activity you want us to review.';
  }

  reasonEl.addEventListener('change', updateDescription);

  function close(): void {
    if (pending) return;
    modalEl.classList.remove('active');
    modalEl.setAttribute('aria-hidden', 'true');
    modalEl.inert = true;
    previousFocus?.focus();
  }

  trigger.addEventListener('click', () => {
    const toggleBtn = document.getElementById('menu-left-toggle-btn');
    const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    previousFocus = toggleBtn ?? active;
    closeMenu();
    formEl.reset();
    updateDescription();
    completed = false;
    submitEl.disabled = false;
    submitEl.hidden = false;
    reasonEl.disabled = false;
    detailsEl.disabled = false;
    resultEl.textContent = '';
    cancelEl.textContent = 'Cancel';
    modalEl.inert = false;
    modalEl.classList.add('active');
    modalEl.setAttribute('aria-hidden', 'false');
    reasonEl.focus();
  });

  cancelEl.addEventListener('click', close);

  modalEl.addEventListener('click', (event: MouseEvent) => {
    if (event.target === modalEl) {
      close();
    }
  });

  modalEl.addEventListener('keydown', (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
    if (event.key === 'Tab') {
      const focusable = Array.from(
        formEl.querySelectorAll<FocusableFormControl>('select, textarea, button'),
      ).filter((element) => !element.disabled && !element.hidden);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) {
        event.preventDefault();
        return;
      }
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      }
      if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });

  formEl.addEventListener('submit', async (event: SubmitEvent) => {
    event.preventDefault();
    if (pending || completed) return;

    const trimmedDetails = detailsEl.value.trim();
    if (!trimmedDetails) {
      resultEl.textContent = 'Describe the problem before sending your report.';
      detailsEl.focus();
      return;
    }

    pending = true;
    submitEl.disabled = true;
    cancelEl.disabled = true;
    resultEl.textContent = 'Sending report…';

    try {
      const feedback = isFeedback();
      if (!feedback && !theaterId) {
        throw new Error('Could not save your report. Please try again.');
      }
      const endpoint = feedback
        ? '/api/reports'
        : `/api/theaters/${encodeURIComponent(theaterId ?? '')}/reports`;
      const payload: TheaterReportPayload = feedback
        ? { category: reasonEl.value, details: trimmedDetails, theater_id: theaterId || null }
        : { reason: reasonEl.value, details: trimmedDetails };

      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const body: unknown = await response.json();
      if (!response.ok) {
        const detail =
          typeof body === 'object' && body !== null && 'detail' in body
            ? (body as ReportErrorResponse).detail
            : undefined;
        throw new Error(
          typeof detail === 'string' ? detail : 'Could not save your report. Please try again.',
        );
      }

      completed = true;
      resultEl.textContent = feedback
        ? 'Feedback sent. Thank you for helping improve Narratron.'
        : 'Report sent. Thank you for helping keep theaters safe.';
      submitEl.hidden = true;
      reasonEl.disabled = true;
      detailsEl.disabled = true;
      cancelEl.textContent = 'Done';
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : '';
      resultEl.textContent = message || 'Could not save your report. Please try again.';
    } finally {
      pending = false;
      submitEl.disabled = completed;
      cancelEl.disabled = false;
      if (completed) {
        cancelEl.focus();
      }
    }
  });
}
