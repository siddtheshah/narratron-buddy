/**
 * Runtime image treatments for Narratron artwork.
 *
 * Usage:
 *   import { attachImageEffect } from '/static/js/image-effects.js';
 *   const controller = attachImageEffect(document.querySelector('.my-image'), {
 *     effect: 'gleam3', intensity: 0.75,
 *   });
 *   controller.setEffect('sparkle');
 *   controller.destroy();
 */

export const IMAGE_EFFECTS = Object.freeze({
  none: { label: 'None', classes: [] },
  creeping: { label: 'Creeping darkness', classes: ['fx-luminance', 'fx-fourier-shadows', 'fx-vignette'] },
  shining: { label: 'Cloudy dreams', classes: ['fx-periodic-light', 'fx-bloom'] },
  sparkle: { label: 'Starlight twinkle', classes: ['fx-star-twinkle'] },
  gleam3: { label: 'Gleam 3', classes: ['fx-gleam3'] },
  bendy: { label: 'Bendy', classes: ['fx-bendy'] },
  haze: { label: 'Drifting haze', classes: ['fx-haze'] },
  trace: { label: 'Light trace', classes: ['fx-trace'] },
});

/** Visual defaults are owned by the canvas, not the agent tool contract. */
export const IMAGE_EFFECT_DEFAULT_INTENSITIES = Object.freeze({
  none: 0,
  creeping: 0.65,
  shining: 0.62,
  sparkle: 0.72,
  gleam3: 0.68,
  bendy: 0.45,
  haze: 0.75,
  trace: 0.60,
});

const ALL_EFFECT_CLASSES = Object.values(IMAGE_EFFECTS)
  .flatMap(({ classes }) => classes);

function normaliseIntensity(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.min(1, Math.max(0, number)) : 0.72;
}

function normaliseSignedIntensity(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.min(1, Math.max(-1, number)) : 0;
}

function smoothstep(edge0, edge1, value) {
  const progress = Math.min(1, Math.max(0, (value - edge0) / (edge1 - edge0)));
  return progress * progress * (3 - 2 * progress);
}

/** Returns a distribution-relative cutoff without sorting a full image buffer. */
function histogramPercentile(values, fraction, maximum = 1, bins = 256) {
  const histogram = new Uint32Array(bins);
  for (let index = 0; index < values.length; index += 1) {
    const bin = clamp(Math.floor(values[index] / maximum * (bins - 1)), 0, bins - 1);
    histogram[bin] += 1;
  }
  const target = Math.max(0, Math.ceil(values.length * fraction) - 1);
  let cumulative = 0;
  for (let bin = 0; bin < bins; bin += 1) {
    cumulative += histogram[bin];
    if (cumulative > target) return (bin + 0.5) / bins * maximum;
  }
  return maximum;
}

/**
 * Follows each pixel's steepest increasing 8-neighbor path to a local HSV Value
 * maximum. Every pixel contributes eight votes to its peak, including +8 for a peak itself.
 */
function buildValueFlowPeaks(values, width, height) {
  const size = width * height;
  const next = new Int32Array(size);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const index = y * width + x;
      let brightest = index;
      let brightestValue = values[index];
      for (let offsetY = -1; offsetY <= 1; offsetY += 1) {
        for (let offsetX = -1; offsetX <= 1; offsetX += 1) {
          if (offsetX === 0 && offsetY === 0) continue;
          const neighborX = x + offsetX;
          const neighborY = y + offsetY;
          if (neighborX < 0 || neighborX >= width || neighborY < 0 || neighborY >= height) continue;
          const neighbor = neighborY * width + neighborX;
          if (values[neighbor] > brightestValue) {
            brightest = neighbor;
            brightestValue = values[neighbor];
          }
        }
      }
      next[index] = brightest;
    }
  }

  const destinations = new Int32Array(size);
  destinations.fill(-1);
  for (let start = 0; start < size; start += 1) {
    if (destinations[start] >= 0) continue;
    const path = [];
    let current = start;
    while (destinations[current] < 0 && next[current] !== current) {
      path.push(current);
      current = next[current];
    }
    const peak = destinations[current] >= 0 ? destinations[current] : current;
    destinations[current] = peak;
    for (const index of path) destinations[index] = peak;
  }

  const peakScores = new Uint32Array(size);
  let maximumScore = 8;
  for (let index = 0; index < destinations.length; index += 1) {
    const peak = destinations[index];
    peakScores[peak] += 8;
    maximumScore = Math.max(maximumScore, peakScores[peak]);
  }
  return { peakScores, maximumScore };
}

/**
 * Builds a low-resolution alpha mask of the source image's bright regions.
 * The mask is calculated once per source-image load, then reused for animation.
 */
function createLuminanceLayer(frame, image) {
  const canvas = document.createElement('canvas');
  canvas.className = 'image-effect-luminance';
  canvas.setAttribute('aria-hidden', 'true');
  frame.appendChild(canvas);

  const analysisCanvas = document.createElement('canvas');
  const maskCanvas = document.createElement('canvas');
  const analysisContext = analysisCanvas.getContext('2d', { willReadFrequently: true });
  const maskContext = maskCanvas.getContext('2d');
  const outputContext = canvas.getContext('2d');
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let animationFrame;
  let enabled = false;
  let paused = prefersReducedMotion.matches;
  let maskReady = false;

  function clearMask() {
    maskReady = false;
    outputContext?.clearRect(0, 0, canvas.width, canvas.height);
    canvas.hidden = true;
  }

  function analyse() {
    if (!analysisContext || !maskContext || !image.complete || !image.naturalWidth) return;
    const width = Math.min(360, image.naturalWidth);
    const height = Math.max(1, Math.round(width * image.naturalHeight / image.naturalWidth));
    analysisCanvas.width = maskCanvas.width = canvas.width = width;
    analysisCanvas.height = maskCanvas.height = canvas.height = height;
    analysisContext.clearRect(0, 0, width, height);
    analysisContext.drawImage(image, 0, 0, width, height);

    try {
      const pixels = analysisContext.getImageData(0, 0, width, height);
      const mask = maskContext.createImageData(width, height);
      for (let index = 0; index < pixels.data.length; index += 4) {
        const red = pixels.data[index] / 255;
        const green = pixels.data[index + 1] / 255;
        const blue = pixels.data[index + 2] / 255;
        // Use display luminance: it follows what viewers perceive as bright in saturated art.
        const luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue;
        // Ignore normal midtones; preserve only a softly feathered upper highlight range.
        const highlight = smoothstep(0.42, 0.82, luminance) ** 0.72;
        mask.data[index] = 255;
        mask.data[index + 1] = 238;
        mask.data[index + 2] = 196;
        mask.data[index + 3] = Math.round(highlight * 190);
      }
      maskContext.putImageData(mask, 0, 0);
      maskReady = true;
      canvas.hidden = !enabled;
      draw(performance.now());
    } catch (error) {
      // A cross-origin image without CORS cannot expose pixels. Hide only this enhancement.
      maskReady = false;
      canvas.hidden = true;
      console.warn('Luminance treatment needs a same-origin or CORS-enabled image.', error);
    }
  }

  function draw(time) {
    if (!outputContext || !maskReady || !enabled) return;
    const { width, height } = canvas;
    const intensity = normaliseIntensity(frame.style.getPropertyValue('--fx-intensity'));
    const phase = paused ? 0.35 : (Math.sin(time / 1350) + 1) / 2;
    const center = width * (-0.12 + phase * 1.24);
    const gradient = outputContext.createLinearGradient(center - width * 0.34, 0, center + width * 0.34, height);
    gradient.addColorStop(0, 'rgba(255, 216, 130, 0)');
    gradient.addColorStop(0.36, `rgba(255, 226, 163, ${0.05 + intensity * 0.14})`);
    gradient.addColorStop(0.52, `rgba(255, 250, 225, ${0.14 + intensity * 0.36})`);
    gradient.addColorStop(0.68, `rgba(255, 211, 121, ${0.04 + intensity * 0.12})`);
    gradient.addColorStop(1, 'rgba(255, 216, 130, 0)');

    outputContext.clearRect(0, 0, width, height);
    outputContext.fillStyle = gradient;
    outputContext.fillRect(0, 0, width, height);
    outputContext.globalCompositeOperation = 'destination-in';
    outputContext.drawImage(maskCanvas, 0, 0);
    outputContext.globalCompositeOperation = 'source-over';
  }

  function animate(time) {
    draw(time);
    if (enabled && !paused) animationFrame = requestAnimationFrame(animate);
  }

  function restart() {
    cancelAnimationFrame(animationFrame);
    if (enabled && !paused) animationFrame = requestAnimationFrame(animate);
  }

  image.addEventListener('load', analyse);
  const sourceObserver = new MutationObserver(clearMask);
  sourceObserver.observe(image, { attributes: true, attributeFilter: ['src', 'srcset'] });
  if (image.complete) analyse();

  return {
    setEnabled(nextEnabled) {
      enabled = nextEnabled;
      canvas.hidden = !enabled;
      if (enabled && !maskReady) analyse();
      draw(performance.now());
      restart();
    },
    setPaused(nextPaused) {
      paused = nextPaused || prefersReducedMotion.matches;
      draw(performance.now());
      restart();
    },
    destroy() {
      cancelAnimationFrame(animationFrame);
      image.removeEventListener('load', analyse);
      sourceObserver.disconnect();
      canvas.remove();
    },
  };
}

/**
 * Draws an animated Fourier field as a low-resolution darkening mask. Several
 * waves with different directions and speeds create organic, non-repeating shadow drift.
 */
