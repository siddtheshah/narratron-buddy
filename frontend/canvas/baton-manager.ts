export interface BatonUser {
  readonly id: string | number;
  readonly username?: string | null;
  readonly name?: string | null;
}

export interface BatonRequest {
  readonly target_user_id: string | number;
  readonly requester_id?: string | number | null;
  readonly timeout_seconds?: number;
}

export interface BatonState {
  readonly owner?: BatonUser | null;
  readonly active_orator?: BatonUser | null;
  readonly contributors?: readonly BatonUser[] | null;
  readonly baton_request?: BatonRequest | null;
  readonly active_viewers?: readonly BatonUser[] | null;
  readonly detail?: string | null;
}

export interface BatonUpdateContext {
  readonly batonState: BatonState;
  readonly activeViewers: readonly BatonUser[];
  readonly isOwner: boolean;
  readonly isActiveOrator: boolean;
  readonly isContributor: boolean;
  readonly isNowOrator: boolean;
}

export interface CreateBatonManagerOptions {
  readonly theaterId: string | null;
  readonly getCurrentUser: () => BatonUser | null;
  readonly batonStatusBadge: HTMLButtonElement | null;
  readonly menuTakeBackBatonBtn: HTMLElement | null;
  readonly oratorsModal: HTMLElement | null;
  readonly oratorsCloseBtn: HTMLElement | null;
  readonly acceptBatonBtn: HTMLElement | null;
  readonly declineBatonBtn: HTMLElement | null;
  readonly activeViewersContainer?: HTMLElement | null;
  readonly contributorsContainer?: HTMLElement | null;
  readonly batonRequestModal?: HTMLElement | null;
  readonly batonTimerBar?: HTMLElement | null;
  readonly onRoleChange: (isNowOrator: boolean) => void;
  readonly onBatonStateUpdated?: (context: BatonUpdateContext) => void;
}

