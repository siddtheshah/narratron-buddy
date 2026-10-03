/* Shared credit purchasing flow for Deploy and Theater Builder. */
let creditPurchasePreviousFocus = null;

function ensureBuyCreditsModalDOM() {
    if (document.getElementById('buyCreditsModal')) return;
    const container = document.createElement('div');
    container.innerHTML = `<div class="modal-overlay" id="buyCreditsModal">
    <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="buy-credits-title" tabindex="-1" style="max-width: 520px; width: 90%;">
        <div class="credit-purchase-heading">
            <h3 id="buy-credits-title" style="margin: 0; font-size: 1.3rem; display: flex; align-items: center; gap: 8px;">
                ⚡ Buy Credits
            </h3>
            <div class="credit-purchase-heading-actions">
                <button data-credit-rates class="btn btn-secondary btn-sm" style="font-size: 0.75rem; padding: 3px 8px;"
                    onclick="closeBuyCreditsModal(); openPricingModal();">🏷️ Rates</button>
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
            <div class="package-card" id="pkgStarter" onclick="selectCreditPackage('starter', 100, 5.00)">
                <div class="package-title">Starter</div>
                <div class="package-credits">⚡ 100</div>
                <div class="package-price">$5.00</div>
                <div class="package-subtext">$0.05 / credit</div>
            </div>

            <div class="package-card selected" id="pkgPro" onclick="selectCreditPackage('pro', 400, 18.00)">
                <div class="package-badge">POPULAR (10% BONUS)</div>
                <div class="package-title">Pro Pack</div>
                <div class="package-credits">⚡ 400</div>
                <div class="package-price">$18.00</div>
                <div class="package-subtext">$0.045 / credit</div>
            </div>

            <div class="package-card" id="pkgUltra" onclick="selectCreditPackage('ultra', 1000, 40.00)">
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
                    <input type="radio" name="paymentMethod" value="stripe_checkout" checked
                        onclick="toggleCardFields(false)"> 💳 Credit / Debit Card via Stripe Checkout
                </label>
                <label style="display: none;">
                    <input type="radio" name="paymentMethod" value="paypal_mock" onclick="toggleCardFields(false)">
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

        <button class="btn btn-primary" id="btnCompletePurchase"
            style="width: 100%; font-size: 1rem; padding: 0.75rem;" onclick="submitBuyCredits()">
            Pay $18.00 & Add 400 Credits
        </button>

        <div class="modal-error" id="buyCreditsModalError"></div>
        <div class="modal-success" id="buyCreditsModalSuccess"
            style="display:none; color: #4ade80; background: rgba(74,222,128,0.1); border: 1px solid rgba(74,222,128,0.2); padding: 0.75rem; border-radius: 8px; font-size: 0.88rem; margin-top: 1rem; text-align: center;">
        </div>

        <button class="btn btn-secondary btn-sm" style="width: 100%; margin-top: 0.75rem;"
            onclick="closeBuyCreditsModal()">Cancel</button>
    </div>
</div>`;
    const modal = container.firstElementChild;
    // The rates viewer is provided by the Deploy page.
    modal.querySelector('[data-credit-rates]').hidden = typeof window.openPricingModal !== 'function';
    document.body.appendChild(modal);
    modal.addEventListener('click', event => {
        if (event.target === modal) closeBuyCreditsModal();
    });
    modal.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeBuyCreditsModal();
        if (event.key !== 'Tab') return;
        const controls = [...modal.querySelectorAll('button, input, a[href]')]
            .filter(control => !control.disabled && control.getClientRects().length > 0);
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement.matches('[role=dialog]'))) {
            event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault(); first.focus();
        }
    });
}

// Buy Credits Modal Logic
let selectedPackage = { id: 'pro', credits: 400, usd: 18.00 };

function openBuyCreditsModal(reason = null) {
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

    if (balanceSpan && window.currentUser) {
        balanceSpan.innerText = `Balance: ${window.currentUser.credits.toFixed(1)} Cr`;
    }

    if (reason) {
        alertBanner.innerText = reason;
        alertBanner.style.display = 'block';
    } else {
        alertBanner.style.display = 'none';
    }

    err.style.display = 'none';
    succ.style.display = 'none';

    selectCreditPackage('pro', 400, 18.00);
    creditPurchasePreviousFocus = document.activeElement;
    modal.classList.add('active');
    modal.querySelector('[role=dialog]').focus();
}

function closeBuyCreditsModal() {
    document.getElementById('buyCreditsModal').classList.remove('active');
    creditPurchasePreviousFocus?.focus();
}

