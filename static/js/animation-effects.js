/** Deterministic local motion and effect parameters for transparent animation layers. */

/**
 * Return canvas transform parameters for a named layer effect at `seconds`.
 * Keeping this pure lets every canvas surface render identical motion.
 */
export function layerTransform(effect, seconds) {
    switch (effect) {
    case "sway":
        return { x: 0, y: 0, rotation: 0, scale: 1 };
    case "gentle_rocking":
        return { x: 0, y: Math.sin(seconds * 1.2) * 1.2, rotation: Math.sin(seconds * 1.2) * 0.028, scale: 1 };
    case "vibrate":
        return { x: Math.sin(seconds * 9.1) * 1.2, y: Math.cos(seconds * 7.3) * 0.9, rotation: 0, scale: 1 };
    case "pulse":
        return { x: 0, y: 0, rotation: 0, scale: 1 + Math.sin(seconds * 1.15) * 0.008 };
    case "twist":
        return { x: 0, y: 0, rotation: Math.sin(seconds * 1.5) * 0.02, scale: 1 };
    case "bend":
        return { x: Math.sin(seconds * 1.5) * 1.5, y: 0, rotation: 0, scale: 1 };
    case "mirage":
        return { x: Math.sin(seconds * 4.5) * 0.8, y: -Math.abs(Math.sin(seconds * 3.0)) * 0.5, rotation: Math.sin(seconds * 2.0) * 0.005, scale: 1 };
    case "light_halo":
    case "dark_halo":
    case "ghostly":
    case "reflective":
    case "energy_blast":
        return { x: 0, y: 0, rotation: 0, scale: 1 };
    default:
        return { x: 0, y: 0, rotation: 0, scale: 1 };
    }
}

/** Return the matching CSS transform for lightweight Test Lab previews. */
export function layerCssTransform(effect, seconds) {
    const { x, y, rotation, scale } = layerTransform(effect, seconds);
    return `translate(${x}px, ${y}px) rotate(${rotation}rad) scale(${scale})`;
}

/** Return modulated opacity for rendering a layer effect at `seconds`. */
export function layerOpacity(effect, seconds, baseOpacity = 1.0) {
    if (effect === "ghostly") {
        const cycle = 0.5 + Math.sin(seconds * 1.5) * 0.4;
        return Math.max(0, Math.min(1, baseOpacity * cycle));
    }
    return baseOpacity;
}

/** Return parameters for rendering a halo aura behind the layer, or null if inapplicable. */
export function layerHaloParams(effect, seconds, amp = 1.0, opacity = 1.0) {
    if (effect !== "light_halo" && effect !== "dark_halo") return null;
    const isLight = effect === "light_halo";
    const haloPulse = 1.0 + Math.sin(seconds * 3.0) * 0.06 * amp;
    const haloAlpha = Math.max(0, Math.min(1, (0.5 + Math.sin(seconds * 3.0) * 0.15) * opacity));
    const haloFilter = isLight
        ? "blur(22px) saturate(4.0) brightness(2.5)"
        : "blur(22px) saturate(4.0) brightness(0.02) contrast(3.0)";
    return {
        isLight,
        haloScale: haloPulse * 1.08,
        haloAlpha,
        haloFilter,
    };
}

/** Return CSS filter for rendering the main image piece layer. */
export function layerPieceFilter(effect, seconds = 0) {
    if (effect === "dark_halo") {
        const darkBrightness = 0.60 + Math.sin(seconds * 3.0) * 0.12;
        return `brightness(${darkBrightness.toFixed(3)}) contrast(1.1)`;
    }
    if (effect === "light_halo") {
        const lightBrightness = 1.10 + Math.sin(seconds * 3.0) * 0.12;
        return `brightness(${lightBrightness.toFixed(3)})`;
    }
    return "none";
}

let offscreenCanvas = null;
let offscreenCtx = null;

function getOffscreenCanvas(width, height) {
    if (typeof document === "undefined") return null;
    if (!offscreenCanvas) {
        offscreenCanvas = document.createElement("canvas");
    }
    if (offscreenCanvas.width !== width || offscreenCanvas.height !== height) {
        offscreenCanvas.width = width;
        offscreenCanvas.height = height;
    }
    if (!offscreenCtx) {
        offscreenCtx = offscreenCanvas.getContext("2d");
    } else {
        offscreenCtx.clearRect(0, 0, width, height);
    }
    return offscreenCanvas;
}

let secondaryOffscreenCanvas = null;
let secondaryOffscreenCtx = null;
let tertiaryOffscreenCanvas = null;
let tertiaryOffscreenCtx = null;
let quaternaryOffscreenCanvas = null;
let quaternaryOffscreenCtx = null;

