import { expect, test } from '../../testing/ui/canvas_fixtures';

type AnimationEffectsModule = typeof import('./animation-effects.js');

const PNG_1X1_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

test('animation-effects shim preserves every exported symbol identically', async ({ canvas, page }) => {
  await canvas.mount();
  const matches = await page.evaluate(async () => {
    const canonical = await window.__importModule<AnimationEffectsModule>('/static/generated/canvas/animation-effects.js');
    const shim = await window.__importModule<AnimationEffectsModule>('/static/js/animation-effects.js');
    return {
      layerTransform: canonical.layerTransform === shim.layerTransform,
      layerCssTransform: canonical.layerCssTransform === shim.layerCssTransform,
      layerOpacity: canonical.layerOpacity === shim.layerOpacity,
      layerHaloParams: canonical.layerHaloParams === shim.layerHaloParams,
      layerPieceFilter: canonical.layerPieceFilter === shim.layerPieceFilter,
      layerMirageDraw: canonical.layerMirageDraw === shim.layerMirageDraw,
      layerFlameRippleDraw: canonical.layerFlameRippleDraw === shim.layerFlameRippleDraw,
      flameRippleAlias: canonical.layerFlameRippleDraw === canonical.layerMirageDraw,
      layerReflectiveDraw: canonical.layerReflectiveDraw === shim.layerReflectiveDraw,
      layerEnergyBlastDraw: canonical.layerEnergyBlastDraw === shim.layerEnergyBlastDraw,
      isMeshDistortionEffect: canonical.isMeshDistortionEffect === shim.isMeshDistortionEffect,
      calculateMeshGrid: canonical.calculateMeshGrid === shim.calculateMeshGrid,
    };
  });

  expect(Object.values(matches).every(Boolean)).toBe(true);
});

test('pure transform, CSS transform, opacity, halo, and piece filter helpers compute expected values', async ({ canvas, page }) => {
  await canvas.mount();
  const result = await page.evaluate(async () => {
    const fx = await window.__importModule<AnimationEffectsModule>('/static/generated/canvas/animation-effects.js');
    const seconds = 1.25;
    return {
      rocking: fx.layerTransform('gentle_rocking', seconds),
      vibrate: fx.layerTransform('vibrate', seconds),
      pulse: fx.layerTransform('pulse', seconds),
      twist: fx.layerTransform('twist', seconds),
      bend: fx.layerTransform('bend', seconds),
      mirage: fx.layerTransform('mirage', seconds),
      sway: fx.layerTransform('sway', seconds),
      unknown: fx.layerTransform('nonexistent', seconds),
      cssRocking: fx.layerCssTransform('gentle_rocking', seconds),
      ghostlyOpacity: fx.layerOpacity('ghostly', Math.PI / 3, 0.8),
      ghostlyClampedHigh: fx.layerOpacity('ghostly', Math.PI / 3, 5),
      normalOpacity: fx.layerOpacity('pulse', seconds, 0.6),
      haloNone: fx.layerHaloParams('pulse', seconds),
      haloLight: fx.layerHaloParams('light_halo', 0.5, 1.5, 0.8),
      haloDark: fx.layerHaloParams('dark_halo', 0.5, 1.0, 2.0),
      filterDark: fx.layerPieceFilter('dark_halo', 0),
      filterLight: fx.layerPieceFilter('light_halo', 0),
      filterDefault: fx.layerPieceFilter('sway', 1.0),
    };
  });

  expect(result.sway).toEqual({ x: 0, y: 0, rotation: 0, scale: 1 });
  expect(result.unknown).toEqual({ x: 0, y: 0, rotation: 0, scale: 1 });
  expect(result.rocking.y).toBeCloseTo(Math.sin(1.25 * 1.2) * 1.2, 6);
  expect(result.rocking.rotation).toBeCloseTo(Math.sin(1.25 * 1.2) * 0.028, 6);
  expect(result.vibrate.x).toBeCloseTo(Math.sin(1.25 * 9.1) * 1.2, 6);
  expect(result.vibrate.y).toBeCloseTo(Math.cos(1.25 * 7.3) * 0.9, 6);
  expect(result.pulse.scale).toBeCloseTo(1 + Math.sin(1.25 * 1.15) * 0.008, 6);
  expect(result.twist.rotation).toBeCloseTo(Math.sin(1.25 * 1.5) * 0.02, 6);
  expect(result.bend.x).toBeCloseTo(Math.sin(1.25 * 1.5) * 1.5, 6);
  expect(result.mirage.y).toBeLessThanOrEqual(0);
  expect(result.cssRocking).toBe(
    `translate(${result.rocking.x}px, ${result.rocking.y}px) rotate(${result.rocking.rotation}rad) scale(${result.rocking.scale})`,
  );

  expect(result.ghostlyOpacity).toBeCloseTo(0.8 * (0.5 + Math.sin((Math.PI / 3) * 1.5) * 0.4), 6);
  expect(result.ghostlyClampedHigh).toBe(1);
  expect(result.normalOpacity).toBe(0.6);

  expect(result.haloNone).toBeNull();
  expect(result.haloLight).toEqual({
    isLight: true,
    haloScale: (1 + Math.sin(1.5) * 0.06 * 1.5) * 1.08,
    haloAlpha: (0.5 + Math.sin(1.5) * 0.15) * 0.8,
    haloFilter: 'blur(22px) saturate(4.0) brightness(2.5)',
  });
  expect(result.haloDark?.isLight).toBe(false);
  expect(result.haloDark?.haloAlpha).toBe(1);
  expect(result.haloDark?.haloFilter).toBe('blur(22px) saturate(4.0) brightness(0.02) contrast(3.0)');

  expect(result.filterDark).toBe('brightness(0.600) contrast(1.1)');
  expect(result.filterLight).toBe('brightness(1.100)');
  expect(result.filterDefault).toBe('none');
});

