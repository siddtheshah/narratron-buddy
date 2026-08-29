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
        return { x: 0, y: 0, rotation: Math.sin(seconds * 1.2) * 0.012, scale: 1 };
    case "vibrate":
        return { x: Math.sin(seconds * 9.1) * 1.2, y: Math.cos(seconds * 7.3) * 0.9, rotation: 0, scale: 1 };
    case "pulse":
        return { x: 0, y: 0, rotation: 0, scale: 1 + Math.sin(seconds * 1.15) * 0.008 };
    case "twist":
        return { x: 0, y: 0, rotation: Math.sin(seconds * 1.5) * 0.02, scale: 1 };
    case "bend":
        return { x: Math.sin(seconds * 1.5) * 1.5, y: 0, rotation: 0, scale: 1 };
    case "light_halo":
    case "dark_halo":
    case "ghostly":
    case "reflective":
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

/** Render piece image with a sweeping reflective sheen restricted strictly to the piece's alpha mask. */
export function layerReflectiveDraw(effect, seconds, context, sourceImage, drawWidth, drawHeight) {
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

/** Return whether the effect requires non-linear mesh distortion. */
export function isMeshDistortionEffect(effect) {
    return effect === "twist" || effect === "bend" || effect === "sway";
}

/** Compute distorted vertex mesh for twist, bend, and sway effects. */
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
            const dTheta = (mode === "bend" || mode === "sway") ? torque * (dy / Rmax) : torque * (r / Rmax);
            const newAngle = angle + dTheta;
            row.push({ u, v, x: localCx + r * Math.cos(newAngle), y: localCy + r * Math.sin(newAngle) });
        }
        vertices.push(row);
    }
    return vertices;
}