function getSecondaryOffscreenCanvas(width, height) {
    if (typeof document === "undefined") return null;
    if (!secondaryOffscreenCanvas) {
        secondaryOffscreenCanvas = document.createElement("canvas");
    }
    if (secondaryOffscreenCanvas.width !== width || secondaryOffscreenCanvas.height !== height) {
        secondaryOffscreenCanvas.width = width;
        secondaryOffscreenCanvas.height = height;
    }
    if (!secondaryOffscreenCtx) {
        secondaryOffscreenCtx = secondaryOffscreenCanvas.getContext("2d");
    } else {
        secondaryOffscreenCtx.clearRect(0, 0, width, height);
    }
    return secondaryOffscreenCanvas;
}

function getTertiaryOffscreenCanvas(width, height) {
    if (typeof document === "undefined") return null;
    if (!tertiaryOffscreenCanvas) {
        tertiaryOffscreenCanvas = document.createElement("canvas");
    }
    if (tertiaryOffscreenCanvas.width !== width || tertiaryOffscreenCanvas.height !== height) {
        tertiaryOffscreenCanvas.width = width;
        tertiaryOffscreenCanvas.height = height;
    }
    if (!tertiaryOffscreenCtx) {
        tertiaryOffscreenCtx = tertiaryOffscreenCanvas.getContext("2d");
    } else {
        tertiaryOffscreenCtx.clearRect(0, 0, width, height);
    }
    return tertiaryOffscreenCanvas;
}

function getQuaternaryOffscreenCanvas(width, height) {
    if (typeof document === "undefined") return null;
    if (!quaternaryOffscreenCanvas) {
        quaternaryOffscreenCanvas = document.createElement("canvas");
    }
    if (quaternaryOffscreenCanvas.width !== width || quaternaryOffscreenCanvas.height !== height) {
        quaternaryOffscreenCanvas.width = width;
        quaternaryOffscreenCanvas.height = height;
    }
    if (!quaternaryOffscreenCtx) {
        quaternaryOffscreenCtx = quaternaryOffscreenCanvas.getContext("2d");
    } else {
        quaternaryOffscreenCtx.clearRect(0, 0, width, height);
    }
    return quaternaryOffscreenCanvas;
}

function renderTwistOverlay(sourceImage, tw, th, activeBubbles, targetCanvas) {
    const ctx = targetCanvas.getContext("2d");
    ctx.save();
    ctx.clearRect(0, 0, tw, th);
    ctx.drawImage(sourceImage, 0, 0, tw, th);

    let srcPixels, dstPixels;
    try {
        srcPixels = ctx.getImageData(0, 0, tw, th);
        dstPixels = ctx.createImageData(tw, th);
    } catch {
        ctx.restore();
        return false;
    }

    const srcData = srcPixels.data;
    const dstData = dstPixels.data;

    for (let y = 0; y < th; y++) {
        for (let x = 0; x < tw; x++) {
            let sampleX = x;
            let sampleY = y;

            for (let i = 0; i < activeBubbles.length; i++) {
                const bubble = activeBubbles[i];
                const dx = x - bubble.cx;
                const dy = y - bubble.cy;
                const r = Math.hypot(dx, dy);
                const R = bubble.R;

                if (r >= 0 && r <= R) {
                    const u = r / R;
                    // Strength is strictly 0 at center (u=0) and edge (u=1), peaking at u=0.5 (r=R/2)
                    // Derivative S'(u) is also strictly 0 at u=0 and u=1
                    const twistStrength = (u <= 0 || u >= 1) ? 0 : 0.5 * (1 - Math.cos(2 * Math.PI * u)) * bubble.peakAngle;
                    
                    if (twistStrength !== 0) {
                        // Apply angular twist rotation around bubble center
                        const angle = Math.atan2(dy, dx) + twistStrength;
                        sampleX = bubble.cx + r * Math.cos(angle);
                        sampleY = bubble.cy + r * Math.sin(angle);
                    }
                }
            }

            // Clamp sample coordinates with boundary limits
            const sx = Math.max(0, Math.min(tw - 1, Math.round(sampleX)));
            const sy = Math.max(0, Math.min(th - 1, Math.round(sampleY)));

            const targetIdx = (y * tw + x) * 4;
            const sourceIdx = (sy * tw + sx) * 4;

            dstData[targetIdx] = srcData[sourceIdx];
            dstData[targetIdx + 1] = srcData[sourceIdx + 1];
            dstData[targetIdx + 2] = srcData[sourceIdx + 2];
            dstData[targetIdx + 3] = srcData[sourceIdx + 3];
        }
    }

    ctx.putImageData(dstPixels, 0, 0);
    ctx.restore();
    return true;
}

