import { expect, test } from '../../testing/ui/canvas_fixtures';

const MUSIC_MUTED_STORAGE_KEY = 'narratron_music_muted';

test('restores saved muted state from localStorage on initial load', async ({ canvas, page }) => {
  await canvas.openCanvas({
    localStorage: {
      [MUSIC_MUTED_STORAGE_KEY]: 'true',
    },
  });

  const muteButton = page.locator('#music-mute-btn');
  await expect(muteButton).toHaveText('🔇 Unmute');
  expect(
    await page.evaluate((key: string) => localStorage.getItem(key), MUSIC_MUTED_STORAGE_KEY),
  ).toBe('true');
});

test('toggling mute button updates UI and persists across reload', async ({ canvas, page }) => {
  await canvas.openCanvas();

  const muteButton = page.locator('#music-mute-btn');
  await expect(muteButton).toHaveText('🔊 Mute');
  expect(
    await page.evaluate((key: string) => localStorage.getItem(key), MUSIC_MUTED_STORAGE_KEY),
  ).toBe('false');

  await muteButton.click();
  await expect(muteButton).toHaveText('🔇 Unmute');
  expect(
    await page.evaluate((key: string) => localStorage.getItem(key), MUSIC_MUTED_STORAGE_KEY),
  ).toBe('true');

  await page.reload();
  await page.waitForFunction(() => window._isActiveOratorState === true, null, { timeout: 5000 });
  await expect(muteButton).toHaveText('🔇 Unmute');
  expect(
    await page.evaluate((key: string) => localStorage.getItem(key), MUSIC_MUTED_STORAGE_KEY),
  ).toBe('true');

  await muteButton.click();
  await expect(muteButton).toHaveText('🔊 Mute');
  expect(
    await page.evaluate((key: string) => localStorage.getItem(key), MUSIC_MUTED_STORAGE_KEY),
  ).toBe('false');
});

test('raising the volume slider while muted persists auto-unmute while zero volume stays muted', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas();

  const muteButton = page.locator('#music-mute-btn');
  const slider = page.locator('#music-volume-slider');

  await muteButton.click();
  await expect(muteButton).toHaveText('🔇 Unmute');
  expect(
    await page.evaluate((key: string) => localStorage.getItem(key), MUSIC_MUTED_STORAGE_KEY),
  ).toBe('true');

  // Setting volume to 0 keeps music muted
  await slider.evaluate((el) => {
    const input = el as HTMLInputElement;
    input.value = '0';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await expect(muteButton).toHaveText('🔇 Unmute');
  expect(
    await page.evaluate((key: string) => localStorage.getItem(key), MUSIC_MUTED_STORAGE_KEY),
  ).toBe('true');

  // Raising volume above 0 auto-unmutes and persists to localStorage
  await slider.evaluate((el) => {
    const input = el as HTMLInputElement;
    input.value = '0.45';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await expect(muteButton).toHaveText('🔊 Mute');
  await expect(page.locator('#volume-percent')).toHaveText('45%');
  expect(
    await page.evaluate((key: string) => localStorage.getItem(key), MUSIC_MUTED_STORAGE_KEY),
  ).toBe('false');

  await page.reload();
  await page.waitForFunction(() => window._isActiveOratorState === true, null, { timeout: 5000 });
  await expect(muteButton).toHaveText('🔊 Mute');
  expect(
    await page.evaluate((key: string) => localStorage.getItem(key), MUSIC_MUTED_STORAGE_KEY),
  ).toBe('false');
});
