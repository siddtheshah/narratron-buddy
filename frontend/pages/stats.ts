/** System Stats & Analytics page: metrics, bar charts, top theaters, and join-key modal. */

export interface DailyViewsPoint {
  readonly date?: string;
  readonly views?: number;
}

export interface DailyActiveUsersPoint {
  readonly date?: string;
  readonly active_users?: number;
}

export interface TopViewedTheater {
  readonly theater_id: string;
  readonly name?: string;
  readonly views: number;
}

export interface StatsDashboardData {
  readonly total_accounts: number;
  readonly active_users_7d: number;
  readonly total_theater_views: number;
  readonly theater_views_7d: number;
  readonly daily_views_7d?: readonly DailyViewsPoint[];
  readonly daily_active_users_7d?: readonly DailyActiveUsersPoint[];
  readonly top_viewed_theaters?: readonly TopViewedTheater[];
}

interface BarPoint {
  readonly date?: string;
  readonly value: number;
}

interface ResolveJoinKeyResponse {
  readonly theater_id?: string;
  readonly detail?: unknown;
}

export interface StatsPageController {
  fetchStats(): Promise<void>;
  renderDashboard(data: StatsDashboardData): void;
  openJoinKeyModal(theaterId: string): void;
  closeJoinKeyModal(): void;
  submitJoinKey(): Promise<void>;
  destroy(): void;
}

async function readErrorDetail(response: Response): Promise<string | null> {
  const text = await response.text();
  if (!text.trim()) {
    return null;
  }
  try {
    const parsed = JSON.parse(text) as ResolveJoinKeyResponse;
    return typeof parsed.detail === 'string' && parsed.detail ? parsed.detail : null;
  } catch {
    return null;
  }
}

