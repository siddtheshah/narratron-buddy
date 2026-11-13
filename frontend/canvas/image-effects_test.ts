import { expect, test } from '../../testing/ui/canvas_fixtures';
import type { ImageEffectController } from './image-effects.js';

type ImageEffectsModule = typeof import('./image-effects.js');

declare global {
  interface Window {
    fxController: ImageEffectController;
    fxFramesRequested: number;
  }
}

const PNG_1X1_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

test('image-effects shim shares exact exports and frozen registries with canonical module', async ({ canvas, page }) => {
  await canvas.mount();
  const result = await page.evaluate(async () => {
    const canonical = await window.__importModule<ImageEffectsModule>('/static/generated/canvas/image-effects.js');
    const shim = await window.__importModule<ImageEffectsModule>('/static/js/image-effects.js');
    const versionedShim = await window.__importModule<ImageEffectsModule>('/static/js/image-effects.js?v=effects-20261005-1');
    return {
      sameEffects: canonical.IMAGE_EFFECTS === shim.IMAGE_EFFECTS && shim.IMAGE_EFFECTS === versionedShim.IMAGE_EFFECTS,
      sameDefaults:
        canonical.IMAGE_EFFECT_DEFAULT_INTENSITIES === shim.IMAGE_EFFECT_DEFAULT_INTENSITIES &&
        shim.IMAGE_EFFECT_DEFAULT_INTENSITIES === versionedShim.IMAGE_EFFECT_DEFAULT_INTENSITIES,
      sameAttach: canonical.attachImageEffect === shim.attachImageEffect && shim.attachImageEffect === versionedShim.attachImageEffect,
      effectsFrozen: Object.isFrozen(canonical.IMAGE_EFFECTS),
      defaultsFrozen: Object.isFrozen(canonical.IMAGE_EFFECT_DEFAULT_INTENSITIES),
      effectKeys: Object.keys(canonical.IMAGE_EFFECTS),
      defaultKeys: Object.keys(canonical.IMAGE_EFFECT_DEFAULT_INTENSITIES),
    };
  });

  expect(result.sameEffects).toBe(true);
  expect(result.sameDefaults).toBe(true);
  expect(result.sameAttach).toBe(true);
  expect(result.effectsFrozen).toBe(true);
  expect(result.defaultsFrozen).toBe(true);
  expect(result.effectKeys).toEqual(['none', 'creeping', 'dream', 'sparkle', 'gleam3', 'haze', 'trace']);
  expect(result.defaultKeys).toEqual(['none', 'creeping', 'dream', 'sparkle', 'gleam3', 'haze', 'trace']);
});

test('attachImageEffect validates arguments, normalises intensity, and falls back for unknown effects', async ({ canvas, page }) => {
  await canvas.mount({
    html: '<div id="wrap"><img id="pic" alt="test"></div><div id="not-an-image"></div>',
  });

  const validation = await page.evaluate(async () => {
    const { attachImageEffect } = await window.__importModule<ImageEffectsModule>('/static/generated/canvas/image-effects.js');

    let nonImageError = '';
    try {
      const notImage = document.getElementById('not-an-image') as unknown as HTMLImageElement;
      attachImageEffect(notImage);
    } catch (error) {
      nonImageError = error instanceof TypeError ? `TypeError:${error.message}` : String(error);
    }

    let detachedError = '';
    try {
      const detached = document.createElement('img');
      attachImageEffect(detached);
    } catch (error) {
      detachedError = error instanceof TypeError ? `TypeError:${error.message}` : String(error);
    }

    const img = document.getElementById('pic') as HTMLImageElement;
    const controller = attachImageEffect(img, { effect: 'unknown-fx', intensity: 4.5 });
    const clampedHigh = controller.element.style.getPropertyValue('--fx-intensity');
    const fallbackClasses = Array.from(controller.element.classList);
    const datasetEffect = controller.element.dataset.effect;

    controller.setIntensity(-2);
    const clampedLow = controller.element.style.getPropertyValue('--fx-intensity');

    controller.setIntensity(Number.NaN);
    const fallbackIntensity = controller.element.style.getPropertyValue('--fx-intensity');

    controller.destroy();
    return {
      nonImageError,
      detachedError,
      clampedHigh,
      clampedLow,
      fallbackIntensity,
      fallbackClasses,
      datasetEffect,
    };
  });

  expect(validation.nonImageError).toBe('TypeError:attachImageEffect expects an HTMLImageElement.');
  expect(validation.detachedError).toContain('TypeError:');
  expect(validation.clampedHigh).toBe('1');
  expect(validation.clampedLow).toBe('0');
  expect(validation.fallbackIntensity).toBe('0.72');
  expect(validation.fallbackClasses).toContain('fx-gleam3');
  expect(validation.datasetEffect).toBe('unknown-fx');
});