/** Render piece image with mirage distortions split across three extra layers at 25% opacity each. */
export function layerMirageDraw(effect, seconds, context, sourceImage, drawWidth, drawHeight) {
    if (effect !== "mirage" && effect !== "flame_ripple") return false;
    if (!sourceImage || !sourceImage.naturalWidth || !sourceImage.naturalHeight) return false;

    const w = sourceImage.naturalWidth;
    const h = sourceImage.naturalHeight;

    // Target resolution for smooth, fast twist calculation (max 320px width)
    const tw = Math.min(320, w);
    const th = Math.max(1, Math.round(tw * h / w));

    const canvas = getOffscreenCanvas(tw, th);
    const overlayCanvas1 = getSecondaryOffscreenCanvas(tw, th);
    const overlayCanvas2 = getTertiaryOffscreenCanvas(tw, th);
    const overlayCanvas3 = getQuaternaryOffscreenCanvas(tw, th);
    if (!canvas || !overlayCanvas1 || !overlayCanvas2 || !overlayCanvas3) return false;

    const offCtx = offscreenCtx;
    const pieceDimension = Math.sqrt(tw * th); // Proportional scale matching image piece area
    const pieceCenterX = tw / 2;
    const pieceCenterY = th / 2;
    const maxDist = Math.hypot(pieceCenterX, pieceCenterY) || 1;

    // Build 3 sets of 8 twist circles (total 24 circles split between three extra layers)
    const activeBubbles1 = [];
    const activeBubbles2 = [];
    const activeBubbles3 = [];

    const numCirclesPerLayer = 8;
    for (let i = 0; i < numCirclesPerLayer; i++) {
        // Layer 1 circles (8 circles)
        const speed1 = 0.045 + (i % 4) * 0.009;
        const phase1 = (i / numCirclesPerLayer) + (i % 3) * 0.05;
        const distRatio1 = (phase1 + seconds * speed1) % 1.0;
        const spawnX1 = tw * (0.28 + (i % 6) * 0.08);
        const spawnY1 = th * (0.88 + (i % 3) * 0.06);
        const weave1 = Math.sin(distRatio1 * Math.PI * 3 + i * 1.4) * (tw * 0.12);
        const cx1 = spawnX1 + weave1;
        const cy1 = spawnY1 - distRatio1 * (th * 0.95);
        // Radius scaled proportionally to image piece size
        const R1 = pieceDimension * (0.15 + (i % 4) * 0.03);
        const distFromCenter1 = Math.hypot(cx1 - pieceCenterX, cy1 - pieceCenterY);
        const farFade1 = Math.max(0, 1.0 - Math.min(1.0, distFromCenter1 / (maxDist * 0.65)));
        const entryFade1 = Math.min(1.0, distRatio1 * 5.0);
        const lifeOpacity1 = entryFade1 * farFade1;
        if (lifeOpacity1 > 0.01) {
            const dir1 = (i % 2 === 0) ? 1 : -1;
            const peakAngle1 = 0.45 * dir1 * Math.sin(seconds * 3.5 + i) * lifeOpacity1;
            activeBubbles1.push({ cx: cx1, cy: cy1, R: R1, peakAngle: peakAngle1 });
        }

        // Layer 2 circles (8 circles, phase offset +0.33)
        const speed2 = 0.050 + (i % 3) * 0.011;
        const phase2 = ((i + 0.33) / numCirclesPerLayer) + (i % 4) * 0.06;
        const distRatio2 = (phase2 + seconds * speed2) % 1.0;
        const spawnX2 = tw * (0.32 + (i % 5) * 0.09);
        const spawnY2 = th * (0.92 + (i % 4) * 0.04);
        const weave2 = Math.cos(distRatio2 * Math.PI * 3 + i * 1.8) * (tw * 0.14);
        const cx2 = spawnX2 + weave2;
        const cy2 = spawnY2 - distRatio2 * (th * 0.95);
        // Radius scaled proportionally to image piece size
        const R2 = pieceDimension * (0.16 + (i % 3) * 0.03);
        const distFromCenter2 = Math.hypot(cx2 - pieceCenterX, cy2 - pieceCenterY);
        const farFade2 = Math.max(0, 1.0 - Math.min(1.0, distFromCenter2 / (maxDist * 0.65)));
        const entryFade2 = Math.min(1.0, distRatio2 * 5.0);
        const lifeOpacity2 = entryFade2 * farFade2;
        if (lifeOpacity2 > 0.01) {
            const dir2 = (i % 2 === 1) ? 1 : -1;
            const peakAngle2 = 0.45 * dir2 * Math.cos(seconds * 3.2 + i * 1.3) * lifeOpacity2;
            activeBubbles2.push({ cx: cx2, cy: cy2, R: R2, peakAngle: peakAngle2 });
        }

        // Layer 3 circles (8 circles, phase offset +0.66)
        const speed3 = 0.042 + (i % 5) * 0.010;
        const phase3 = ((i + 0.66) / numCirclesPerLayer) + (i % 3) * 0.08;
        const distRatio3 = (phase3 + seconds * speed3) % 1.0;
        const spawnX3 = tw * (0.25 + (i % 4) * 0.11);
        const spawnY3 = th * (0.90 + (i % 3) * 0.05);
        const weave3 = Math.sin(distRatio3 * Math.PI * 2.5 + i * 2.1) * (tw * 0.13);
        const cx3 = spawnX3 + weave3;
        const cy3 = spawnY3 - distRatio3 * (th * 0.95);
        // Radius scaled proportionally to image piece size
        const R3 = pieceDimension * (0.14 + (i % 4) * 0.04);
        const distFromCenter3 = Math.hypot(cx3 - pieceCenterX, cy3 - pieceCenterY);
        const farFade3 = Math.max(0, 1.0 - Math.min(1.0, distFromCenter3 / (maxDist * 0.65)));
        const entryFade3 = Math.min(1.0, distRatio3 * 5.0);
        const lifeOpacity3 = entryFade3 * farFade3;
        if (lifeOpacity3 > 0.01) {
            const dir3 = (i % 3 === 0) ? -1 : 1;
            const peakAngle3 = 0.45 * dir3 * Math.sin(seconds * 4.0 + i * 1.7) * lifeOpacity3;
            activeBubbles3.push({ cx: cx3, cy: cy3, R: R3, peakAngle: peakAngle3 });
        }
    }

    // Render Layer 1 overlay (8 circles), Layer 2 overlay (8 circles), and Layer 3 overlay (8 circles)
    const ok1 = renderTwistOverlay(sourceImage, tw, th, activeBubbles1, overlayCanvas1);
    const ok2 = renderTwistOverlay(sourceImage, tw, th, activeBubbles2, overlayCanvas2);
    const ok3 = renderTwistOverlay(sourceImage, tw, th, activeBubbles3, overlayCanvas3);

    if (!ok1 || !ok2 || !ok3) {
        context.drawImage(sourceImage, -drawWidth / 2, -drawHeight / 2, drawWidth, drawHeight);
        return true;
    }

    // Composite: 1. Clean base piece image (100% opacity)
    offCtx.save();
    offCtx.drawImage(sourceImage, 0, 0, tw, th);

    // Composite: 2. Extra Layer 1 at 25% opacity
    offCtx.globalAlpha = 0.25;
    offCtx.drawImage(overlayCanvas1, 0, 0);

    // Composite: 3. Extra Layer 2 at 25% opacity
    offCtx.globalAlpha = 0.25;
    offCtx.drawImage(overlayCanvas2, 0, 0);

    // Composite: 4. Extra Layer 3 at 25% opacity
    offCtx.globalAlpha = 0.25;
    offCtx.drawImage(overlayCanvas3, 0, 0);

    offCtx.restore();

    context.drawImage(canvas, -drawWidth / 2, -drawHeight / 2, drawWidth, drawHeight);
    return true;
}

