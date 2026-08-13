import {
    attachImageEffect,
    IMAGE_EFFECTS,
    IMAGE_EFFECT_DEFAULT_INTENSITIES,
} from "/static/js/image-effects.js?v=effects-20260811-2";

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
}) {
    const context = canvas?.getContext("2d");
    let currentImage = null;
    let activeAnimation = null;
    let sequenceTimer = null;
    let sequenceGeneration = 0;
    let imageEffectController = null;

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
    }

    async function applyTransition(imageUrl, transition = "crossfade", effect = "gleam3") {
        if (!canvas || !context) return;
        stopSequence();
        resize();

        const newImage = new Image();
        newImage.crossOrigin = "anonymous";
        newImage.src = imageUrl;

        try {
            await newImage.decode();
        } catch {
            await new Promise((resolve) => {
                newImage.onload = resolve;
                newImage.onerror = resolve;
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
        const hasOldImage = oldImage?.complete && oldImage.naturalWidth > 0;

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
                drawScaledImage(oldImage, rect.width, rect.height, 1 - progress);
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
    } = {}) {
        if (!canvas || !context || !Array.isArray(imageUrls) || imageUrls.length < 2) return;
        stopSequence();
        const generation = sequenceGeneration;
        const frames = await Promise.all(imageUrls.map(async (imageUrl) => {
            const frame = new Image();
            frame.crossOrigin = "anonymous";
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
        if (generation !== sequenceGeneration || frames.some((frame) => !frame.naturalWidth)) return;

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
        drawSingleImage(currentImage);

        const advance = () => {
            if (generation !== sequenceGeneration) return;
            const previous = currentImage;
            index = (index + 1) % frames.length;
            const next = frames[index];
            const rect = canvas.getBoundingClientRect();
            const startTime = performance.now();
            const fade = (now) => {
                if (generation !== sequenceGeneration) return;
                const progress = Math.min(1, (now - startTime) / crossfadeDuration);
                context.clearRect(0, 0, rect.width, rect.height);
                drawScaledImage(next, rect.width, rect.height);
                drawScaledImage(previous, rect.width, rect.height, 1 - progress);
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

    return { resize, applyTransition, playSequence };
}

/** Draws normalized doodle segments and replays them after canvas resizes. */
export function createDoodleRenderer({ canvas, isVisible = () => true }) {
    const context = canvas?.getContext("2d");

    function renderSegment(x0, y0, x1, y1, color, size) {
        if (!context) return;

        context.save();
        context.beginPath();
        if (color === "erase") {
            context.globalCompositeOperation = "destination-out";
            context.strokeStyle = "rgba(0,0,0,1)";
        } else {
            context.globalCompositeOperation = "source-over";
            context.strokeStyle = color;
        }
        context.moveTo(x0, y0);
        context.lineTo(x1, y1);
        context.lineWidth = size;
        context.lineCap = "round";
        context.lineJoin = "round";
        context.stroke();
        context.restore();
    }

    function redraw(actions) {
        if (!canvas || !context) return;

        const rect = canvas.getBoundingClientRect();
        context.clearRect(0, 0, rect.width, rect.height);
        if (!isVisible()) return;

        actions.forEach((action) => {
            if (action.type === "draw") {
                renderSegment(
                    action.x0 * rect.width,
                    action.y0 * rect.height,
                    action.x1 * rect.width,
                    action.y1 * rect.height,
                    action.color,
                    action.size || 3,
                );
            }
        });
    }

    function resize(actions) {
        if (!canvas || !context) return;

        const rect = canvas.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;

        const dpr = window.devicePixelRatio || 1;
        if (canvas.width !== Math.floor(rect.width * dpr) || canvas.height !== Math.floor(rect.height * dpr)) {
            canvas.width = Math.floor(rect.width * dpr);
            canvas.height = Math.floor(rect.height * dpr);
        }

        context.setTransform(1, 0, 0, 1, 0, 0);
        context.scale(dpr, dpr);
        redraw(actions);
    }

    return { renderSegment, redraw, resize };
}