function createFourierShadowLayer(frame, image) {
  const canvas = document.createElement('canvas');
  canvas.className = 'image-effect-fourier-shadows';
  canvas.setAttribute('aria-hidden', 'true');
  frame.appendChild(canvas);

  const context = canvas.getContext('2d');
  const sourceCanvas = document.createElement('canvas');
  const sourceContext = sourceCanvas.getContext('2d', { willReadFrequently: true });
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let pixels;
  let brightnessWeights;
  let animationFrame;
  let lastDraw = 0;
  let enabled = false;
  let paused = prefersReducedMotion.matches;

  function resize() {
    if (!image.naturalWidth) return;
    const width = Math.min(190, image.naturalWidth);
    const height = Math.max(1, Math.round(width * image.naturalHeight / image.naturalWidth));
    canvas.width = width;
    canvas.height = height;
    pixels = context?.createImageData(width, height);
    brightnessWeights = new Uint8Array(width * height);

    if (!sourceContext) return;
    sourceCanvas.width = width;
    sourceCanvas.height = height;
    sourceContext.clearRect(0, 0, width, height);
    sourceContext.drawImage(image, 0, 0, width, height);

    try {
      const sourcePixels = sourceContext.getImageData(0, 0, width, height).data;
      for (let index = 0; index < brightnessWeights.length; index += 1) {
        const pixelIndex = index * 4;
        const red = sourcePixels[pixelIndex] / 255;
        const green = sourcePixels[pixelIndex + 1] / 255;
        const blue = sourcePixels[pixelIndex + 2] / 255;
        const luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue;
        // Shadows begin in midlight and reach full strength over apparent highlights.
        brightnessWeights[index] = Math.round(smoothstep(0.12, 0.55, luminance) ** 0.75 * 255);
      }
    } catch (error) {
      // A CORS-restricted image cannot be sampled; retain a conservative effect instead.
      brightnessWeights.fill(72);
      console.warn('Luminance-weighted shadows need a same-origin or CORS-enabled image.', error);
    }
  }

  function draw(time) {
    if (!context || !pixels || !enabled) return;
    const { width, height } = canvas;
    const intensity = normaliseIntensity(frame.style.getPropertyValue('--fx-intensity'));
    const t = paused ? 0.8 : time * 0.000075;
    const data = pixels.data;

    for (let y = 0; y < height; y += 1) {
      const v = y / height;
      for (let x = 0; x < width; x += 1) {
        const u = x / width;
        // A compact Fourier series: mixed spatial frequencies, directions, and phase velocities.
        const field =
          0.24 * Math.sin(Math.PI * 2 * (1.18 * u + 0.43 * v + t * 0.82)) +
          0.20 * Math.sin(Math.PI * 2 * (-2.16 * u + 0.96 * v - t * 1.18) + 1.1) +
          0.16 * Math.sin(Math.PI * 2 * (3.42 * u - 1.12 * v + t * 0.63) + 2.4) +
          0.14 * Math.sin(Math.PI * 2 * (-4.72 * u - 2.06 * v - t * 0.47) + 0.5) +
          0.11 * Math.sin(Math.PI * 2 * (6.18 * u + 1.76 * v + t * 0.94) + 1.8) +
          0.09 * Math.sin(Math.PI * 2 * (-7.56 * u + 3.28 * v - t * 0.39) + 2.9) +
          0.06 * Math.sin(Math.PI * 2 * (9.22 * u - 4.14 * v + t * 0.28));
        // More high-frequency modes create finer, more numerous drifting wavelets.
        // Brightness weighting below still prevents them from darkening true shadows.
        const shadow = smoothstep(-0.12, 0.42, field);
        const index = (y * width + x) * 4;
        data[index] = 6;
        data[index + 1] = 10;
        data[index + 2] = 24;
        // Multiply-blended shadow strength is modulated by image lightness: dark pixels
        // remain almost untouched while highlights carry the moving shadow pattern.
        const brightnessWeight = brightnessWeights ? brightnessWeights[y * width + x] / 255 : 0.28;
        data[index + 3] = Math.round(shadow * intensity * brightnessWeight ** 0.55 * 210);
      }
    }
    context.putImageData(pixels, 0, 0);
  }

  function animate(time) {
    if (time - lastDraw >= 42) {
      draw(time);
      lastDraw = time;
    }
    if (enabled && !paused) animationFrame = requestAnimationFrame(animate);
  }

  function restart() {
    cancelAnimationFrame(animationFrame);
    if (enabled && !paused) animationFrame = requestAnimationFrame(animate);
  }

  image.addEventListener('load', resize);
  if (image.complete) resize();

  return {
    setEnabled(nextEnabled) {
      enabled = nextEnabled;
      canvas.hidden = !enabled;
      if (enabled && !pixels) resize();
      draw(performance.now());
      restart();
    },
    setPaused(nextPaused) {
      paused = nextPaused || prefersReducedMotion.matches;
      draw(performance.now());
      restart();
    },
    destroy() {
      cancelAnimationFrame(animationFrame);
      image.removeEventListener('load', resize);
      canvas.remove();
    },
  };
}

/**
 * A companion to the Fourier shadow layer: bright source areas emit a soft,
 * periodically pulsing light field that blooms into their immediate surroundings.
 */
function createPeriodicLightLayer(frame, image) {
  const canvas = document.createElement('canvas');
  canvas.className = 'image-effect-periodic-light';
  canvas.setAttribute('aria-hidden', 'true');
  frame.appendChild(canvas);

  const context = canvas.getContext('2d');
  const sourceCanvas = document.createElement('canvas');
  const sourceContext = sourceCanvas.getContext('2d', { willReadFrequently: true });
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let pixels;
  let brightWeights;
  let animationFrame;
  let lastDraw = 0;
  let enabled = false;
  let paused = prefersReducedMotion.matches;

  function resize() {
    if (!image.naturalWidth) return;
    const width = Math.min(190, image.naturalWidth);
    const height = Math.max(1, Math.round(width * image.naturalHeight / image.naturalWidth));
    canvas.width = sourceCanvas.width = width;
    canvas.height = sourceCanvas.height = height;
    pixels = context?.createImageData(width, height);
    brightWeights = new Uint8Array(width * height);
    if (!sourceContext) return;

    sourceContext.clearRect(0, 0, width, height);
    sourceContext.drawImage(image, 0, 0, width, height);
    try {
      const sourcePixels = sourceContext.getImageData(0, 0, width, height).data;
      for (let index = 0; index < brightWeights.length; index += 1) {
        const pixelIndex = index * 4;
        const luminance =
          0.2126 * sourcePixels[pixelIndex] / 255 +
          0.7152 * sourcePixels[pixelIndex + 1] / 255 +
          0.0722 * sourcePixels[pixelIndex + 2] / 255;
        brightWeights[index] = Math.round(smoothstep(0.20, 0.72, luminance) ** 0.72 * 255);
      }
    } catch (error) {
      brightWeights.fill(72);
      console.warn('Periodic light needs a same-origin or CORS-enabled image.', error);
    }
  }

  function draw(time) {
    if (!context || !pixels || !enabled) return;
    const { width, height } = canvas;
    const intensity = normaliseIntensity(frame.style.getPropertyValue('--fx-intensity'));
    const t = paused ? 0.65 : time * 0.000095;
    const data = pixels.data;

    for (let y = 0; y < height; y += 1) {
      const v = y / height;
      for (let x = 0; x < width; x += 1) {
        const u = x / width;
        const field =
          0.38 * Math.sin(Math.PI * 2 * (1.12 * u + 0.52 * v + t)) +
          0.26 * Math.sin(Math.PI * 2 * (-2.34 * u + 1.06 * v - t * 1.28) + 1.2) +
          0.20 * Math.sin(Math.PI * 2 * (3.74 * u - 1.48 * v + t * 0.74) + 2.1) +
          0.16 * Math.sin(Math.PI * 2 * (-5.68 * u - 2.20 * v - t * 0.46) + 0.6);
        const pulse = 0.22 + 0.78 * smoothstep(-0.18, 0.46, field);
        const index = (y * width + x) * 4;
        data[index] = 255;
        data[index + 1] = 224;
        data[index + 2] = 156;
        const brightWeight = brightWeights ? brightWeights[y * width + x] / 255 : 0.25;
        data[index + 3] = Math.round(brightWeight ** 0.65 * pulse * intensity * 168);
      }
    }
    context.putImageData(pixels, 0, 0);
  }

  function animate(time) {
    if (time - lastDraw >= 42) {
      draw(time);
      lastDraw = time;
    }
    if (enabled && !paused) animationFrame = requestAnimationFrame(animate);
  }

  function restart() {
    cancelAnimationFrame(animationFrame);
    if (enabled && !paused) animationFrame = requestAnimationFrame(animate);
  }

  image.addEventListener('load', resize);
  if (image.complete) resize();

  return {
    setEnabled(nextEnabled) {
      enabled = nextEnabled;
      canvas.hidden = !enabled;
      if (enabled && !pixels) resize();
      draw(performance.now());
      restart();
    },
    setPaused(nextPaused) {
      paused = nextPaused || prefersReducedMotion.matches;
      draw(performance.now());
      restart();
    },
    destroy() {
      cancelAnimationFrame(animationFrame);
      image.removeEventListener('load', resize);
      canvas.remove();
    },
  };
}

/**
 * Finds small bright local maxima against dark neighborhoods, then renders each
 * point as an independently phased, four-pointed star flare. This keeps twinkles in night sky
 * and dark-space details instead of treating all bright illustration edges as stars.
 */