export const layerFlameRippleDraw = layerMirageDraw;

/** Render piece image with a sweeping reflective sheen restricted strictly to the piece's alpha mask. */
export function layerReflectiveDraw(effect, seconds, context, sourceImage, drawWidth, drawHeight) {
    if (effect === "mirage" || effect === "flame_ripple") {
        return layerMirageDraw(effect, seconds, context, sourceImage, drawWidth, drawHeight);
    }
    if (effect === "energy_blast") {
        return layerEnergyBlastDraw(effect, seconds, context, sourceImage, drawWidth, drawHeight);
    }
    if (effect !== "reflective") return false;
    if (!sourceImage || !sourceImage.naturalWidth || !sourceImage.naturalHeight) return false;

    const w = sourceImage.naturalWidth;
    const h = sourceImage.naturalHeight;
    const canvas = getOffscreenCanvas(w, h);
    if (!canvas) return false;

    const offCtx = offscreenCtx;
    offCtx.save();
    offCtx.drawImage(sourceImage, 0, 0, w, h);

    const sweepPhase = (Math.sin(seconds * 1.6) + 1) / 2;
    const center = -w * 0.6 + sweepPhase * (w * 2.2);
    const gradient = offCtx.createLinearGradient(
        center - w * 0.35,
        0,
        center + w * 0.35,
        h
    );
    gradient.addColorStop(0, "rgba(255, 255, 255, 0)");
    gradient.addColorStop(0.35, "rgba(255, 245, 220, 0.12)");
    gradient.addColorStop(0.50, "rgba(255, 255, 255, 0.65)");
    gradient.addColorStop(0.65, "rgba(255, 245, 220, 0.12)");
    gradient.addColorStop(1, "rgba(255, 255, 255, 0)");

    offCtx.globalCompositeOperation = "source-atop";
    offCtx.fillStyle = gradient;
    offCtx.fillRect(0, 0, w, h);
    offCtx.restore();

    context.drawImage(canvas, -drawWidth / 2, -drawHeight / 2, drawWidth, drawHeight);
    return true;
}

