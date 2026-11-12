import fs from 'node:fs';
import path from 'node:path';
import { expect, TEMPLATES_DIR, test } from '../../testing/ui/canvas_fixtures';

type CollabBarModule = typeof import('/static/generated/canvas/collab-bar.js');
type CollabBarController = import('/static/generated/canvas/collab-bar.js').CollabBarController;

interface CollabHarnessWindow extends Window {
  activeOrator: boolean;
  viewerCollabEnabled: boolean;
  collabBar: CollabBarController;
}

const COLLAB_BAR_URL = '/static/generated/canvas/collab-bar.js';

function loadSidebarAndModalHtml(): string {
  const template = fs.readFileSync(path.join(TEMPLATES_DIR, 'canvas.html'), 'utf-8');
  const sidebarStart = template.indexOf('    <div id="chat-sidebar">');
  const sidebarEnd = template.indexOf('    <script type="module"', sidebarStart);
  const modalStart = template.indexOf('    <div id="viewer-collab-guide-modal"');
  const modalEnd = template.indexOf('    <!-- Adventure Mode Guide Modal -->', modalStart);
  if (sidebarStart < 0 || sidebarEnd <= sidebarStart || modalStart < 0 || modalEnd <= modalStart) {
    throw new Error('Could not locate chat sidebar and collaboration guide modal in templates/canvas.html');
  }
  const sidebar = template.slice(sidebarStart, sidebarEnd);
  const modal = template.slice(modalStart, modalEnd);
  return `<div style="height: 650px">${sidebar}</div>${modal}`;
}

const SHELL_HTML = loadSidebarAndModalHtml();

for (const viewportWidth of [1000, 1400]) {
  test(`viewer collaboration bar and help modal layout at ${viewportWidth}px`, async ({
    canvas,
    page,
  }) => {
    await canvas.mount({
      viewport: { width: viewportWidth, height: 800 },
      stylesheets: ['/static/css/chat.css', '/static/css/canvas.css'],
      html: SHELL_HTML,
    });

    await page.evaluate(async (moduleUrl: string) => {
      const { createCollabBarController } =
        await window.__importModule<CollabBarModule>(moduleUrl);
      const win = window as unknown as CollabHarnessWindow;
      win.activeOrator = false;
      win.viewerCollabEnabled = false;
      win.collabBar = createCollabBarController({
        theaterId: 'test',
        bar: document.getElementById('viewer-collab-bar'),
        barStatus: document.getElementById('viewer-collab-bar-status'),
        guideBtn: document.getElementById('viewer-collab-guide-btn'),
        guideModal: document.getElementById('viewer-collab-guide-modal'),
        guideCloseBtn: document.getElementById('viewer-collab-guide-close-btn'),
        guideAckBtn: document.getElementById('viewer-collab-guide-ack-btn'),
        guideStatus: document.getElementById('viewer-collab-status'),
        isOrator: () => win.activeOrator,
        isCollabEnabled: () => win.viewerCollabEnabled,
      });
      win.collabBar.updateStatus(false);
    }, COLLAB_BAR_URL);

    const bar = page.locator('#viewer-collab-bar');
    await expect(bar).toBeVisible();
    await expect(page.locator('#viewer-collab-bar-status')).toHaveText('Collaboration: Off');
    await expect(page.getByRole('button', { name: 'Pop out chat into separate window' })).toHaveCount(0);

    await page.locator('#chat-name-input').fill('Moonlit Wanderer 42');
    await page.locator('#chat-login-chip').evaluate((element: HTMLElement) => {
      element.style.display = 'inline-flex';
    });
    const inputWidth = await page
      .locator('#chat-name-input')
      .evaluate((element: HTMLElement) => element.clientWidth);
    expect(inputWidth).toBeGreaterThan(80);

    const barBox = await bar.boundingBox();
    const tabsBox = await page.locator('#chat-user-bar').boundingBox();
    const thoughtBox = await page.locator('#agent-thought-card').boundingBox();
    expect(barBox).not.toBeNull();
    expect(tabsBox).not.toBeNull();
    expect(thoughtBox).not.toBeNull();
    expect(barBox!.y).toBeGreaterThanOrEqual(tabsBox!.y + tabsBox!.height);
    expect(thoughtBox!.y).toBeGreaterThanOrEqual(barBox!.y + barBox!.height);

    await page.getByRole('button', { name: 'What is collaboration?' }).click();
    const dialog = page.getByRole('dialog', { name: 'Collaborate with Narratron' });
    await expect(dialog).toBeVisible();
    await expect(page.locator('#viewer-collab-status')).toContainText('currently off');

    await page.evaluate(() => {
      const win = window as unknown as CollabHarnessWindow;
      win.viewerCollabEnabled = true;
      win.collabBar.updateStatus(true);
    });
    await expect(page.locator('#viewer-collab-bar-status')).toHaveText('Collaboration: On');
    await expect(page.locator('#viewer-collab-status')).toContainText('top viewer suggestion');

    await page.getByRole('button', { name: 'Close collaboration guide' }).click();
    await expect(dialog).toBeHidden();
    expect(
      await page.evaluate(() => localStorage.getItem('narratron_viewer_collab_guide_seen:test')),
    ).toBe('true');

    await page.evaluate(() => {
      const win = window as unknown as CollabHarnessWindow;
      win.activeOrator = true;
      win.collabBar.updateBar(win.viewerCollabEnabled);
    });
    await expect(bar).toBeHidden();

    await page.evaluate(() => {
      const win = window as unknown as CollabHarnessWindow;
      win.activeOrator = false;
      win.collabBar.updateBar(win.viewerCollabEnabled);
    });
    await expect(bar).toBeVisible();
    await expect(page.locator('#viewer-collab-bar-status')).toHaveText('Collaboration: On');
  });
}