function createStarTwinkleLayer(frame, image) {
  const canvas = document.createElement('canvas');
  canvas.className = 'image-effect-star-twinkle';
  canvas.setAttribute('aria-hidden', 'true');
  frame.appendChild(canvas);

  const context = canvas.getContext('2d');
  const sourceCanvas = document.createElement('canvas');
  const sourceContext = sourceCanvas.getContext('2d', { willReadFrequently: true });
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let stars = [];
  let animationFrame;
  let lastDraw = 0;
  let enabled = false;
  let paused = prefersReducedMotion.matches;

  function hash(x, y, salt = 0) {
    const value = Math.sin(x * 12.9898 + y * 78.233 + salt * 37.719) * 43758.5453;
    return value - Math.floor(value);
  }

  function analyse() {
    if (!sourceContext || !image.naturalWidth) return;
    const width = Math.min(420, image.naturalWidth);
    const height = Math.max(1, Math.round(width * image.naturalHeight / image.naturalWidth));
    canvas.width = sourceCanvas.width = width;
    canvas.height = sourceCanvas.height = height;
    sourceContext.clearRect(0, 0, width, height);
    sourceContext.drawImage(image, 0, 0, width, height);

    try {
      const sourcePixels = sourceContext.getImageData(0, 0, width, height).data;
      const luminance = new Float32Array(width * height);
      for (let index = 0; index < luminance.length; index += 1) {
        const pixelIndex = index * 4;
        luminance[index] =
          0.2126 * sourcePixels[pixelIndex] / 255 +
          0.7152 * sourcePixels[pixelIndex + 1] / 255 +
          0.0722 * sourcePixels[pixelIndex + 2] / 255;
      }

      const candidates = [];
      for (let y = 2; y < height - 2; y += 1) {
        for (let x = 2; x < width - 2; x += 1) {
          const index = y * width + x;
          const center = luminance[index];
          if (center < 0.52) continue;

          let neighborTotal = 0;
          let isPeak = true;
          for (let offsetY = -2; offsetY <= 2; offsetY += 1) {
            for (let offsetX = -2; offsetX <= 2; offsetX += 1) {
              if (offsetX === 0 && offsetY === 0) continue;
              const neighbor = luminance[(y + offsetY) * width + x + offsetX];
              neighborTotal += neighbor;
              if (neighbor > center) isPeak = false;
            }
          }
          const neighborhood = neighborTotal / 24;
          const contrast = center - neighborhood;
          if (isPeak && neighborhood < 0.38 && contrast > 0.22) {
            const pixelIndex = index * 4;
            candidates.push({
              x,
              y,
              score: center * contrast,
              red: sourcePixels[pixelIndex],
              green: sourcePixels[pixelIndex + 1],
              blue: sourcePixels[pixelIndex + 2],
            });
          }
        }
      }

      candidates.sort((left, right) => right.score - left.score);
      stars = [];
      for (const candidate of candidates) {
        if (stars.some((star) => Math.hypot(star.x - candidate.x, star.y - candidate.y) < 3.2)) continue;
        const random = hash(candidate.x, candidate.y);
        stars.push({
          ...candidate,
          phase: random * Math.PI * 2,
          frequency: 1.1 + hash(candidate.x, candidate.y, 1) * 1.9,
          radius: 0.42 + Math.min(0.88, candidate.score * 1.9),
          rotation: (hash(candidate.x, candidate.y, 2) - 0.5) * 0.48,
        });
        if (stars.length === 160) break;
      }
      canvas.dataset.sparkleCount = String(stars.length);
      draw(performance.now());
    } catch (error) {
      stars = [];
      canvas.dataset.sparkleCount = '0';
      console.warn('Starlight twinkle needs a same-origin or CORS-enabled image.', error);
    }
  }

  function draw(time) {
    if (!context || !enabled) return;
    const intensity = normaliseIntensity(frame.style.getPropertyValue('--fx-intensity'));
    const t = paused ? 0.9 : time * 0.001;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.globalCompositeOperation = 'lighter';

    for (const star of stars) {
      const wave = (Math.sin(t * star.frequency + star.phase) + 1) / 2;
      const twinkle = 0.06 + 0.94 * wave ** 6;
      const radius = star.radius * (1.1 + twinkle * 3.4) * intensity;
      const gradient = context.createRadialGradient(star.x, star.y, 0, star.x, star.y, radius);
      gradient.addColorStop(0, `rgba(${star.red}, ${star.green}, ${star.blue}, ${0.94 * twinkle})`);
      gradient.addColorStop(0.24, `rgba(${star.red}, ${star.green}, ${star.blue}, ${0.52 * twinkle})`);
      gradient.addColorStop(1, `rgba(${star.red}, ${star.green}, ${star.blue}, 0)`);
      context.fillStyle = gradient;
      context.fillRect(star.x - radius, star.y - radius, radius * 2, radius * 2);

      if (twinkle > 0.58) {
        const flare = radius * (1.45 + twinkle * 2.65);
        const inset = flare * 0.17;
        // A filled concave diamond has four actual points, unlike two crossed
        // strokes; a small per-star rotation keeps the field from looking rigid.
        context.save();
        context.translate(star.x, star.y);
        context.rotate(star.rotation);
        context.fillStyle = `rgba(${star.red}, ${star.green}, ${star.blue}, ${(twinkle - 0.5) * 0.92})`;
        context.beginPath();
        context.moveTo(0, -flare);
        context.lineTo(inset, -inset);
        context.lineTo(flare, 0);
        context.lineTo(inset, inset);
        context.lineTo(0, flare);
        context.lineTo(-inset, inset);
        context.lineTo(-flare, 0);
        context.lineTo(-inset, -inset);
        context.closePath();
        context.fill();
        context.restore();
      }
    }
    context.globalCompositeOperation = 'source-over';
  }

  function animate(time) {
    if (time - lastDraw >= 42) {
      draw(time);
      lastDraw = time;
    }
    if (enabled && !paused) animationFrame = requestAnimationFrame(animate);
  }

  function restart() {
    cancelAnimationFrame(animationFrame);
    if (enabled && !paused) animationFrame = requestAnimationFrame(animate);
  }

  image.addEventListener('load', analyse);
  if (image.complete) analyse();

  return {
    setEnabled(nextEnabled) {
      enabled = nextEnabled;
      canvas.hidden = !enabled;
      if (enabled && !stars.length) analyse();
      draw(performance.now());
      restart();
    },
    setPaused(nextPaused) {
      paused = nextPaused || prefersReducedMotion.matches;
      draw(performance.now());
      restart();
    },
    destroy() {
      cancelAnimationFrame(animationFrame);
      image.removeEventListener('load', analyse);
      canvas.remove();
    },
  };
}

/**
 * Separably blurs the HSV Value channel. Keeping this independent from RGB lets
 * the treatment raise or lower brightness without rotating hue or changing
 * saturation: scaling RGB by nextValue / value preserves both HSV H and S.
 */
function gaussianBlurValues(values, width, height, radius) {
  const sigma = Math.max(0.8, radius / 2);
  const kernelRadius = Math.max(1, Math.ceil(sigma * 2.5));
  const kernel = new Float32Array(kernelRadius * 2 + 1);
  let kernelTotal = 0;
  for (let offset = -kernelRadius; offset <= kernelRadius; offset += 1) {
    const weight = Math.exp(-(offset * offset) / (2 * sigma * sigma));
    kernel[offset + kernelRadius] = weight;
    kernelTotal += weight;
  }
  for (let index = 0; index < kernel.length; index += 1) kernel[index] /= kernelTotal;

  const horizontal = new Float32Array(values.length);
  const blurred = new Float32Array(values.length);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      let total = 0;
      for (let offset = -kernelRadius; offset <= kernelRadius; offset += 1) {
        total += values[y * width + Math.min(width - 1, Math.max(0, x + offset))] * kernel[offset + kernelRadius];
      }
      horizontal[y * width + x] = total;
    }
  }
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      let total = 0;
      for (let offset = -kernelRadius; offset <= kernelRadius; offset += 1) {
        total += horizontal[Math.min(height - 1, Math.max(0, y + offset)) * width + x] * kernel[offset + kernelRadius];
      }
      blurred[y * width + x] = total;
    }
  }
  return blurred;
}

/**
 * Gleam 3 keeps the inverse-Gaussian highlight mask spatially fixed, then
 * builds its time-varying mask from many short-lived Gaussians. Their centres
 * are sampled from the value-flow peaks, so broad bright basins receive more
 * visits while the yellow positive-residual mask still clips the final light.
 */