function getPieceDominantHue(sourceImage) {
    const key = sourceImage?.currentSrc || sourceImage?.src || "";
    if (sourceImage._dominantHueKey === key && sourceImage._dominantHue !== undefined) {
        return sourceImage._dominantHue;
    }
    if (!sourceImage || !sourceImage.naturalWidth) return 200;
    try {
        const tempCanvas = document.createElement("canvas");
        const w = Math.min(64, sourceImage.naturalWidth);
        const h = Math.max(1, Math.round(w * sourceImage.naturalHeight / sourceImage.naturalWidth));
        tempCanvas.width = w; tempCanvas.height = h;
        const tempCtx = tempCanvas.getContext("2d", { willReadFrequently: true });
        tempCtx.drawImage(sourceImage, 0, 0, w, h);
        const data = tempCtx.getImageData(0, 0, w, h).data;

        let totalR = 0, totalG = 0, totalB = 0, count = 0;
        for (let i = 0; i < data.length; i += 4) {
            if (data[i + 3] > 30) {
                totalR += data[i];
                totalG += data[i + 1];
                totalB += data[i + 2];
                count++;
            }
        }
        if (count === 0) {
            sourceImage._dominantHueKey = key;
            sourceImage._dominantHue = 200;
            return 200;
        }
        const r = totalR / count / 255;
        const g = totalG / count / 255;
        const b = totalB / count / 255;
        const max = Math.max(r, g, b), min = Math.min(r, g, b);
        let hDeg = 0;
        if (max !== min) {
            const d = max - min;
            if (max === r) hDeg = ((g - b) / d + (g < b ? 6 : 0)) * 60;
            else if (max === g) hDeg = ((b - r) / d + 2) * 60;
            else if (max === b) hDeg = ((r - g) / d + 4) * 60;
        }
        sourceImage._dominantHueKey = key;
        sourceImage._dominantHue = hDeg;
        return hDeg;
    } catch {
        sourceImage._dominantHueKey = key;
        sourceImage._dominantHue = 200;
        return 200;
    }
}

