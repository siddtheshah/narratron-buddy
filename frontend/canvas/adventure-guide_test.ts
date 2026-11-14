import fs from 'node:fs';
import path from 'node:path';
import {
  expect,
  REPO_ROOT,
  TEMPLATES_DIR,
  test,
} from '../../testing/ui/canvas_fixtures';

test.describe('Adventure Guide UI', () => {
  test('canvas template and controller include required adventure guide elements and copy', async () => {
    const canvasHtml = await fs.promises.readFile(
      path.join(TEMPLATES_DIR, 'canvas.html'),
      'utf-8',
    );
    const canvasTs = await fs.promises.readFile(
      path.join(REPO_ROOT, 'frontend', 'canvas', 'canvas.ts'),
      'utf-8',
    );
    const theaterModalsTs = await fs.promises.readFile(
      path.join(REPO_ROOT, 'frontend', 'canvas', 'theater-modals.ts'),
      'utf-8',
    );

    expect(canvasHtml).toContain('id="adventure-guide-modal"');
    expect(canvasHtml).toContain('id="menu-item-adventure-guide"');
    expect(canvasHtml).toContain('id="adventure-guide-close-btn"');
    expect(canvasHtml).toContain('id="adventure-guide-ack-btn"');
    expect(canvasHtml).toContain('Adventure Mode Guide');
    expect(canvasHtml).not.toContain('id="adventure-guide-card"');

    expect(canvasHtml).toContain('pre-existing sandbox and structure');
    expect(canvasHtml).toContain('never told the same way twice');
    expect(canvasHtml).toContain("Play In-Character, Don't Force Control");
    expect(canvasHtml).toContain('disable adventure mode and take full control');

    expect(canvasTs).toContain('openAdventureGuideModal');
    expect(canvasTs).toContain('closeAdventureGuideModal');
    expect(canvasTs).toContain('initAdventureGuide');
    expect(theaterModalsTs).toContain('narratron_adventure_guide_seen');
  });

  test('auto-opens adventure guide once per theater and reopens from pullout menu', async ({
    canvas,
  }) => {
    await canvas.openCanvas({
      theaterId: 'adventure-stage',
      apiRoutes: {
        '/api/theaters/adventure-stage': {
          id: 'adventure-stage',
          metadata: {
            is_owner: true,
            is_active_orator: true,
            is_adventure_mode: true,
          },
        },
      },
    });

    const modal = canvas.page.locator('#adventure-guide-modal');
    await expect(modal).toHaveClass(/active/, { timeout: 3000 });
    await expect(modal).toHaveAttribute('aria-hidden', 'false');

    await canvas.page.locator('#adventure-guide-ack-btn').click();
    await expect(modal).not.toHaveClass(/active/);
    await expect(modal).toHaveAttribute('aria-hidden', 'true');

    const stored = await canvas.page.evaluate(() => ({
      globalSeen: window.localStorage.getItem('narratron_adventure_guide_seen'),
      theaterSeen: window.localStorage.getItem('narratron_adventure_guide_seen:adventure-stage'),
    }));
    expect(stored).toEqual({
      globalSeen: 'true',
      theaterSeen: 'true',
    });

    await canvas.page.locator('#menu-left-toggle-btn').click();
    const menuItem = canvas.page.locator('#menu-item-adventure-guide');
    await expect(menuItem).toBeVisible();
    await menuItem.click();

    await expect(modal).toHaveClass(/active/);
    await canvas.page.locator('#adventure-guide-close-btn').click();
    await expect(modal).not.toHaveClass(/active/);
  });

  test('keeps adventure guide hidden when theater is not in adventure mode', async ({
    canvas,
  }) => {
    await canvas.openCanvas({
      theaterId: 'freeform-stage',
      apiRoutes: {
        '/api/theaters/freeform-stage': {
          id: 'freeform-stage',
          metadata: {
            is_owner: true,
            is_active_orator: true,
            is_adventure_mode: false,
          },
        },
      },
    });

    await canvas.page.waitForTimeout(600);
    await expect(canvas.page.locator('#adventure-guide-modal')).not.toHaveClass(/active/);

    await canvas.page.locator('#menu-left-toggle-btn').click();
    await expect(canvas.page.locator('#menu-item-adventure-guide')).toBeHidden();
  });
});
