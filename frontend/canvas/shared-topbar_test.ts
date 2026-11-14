import fs from 'node:fs';
import path from 'node:path';
import { expect, TEMPLATES_DIR, test } from '../../testing/ui/canvas_fixtures';

interface TopbarHarnessWindow extends Window {
  authModalCalls?: Array<string | undefined>;
  pricingCalls?: number;
}

function renderTopbarSnippet(showPricing = false): string {
  let html = fs.readFileSync(path.join(TEMPLATES_DIR, 'shared_topbar.html'), 'utf-8');
  html = html.replace(/\{% if active_page == 'join' %\}([\s\S]*?)\{% endif %\}/g, '');
  html = html.replace(/\{% if show_pricing %\}([\s\S]*?)\{% endif %\}/g, showPricing ? '$1' : '');
  html = html.replace(/\{%[^%]*%\}/g, '');
  return html;
}

test('shared_topbar mobile menu toggles and closes via scrim, nav item, Escape, and desktop resize', async ({
  canvas,
  page,
}) => {
  const topbarHtml = renderTopbarSnippet(true);
  expect(topbarHtml).toContain('/static/generated/canvas/shared-topbar.js');

  await canvas.mount({
    viewport: { width: 420, height: 800 },
    html: topbarHtml,
    moduleScripts: [
      `
      window.authModalCalls = [];
      window.pricingCalls = 0;
      window.openAuthModal = (tab) => window.authModalCalls.push(tab);
      window.openPricingModal = () => { window.pricingCalls += 1; };
      `,
    ],
  });

  const header = page.locator('header.narratron-topbar');
  const toggle = header.locator('.mobile-menu-toggle');
  const scrim = header.locator('.mobile-menu-scrim');

  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(toggle).toHaveAttribute('aria-label', 'Open navigation menu');

  await toggle.click();
  await expect(header).toHaveClass(/mobile-menu-open/);
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(toggle).toHaveAttribute('aria-label', 'Close navigation menu');

  // Clicking the scrim closes the menu.
  await scrim.click({ force: true });
  await expect(header).not.toHaveClass(/mobile-menu-open/);
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');

  // Clicking a button inside .nav-links invokes its action and closes the menu.
  await toggle.click();
  await expect(header).toHaveClass(/mobile-menu-open/);
  await header.locator('.auth-nav-btn', { hasText: 'Log In' }).click();
  await expect(header).not.toHaveClass(/mobile-menu-open/);
  expect(
    await page.evaluate(() => (window as unknown as TopbarHarnessWindow).authModalCalls),
  ).toEqual(['login']);

  // Pressing Escape closes the menu.
  await toggle.click();
  await expect(header).toHaveClass(/mobile-menu-open/);
  await page.keyboard.press('Escape');
  await expect(header).not.toHaveClass(/mobile-menu-open/);

  // Resizing to desktop (>= 761px) closes the menu automatically.
  await toggle.click();
  await expect(header).toHaveClass(/mobile-menu-open/);
  await page.setViewportSize({ width: 900, height: 800 });
  await expect(header).not.toHaveClass(/mobile-menu-open/);

  // Pricing button triggers openPricingModal.
  await header.getByRole('button', { name: /Pricing/ }).click();
  expect(
    await page.evaluate(() => (window as unknown as TopbarHarnessWindow).pricingCalls),
  ).toBe(1);
});