function createGleam3Layer(frame, image) {
  const canvas = document.createElement('canvas');
  canvas.className = 'image-effect-gleam3';
  canvas.setAttribute('aria-hidden', 'true');
  frame.appendChild(canvas);

  const seedMapCanvas = document.createElement('canvas');
  seedMapCanvas.className = 'image-effect-gleam3-seed-map';
  seedMapCanvas.setAttribute('aria-hidden', 'true');
  seedMapCanvas.hidden = true;
  frame.appendChild(seedMapCanvas);

  const inverseMapCanvas = document.createElement('canvas');
  inverseMapCanvas.className = 'image-effect-v-inverse-map';
  inverseMapCanvas.setAttribute('aria-hidden', 'true');
  inverseMapCanvas.hidden = true;
  frame.appendChild(inverseMapCanvas);

  const sourceCanvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  const seedMapContext = seedMapCanvas.getContext('2d');
  const inverseMapContext = inverseMapCanvas.getContext('2d');
  const sourceContext = sourceCanvas.getContext('2d', { willReadFrequently: true });
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let sourcePixels;
  let outputPixels;
  let seedMask;
  let flowSeedStrength;
  let seedIndices = [];
  let seedCumulativeWeights = [];
  let seedWeightTotal = 0;
  let gaussians = [];
  let animationFrame;
  let lastDraw = 0;
  let enabled = false;
  let paused = prefersReducedMotion.matches;
  let seedMapVisible = false;
  let inverseMapVisible = false;
  // Overlapping Gaussians may enrich the mask, but never make it grow without
  // bound. This is energy saturation, not a colour-saturation adjustment.
  const maximumGaussianEnergy = 2.0;

  function pickSeedIndex() {
    if (!seedIndices.length || !seedWeightTotal) return undefined;
    const target = Math.random() * seedWeightTotal;
    let low = 0;
    let high = seedCumulativeWeights.length - 1;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (seedCumulativeWeights[middle] < target) low = middle + 1;
      else high = middle;
    }
    return seedIndices[low];
  }

  function renewGaussian(gaussian, time) {
    const index = pickSeedIndex();
    if (index === undefined) return;
    gaussian.x = index % canvas.width;
    gaussian.y = Math.floor(index / canvas.width);
    // Broader kernels make the temporal field read as drifting light instead
    // of pin-prick sparkles while still retaining distinct Gaussian centres.
    gaussian.sigma = 5.0 + Math.random() * 6.0;
    gaussian.amplitude = 0.22 + Math.random() * 0.42;
    gaussian.born = time;
    gaussian.lifetime = (620 + Math.random() * 1180) / 0.75;
  }

  function drawSeedMap() {
    if (!seedMapContext || !sourcePixels || !flowSeedStrength) return;
    const map = seedMapContext.createImageData(seedMapCanvas.width, seedMapCanvas.height);
    for (let index = 0; index < flowSeedStrength.length; index += 1) {
      const pixelIndex = index * 4;
      const strength = flowSeedStrength[index];
      if (strength > 0) {
        // Cyan points visualize the exact value-flow distribution used for
        // Gaussian birth sampling; brighter points carry more basin weight.
        map.data[pixelIndex] = Math.round(94 + strength * 110);
        map.data[pixelIndex + 1] = Math.round(182 + strength * 73);
        map.data[pixelIndex + 2] = 255;
      } else {
        map.data[pixelIndex] = Math.round(sourcePixels.data[pixelIndex] * 0.10);
        map.data[pixelIndex + 1] = Math.round(sourcePixels.data[pixelIndex + 1] * 0.10);
        map.data[pixelIndex + 2] = Math.round(sourcePixels.data[pixelIndex + 2] * 0.10);
      }
      map.data[pixelIndex + 3] = 255;
    }
    seedMapContext.putImageData(map, 0, 0);
  }

  function drawInverseMap(values, blurredValues) {
    if (!inverseMapContext) return;
    const map = inverseMapContext.createImageData(inverseMapCanvas.width, inverseMapCanvas.height);
    for (let index = 0; index < values.length; index += 1) {
      const pixelIndex = index * 4;
      const residual = values[index] - blurredValues[index];
      const strength = Math.min(1, Math.abs(residual) * 7.5);
      // Warm pixels sit above their local Gaussian field; cool pixels sit below it.
      if (residual >= 0) {
        map.data[pixelIndex] = Math.round(26 + 229 * strength);
        map.data[pixelIndex + 1] = Math.round(21 + 170 * strength);
        map.data[pixelIndex + 2] = Math.round(14 + 62 * strength);
      } else {
        map.data[pixelIndex] = Math.round(12 + 58 * strength);
        map.data[pixelIndex + 1] = Math.round(22 + 112 * strength);
        map.data[pixelIndex + 2] = Math.round(30 + 225 * strength);
      }
      map.data[pixelIndex + 3] = 255;
    }
    inverseMapContext.putImageData(map, 0, 0);
  }

  function analyse() {
    if (!sourceContext || !image.naturalWidth) return;
    const width = Math.min(360, image.naturalWidth);
    const height = Math.max(1, Math.round(width * image.naturalHeight / image.naturalWidth));
    canvas.width = seedMapCanvas.width = inverseMapCanvas.width = sourceCanvas.width = width;
    canvas.height = seedMapCanvas.height = inverseMapCanvas.height = sourceCanvas.height = height;
    sourceContext.clearRect(0, 0, width, height);
    sourceContext.drawImage(image, 0, 0, width, height);

    try {
      sourcePixels = sourceContext.getImageData(0, 0, width, height);
      outputPixels = context?.createImageData(width, height);
      const values = new Float32Array(width * height);
      for (let index = 0; index < values.length; index += 1) {
        const pixelIndex = index * 4;
        values[index] = Math.max(sourcePixels.data[pixelIndex], sourcePixels.data[pixelIndex + 1], sourcePixels.data[pixelIndex + 2]) / 255;
      }
      const blurredValues = gaussianBlurValues(values, width, height, 6);
      drawInverseMap(values, blurredValues);
      const flow = buildValueFlowPeaks(values, width, height);
      seedMask = new Float32Array(values.length);
      flowSeedStrength = new Float32Array(values.length);
      seedIndices = [];
      seedCumulativeWeights = [];
      seedWeightTotal = 0;
      // Avoid a diffuse field of tiny basins: only peaks with meaningful
      // upstream value-flow support may seed a renewing Gaussian.
      const minimumFlowScore = Math.max(64, flow.maximumScore * 0.26);
      for (let index = 0; index < values.length; index += 1) {
        const positiveResidual = Math.max(0, values[index] - blurredValues[index]);
        seedMask[index] = smoothstep(0.015, 0.060, positiveResidual);
        const score = flow.peakScores[index];
        if (score >= minimumFlowScore) {
          const strength = Math.log1p(score) / Math.log1p(flow.maximumScore);
          flowSeedStrength[index] = strength;
          seedWeightTotal += 0.10 + strength * strength;
          seedIndices.push(index);
          seedCumulativeWeights.push(seedWeightTotal);
        }
      }
      drawSeedMap();
      gaussians = Array.from({ length: Math.min(156, Math.max(52, Math.round(seedIndices.length / 18))) }, () => ({}));
      const now = performance.now();
      gaussians.forEach((gaussian) => renewGaussian(gaussian, now - Math.random() * 1350));
      canvas.dataset.gleam3SeedPixels = String(seedIndices.length);
      canvas.dataset.gleam3SeedSource = 'value-flow';
      canvas.dataset.gleam3FlowMaximumScore = String(flow.maximumScore);
      canvas.dataset.gleam3MinimumFlowScore = String(minimumFlowScore);
      canvas.dataset.gleam3Gaussians = String(gaussians.length);
      canvas.dataset.gleam3MaximumEnergy = String(maximumGaussianEnergy);
      canvas.hidden = !enabled;
      seedMapCanvas.hidden = !seedMapVisible;
      inverseMapCanvas.hidden = !inverseMapVisible;
      draw(now);
    } catch (error) {
      sourcePixels = undefined;
      outputPixels = undefined;
      seedMask = undefined;
      flowSeedStrength = undefined;
      seedIndices = [];
      seedCumulativeWeights = [];
      gaussians = [];
      canvas.hidden = true;
      seedMapCanvas.hidden = true;
      inverseMapCanvas.hidden = true;
      canvas.dataset.gleam3SeedPixels = '0';
      console.warn('Gleam 3 needs a same-origin or CORS-enabled image.', error);
    }
  }

  function draw(time) {
    if (!context || !outputPixels || !seedMask || !enabled) return;
    const variation = new Float32Array(seedMask.length);
    for (const gaussian of gaussians) {
      if (time - gaussian.born >= gaussian.lifetime) renewGaussian(gaussian, time);
      const age = Math.max(0, Math.min(1, (time - gaussian.born) / gaussian.lifetime));
      const envelope = Math.sin(Math.PI * age) ** 1.7;
      const radius = Math.ceil(gaussian.sigma * 3.25);
      const minX = Math.max(0, Math.floor(gaussian.x - radius));
      const maxX = Math.min(canvas.width - 1, Math.ceil(gaussian.x + radius));
      const minY = Math.max(0, Math.floor(gaussian.y - radius));
      const maxY = Math.min(canvas.height - 1, Math.ceil(gaussian.y + radius));
      const inverseTwoSigmaSquared = 1 / (2 * gaussian.sigma * gaussian.sigma);
      for (let y = minY; y <= maxY; y += 1) {
        for (let x = minX; x <= maxX; x += 1) {
          const distanceSquared = (x - gaussian.x) ** 2 + (y - gaussian.y) ** 2;
          variation[y * canvas.width + x] += gaussian.amplitude * envelope * Math.exp(-distanceSquared * inverseTwoSigmaSquared);
        }
      }
    }
    const intensity = normaliseIntensity(frame.style.getPropertyValue('--fx-intensity'));
    for (let index = 0; index < seedMask.length; index += 1) {
      const pixelIndex = index * 4;
      const cappedEnergy = Math.min(variation[index], maximumGaussianEnergy);
      const temporalMask = 1 - Math.exp(-cappedEnergy * 1.55);
      const alpha = seedMask[index] * temporalMask * intensity;
      outputPixels.data[pixelIndex] = 255;
      outputPixels.data[pixelIndex + 1] = 245;
      outputPixels.data[pixelIndex + 2] = 205;
      outputPixels.data[pixelIndex + 3] = Math.round(alpha * 222);
    }
    context.putImageData(outputPixels, 0, 0);
  }

  function animate(time) {
    if (time - lastDraw >= 34) {
      draw(time);
      lastDraw = time;
    }
    if (enabled && !paused) animationFrame = requestAnimationFrame(animate);
  }

  function restart() {
    cancelAnimationFrame(animationFrame);
    if (enabled && !paused) animationFrame = requestAnimationFrame(animate);
  }

  image.addEventListener('load', analyse);
  if (image.complete) analyse();

  return {
    setEnabled(nextEnabled) {
      enabled = nextEnabled;
      canvas.hidden = !enabled;
      if (enabled && !sourcePixels) analyse();
      draw(performance.now());
      restart();
    },
    setPaused(nextPaused) {
      paused = nextPaused || prefersReducedMotion.matches;
      draw(performance.now());
      restart();
    },
    setSeedMapVisible(nextVisible) {
      seedMapVisible = nextVisible;
      seedMapCanvas.hidden = !seedMapVisible;
    },
    setInverseMapVisible(nextVisible) {
      inverseMapVisible = nextVisible;
      inverseMapCanvas.hidden = !inverseMapVisible;
    },
    destroy() {
      cancelAnimationFrame(animationFrame);
      image.removeEventListener('load', analyse);
      canvas.remove();
      seedMapCanvas.remove();
      inverseMapCanvas.remove();
    },
  };
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

/** Map any continuous coordinate back into an image by mirrored reflection. */
function mirrorCoordinate(value, maximum) {
  if (maximum <= 0) return 0;
  const period = maximum * 2;
  const wrapped = ((value % period) + period) % period;
  return wrapped <= maximum ? wrapped : period - wrapped;
}

/**
 * Finds prominent image-spanning edges with a small weighted Hough transform.
 * Lines are stored in normal form: normal . point = offset.
 */
