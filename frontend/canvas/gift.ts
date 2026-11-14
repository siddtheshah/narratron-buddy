/** Credit gift landing page: validates gift token and claims credits on sign-in. */

export interface GiftUserSummary {
  readonly id?: number;
  readonly username?: string;
  readonly credits?: number;
}

export interface GiftAuthState {
  readonly authenticated?: boolean;
  readonly user?: GiftUserSummary | null;
}

export interface GiftDetails {
  readonly status: string;
  readonly claimed?: boolean;
  readonly credits?: number;
}

interface GiftClaimResponse {
  readonly credits?: number;
  readonly detail?: unknown;
}

interface ApiErrorBody {
  readonly detail?: unknown;
}

export interface GiftPageController {
  loadGift(): Promise<void>;
  claimGift(): Promise<void>;
  destroy(): void;
}

declare global {
  interface Window {
    currentUser?: GiftUserSummary | null;
    openAuthModal?: (tab?: string) => void;
    checkAuthStatus?: (options?: { refresh?: boolean }) => Promise<GiftAuthState | undefined>;
    invalidateAuthState?: () => void;
  }
}

async function readErrorDetail(response: Response): Promise<string | null> {
  const text = await response.text();
  if (!text.trim()) {
    return null;
  }
  try {
    const parsed = JSON.parse(text) as ApiErrorBody;
    return typeof parsed.detail === 'string' && parsed.detail ? parsed.detail : null;
  } catch {
    return null;
  }
}

export function initGiftPage(): GiftPageController | null {
  const rawMessage = document.getElementById('message');
  const rawClaim = document.getElementById('claim');
  if (!(rawMessage instanceof HTMLElement) || !(rawClaim instanceof HTMLButtonElement)) {
    return null;
  }

  const messageEl = rawMessage;
  const claimBtn = rawClaim;
  const rawSegment = window.location.pathname.split('/').pop() ?? '';
  const token = decodeURIComponent(rawSegment);
  let gift: GiftDetails | null = null;

  function show(text: string, kind: 'muted' | 'error' | 'success' = 'muted'): void {
    messageEl.textContent = text;
    messageEl.className = kind;
  }

  async function claimGift(): Promise<void> {
    if (claimBtn.disabled) return;
    claimBtn.disabled = true;
    show('Claiming your credits…');
    const res = await fetch(`/api/credit-gifts/${encodeURIComponent(token)}/claim`, {
      method: 'POST',
    });
    if (!res.ok) {
      const detail = await readErrorDetail(res);
      show(detail ?? 'This gift could not be claimed.', 'error');
      claimBtn.style.display = 'none';
      return;
    }
    const data = (await res.json()) as GiftClaimResponse;
    show(
      `Success! ${Number(data.credits).toFixed(1)} credits were added to your account. You and the sender are now friends.`,
      'success',
    );
    claimBtn.style.display = 'none';
    window.invalidateAuthState?.();
    await window.checkAuthStatus?.({ refresh: true });
  }

  async function loadGift(): Promise<void> {
    const res = await fetch(`/api/credit-gifts/${encodeURIComponent(token)}`);
    if (!res.ok) {
      const detail = await readErrorDetail(res);
      show(detail ?? 'Gift link not found.', 'error');
      return;
    }
    const data = (await res.json()) as GiftDetails;
    gift = data;
    if (gift.status !== 'pending' || gift.claimed) {
      show(
        gift.status === 'expired'
          ? 'This gift link has expired.'
          : 'This gift has already been claimed.',
        'error',
      );
      return;
    }
    const auth = await window.checkAuthStatus?.({ refresh: true });
    if (auth?.authenticated) {
      await claimGift();
    } else {
      show(
        `This link gives you ${Number(gift.credits).toFixed(1)} credits. Sign in or create an account to claim it.`,
        'muted',
      );
      claimBtn.style.display = 'inline-block';
      claimBtn.textContent = 'Sign in to claim';
    }
  }

  const onClaimClick = (): void => {
    if (window.currentUser) {
      void claimGift();
    } else {
      window.openAuthModal?.('login');
    }
  };

  const onAuthChanged = (event: Event): void => {
    const detail = (event as CustomEvent<GiftAuthState | undefined>).detail;
    if (gift?.status === 'pending' && detail?.authenticated && !claimBtn.disabled) {
      void claimGift();
    }
  };

  claimBtn.addEventListener('click', onClaimClick);
  window.addEventListener('narratron:auth-changed', onAuthChanged);

  void loadGift();

  return {
    loadGift,
    claimGift,
    destroy(): void {
      claimBtn.removeEventListener('click', onClaimClick);
      window.removeEventListener('narratron:auth-changed', onAuthChanged);
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initGiftPage();
  });
} else {
  initGiftPage();
}