export interface BatonManagerController {
  getCurrentBatonState(): BatonState | null;
  getActiveViewers(): readonly BatonUser[];
  checkBatonState(): Promise<BatonState | null>;
  updateBatonStateUI(batonState: BatonState, activeViewers?: readonly BatonUser[] | null): void;
  showBatonRequestModal(batonReq: BatonRequest): void;
  hideBatonRequestModal(): void;
  acceptBatonPass(): Promise<void>;
  declineBatonPass(): Promise<void>;
  takeBackBaton(): Promise<void>;
  renderOratorManagementModalContent(): void;
  addContributor(targetUserId: string | number): Promise<void>;
  removeContributor(targetUserId: string | number): Promise<void>;
  requestBatonPass(targetUserId: string | number): Promise<void>;
  destroy(): void;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Coordinate theater baton state, contributor permissions, 30-second baton
 * pass requests, and the owner's orator management modal.
 */
export function createBatonManager(
  options: CreateBatonManagerOptions,
): BatonManagerController {
  const {
    theaterId,
    getCurrentUser,
    batonStatusBadge,
    menuTakeBackBatonBtn,
    oratorsModal,
    oratorsCloseBtn,
    acceptBatonBtn,
    declineBatonBtn,
    activeViewersContainer = document.getElementById('active-viewers-list'),
    contributorsContainer = document.getElementById('contributors-list'),
    batonRequestModal = document.getElementById('baton-request-modal'),
    batonTimerBar = document.getElementById('baton-timer-bar'),
    onRoleChange,
    onBatonStateUpdated,
  } = options;

  let currentBatonState: BatonState | null = null;
  let activeViewersList: readonly BatonUser[] = [];
  let batonTimerInterval: ReturnType<typeof setInterval> | null = null;

  function hideBatonRequestModal(): void {
    if (batonRequestModal) {
      batonRequestModal.classList.remove('active');
      batonRequestModal.setAttribute('aria-hidden', 'true');
    }
    if (batonTimerInterval !== null) {
      clearInterval(batonTimerInterval);
      batonTimerInterval = null;
    }
  }

  function showBatonRequestModal(_batonReq: BatonRequest): void {
    if (!batonRequestModal) return;
    if (batonRequestModal.classList.contains('active')) return;

    batonRequestModal.classList.add('active');
    batonRequestModal.setAttribute('aria-hidden', 'false');

    const totalMs = 30000;
    const startTime = Date.now();

    if (batonTimerInterval !== null) {
      clearInterval(batonTimerInterval);
    }

    batonTimerInterval = setInterval(() => {
      const elapsed = Date.now() - startTime;
      const remaining = Math.max(0, totalMs - elapsed);
      const pct = (remaining / totalMs) * 100;
      if (batonTimerBar) {
        batonTimerBar.style.width = `${pct}%`;
      }

      if (remaining <= 0) {
        if (batonTimerInterval !== null) {
          clearInterval(batonTimerInterval);
          batonTimerInterval = null;
        }
        void declineBatonPass();
      }
    }, 100);
  }

  function renderOratorManagementModalContent(): void {
    if (!currentBatonState) return;

    const allowedIds = (currentBatonState.contributors || []).map((u) => String(u.id));
    const ownerId = currentBatonState.owner ? String(currentBatonState.owner.id) : null;

    if (activeViewersContainer) {
      const viewers = activeViewersList.filter((u) => String(u.id) !== ownerId);
      if (viewers.length === 0) {
        activeViewersContainer.innerHTML =
          '<div style="font-size:0.85rem; color:#64748b; padding:6px;">No other connected viewers online.</div>';
      } else {
        activeViewersContainer.innerHTML = viewers
          .map((v) => {
            const isAllowed = allowedIds.includes(String(v.id));
            const safeName = escapeHtml(String(v.username || v.name || 'Viewer'));
            const safeId = escapeHtml(String(v.id));
            return `
              <div style="display:flex; align-items:center; justify-content:space-between; padding:6px 10px; background:rgba(255,255,255,0.05); border-radius:6px;">
                <span style="font-size:0.9rem; color:#fff;">👤 @${safeName}</span>
                ${
                  isAllowed
                    ? '<span style="font-size:0.75rem; color:#4ade80; font-weight:600;">Allowed</span>'
                    : `<button type="button" data-baton-action="add-contributor" data-user-id="${safeId}" style="background:#6366f1; border:none; color:#fff; padding:4px 10px; border-radius:12px; font-size:0.75rem; cursor:pointer;">+ Contributor</button>`
                }
              </div>
            `;
          })
          .join('');
      }
    }

    if (contributorsContainer) {
      const allowedList = currentBatonState.contributors || [];
      if (allowedList.length === 0) {
        contributorsContainer.innerHTML =
          '<div style="font-size:0.85rem; color:#64748b; padding:6px;">No contributor added yet.</div>';
      } else {
        contributorsContainer.innerHTML = allowedList
          .map((u) => {
            const isTargeting =
              Boolean(currentBatonState?.baton_request) &&
              String(currentBatonState?.baton_request?.target_user_id) === String(u.id);
            const safeName = escapeHtml(String(u.username || u.name || 'Contributor'));
            const safeId = escapeHtml(String(u.id));
            return `
              <div style="display:flex; align-items:center; justify-content:space-between; padding:6px 10px; background:rgba(255,255,255,0.05); border-radius:6px;">
                <span style="font-size:0.9rem; color:#fff;">🎙️ @${safeName}</span>
                <div style="display:flex; gap:6px;">
                  ${
                    isTargeting
                      ? '<span style="font-size:0.75rem; color:#fbbf24; padding:3px 8px; font-weight:600;">Pending 30s...</span>'
                      : `<button type="button" data-baton-action="pass-baton" data-user-id="${safeId}" style="background:#f59e0b; border:none; color:#fff; padding:4px 10px; border-radius:12px; font-size:0.75rem; cursor:pointer;">Pass Baton 🎙️</button>`
                  }
                  <button type="button" data-baton-action="remove-contributor" data-user-id="${safeId}" style="background:rgba(239,68,68,0.2); border:1px solid rgba(239,68,68,0.4); color:#f87171; padding:4px 8px; border-radius:12px; font-size:0.75rem; cursor:pointer;">Remove</button>
                </div>
              </div>
            `;
          })
          .join('');
      }
    }
  }

  function updateBatonStateUI(
    batonState: BatonState,
    activeViewers?: readonly BatonUser[] | null,
  ): void {
    currentBatonState = batonState;
    activeViewersList = activeViewers || [];

    const currentUser = getCurrentUser();
    const isOwner = Boolean(
      currentUser && batonState.owner && currentUser.id === batonState.owner.id,
    );
    const isActiveOrator = Boolean(
      currentUser && batonState.active_orator && currentUser.id === batonState.active_orator.id,
    );
    const isContributorInList = Boolean(
      currentUser && (batonState.contributors || []).some((u) => u.id === currentUser.id),
    );
    const isContributor = Boolean(isOwner || isActiveOrator || isContributorInList);
    const isNowOrator = Boolean(isActiveOrator || (isOwner && !batonState.active_orator));

    onBatonStateUpdated?.({
      batonState,
      activeViewers: activeViewersList,
      isOwner,
      isActiveOrator,
      isContributor,
      isNowOrator,
    });

    if (batonStatusBadge) {
      const holder = batonState.active_orator || batonState.owner;
      const holderName = (holder && (holder.username || holder.name)) || 'Unknown';
      batonStatusBadge.textContent = `👑 @${holderName}`;
      batonStatusBadge.disabled = !isOwner;
      batonStatusBadge.style.cursor = isOwner ? 'pointer' : 'default';
      batonStatusBadge.title = isOwner ? 'Open baton menu' : `Baton is held by @${holderName}`;
      batonStatusBadge.style.background = isOwner ? '#ec4899' : 'rgba(236, 72, 153, 0.15)';
      batonStatusBadge.style.color = isOwner ? '#fff' : '#f9a8d4';
      batonStatusBadge.style.borderColor = isOwner ? '#fbcfe8' : 'rgba(236, 72, 153, 0.3)';
      batonStatusBadge.style.boxShadow = isOwner ? '0 0 12px rgba(236, 72, 153, 0.45)' : 'none';
    }

    if (menuTakeBackBatonBtn) {
      menuTakeBackBatonBtn.style.display = isOwner && !isActiveOrator ? 'inline-flex' : 'none';
    }

    onRoleChange(isNowOrator);

    if (
      batonState.baton_request &&
      currentUser &&
      batonState.baton_request.target_user_id === currentUser.id
    ) {
      showBatonRequestModal(batonState.baton_request);
    } else {
      hideBatonRequestModal();
    }

    if (oratorsModal && oratorsModal.classList.contains('active')) {
      renderOratorManagementModalContent();
    }
  }

  async function checkBatonState(): Promise<BatonState | null> {
    if (!theaterId) return null;
    try {
      const res = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/baton`);
      if (!res.ok) return null;
      const data = (await res.json()) as BatonState | null;
      if (data) {
        updateBatonStateUI(data, data.active_viewers || []);
      }
      return data;
    } catch (err) {
      console.error('Error fetching baton state:', err);
      return null;
    }
  }

  async function acceptBatonPass(): Promise<void> {
    if (!theaterId) return;
    try {
      const res = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/baton/accept`, {
        method: 'POST',
      });
      const data = (await res.json()) as BatonState | null;
      hideBatonRequestModal();
      if (data && !data.detail) {
        updateBatonStateUI(data, data.active_viewers);
      }
    } catch (err) {
      console.error('Error accepting baton:', err);
    }
  }