function findStrongLines(sourcePixels, width, height) {
  const luminance = new Float32Array(width * height);
  for (let index = 0; index < luminance.length; index += 1) {
    const pixel = index * 4;
    luminance[index] = (0.2126 * sourcePixels.data[pixel] + 0.7152 * sourcePixels.data[pixel + 1] + 0.0722 * sourcePixels.data[pixel + 2]) / 255;
  }

  const diagonal = Math.hypot(width, height);
  const angleCount = 54;
  const rhoCount = Math.ceil(diagonal * 2) + 1;
  const accumulator = new Float32Array(angleCount * rhoCount);
  const cosines = Array.from({ length: angleCount }, (_, angle) => Math.cos(Math.PI * angle / angleCount));
  const sines = Array.from({ length: angleCount }, (_, angle) => Math.sin(Math.PI * angle / angleCount));
  const candidates = [];

  for (let y = 1; y < height - 1; y += 2) {
    for (let x = 1; x < width - 1; x += 2) {
      const gradientX = luminance[y * width + x + 1] - luminance[y * width + x - 1];
      const gradientY = luminance[(y + 1) * width + x] - luminance[(y - 1) * width + x];
      const strength = Math.hypot(gradientX, gradientY);
      if (strength < 0.12) continue;
      // The edge gradient is already the line normal, so a narrow angular vote
      // is less noisy than voting for every possible direction.
      const normalAngle = Math.atan2(gradientY, gradientX);
      const centerAngle = Math.round((((normalAngle / Math.PI) % 1 + 1) % 1) * angleCount) % angleCount;
      for (let delta = -2; delta <= 2; delta += 1) {
        const angle = (centerAngle + delta + angleCount) % angleCount;
        const rho = Math.round(x * cosines[angle] + y * sines[angle] + diagonal);
        accumulator[angle * rhoCount + clamp(rho, 0, rhoCount - 1)] += strength * (delta === 0 ? 1 : 0.55);
      }
    }
  }

  for (let angle = 0; angle < angleCount; angle += 1) {
    for (let rho = 0; rho < rhoCount; rho += 1) {
      const score = accumulator[angle * rhoCount + rho];
      if (score > 0) candidates.push({ angle, rho, score });
    }
  }
  candidates.sort((a, b) => b.score - a.score);

  const strongest = candidates[0];
  if (!strongest) return [];
  const primary = {
    angle: strongest.angle,
    nx: cosines[strongest.angle],
    ny: sines[strongest.angle],
    offset: strongest.rho - diagonal,
    score: strongest.score,
  };

  // Find the strongest line's in-frame segment, then place the derived line
  // perpendicular to it through that segment's midpoint.
  const point = { x: primary.nx * primary.offset, y: primary.ny * primary.offset };
  const direction = { x: -primary.ny, y: primary.nx };
  const intersections = [];
  const addIntersection = (x, y) => {
    if (x >= -0.001 && x <= width - 1 + 0.001 && y >= -0.001 && y <= height - 1 + 0.001
      && !intersections.some((existing) => Math.hypot(existing.x - x, existing.y - y) < 0.01)) {
      intersections.push({ x: clamp(x, 0, width - 1), y: clamp(y, 0, height - 1) });
    }
  };
  if (Math.abs(direction.x) > 0.0001) {
    addIntersection(0, point.y - point.x * direction.y / direction.x);
    addIntersection(width - 1, point.y + (width - 1 - point.x) * direction.y / direction.x);
  }
  if (Math.abs(direction.y) > 0.0001) {
    addIntersection(point.x - point.y * direction.x / direction.y, 0);
    addIntersection(point.x + (height - 1 - point.y) * direction.x / direction.y, height - 1);
  }
  let start = intersections[0] || { x: width / 2, y: height / 2 };
  let end = intersections[1] || start;
  let largestDistance = -1;
  for (const first of intersections) {
    for (const second of intersections) {
      const distance = Math.hypot(first.x - second.x, first.y - second.y);
      if (distance > largestDistance) {
        start = first;
        end = second;
        largestDistance = distance;
      }
    }
  }
  const midpoint = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
  const secondary = {
    // This normal follows the primary line, making the derived line itself
    // perpendicular to the detected boundary.
    nx: direction.x,
    ny: direction.y,
    offset: midpoint.x * direction.x + midpoint.y * direction.y,
    score: strongest.score,
    derived: 'perpendicular-bisector',
  };
  return [primary, secondary];
}

function sampleBilinear(source, width, height, x, y, output, outputIndex) {
  x = mirrorCoordinate(x, width - 1);
  y = mirrorCoordinate(y, height - 1);
  const left = Math.floor(x);
  const top = Math.floor(y);
  const right = Math.min(width - 1, left + 1);
  const bottom = Math.min(height - 1, top + 1);
  const horizontal = x - left;
  const vertical = y - top;
  const topLeft = (top * width + left) * 4;
  const topRight = (top * width + right) * 4;
  const bottomLeft = (bottom * width + left) * 4;
  const bottomRight = (bottom * width + right) * 4;
  for (let channel = 0; channel < 4; channel += 1) {
    const upper = source.data[topLeft + channel] * (1 - horizontal) + source.data[topRight + channel] * horizontal;
    const lower = source.data[bottomLeft + channel] * (1 - horizontal) + source.data[bottomRight + channel] * horizontal;
    output.data[outputIndex + channel] = Math.round(upper * (1 - vertical) + lower * vertical);
  }
}

/**
 * Bendy partitions the artwork with its strongest lines then reverse-maps the
 * output canvas through a collection of boundary-preserving normal warps.
 * Each warp is zero at the detected line and at the image boundary, leaving all
 * lookup coordinates inside the input image while expanding/compressing regions.
 */
function createBendyLayer(frame, image) {
  const canvas = document.createElement('canvas');
  canvas.className = 'image-effect-bendy';
  canvas.setAttribute('aria-hidden', 'true');
  frame.appendChild(canvas);

  const lineMapCanvas = document.createElement('canvas');
  lineMapCanvas.className = 'image-effect-bendy-line-map';
  lineMapCanvas.setAttribute('aria-hidden', 'true');
  lineMapCanvas.hidden = true;
  frame.appendChild(lineMapCanvas);

  const sourceCanvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  const sourceContext = sourceCanvas.getContext('2d', { willReadFrequently: true });
  const lineMapContext = lineMapCanvas.getContext('2d');
  let sourcePixels;
  let outputPixels;
  let lines = [];
  let analysedSourceKey;
  let enabled = false;
  let lineMapVisible = false;
  let paused = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let animationFrame;
  let motionStartedAt = performance.now();
  let cacheGeneration = 0;
  let cacheSignature;
  let cacheFrames = new Map();
  let cacheReady = false;
  // A restrained cycle: at the zero resting intensity this yields
  // -0.03, -0.02, -0.01, 0, +0.01, +0.02, +0.03.
  const cachedFrameCount = 7;
  const motionAmplitude = 0.03;
  // Cached-frame playback only composites two canvases, so it can move faster
  // than the one-time reverse-warp calculation without a quality penalty.
  const motionPeriod = 900;

  function drawLineMap() {
    if (!lineMapContext || !sourcePixels) return;
    const map = lineMapContext.createImageData(canvas.width, canvas.height);
    map.data.set(sourcePixels.data);
    for (let y = 0; y < canvas.height; y += 1) {
      for (let x = 0; x < canvas.width; x += 1) {
        let nearest = Infinity;
        for (const line of lines) nearest = Math.min(nearest, Math.abs(x * line.nx + y * line.ny - line.offset));
        if (nearest < 1.35) {
          const pixel = (y * canvas.width + x) * 4;
          const glow = 1 - nearest / 1.35;
          map.data[pixel] = Math.round(72 + 183 * glow);
          map.data[pixel + 1] = Math.round(220 + 35 * glow);
          map.data[pixel + 2] = 255;
        }
      }
    }
    lineMapContext.putImageData(map, 0, 0);
  }

  function renderPixels(targetPixels, intensity) {
    if (!sourcePixels || !targetPixels) return;
    // At full intensity the transform remains monotonic on either side of each
    // line; this avoids folds and guarantees the reverse lookup stays legal.
    const warpAmount = intensity * 0.115;
    const { width, height } = canvas;
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        let sourceX = x;
        let sourceY = y;
        for (let index = 0; index < lines.length; index += 1) {
          const line = lines[index];
          const signedDistance = sourceX * line.nx + sourceY * line.ny - line.offset;
          const direction = signedDistance < 0 ? -1 : 1;
          const boundaryDistance = direction > 0
            ? Math.min((width - 1 - sourceX) / Math.max(0.001, line.nx), (height - 1 - sourceY) / Math.max(0.001, line.ny))
            : Math.min(sourceX / Math.max(0.001, -line.nx), sourceY / Math.max(0.001, -line.ny));
          // A corner can be the first boundary, even when a normal component is
          // negative. Evaluate all four ray intersections and retain the nearest.
          const rayLimits = [
            line.nx > 0 ? (width - 1 - sourceX) / line.nx : line.nx < 0 ? -sourceX / line.nx : Infinity,
            line.ny > 0 ? (height - 1 - sourceY) / line.ny : line.ny < 0 ? -sourceY / line.ny : Infinity,
          ];
          const edgeDistance = Math.min(...rayLimits.filter((value) => value >= 0));
          const sideLength = Math.max(1, Math.min(Math.abs(signedDistance) + edgeDistance, diagonalFor(width, height)));
          const normalized = clamp(Math.abs(signedDistance) / sideLength, 0, 1);
          const bend = Math.sin(Math.PI * normalized) * sideLength * warpAmount * (index % 2 === 0 ? 1 : -1);
          sourceX += direction * line.nx * bend;
          sourceY += direction * line.ny * bend;
        }
        // A combined line field may cross an image edge. The sampler reflects
        // that coordinate back across the edge, so every texel read stays in
        // the source image without creating a stretched border colour.
        sampleBilinear(sourcePixels, width, height, sourceX, sourceY, targetPixels, (y * width + x) * 4);
      }
    }
  }

  function cacheSettings() {
    const center = normaliseSignedIntensity(frame.style.getPropertyValue('--fx-intensity'));
    const amplitude = Math.min(motionAmplitude, 1 - Math.abs(center));
    return { center, amplitude, signature: `${analysedSourceKey}|${center.toFixed(4)}|${amplitude.toFixed(4)}` };
  }

  function displayCachedFrame(time) {
    if (!context || !enabled) return;
    const { center, amplitude } = cacheSettings();
    const phase = paused ? 0 : (time - motionStartedAt) / motionPeriod;
    const targetIntensity = center + amplitude * Math.sin(Math.PI * 2 * phase);
    const position = amplitude ? (targetIntensity - (center - amplitude)) / (amplitude * 2) : 0.5;
    const index = clamp(Math.round(position * (cachedFrameCount - 1)), 0, cachedFrameCount - 1);
    const cachedFrame = cacheFrames.get(index);
    if (!cachedFrame) return;
    canvas.hidden = false;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(cachedFrame, 0, 0);
  }

  function buildFrameCache() {
    if (!context || !sourcePixels || !outputPixels) return false;
    const { center, amplitude, signature } = cacheSettings();
    if (cacheSignature === signature) return cacheReady;
    cacheSignature = signature;
    cacheFrames = new Map();
    cacheReady = false;
    canvas.dataset.bendyCacheState = 'building';
    const generation = ++cacheGeneration;
    const values = Array.from({ length: cachedFrameCount }, (_, index) => (
      center - amplitude + (index / (cachedFrameCount - 1)) * amplitude * 2
    ));
    canvas.dataset.bendyCachedIntensityFrames = values.map((value) => value.toFixed(3)).join(',');
    // Build the resting frame first so the source is replaced immediately, then
    // fill outward through the motion range one cached frame per paint cycle.
    const centerIndex = Math.floor(cachedFrameCount / 2);
    const buildOrder = [centerIndex, centerIndex + 1, centerIndex - 1, centerIndex + 2, centerIndex - 2, centerIndex + 3, centerIndex - 3]
      .filter((index) => index >= 0 && index < cachedFrameCount);
    let orderIndex = 0;
    const buildNext = () => {
      if (generation !== cacheGeneration || !sourcePixels || !enabled) return;
      const index = buildOrder[orderIndex];
      const pixels = context.createImageData(canvas.width, canvas.height);
      renderPixels(pixels, values[index]);
      const cachedCanvas = document.createElement('canvas');
      cachedCanvas.width = canvas.width;
      cachedCanvas.height = canvas.height;
      cachedCanvas.getContext('2d')?.putImageData(pixels, 0, 0);
      cacheFrames.set(index, cachedCanvas);
      canvas.dataset.bendyCachedFrames = String(cacheFrames.size);
      orderIndex += 1;
      if (orderIndex < buildOrder.length) {
        requestAnimationFrame(buildNext);
      } else {
        cacheReady = true;
        canvas.dataset.bendyCacheState = 'ready';
        motionStartedAt = performance.now();
        displayCachedFrame(motionStartedAt);
        if (!paused) restartAnimation();
      }
    };
    requestAnimationFrame(buildNext);
    return false;
  }

  function animate(time) {
    displayCachedFrame(time);
    if (enabled && !paused) animationFrame = requestAnimationFrame(animate);
  }

  function restartAnimation() {
    cancelAnimationFrame(animationFrame);
    if (enabled && !paused) {
      motionStartedAt = performance.now();
      animationFrame = requestAnimationFrame(animate);
    }
  }

  function render() {
    if (!enabled || !sourcePixels) return;
    if (!buildFrameCache()) {
      // The untouched source remains on screen until the entire cycle exists.
      canvas.hidden = true;
      cancelAnimationFrame(animationFrame);
      return;
    }
    if (paused) {
      displayCachedFrame(motionStartedAt);
      return;
    }
    displayCachedFrame(performance.now());
    restartAnimation();
  }

  function analyse() {
    if (!sourceContext || !image.naturalWidth) return;
    const sourceKey = image.currentSrc || image.src;
    // Rendering responds to intensity changes, but the expensive Hough pass is
    // retained until the source artwork actually changes.
    if (sourceKey === analysedSourceKey && sourcePixels && outputPixels) {
      canvas.hidden = !enabled;
      lineMapCanvas.hidden = !lineMapVisible;
      render();
      return;
    }
    // Keep both line detection and reverse sampling at source resolution. The
    // only downsampling happens later when CSS displays the finished canvas.
    const width = image.naturalWidth;
    const height = image.naturalHeight;
    canvas.width = lineMapCanvas.width = sourceCanvas.width = width;
    canvas.height = lineMapCanvas.height = sourceCanvas.height = height;
    sourceContext.clearRect(0, 0, width, height);
    sourceContext.drawImage(image, 0, 0, width, height);
    try {
      sourcePixels = sourceContext.getImageData(0, 0, width, height);
      outputPixels = context?.createImageData(width, height);
      lines = findStrongLines(sourcePixels, width, height);
      analysedSourceKey = sourceKey;
      canvas.dataset.bendyLineCount = String(lines.length);
      drawLineMap();
      canvas.hidden = !enabled;
      lineMapCanvas.hidden = !lineMapVisible;
      render();
    } catch (error) {
      sourcePixels = undefined;
      outputPixels = undefined;
      lines = [];
      analysedSourceKey = undefined;
      canvas.hidden = true;
      lineMapCanvas.hidden = true;
      console.warn('Bendy needs a same-origin or CORS-enabled image.', error);
    }
  }

  image.addEventListener('load', analyse);
  if (image.complete) analyse();

  return {
    setEnabled(nextEnabled) {
      enabled = nextEnabled;
      canvas.hidden = !enabled;
      if (enabled && !sourcePixels) analyse();
      render();
      if (!enabled) {
        cancelAnimationFrame(animationFrame);
        cacheGeneration += 1;
        cacheSignature = undefined;
        cacheReady = false;
      }
    },
    setPaused(nextPaused) {
      paused = nextPaused || window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      render();
    },
    setLineMapVisible(nextVisible) {
      lineMapVisible = nextVisible;
      lineMapCanvas.hidden = !lineMapVisible;
    },
    destroy() {
      cancelAnimationFrame(animationFrame);
      cacheGeneration += 1;
      image.removeEventListener('load', analyse);
      canvas.remove();
      lineMapCanvas.remove();
    },
  };
}