test('attachImageEffect wraps image once, switches effect classes/canvases, and reuses existing frame', async ({ canvas, page }) => {
  await canvas.mount({
    html: '<div id="stage"><img id="art" alt="art"></div>',
    stylesheets: ['/static/css/image-effects.css'],
  });

  const transitions = await page.evaluate(async () => {
    const { attachImageEffect } = await window.__importModule<ImageEffectsModule>('/static/generated/canvas/image-effects.js');
    const img = document.getElementById('art') as HTMLImageElement;
    const controller = attachImageEffect(img, { effect: 'creeping', intensity: 0.65 });

    const snapshot = (): {
      classes: string[];
      effect: string | undefined;
      intensity: string;
      hiddenMap: Record<string, boolean>;
    } => {
      const frame = controller.element;
      const hiddenMap: Record<string, boolean> = {};
      for (const cls of [
        'image-effect-luminance',
        'image-effect-fourier-shadows',
        'image-effect-periodic-light',
        'image-effect-star-twinkle',
        'image-effect-gleam3',
        'image-effect-haze',
        'image-effect-trace',
      ]) {
        const el = frame.querySelector<HTMLCanvasElement>(`.${cls}`)!;
        hiddenMap[cls] = Boolean(el.hidden);
      }
      return {
        classes: Array.from(frame.classList),
        effect: frame.dataset.effect,
        intensity: frame.style.getPropertyValue('--fx-intensity'),
        hiddenMap,
      };
    };

    const creepingState = snapshot();
    controller.setEffect('dream');
    const dreamState = snapshot();
    controller.setEffect('none');
    const noneState = snapshot();

    // Re-attaching to the already-wrapped image reuses the same .image-effect-frame.
    const secondController = attachImageEffect(img, { effect: 'haze', intensity: 0.4 });
    const frameCount = document.querySelectorAll('.image-effect-frame').length;
    const sameFrame = secondController.element === controller.element;

    secondController.destroy();
    controller.destroy();
    return {
      hasSourceClass: img.classList.contains('image-effect-source'),
      creepingState,
      dreamState,
      noneState,
      frameCount,
      sameFrame,
    };
  });

  expect(transitions.hasSourceClass).toBe(true);
  expect(transitions.creepingState.classes).toEqual(['image-effect-frame', 'fx-luminance', 'fx-fourier-shadows', 'fx-vignette']);
  expect(transitions.creepingState.hiddenMap['image-effect-luminance']).toBe(false);
  expect(transitions.creepingState.hiddenMap['image-effect-fourier-shadows']).toBe(false);
  expect(transitions.creepingState.hiddenMap['image-effect-gleam3']).toBe(true);

  expect(transitions.dreamState.classes).toEqual(['image-effect-frame', 'fx-periodic-light', 'fx-bloom']);
  expect(transitions.dreamState.intensity).toBe('0.65');
  expect(transitions.dreamState.hiddenMap['image-effect-periodic-light']).toBe(false);
  expect(transitions.dreamState.hiddenMap['image-effect-luminance']).toBe(true);

  expect(transitions.noneState.classes).toEqual(['image-effect-frame']);
  expect(Object.values(transitions.noneState.hiddenMap).every((hidden) => hidden)).toBe(true);
  expect(transitions.frameCount).toBe(1);
  expect(transitions.sameFrame).toBe(true);
});

