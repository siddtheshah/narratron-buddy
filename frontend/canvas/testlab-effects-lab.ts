/** Image Effects Lab page controller: artwork selector, effect/intensity controls, and debug map toggles. */

import {
  attachImageEffect,
  IMAGE_EFFECTS,
  type ImageEffectController,
} from './image-effects.js';

export interface ArtworkOption {
  readonly src: string;
  readonly alt: string;
  readonly title: string;
  readonly kind: string;
}

export interface EffectsLabController {
  apply(): void;
  destroy(): void;
}

const ARTWORKS: Readonly<Record<string, ArtworkOption>> = {
  vortex: {
    src: '/carousel/spacetime%20vortex.webp',
    alt: 'Spacetime vortex preview',
    title: 'Spacetime vortex',
    kind: 'space',
  },
  knightSword: {
    src: '/test-images/trace-knight-sword.png',
    alt: 'Armored knight holding a bright sword',
    title: 'Trace knight and sword',
    kind: 'trace target: sword blade',
  },
  energyStreams: {
    src: '/test-images/trace-energy-streams.png',
    alt: 'Mage charging a glowing energy attack surrounded by streamlines',
    title: 'Trace energy streamlines',
    kind: 'trace stress test: converging streams',
  },
  pirate: {
    src: '/carousel/paint%20pirate.webp',
    alt: 'Paint pirate preview',
    title: 'Paint pirate',
    kind: 'character',
  },
  river: {
    src: '/carousel/glow%20river.webp',
    alt: 'Glow river preview',
    title: 'Glow river',
    kind: 'landscape',
  },
  map: {
    src: '/carousel/candle%20map.webp',
    alt: 'Candle map detail preview',
    title: 'Candle map',
    kind: 'detail',
  },
  nightLake: {
    src: '/carousel/sparse-night-lake.webp',
    alt: 'Sparse night lake preview',
    title: 'Sparse night lake',
    kind: 'sparse highlights',
  },
  sunrise: {
    src: '/carousel/diffuse-sunrise.png',
    alt: 'Diffuse sunrise preview',
    title: 'Diffuse sunrise',
    kind: 'diffuse light',
  },
  obsidian: {
    src: '/carousel/obsidian-beams.png',
    alt: 'Obsidian beams preview',
    title: 'Obsidian beams',
    kind: 'hard highlights',
  },
  astrolabe: {
    src: '/carousel/brass-astrolabe.webp',
    alt: 'Brass astrolabe preview',
    title: 'Brass astrolabe',
    kind: 'dense specular detail',
  },
};