test('isMeshDistortionEffect and calculateMeshGrid handle distortion modes, anchors, damping, and zero sizes', async ({ canvas, page }) => {
  await canvas.mount();
  const meshResults = await page.evaluate(async () => {
    const fx = await window.__importModule<AnimationEffectsModule>('/static/generated/canvas/animation-effects.js');
    const predicates = {
      twist: fx.isMeshDistortionEffect('twist'),
      bend: fx.isMeshDistortionEffect('bend'),
      sway: fx.isMeshDistortionEffect('sway'),
      mirage: fx.isMeshDistortionEffect('mirage'),
      pulse: fx.isMeshDistortionEffect('pulse'),
      reflective: fx.isMeshDistortionEffect('reflective'),
    };

    const restTwist = fx.calculateMeshGrid(200, 100, 0, 1.0, null, 'twist', 4);
    const activeTwist = fx.calculateMeshGrid(200, 100, 0.7, 1.0, { cx: 0.5, cy: 0.5, maxRadiusRatio: 0.5 }, 'twist', 4);
    const activeSway = fx.calculateMeshGrid(200, 100, 0.7, 1.0, { cx: 0.5, cy: 0.2, maxRadiusRatio: 0.7 }, 'sway', 4);
    const activeBend = fx.calculateMeshGrid(200, 100, 0.7, 1.0, null, 'bend', 4);
    const activeMirage = fx.calculateMeshGrid(200, 100, 0.7, 1.0, null, 'mirage', 4);
    const dampedLarge = fx.calculateMeshGrid(1400, 700, 0.7, 1.0, null, 'twist', 2);
    const zeroSized = fx.calculateMeshGrid(0, 0, 0.7, 1.0, null, 'mirage', 2);

    return {
      predicates,
      gridRows: restTwist.length,
      gridCols: restTwist[0].length,
      restCornerTopLeft: restTwist[0][0],
      restCornerBottomRight: restTwist[4][4],
      twistCenter: activeTwist[2][2],
      twistCorner: activeTwist[0][0],
      // For sway, localCy is forced to H/2 regardless of centroid.cy, pinning bottom-center vertex (x=0, y=H/2).
      swayBottomCenter: activeSway[4][2],
      swayTopCenter: activeSway[0][2],
      bendTopCenter: activeBend[0][2],
      mirageCorner: activeMirage[0][0],
      mirageCenter: activeMirage[2][2],
      dampedCornerShift: Math.hypot(dampedLarge[0][0].x - (-700), dampedLarge[0][0].y - (-350)),
      zeroHasNaN: zeroSized.flat().some((pt) => Number.isNaN(pt.x) || Number.isNaN(pt.y)),
    };
  });

  expect(meshResults.predicates).toEqual({
    twist: true,
    bend: true,
    sway: true,
    mirage: false,
    pulse: false,
    reflective: false,
  });
  expect(meshResults.gridRows).toBe(5);
  expect(meshResults.gridCols).toBe(5);
  expect(meshResults.restCornerTopLeft.u).toBe(0);
  expect(meshResults.restCornerTopLeft.v).toBe(0);
  expect(meshResults.restCornerTopLeft.x).toBeCloseTo(-100, 6);
  expect(meshResults.restCornerTopLeft.y).toBeCloseTo(-50, 6);
  expect(meshResults.restCornerBottomRight.u).toBe(1);
  expect(meshResults.restCornerBottomRight.v).toBe(1);
  expect(meshResults.restCornerBottomRight.x).toBeCloseTo(100, 6);
  expect(meshResults.restCornerBottomRight.y).toBeCloseTo(50, 6);
  expect(meshResults.twistCenter.x).toBeCloseTo(0, 6);
  expect(meshResults.twistCenter.y).toBeCloseTo(0, 6);
  expect(meshResults.twistCorner.x).not.toBeCloseTo(-100, 1);

  expect(meshResults.swayBottomCenter.x).toBeCloseTo(0, 6);
  expect(meshResults.swayBottomCenter.y).toBeCloseTo(50, 6);
  expect(meshResults.swayTopCenter.x).not.toBeCloseTo(0, 1);
  expect(meshResults.bendTopCenter.x).not.toBeCloseTo(0, 1);

  expect(meshResults.mirageCenter.y).toBeLessThanOrEqual(0);
  expect(meshResults.mirageCorner.x).not.toBeCloseTo(-100, 2);
  expect(meshResults.dampedCornerShift).toBeGreaterThan(0);
  expect(meshResults.zeroHasNaN).toBe(false);
});