test('all canvas-based image effects analyze real pixels, toggle debug maps, and handle src changes', async ({ canvas, page }) => {
  await canvas.mount({
    html: '<div id="stage"><img id="art" alt="art"></div>',
    stylesheets: ['/static/css/image-effects.css'],
  });

  const stats = await page.evaluate(async () => {
    const { attachImageEffect } = await window.__importModule<ImageEffectsModule>('/static/generated/canvas/image-effects.js');
    const img = document.getElementById('art') as HTMLImageElement;

    // Build a 120x120 synthetic scene with dark sky, bright stars, pale diagonal trace stroke, and bright region.
    const source = document.createElement('canvas');
    source.width = 120;
    source.height = 120;
    const ctx = source.getContext('2d')!;
    ctx.fillStyle = '#080c18';
    ctx.fillRect(0, 0, 120, 120);

    // Bright warm gradient block for luminance, fourier shadows, periodic light, haze, and gleam3.
    const grad = ctx.createRadialGradient(35, 35, 2, 35, 35, 28);
    grad.addColorStop(0, '#fff7d6');
    grad.addColorStop(0.5, '#f0a850');
    grad.addColorStop(1, '#080c18');
    ctx.fillStyle = grad;
    ctx.fillRect(5, 5, 60, 60);

    // Isolated bright stars on dark background for sparkle.
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(95, 20, 1, 1);
    ctx.fillRect(105, 35, 1, 1);
    ctx.fillRect(90, 50, 1, 1);

    // Pale desaturated highlight stroke with brightness gradient for trace.
    for (let i = 0; i < 40; i += 1) {
      const shade = 165 + Math.round((i / 39) * 90);
      ctx.fillStyle = `rgb(${shade}, ${shade}, ${shade})`;
      ctx.fillRect(20 + i * 2, 85 + Math.round(i * 0.4), 2, 2);
    }

    await new Promise<void>((resolve) => {
      img.onload = () => resolve();
      img.src = source.toDataURL();
    });

    const controller = attachImageEffect(img, { effect: 'gleam3', intensity: 0.85 });
    const frame = controller.element;

    const hasPaintedAlpha = (selector: string): boolean => {
      const c = frame.querySelector<HTMLCanvasElement>(selector)!;
      const cCtx = c.getContext('2d');
      if (!cCtx || !c.width || !c.height) return false;
      const data = cCtx.getImageData(0, 0, c.width, c.height).data;
      for (let i = 3; i < data.length; i += 4) {
        if (data[i] > 0) return true;
      }
      return false;
    };

    const gleamCanvas = frame.querySelector<HTMLCanvasElement>('.image-effect-gleam3')!;
    const gleamSeedMap = frame.querySelector<HTMLCanvasElement>('.image-effect-gleam3-seed-map')!;
    const gleamInverseMap = frame.querySelector<HTMLCanvasElement>('.image-effect-v-inverse-map')!;
    const gleamSeedCount = Number(gleamCanvas.dataset.gleam3SeedPixels || '0');
    const gleamSeedSource = gleamCanvas.dataset.gleam3SeedSource;

    controller.setGleam3SeedMapVisible(true);
    controller.setInverseBlurMapVisible(true);
    const gleamMapsShown = !gleamSeedMap.hidden && !gleamInverseMap.hidden;
    controller.setGleam3SeedMapVisible(false);
    controller.setInverseBlurMapVisible(false);
    const gleamMapsHidden = gleamSeedMap.hidden && gleamInverseMap.hidden;

    controller.setEffect('creeping');
    const creepingLuminancePainted = hasPaintedAlpha('.image-effect-luminance');
    const creepingShadowsPainted = hasPaintedAlpha('.image-effect-fourier-shadows');

    controller.setEffect('dream');
    const dreamLightPainted = hasPaintedAlpha('.image-effect-periodic-light');

    controller.setEffect('sparkle');
    const sparkleCanvas = frame.querySelector<HTMLCanvasElement>('.image-effect-star-twinkle')!;
    const sparkleCount = Number(sparkleCanvas.dataset.sparkleCount || '0');

    controller.setEffect('haze');
    const hazePainted = hasPaintedAlpha('.image-effect-haze');

    controller.setEffect('trace');
    const traceCanvas = frame.querySelector<HTMLCanvasElement>('.image-effect-trace')!;
    const traceMap = frame.querySelector<HTMLCanvasElement>('.image-effect-trace-map')!;
    const traceLines = Number(traceCanvas.dataset.traceLineCount || '0');
    const traceDirected = traceCanvas.dataset.traceBrightnessDirected;
    controller.setTraceMapVisible(true);
    const traceMapShown = !traceMap.hidden;
    controller.setTraceMapVisible(false);
    const traceMapHidden = traceMap.hidden;

    // Changing src triggers MutationObserver clearMask on luminance canvas.
    controller.setEffect('creeping');
    const luminanceCanvas = frame.querySelector<HTMLCanvasElement>('.image-effect-luminance')!;
    const loadedNext = new Promise<void>((resolve) => {
      img.addEventListener('load', () => resolve(), { once: true });
    });
    img.setAttribute('src', source.toDataURL());
    await Promise.resolve();
    const clearedOnMutation = luminanceCanvas.hidden;
    await loadedNext;
    const restoredAfterLoad = !luminanceCanvas.hidden;

    controller.destroy();
    return {
      gleamSeedCount,
      gleamSeedSource,
      gleamMapsShown,
      gleamMapsHidden,
      creepingLuminancePainted,
      creepingShadowsPainted,
      dreamLightPainted,
      sparkleCount,
      hazePainted,
      traceLines,
      traceDirected,
      traceMapShown,
      traceMapHidden,
      clearedOnMutation,
      restoredAfterLoad,
    };
  });

  expect(stats.gleamSeedCount).toBeGreaterThan(0);
  expect(stats.gleamSeedSource).toBe('value-flow');
  expect(stats.gleamMapsShown).toBe(true);
  expect(stats.gleamMapsHidden).toBe(true);
  expect(stats.creepingLuminancePainted).toBe(true);
  expect(stats.creepingShadowsPainted).toBe(true);
  expect(stats.dreamLightPainted).toBe(true);
  expect(stats.sparkleCount).toBeGreaterThanOrEqual(1);
  expect(stats.hazePainted).toBe(true);
  expect(stats.traceLines).toBeGreaterThanOrEqual(1);
  expect(stats.traceDirected).toBe('true');
  expect(stats.traceMapShown).toBe(true);
  expect(stats.traceMapHidden).toBe(true);
  expect(stats.clearedOnMutation).toBe(true);
  expect(stats.restoredAfterLoad).toBe(true);
});