/** Soft, blurred copies of the source drift as slow atmospheric bubbles. */
function createHazeLayer(frame, image) {
  const canvas = document.createElement('canvas');
  canvas.className = 'image-effect-haze';
  canvas.setAttribute('aria-hidden', 'true');
  frame.appendChild(canvas);

  const sourceCanvas = document.createElement('canvas');
  const bubbleCanvas = document.createElement('canvas');
  const twistCanvas = document.createElement('canvas');
  const twistSourceCanvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  const sourceContext = sourceCanvas.getContext('2d');
  const bubbleContext = bubbleCanvas.getContext('2d');
  const twistContext = twistCanvas.getContext('2d');
  const twistSourceContext = twistSourceCanvas.getContext('2d', { willReadFrequently: true });
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const bubbles = [
    { x: 0.25, radius: 0.11, speed: 0.000050, phase: 0.00 },
    { x: 0.66, radius: 0.14, speed: 0.000041, phase: 0.35 },
    { x: 0.46, radius: 0.09, speed: 0.000058, phase: 0.68 },
    { x: 0.12, radius: 0.08, speed: 0.000053, phase: 0.13 },
    { x: 0.84, radius: 0.12, speed: 0.000046, phase: 0.48 },
    { x: 0.38, radius: 0.10, speed: 0.000061, phase: 0.82 },
    { x: 0.57, radius: 0.07, speed: 0.000055, phase: 0.24 },
    { x: 0.76, radius: 0.10, speed: 0.000044, phase: 0.59 },
    { x: 0.21, radius: 0.09, speed: 0.000057, phase: 0.91 },
  ];
  let sourceReady = false;
  let twistSourcePixels;
  let twistOutputPixels;
  let enabled = false;
  let paused = prefersReducedMotion.matches;
  let animationFrame;
  let lastDraw = 0;
  let lastStrengthUpdate = performance.now();

  function analyse() {
    if (!sourceContext || !image.naturalWidth) return;
    const width = Math.min(420, image.naturalWidth);
    const height = Math.max(1, Math.round(width * image.naturalHeight / image.naturalWidth));
    canvas.width = sourceCanvas.width = bubbleCanvas.width = width;
    canvas.height = sourceCanvas.height = bubbleCanvas.height = height;
    sourceContext.clearRect(0, 0, width, height);
    sourceContext.drawImage(image, 0, 0, width, height);
    lastStrengthUpdate = performance.now();
    const twistWidth = Math.min(300, width);
    const twistHeight = Math.max(1, Math.round(twistWidth * height / width));
    twistCanvas.width = twistSourceCanvas.width = twistWidth;
    twistCanvas.height = twistSourceCanvas.height = twistHeight;
    twistSourceContext?.clearRect(0, 0, twistWidth, twistHeight);
    twistSourceContext?.drawImage(image, 0, 0, twistWidth, twistHeight);
    try {
      twistSourcePixels = twistSourceContext?.getImageData(0, 0, twistWidth, twistHeight);
      twistOutputPixels = twistContext?.createImageData(twistWidth, twistHeight);
    } catch (error) {
      twistSourcePixels = undefined;
      twistOutputPixels = undefined;
      console.warn('Haze twist needs a same-origin or CORS-enabled image.', error);
    }
    sourceReady = true;
    draw(performance.now());
  }

  function drawTwist(states, intensity) {
    if (!twistContext || !twistSourcePixels || !twistOutputPixels) return;
    const { width, height } = twistCanvas;
    const scaleX = width / canvas.width;
    const scaleY = height / canvas.height;
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        let sampleX = x;
        let sampleY = y;
        let opacity = 0;
        for (let bubbleIndex = 0; bubbleIndex < states.length; bubbleIndex += 1) {
          const state = states[bubbleIndex];
          const centerX = state.centerX * scaleX;
          const centerY = state.centerY * scaleY;
          const surface = state.radius * Math.min(scaleX, scaleY);
          const dx = x - centerX;
          const dy = y - centerY;
          const distance = Math.hypot(dx, dy);
          const reach = surface * 2.75;
          if (distance < 0.001 || distance >= reach) continue;
          // The angular field is zero at the core, rises linearly to its
          // maximum at the bubble surface, then decays proportionally to 1/r.
          const peakTwist = 0.22 * intensity;
          const vorticity = distance < surface
            ? peakTwist * distance / surface
            : peakTwist * surface / distance;
          const edgeFade = 1 - smoothstep(surface * 2.15, reach, distance);
          const signedVorticity = vorticity * edgeFade * (bubbleIndex % 2 ? -1 : 1);
          sampleX += -dy * signedVorticity;
          sampleY += dx * signedVorticity;
          opacity = Math.max(opacity, signedVorticity === 0 ? 0 : Math.abs(signedVorticity) / (0.22 * intensity) * edgeFade);
        }
        const sourceX = Math.round(mirrorCoordinate(sampleX, width - 1));
        const sourceY = Math.round(mirrorCoordinate(sampleY, height - 1));
        const source = (sourceY * width + sourceX) * 4;
        const target = (y * width + x) * 4;
        twistOutputPixels.data[target] = twistSourcePixels.data[source];
        twistOutputPixels.data[target + 1] = twistSourcePixels.data[source + 1];
        twistOutputPixels.data[target + 2] = twistSourcePixels.data[source + 2];
        twistOutputPixels.data[target + 3] = Math.round(opacity * 178);
      }
    }
    twistContext.putImageData(twistOutputPixels, 0, 0);
  }

  function draw(time) {
    if (!context || !bubbleContext || !sourceReady || !enabled) return;
    const { width, height } = canvas;
    const intensity = normaliseIntensity(frame.style.getPropertyValue('--fx-intensity'));
    const strengthDelta = Math.min(100, Math.max(0, time - lastStrengthUpdate));
    const strengthMomentum = paused ? 0 : 1 - Math.exp(-strengthDelta / 550);
    lastStrengthUpdate = time;
    context.clearRect(0, 0, width, height);
    const states = [];
    for (const bubble of bubbles) {
      // Each bubble rises from below the artwork, crossing it on a repeating
      // triangular (zigzag) course before restarting beneath the bottom edge.
      const progress = paused ? bubble.phase : (bubble.phase + time * bubble.speed) % 1;
      const zigzag = 1 - Math.abs(((progress * 3) % 2) - 1);
      const centerX = width * (bubble.x + (zigzag - 0.5) * 0.32);
      const centerY = height * (1.16 - progress * 1.32);
      const radius = Math.min(width, height) * bubble.radius * (0.82 + intensity * 0.22);
      let lightFactor = 1;
      if (twistSourcePixels) {
        const sampleX = clamp(Math.round(centerX * twistCanvas.width / width), 0, twistCanvas.width - 1);
        const sampleY = clamp(Math.round(centerY * twistCanvas.height / height), 0, twistCanvas.height - 1);
        let luminance = 0;
        let samples = 0;
        // A small local average prevents a single bright edge from causing a
        // strength jump as the bubble passes across it.
        for (let offsetY = -2; offsetY <= 2; offsetY += 1) {
          for (let offsetX = -2; offsetX <= 2; offsetX += 1) {
            const x = clamp(sampleX + offsetX, 0, twistCanvas.width - 1);
            const y = clamp(sampleY + offsetY, 0, twistCanvas.height - 1);
            const pixel = (y * twistCanvas.width + x) * 4;
            luminance += (0.2126 * twistSourcePixels.data[pixel]
              + 0.7152 * twistSourcePixels.data[pixel + 1]
              + 0.0722 * twistSourcePixels.data[pixel + 2]) / 255;
            samples += 1;
          }
        }
        luminance /= samples;
        // Shadows retain a quiet residual, while the effect reaches full
        // energy only once the bubble passes through a lit part of the image.
        const targetLightFactor = 0.16 + 0.84 * smoothstep(0.07, 0.56, luminance);
        bubble.lightFactor = bubble.lightFactor === undefined
          ? targetLightFactor
          : bubble.lightFactor + (targetLightFactor - bubble.lightFactor) * strengthMomentum;
        lightFactor = bubble.lightFactor;
      }
      states.push({ centerX, centerY, radius, lightFactor });
      // Blur first, then use a very wide alpha falloff. This avoids a visible
      // circular edge while preserving the strong, moving refraction inside.
      bubbleContext.clearRect(0, 0, width, height);
      bubbleContext.filter = `blur(${5 + intensity * 10}px)`;
      bubbleContext.drawImage(sourceCanvas, -radius * 0.18, radius * 0.13, width + radius * 0.36, height - radius * 0.26);
      bubbleContext.filter = 'none';
      bubbleContext.globalCompositeOperation = 'destination-in';
      const maskRadius = radius * 1.34;
      const mask = bubbleContext.createRadialGradient(centerX, centerY, 0, centerX, centerY, maskRadius);
      mask.addColorStop(0, 'rgba(255, 255, 255, .68)');
      mask.addColorStop(0.34, 'rgba(255, 255, 255, .52)');
      mask.addColorStop(0.68, 'rgba(255, 255, 255, .14)');
      mask.addColorStop(1, 'rgba(255, 255, 255, 0)');
      bubbleContext.fillStyle = mask;
      bubbleContext.fillRect(0, 0, width, height);
      bubbleContext.globalCompositeOperation = 'source-over';
      context.save();
      context.globalAlpha = (0.18 + intensity * 0.38) * lightFactor;
      context.drawImage(bubbleCanvas, 0, 0);
      context.restore();
      const glow = context.createRadialGradient(centerX, centerY, 0, centerX, centerY, maskRadius);
      glow.addColorStop(0, `rgba(218, 232, 255, ${0.045 + intensity * 0.11})`);
      glow.addColorStop(0.7, `rgba(196, 215, 255, ${0.012 + intensity * 0.04})`);
      glow.addColorStop(1, 'rgba(196, 215, 255, 0)');
      context.fillStyle = glow;
      context.beginPath();
      context.arc(centerX, centerY, maskRadius, 0, Math.PI * 2);
      context.fill();
    }
    drawTwist(states, intensity);
    if (twistOutputPixels) {
      context.save();
      context.globalAlpha = 0.72;
      context.drawImage(twistCanvas, 0, 0, width, height);
      context.restore();
    }
  }

  function animate(time) {
    if (time - lastDraw >= 42) { draw(time); lastDraw = time; }
    if (enabled && !paused) animationFrame = requestAnimationFrame(animate);
  }

  function restart() {
    cancelAnimationFrame(animationFrame);
    if (enabled && !paused) animationFrame = requestAnimationFrame(animate);
  }

  image.addEventListener('load', analyse);
  if (image.complete) analyse();
  return {
    setEnabled(nextEnabled) { enabled = nextEnabled; canvas.hidden = !enabled; if (enabled && !sourceReady) analyse(); draw(performance.now()); restart(); },
    setPaused(nextPaused) { paused = nextPaused || prefersReducedMotion.matches; draw(performance.now()); restart(); },
    destroy() { cancelAnimationFrame(animationFrame); image.removeEventListener('load', analyse); canvas.remove(); },
  };
}

