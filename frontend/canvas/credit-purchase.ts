/** Shared credit purchasing flow for Deploy and Theater Builder. */

import { checkAuthStatus, openAuthModal } from './auth-flow.js';

export type CreditPackageId = 'starter' | 'pro' | 'ultra';

export interface CreditPackage {
  readonly id: CreditPackageId;
  readonly credits: number;
  readonly usd: number;
}

interface BuyCreditsRequestPayload {
  payment_method: string;
  checkout_mode: boolean;
  package_id?: CreditPackageId;
  card_name?: string;
  card_number?: string;
  card_exp?: string;
  card_cvc?: string;
}

interface BuyCreditsResponse {
  readonly status?: string;
  readonly mode?: string;
  readonly checkout_url?: string;
  readonly detail?: string;
  readonly credits_added?: number;
  readonly user?: {
    readonly credits?: number;
  };
}

declare global {
  interface Window {
    openPricingModal?: () => void;
    beforeCreditPurchase?: () => void | Promise<void>;
    closeBuyCreditsModal?: () => void;
    toggleCardFields?: (show: boolean) => void;
    selectCreditPackage?: (pkgId: CreditPackageId, credits: number, usd: number) => void;
    submitBuyCredits?: () => Promise<void>;
  }
}

let creditPurchasePreviousFocus: HTMLElement | null = null;
let selectedPackage: CreditPackage = { id: 'pro', credits: 400, usd: 18.0 };

function updateRatesButtonVisibility(modal: Element): void {
  const ratesBtn = modal.querySelector('[data-credit-rates]');
  if (ratesBtn instanceof HTMLElement) {
    ratesBtn.hidden = typeof window.openPricingModal !== 'function';
  }
}