export function initStatsPage(): StatsPageController | null {
  const accountsEl = document.getElementById('val-accounts');
  const active7dEl = document.getElementById('val-active-7d');
  const totalViewsEl = document.getElementById('val-total-views');
  const views7dEl = document.getElementById('val-views-7d');
  const dailyViewsChart = document.getElementById('chart-daily-views');
  const dailyUsersChart = document.getElementById('chart-daily-users');
  const topTheatersBody = document.getElementById('top-theaters-body');
  const rawJoinModal = document.getElementById('join-modal');
  const rawJoinTheaterIdEl = document.getElementById('join-narratron-theater-id');
  const rawJoinKeyInput = document.getElementById('join-key-input');
  const rawJoinError = document.getElementById('join-error');
  const joinCancelBtn = document.getElementById('join-cancel');
  const joinSubmitBtn = document.getElementById('join-submit');

  if (
    !accountsEl ||
    !active7dEl ||
    !totalViewsEl ||
    !views7dEl ||
    !dailyViewsChart ||
    !dailyUsersChart ||
    !topTheatersBody ||
    !(rawJoinModal instanceof HTMLElement) ||
    !rawJoinTheaterIdEl ||
    !(rawJoinKeyInput instanceof HTMLInputElement) ||
    !rawJoinError ||
    !joinCancelBtn ||
    !joinSubmitBtn
  ) {
    return null;
  }

  const valAccounts = accountsEl;
  const valActive7d = active7dEl;
  const valTotalViews = totalViewsEl;
  const valViews7d = views7dEl;
  const viewsChartEl = dailyViewsChart;
  const usersChartEl = dailyUsersChart;
  const theatersTbody = topTheatersBody;
  const joinModal = rawJoinModal;
  const joinTheaterIdEl = rawJoinTheaterIdEl;
  const joinKeyInput = rawJoinKeyInput;
  const joinError = rawJoinError;
  const refreshBtn =
    document.getElementById('refresh-stats-btn') ??
    document.querySelector<HTMLButtonElement>('.controls-row .nav-btn');

  let pendingTheaterId: string | null = null;

  function renderBarChart(container: HTMLElement, points: readonly BarPoint[], isAlt: boolean): void {
    container.replaceChildren();
    if (points.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.textContent = 'No chart data available';
      container.append(empty);
      return;
    }

    const maxVal = Math.max(...points.map((point) => point.value), 1);
    for (const point of points) {
      const pct = Math.max((point.value / maxVal) * 100, 4);
      const dateStr = point.date ? point.date.slice(5) : '';

      const col = document.createElement('div');
      col.className = 'bar-col';

      const wrapper = document.createElement('div');
      wrapper.className = 'bar-wrapper';

      const valEl = document.createElement('div');
      valEl.className = 'bar-val';
      valEl.textContent = String(point.value);

      const fill = document.createElement('div');
      fill.className = isAlt ? 'bar-fill alt' : 'bar-fill';
      fill.style.height = `${pct}%`;

      wrapper.append(valEl, fill);

      const label = document.createElement('div');
      label.className = 'bar-label';
      label.textContent = dateStr;

      col.append(wrapper, label);
      container.append(col);
    }
  }

  function renderTopTheaters(theaters: readonly TopViewedTheater[] | undefined): void {
    theatersTbody.replaceChildren();

    if (!theaters || theaters.length === 0) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 5;
      cell.className = 'empty-state';
      cell.textContent = 'No theater view history recorded yet.';
      row.append(cell);
      theatersTbody.append(row);
      return;
    }

    theaters.forEach((theater, idx) => {
      const row = document.createElement('tr');

      const rankCell = document.createElement('td');
      rankCell.style.fontWeight = '600';
      rankCell.style.color = 'var(--text-muted)';
      rankCell.textContent = `#${idx + 1}`;

      const nameCell = document.createElement('td');
      nameCell.className = 'theater-name';
      nameCell.textContent = theater.name || theater.theater_id;

      const idCell = document.createElement('td');
      const idTag = document.createElement('span');
      idTag.className = 'theater-id-tag';
      idTag.textContent = theater.theater_id;
      idCell.append(idTag);

      const viewsCell = document.createElement('td');
      const viewsBadge = document.createElement('span');
      viewsBadge.className = 'views-badge';
      viewsBadge.textContent = `👁️ ${theater.views}`;
      viewsCell.append(viewsBadge);

      const actionCell = document.createElement('td');
      const viewBtn = document.createElement('button');
      viewBtn.type = 'button';
      viewBtn.className = 'nav-btn view-canvas-btn';
      viewBtn.dataset.narratronTheaterId = theater.theater_id;
      viewBtn.style.padding = '6px 12px';
      viewBtn.style.fontSize = '0.85rem';
      viewBtn.textContent = 'View Canvas';
      viewBtn.addEventListener('click', () => openJoinKeyModal(theater.theater_id));
      actionCell.append(viewBtn);

      row.append(rankCell, nameCell, idCell, viewsCell, actionCell);
      theatersTbody.append(row);
    });
  }

  function renderDashboard(data: StatsDashboardData): void {
    valAccounts.textContent = data.total_accounts.toLocaleString();
    valActive7d.textContent = data.active_users_7d.toLocaleString();
    valTotalViews.textContent = data.total_theater_views.toLocaleString();
    valViews7d.textContent = data.theater_views_7d.toLocaleString();

    const dailyViews: BarPoint[] = (data.daily_views_7d ?? []).map((item) => ({
      date: item.date,
      value: item.views ?? 0,
    }));
    const dailyUsers: BarPoint[] = (data.daily_active_users_7d ?? []).map((item) => ({
      date: item.date,
      value: item.active_users ?? 0,
    }));

    renderBarChart(viewsChartEl, dailyViews, false);
    renderBarChart(usersChartEl, dailyUsers, true);
    renderTopTheaters(data.top_viewed_theaters);
  }

  async function fetchStats(): Promise<void> {
    try {
      const res = await fetch('/api/stats');
      if (!res.ok) {
        throw new Error('Failed to load stats data');
      }
      const data = (await res.json()) as StatsDashboardData;
      renderDashboard(data);
    } catch (error: unknown) {
      console.error(error);
    }
  }

  function openJoinKeyModal(theaterId: string): void {
    pendingTheaterId = theaterId;
    joinTheaterIdEl.textContent = theaterId;
    joinKeyInput.value = '';
    joinError.textContent = '';
    joinModal.hidden = false;
    joinKeyInput.focus();
  }

  function closeJoinKeyModal(): void {
    joinModal.hidden = true;
    pendingTheaterId = null;
  }

  async function submitJoinKey(): Promise<void> {
    const joinKey = joinKeyInput.value.trim();
    joinError.textContent = '';
    if (!joinKey) {
      joinError.textContent = 'Please enter a Join Key.';
      return;
    }

    try {
      const response = await fetch('/api/theaters/resolve-join-key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ join_key: joinKey }),
      });

      if (!response.ok) {
        const detail = await readErrorDetail(response);
        joinError.textContent = detail ?? 'Invalid Join Key.';
        return;
      }

      const data = (await response.json()) as ResolveJoinKeyResponse;
      if (!data.theater_id || data.theater_id !== pendingTheaterId) {
        joinError.textContent = 'That Join Key belongs to a different theater.';
        return;
      }

      window.location.href = `/canvas?theater_id=${encodeURIComponent(data.theater_id)}`;
    } catch {
      joinError.textContent = 'Unable to verify the Join Key. Please try again.';
    }
  }

  const onRefreshClick = (): void => {
    void fetchStats();
  };
  const onCancelClick = (): void => {
    closeJoinKeyModal();
  };
  const onSubmitClick = (): void => {
    void submitJoinKey();
  };
  const onInputKeyDown = (event: KeyboardEvent): void => {
    if (event.key === 'Enter') {
      void submitJoinKey();
    } else if (event.key === 'Escape') {
      closeJoinKeyModal();
    }
  };
  const onModalBackdropClick = (event: MouseEvent): void => {
    if (event.target === joinModal) {
      closeJoinKeyModal();
    }
  };

  refreshBtn?.addEventListener('click', onRefreshClick);
  joinCancelBtn.addEventListener('click', onCancelClick);
  joinSubmitBtn.addEventListener('click', onSubmitClick);
  joinKeyInput.addEventListener('keydown', onInputKeyDown);
  joinModal.addEventListener('click', onModalBackdropClick);

  void fetchStats();
  const refreshInterval = window.setInterval(() => {
    void fetchStats();
  }, 10000);

  return {
    fetchStats,
    renderDashboard,
    openJoinKeyModal,
    closeJoinKeyModal,
    submitJoinKey,
    destroy(): void {
      refreshBtn?.removeEventListener('click', onRefreshClick);
      joinCancelBtn.removeEventListener('click', onCancelClick);
      joinSubmitBtn.removeEventListener('click', onSubmitClick);
      joinKeyInput.removeEventListener('keydown', onInputKeyDown);
      joinModal.removeEventListener('click', onModalBackdropClick);
      window.clearInterval(refreshInterval);
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initStatsPage();
  });
} else {
  initStatsPage();
}