/**
 * Finds connected pale highlight strokes. Each stroke receives a local progress
 * coordinate, so one global phase sends a pulse along every detected line.
 */
function createTraceLayer(frame, image) {
  const canvas = document.createElement('canvas');
  canvas.className = 'image-effect-trace';
  canvas.setAttribute('aria-hidden', 'true');
  frame.appendChild(canvas);
  const mapCanvas = document.createElement('canvas');
  mapCanvas.className = 'image-effect-trace-map';
  mapCanvas.setAttribute('aria-hidden', 'true');
  mapCanvas.hidden = true;
  frame.appendChild(mapCanvas);

  const sourceCanvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  const mapContext = mapCanvas.getContext('2d');
  const sourceContext = sourceCanvas.getContext('2d', { willReadFrequently: true });
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let outputPixels;
  let traceStrength;
  let traceProgress;
  let traceOrientationGroups;
  let enabled = false;
  let paused = prefersReducedMotion.matches;
  let mapVisible = false;
  let animationFrame;
  let lastDraw = 0;

  function analyse() {
    if (!sourceContext || !image.naturalWidth) return;
    const width = Math.min(420, image.naturalWidth);
    const height = Math.max(1, Math.round(width * image.naturalHeight / image.naturalWidth));
    canvas.width = mapCanvas.width = sourceCanvas.width = width;
    canvas.height = mapCanvas.height = sourceCanvas.height = height;
    sourceContext.clearRect(0, 0, width, height);
    sourceContext.drawImage(image, 0, 0, width, height);
    try {
      const pixels = sourceContext.getImageData(0, 0, width, height);
      const candidates = new Uint8Array(width * height);
      const values = new Float32Array(candidates.length);
      const saturations = new Float32Array(candidates.length);
      for (let index = 0; index < candidates.length; index += 1) {
        const pixel = index * 4;
        const maximum = Math.max(pixels.data[pixel], pixels.data[pixel + 1], pixels.data[pixel + 2]) / 255;
        const minimum = Math.min(pixels.data[pixel], pixels.data[pixel + 1], pixels.data[pixel + 2]) / 255;
        values[index] = maximum;
        saturations[index] = maximum > 0.001 ? (maximum - minimum) / maximum : 0;
      }
      const localValues = gaussianBlurValues(values, width, height, 14);
      const relativeBrightness = new Float32Array(candidates.length);
      for (let index = 0; index < candidates.length; index += 1) {
        relativeBrightness[index] = Math.max(0, (values[index] - localValues[index]) / (0.08 + localValues[index]));
      }
      // All three gates adapt to the current image, making a pale line in a
      // low-key scene as eligible as a pale line in a bright illustration.
      const brightCutoff = histogramPercentile(values, 0.72);
      const relativeBrightnessCutoff = Math.max(0.018, histogramPercentile(relativeBrightness, 0.70, 2));
      const saturationCutoff = Math.min(0.46, histogramPercentile(saturations, 0.46) + 0.055);
      for (let index = 0; index < candidates.length; index += 1) {
        candidates[index] = values[index] >= brightCutoff
          && relativeBrightness[index] >= relativeBrightnessCutoff
          && saturations[index] <= saturationCutoff ? 1 : 0;
      }
      traceStrength = new Float32Array(candidates.length);
      traceProgress = new Float32Array(candidates.length);
      traceOrientationGroups = new Int8Array(candidates.length);
      traceOrientationGroups.fill(-1);
      const orientationGroupCount = 4;
      const orientationGroupSizes = new Uint32Array(orientationGroupCount);
      const visited = new Uint8Array(candidates.length);
      let lineCount = 0;
      for (let start = 0; start < candidates.length; start += 1) {
        if (!candidates[start] || visited[start]) continue;
        const component = [];
        const queue = [start];
        visited[start] = 1;
        for (let head = 0; head < queue.length; head += 1) {
          const index = queue[head]; component.push(index);
          const x = index % width; const y = Math.floor(index / width);
          for (let dy = -1; dy <= 1; dy += 1) for (let dx = -1; dx <= 1; dx += 1) {
            const nextX = x + dx; const nextY = y + dy;
            if ((dx || dy) && nextX >= 0 && nextX < width && nextY >= 0 && nextY < height) {
              const next = nextY * width + nextX;
              if (candidates[next] && !visited[next]) { visited[next] = 1; queue.push(next); }
            }
          }
        }
        if (component.length < 14) continue;
        let meanX = 0; let meanY = 0;
        for (const index of component) { meanX += index % width; meanY += Math.floor(index / width); }
        meanX /= component.length; meanY /= component.length;
        let xx = 0; let yy = 0; let xy = 0;
        for (const index of component) {
          const dx = index % width - meanX; const dy = Math.floor(index / width) - meanY;
          xx += dx * dx; yy += dy * dy; xy += dx * dy;
        }
        const angle = 0.5 * Math.atan2(2 * xy, xx - yy);
        const axisX = Math.cos(angle); const axisY = Math.sin(angle);
        const major = Math.max(xx, yy) / component.length;
        const minor = Math.max(0.0001, Math.min(xx, yy) / component.length);
        if (major / minor < 1.55) continue;
        let minimumProjection = Infinity; let maximumProjection = -Infinity;
        for (const index of component) {
          const projection = (index % width - meanX) * axisX + (Math.floor(index / width) - meanY) * axisY;
          minimumProjection = Math.min(minimumProjection, projection); maximumProjection = Math.max(maximumProjection, projection);
        }
        if (maximumProjection - minimumProjection < 7) continue;
        // PCA gives the stroke an axis but not a direction. Recover that
        // direction from the component's aggregate brightness gradient, so
        // travel always heads from the darker end toward the brighter end.
        let meanProjection = 0;
        let meanBrightness = 0;
        for (const index of component) {
          meanProjection += (index % width - meanX) * axisX + (Math.floor(index / width) - meanY) * axisY;
          meanBrightness += values[index];
        }
        meanProjection /= component.length;
        meanBrightness /= component.length;
        let brightnessGradient = 0;
        for (const index of component) {
          const projection = (index % width - meanX) * axisX + (Math.floor(index / width) - meanY) * axisY;
          brightnessGradient += (projection - meanProjection) * (values[index] - meanBrightness);
        }
        const risesWithProjection = brightnessGradient >= 0;
        // Unlike the geometric line axis, a brightness gradient has an
        // actual heading. Keep the full 360° range, so two otherwise parallel
        // regions moving in opposite directions belong to separate families.
        const directedOrientation = ((angle + (risesWithProjection ? 0 : Math.PI)) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2);
        const orientationGroup = clamp(Math.floor(directedOrientation / (Math.PI * 2) * orientationGroupCount), 0, orientationGroupCount - 1);
        for (const index of component) {
          const projection = (index % width - meanX) * axisX + (Math.floor(index / width) - meanY) * axisY;
          const normalizedProgress = (projection - minimumProjection) / (maximumProjection - minimumProjection);
          traceProgress[index] = risesWithProjection ? normalizedProgress : 1 - normalizedProgress;
          traceStrength[index] = 1;
          traceOrientationGroups[index] = orientationGroup;
          orientationGroupSizes[orientationGroup] += 1;
        }
        lineCount += 1;
      }
      outputPixels = context?.createImageData(width, height);
      if (mapContext) {
        const map = mapContext.createImageData(width, height);
        for (let index = 0; index < traceStrength.length; index += 1) {
          const pixel = index * 4;
          if (traceStrength[index]) {
            const group = traceOrientationGroups[index];
            const colours = [[105, 236, 255], [152, 255, 166], [255, 202, 105], [226, 145, 255]];
            map.data[pixel] = colours[group][0]; map.data[pixel + 1] = colours[group][1]; map.data[pixel + 2] = colours[group][2]; map.data[pixel + 3] = 230;
          }
          else { map.data[pixel] = pixels.data[pixel] * 0.12; map.data[pixel + 1] = pixels.data[pixel + 1] * 0.12; map.data[pixel + 2] = pixels.data[pixel + 2] * 0.12; map.data[pixel + 3] = 255; }
        }
        mapContext.putImageData(map, 0, 0);
      }
      canvas.dataset.traceLineCount = String(lineCount);
      canvas.dataset.tracePixels = String(traceStrength.reduce((total, strength) => total + (strength ? 1 : 0), 0));
      canvas.dataset.traceBrightCutoff = brightCutoff.toFixed(3);
      canvas.dataset.traceRelativeBrightnessCutoff = relativeBrightnessCutoff.toFixed(3);
      canvas.dataset.traceSaturationCutoff = saturationCutoff.toFixed(3);
      canvas.dataset.traceOrientationGroups = Array.from(orientationGroupSizes).join(',');
      canvas.dataset.traceBrightnessDirected = 'true';
      canvas.hidden = !enabled; mapCanvas.hidden = !mapVisible;
      draw(performance.now());
    } catch (error) {
      traceStrength = undefined; traceProgress = undefined; traceOrientationGroups = undefined; outputPixels = undefined;
      canvas.hidden = true; mapCanvas.hidden = true;
      console.warn('Light trace needs a same-origin or CORS-enabled image.', error);
    }
  }

  function draw(time) {
    if (!context || !outputPixels || !traceStrength || !enabled) return;
    const intensity = normaliseIntensity(frame.style.getPropertyValue('--fx-intensity'));
    const orientationGroupCount = 4;
    const sweepDuration = 2000;
    const fadeDuration = 260;
    const deadDuration = 1480;
    const orientationSlotDuration = sweepDuration + fadeDuration * 2 + deadDuration;
    const orientationCycleTime = paused ? 0 : time % (orientationSlotDuration * orientationGroupCount);
    const activeOrientationGroup = paused ? 0 : Math.floor(orientationCycleTime / orientationSlotDuration);
    const slotTime = paused ? 0 : orientationCycleTime - activeOrientationGroup * orientationSlotDuration;
    let phase = 0.26;
    let pulseEnvelope = 1;
    if (!paused) {
      if (slotTime < fadeDuration) {
        phase = 0;
        pulseEnvelope = slotTime / fadeDuration;
      } else if (slotTime < fadeDuration + sweepDuration) {
        phase = (slotTime - fadeDuration) / sweepDuration;
      } else if (slotTime < fadeDuration * 2 + sweepDuration) {
        phase = 1;
        pulseEnvelope = 1 - (slotTime - fadeDuration - sweepDuration) / fadeDuration;
      } else {
        // Leave a clear beat of darkness between orientation families.
        phase = 0;
        pulseEnvelope = 0;
      }
    }
    for (let index = 0; index < traceStrength.length; index += 1) {
      const pixel = index * 4;
      const distance = Math.abs(traceProgress[index] - phase);
      // Trace paths are directional for this animation: reaching one end must
      // not wrap the pulse around and flash the path's starting point.
      const pulse = Math.exp(-(distance * distance) / 0.0019);
      outputPixels.data[pixel] = 255; outputPixels.data[pixel + 1] = 253; outputPixels.data[pixel + 2] = 246;
      const orientationIsActive = traceOrientationGroups[index] === activeOrientationGroup;
      outputPixels.data[pixel + 3] = Math.round(traceStrength[index] * pulse * intensity * pulseEnvelope * (orientationIsActive ? 255 : 0));
    }
    context.putImageData(outputPixels, 0, 0);
  }

  function animate(time) { if (time - lastDraw >= 34) { draw(time); lastDraw = time; } if (enabled && !paused) animationFrame = requestAnimationFrame(animate); }
  function restart() { cancelAnimationFrame(animationFrame); if (enabled && !paused) animationFrame = requestAnimationFrame(animate); }
  image.addEventListener('load', analyse);
  if (image.complete) analyse();
  return {
    setEnabled(nextEnabled) { enabled = nextEnabled; canvas.hidden = !enabled; if (enabled && !traceStrength) analyse(); draw(performance.now()); restart(); },
    setPaused(nextPaused) { paused = nextPaused || prefersReducedMotion.matches; draw(performance.now()); restart(); },
    setMapVisible(nextVisible) { mapVisible = nextVisible; mapCanvas.hidden = !mapVisible; },
    destroy() { cancelAnimationFrame(animationFrame); image.removeEventListener('load', analyse); canvas.remove(); mapCanvas.remove(); },
  };
}

