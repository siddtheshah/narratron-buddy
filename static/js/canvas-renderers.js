import {
    attachImageEffect,
    IMAGE_EFFECTS,
    IMAGE_EFFECT_DEFAULT_INTENSITIES,
} from "/static/js/image-effects.js?v=effects-20261005-1";
import {
    layerTransform,
    layerOpacity,
    layerHaloParams,
    layerPieceFilter,
    layerReflectiveDraw,
    layerMirageDraw,
    layerFlameRippleDraw,
    layerEnergyBlastDraw,
    isMeshDistortionEffect,
    calculateMeshGrid,
} from "/static/js/animation-effects.js";

export const WHITELISTED_CDN_DOMAINS = [
    "fal.media",
    "fal.ai",
];

export function isWhitelistedCdn(url) {
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
}) {
    const context = canvas?.getContext("2d");
    let currentImage = null;
    let currentVideo = null;
    let activeAnimation = null;
    let sequenceTimer = null;
    let sequenceGeneration = 0;
    let imageEffectController = null;
    let currentImageEffect = "none";
    let layeredAnimationRequest = null;
    let currentSequenceFrames = [];
    let currentLayeredFrames = [];

    function areImageEffectsEnabled() {
        return typeof imageEffectsEnabled === "function"
            ? imageEffectsEnabled()
            : Boolean(imageEffectsEnabled);
    }

    function removeImageEffect() {
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

    function drawScaledImage(sourceImage, width, height, opacity = 1) {
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

    function drawSingleImage(sourceImage) {
        if (!canvas || !context) return;
        const rect = canvas.getBoundingClientRect();
        context.clearRect(0, 0, rect.width, rect.height);
        drawScaledImage(sourceImage, rect.width, rect.height);
    }

    function layoutImageEffect(sourceImage = image) {
        if (!imageEffectController || !canvas || !sourceImage?.naturalWidth) return;

        const rect = canvas.getBoundingClientRect();
        const scale = fitMode === "cover"
            ? Math.max(rect.width / sourceImage.naturalWidth, rect.height / sourceImage.naturalHeight)
            : Math.min(rect.width / sourceImage.naturalWidth, rect.height / sourceImage.naturalHeight);
        const width = sourceImage.naturalWidth * scale;
        const height = sourceImage.naturalHeight * scale;
        const frame = imageEffectController.element;

        frame.style.left = `${(rect.width - width) / 2}px`;
        frame.style.top = `${(rect.height - height) / 2}px`;
        frame.style.width = `${width}px`;
        frame.style.height = `${height}px`;
        frame.style.display = "block";
    }

    function applyImageEffect(sourceImage, effect, transition) {
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

    function resize() {
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

    function captureCanvasSnapshot() {
        if (!canvas || canvas.width === 0 || canvas.height === 0) return null;
        const snapshot = document.createElement("canvas");
        snapshot.width = canvas.width;
        snapshot.height = canvas.height;
        const snapCtx = snapshot.getContext("2d");
        if (!snapCtx) return null;
        snapCtx.drawImage(canvas, 0, 0);
        return snapshot;
    }

    function stopSequence() {
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

    async function applyTransition(imageUrl, transition = "crossfade", effect = "gleam3") {
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
            await new Promise((resolve) => {
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

        const rect = canvas.getBoundingClientRect();
        const oldImage = currentImage;
        const hasOldImage = (oldImage?.complete && oldImage.naturalWidth > 0) || oldSnapshot !== null;

        const animate = (duration, drawFrame) => {
            const startTime = performance.now();
            const frame = (now) => {
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

    async function playSequence(imageUrls, {
        frameDuration = 1400,
        crossfadeDuration = 500,
        transitionDuration = fadeDuration,
    } = {}) {
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
                await new Promise((resolve) => {
                    frame.onload = resolve;
                    frame.onerror = resolve;
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

        const drawSnapshotOverlay = (now) => {
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

        const rect = canvas.getBoundingClientRect();
        context.clearRect(0, 0, rect.width, rect.height);
        drawScaledImage(currentImage, rect.width, rect.height);
        drawSnapshotOverlay(performance.now());

        if (oldSnapshot && transitionDuration > 0) {
            const animateEntry = (now) => {
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
            const fade = (now) => {
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

    async function playLayeredAnimation(layers, options = {}) {
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
            try { await source.decode(); } catch { await new Promise(resolve => { source.onload = resolve; source.onerror = resolve; }); }
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

        const computeImageCentroid = (sourceImage) => {
            const key = sourceImage?.currentSrc || sourceImage?.src || "";
            if (sourceImage._centroidKey === key && sourceImage._centroid) return sourceImage._centroid;
            if (!sourceImage || !sourceImage.naturalWidth) return { cx: 0.5, cy: 0.5, maxRadiusRatio: 0.707 };
            try {
                const tempCanvas = document.createElement("canvas");
                const w = Math.min(256, sourceImage.naturalWidth);
                const h = Math.max(1, Math.round(w * sourceImage.naturalHeight / sourceImage.naturalWidth));
                tempCanvas.width = w; tempCanvas.height = h;
                const tempCtx = tempCanvas.getContext("2d", { willReadFrequently: true });
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

        const drawMeshDistortion = (ctx, sourceImage, drawWidth, drawHeight, phase, amplitude = 1.0, centroid = null, mode = "twist") => {
            const vertices = calculateMeshGrid(drawWidth, drawHeight, phase, amplitude, centroid, mode);
            const GRID = vertices.length - 1;
            const drawTriangle = (p0, p1, p2) => {
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

        const drawLayer = (layer, now, width, height) => {
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
        const frame = (now) => {
            if (generation !== sequenceGeneration) return;
            const rect = canvas.getBoundingClientRect();
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

    async function playVideoAnimation(videoUrl, options = {}) {
        if (!canvas || !context || !videoUrl) return;
        layeredAnimationRequest = null;
        stopSequence();
        const generation = sequenceGeneration;

        const oldSnapshot = captureCanvasSnapshot();
        const crossfadeDuration = typeof options === "number"
            ? options
            : (options?.crossfadeDuration ?? options?.fadeDuration ?? fadeDuration);
        const videoDurationSeconds = typeof options?.video_duration_seconds === "number" && options.video_duration_seconds > 0
            ? options.video_duration_seconds
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

        const video = document.createElement("video");
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

        const autoLoop = options.loop !== false;
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

        const fallbackUrl = options?.local_video_url || options?.fallback_url || (options?.video_path ? options.video_path : null);
        let triedFallback = false;

        await new Promise((resolve) => {
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

        const drawScaledVideo = (sourceVideo, width, height, opacity = 1) => {
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

        const frame = (now) => {
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

            const rect = canvas.getBoundingClientRect();
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
        setImageEffectsEnabled(enabled) {
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

export function createDoodleRenderer({ canvas, isVisible = () => true, textLayer = null, canEditText = () => false, onEditText = () => {}, onMoveText = () => {}, stampLayer = null, canMoveStamp = () => false, onMoveStamp = () => {}, onRemoveStamp = () => {}, onSelectStamp = () => {}, canSelectSegment = () => true, onSelectSegment = () => {}, onRemoveSegment = () => {} }) {
    const context = canvas?.getContext("2d");

    const selectedSegmentIds = new Set();

    function ensureSegmentId(action) {
        if (!action) return null;
        if (!action.id) {
            action.id = (typeof crypto !== "undefined" && crypto.randomUUID)
                ? crypto.randomUUID()
                : ("seg-" + Math.random().toString(36).slice(2, 10));
        }
        if (!action.stroke_id) {
            action.stroke_id = action.id;
        }
        return action.id;
    }

    function isSegmentSelected(action) {
        if (!action) return false;
        if (action.selected) return true;
        return Boolean(action.id && selectedSegmentIds.has(action.id));
    }

    function getSelectedSegments() {
        return lastDoodleActions.filter(action => action.type === "draw" && isSegmentSelected(action));
    }

    function selectSegment(target, multi = false) {
        if (typeof canSelectSegment === "function" && !canSelectSegment()) return;
        if (!multi) {
            selectedSegmentIds.clear();
            lastDoodleActions.forEach(a => {
                if (a.type === "draw") a.selected = false;
            });
        }
        const items = Array.isArray(target) ? target : (target ? [target] : []);
        items.forEach(item => {
            const action = (typeof item === "string")
                ? lastDoodleActions.find(a => a.type === "draw" && (a.id === item || a.stroke_id === item))
                : item;
            if (action && action.type === "draw") {
                ensureSegmentId(action);
                const strokeId = action.stroke_id;
                if (strokeId) {
                    lastDoodleActions.forEach(a => {
                        if (a.type === "draw" && (a.stroke_id === strokeId || a.id === strokeId)) {
                            ensureSegmentId(a);
                            selectedSegmentIds.add(a.id);
                            a.selected = true;
                        }
                    });
                } else {
                    selectedSegmentIds.add(action.id);
                    action.selected = true;
                }
            }
        });
        if (typeof onSelectSegment === "function") {
            onSelectSegment(getSelectedSegments());
        }
        redraw();
    }

    function deselectSegment(target) {
        const items = Array.isArray(target) ? target : (target ? [target] : []);
        items.forEach(item => {
            const action = (typeof item === "string")
                ? lastDoodleActions.find(a => a.type === "draw" && (a.id === item || a.stroke_id === item))
                : item;
            if (action) {
                const strokeId = action.stroke_id;
                if (strokeId) {
                    lastDoodleActions.forEach(a => {
                        if (a.type === "draw" && (a.stroke_id === strokeId || a.id === strokeId)) {
                            if (a.id) selectedSegmentIds.delete(a.id);
                            a.selected = false;
                        }
                    });
                } else {
                    if (action.id) selectedSegmentIds.delete(action.id);
                    action.selected = false;
                }
            }
        });
        if (typeof onSelectSegment === "function") {
            onSelectSegment(getSelectedSegments());
        }
        redraw();
    }

    function clearSelectedSegments() {
        if (selectedSegmentIds.size === 0 && !lastDoodleActions.some(a => a.type === "draw" && a.selected)) {
            return;
        }
        selectedSegmentIds.clear();
        lastDoodleActions.forEach(a => {
            if (a.type === "draw") a.selected = false;
        });
        if (typeof onSelectSegment === "function") {
            onSelectSegment([]);
        }
        redraw();
    }

    function distanceToSegment(px, py, x0, y0, x1, y1) {
        const dx = x1 - x0;
        const dy = y1 - y0;
        const l2 = dx * dx + dy * dy;
        if (l2 === 0) return Math.hypot(px - x0, py - y0);
        let t = ((px - x0) * dx + (py - y0) * dy) / l2;
        t = Math.max(0, Math.min(1, t));
        const projX = x0 + t * dx;
        const projY = y0 + t * dy;
        return Math.hypot(px - projX, py - projY);
    }

    function segmentsIntersect(x1, y1, x2, y2, x3, y3, x4, y4) {
        function ccw(ax, ay, bx, by, cx, cy) {
            return (cy - ay) * (bx - ax) > (by - ay) * (cx - ax);
        }
        return (ccw(x1, y1, x3, y3, x4, y4) !== ccw(x2, y2, x3, y3, x4, y4)) &&
               (ccw(x1, y1, x2, y2, x3, y3) !== ccw(x1, y1, x2, y2, x4, y4));
    }

    function lineIntersectsRect(ax, ay, bx, by, minX, minY, maxX, maxY) {
        return segmentsIntersect(ax, ay, bx, by, minX, minY, maxX, minY) ||
               segmentsIntersect(ax, ay, bx, by, minX, maxY, maxX, maxY) ||
               segmentsIntersect(ax, ay, bx, by, minX, minY, minX, maxY) ||
               segmentsIntersect(ax, ay, bx, by, maxX, minY, maxX, maxY);
    }

    function findSegmentAt(px, py, threshold = null) {
        if (!canvas) return null;
        const rect = canvas.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return null;
        for (let i = lastDoodleActions.length - 1; i >= 0; i--) {
            const action = lastDoodleActions[i];
            if (action.type !== "draw") continue;
            ensureSegmentId(action);
            const x0 = Number(action.x0) * rect.width;
            const y0 = Number(action.y0) * rect.height;
            const x1 = Number(action.x1) * rect.width;
            const y1 = Number(action.y1) * rect.height;
            const limit = threshold !== null ? threshold : Math.max(8, (Number(action.size) || 3) / 2 + 5);
            if (distanceToSegment(px, py, x0, y0, x1, y1) <= limit) {
                return action;
            }
        }
        return null;
    }

    function getSegmentsInRect(x0, y0, x1, y1) {
        if (!canvas) return [];
        const rect = canvas.getBoundingClientRect();
        let minX = Math.min(x0, x1);
        let maxX = Math.max(x0, x1);
        let minY = Math.min(y0, y1);
        let maxY = Math.max(y0, y1);
        if (maxX <= 1 && maxY <= 1 && rect.width > 0 && rect.height > 0) {
            // Already normalized [0..1]
        } else if (rect.width > 0 && rect.height > 0) {
            minX /= rect.width;
            maxX /= rect.width;
            minY /= rect.height;
            maxY /= rect.height;
        }
        return lastDoodleActions.filter(action => {
            if (action.type !== "draw") return false;
            ensureSegmentId(action);
            const ax = Number(action.x0);
            const ay = Number(action.y0);
            const bx = Number(action.x1);
            const by = Number(action.y1);
            const segMinX = Math.min(ax, bx);
            const segMaxX = Math.max(ax, bx);
            const segMinY = Math.min(ay, by);
            const segMaxY = Math.max(ay, by);
            if (segMaxX < minX || segMinX > maxX || segMaxY < minY || segMinY > maxY) {
                return false;
            }
            if ((ax >= minX && ax <= maxX && ay >= minY && ay <= maxY) ||
                (bx >= minX && bx <= maxX && by >= minY && by <= maxY)) {
                return true;
            }
            return lineIntersectsRect(ax, ay, bx, by, minX, minY, maxX, maxY);
        });
    }

    function selectSegmentsInRect(x0, y0, x1, y1, multi = false) {
        const found = getSegmentsInRect(x0, y0, x1, y1);
        selectSegment(found, multi);
        return found;
    }

    const interactionTarget = canvas?.parentElement || canvas;

    function getOrCreateSelectionBox() {
        if (!interactionTarget) return null;
        let box = interactionTarget.querySelector(".canvas-selection-box");
        if (!box) {
            box = document.createElement("div");
            box.className = "canvas-selection-box";
            box.style.position = "absolute";
            box.style.border = "1.5px dashed #818cf8";
            box.style.background = "rgba(99, 102, 241, 0.16)";
            box.style.borderRadius = "4px";
            box.style.pointerEvents = "none";
            box.style.zIndex = "25";
            box.style.display = "none";
            box.style.boxSizing = "border-box";
            interactionTarget.appendChild(box);
        }
        return box;
    }

    let boxDrag = null;

    interactionTarget?.addEventListener("pointerdown", event => {
        if (event.button !== 0) return;
        if (event.target && event.target.closest && (
            event.target.closest(".canvas-stamp-annotation") ||
            event.target.closest(".canvas-text-annotation") ||
            event.target.closest(".stamp-resize-handle") ||
            event.target.closest(".stamp-rotate-handle") ||
            event.target.closest("#orator-command-overlay") ||
            event.target.closest("#history-paging-controls") ||
            event.target.closest(".a2ui-surface") ||
            event.target.closest("#sticky-notes-toggle-btn") ||
            event.target.closest("#suggestions-toggle-btn") ||
            event.target.closest("#prompt-toggle-btn") ||
            event.target.closest("#canvas-pin-btn") ||
            event.target.closest("#agent-activity-tray") ||
            event.target.closest("#narration-pane-mount") ||
            event.target.closest("button, a, input, select, textarea, [role='button']")
        )) {
            return;
        }
        if (typeof canSelectSegment === "function" && !canSelectSegment()) return;
        if (!canvas) return;
        const rect = canvas.getBoundingClientRect();
        if (!rect.width || !rect.height) return;

        event.preventDefault();

        const startX = event.clientX - rect.left;
        const startY = event.clientY - rect.top;

        boxDrag = {
            startClientX: event.clientX,
            startClientY: event.clientY,
            startX,
            startY,
            isMulti: event.shiftKey || event.ctrlKey || event.metaKey,
            active: false,
            pointerId: event.pointerId,
        };

        if (interactionTarget.setPointerCapture) {
            try {
                interactionTarget.setPointerCapture(event.pointerId);
            } catch (_) {}
        }
    });

    interactionTarget?.addEventListener("dragstart", event => {
        if (event.target && event.target.closest && (
            event.target.closest("#sticky-notes-toggle-btn") ||
            event.target.closest("#suggestions-toggle-btn") ||
            event.target.closest("#prompt-toggle-btn") ||
            event.target.closest("#canvas-pin-btn") ||
            event.target.closest(".canvas-stamp-annotation") ||
            event.target.closest(".canvas-text-annotation") ||
            event.target.closest("#orator-command-overlay") ||
            event.target.closest("#history-paging-controls") ||
            event.target.closest(".a2ui-surface") ||
            event.target.closest("button, a, input, select, textarea, [role='button']")
        )) {
            return;
        }
        if (boxDrag || (typeof canSelectSegment === "function" && canSelectSegment())) {
            event.preventDefault();
        }
    });

    interactionTarget?.addEventListener("pointermove", event => {
        if (!boxDrag) return;
        const dx = event.clientX - boxDrag.startClientX;
        const dy = event.clientY - boxDrag.startClientY;
        if (!boxDrag.active && Math.hypot(dx, dy) >= 4) {
            boxDrag.active = true;
            if (typeof window !== "undefined") window.isCanvasSelectionActive = true;
            if (interactionTarget.classList.contains("stamp-drag-over")) {
                interactionTarget.classList.remove("stamp-drag-over");
            }
            const box = getOrCreateSelectionBox();
            if (box) box.style.display = "block";
        }
        if (boxDrag.active) {
            event.preventDefault();
            if (interactionTarget.classList.contains("stamp-drag-over")) {
                interactionTarget.classList.remove("stamp-drag-over");
            }
            const rect = canvas.getBoundingClientRect();
            const curX = event.clientX - rect.left;
            const curY = event.clientY - rect.top;
            const boxLeft = Math.max(0, Math.min(rect.width, Math.min(boxDrag.startX, curX)));
            const boxTop = Math.max(0, Math.min(rect.height, Math.min(boxDrag.startY, curY)));
            const boxRight = Math.max(0, Math.min(rect.width, Math.max(boxDrag.startX, curX)));
            const boxBottom = Math.max(0, Math.min(rect.height, Math.max(boxDrag.startY, curY)));

            const box = getOrCreateSelectionBox();
            if (box) {
                box.style.left = `${boxLeft}px`;
                box.style.top = `${boxTop}px`;
                box.style.width = `${boxRight - boxLeft}px`;
                box.style.height = `${boxBottom - boxTop}px`;
            }
        }
    });

    const selectedStampItems = new Set();
    let selectedStampItem = null;

    function selectMovableStamp(item, multi = false) {
        if (!stampLayer) return;
        const previous = selectedStampItem;
        const items = Array.isArray(item) ? item : (item ? [item] : []);
        if (!multi) {
            selectedStampItems.clear();
        }
        items.forEach(node => {
            if (node) selectedStampItems.add(node);
        });
        Array.from(stampLayer.children).forEach(node => {
            const isMovable = selectedStampItems.has(node);
            node.classList.toggle("movable", isMovable);
            if (!isMovable && (document.activeElement === node || node.contains(document.activeElement))) {
                if (typeof document.activeElement?.blur === "function") {
                    document.activeElement.blur();
                }
                if (typeof node.blur === "function") {
                    node.blur();
                }
            }
        });
        const lastItem = items[items.length - 1] || null;
        selectedStampItem = lastItem;
        if (lastItem) {
            stampLayer.appendChild(lastItem);
            if (typeof lastItem.focus === "function") {
                lastItem.focus();
            }
            if (previous !== lastItem && typeof onSelectStamp === "function" && lastItem.annotation) {
                onSelectStamp(lastItem.annotation);
            }
        }
    }

    function getStampsInRect(x0, y0, x1, y1) {
        if (!stampLayer || !canvas) return [];
        const canvasRect = canvas.getBoundingClientRect();
        if (!canvasRect.width || !canvasRect.height) return [];
        const minX = Math.min(x0, x1);
        const maxX = Math.max(x0, x1);
        const minY = Math.min(y0, y1);
        const maxY = Math.max(y0, y1);

        return Array.from(stampLayer.children).filter(item => {
            if (!item.classList.contains("canvas-stamp-annotation")) return false;
            const itemRect = item.getBoundingClientRect();
            const stampLeft = itemRect.left - canvasRect.left;
            const stampTop = itemRect.top - canvasRect.top;
            const stampRight = itemRect.right - canvasRect.left;
            const stampBottom = itemRect.bottom - canvasRect.top;

            return stampLeft <= maxX && stampRight >= minX && stampTop <= maxY && stampBottom >= minY;
        });
    }

    const finishSelectionBox = event => {
        if (!boxDrag) return;
        const drag = boxDrag;
        boxDrag = null;
        if (typeof window !== "undefined") window.isCanvasSelectionActive = false;
        if (interactionTarget.classList.contains("stamp-drag-over")) {
            interactionTarget.classList.remove("stamp-drag-over");
        }

        if (interactionTarget.hasPointerCapture && interactionTarget.hasPointerCapture(event.pointerId)) {
            try {
                interactionTarget.releasePointerCapture(event.pointerId);
            } catch (_) {}
        }

        const box = getOrCreateSelectionBox();
        if (box) box.style.display = "none";

        const rect = canvas.getBoundingClientRect();
        if (drag.active) {
            const curX = event.clientX - rect.left;
            const curY = event.clientY - rect.top;
            const boxLeft = Math.min(drag.startX, curX);
            const boxTop = Math.min(drag.startY, curY);
            const boxRight = Math.max(drag.startX, curX);
            const boxBottom = Math.max(drag.startY, curY);

            const segmentsInBox = getSegmentsInRect(boxLeft, boxTop, boxRight, boxBottom);
            selectSegment(segmentsInBox, drag.isMulti);

            const stampsInBox = getStampsInRect(boxLeft, boxTop, boxRight, boxBottom);
            if (stampsInBox.length > 0) {
                selectMovableStamp(stampsInBox, drag.isMulti);
            } else if (!drag.isMulti) {
                selectMovableStamp(null);
            }
        } else {
            const hit = findSegmentAt(drag.startX, drag.startY);
            if (hit) {
                selectSegment(hit, drag.isMulti);
                if (!drag.isMulti) selectMovableStamp(null);
            } else if (!drag.isMulti) {
                clearSelectedSegments();
                selectMovableStamp(null);
            }
        }
    };

    interactionTarget?.addEventListener("pointerup", finishSelectionBox);
    interactionTarget?.addEventListener("pointercancel", finishSelectionBox);
    interactionTarget?.addEventListener("lostpointercapture", finishSelectionBox);

    if (typeof window !== "undefined") {
        window.addEventListener("keydown", event => {
            const active = document.activeElement;
            if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA" || active.isContentEditable)) return;
            if (event.key === "Escape") {
                if (boxDrag) {
                    boxDrag = null;
                    const box = getOrCreateSelectionBox();
                    if (box) box.style.display = "none";
                }
                if (selectedSegmentIds.size > 0) {
                    event.preventDefault();
                    clearSelectedSegments();
                }
                if (selectedStampItems.size > 0 || selectedStampItem) {
                    event.preventDefault();
                    selectMovableStamp(null);
                }
            } else if (event.key === "Delete" || event.key === "Backspace") {
                let handled = false;
                if (selectedSegmentIds.size > 0 && typeof onRemoveSegment === "function") {
                    const removed = getSelectedSegments();
                    clearSelectedSegments();
                    onRemoveSegment(removed);
                    handled = true;
                }
                if (selectedStampItems.size > 0 && typeof onRemoveStamp === "function" && canMoveStamp()) {
                    const toRemove = Array.from(selectedStampItems).map(node => node.annotation).filter(Boolean);
                    selectMovableStamp(null);
                    toRemove.forEach(ann => onRemoveStamp(ann));
                    handled = true;
                }
                if (handled) {
                    event.preventDefault();
                }
            }
        });
    }

    if (typeof window !== "undefined") {
        window.addEventListener("pointerdown", event => {
            if (event.shiftKey || event.ctrlKey || event.metaKey) return;
            if (event.target && event.target.closest && (
                event.target.closest(".canvas-stamp-annotation") ||
                event.target.closest(".stamp-resize-handle") ||
                event.target.closest(".stamp-rotate-handle") ||
                event.target.closest(".canvas-selection-box")
            )) return;
            if (selectedStampItems.size > 0 || selectedStampItem) {
                selectMovableStamp(null);
            }
        });
    } else {
        stampLayer?.parentElement?.addEventListener("pointerdown", event => {
            if (event.shiftKey || event.ctrlKey || event.metaKey) return;
            if (!event.target.closest(".canvas-stamp-annotation")) selectMovableStamp(null);
        });
    }

    function renderSelectableStamp(action) {
        if (!stampLayer || !action.id) return;
        let item = Array.from(stampLayer.children).find(node => node.dataset.annotationId === action.id);
        if (!item) {
            item = document.createElement("div");
            item.className = "canvas-stamp-annotation";
            item.dataset.annotationId = action.id;
            item.tabIndex = 0;
            item.title = "Drag to move; click to select, resize and rotate; press Delete to remove";

            const img = document.createElement("img");
            img.src = action.url;
            img.alt = action.name || "Stamp";
            img.draggable = false;
            item.appendChild(img);

            const resizeHandle = document.createElement("div");
            resizeHandle.className = "stamp-resize-handle";
            resizeHandle.title = "Drag to resize stamp";
            item.appendChild(resizeHandle);

            const rotateHandle = document.createElement("button");
            rotateHandle.type = "button";
            rotateHandle.className = "stamp-rotate-handle";
            rotateHandle.title = "Drag to rotate stamp; use Left/Right arrows for 15° steps";
            rotateHandle.setAttribute("aria-label", "Rotate stamp");
            rotateHandle.textContent = "↻";
            item.appendChild(rotateHandle);

            let rotateDrag = null;
            rotateHandle.addEventListener("pointerdown", event => {
                event.stopPropagation();
                if (event.button !== 0 || !canMoveStamp() || !item.classList.contains("movable")) return;
                event.preventDefault();
                const rect = stampLayer.getBoundingClientRect();
                const centerX = rect.left + Number(item.annotation.x) * rect.width;
                const centerY = rect.top + Number(item.annotation.y) * rect.height;
                rotateDrag = {
                    original: item.annotation, centerX, centerY,
                    startAngle: Math.atan2(event.clientY - centerY, event.clientX - centerX),
                };
                rotateHandle.setPointerCapture(event.pointerId);
            });
            rotateHandle.addEventListener("pointermove", event => {
                if (!rotateDrag || !canMoveStamp()) return;
                const angle = Math.atan2(event.clientY - rotateDrag.centerY, event.clientX - rotateDrag.centerX);
                const degrees = (Number(rotateDrag.original.rotation) || 0) + (angle - rotateDrag.startAngle) * 180 / Math.PI;
                const rotation = ((Math.round(degrees) % 360) + 360) % 360;
                onMoveStamp({ ...rotateDrag.original, rotation }, rotateDrag.original, false);
            });
            const finishRotation = event => {
                if (!rotateDrag) return;
                const finished = rotateDrag;
                rotateDrag = null;
                if (rotateHandle.hasPointerCapture(event.pointerId)) rotateHandle.releasePointerCapture(event.pointerId);
                if (event.type === "pointerup" && canMoveStamp()) {
                    if ((Number(item.annotation.rotation) || 0) !== (Number(finished.original.rotation) || 0)) {
                        onMoveStamp(item.annotation, finished.original, true);
                    }
                } else {
                    onMoveStamp(finished.original, finished.original, false);
                }
            };
            rotateHandle.addEventListener("pointerup", finishRotation);
            rotateHandle.addEventListener("pointercancel", finishRotation);
            rotateHandle.addEventListener("lostpointercapture", finishRotation);
            rotateHandle.addEventListener("keydown", event => {
                if (!canMoveStamp() || !item.classList.contains("movable")) return;
                if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                event.preventDefault();
                event.stopPropagation();
                const step = event.key === "ArrowRight" ? 15 : -15;
                const rotation = ((Number(item.annotation.rotation) || 0) + step + 360) % 360;
                onMoveStamp({ ...item.annotation, rotation }, item.annotation, true);
            });

            let resizeDrag = null;
            resizeHandle.addEventListener("pointerdown", event => {
                if (event.button !== 0 || !canMoveStamp() || !item.classList.contains("movable")) return;
                event.stopPropagation();
                event.preventDefault();
                resizeDrag = {
                    original: item.annotation,
                    startX: event.clientX,
                    startY: event.clientY,
                    startSize: Math.max(24, Number(item.annotation.size) || 80),
                    moved: false,
                };
                resizeHandle.setPointerCapture(event.pointerId);
            });

            resizeHandle.addEventListener("pointermove", event => {
                if (!resizeDrag || !canMoveStamp()) return;
                const rect = (canvas && canvas.getBoundingClientRect().width > 0)
                    ? canvas.getBoundingClientRect()
                    : (stampLayer?.getBoundingClientRect() || null);
                if (!rect || !rect.width || !rect.height) return;
                const centerX = rect.left + Number(resizeDrag.original.x) * rect.width;
                const centerY = rect.top + Number(resizeDrag.original.y) * rect.height;
                const angle = (Number(resizeDrag.original.rotation) || 0) * Math.PI / 180;
                const dx = event.clientX - centerX;
                const dy = event.clientY - centerY;
                const dist = Math.max(Math.abs(dx * Math.cos(angle) + dy * Math.sin(angle)),
                    Math.abs(-dx * Math.sin(angle) + dy * Math.cos(angle)));
                const currentPixelSize = dist * 2;
                const refWidth = 1000;
                const normalizedSize = Math.round((currentPixelSize / rect.width) * refWidth);
                const newSize = Math.max(24, Math.min(512, normalizedSize));
                if (Math.abs(newSize - resizeDrag.startSize) > 2) {
                    resizeDrag.moved = true;
                }
                onMoveStamp({ ...resizeDrag.original, size: newSize }, resizeDrag.original, false);
            });

            const finishResize = event => {
                if (!resizeDrag) return;
                const finished = resizeDrag;
                resizeDrag = null;
                if (resizeHandle.hasPointerCapture(event.pointerId)) resizeHandle.releasePointerCapture(event.pointerId);
                if (!finished.moved) return;
                if (event.type === "pointerup" && canMoveStamp()) {
                    onMoveStamp(item.annotation, finished.original, true);
                } else {
                    onMoveStamp(finished.original, finished.original, false);
                }
            };
            resizeHandle.addEventListener("pointerup", finishResize);
            resizeHandle.addEventListener("pointercancel", finishResize);
            resizeHandle.addEventListener("lostpointercapture", finishResize);

            item.addEventListener("wheel", event => {
                if (!canMoveStamp() || !item.classList.contains("movable")) return;
                event.preventDefault();
                event.stopPropagation();
                const step = event.deltaY < 0 ? 8 : -8;
                const currentSize = Math.max(24, Number(item.annotation.size) || 80);
                const nextSize = Math.max(24, Math.min(512, currentSize + step));
                if (nextSize !== currentSize) {
                    onMoveStamp({ ...item.annotation, size: nextSize }, item.annotation, true);
                }
            }, { passive: false });

            let drag = null;
            item.addEventListener("click", event => {
                event.stopPropagation();
                if (canMoveStamp()) selectMovableStamp(item);
            });
            item.addEventListener("pointerdown", event => {
                if (event.button !== 0 || !canMoveStamp()) return;
                event.stopPropagation();
                event.preventDefault();
                selectMovableStamp(item);
                drag = {original: item.annotation, x: event.clientX, y: event.clientY, moved: false};
                item.setPointerCapture(event.pointerId);
            });
            item.addEventListener("pointermove", event => {
                if (!drag || !canMoveStamp()) return;
                const dx = event.clientX - drag.x;
                const dy = event.clientY - drag.y;
                if (!drag.moved && Math.hypot(dx, dy) < 4) return;
                const rect = canvas.getBoundingClientRect();
                if (!rect.width || !rect.height) return;
                drag.moved = true;
                item.classList.add("dragging");
                onMoveStamp({
                    ...drag.original,
                    x: Math.max(0, Math.min(1, Number(drag.original.x) + dx / rect.width)),
                    y: Math.max(0, Math.min(1, Number(drag.original.y) + dy / rect.height)),
                }, drag.original, false);
            });
            const finishMove = event => {
                if (!drag) return;
                const finished = drag;
                drag = null;
                item.classList.remove("dragging");
                if (item.hasPointerCapture(event.pointerId)) item.releasePointerCapture(event.pointerId);
                if (!finished.moved) return;
                if (event.type === "pointerup" && canMoveStamp()) {
                    onMoveStamp(item.annotation, finished.original, true);
                } else {
                    onMoveStamp(finished.original, finished.original, false);
                }
            };
            item.addEventListener("pointerup", finishMove);
            item.addEventListener("pointercancel", finishMove);
            item.addEventListener("lostpointercapture", finishMove);
            item.addEventListener("keydown", event => {
                if ((event.key === "Delete" || event.key === "Backspace") && canMoveStamp()) {
                    event.preventDefault();
                    selectMovableStamp(null);
                    onRemoveStamp(item.annotation);
                } else if (event.key === "Escape") {
                    selectMovableStamp(null);
                }
            });
            stampLayer.appendChild(item);
        }
        item.annotation = action;
        const rect = (canvas && canvas.getBoundingClientRect().width > 0)
            ? canvas.getBoundingClientRect()
            : (stampLayer?.getBoundingClientRect() || null);
        const refWidth = 1000;
        const scale = (rect && rect.width > 0) ? (rect.width / refWidth) : 1;
        const baseSize = Math.max(16, Number(action.size) || 80);
        const renderedSize = Math.max(16, Math.round(baseSize * scale));
        item.style.width = `${renderedSize}px`;
        item.style.height = `${renderedSize}px`;
        item.style.left = `${Number(action.x) * 100}%`;
        item.style.top = `${Number(action.y) * 100}%`;
        item.style.transform = `translate(-50%, -50%) rotate(${Number(action.rotation) || 0}deg)`;
        const img = item.querySelector("img");
        if (img && img.getAttribute("src") !== action.url) {
            img.src = action.url;
        }
    }

    function selectMovableText(label) {
        if (!textLayer) return;
        Array.from(textLayer.children).forEach(node => node.classList.toggle("movable", node === label));
    }

    textLayer?.parentElement.addEventListener("pointerdown", event => {
        if (!event.target.closest(".canvas-text-annotation")) selectMovableText(null);
    });

    function renderSelectableText(action) {
        if (!textLayer || !action.id) return;
        let label = Array.from(textLayer.children).find(node => node.dataset.annotationId === action.id);
        if (!label) {
            label = document.createElement("span");
            label.className = "canvas-text-annotation";
            label.dataset.annotationId = action.id;
            label.tabIndex = 0;
            label.title = "Click to select, then drag to move; double-click or press Enter to edit";
            let drag = null;
            label.addEventListener("click", event => {
                event.stopPropagation();
                if (canEditText()) selectMovableText(label);
            });
            label.addEventListener("pointerdown", event => {
                if (event.button !== 0 || !canEditText() || !label.classList.contains("movable")) return;
                event.preventDefault();
                drag = {original: label.annotation, x: event.clientX, y: event.clientY, moved: false};
                label.setPointerCapture(event.pointerId);
            });
            label.addEventListener("pointermove", event => {
                if (!drag || !canEditText()) return;
                const dx = event.clientX - drag.x;
                const dy = event.clientY - drag.y;
                if (!drag.moved && Math.hypot(dx, dy) < 4) return;
                const rect = canvas.getBoundingClientRect();
                if (!rect.width || !rect.height) return;
                drag.moved = true;
                label.classList.add("dragging");
                onMoveText({...drag.original,
                    x: Math.max(0, Math.min(1, Number(drag.original.x) + dx / rect.width)),
                    y: Math.max(0, Math.min(1, Number(drag.original.y) + dy / rect.height)),
                }, drag.original, false);
            });
            const finishMove = event => {
                if (!drag) return;
                const finished = drag;
                drag = null;
                label.classList.remove("dragging");
                if (label.hasPointerCapture(event.pointerId)) label.releasePointerCapture(event.pointerId);
                if (!finished.moved) return;
                if (event.type === "pointerup" && canEditText()) {
                    onMoveText(label.annotation, finished.original, true);
                } else {
                    onMoveText(finished.original, finished.original, false);
                }
            };
            label.addEventListener("pointerup", finishMove);
            label.addEventListener("pointercancel", finishMove);
            label.addEventListener("lostpointercapture", finishMove);
            label.addEventListener("dblclick", event => {
                event.stopPropagation();
                if (canEditText()) {
                    selectMovableText(null);
                    onEditText(label.annotation);
                }
            });
            label.addEventListener("keydown", event => {
                if (event.key === "Enter" && canEditText()) {
                    event.preventDefault();
                    selectMovableText(null);
                    onEditText(label.annotation);
                } else if (event.key === "Escape") {
                    selectMovableText(null);
                }
            });
            textLayer.appendChild(label);
        }
        label.annotation = action;
        label.textContent = String(action.text).split("\n").slice(0, 4).join("\n");
        label.style.left = `${Number(action.x) * 100}%`;
        label.style.top = `${Number(action.y) * 100}%`;
        label.style.font = `600 ${Math.max(12, Number(action.size) || 32)}px "${String(action.font || "Outfit").replace(/["'`;{}]/g, "")}", sans-serif`;
        label.style.lineHeight = "1.15";
    }

    function renderText(action) {
        if (!context || !canvas || !action?.text) return;

        const rect = canvas.getBoundingClientRect();
        const size = Math.max(12, Number(action.size) || 32);
        const font = String(action.font || "Outfit").replace(/["'`;{}]/g, "");
        const lines = String(action.text).split("\n").slice(0, 4);
        const x = Number(action.x) * rect.width;
        const y = Number(action.y) * rect.height;

        context.save();
        context.globalCompositeOperation = "source-over";
        context.font = `600 ${size}px "${font}", sans-serif`;
        context.textBaseline = "top";
        context.lineJoin = "round";
        context.strokeStyle = "rgba(0, 0, 0, 0.72)";
        context.lineWidth = Math.max(2, size / 10);
        context.fillStyle = action.color || "#ffffff";
        lines.forEach((line, index) => {
            const lineY = y + (index * size * 1.15);
            context.strokeText(line, x, lineY);
            context.fillText(line, x, lineY);
        });
        context.restore();
        renderSelectableText(action);
    }

    function renderSegment(x0, y0, x1, y1, color, size, isSelected = false) {
        if (!context) return;

        context.save();
        if (isSelected) {
            context.beginPath();
            context.globalCompositeOperation = "source-over";
            context.strokeStyle = "#818cf8";
            context.lineWidth = Math.max(size + 6, 8);
            context.lineCap = "round";
            context.lineJoin = "round";
            context.shadowColor = "rgba(99, 102, 241, 0.75)";
            context.shadowBlur = 8;
            context.moveTo(x0, y0);
            context.lineTo(x1, y1);
            context.stroke();
        }

        context.beginPath();
        if (color === "erase") {
            context.globalCompositeOperation = "destination-out";
            context.strokeStyle = "rgba(0,0,0,1)";
        } else {
            context.globalCompositeOperation = "source-over";
            context.strokeStyle = color;
        }
        context.shadowColor = "transparent";
        context.shadowBlur = 0;
        context.moveTo(x0, y0);
        context.lineTo(x1, y1);
        context.lineWidth = size;
        context.lineCap = "round";
        context.lineJoin = "round";
        context.stroke();

        if (isSelected) {
            context.globalCompositeOperation = "source-over";
            context.fillStyle = "#ffffff";
            context.strokeStyle = "#6366f1";
            context.lineWidth = 2;
            const handleRadius = Math.max(3, Math.min(6, size / 2 + 2));
            [ [x0, y0], [x1, y1] ].forEach(([hx, hy]) => {
                context.beginPath();
                context.arc(hx, hy, handleRadius, 0, Math.PI * 2);
                context.fill();
                context.stroke();
            });
        }
        context.restore();
    }

    let lastDoodleActions = [];

    function redraw(actions) {
        if (!canvas || !context) return;
        if (actions) lastDoodleActions = actions;

        if (textLayer) {
            const ids = new Set(lastDoodleActions.filter(action => action.type === "text").map(action => action.id));
            Array.from(textLayer.children).forEach(label => {
                if (!ids.has(label.dataset.annotationId)) label.remove();
            });
            textLayer.hidden = !isVisible();
        }

        if (stampLayer) {
            const ids = new Set(lastDoodleActions.filter(action => action.type === "stamp").map(action => action.id));
            Array.from(stampLayer.children).forEach(item => {
                if (!ids.has(item.dataset.annotationId)) item.remove();
            });
            stampLayer.hidden = !isVisible();
        }

        const rect = canvas.getBoundingClientRect();
        context.clearRect(0, 0, rect.width, rect.height);
        if (!isVisible()) return;

        lastDoodleActions.forEach((action) => {
            if (action.type === "draw") {
                ensureSegmentId(action);
                const isSelected = isSegmentSelected(action);
                renderSegment(
                    action.x0 * rect.width,
                    action.y0 * rect.height,
                    action.x1 * rect.width,
                    action.y1 * rect.height,
                    action.color,
                    action.size || 3,
                    isSelected
                );
            } else if (action.type === "text") {
                renderText(action);
            } else if (action.type === "stamp") {
                renderSelectableStamp(action);
            }
        });
        syncStampLayerOrder();
    }

    // Layer stamp nodes to match doodle order without touching nodes already in place.
    // Re-parenting a node that holds pointer capture fires lostpointercapture and
    // aborts an in-progress drag, so nodes are only moved when the order differs.
    function syncStampLayerOrder() {
        if (!stampLayer) return;
        const desired = lastDoodleActions
            .filter(action => action.type === "stamp" && action.id)
            .map(action => Array.from(stampLayer.children).find(node => node.dataset.annotationId === action.id))
            .filter(node => node);
        desired.forEach((node, index) => {
            const current = stampLayer.children[index];
            if (current !== node) stampLayer.insertBefore(node, current || null);
        });
    }

    function resize(actions) {
        if (!canvas || !context) return;
        if (actions) lastDoodleActions = actions;

        const rect = canvas.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;

        const dpr = window.devicePixelRatio || 1;
        if (canvas.width !== Math.floor(rect.width * dpr) || canvas.height !== Math.floor(rect.height * dpr)) {
            canvas.width = Math.floor(rect.width * dpr);
            canvas.height = Math.floor(rect.height * dpr);
        }

        context.setTransform(1, 0, 0, 1, 0, 0);
        context.scale(dpr, dpr);
        redraw(lastDoodleActions);
    }

    if (typeof ResizeObserver !== "undefined") {
        const resizeTarget = canvas?.parentElement || canvas || stampLayer;
        if (resizeTarget) {
            let lastObservedWidth = 0;
            let lastObservedHeight = 0;
            const ro = new ResizeObserver(entries => {
                for (const entry of entries) {
                    const width = entry.contentRect.width;
                    const height = entry.contentRect.height;
                    if (Math.abs(width - lastObservedWidth) > 0.5 || Math.abs(height - lastObservedHeight) > 0.5) {
                        lastObservedWidth = width;
                        lastObservedHeight = height;
                        resize(lastDoodleActions);
                    }
                }
            });
            ro.observe(resizeTarget);
        }
    }

    return {
        renderSegment,
        renderText,
        renderStamp: renderSelectableStamp,
        redraw,
        resize,
        selectSegment,
        deselectSegment,
        clearSelectedSegments,
        getSelectedSegments,
        isSegmentSelected,
        findSegmentAt,
        getSegmentsInRect,
        selectSegmentsInRect,
        selectStamp: selectMovableStamp,
        getSelectedStamps: () => Array.from(selectedStampItems).map(node => node.annotation).filter(Boolean),
        getStampsInRect,
    };
}
