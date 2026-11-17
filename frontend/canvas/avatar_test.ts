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
      [`${avatarRoute}/shaders/dust.vert`]: (route) => route.fulfill({ path: path.join(avatarPath, 'shaders', 'dust.vert') }),
      [`${avatarRoute}/shaders/dust.frag`]: (route) => route.fulfill({ path: path.join(avatarPath, 'shaders', 'dust.frag') }),
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

  // Hold the angle steady so only the emissive materials and shader glow can change the canvas.
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const resting = await model.screenshot();
  await mount.hover();
  await expect.poll(async () => (await model.screenshot()).equals(resting)).toBe(false);
  await page.mouse.move(0, 0);
  await expect.poll(async () => (await model.screenshot()).equals(resting)).toBe(true);
});

test('minimal mode skips avatar shaders on load and disables them when toggled on', async ({ canvas, page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const shaderRequests: string[] = [];
  await canvas.openCanvas({
    viewport: { width: 1440, height: 900 },
    localStorage: { 'narratron.canvas.performance.minimalMode': 'true' },
    apiRoutes: {
      '/api/theaters/stage': { metadata: { config: { avatar: { enabled: true, package: 'light-cube' } }, is_active_orator: true } },
      [`${avatarRoute}/avatar.json`]: (route) => route.fulfill({ path: path.join(avatarPath, 'avatar.json') }),
      [`${avatarRoute}/model.glb`]: (route) => route.fulfill({ path: path.join(avatarPath, 'model.glb') }),
      [`${avatarRoute}/shaders/dust.vert`]: (route) => {
        shaderRequests.push('dust.vert');
        return route.fulfill({ path: path.join(avatarPath, 'shaders', 'dust.vert') });
      },
      [`${avatarRoute}/shaders/dust.frag`]: (route) => {
        shaderRequests.push('dust.frag');
        return route.fulfill({ path: path.join(avatarPath, 'shaders', 'dust.frag') });
      },
    },
  });

  const mount = page.getByRole('img', { name: 'Cube of Light' });
  const model = mount.locator('canvas');
  await expect(model).toBeVisible();
  expect(shaderRequests).toEqual([]);
  const withoutShaders = await model.screenshot();

  await page.click('#menu-left-toggle-btn');
  const minimalInput = page.locator('#minimal-mode-input');
  await minimalInput.uncheck();
  await page.click('#pullout-close-btn');
  await expect.poll(() => shaderRequests.slice().sort()).toEqual(['dust.frag', 'dust.vert']);
  await expect.poll(async () => (await model.screenshot()).equals(withoutShaders)).toBe(false);

  await page.click('#menu-left-toggle-btn');
  await minimalInput.check();
  await page.click('#pullout-close-btn');
  await expect.poll(async () => (await model.screenshot()).equals(withoutShaders)).toBe(true);
});
