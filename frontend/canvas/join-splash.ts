/** Join splash page: join-key resolution, mobile card switcher, and carousels. */

export interface SplideBreakpoints {
  readonly [maxWidth: number]: {
    readonly perPage?: number;
    readonly gap?: string;
  };
}

export interface SplideOptions {
  readonly type?: string;
  readonly perPage?: number;
  readonly perMove?: number;
  readonly gap?: string;
  readonly pagination?: boolean;
  readonly speed?: number;
  readonly breakpoints?: SplideBreakpoints;
}

export interface SplideInstance {
  mount(): void;
}

export type SplideConstructor = new (
  selector: string | HTMLElement,
  options?: SplideOptions,
) => SplideInstance;

interface ResolveJoinKeyResponse {
  readonly theater_id?: string;
  readonly detail?: unknown;
}

export interface JoinSplashController {
  submitJoinKey(): Promise<void>;
  destroy(): void;
}

declare global {
  interface Window {
    Splide?: SplideConstructor;
  }
}

export function normalizeJoinKey(rawValue: string): string {
  const enteredKey = rawValue.trim().toUpperCase();
  return /^[A-Z0-9]{6}$/.test(enteredKey) ? `KEY-${enteredKey}` : enteredKey;
}

async function parseErrorDetail(response: Response): Promise<string | null> {
  const text = await response.text();
  if (!text.trim()) {
    return null;
  }
  try {
    const parsed = JSON.parse(text) as ResolveJoinKeyResponse;
    return typeof parsed.detail === 'string' && parsed.detail ? parsed.detail : null;
  } catch {
    return null;
  }
}