test('animation loops pause, resume, respect reduced motion, and clean up completely on destroy', async ({ canvas, page }) => {
  await canvas.mount({
    html: '<div id="stage"><img id="art" alt="art"></div>',
  });
  await page.clock.install();

  await page.evaluate(async () => {
    const { attachImageEffect } = await window.__importModule<ImageEffectsModule>('/static/generated/canvas/image-effects.js');
    window.fxFramesRequested = 0;
    const originalRaf = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = (callback) => {
      window.fxFramesRequested += 1;
      return originalRaf(callback);
    };

    const img = document.getElementById('art') as HTMLImageElement;
    const source = document.createElement('canvas');
    source.width = 40;
    source.height = 40;
    const ctx = source.getContext('2d')!;
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, 40, 40);
    await new Promise<void>((resolve) => {
      img.onload = () => resolve();
      img.src = source.toDataURL();
    });

    window.fxController = attachImageEffect(img, { effect: 'creeping', intensity: 0.7 });
  });

  await page.clock.runFor(100);
  const runningFrames = await page.evaluate(() => window.fxFramesRequested);
  expect(runningFrames).toBeGreaterThan(0);

  await page.evaluate(() => window.fxController.setPaused(true));
  const pausedBaseline = await page.evaluate(() => window.fxFramesRequested);
  await page.clock.runFor(150);
  expect(await page.evaluate(() => window.fxFramesRequested)).toBe(pausedBaseline);

  await page.evaluate(() => window.fxController.setPaused(false));
  await page.clock.runFor(100);
  expect(await page.evaluate(() => window.fxFramesRequested)).toBeGreaterThan(pausedBaseline);

  await page.evaluate(() => window.fxController.destroy());
  const destroyedState = await page.evaluate(() => {
    const frame = document.querySelector<HTMLElement>('.image-effect-frame')!;
    return {
      classes: Array.from(frame.classList),
      intensity: frame.style.getPropertyValue('--fx-intensity'),
      canvasCount: frame.querySelectorAll('canvas').length,
      imgStillPresent: frame.querySelector('img') !== null,
      framesAtDestroy: window.fxFramesRequested,
    };
  });
  expect(destroyedState.classes).toEqual(['image-effect-frame']);
  expect(destroyedState.intensity).toBe('');
  expect(destroyedState.canvasCount).toBe(0);
  expect(destroyedState.imgStillPresent).toBe(true);

  await page.clock.runFor(150);
  expect(await page.evaluate(() => window.fxFramesRequested)).toBe(destroyedState.framesAtDestroy);
});