function getPieceMassMoments(sourceImage) {
    const key = sourceImage?.currentSrc || sourceImage?.src || "";
    if (sourceImage._massMomentsKey === key && sourceImage._massMoments !== undefined) {
        return sourceImage._massMoments;
    }
    if (!sourceImage || !sourceImage.naturalWidth) {
        return {
            startX: 20, startY: 20, endX: 200, endY: 200, highCx: 200, highCy: 200,
            dx: 0.707, dy: 0.707, nx: -0.707, ny: 0.707,
            majorSpan: 250, minorSpan: 50, waveWidth: 30
        };
    }

    try {
        const tempCanvas = document.createElement("canvas");
        const w = Math.min(128, sourceImage.naturalWidth);
        const h = Math.max(1, Math.round(w * sourceImage.naturalHeight / sourceImage.naturalWidth));
        tempCanvas.width = w; tempCanvas.height = h;
        const tempCtx = tempCanvas.getContext("2d", { willReadFrequently: true });
        tempCtx.drawImage(sourceImage, 0, 0, w, h);
        const data = tempCtx.getImageData(0, 0, w, h).data;

        // 1. 0th & 1st Order Spatial Moments
        let M = 0, m10 = 0, m01 = 0;
        for (let y = 0; y < h; y++) {
            for (let x = 0; x < w; x++) {
                const alpha = data[(y * w + x) * 4 + 3];
                if (alpha > 30) {
                    M += alpha;
                    m10 += x * alpha;
                    m01 += y * alpha;
                }
            }
        }

        if (M === 0) {
            const fallbackMoments = {
                startX: 0, startY: 0,
                endX: sourceImage.naturalWidth, endY: sourceImage.naturalHeight,
                highCx: sourceImage.naturalWidth * 0.75, highCy: sourceImage.naturalHeight * 0.75,
                dx: 0.707, dy: 0.707, nx: -0.707, ny: 0.707,
                majorSpan: sourceImage.naturalWidth, minorSpan: 40, waveWidth: 30
            };
            sourceImage._massMomentsKey = key;
            sourceImage._massMoments = fallbackMoments;
            return fallbackMoments;
        }

        const cx = m10 / M;
        const cy = m01 / M;

        // 2. 2nd Order Central Moments & Inertia Eigenvalues
        let mu20 = 0, mu02 = 0, mu11 = 0;
        for (let y = 0; y < h; y++) {
            for (let x = 0; x < w; x++) {
                const alpha = data[(y * w + x) * 4 + 3];
                if (alpha > 30) {
                    const dxPixel = x - cx;
                    const dyPixel = y - cy;
                    mu20 += dxPixel * dxPixel * alpha;
                    mu02 += dyPixel * dyPixel * alpha;
                    mu11 += dxPixel * dyPixel * alpha;
                }
            }
        }

        // 3. Principal Major Axis Angle
        const theta = 0.5 * Math.atan2(2 * mu11, mu20 - mu02);
        const uX = Math.cos(theta);
        const uY = Math.sin(theta);
        const vX = -uY;
        const vY = uX;

        // 4. Inertia Eigenvalues & Ratio
        const delta = (mu20 - mu02) / 2;
        const sumVal = (mu20 + mu02) / 2;
        const R = Math.sqrt(delta * delta + mu11 * mu11);
        const lambda1 = Math.max(1.0, sumVal + R);
        const lambda2 = Math.max(1.0, sumVal - R);
        const inertiaRatio = Math.sqrt(lambda2 / lambda1); // Minor to major standard deviation ratio

        // 5. Extents along principal axis u and perpendicular axis v
        let minP = Infinity, maxP = -Infinity;
        let minPerp = Infinity, maxPerp = -Infinity;

        for (let y = 0; y < h; y++) {
            for (let x = 0; x < w; x++) {
                const alpha = data[(y * w + x) * 4 + 3];
                if (alpha > 30) {
                    const dxPixel = x - cx;
                    const dyPixel = y - cy;
                    const p = dxPixel * uX + dyPixel * uY;
                    const perp = dxPixel * vX + dyPixel * vY;

                    if (p < minP) minP = p;
                    if (p > maxP) maxP = p;
                    if (perp < minPerp) minPerp = perp;
                    if (perp > maxPerp) maxPerp = perp;
                }
            }
        }

        // Split major axis at midpoint of total extent to cleanly separate low-mass and high-mass halves
        const pMid = (minP + maxP) / 2;

        let mass0 = 0, mass1 = 0;
        let sumX0 = 0, sumY0 = 0;
        let sumX1 = 0, sumY1 = 0;

        for (let y = 0; y < h; y++) {
            for (let x = 0; x < w; x++) {
                const alpha = data[(y * w + x) * 4 + 3];
                if (alpha > 30) {
                    const dxPixel = x - cx;
                    const dyPixel = y - cy;
                    const p = dxPixel * uX + dyPixel * uY;

                    if (p < pMid) {
                        mass0 += alpha;
                        sumX0 += x * alpha;
                        sumY0 += y * alpha;
                    } else {
                        mass1 += alpha;
                        sumX1 += x * alpha;
                        sumY1 += y * alpha;
                    }
                }
            }
        }

        // Flow Direction vector d (towards higher mass end)
        const isPosHigh = mass1 >= mass0;
        const dx = isPosHigh ? uX : -uX;
        const dy = isPosHigh ? uY : -uY;
        const nx = -dy;
        const ny = dx;

        const scaleX = sourceImage.naturalWidth / w;
        const scaleY = sourceImage.naturalHeight / h;

        // Full length endpoints from very low mass tip to very high mass tip
        const pStart = isPosHigh ? minP : maxP;
        const pEnd = isPosHigh ? maxP : minP;

        const startX = (cx + pStart * uX) * scaleX;
        const startY = (cy + pStart * uY) * scaleY;
        const endX = (cx + pEnd * uX) * scaleX;
        const endY = (cy + pEnd * uY) * scaleY;

        // High-mass half centroid (explosion center) and low-mass half centroid
        const highCx = (isPosHigh ? (mass1 > 0 ? sumX1 / mass1 : cx) : (mass0 > 0 ? sumX0 / mass0 : cx)) * scaleX;
        const highCy = (isPosHigh ? (mass1 > 0 ? sumY1 / mass1 : cy) : (mass0 > 0 ? sumY0 / mass0 : cy)) * scaleY;

        const lowCx = (isPosHigh ? (mass0 > 0 ? sumX0 / mass0 : cx) : (mass1 > 0 ? sumX1 / mass1 : cx)) * scaleX;
        const lowCy = (isPosHigh ? (mass0 > 0 ? sumY0 / mass0 : cy) : (mass1 > 0 ? sumY1 / mass1 : cy)) * scaleY;

        const majorSpan = (maxP - minP) * Math.max(scaleX, scaleY);
        const minorSpan = (maxPerp - minPerp) * Math.max(scaleX, scaleY);

        // Transverse wave width scaled narrower based on the inertia ratio
        const waveWidth = Math.max(10, minorSpan * Math.min(1.0, Math.max(0.20, inertiaRatio * 2.2)));

        const calculatedMoments = {
            startX, startY, endX, endY, lowCx, lowCy, highCx, highCy,
            dx, dy, nx, ny,
            majorSpan: Math.max(20, majorSpan),
            minorSpan: Math.max(20, minorSpan),
            waveWidth,
            inertiaRatio
        };
        sourceImage._massMomentsKey = key;
        sourceImage._massMoments = calculatedMoments;
        return calculatedMoments;
    } catch {
        const fallbackMoments = {
            startX: 0, startY: 0,
            endX: sourceImage.naturalWidth, endY: sourceImage.naturalHeight,
            highCx: sourceImage.naturalWidth * 0.75, highCy: sourceImage.naturalHeight * 0.75,
            dx: 0.707, dy: 0.707, nx: -0.707, ny: 0.707,
            majorSpan: sourceImage.naturalWidth * 0.7, minorSpan: sourceImage.naturalHeight * 0.4,
            waveWidth: 30, inertiaRatio: 0.5
        };
        sourceImage._massMomentsKey = key;
        sourceImage._massMoments = fallbackMoments;
        return fallbackMoments;
    }
}

