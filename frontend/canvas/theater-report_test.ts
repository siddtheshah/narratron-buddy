import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';
import { expect, TEMPLATES_DIR, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';

type TheaterReportModule = typeof import('/static/generated/canvas/theater-report.js');

interface ReportHarnessWindow extends Window {
  menuClosedCount: number;
}

interface RecordedReportSubmission {
  readonly pathname: string;
  readonly body: string;
}

const THEATER_REPORT_MODULE_URL = '/static/generated/canvas/theater-report.js';
const THEATER_REPORT_SHIM_URL = '/static/js/theater-report.js';

function loadPulloutAndReportMarkup(): string {
  const template = fs.readFileSync(path.join(TEMPLATES_DIR, 'canvas.html'), 'utf-8');
  const start = template.indexOf('    <!-- Pullout Navigation & Help Menu Drawer -->');
  const end = template.indexOf('    <div id="clip-share-modal"', start);
  if (start < 0 || end <= start) {
    throw new Error('Could not locate pullout drawer and report modal markup in templates/canvas.html');
  }
  return template.slice(start, end);
}

const PULLOUT_AND_REPORT_MARKUP = loadPulloutAndReportMarkup();

async function mountReportModalPage(
  canvas: CanvasHarness,
  options: {
    moduleUrl?: string;
    theaterId?: string | null;
    extraPrefixHtml?: string;
  } = {},
): Promise<void> {
  const moduleUrl = options.moduleUrl ?? THEATER_REPORT_MODULE_URL;
  const theaterIdLiteral = JSON.stringify(options.theaterId === undefined ? 'stage' : options.theaterId);
  const extraPrefixHtml = options.extraPrefixHtml ?? '';

  await canvas.mount({
    viewport: { width: 1000, height: 850 },
    stylesheets: ['/static/css/canvas.css'],
    html: `${extraPrefixHtml}${PULLOUT_AND_REPORT_MARKUP}`,
    moduleScripts: [
      `
      import { setupTheaterReport } from ${JSON.stringify(moduleUrl)};
      const overlay = document.getElementById('pullout-overlay');
      overlay.classList.add('active');
      overlay.setAttribute('aria-hidden', 'false');
      window.menuClosedCount = 0;
      setupTheaterReport(${theaterIdLiteral}, () => {
        window.menuClosedCount += 1;
      });
      `,
    ],
  });
}

test('deprecated shim re-exports the exact setupTheaterReport function and works end-to-end', async ({
  canvas,
  page,
}) => {
  const submissions: RecordedReportSubmission[] = [];
  await canvas.serveRoute(
    (url: URL) => url.pathname === '/api/theaters/stage/reports' || url.pathname === '/api/reports',
    async (route: Route) => {
      submissions.push({
        pathname: new URL(route.request().url()).pathname,
        body: route.request().postData() ?? '',
      });
      await route.fulfill({ status: 201, json: { report_id: 1, status: 'ok' } });
    },
  );

  await mountReportModalPage(canvas, { moduleUrl: THEATER_REPORT_SHIM_URL });

  const sameReference = await page.evaluate(
    async ({ compiledUrl, shimUrl }: { compiledUrl: string; shimUrl: string }) => {
      const compiled = await window.__importModule<TheaterReportModule>(compiledUrl);
      const shim = await window.__importModule<TheaterReportModule>(shimUrl);
      return compiled.setupTheaterReport === shim.setupTheaterReport;
    },
    { compiledUrl: THEATER_REPORT_MODULE_URL, shimUrl: THEATER_REPORT_SHIM_URL },
  );
  expect(sameReference).toBe(true);

  await page.getByRole('button', { name: 'Report Theater' }).click();
  await page.getByLabel('Report type', { exact: true }).selectOption('other');
  await page.getByLabel('What happened?').fill('Submitted through deprecated shim');
  await page.getByRole('button', { name: 'Send Report' }).click();
  await expect(page.locator('#report-theater-modal').getByRole('status')).toContainText('Report sent');
  expect(submissions).toEqual([
    {
      pathname: '/api/theaters/stage/reports',
      body: JSON.stringify({ reason: 'other', details: 'Submitted through deprecated shim' }),
    },
  ]);
});

test('report modal settings, submission, retry, dismissal, and feedback categories', async ({
  canvas,
  page,
}) => {
  const submissions: RecordedReportSubmission[] = [];
  await canvas.serveRoute(
    (url: URL) => url.pathname === '/api/theaters/stage/reports' || url.pathname === '/api/reports',
    async (route: Route) => {
      submissions.push({
        pathname: new URL(route.request().url()).pathname,
        body: route.request().postData() ?? '',
      });
      if (submissions.length === 1) {
        await route.fulfill({
          status: 503,
          json: { detail: 'Could not save your report. Please try again.' },
        });
      } else {
        await route.fulfill({ status: 201, json: { report_id: 2, status: 'ok' } });
      }
    },
  );

  await mountReportModalPage(canvas);

  const settings = page
    .locator('.pullout-body > div')
    .filter({ has: page.getByText('Settings', { exact: true }) });
  await expect(settings).toHaveCount(1);
  await expect(settings.getByText('Minimal Mode')).toBeVisible();
  await expect(settings.getByText('Input Settings')).toBeVisible();
  await expect(settings.getByText('Edit theater.yaml')).toBeVisible();

  await page.getByRole('button', { name: 'Report Theater' }).click();
  const modal = page.locator('#report-theater-modal');
  await expect(modal).toBeVisible();
  await expect(page.getByLabel('Report type', { exact: true })).toBeFocused();

  await page.getByLabel('Report type', { exact: true }).selectOption('harassment');
  await page.getByLabel('What happened?').fill('Abusive messages');
  await page.getByRole('button', { name: 'Send Report' }).click();
  await expect(page.getByRole('status')).toContainText('Please try again');
  await expect(page.getByLabel('What happened?')).toHaveValue('Abusive messages');

  await page.getByRole('button', { name: 'Send Report' }).click();
  await expect(page.locator('#report-theater-modal').getByRole('status')).toContainText('Report sent');
  expect(submissions).toHaveLength(2);
  expect(submissions[0]).toEqual({
    pathname: '/api/theaters/stage/reports',
    body: JSON.stringify({ reason: 'harassment', details: 'Abusive messages' }),
  });
  expect(submissions[1]).toEqual(submissions[0]);
  await expect(page.getByRole('button', { name: 'Done' })).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(modal).toHaveAttribute('aria-hidden', 'true');

  await page.getByRole('button', { name: 'Report Theater' }).click();
  await expect(page.getByLabel('What happened?')).toHaveValue('');
  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect(modal).toHaveAttribute('aria-hidden', 'true');

  for (const category of ['bug', 'suggestion'] as const) {
    await page.getByRole('button', { name: 'Report Theater' }).click();
    await page.getByLabel('Report type', { exact: true }).selectOption(category);
    await expect(page.locator('#report-theater-description')).toContainText('feedback');
    await page.getByLabel('What happened?').fill('Please improve audio reconnect behavior.');
    await page.getByRole('button', { name: 'Send Report' }).click();
    await expect(page.getByRole('status')).toContainText('Feedback sent');
    const last = submissions[submissions.length - 1];
    expect(last?.pathname).toBe('/api/reports');
    expect(last?.body).toContain(`"category":"${category}"`);
    expect(last?.body).toContain('"theater_id":"stage"');
    await page.getByRole('button', { name: 'Done' }).click();
  }
});

test('report modal validates whitespace, updates placeholders, traps Tab focus, and restores focus', async ({
  canvas,
  page,
}) => {
  const submissions: RecordedReportSubmission[] = [];
  await canvas.serveRoute(
    (url: URL) => url.pathname === '/api/theaters/stage/reports' || url.pathname === '/api/reports',
    async (route: Route) => {
      submissions.push({
        pathname: new URL(route.request().url()).pathname,
        body: route.request().postData() ?? '',
      });
      await route.fulfill({
        status: 401,
        json: { detail: 'Sign in to report a theater.' },
      });
    },
  );

  await mountReportModalPage(canvas, {
    extraPrefixHtml: '<button id="menu-left-toggle-btn" type="button">Menu</button>',
  });

  await page.getByRole('button', { name: 'Report Theater' }).click();
  expect(
    await page.evaluate(() => (window as unknown as ReportHarnessWindow).menuClosedCount),
  ).toBe(1);

  const reason = page.getByLabel('Report type', { exact: true });
  const details = page.getByLabel('What happened?');
  const description = page.locator('#report-theater-description');

  await expect(description).toContainText('theater snapshot');
  await expect(details).toHaveAttribute('placeholder', 'Describe the activity you want us to review.');

  await reason.selectOption('bug');
  await expect(description).toContainText('saved with this theater as context');
  await expect(details).toHaveAttribute(
    'placeholder',
    'Steps to reproduce, what you expected, and what happened instead.',
  );

  await reason.selectOption('suggestion');
  await expect(details).toHaveAttribute('placeholder', 'Describe your idea and how it would help.');

  await reason.selectOption('harmful_content');
  await expect(description).toContainText('theater snapshot');
  await expect(details).toHaveAttribute('placeholder', 'Describe the activity you want us to review.');

  // Whitespace-only input fails client-side validation without issuing a network request.
  await details.fill('   ');
  await page.getByRole('button', { name: 'Send Report' }).click();
  await expect(page.getByRole('status')).toHaveText('Describe the problem before sending your report.');
  await expect(details).toBeFocused();
  expect(submissions).toHaveLength(0);

  // Tab and Shift+Tab trap focus between the first and last enabled/visible form controls.
  await reason.focus();
  await page.keyboard.press('Shift+Tab');
  await expect(page.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(reason).toBeFocused();

  // Server 401 detail is surfaced to the status region.
  await details.fill('Needs sign in');
  await page.getByRole('button', { name: 'Send Report' }).click();
  await expect(page.getByRole('status')).toHaveText('Sign in to report a theater.');
  expect(submissions).toHaveLength(1);

  // Clicking the modal backdrop closes it and returns focus to #menu-left-toggle-btn.
  await page.locator('#report-theater-modal').click({ position: { x: 5, y: 5 } });
  await expect(page.locator('#report-theater-modal')).toHaveAttribute('aria-hidden', 'true');
  await expect(page.locator('#menu-left-toggle-btn')).toBeFocused();
});

test('report modal blocks dismissal while submission is pending and handles non-string errors', async ({
  canvas,
  page,
}) => {
  let releaseResponse: (() => void) | null = null;
  let requestCount = 0;

  await canvas.serveRoute('/api/theaters/stage/reports', async (route: Route) => {
    requestCount += 1;
    if (requestCount === 1) {
      await new Promise<void>((resolve) => {
        releaseResponse = resolve;
      });
      await route.fulfill({ status: 500, json: { detail: { code: 'unexpected' } } });
      return;
    }
    await route.fulfill({ status: 201, json: { report_id: 9, status: 'ok' } });
  });

  await mountReportModalPage(canvas);
  await page.getByRole('button', { name: 'Report Theater' }).click();
  await page.getByLabel('Report type', { exact: true }).selectOption('illegal_content');
  await page.getByLabel('What happened?').fill('Urgent review needed');
  await page.getByRole('button', { name: 'Send Report' }).click();

  await expect(page.getByRole('status')).toHaveText('Sending report…');
  await expect(page.getByRole('button', { name: 'Send Report' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Cancel' })).toBeDisabled();

  // Escape and backdrop clicks are ignored while pending.
  await page.keyboard.press('Escape');
  await page.locator('#report-theater-modal').click({ position: { x: 5, y: 5 } });
  await expect(page.locator('#report-theater-modal')).toHaveAttribute('aria-hidden', 'false');

  expect(releaseResponse).not.toBeNull();
  releaseResponse!();

  await expect(page.getByRole('status')).toHaveText('Could not save your report. Please try again.');
  await expect(page.getByRole('button', { name: 'Send Report' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Cancel' })).toBeEnabled();

  await page.getByRole('button', { name: 'Send Report' }).click();
  await expect(page.locator('#report-theater-modal').getByRole('status')).toContainText('Report sent');
  await expect(page.getByLabel('Report type', { exact: true })).toBeDisabled();
  await expect(page.getByLabel('What happened?')).toBeDisabled();
  await expect(page.locator('#report-theater-submit')).toBeHidden();

  // Duplicate form submit after completion does not trigger another POST.
  await page.evaluate(() => {
    const form = document.getElementById('report-theater-form');
    form?.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
  });
  expect(requestCount).toBe(2);
});

test('report modal works inside the full /canvas shell', async ({ canvas, page }) => {
  const reportBodies: string[] = [];
  await canvas.openCanvas({
    viewport: { width: 1400, height: 900 },
    apiRoutes: {
      '/api/theaters/stage/reports': async (route: Route) => {
        reportBodies.push(route.request().postData() ?? '');
        await route.fulfill({ status: 201, json: { report_id: 42, status: 'ok' } });
      },
    },
  });

  await page.locator('#menu-left-toggle-btn').click();
  await expect(page.locator('#pullout-overlay')).toHaveClass(/active/);

  await page.locator('#menu-item-report').click();
  await expect(page.locator('#pullout-overlay')).not.toHaveClass(/active/);
  await expect(page.locator('#report-theater-modal')).toBeVisible();

  await page.getByLabel('Report type', { exact: true }).selectOption('sexual_content');
  await page.getByLabel('What happened?').fill('Canvas integration report');
  await page.getByRole('button', { name: 'Send Report' }).click();

  await expect(page.locator('#report-theater-modal').getByRole('status')).toContainText('Report sent');
  expect(reportBodies).toEqual([
    JSON.stringify({ reason: 'sexual_content', details: 'Canvas integration report' }),
  ]);

  await page.getByRole('button', { name: 'Done' }).click();
  await expect(page.locator('#report-theater-modal')).toHaveAttribute('aria-hidden', 'true');
});
