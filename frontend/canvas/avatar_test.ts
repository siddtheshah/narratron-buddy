import path from 'node:path';
import { expect, REPO_ROOT, test } from '../../testing/ui/canvas_fixtures';

test.use({ launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] } });

const avatarPath = path.join(REPO_ROOT, 'adventures', 'example_adventure', 'avatars', 'light-cube');
const avatarRoute = '/theaters/stage/avatars/light-cube';

test('the adventure avatar turns, brightens on hover, and dims when the pointer leaves', async ({ canvas, page }) => {
  await canvas.openCanvas({
    viewport: { width: 1440, height: 900 },
    apiRoutes: {
      '/api/theaters/stage': { metadata: { config: { avatar: { enabled: true, package: 'light-cube' } }, is_active_orator: true } },
      [`${avatarRoute}/avatar.json`]: (route) => route.fulfill({ path: path.join(avatarPath, 'avatar.json') }),
      [`${avatarRoute}/model.glb`]: (route) => route.fulfill({ path: path.join(avatarPath, 'model.glb') }),
    },
  });
  const mount = page.getByRole('img', { name: 'Cube of Light' });
  const model = mount.locator('canvas');
  await expect(model).toBeVisible();
  const avatarBox = (await mount.boundingBox())!;
  const controlsBox = (await page.locator('.canvas-corner-bottom-right').boundingBox())!;
  expect(avatarBox.width).toBeCloseTo(9 * 16 * 0.8, 1);
  expect(avatarBox.y + avatarBox.height).toBeLessThan(controlsBox.y);
  const turning = await model.screenshot();
  await expect.poll(async () => (await model.screenshot()).equals(turning)).toBe(false);

  // Hold the angle steady so only the emissive materials can change the canvas.
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const resting = await model.screenshot();
  await mount.hover();
  await expect.poll(async () => (await model.screenshot()).equals(resting)).toBe(false);
  await page.mouse.move(0, 0);
  await expect.poll(async () => (await model.screenshot()).equals(resting)).toBe(true);
});