export function ensureBuyCreditsModalDOM(): void {
  if (document.getElementById('buyCreditsModal')) return;
  const container = document.createElement('div');
  container.innerHTML = `<div class="modal-overlay" id="buyCreditsModal">
    <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="buy-credits-title" tabindex="-1" style="max-width: 520px; width: 90%;">
        <div class="credit-purchase-heading">
            <h3 id="buy-credits-title" style="margin: 0; font-size: 1.3rem; display: flex; align-items: center; gap: 8px;">
                ⚡ Buy Credits
            </h3>
            <div class="credit-purchase-heading-actions">
                <button data-credit-rates type="button" class="btn btn-secondary btn-sm" style="font-size: 0.75rem; padding: 3px 8px;">🏷️ Rates</button>
                <span id="buyCreditsCurrentBalance"
                    style="font-size: 0.85rem; color: #34d399; font-weight: 600; background: rgba(16, 185, 129, 0.15); padding: 4px 10px; border-radius: 99px;">
                    Balance: 0.0 Cr
                </span>
            </div>
        </div>

        <div id="buyCreditsAlertBanner"
            style="display: none; background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); color: #f87171; padding: 10px 14px; border-radius: 8px; font-size: 0.85rem; margin-bottom: 1rem;">
        </div>

        <p style="color: var(--text-secondary); font-size: 0.88rem; margin-bottom: 1rem; line-height: 1.4;">
            Select a credit package below to power canvas deployments and storytelling features.
        </p>

        <div class="packages-grid">
            <div class="package-card" id="pkgStarter">
                <div class="package-title">Starter</div>
                <div class="package-credits">⚡ 100</div>
                <div class="package-price">$5.00</div>
                <div class="package-subtext">$0.05 / credit</div>
            </div>

            <div class="package-card selected" id="pkgPro">
                <div class="package-badge">POPULAR (10% BONUS)</div>
                <div class="package-title">Pro Pack</div>
                <div class="package-credits">⚡ 400</div>
                <div class="package-price">$18.00</div>
                <div class="package-subtext">$0.045 / credit</div>
            </div>

            <div class="package-card" id="pkgUltra">
                <div class="package-badge">25% BONUS</div>
                <div class="package-title">Ultra Pack</div>
                <div class="package-credits">⚡ 1000</div>
                <div class="package-price">$40.00</div>
                <div class="package-subtext">$0.04 / credit</div>
            </div>
        </div>

        <div
            style="background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 10px; padding: 1rem; margin-bottom: 1.25rem;">
            <div
                style="font-size: 0.8rem; font-weight: 700; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 0.75rem;">
                Secure Payment</div>
            <div style="display: flex; gap: 1rem; margin-bottom: 0.75rem;">
                <label
                    style="display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: 0.88rem; color: #f8fafc;">
                    <input type="radio" name="paymentMethod" value="stripe_checkout" checked> 💳 Credit / Debit Card via Stripe Checkout
                </label>
                <label style="display: none;">
                    <input type="radio" name="paymentMethod" value="paypal_mock">
                    🅿️ PayPal
                </label>
            </div>
            <!-- Simulated Card Input Fields -->
            <div id="cardFieldsContainer" style="display: none;">
                <input type="text" id="cardHolderName" placeholder="Cardholder Name" autocomplete="cc-name"
                    style="width: 100%; background: #0f172a; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 7px 10px; color: white; font-size: 0.85rem;">
                <input type="text" id="cardNumber" placeholder="Card Number (•••• •••• •••• 4242)"
                    autocomplete="cc-number"
                    style="width: 100%; background: #0f172a; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 7px 10px; color: white; font-size: 0.85rem;">
                <div style="display: flex; gap: 10px;">
                    <input type="text" id="cardExp" placeholder="MM / YY" autocomplete="cc-exp"
                        style="flex: 1; background: #0f172a; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 7px 10px; color: white; font-size: 0.85rem; text-align: center;">
                    <input type="text" id="cardCvc" placeholder="CVC / CVV" autocomplete="cc-csc"
                        style="flex: 1; background: #0f172a; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 7px 10px; color: white; font-size: 0.85rem; text-align: center;">
                </div>
            </div>
        </div>

        <button class="btn btn-primary" id="btnCompletePurchase" type="button"
            style="width: 100%; font-size: 1rem; padding: 0.75rem;">
            Pay $18.00 & Add 400 Credits
        </button>

        <div class="modal-error" id="buyCreditsModalError"></div>
        <div class="modal-success" id="buyCreditsModalSuccess"
            style="display:none; color: #4ade80; background: rgba(74,222,128,0.1); border: 1px solid rgba(74,222,128,0.2); padding: 0.75rem; border-radius: 8px; font-size: 0.88rem; margin-top: 1rem; text-align: center;">
        </div>

        <button class="btn btn-secondary btn-sm" type="button" data-credit-cancel style="width: 100%; margin-top: 0.75rem;">Cancel</button>
    </div>
</div>`;
  const modal = container.firstElementChild;
  if (!(modal instanceof HTMLElement)) return;
  updateRatesButtonVisibility(modal);
  document.body.appendChild(modal);

  modal.querySelector('[data-credit-rates]')?.addEventListener('click', () => {
    closeBuyCreditsModal();
    window.openPricingModal?.();
  });
  modal.querySelector('#pkgStarter')?.addEventListener('click', () => {
    selectCreditPackage('starter', 100, 5.0);
  });
  modal.querySelector('#pkgPro')?.addEventListener('click', () => {
    selectCreditPackage('pro', 400, 18.0);
  });
  modal.querySelector('#pkgUltra')?.addEventListener('click', () => {
    selectCreditPackage('ultra', 1000, 40.0);
  });
  modal.querySelectorAll('input[name="paymentMethod"]').forEach((radio) => {
    radio.addEventListener('click', () => toggleCardFields(false));
  });
  modal.querySelector('#btnCompletePurchase')?.addEventListener('click', () => {
    void submitBuyCredits();
  });
  modal.querySelector('[data-credit-cancel]')?.addEventListener('click', closeBuyCreditsModal);

  modal.addEventListener('click', (event: MouseEvent) => {
    if (event.target === modal) closeBuyCreditsModal();
  });
  modal.addEventListener('keydown', (event: KeyboardEvent) => {
    if (event.key === 'Escape') closeBuyCreditsModal();
    if (event.key !== 'Tab') return;
    const controls = Array.from(modal.querySelectorAll<HTMLElement>('button, input, a[href]')).filter(
      (control) =>
        !(control instanceof HTMLButtonElement && control.disabled) &&
        !(control instanceof HTMLInputElement && control.disabled) &&
        control.getClientRects().length > 0,
    );
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (!first || !last) return;
    const active = document.activeElement;
    if (event.shiftKey && (active === first || (active instanceof Element && active.matches('[role=dialog]')))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  });
}

export function openBuyCreditsModal(reason: string | null = null): void {
  if (!window.currentUser) {
    openAuthModal('login');
    return;
  }
  ensureBuyCreditsModalDOM();
  const modal = document.getElementById('buyCreditsModal');
  const alertBanner = document.getElementById('buyCreditsAlertBanner');
  const balanceSpan = document.getElementById('buyCreditsCurrentBalance');
  const err = document.getElementById('buyCreditsModalError');
  const succ = document.getElementById('buyCreditsModalSuccess');
  if (!modal || !alertBanner || !err || !succ) return;

  updateRatesButtonVisibility(modal);

  if (balanceSpan && window.currentUser) {
    balanceSpan.textContent = `Balance: ${(window.currentUser.credits ?? 0).toFixed(1)} Cr`;
  }

  if (reason) {
    alertBanner.textContent = reason;
    alertBanner.style.display = 'block';
  } else {
    alertBanner.style.display = 'none';
  }

  err.style.display = 'none';
  succ.style.display = 'none';

  selectCreditPackage('pro', 400, 18.0);
  creditPurchasePreviousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  modal.classList.add('active');
  const dialog = modal.querySelector('[role=dialog]');
  if (dialog instanceof HTMLElement) {
    dialog.focus();
  }
}

export function closeBuyCreditsModal(): void {
  document.getElementById('buyCreditsModal')?.classList.remove('active');
  creditPurchasePreviousFocus?.focus();
}

export function toggleCardFields(show: boolean): void {
  const container = document.getElementById('cardFieldsContainer');
  if (container) {
    container.style.display = show ? 'flex' : 'none';
  }
}

export function selectCreditPackage(pkgId: CreditPackageId, credits: number, usd: number): void {
  for (const id of ['pkgStarter', 'pkgPro', 'pkgUltra']) {
    document.getElementById(id)?.classList.remove('selected');
  }

  selectedPackage = { id: pkgId, credits, usd };
  const currentId = pkgId === 'starter' ? 'pkgStarter' : pkgId === 'pro' ? 'pkgPro' : 'pkgUltra';
  document.getElementById(currentId)?.classList.add('selected');

  const payBtn = document.getElementById('btnCompletePurchase');
  if (payBtn) {
    payBtn.textContent = `Pay $${usd.toFixed(2)} & Add ${credits} Credits`;
  }
}

function getInputText(id: string): string {
  const el = document.getElementById(id);
  return el instanceof HTMLInputElement ? el.value : '';
}

export async function submitBuyCredits(): Promise<void> {
  const err = document.getElementById('buyCreditsModalError');
  const succ = document.getElementById('buyCreditsModalSuccess');
  const payBtn = document.getElementById('btnCompletePurchase');
  if (!err || !succ || !(payBtn instanceof HTMLButtonElement)) return;

  err.style.display = 'none';
  succ.style.display = 'none';

  const methodEl = document.querySelector('input[name="paymentMethod"]:checked');
  const paymentMethod = methodEl instanceof HTMLInputElement ? methodEl.value : 'stripe_checkout';

  const bodyPayload: BuyCreditsRequestPayload = {
    payment_method: paymentMethod,
    checkout_mode: true,
  };

  if (paymentMethod.startsWith('card')) {
    const cardName = getInputText('cardHolderName').trim();
    const cardNumber = getInputText('cardNumber').replace(/[\s-]/g, '');
    const cardExp = getInputText('cardExp').trim();
    const cardCvc = getInputText('cardCvc').trim();

    if (!cardName) {
      err.textContent = 'Please enter Cardholder Name.';
      err.style.display = 'block';
      return;
    }
    if (!cardNumber || !/^\d{13,19}$/.test(cardNumber.replace(/•/g, ''))) {
      err.textContent = 'Please enter a valid credit card number (13-19 digits).';
      err.style.display = 'block';
      return;
    }
    if (!cardExp || !/^(0[1-9]|1[0-2])\/?([0-9]{2})$/.test(cardExp.replace(/\s/g, ''))) {
      err.textContent = 'Please enter a valid expiration date (MM/YY).';
      err.style.display = 'block';
      return;
    }
    if (!cardCvc || !/^\d{3,4}$/.test(cardCvc)) {
      err.textContent = 'Please enter a valid 3 or 4-digit CVV / CVC code.';
      err.style.display = 'block';
      return;
    }

    bodyPayload.card_name = cardName;
    bodyPayload.card_number = cardNumber;
    bodyPayload.card_exp = cardExp;
    bodyPayload.card_cvc = cardCvc;
  }

  payBtn.disabled = true;
  payBtn.textContent = 'Processing One-Off Payment...';

  try {
    if (typeof window.beforeCreditPurchase === 'function') await window.beforeCreditPurchase();
    bodyPayload.package_id = selectedPackage.id;

    const res = await fetch('/api/payments/buy-credits', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(bodyPayload),
    });

    const data = (await res.json()) as BuyCreditsResponse;
    if (!res.ok || data.status !== 'ok') {
      err.textContent = data.detail || 'Payment failed. Please try again.';
      err.style.display = 'block';
    } else if (data.mode === 'stripe_checkout' && data.checkout_url) {
      succ.textContent = '🚀 Redirecting to secure Stripe Checkout...';
      succ.style.display = 'block';
      const checkoutUrl = data.checkout_url;
      setTimeout(() => {
        window.location.href = checkoutUrl;
      }, 600);
    } else {
      succ.textContent = `🎉 Payment successful! Added +${(data.credits_added ?? 0).toFixed(1)} Credits.`;
      succ.style.display = 'block';

      if (window.currentUser) {
        window.currentUser.credits = data.user?.credits ?? window.currentUser.credits;
        const balanceSpan = document.getElementById('buyCreditsCurrentBalance');
        if (balanceSpan) {
          balanceSpan.textContent = `Balance: ${(window.currentUser.credits ?? 0).toFixed(1)} Cr`;
        }
        await checkAuthStatus({ refresh: true });
      }

      setTimeout(() => {
        closeBuyCreditsModal();
      }, 1800);
    }
  } catch (e: unknown) {
    err.textContent =
      e instanceof Error && e.message ? e.message : 'Network error during payment processing.';
    err.style.display = 'block';
  } finally {
    payBtn.disabled = false;
    payBtn.textContent = `Pay $${selectedPackage.usd.toFixed(2)} & Add ${selectedPackage.credits.toFixed(1)} Credits`;
  }
}

window.openBuyCreditsModal = openBuyCreditsModal;
window.closeBuyCreditsModal = closeBuyCreditsModal;
window.toggleCardFields = toggleCardFields;
window.selectCreditPackage = selectCreditPackage;
window.submitBuyCredits = submitBuyCredits;