/** Render piece image with translucent jagged wave fronts traveling along major dimension and winking out. */
export function layerEnergyBlastDraw(effect, seconds, context, sourceImage, drawWidth, drawHeight) {
    if (effect !== "energy_blast") return false;
    if (!sourceImage || !sourceImage.naturalWidth || !sourceImage.naturalHeight) return false;

    const w = sourceImage.naturalWidth;
    const h = sourceImage.naturalHeight;
    const canvas = getOffscreenCanvas(w, h);
    if (!canvas) return false;

    const offCtx = offscreenCtx;
    offCtx.save();
    offCtx.drawImage(sourceImage, 0, 0, w, h);

    // Restrict wave fronts and ripples strictly within the alpha mask of the piece
    offCtx.globalCompositeOperation = "source-atop";

    const baseHue = getPieceDominantHue(sourceImage);
    const moments = getPieceMassMoments(sourceImage);
    const { startX, startY, endX, endY, highCx, highCy, dx, dy, nx, ny, minorSpan, waveWidth } = moments;

    // 1. High-mass half centroid ripple waves (8 radial/jagged arcs expanding from highCx, highCy)
    const numRipples = 8;
    for (let k = 0; k < numRipples; k++) {
        const speedK = 0.16 + (k % 4) * 0.04;
        const seedK = (Math.sin(k * 19.17 + 2.45) * 43758.5453) % 1.0;
        const phaseK = (seconds * speedK + Math.abs(seedK)) % 1.0;
        const maxRadiusK = minorSpan * 0.45;
        const radiusK = phaseK * maxRadiusK;
        const opacityK = (0.60 + (k % 3) * 0.15) * Math.sin(phaseK * Math.PI) * (0.65 + 0.35 * Math.sin(seconds * 5.0 + k * 2.7));

        if (opacityK > 0.01) {
            // Slight hue offset; desaturation (sFactor -> 0) is tied directly to high brightness (lightK -> 92%)
            const hueOffsetK = Math.max(0.0, baseHue - 12.0 - (k % 3) * 3.0);
            const sFactorK = 0.5 + 0.5 * Math.sin(seconds * 4.5 + k * 2.1);
            const satK = 15.0 + 80.0 * sFactorK;
            const lightK = 92.0 - 32.0 * sFactorK;

            offCtx.lineWidth = Math.max(4.5, Math.min(w, h) * 0.016);
            offCtx.lineCap = "round";
            offCtx.lineJoin = "miter";
            offCtx.strokeStyle = `hsla(${hueOffsetK.toFixed(1)}, ${satK.toFixed(1)}%, ${lightK.toFixed(1)}%, ${opacityK.toFixed(3)})`;

            offCtx.beginPath();
            const arcSteps = 16;
            for (let s = 0; s <= arcSteps; s++) {
                const angle = (s / arcSteps) * Math.PI * 2;
                const jaggedNoise = Math.sin(s * 3.5 + k * 4.2 + seconds * 4.0) * (minorSpan * 0.025);
                const r = radiusK + jaggedNoise;
                const rx = highCx + r * Math.cos(angle);
                const ry = highCy + r * Math.sin(angle);
                if (s === 0) offCtx.moveTo(rx, ry);
                else offCtx.lineTo(rx, ry);
            }
            offCtx.stroke();
        }
    }

    // 2. Low-mass wave fronts traveling along (dx, dy) from low-mass tip to highCx, highCy
    const numFronts = 18;
    const numPoints = 14;

    for (let i = 0; i < numFronts; i++) {
        const seedRaw = Math.sin(i * 13.37 + 1.23) * 43758.5453;
        const seed = seedRaw - Math.floor(seedRaw);

        const speed = 0.18 + (i % 6) * 0.05;
        const phase = (seconds * speed + seed) % 1.0;

        const sineFade = Math.sin(phase * Math.PI);
        const winkFlicker = 0.60 + 0.40 * Math.sin(seconds * 6.0 + i * 3.5);
        const frontOpacity = (0.60 + (i % 3) * 0.15) * sineFade * winkFlicker;

        const convergence = Math.pow(phase, 1.4);

        if (frontOpacity > 0.01) {
            const lineWidth = Math.max(5.0, Math.min(w, h) * (0.018 + (i % 3) * 0.006));
            
            // Slight hue offset; desaturation (sFactor -> 0) is tied directly to high brightness (lightI -> 92%)
            const hueOffset = Math.max(0.0, baseHue - 12.0 - (i % 3) * 3.0);
            const sFactorI = 0.5 + 0.5 * Math.sin(seconds * 5.0 + i * 2.8 + phase * 6.28);
            const satI = 15.0 + 80.0 * sFactorI;
            const lightI = 92.0 - 32.0 * sFactorI;

            offCtx.lineWidth = lineWidth;
            offCtx.lineCap = "round";
            offCtx.lineJoin = "miter";
            offCtx.strokeStyle = `hsla(${hueOffset.toFixed(1)}, ${satI.toFixed(1)}%, ${lightI.toFixed(1)}%, ${frontOpacity.toFixed(3)})`;

            // Travels from startX/startY (low-mass tip) directly to highCx/highCy (explosion centroid)
            const baseX = startX + phase * (highCx - startX);
            const baseY = startY + phase * (highCy - startY);

            offCtx.beginPath();
            for (let j = 0; j <= numPoints; j++) {
                const t = j / numPoints - 0.5;
                const minorDist = t * waveWidth;

                const jaggedNoise = Math.sin(j * 3.7 + i * 5.1 + seconds * 9.0) * (waveWidth * 0.08)
                                  + Math.cos(j * 7.3 - i * 2.9) * (waveWidth * 0.05);
                const bow = Math.sin((t + 0.5) * Math.PI) * (waveWidth * 0.08);

                const pxUnconv = baseX + minorDist * nx + (jaggedNoise + bow) * dx;
                const pyUnconv = baseY + minorDist * ny + (jaggedNoise + bow) * dy;

                const targetRadius = (1.0 - phase) * (waveWidth * 0.6);
                const arcAngle = t * Math.PI * 1.3;
                const ax = Math.cos(arcAngle) * (-dx) + Math.sin(arcAngle) * nx;
                const ay = Math.cos(arcAngle) * (-dy) + Math.sin(arcAngle) * ny;

                const pxConv = highCx + (targetRadius + jaggedNoise) * ax;
                const pyConv = highCy + (targetRadius + jaggedNoise) * ay;

                const px = (1 - convergence) * pxUnconv + convergence * pxConv;
                const py = (1 - convergence) * pyUnconv + convergence * pyConv;

                if (j === 0) offCtx.moveTo(px, py);
                else offCtx.lineTo(px, py);
            }
            offCtx.stroke();
        }
    }
    offCtx.restore();

    context.drawImage(canvas, -drawWidth / 2, -drawHeight / 2, drawWidth, drawHeight);
    return true;
}