test('canvas draw functions validate inputs, preserve alpha masks, cache moments, and draw effects', async ({ canvas, page }) => {
  await canvas.mount({
    html: '<canvas id="out" width="120" height="80"></canvas>',
  });

  const drawReport = await page.evaluate(async () => {
    const fx = await window.__importModule<AnimationEffectsModule>('/static/generated/canvas/animation-effects.js');
    const outCanvas = document.getElementById('out') as HTMLCanvasElement;
    const outCtx = outCanvas.getContext('2d')!;

    const unloaded = document.createElement('img');
    const rejectedWrongEffect = fx.layerReflectiveDraw('pulse', 1, outCtx, unloaded, 100, 60);
    const rejectedUnloaded = fx.layerReflectiveDraw('reflective', 1, outCtx, unloaded, 100, 60);
    const rejectedMirageUnloaded = fx.layerMirageDraw('mirage', 1, outCtx, unloaded, 100, 60);
    const rejectedBlastUnloaded = fx.layerEnergyBlastDraw('energy_blast', 1, outCtx, unloaded, 100, 60);

    // Build a 60x40 piece with an opaque right half and fully transparent left half.
    const pieceCanvas = document.createElement('canvas');
    pieceCanvas.width = 60;
    pieceCanvas.height = 40;
    const pCtx = pieceCanvas.getContext('2d')!;
    pCtx.fillStyle = '#2a7fff';
    pCtx.fillRect(30, 6, 26, 28);

    const pieceImg = document.createElement('img');
    await new Promise<void>((resolve) => {
      pieceImg.onload = () => resolve();
      pieceImg.src = pieceCanvas.toDataURL();
    });

    const resetAndDraw = (fn: () => boolean): { ok: boolean; leftAlpha: number; rightAlpha: number } => {
      outCtx.setTransform(1, 0, 0, 1, 0, 0);
      outCtx.clearRect(0, 0, outCanvas.width, outCanvas.height);
      outCtx.save();
      outCtx.translate(outCanvas.width / 2, outCanvas.height / 2);
      const ok = fn();
      outCtx.restore();
      const leftAlpha = outCtx.getImageData(40, 40, 1, 1).data[3];
      const rightAlpha = outCtx.getImageData(75, 40, 1, 1).data[3];
      return { ok, leftAlpha, rightAlpha };
    };

    const reflective = resetAndDraw(() => fx.layerReflectiveDraw('reflective', 0.8, outCtx, pieceImg, 60, 40));
    const delegatedMirage = resetAndDraw(() => fx.layerReflectiveDraw('mirage', 1.2, outCtx, pieceImg, 60, 40));
    const flameRipple = resetAndDraw(() => fx.layerFlameRippleDraw('flame_ripple', 1.2, outCtx, pieceImg, 60, 40));
    const delegatedBlast = resetAndDraw(() => fx.layerReflectiveDraw('energy_blast', 1.4, outCtx, pieceImg, 60, 40));

    // Also test energy_blast with an all-transparent image (zero-mass fallback).
    const emptyCanvas = document.createElement('canvas');
    emptyCanvas.width = 20;
    emptyCanvas.height = 20;
    const emptyImg = document.createElement('img');
    await new Promise<void>((resolve) => {
      emptyImg.onload = () => resolve();
      emptyImg.src = emptyCanvas.toDataURL();
    });
    const emptyBlast = resetAndDraw(() => fx.layerEnergyBlastDraw('energy_blast', 0.5, outCtx, emptyImg, 20, 20));

    return {
      rejectedWrongEffect,
      rejectedUnloaded,
      rejectedMirageUnloaded,
      rejectedBlastUnloaded,
      reflective,
      delegatedMirage,
      flameRipple,
      delegatedBlast,
      emptyBlast,
    };
  });

  expect(drawReport.rejectedWrongEffect).toBe(false);
  expect(drawReport.rejectedUnloaded).toBe(false);
  expect(drawReport.rejectedMirageUnloaded).toBe(false);
  expect(drawReport.rejectedBlastUnloaded).toBe(false);

  expect(drawReport.reflective).toEqual({ ok: true, leftAlpha: 0, rightAlpha: 255 });
  expect(drawReport.delegatedMirage.ok).toBe(true);
  expect(drawReport.delegatedMirage.rightAlpha).toBe(255);
  expect(drawReport.flameRipple.ok).toBe(true);
  expect(drawReport.flameRipple.rightAlpha).toBe(255);
  expect(drawReport.delegatedBlast).toEqual({ ok: true, leftAlpha: 0, rightAlpha: 255 });
  expect(drawReport.emptyBlast).toEqual({ ok: true, leftAlpha: 0, rightAlpha: 0 });
});

test('mirage and energy_blast fall back cleanly on CORS-tainted images without throwing', async ({ canvas, page }) => {
  await canvas.serveRoute('https://other.test/piece.png', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: Buffer.from(PNG_1X1_BASE64, 'base64'),
    });
  });

  await canvas.mount({
    html: '<canvas id="out" width="40" height="40"></canvas>',
  });

  const result = await page.evaluate(async () => {
    const fx = await window.__importModule<AnimationEffectsModule>('/static/generated/canvas/animation-effects.js');
    const outCanvas = document.getElementById('out') as HTMLCanvasElement;
    const outCtx = outCanvas.getContext('2d')!;
    const img = document.createElement('img');
    await new Promise<void>((resolve) => {
      img.onload = () => resolve();
      img.src = 'https://other.test/piece.png';
    });

    outCtx.save();
    outCtx.translate(20, 20);
    const mirageOk = fx.layerMirageDraw('mirage', 1.0, outCtx, img, 20, 20);
    const blastOk = fx.layerEnergyBlastDraw('energy_blast', 1.0, outCtx, img, 20, 20);
    outCtx.restore();
    return { mirageOk, blastOk };
  });

  expect(result).toEqual({ mirageOk: true, blastOk: true });
});