  async function declineBatonPass(): Promise<void> {
    if (!theaterId) return;
    try {
      const res = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/baton/decline`, {
        method: 'POST',
      });
      const data = (await res.json()) as BatonState | null;
      hideBatonRequestModal();
      if (data && !data.detail) {
        updateBatonStateUI(data, data.active_viewers);
      }
    } catch (err) {
      console.error('Error declining baton:', err);
    }
  }

  async function takeBackBaton(): Promise<void> {
    if (!theaterId) return;
    try {
      const res = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/baton/takeback`, {
        method: 'POST',
      });
      const data = (await res.json()) as BatonState | null;
      if (data && !data.detail) {
        updateBatonStateUI(data, data.active_viewers);
      }
    } catch (err) {
      console.error('Error taking back baton:', err);
    }
  }

  async function addContributor(targetUserId: string | number): Promise<void> {
    if (!theaterId) return;
    const res = await fetch(
      `/api/theaters/${encodeURIComponent(theaterId)}/baton/contributors`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_user_id: targetUserId }),
      },
    );
    const data = (await res.json()) as BatonState;
    if (!res.ok) {
      throw new Error(data.detail || `Failed to add contributor (${res.status})`);
    }
    updateBatonStateUI(data, data.active_viewers);
  }

  async function removeContributor(targetUserId: string | number): Promise<void> {
    if (!theaterId) return;
    const res = await fetch(
      `/api/theaters/${encodeURIComponent(theaterId)}/baton/contributors/${encodeURIComponent(String(targetUserId))}`,
      { method: 'DELETE' },
    );
    const data = (await res.json()) as BatonState;
    if (!res.ok) {
      throw new Error(data.detail || `Failed to remove contributor (${res.status})`);
    }
    updateBatonStateUI(data, data.active_viewers);
  }

  async function requestBatonPass(targetUserId: string | number): Promise<void> {
    if (!theaterId) return;
    const res = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/baton/request`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_user_id: targetUserId, timeout_seconds: 30 }),
    });
    const data = (await res.json()) as BatonState;
    if (!res.ok) {
      throw new Error(data.detail || `Failed to request baton pass (${res.status})`);
    }
    updateBatonStateUI(data, data.active_viewers);
  }

  function parseUserId(rawId: string | undefined): string | number | null {
    if (!rawId) return null;
    return /^\d+$/.test(rawId) ? Number(rawId) : rawId;
  }

  const handleModalActionClick = (event: MouseEvent): void => {
    const target = event.target as HTMLElement | null;
    const button = target?.closest<HTMLButtonElement>('button[data-baton-action]');
    if (!button) return;
    const action = button.dataset.batonAction;
    const userId = parseUserId(button.dataset.userId);
    if (userId === null) return;
    if (action === 'add-contributor') {
      void addContributor(userId);
    } else if (action === 'pass-baton') {
      void requestBatonPass(userId);
    } else if (action === 'remove-contributor') {
      void removeContributor(userId);
    }
  };

  activeViewersContainer?.addEventListener('click', handleModalActionClick);
  contributorsContainer?.addEventListener('click', handleModalActionClick);

  const handleBadgeClick = (): void => {
    if (!batonStatusBadge || batonStatusBadge.disabled) return;
    if (oratorsModal) {
      oratorsModal.classList.add('active');
      oratorsModal.setAttribute('aria-hidden', 'false');
      renderOratorManagementModalContent();
    }
  };

  const handleOratorsCloseClick = (): void => {
    if (oratorsModal) {
      oratorsModal.classList.remove('active');
      oratorsModal.setAttribute('aria-hidden', 'true');
    }
  };

  const handleTakeBackClick = (): void => {
    void takeBackBaton();
  };

  const handleAcceptClick = (): void => {
    void acceptBatonPass();
  };

  const handleDeclineClick = (): void => {
    void declineBatonPass();
  };

  batonStatusBadge?.addEventListener('click', handleBadgeClick);
  oratorsCloseBtn?.addEventListener('click', handleOratorsCloseClick);
  menuTakeBackBatonBtn?.addEventListener('click', handleTakeBackClick);
  acceptBatonBtn?.addEventListener('click', handleAcceptClick);
  declineBatonBtn?.addEventListener('click', handleDeclineClick);

  return {
    getCurrentBatonState: () => currentBatonState,
    getActiveViewers: () => activeViewersList,
    checkBatonState,
    updateBatonStateUI,
    showBatonRequestModal,
    hideBatonRequestModal,
    acceptBatonPass,
    declineBatonPass,
    takeBackBaton,
    renderOratorManagementModalContent,
    addContributor,
    removeContributor,
    requestBatonPass,
    destroy: () => {
      hideBatonRequestModal();
      activeViewersContainer?.removeEventListener('click', handleModalActionClick);
      contributorsContainer?.removeEventListener('click', handleModalActionClick);
      batonStatusBadge?.removeEventListener('click', handleBadgeClick);
      oratorsCloseBtn?.removeEventListener('click', handleOratorsCloseClick);
      menuTakeBackBatonBtn?.removeEventListener('click', handleTakeBackClick);
      acceptBatonBtn?.removeEventListener('click', handleAcceptClick);
      declineBatonBtn?.removeEventListener('click', handleDeclineClick);
    },
  };
}