export function initJoinSplash(): JoinSplashController | null {
  const joinInput = document.getElementById('joinKeyInput');
  const errorEl = document.getElementById('errorMessage');
  if (!(joinInput instanceof HTMLInputElement) || !(errorEl instanceof HTMLElement)) {
    return null;
  }

  const inputEl = joinInput;
  const errorBox = errorEl;
  const joinSubmitBtn =
    document.getElementById('join-key-submit') ??
    document.querySelector<HTMLButtonElement>('#join-adventure .input-group .join-btn');

  const joinOptions = document.querySelector('.join-options');
  const joinCards = Array.from(
    document.querySelectorAll<HTMLElement>('.join-options-track > .join-card'),
  );
  const mobileJoinView = window.matchMedia('(max-width: 760px)');
  let showFriendCard = false;

  function updateJoinCards(): void {
    joinOptions?.classList.toggle('show-friends', showFriendCard);
    joinCards.forEach((card, index) => {
      const inactive = mobileJoinView.matches && index !== Number(showFriendCard);
      card.inert = inactive;
      if (inactive) {
        card.setAttribute('aria-hidden', 'true');
      } else {
        card.removeAttribute('aria-hidden');
      }
    });
  }

  const switchButtons = Array.from(document.querySelectorAll<HTMLElement>('[data-join-card]'));
  const switchHandlers: Array<{ button: HTMLElement; handler: () => void }> = [];
  for (const button of switchButtons) {
    const handler = (): void => {
      showFriendCard = button.dataset.joinCard === 'friends';
      updateJoinCards();
      const targetCard = joinCards[Number(showFriendCard)];
      const switchTarget = targetCard?.querySelector<HTMLElement>('.join-card-switch');
      switchTarget?.focus({ preventScroll: true });
    };
    button.addEventListener('click', handler);
    switchHandlers.push({ button, handler });
  }

  mobileJoinView.addEventListener('change', updateJoinCards);
  updateJoinCards();

  const featuresCarouselEl = document.getElementById('featuresCarousel');
  if (featuresCarouselEl && typeof window.Splide === 'function') {
    new window.Splide('#featuresCarousel', {
      type: 'loop',
      perPage: 3,
      perMove: 1,
      gap: '24px',
      pagination: false,
      speed: 450,
      breakpoints: {
        900: { perPage: 2 },
        640: { perPage: 1, gap: '16px' },
      },
    }).mount();
  }

  const carouselSlides = Array.from(document.querySelectorAll<HTMLElement>('.carousel-slide'));
  let activeSlide = 0;
  let initialPreloadTimer: number | null = null;
  let fadeResetTimer: number | null = null;
  let slideInterval: number | null = null;

  function loadSlideImage(slide: HTMLElement | undefined): void {
    if (!slide || slide.dataset.loaded) return;
    const src = slide.dataset.src;
    if (src) {
      slide.style.backgroundImage = `url('${src}')`;
      slide.dataset.loaded = 'true';
    }
  }

  if (carouselSlides[0]) {
    carouselSlides[0].dataset.loaded = 'true';
  }

  function preloadNextSlide(nextIdx: number): void {
    const slide = carouselSlides[nextIdx];
    if (slide && !slide.dataset.loaded && slide.dataset.src) {
      const img = new Image();
      img.onload = () => loadSlideImage(slide);
      img.onerror = () => loadSlideImage(slide);
      img.src = slide.dataset.src;
    }
  }

  if (
    !window.matchMedia('(prefers-reduced-motion: reduce)').matches &&
    carouselSlides.length > 1
  ) {
    initialPreloadTimer = window.setTimeout(() => {
      preloadNextSlide(1);
    }, 1800);

    slideInterval = window.setInterval(() => {
      const outgoingSlide = carouselSlides[activeSlide];
      outgoingSlide.classList.remove('is-active');
      fadeResetTimer = window.setTimeout(() => {
        outgoingSlide.classList.remove('is-panning');
      }, 1800);

      activeSlide = (activeSlide + 1) % carouselSlides.length;
      loadSlideImage(carouselSlides[activeSlide]);
      carouselSlides[activeSlide].classList.add('is-panning', 'is-active');

      const nextSlideIndex = (activeSlide + 1) % carouselSlides.length;
      preloadNextSlide(nextSlideIndex);
    }, 7000);
  }

  async function submitJoinKey(): Promise<void> {
    const key = normalizeJoinKey(inputEl.value);

    errorBox.style.display = 'none';
    errorBox.textContent = '';

    if (!key) {
      errorBox.textContent = 'Please enter a valid Join Key.';
      errorBox.style.display = 'block';
      return;
    }

    try {
      const resp = await fetch('/api/theaters/resolve-join-key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ join_key: key }),
      });

      if (!resp.ok) {
        const detail = await parseErrorDetail(resp);
        errorBox.textContent = detail ?? 'Invalid or expired Join Key.';
        errorBox.style.display = 'block';
        return;
      }

      const data = (await resp.json()) as ResolveJoinKeyResponse;
      if (data.theater_id) {
        window.location.href = `/canvas?theater_id=${encodeURIComponent(data.theater_id)}`;
      } else {
        const detail = typeof data.detail === 'string' && data.detail ? data.detail : null;
        errorBox.textContent = detail ?? 'Invalid or expired Join Key.';
        errorBox.style.display = 'block';
      }
    } catch {
      errorBox.textContent = 'Failed to connect to server. Please try again.';
      errorBox.style.display = 'block';
    }
  }

  const onInputKeyDown = (event: KeyboardEvent): void => {
    if (event.key === 'Enter') {
      void submitJoinKey();
    }
  };
  const onJoinClick = (): void => {
    void submitJoinKey();
  };

  inputEl.addEventListener('keydown', onInputKeyDown);
  joinSubmitBtn?.addEventListener('click', onJoinClick);

  return {
    submitJoinKey,
    destroy(): void {
      inputEl.removeEventListener('keydown', onInputKeyDown);
      joinSubmitBtn?.removeEventListener('click', onJoinClick);
      mobileJoinView.removeEventListener('change', updateJoinCards);
      for (const { button, handler } of switchHandlers) {
        button.removeEventListener('click', handler);
      }
      if (initialPreloadTimer !== null) {
        window.clearTimeout(initialPreloadTimer);
      }
      if (fadeResetTimer !== null) {
        window.clearTimeout(fadeResetTimer);
      }
      if (slideInterval !== null) {
        window.clearInterval(slideInterval);
      }
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initJoinSplash();
  });
} else {
  initJoinSplash();
}