function toggleCardFields(show) {
    const container = document.getElementById('cardFieldsContainer');
    if (container) {
        container.style.display = show ? 'flex' : 'none';
    }
}


function selectCreditPackage(pkgId, credits, usd) {

    ['pkgStarter', 'pkgPro', 'pkgUltra'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.remove('selected');
    });


    selectedPackage = { id: pkgId, credits: credits, usd: usd };
    const currentId = pkgId === 'starter' ? 'pkgStarter' : (pkgId === 'pro' ? 'pkgPro' : 'pkgUltra');
    const currentEl = document.getElementById(currentId);
    if (currentEl) currentEl.classList.add('selected');

    const payBtn = document.getElementById('btnCompletePurchase');
    if (payBtn) {
        payBtn.innerText = `Pay $${usd.toFixed(2)} & Add ${credits} Credits`;
    }
}

async function submitBuyCredits() {
    const err = document.getElementById('buyCreditsModalError');
    const succ = document.getElementById('buyCreditsModalSuccess');
    const payBtn = document.getElementById('btnCompletePurchase');
    err.style.display = 'none';
    succ.style.display = 'none';

    const methodEl = document.querySelector('input[name="paymentMethod"]:checked');
    const paymentMethod = methodEl ? methodEl.value : 'stripe_checkout';

    const bodyPayload = {
        payment_method: paymentMethod,
        checkout_mode: true
    };

    if (paymentMethod.startsWith('card')) {
        const cardName = document.getElementById('cardHolderName').value.trim();
        const cardNumber = document.getElementById('cardNumber').value.replace(/[\s\-]/g, '');
        const cardExp = document.getElementById('cardExp').value.trim();
        const cardCvc = document.getElementById('cardCvc').value.trim();

        if (!cardName) {
            err.innerText = 'Please enter Cardholder Name.';
            err.style.display = 'block';
            return;
        }
        if (!cardNumber || !/^\d{13,19}$/.test(cardNumber.replace(/•/g, ''))) {
            err.innerText = 'Please enter a valid credit card number (13-19 digits).';
            err.style.display = 'block';
            return;
        }
        if (!cardExp || !/^(0[1-9]|1[0-2])\/?([0-9]{2})$/.test(cardExp.replace(/\s/g, ''))) {
            err.innerText = 'Please enter a valid expiration date (MM/YY).';
            err.style.display = 'block';
            return;
        }
        if (!cardCvc || !/^\d{3,4}$/.test(cardCvc)) {
            err.innerText = 'Please enter a valid 3 or 4-digit CVV / CVC code.';
            err.style.display = 'block';
            return;
        }

        bodyPayload.card_name = cardName;
        bodyPayload.card_number = cardNumber;
        bodyPayload.card_exp = cardExp;
        bodyPayload.card_cvc = cardCvc;
    }

    payBtn.disabled = true;
    payBtn.innerText = 'Processing One-Off Payment...';

    try {
        if (typeof window.beforeCreditPurchase === 'function') await window.beforeCreditPurchase();
        bodyPayload.package_id = selectedPackage.id;

        const res = await fetch('/api/payments/buy-credits', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(bodyPayload)
        });

        const data = await res.json();
        if (!res.ok || data.status !== 'ok') {
            err.innerText = data.detail || 'Payment failed. Please try again.';
            err.style.display = 'block';
        } else if (data.mode === 'stripe_checkout' && data.checkout_url) {
            succ.innerText = '🚀 Redirecting to secure Stripe Checkout...';
            succ.style.display = 'block';
            setTimeout(() => {
                window.location.href = data.checkout_url;
            }, 600);
        } else {
            succ.innerText = `🎉 Payment successful! Added +${data.credits_added.toFixed(1)} Credits.`;
            succ.style.display = 'block';

            if (window.currentUser) {
                window.currentUser.credits = data.user.credits;
                const balanceSpan = document.getElementById('buyCreditsCurrentBalance');
                if (balanceSpan) balanceSpan.innerText = `Balance: ${window.currentUser.credits.toFixed(1)} Cr`;

                await checkAuthStatus({ refresh: true });
            }

            setTimeout(() => {
                closeBuyCreditsModal();
            }, 1800);
        }
    } catch (e) {
        err.innerText = e.message || 'Network error during payment processing.';
        err.style.display = 'block';
    } finally {
        payBtn.disabled = false;
        payBtn.innerText = `Pay $${selectedPackage.usd.toFixed(2)} & Add ${selectedPackage.credits.toFixed(1)} Credits`;
    }
}
