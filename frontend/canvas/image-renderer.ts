import {
    attachImageEffect,
    type ImageEffectController,
    IMAGE_EFFECTS,
    IMAGE_EFFECT_DEFAULT_INTENSITIES,
} from "../shared/image-effects.js";
import {
    layerTransform,
    layerOpacity,
    layerHaloParams,
    layerPieceFilter,
    layerReflectiveDraw,
    layerMirageDraw,
    layerEnergyBlastDraw,
    isMeshDistortionEffect,
    calculateMeshGrid,
    type ImageCentroid,
    type MeshVertex,
} from "../shared/animation-effects.js";


export interface ImageRendererOptions {
    canvas: HTMLCanvasElement | null;
    image?: HTMLImageElement | null;
    backgroundLayer?: HTMLElement | null;
    sizingElement?: HTMLElement | null;
    fadeDuration?: number;
    loadedClassNames?: string[];
    fitMode?: 'contain' | 'cover';
    imageEffectsEnabled?: boolean | (() => boolean);
}
export interface SequenceOptions {
    frameDuration?: number;
    crossfadeDuration?: number;
    transitionDuration?: number;
}
export interface AnimationOptions {
    crossfadeDuration?: number;
    fadeDuration?: number;
    video_duration_seconds?: number;
    loop?: boolean;
    local_video_url?: string;
    fallback_url?: string;
    video_path?: string;
}
export interface AnimationLayer {
    url: string;
    effect: string;
    scale?: number;
    opacity?: number;
    speed?: number;
    amplitude?: number;
    order?: number;
}
type PreparedLayer = AnimationLayer & { source: CachedImage };
interface CachedImage extends HTMLImageElement {
    _centroidKey?: string;
    _centroid?: ImageCentroid;
}
interface SilentVideo extends HTMLVideoElement {
    audioTracks?: ArrayLike<{ enabled: boolean }>;
}
export interface ImageRenderer {
    resize(): void;
    applyTransition(url: string, transition?: string, effect?: string): Promise<void>;
    playSequence(urls: string[], options?: SequenceOptions): Promise<void>;
    playLayeredAnimation(layers: AnimationLayer[], options?: AnimationOptions | number): Promise<void>;
    playVideoAnimation(url: string, options?: AnimationOptions | number): Promise<void>;
    stopSequence(): void;
    setImageEffectsEnabled(enabled: boolean): void;
}

export const WHITELISTED_CDN_DOMAINS = [
    "fal.media",
    "fal.ai",
];

export function isWhitelistedCdn(url: string | null | undefined): boolean {
    if (!url) return false;
    try {
        const parsed = new URL(url, window.location.href);
        if (parsed.origin === window.location.origin) return true;
        return WHITELISTED_CDN_DOMAINS.some(
            (domain) => parsed.hostname === domain || parsed.hostname.endsWith("." + domain)
        );
    } catch {
        return false;
    }
}

/**
 * Draws generated images to a canvas, including transitions and image effects.
 * This is shared by the interactive canvas and the UI-free OBS canvas.
 */