test('reduced motion preference keeps effect animation paused', async ({ canvas, page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await canvas.mount({
    html: '<div id="stage"><img id="art" alt="art"></div>',
  });
  await page.clock.install();

  await page.evaluate(async () => {
    const { attachImageEffect } = await window.__importModule<ImageEffectsModule>('/static/generated/canvas/image-effects.js');
    window.fxFramesRequested = 0;
    const originalRaf = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = (callback) => {
      window.fxFramesRequested += 1;
      return originalRaf(callback);
    };

    const img = document.getElementById('art') as HTMLImageElement;
    const source = document.createElement('canvas');
    source.width = 20;
    source.height = 20;
    source.getContext('2d')!.fillRect(0, 0, 20, 20);
    await new Promise<void>((resolve) => {
      img.onload = () => resolve();
      img.src = source.toDataURL();
    });

    window.fxController = attachImageEffect(img, { effect: 'creeping', intensity: 0.7 });
    window.fxController.setPaused(false);
  });

  await page.clock.runFor(150);
  expect(await page.evaluate(() => window.fxFramesRequested)).toBe(0);
  await page.evaluate(() => window.fxController.destroy());
});

test('cross-origin image without CORS degrades gracefully without throwing', async ({ canvas, page }) => {
  await canvas.serveRoute('https://other.test/art.png', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: Buffer.from(PNG_1X1_BASE64, 'base64'),
    });
  });

  await canvas.mount({
    html: '<div id="stage"><img id="art" alt="cors-art"></div>',
  });

  const result = await page.evaluate(async () => {
    const { attachImageEffect } = await window.__importModule<ImageEffectsModule>('/static/generated/canvas/image-effects.js');
    const img = document.getElementById('art') as HTMLImageElement;
    await new Promise<void>((resolve) => {
      img.onload = () => resolve();
      img.src = 'https://other.test/art.png';
    });

    const controller = attachImageEffect(img, { effect: 'creeping', intensity: 0.7 });
    const luminanceHidden = controller.element.querySelector<HTMLCanvasElement>('.image-effect-luminance')!.hidden;
    for (const effect of ['dream', 'sparkle', 'gleam3', 'haze', 'trace']) {
      controller.setEffect(effect);
    }
    const gleamHidden = controller.element.querySelector<HTMLCanvasElement>('.image-effect-gleam3')!.hidden;
    const traceHidden = controller.element.querySelector<HTMLCanvasElement>('.image-effect-trace')!.hidden;
    controller.destroy();
    return { luminanceHidden, gleamHidden, traceHidden };
  });

  expect(result).toEqual({ luminanceHidden: true, gleamHidden: true, traceHidden: true });
});