/** Wires the Image Effects Lab controls and attaches the image effect controller. */
export function initEffectsLab(): EffectsLabController | null {
  const featuredImage = document.querySelector<HTMLImageElement>('#featured-image');
  const artwork = document.querySelector<HTMLSelectElement>('#artwork');
  const effect = document.querySelector<HTMLSelectElement>('#effect');
  const intensity = document.querySelector<HTMLInputElement>('#intensity');
  const intensityValue = document.querySelector<HTMLOutputElement>('#intensity-value');
  const status = document.querySelector<HTMLElement>('#status');
  const pause = document.querySelector<HTMLButtonElement>('#pause');
  const gleam3SeedMap = document.querySelector<HTMLButtonElement>('#gleam3-seed-map');
  const inverseBlurMap = document.querySelector<HTMLButtonElement>('#inverse-blur-map');
  const traceMap = document.querySelector<HTMLButtonElement>('#trace-map');
  const artworkTitle = document.querySelector<HTMLElement>('#artwork-title');
  const artworkKind = document.querySelector<HTMLElement>('#artwork-kind');

  if (
    !featuredImage ||
    !artwork ||
    !effect ||
    !intensity ||
    !intensityValue ||
    !status ||
    !pause ||
    !gleam3SeedMap ||
    !inverseBlurMap ||
    !traceMap ||
    !artworkTitle ||
    !artworkKind
  ) {
    return null;
  }

  const controllers: readonly ImageEffectController[] = [
    attachImageEffect(featuredImage, { effect: 'gleam3', intensity: 0.75 }),
  ];

  const apply = (): void => {
    const effectKey = effect.value;
    const effectIntensity = Math.abs(Number(intensity.value) / 100);
    controllers.forEach((controller: ImageEffectController): void => {
      controller.setEffect(effectKey);
      controller.setIntensity(effectIntensity);
    });
    intensityValue.value = intensity.value;
    intensityValue.textContent = intensity.value;
    const label = IMAGE_EFFECTS[effectKey]?.label || effectKey;
    status.textContent = `${label}: ${intensity.value}`;
  };

  const syncPreviewAspectRatio = (): void => {
    if (featuredImage.naturalWidth && featuredImage.naturalHeight) {
      controllers[0].element.style.aspectRatio = `${featuredImage.naturalWidth} / ${featuredImage.naturalHeight}`;
    }
  };

  const onEffectChange = (): void => {
    apply();
  };

  const onArtworkChange = (): void => {
    const next = ARTWORKS[artwork.value];
    if (!next) {
      return;
    }
    featuredImage.src = next.src;
    featuredImage.alt = next.alt;
    artworkTitle.textContent = next.title;
    artworkKind.textContent = next.kind;
  };

  const onPauseClick = (): void => {
    const frozen = document.body.classList.toggle('motion-paused');
    controllers.forEach((controller: ImageEffectController): void => controller.setPaused(frozen));
    pause.textContent = frozen ? 'Resume motion' : 'Pause motion';
  };

  const onGleam3SeedMapClick = (): void => {
    const visible = gleam3SeedMap.getAttribute('aria-pressed') !== 'true';
    controllers.forEach((controller: ImageEffectController): void =>
      controller.setGleam3SeedMapVisible(visible),
    );
    gleam3SeedMap.setAttribute('aria-pressed', String(visible));
    gleam3SeedMap.textContent = visible
      ? 'Hide Gleam 3 flow seeds'
      : 'Show Gleam 3 flow seeds';
  };

  const onInverseBlurMapClick = (): void => {
    const visible = inverseBlurMap.getAttribute('aria-pressed') !== 'true';
    controllers.forEach((controller: ImageEffectController): void =>
      controller.setInverseBlurMapVisible(visible),
    );
    inverseBlurMap.setAttribute('aria-pressed', String(visible));
    inverseBlurMap.textContent = visible
      ? 'Hide V inverse-Gaussian map'
      : 'Show V inverse-Gaussian map';
  };

  const onTraceMapClick = (): void => {
    const visible = traceMap.getAttribute('aria-pressed') !== 'true';
    controllers.forEach((controller: ImageEffectController): void =>
      controller.setTraceMapVisible(visible),
    );
    traceMap.setAttribute('aria-pressed', String(visible));
    traceMap.textContent = visible ? 'Hide Light trace pixels' : 'Show Light trace pixels';
  };

  effect.addEventListener('change', onEffectChange);
  intensity.addEventListener('input', apply);
  featuredImage.addEventListener('load', syncPreviewAspectRatio);
  if (featuredImage.complete) {
    syncPreviewAspectRatio();
  }
  artwork.addEventListener('change', onArtworkChange);
  pause.addEventListener('click', onPauseClick);
  gleam3SeedMap.addEventListener('click', onGleam3SeedMapClick);
  inverseBlurMap.addEventListener('click', onInverseBlurMapClick);
  traceMap.addEventListener('click', onTraceMapClick);

  return {
    apply,
    destroy(): void {
      effect.removeEventListener('change', onEffectChange);
      intensity.removeEventListener('input', apply);
      featuredImage.removeEventListener('load', syncPreviewAspectRatio);
      artwork.removeEventListener('change', onArtworkChange);
      pause.removeEventListener('click', onPauseClick);
      gleam3SeedMap.removeEventListener('click', onGleam3SeedMapClick);
      inverseBlurMap.removeEventListener('click', onInverseBlurMapClick);
      traceMap.removeEventListener('click', onTraceMapClick);
      controllers.forEach((controller: ImageEffectController): void => controller.destroy());
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', (): void => {
    initEffectsLab();
  });
} else {
  initEffectsLab();
}