export function createImageRenderer({
    canvas,
    image,
    backgroundLayer,
    sizingElement = canvas?.parentElement,
    fadeDuration = 1500,
    loadedClassNames = ["loaded"],
    fitMode = "contain",
    imageEffectsEnabled = () => true,
}: ImageRendererOptions): ImageRenderer {
    const context = canvas?.getContext("2d");
    let currentImage: HTMLImageElement | null = null;
    let currentVideo: SilentVideo | null = null;
    let activeAnimation: number | null = null;
    let sequenceTimer: ReturnType<typeof setTimeout> | null = null;
    let sequenceGeneration = 0;
    let imageEffectController: ImageEffectController | null = null;
    let currentImageEffect = "none";
    let layeredAnimationRequest: { layers: AnimationLayer[]; options: AnimationOptions | number } | null = null;
    let currentSequenceFrames: HTMLImageElement[] = [];
    let currentLayeredFrames: PreparedLayer[] = [];

    function areImageEffectsEnabled(): boolean {
        return typeof imageEffectsEnabled === "function"
            ? imageEffectsEnabled()
            : Boolean(imageEffectsEnabled);
    }

    function removeImageEffect(): void {
        if (!imageEffectController || !image) return;
        const frame = imageEffectController.element;
        imageEffectController.destroy();
        if (frame?.parentElement) {
            frame.parentElement.insertBefore(image, frame);
            frame.remove();
        }
        imageEffectController = null;
        image.classList.remove("image-effect-source");
        image.style.position = "absolute";
        image.style.opacity = "0";
        image.style.zIndex = "-1";
        image.style.pointerEvents = "none";
    }

    function drawScaledImage(sourceImage: HTMLImageElement | null, width: number, height: number, opacity = 1): void {
        if (!context || !sourceImage?.complete || sourceImage.naturalWidth === 0) return;

        const scale = fitMode === "cover"
            ? Math.max(width / sourceImage.naturalWidth, height / sourceImage.naturalHeight)
            : Math.min(width / sourceImage.naturalWidth, height / sourceImage.naturalHeight);
        const drawWidth = sourceImage.naturalWidth * scale;
        const drawHeight = sourceImage.naturalHeight * scale;

        context.save();
        context.globalAlpha = opacity;
        context.drawImage(sourceImage, (width - drawWidth) / 2, (height - drawHeight) / 2, drawWidth, drawHeight);
        context.restore();
    }

    function drawSingleImage(sourceImage: HTMLImageElement): void {
        if (!canvas || !context) return;
        // Drawing uses layout pixels; screen bounds include the viewer's zoom.
        const rect = { width: canvas.clientWidth, height: canvas.clientHeight };
        context.clearRect(0, 0, rect.width, rect.height);
        drawScaledImage(sourceImage, rect.width, rect.height);
    }

    function layoutImageEffect(sourceImage = image): void {
        if (!imageEffectController || !canvas || !sourceImage?.naturalWidth) return;

        const rect = { width: canvas.clientWidth, height: canvas.clientHeight };
        const scale = fitMode === "cover"
            ? Math.max(rect.width / sourceImage.naturalWidth, rect.height / sourceImage.naturalHeight)
            : Math.min(rect.width / sourceImage.naturalWidth, rect.height / sourceImage.naturalHeight);
        const width = sourceImage.naturalWidth * scale;
        const height = sourceImage.naturalHeight * scale;
        const frame = imageEffectController.element;
        const left = (rect.width - width) / 2;
        const top = (rect.height - height) / 2;

        frame.style.left = `${left}px`;
        frame.style.top = `${top}px`;
        // The fitted frame is inset from the canvas. Zoom both layers around
        // the canvas origin so their copies of the image stay registered.
        frame.style.transformOrigin = `${-left}px ${-top}px`;
        frame.style.width = `${width}px`;
        frame.style.height = `${height}px`;
        frame.style.display = "block";
    }

    function applyImageEffect(sourceImage: HTMLImageElement, effect: string, transition: string): void {
        if (!image) return;

        currentImageEffect = effect || "gleam3";
        if (!areImageEffectsEnabled()) {
            // Avoid creating or running effect layers when performance mode disables them.
            removeImageEffect();
            return;
        }

        const candidate = String(effect || "gleam3").toLowerCase().trim();
        const resolvedEffect = IMAGE_EFFECTS[candidate] ? candidate : "gleam3";
        const intensity = IMAGE_EFFECT_DEFAULT_INTENSITIES[resolvedEffect]
            ?? IMAGE_EFFECT_DEFAULT_INTENSITIES.gleam3;

        if (resolvedEffect === "none") {
            if (imageEffectController) {
                imageEffectController.setEffect("none");
                imageEffectController.element.style.display = "none";
            }
            return;
        }

        image.style.position = "static";
        image.style.opacity = "1";
        image.style.zIndex = "auto";
        image.style.pointerEvents = "none";

        if (!imageEffectController) {
            imageEffectController = attachImageEffect(image, { effect: resolvedEffect, intensity });
            imageEffectController.element.classList.add("canvas-image-effect-frame");
        } else {
            imageEffectController.setEffect(resolvedEffect);
            imageEffectController.setIntensity(intensity);
        }

        layoutImageEffect(sourceImage);
        const frame = imageEffectController.element;
        if (transition === "none") {
            frame.style.transition = "none";
            frame.style.opacity = "1";
            return;
        }

        frame.style.transition = "none";
        frame.style.opacity = "0";
        requestAnimationFrame(() => {
            const duration = transition === "crossfade" ? 2000 : fadeDuration;
            frame.style.transition = `opacity ${duration}ms ease-in-out`;
            frame.style.opacity = "1";
        });
    }

    function resize(): void {
        if (!canvas || !context || !sizingElement) return;

        const rect = sizingElement.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;

        const dpr = window.devicePixelRatio || 1;
        if (canvas.width !== Math.floor(rect.width * dpr) || canvas.height !== Math.floor(rect.height * dpr)) {
            canvas.width = Math.floor(rect.width * dpr);
            canvas.height = Math.floor(rect.height * dpr);
        }

        context.setTransform(1, 0, 0, 1, 0, 0);
        context.scale(dpr, dpr);

        if (currentImage && !activeAnimation) drawSingleImage(currentImage);
        layoutImageEffect();
    }

    function captureCanvasSnapshot(): HTMLCanvasElement | null {
        if (!canvas || canvas.width === 0 || canvas.height === 0) return null;
        const snapshot = document.createElement("canvas");
        snapshot.width = canvas.width;
        snapshot.height = canvas.height;
        const snapCtx = snapshot.getContext("2d");
        if (!snapCtx) return null;
        snapCtx.drawImage(canvas, 0, 0);
        return snapshot;
    }

    function stopSequence(): void {
        sequenceGeneration += 1;
        if (sequenceTimer) {
            clearTimeout(sequenceTimer);
            sequenceTimer = null;
        }
        if (activeAnimation) {
            cancelAnimationFrame(activeAnimation);
            activeAnimation = null;
        }
        if (currentVideo) {
            try {
                currentVideo.pause();
                currentVideo.removeAttribute("src");
                currentVideo.load();
                if (currentVideo.parentElement) {
                    currentVideo.parentElement.removeChild(currentVideo);
                }
            } catch (e) {}
            currentVideo = null;
        }
        if (currentSequenceFrames && currentSequenceFrames.length > 0) {
            currentSequenceFrames.forEach((frame) => {
                try {
                    frame.onload = null;
                    frame.onerror = null;
                    frame.src = "";
                } catch (e) {}
            });
            currentSequenceFrames = [];
        }
        if (currentLayeredFrames && currentLayeredFrames.length > 0) {
            currentLayeredFrames.forEach((layer) => {
                try {
                    if (layer.source) {
                        layer.source.onload = null;
                        layer.source.onerror = null;
                        layer.source.src = "";
                    }
                } catch (e) {}
            });
            currentLayeredFrames = [];
        }
    }

    async function applyTransition(imageUrl: string, transition = "crossfade", effect = "gleam3"): Promise<void> {
        if (!canvas || !context) return;
        layeredAnimationRequest = null;
        const oldSnapshot = captureCanvasSnapshot();
        stopSequence();

        const newImage = new Image();
        if (isWhitelistedCdn(imageUrl)) {
            newImage.crossOrigin = "anonymous";
        }
        newImage.src = imageUrl;

        try {
            await newImage.decode();
        } catch {
            await new Promise<void>((resolve) => {
                if (newImage.complete && newImage.naturalWidth > 0) {
                    resolve();
                    return;
                }
                newImage.onload = () => resolve();
                newImage.onerror = () => resolve();
            });
        }

        if (image) {
            // Haze and trace sample the DOM image for their source pixels.
            // Update it before enabling the effect so those layers never
            // analyse the preceding image (or an empty source on first load).
            image.src = imageUrl;
            image.classList.add(...loadedClassNames);
        }
        applyImageEffect(newImage, effect, transition);

        if (activeAnimation) {
            cancelAnimationFrame(activeAnimation);
            activeAnimation = null;
        }

        const rect = { width: canvas.clientWidth, height: canvas.clientHeight };
        const oldImage = currentImage;
        const hasOldImage = (oldImage?.complete && oldImage.naturalWidth > 0) || oldSnapshot !== null;

        const animate = (duration: number, drawFrame: (progress: number) => void): void => {
            const startTime = performance.now();
            const frame = (now: number): void => {
                const progress = Math.min(1, (now - startTime) / duration);
                const eased = progress < 0.5
                    ? 4 * progress * progress * progress
                    : 1 - Math.pow(-2 * progress + 2, 3) / 2;

                context.clearRect(0, 0, rect.width, rect.height);
                drawFrame(eased);

                if (progress < 1) {
                    activeAnimation = requestAnimationFrame(frame);
                } else {
                    activeAnimation = null;
                    currentImage = newImage;
                }
            };
            activeAnimation = requestAnimationFrame(frame);
        };

        if (transition === "crossfade" && hasOldImage) {
            animate(2000, (progress) => {
                drawScaledImage(newImage, rect.width, rect.height);
                if (oldSnapshot) {
                    context.save();
                    context.globalAlpha = 1 - progress;
                    context.drawImage(oldSnapshot, 0, 0, rect.width, rect.height);
                    context.restore();
                } else if (oldImage) {
                    drawScaledImage(oldImage, rect.width, rect.height, 1 - progress);
                }
            });
        } else if (transition === "none") {
            drawSingleImage(newImage);
            currentImage = newImage;
        } else {
            animate(fadeDuration, (progress) => {
                drawScaledImage(newImage, rect.width, rect.height, progress);
            });
        }

        if (backgroundLayer) backgroundLayer.style.backgroundImage = `url(${imageUrl})`;
    }

    async function playSequence(imageUrls: string[], {
        frameDuration = 1400,
        crossfadeDuration = 500,
        transitionDuration = fadeDuration,
    }: SequenceOptions = {}): Promise<void> {
        if (!canvas || !context || !Array.isArray(imageUrls) || imageUrls.length < 2) return;
        layeredAnimationRequest = null;
        stopSequence();
        const generation = sequenceGeneration;
        const frames = await Promise.all(imageUrls.map(async (imageUrl) => {
            const frame = new Image();
            if (isWhitelistedCdn(imageUrl)) {
                frame.crossOrigin = "anonymous";
            }
            frame.src = imageUrl;
            try {
                await frame.decode();
            } catch {
                await new Promise<void>((resolve) => {
                    frame.onload = () => resolve();
                    frame.onerror = () => resolve();
                });
            }
            return frame;
        }));
        if (generation !== sequenceGeneration || frames.some((frame) => !frame.naturalWidth)) {
            frames.forEach((frame) => {
                try {
                    frame.onload = null;
                    frame.onerror = null;
                    frame.src = "";
                } catch (e) {}
            });
            return;
        }
        currentSequenceFrames = frames;

        const oldSnapshot = captureCanvasSnapshot();
        const sequenceStartTime = performance.now();

        if (imageEffectController) {
            imageEffectController.setEffect("none");
            imageEffectController.element.style.display = "none";
        }
        // The sequence is composited entirely on the renderer canvas. Hide
        // the compatibility DOM image so it cannot sit on top of the canvas
        // as an extra, stale frame during the crossfade.
        if (image) {
            image.classList.remove(...loadedClassNames);
            image.style.opacity = "0";
        }
        // The enlarged, blurred backdrop is useful for a single image but it
        // turns each sequence crossfade into a second, highly visible fade.
        // Keep it empty for the entire animation; applyTransition restores it
        // when the next normal canvas image is shown.
        if (backgroundLayer) backgroundLayer.style.backgroundImage = "none";
        let index = 0;
        currentImage = frames[0];
        if (image) image.src = imageUrls[0];

        const drawSnapshotOverlay = (now: number): void => {
            if (!oldSnapshot || transitionDuration <= 0) return;
            const elapsed = now - sequenceStartTime;
            const entryProgress = Math.min(1, Math.max(0, elapsed / transitionDuration));
            if (entryProgress < 1) {
                const eased = entryProgress < 0.5
                    ? 4 * entryProgress * entryProgress * entryProgress
                    : 1 - Math.pow(-2 * entryProgress + 2, 3) / 2;
                context.save();
                context.globalAlpha = 1 - eased;
                context.drawImage(oldSnapshot, 0, 0, rect.width, rect.height);
                context.restore();
            }
        };

        const rect = { width: canvas.clientWidth, height: canvas.clientHeight };
        context.clearRect(0, 0, rect.width, rect.height);
        drawScaledImage(currentImage, rect.width, rect.height);
        drawSnapshotOverlay(performance.now());

        if (oldSnapshot && transitionDuration > 0) {
            const animateEntry = (now: number): void => {
                if (generation !== sequenceGeneration) return;
                const elapsed = now - sequenceStartTime;
                const progress = Math.min(1, elapsed / transitionDuration);
                context.clearRect(0, 0, rect.width, rect.height);
                drawScaledImage(currentImage, rect.width, rect.height);
                drawSnapshotOverlay(now);
                if (progress < 1 && activeAnimation === null) {
                    activeAnimation = requestAnimationFrame(animateEntry);
                }
            };
            activeAnimation = requestAnimationFrame(animateEntry);
        }

        const advance = () => {
            if (generation !== sequenceGeneration) return;
            const previous = currentImage;
            index = (index + 1) % frames.length;
            const next = frames[index];
            const startTime = performance.now();
            const fade = (now: number): void => {
                if (generation !== sequenceGeneration) return;
                const progress = Math.min(1, (now - startTime) / crossfadeDuration);
                context.clearRect(0, 0, rect.width, rect.height);
                drawScaledImage(next, rect.width, rect.height);
                drawScaledImage(previous, rect.width, rect.height, 1 - progress);
                drawSnapshotOverlay(now);
                if (progress < 1) {
                    activeAnimation = requestAnimationFrame(fade);
                } else {
                    activeAnimation = null;
                    currentImage = next;
                    if (image) image.src = imageUrls[index];
                    sequenceTimer = setTimeout(advance, Math.max(0, frameDuration - crossfadeDuration));
                }
            };
            activeAnimation = requestAnimationFrame(fade);
        };
        sequenceTimer = setTimeout(advance, Math.max(0, frameDuration - crossfadeDuration));
    }

    async function playLayeredAnimation(layers: AnimationLayer[], options: AnimationOptions | number = {}): Promise<void> {
        if (!canvas || !context || !Array.isArray(layers) || layers.length < 2) return;
        layeredAnimationRequest = { layers, options };
        stopSequence();
        const generation = sequenceGeneration;
        const prepared = await Promise.all(layers.map(async (layer) => {
            const source = new Image();
            if (isWhitelistedCdn(layer.url)) {
                source.crossOrigin = "anonymous";
            }
            source.src = layer.url;
            try { await source.decode(); } catch { await new Promise<void>(resolve => { source.onload = () => resolve(); source.onerror = () => resolve(); }); }
            return { ...layer, source };
        }));
        if (generation !== sequenceGeneration || prepared.some(layer => !layer.source.naturalWidth)) {
            prepared.forEach((layer) => {
                try {
                    if (layer.source) {
                        layer.source.onload = null;
                        layer.source.onerror = null;
                        layer.source.src = "";
                    }
                } catch (e) {}
            });
            return;
        }
        currentLayeredFrames = prepared;

        const oldSnapshot = captureCanvasSnapshot();
        const crossfadeDuration = typeof options === "number"
            ? options
            : (options?.crossfadeDuration ?? options?.fadeDuration ?? fadeDuration);

        if (imageEffectController) { imageEffectController.setEffect("none"); imageEffectController.element.style.display = "none"; }
        if (image) { image.classList.remove(...loadedClassNames); image.style.opacity = "0"; image.src = prepared[0].url; }
        if (backgroundLayer) backgroundLayer.style.backgroundImage = "none";
        currentImage = prepared[0].source;
        const start = performance.now();

        const computeImageCentroid = (sourceImage: CachedImage): ImageCentroid => {
            const key = sourceImage?.currentSrc || sourceImage?.src || "";
            if (sourceImage._centroidKey === key && sourceImage._centroid) return sourceImage._centroid;
            if (!sourceImage || !sourceImage.naturalWidth) return { cx: 0.5, cy: 0.5, maxRadiusRatio: 0.707 };
            try {
                const tempCanvas = document.createElement("canvas");
                const w = Math.min(256, sourceImage.naturalWidth);
                const h = Math.max(1, Math.round(w * sourceImage.naturalHeight / sourceImage.naturalWidth));
                tempCanvas.width = w; tempCanvas.height = h;
                const tempCtx = tempCanvas.getContext("2d", { willReadFrequently: true });
                if (!tempCtx) throw new Error("Canvas context unavailable");
                tempCtx.drawImage(sourceImage, 0, 0, w, h);
                const imgData = tempCtx.getImageData(0, 0, w, h).data;
                let minX = w, maxX = 0, minY = h, maxY = 0, count = 0;
                for (let y = 0; y < h; y++) {
                    for (let x = 0; x < w; x++) {
                        if (imgData[(y * w + x) * 4 + 3] > 15) {
                            if (x < minX) minX = x; if (x > maxX) maxX = x;
                            if (y < minY) minY = y; if (y > maxY) maxY = y;
                            count++;
                        }
                    }
                }
                if (count === 0) {
                    const fallbackCentroid = { cx: 0.5, cy: 0.5, maxRadiusRatio: 0.707 };
                    sourceImage._centroidKey = key;
                    sourceImage._centroid = fallbackCentroid;
                    return fallbackCentroid;
                }
                const bboxCx = (minX + maxX) / 2 / w;
                const bboxCy = (minY + maxY) / 2 / h;
                const halfW = (maxX - minX) / 2 / w;
                const halfH = (maxY - minY) / 2 / h;
                const calculatedCentroid = { cx: bboxCx, cy: bboxCy, maxRadiusRatio: Math.max(0.1, Math.hypot(halfW, halfH)) };
                sourceImage._centroidKey = key;
                sourceImage._centroid = calculatedCentroid;
                return calculatedCentroid;
            } catch {
                const fallbackCentroid = { cx: 0.5, cy: 0.5, maxRadiusRatio: 0.707 };
                sourceImage._centroidKey = key;
                sourceImage._centroid = fallbackCentroid;
                return fallbackCentroid;
            }
        };

        const drawMeshDistortion = (ctx: CanvasRenderingContext2D, sourceImage: HTMLImageElement, drawWidth: number, drawHeight: number, phase: number, amplitude = 1.0, centroid: ImageCentroid | null = null, mode = "twist"): void => {
            const vertices = calculateMeshGrid(drawWidth, drawHeight, phase, amplitude, centroid, mode);
            const GRID = vertices.length - 1;
            const drawTriangle = (p0: MeshVertex, p1: MeshVertex, p2: MeshVertex): void => {
                const sx0 = p0.u * sourceImage.naturalWidth, sy0 = p0.v * sourceImage.naturalHeight;
                const sx1 = p1.u * sourceImage.naturalWidth, sy1 = p1.v * sourceImage.naturalHeight;
                const sx2 = p2.u * sourceImage.naturalWidth, sy2 = p2.v * sourceImage.naturalHeight;
                ctx.save(); ctx.beginPath(); ctx.moveTo(p0.x, p0.y); ctx.lineTo(p1.x, p1.y); ctx.lineTo(p2.x, p2.y); ctx.closePath(); ctx.clip();
                const denom = (sx0 * (sy1 - sy2) + sx1 * (sy2 - sy0) + sx2 * (sy0 - sy1));
                if (Math.abs(denom) > 1e-5) {
                    const m11 = (p0.x * (sy1 - sy2) + p1.x * (sy2 - sy0) + p2.x * (sy0 - sy1)) / denom;
                    const m12 = (p0.y * (sy1 - sy2) + p1.y * (sy2 - sy0) + p2.y * (sy0 - sy1)) / denom;
                    const m21 = (p0.x * (sx2 - sx1) + p1.x * (sx0 - sx2) + p2.x * (sx1 - sx0)) / denom;
                    const m22 = (p0.y * (sx2 - sx1) + p1.y * (sx0 - sx2) + p2.y * (sx1 - sx0)) / denom;
                    const dx = (p0.x * (sx1 * sy2 - sx2 * sy1) + p1.x * (sx2 * sy0 - sx0 * sy2) + p2.x * (sx0 * sy1 - sx1 * sy0)) / denom;
                    const dy = (p0.y * (sx1 * sy2 - sx2 * sy1) + p1.y * (sx2 * sy0 - sx0 * sy2) + p2.y * (sx0 * sy1 - sx1 * sy0)) / denom;
                    ctx.transform(m11, m12, m21, m22, dx, dy);
                    ctx.drawImage(sourceImage, 0, 0);
                }
                ctx.restore();
            };
            for (let j = 0; j < GRID; j++) {
                for (let i = 0; i < GRID; i++) {
                    drawTriangle(vertices[j][i], vertices[j][i + 1], vertices[j + 1][i]);
                    drawTriangle(vertices[j][i + 1], vertices[j + 1][i + 1], vertices[j + 1][i]);
                }
            }
        };

        const drawLayer = (layer: PreparedLayer, now: number, width: number, height: number): void => {
            const source = layer.source;
            const scale = fitMode === "cover" ? Math.max(width / source.naturalWidth, height / source.naturalHeight) : Math.min(width / source.naturalWidth, height / source.naturalHeight);
            const drawWidth = source.naturalWidth * scale * (layer.scale || 1), drawHeight = source.naturalHeight * scale * (layer.scale || 1);
            if (!areImageEffectsEnabled()) {
                context.save();
                context.globalAlpha = layer.opacity !== undefined ? layer.opacity : 1.0;
                context.drawImage(source, (width - drawWidth) / 2, (height - drawHeight) / 2, drawWidth, drawHeight);
                context.restore();
                return;
            }
            const cx = width / 2, cy = height / 2, phase = ((now - start) / 1000) * (layer.speed || 1.0);
            const transform = layerTransform(layer.effect, phase);
            const amp = layer.amplitude ?? 1.0;
            const halo = layerHaloParams(layer.effect, phase, amp, layer.opacity !== undefined ? layer.opacity : 1.0);
            if (halo) {
                context.save();
                context.globalAlpha = halo.haloAlpha;
                context.translate(cx + transform.x * amp, cy + transform.y * amp);
                context.rotate(transform.rotation * amp);
                context.scale(transform.scale * halo.haloScale, transform.scale * halo.haloScale);
                context.filter = halo.haloFilter;
                context.drawImage(source, -drawWidth / 2, -drawHeight / 2, drawWidth, drawHeight);
                context.restore();
            }
            context.save();
            context.globalAlpha = layerOpacity(layer.effect, phase, layer.opacity !== undefined ? layer.opacity : 1.0);
            context.translate(cx + transform.x * amp, cy + transform.y * amp);
            context.rotate(transform.rotation * amp);
            context.scale(transform.scale, transform.scale);
            const pieceFilter = layerPieceFilter(layer.effect, phase);
            if (pieceFilter !== "none") {
                context.filter = pieceFilter;
            }
            if (isMeshDistortionEffect(layer.effect)) {
                const centroid = computeImageCentroid(source);
                drawMeshDistortion(context, source, drawWidth, drawHeight, phase, amp, centroid, layer.effect);
            } else if (!layerEnergyBlastDraw(layer.effect, phase, context, source, drawWidth, drawHeight) && !layerMirageDraw(layer.effect, phase, context, source, drawWidth, drawHeight) && !layerReflectiveDraw(layer.effect, phase, context, source, drawWidth, drawHeight)) {
                context.drawImage(source, -drawWidth / 2, -drawHeight / 2, drawWidth, drawHeight);
            }
            context.restore();
        };
        const frame = (now: number): void => {
            if (generation !== sequenceGeneration) return;
            const rect = { width: canvas.clientWidth, height: canvas.clientHeight };
            context.clearRect(0, 0, rect.width, rect.height);
            prepared.slice().sort((a, b) => (a.order || 0) - (b.order || 0)).forEach(layer => drawLayer(layer, now, rect.width, rect.height));

            // The plain composition is final; do not keep an animation loop alive
            // once image effects have been disabled.
            if (!areImageEffectsEnabled()) {
                activeAnimation = null;
                return;
            }

            if (oldSnapshot && crossfadeDuration > 0) {
                const elapsed = now - start;
                const progress = Math.min(1, Math.max(0, elapsed / crossfadeDuration));
                if (progress < 1) {
                    const eased = progress < 0.5
                        ? 4 * progress * progress * progress
                        : 1 - Math.pow(-2 * progress + 2, 3) / 2;
                    context.save();
                    context.globalAlpha = 1 - eased;
                    context.drawImage(oldSnapshot, 0, 0, rect.width, rect.height);
                    context.restore();
                }
            }

            activeAnimation = requestAnimationFrame(frame);
        };
        activeAnimation = requestAnimationFrame(frame);
    }

    async function playVideoAnimation(videoUrl: string, options: AnimationOptions | number = {}): Promise<void> {
        if (!canvas || !context || !videoUrl) return;
        layeredAnimationRequest = null;
        stopSequence();
        const generation = sequenceGeneration;

        const oldSnapshot = captureCanvasSnapshot();
        const crossfadeDuration = typeof options === "number"
            ? options
            : (options?.crossfadeDuration ?? options?.fadeDuration ?? fadeDuration);
        const videoOptions = typeof options === "number" ? {} : options;
        const videoDurationSeconds = typeof videoOptions.video_duration_seconds === "number" && videoOptions.video_duration_seconds > 0
            ? videoOptions.video_duration_seconds
            : 5;

        if (imageEffectController) {
            imageEffectController.setEffect("none");
            imageEffectController.element.style.display = "none";
        }
        if (image) {
            image.classList.remove(...loadedClassNames);
            image.style.opacity = "0";
        }
        if (backgroundLayer) {
            backgroundLayer.style.backgroundImage = "none";
        }

        const video: SilentVideo = document.createElement("video");
        // Guarantee that the video is completely silent (no audio output) across all browsers
        video.muted = true;
        video.defaultMuted = true;
        video.volume = 0;
        video.setAttribute("muted", "");
        video.setAttribute("playsinline", "");
        video.setAttribute("webkit-playsinline", "");
        video.setAttribute("autoplay", "");
        video.setAttribute("loop", "");
        video.playsInline = true;
        video.loop = true;
        video.autoplay = true;
        if (isWhitelistedCdn(videoUrl)) {
            video.crossOrigin = "anonymous";
        }

        // Keep video completely silent at all times (e.g., if unmuted by browser policies or extensions)
        const silenceVideo = () => {
            video.muted = true;
            video.defaultMuted = true;
            video.volume = 0;
            if (video.audioTracks) {
                try {
                    for (let i = 0; i < video.audioTracks.length; i++) {
                        video.audioTracks[i].enabled = false;
                    }
                } catch (e) {}
            }
        };
        silenceVideo();
        video.addEventListener("volumechange", silenceVideo);
        video.addEventListener("play", silenceVideo);
        video.addEventListener("playing", silenceVideo);
        video.addEventListener("loadedmetadata", silenceVideo);

        // Attach video element to DOM to prevent browser power-saver throttling on detached media elements
        video.style.position = "absolute";
        video.style.width = "1px";
        video.style.height = "1px";
        video.style.opacity = "0.001";
        video.style.pointerEvents = "none";
        video.style.zIndex = "-1";
        if (sizingElement) {
            sizingElement.appendChild(video);
        }

        video.src = videoUrl;
        currentVideo = video;

        const autoLoop = videoOptions.loop !== false;
        const restartVideoLoop = () => {
            if (generation === sequenceGeneration && currentVideo === video && autoLoop) {
                try {
                    video.currentTime = 0;
                    video.play().catch(() => {});
                } catch (e) {}
            }
        };

        if (autoLoop) {
            video.addEventListener("ended", restartVideoLoop);
            video.addEventListener("pause", () => {
                if (generation === sequenceGeneration && currentVideo === video && autoLoop) {
                    const dur = (video.duration > 0) ? video.duration : videoDurationSeconds;
                    if (video.ended || (dur > 0 && video.currentTime >= dur - 0.15)) {
                        restartVideoLoop();
                    }
                }
            });
        }

        const fallbackUrl = videoOptions.local_video_url || videoOptions.fallback_url || (videoOptions.video_path ? videoOptions.video_path : null);
        let triedFallback = false;

        await new Promise<void>((resolve) => {
            let resolved = false;
            const onReady = () => {
                if (!resolved) {
                    resolved = true;
                    resolve();
                }
            };
            const onError = () => {
                if (!triedFallback && fallbackUrl && fallbackUrl !== video.src && fallbackUrl !== videoUrl) {
                    triedFallback = true;
                    console.warn("Video CDN URL failed to load, falling back to local theater copy:", fallbackUrl);
                    silenceVideo();
                    video.src = fallbackUrl;
                    video.load();
                    return;
                }
                onReady();
            };
            if (video.readyState >= 2) {
                onReady();
            } else {
                video.oncanplay = onReady;
                video.onloadeddata = onReady;
                video.onerror = onError;
                setTimeout(onReady, 5000);
            }
        });

        if (generation !== sequenceGeneration) {
            try {
                video.pause();
                if (video.parentElement) {
                    video.parentElement.removeChild(video);
                }
            } catch (e) {}
            return;
        }

        silenceVideo();

        try {
            await video.play();
        } catch (err) {
            console.warn("[CanvasRenderer] Video autoplay prevented or failed:", err);
        }

        const start = performance.now();

        const drawScaledVideo = (sourceVideo: HTMLVideoElement, width: number, height: number, opacity = 1): void => {
            const vWidth = sourceVideo.videoWidth || width;
            const vHeight = sourceVideo.videoHeight || height;
            if (!vWidth || !vHeight) return;

            const scale = fitMode === "cover"
                ? Math.max(width / vWidth, height / vHeight)
                : Math.min(width / vWidth, height / vHeight);
            const drawWidth = vWidth * scale;
            const drawHeight = vHeight * scale;

            context.save();
            context.globalAlpha = opacity;
            context.drawImage(sourceVideo, (width - drawWidth) / 2, (height - drawHeight) / 2, drawWidth, drawHeight);
            context.restore();
        };

        const frame = (now: number): void => {
            if (generation !== sequenceGeneration) return;

            // Auto-loop watchdog: if the video has ended or stalled near the end without an image/video update, rewind and replay
            if (autoLoop && currentVideo === video) {
                const dur = (video.duration > 0) ? video.duration : videoDurationSeconds;
                if (video.ended || (dur > 0 && video.currentTime >= dur - 0.05)) {
                    try {
                        video.currentTime = 0;
                        if (video.paused) {
                            video.play().catch(() => {});
                        }
                    } catch (e) {}
                } else if (video.paused && !video.seeking && video.readyState >= 2) {
                    video.play().catch(() => {});
                }
            }

            const rect = { width: canvas.clientWidth, height: canvas.clientHeight };
            context.clearRect(0, 0, rect.width, rect.height);
            drawScaledVideo(video, rect.width, rect.height);

            if (oldSnapshot && crossfadeDuration > 0) {
                const elapsed = now - start;
                const progress = Math.min(1, Math.max(0, elapsed / crossfadeDuration));
                if (progress < 1) {
                    const eased = progress < 0.5
                        ? 4 * progress * progress * progress
                        : 1 - Math.pow(-2 * progress + 2, 3) / 2;
                    context.save();
                    context.globalAlpha = 1 - eased;
                    context.drawImage(oldSnapshot, 0, 0, rect.width, rect.height);
                    context.restore();
                }
            }

            activeAnimation = requestAnimationFrame(frame);
        };
        activeAnimation = requestAnimationFrame(frame);
    }

    return {
        resize,
        applyTransition,
        playSequence,
        playLayeredAnimation,
        playVideoAnimation,
        stopSequence,
        setImageEffectsEnabled(enabled: boolean): void {
            if (!enabled) {
                removeImageEffect();
            } else if (layeredAnimationRequest) {
                playLayeredAnimation(layeredAnimationRequest.layers, layeredAnimationRequest.options);
            } else if (currentImage && currentImageEffect !== "none") {
                applyImageEffect(currentImage, currentImageEffect, "none");
            }
        },
    };
}

