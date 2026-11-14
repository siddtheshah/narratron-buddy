import { CANVAS_ORIGIN, expect, test } from '../../testing/ui/canvas_fixtures';

interface CreditPurchaseHarnessWindow extends Window {
  pricingCalls?: number;
  beforePurchaseCalls?: number;
}

const CREDIT_PURCHASE_MODULE_URL = '/static/generated/shared/credit-purchase.js';

test('credit-purchase guest auth redirect, rates toggle, package selection, and focus trap', async ({
  canvas,
  page,
}) => {
  await canvas.serveRoute(
    (url: URL) => url.origin === CANVAS_ORIGIN && url.pathname === '/api/auth/me',
    async (route) => {
      await route.fulfill({ json: { authenticated: false } });
    },
  );

  await canvas.mount({
    stylesheets: ['/static/css/auth-flow.css', '/static/css/credit-purchase.css'],
    html: '<button id="opener" type="button">Buy</button>',
    moduleScripts: [`import ${JSON.stringify(CREDIT_PURCHASE_MODULE_URL)};`],
  });

  // When unauthenticated, openBuyCreditsModal opens the login modal instead.
  await page.evaluate(() => {
    window.openBuyCreditsModal?.();
  });
  await expect(page.locator('#authModal')).toHaveClass(/active/);
  await expect(page.locator('#buyCreditsModal')).toHaveCount(0);
  await page.evaluate(() => {
    window.closeAuthModal?.();
  });

  // When authenticated without openPricingModal, Rates button stays hidden.
  await page.evaluate(() => {
    window.currentUser = { username: 'Ada', credits: 12.5 };
    const opener = document.getElementById('opener');
    opener?.focus();
    window.openBuyCreditsModal?.('Low credits');
  });

  await expect(page.locator('#buyCreditsModal')).toHaveClass(/active/);
  await expect(page.locator('#buyCreditsCurrentBalance')).toHaveText('Balance: 12.5 Cr');
  await expect(page.locator('#buyCreditsAlertBanner')).toBeVisible();
  await expect(page.locator('#buyCreditsAlertBanner')).toHaveText('Low credits');
  await expect(page.locator('[data-credit-rates]')).toBeHidden();

  // Select Starter package and verify selected class & CTA label.
  await page.locator('#pkgStarter').click();
  await expect(page.locator('#pkgStarter')).toHaveClass(/selected/);
  await expect(page.locator('#pkgPro')).not.toHaveClass(/selected/);
  await expect(page.locator('#btnCompletePurchase')).toHaveText('Pay $5.00 & Add 100 Credits');

  // Escape closes modal and restores focus to #opener.
  await page.locator('[role=dialog]').press('Escape');
  await expect(page.locator('#buyCreditsModal')).not.toHaveClass(/active/);
  await expect(page.locator('#opener')).toBeFocused();

  // Defining window.openPricingModal exposes the Rates button on re-open.
  await page.evaluate(() => {
    const win = window as unknown as CreditPurchaseHarnessWindow;
    win.pricingCalls = 0;
    window.openPricingModal = () => {
      win.pricingCalls = (win.pricingCalls ?? 0) + 1;
    };
    window.openBuyCreditsModal?.();
  });
  await expect(page.locator('[data-credit-rates]')).toBeVisible();
  await page.locator('[data-credit-rates]').click();
  await expect(page.locator('#buyCreditsModal')).not.toHaveClass(/active/);
  expect(
    await page.evaluate(() => (window as unknown as CreditPurchaseHarnessWindow).pricingCalls),
  ).toBe(1);
});

test('credit-purchase handles beforeCreditPurchase hook, payment success, and error responses', async ({
  canvas,
  page,
}) => {
  let credits = 10;
  let mode: 'error' | 'ok' = 'error';
  const purchasePayloads: Array<Record<string, unknown>> = [];

  await canvas.serveRoute(
    (url: URL) =>
      url.origin === CANVAS_ORIGIN &&
      (url.pathname === '/api/auth/me' || url.pathname === '/api/payments/buy-credits'),
    async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/auth/me') {
        await route.fulfill({
          json: { authenticated: true, user: { id: 1, username: 'Ada', credits } },
        });
        return;
      }
      const payload = JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>;
      purchasePayloads.push(payload);
      if (mode === 'error') {
        await route.fulfill({ status: 400, json: { detail: 'Card declined' } });
        return;
      }
      credits = 1010;
      await route.fulfill({
        json: { status: 'ok', credits_added: 1000, user: { credits } },
      });
    },
  );

  await canvas.mount({
    stylesheets: ['/static/css/auth-flow.css', '/static/css/credit-purchase.css'],
    html: '<div id="userAccountBar"></div>',
    moduleScripts: [
      `
      window.beforePurchaseCalls = 0;
      window.beforeCreditPurchase = async () => { window.beforePurchaseCalls += 1; };
      import ${JSON.stringify(CREDIT_PURCHASE_MODULE_URL)};
      `,
    ],
  });

  await expect(page.locator('#userAccountBar .credit-badge')).toContainText('⚡ 10.0');
  await page.locator('#userAccountBar .credit-badge').click();
  await page.locator('#pkgUltra').click();
  await page.locator('#btnCompletePurchase').click();

  await expect(page.locator('#buyCreditsModalError')).toBeVisible();
  await expect(page.locator('#buyCreditsModalError')).toHaveText('Card declined');

  mode = 'ok';
  await page.locator('#btnCompletePurchase').click();

  await expect(page.locator('#buyCreditsModalSuccess')).toBeVisible();
  await expect(page.locator('#buyCreditsModalSuccess')).toHaveText(
    '🎉 Payment successful! Added +1000.0 Credits.',
  );
  await expect(page.locator('#buyCreditsCurrentBalance')).toHaveText('Balance: 1010.0 Cr');
  await expect(page.locator('#userAccountBar .credit-badge')).toContainText('⚡ 1010.0');
  expect(
    await page.evaluate(() => (window as unknown as CreditPurchaseHarnessWindow).beforePurchaseCalls),
  ).toBe(2);
  expect(purchasePayloads).toEqual([
    { payment_method: 'stripe_checkout', checkout_mode: true, package_id: 'ultra' },
    { payment_method: 'stripe_checkout', checkout_mode: true, package_id: 'ultra' },
  ]);
});
