/** Deterministic local motion for transparent animation layers. */

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
    case "drift":
        return { x: Math.sin(seconds * 0.65) * 3, y: Math.cos(seconds * 0.5) * 1.5, rotation: 0, scale: 1 };
    case "breathe":
        return { x: 0, y: 0, rotation: 0, scale: 1 + Math.sin(seconds * 1.15) * 0.008 };
    case "twist":
        return { x: 0, y: 0, rotation: Math.sin(seconds * 1.5) * 0.02, scale: 1 };
    case "bend":
        return { x: Math.sin(seconds * 1.5) * 1.5, y: 0, rotation: 0, scale: 1 };
    default:
        return { x: 0, y: 0, rotation: 0, scale: 1 };
    }
}

/** Return the matching CSS transform for lightweight Test Lab previews. */
export function layerCssTransform(effect, seconds) {
    const { x, y, rotation, scale } = layerTransform(effect, seconds);
    return `translate(${x}px, ${y}px) rotate(${rotation}rad) scale(${scale})`;
}