function diagonalFor(width, height) {
  return Math.hypot(width, height);
}

/** Attach a non-destructive animated treatment around an existing image element. */
export function attachImageEffect(image, { effect = 'gleam3', intensity = 0.72 } = {}) {
  if (!(image instanceof HTMLImageElement)) {
    throw new TypeError('attachImageEffect expects an HTMLImageElement.');
  }

  let frame = image.closest('.image-effect-frame');
  if (!frame) {
    frame = document.createElement('div');
    frame.className = 'image-effect-frame';
    image.parentNode.insertBefore(frame, image);
    frame.appendChild(image);
  }
  image.classList.add('image-effect-source');
  const luminanceLayer = createLuminanceLayer(frame, image);
  const fourierShadowLayer = createFourierShadowLayer(frame, image);
  const periodicLightLayer = createPeriodicLightLayer(frame, image);
  const starTwinkleLayer = createStarTwinkleLayer(frame, image);
  const gleam3Layer = createGleam3Layer(frame, image);
  const bendyLayer = createBendyLayer(frame, image);
  const hazeLayer = createHazeLayer(frame, image);
  const traceLayer = createTraceLayer(frame, image);

  const apply = (nextEffect = effect, nextIntensity = intensity) => {
    const definition = IMAGE_EFFECTS[nextEffect] || IMAGE_EFFECTS.gleam3;
    // Bendy's direction is meaningful: negative values are its inverse warp.
    // The other effects retain their existing non-negative intensity contract.
    const resolvedIntensity = nextEffect === 'bendy'
      ? normaliseSignedIntensity(nextIntensity)
      : normaliseIntensity(nextIntensity);
    frame.classList.remove(...ALL_EFFECT_CLASSES);
    frame.classList.add(...definition.classes);
    frame.dataset.effect = nextEffect;
    frame.style.setProperty('--fx-intensity', resolvedIntensity);
    luminanceLayer.setEnabled(definition.classes.includes('fx-luminance'));
    fourierShadowLayer.setEnabled(definition.classes.includes('fx-fourier-shadows'));
    periodicLightLayer.setEnabled(definition.classes.includes('fx-periodic-light'));
    starTwinkleLayer.setEnabled(definition.classes.includes('fx-star-twinkle'));
    gleam3Layer.setEnabled(definition.classes.includes('fx-gleam3'));
    bendyLayer.setEnabled(definition.classes.includes('fx-bendy'));
    hazeLayer.setEnabled(definition.classes.includes('fx-haze'));
    traceLayer.setEnabled(definition.classes.includes('fx-trace'));
  };

  apply(effect, intensity);
  return {
    element: frame,
    setEffect(nextEffect) { apply(nextEffect, frame.style.getPropertyValue('--fx-intensity')); },
    setIntensity(nextIntensity) { apply(frame.dataset.effect, nextIntensity); },
    setPaused(isPaused) {
      luminanceLayer.setPaused(isPaused);
      fourierShadowLayer.setPaused(isPaused);
      periodicLightLayer.setPaused(isPaused);
      starTwinkleLayer.setPaused(isPaused);
      gleam3Layer.setPaused(isPaused);
      bendyLayer.setPaused(isPaused);
      hazeLayer.setPaused(isPaused);
      traceLayer.setPaused(isPaused);
    },
    setInverseBlurMapVisible(isVisible) { gleam3Layer.setInverseMapVisible(isVisible); },
    setGleam3SeedMapVisible(isVisible) { gleam3Layer.setSeedMapVisible(isVisible); },
    setBendyLineMapVisible(isVisible) { bendyLayer.setLineMapVisible(isVisible); },
    setTraceMapVisible(isVisible) { traceLayer.setMapVisible(isVisible); },
    destroy() {
      frame.classList.remove(...ALL_EFFECT_CLASSES);
      frame.style.removeProperty('--fx-intensity');
      luminanceLayer.destroy();
      fourierShadowLayer.destroy();
      periodicLightLayer.destroy();
      starTwinkleLayer.destroy();
      gleam3Layer.destroy();
      bendyLayer.destroy();
      hazeLayer.destroy();
      traceLayer.destroy();
    },
  };
}