/** Return whether the effect requires non-linear mesh distortion. */
export function isMeshDistortionEffect(effect) {
    return effect === "twist" || effect === "bend" || effect === "sway";
}

/** Compute distorted vertex mesh for twist, bend, sway, and mirage effects. */
export function calculateMeshGrid(drawWidth, drawHeight, phase, amplitude = 1.0, centroid = null, mode = "twist", gridDivs = 12) {
    const W = drawWidth, H = drawHeight;
    const localCx = -W / 2 + (centroid?.cx ?? 0.5) * W;
    const localCy = mode === "sway" ? H / 2 : -H / 2 + (centroid?.cy ?? 0.5) * H;
    const Rmax = Math.max(1, (centroid?.maxRadiusRatio ?? 0.707) * Math.max(W, H));
    const GRID = gridDivs;
    const majorDim = Math.max(W, H);
    const refDim = 350;
    const dimDamping = Math.max(0.25, refDim / Math.max(refDim, majorDim));
    const torque = Math.sin(phase * 2.0) * (amplitude * 0.45) * dimDamping;
    const vertices = [];
    for (let j = 0; j <= GRID; j++) {
        const row = []; const v = j / GRID; const y0 = -H / 2 + v * H;
        for (let i = 0; i <= GRID; i++) {
            const u = i / GRID; const x0 = -W / 2 + u * W;
            const dx = x0 - localCx, dy = y0 - localCy;
            const r = Math.hypot(dx, dy), angle = Math.atan2(dy, dx);
            if (mode === "mirage" || mode === "flame_ripple") {
                const distRatio = Math.min(1.0, r / Rmax);
                const distFade = Math.exp(-distRatio * 2.2);
                const rippleWave = Math.sin(phase * 6.5 - (y0 / (H || 1)) * 12.0 + (x0 / (W || 1)) * 4.0);
                const crossWave = Math.cos(phase * 4.8 + (y0 / (H || 1)) * 8.0);
                const shiftX = (rippleWave * 6.0 + crossWave * 2.5) * amplitude * dimDamping * distFade;
                const shiftY = (-Math.abs(rippleWave) * 4.0) * amplitude * dimDamping * distFade;
                row.push({ u, v, x: x0 + shiftX, y: y0 + shiftY });
            } else {
                const dTheta = (mode === "bend" || mode === "sway") ? torque * (dy / Rmax) : torque * (r / Rmax);
                const newAngle = angle + dTheta;
                row.push({ u, v, x: localCx + r * Math.cos(newAngle), y: localCy + r * Math.sin(newAngle) });
            }
        }
        vertices.push(row);
    }
    return vertices;
}
