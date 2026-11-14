import { expect, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';
import type { Page } from '@playwright/test';
import type { ImageRenderer } from './image-renderer.js';

type RendererModule = typeof import('./image-renderer.js');

declare global {
  interface Window {
    imageRenderer: ImageRenderer;
    frameUrls: string[];
    pendingVideo: Promise<void>;
    videoPlays: number;
    setRendererEffects(enabled: boolean): void;
    requestedFrames: number;
  }
}

async function mount(canvas: CanvasHarness, page: Page): Promise<void> {
  await canvas.mount({
    html: '<div id="scene"><canvas></canvas><img><div id="background"></div></div>',
    inlineStyles: ['#scene {position:relative;width:200px;height:100px} canvas {width:100%;height:100%}'],
    stylesheets: ['/static/css/image-effects.css'],
  });
  await page.clock.install();
  await page.evaluate(async () => {
    const { createImageRenderer } = await window.__importModule<RendererModule>('/static/generated/canvas/image-renderer.js');
    const bitmap = document.createElement('canvas');
    bitmap.width = 20;
    bitmap.height = 10;
    const context = bitmap.getContext('2d')!;
    window.frameUrls = ['red', 'green', 'blue'].map(color => {
      context.fillStyle = color;
      context.fillRect(0, 0, 20, 10);
      return bitmap.toDataURL();
    });
    let effectsEnabled = true;
    window.imageRenderer = createImageRenderer({
      canvas: document.querySelector('canvas'), image: document.querySelector('img'),
      backgroundLayer: document.getElementById('background'),
      imageEffectsEnabled: () => effectsEnabled,
    });
    window.setRendererEffects = (enabled) => {
      effectsEnabled = enabled;
      window.imageRenderer.setImageEffectsEnabled(enabled);
    };
    window.imageRenderer.resize();
  });
}

test('CDN policy accepts same-origin and trusted subdomains', async ({ canvas, page }) => {
  await canvas.mount();
  expect(await page.evaluate(async () => {
    const module = await window.__importModule<RendererModule>('/static/generated/canvas/image-renderer.js');
    return {
      accepted: ['/local.png', 'https://canvas.test/a', 'https://fal.media/a', 'https://v3.fal.media/a', 'https://fal.ai/a']
        .map(module.isWhitelistedCdn),
      rejected: ['', 'http://[', 'https://evilfal.media/a', 'https://fal.media.evil.test/a', 'https://other.test/a']
        .map(module.isWhitelistedCdn),
    };
  })).toEqual({ accepted: [true, true, true, true, true], rejected: [false, false, false, false, false] });
});

test('sequences hide stale DOM layers, advance frames, stop, and restore single images', async ({ canvas, page }) => {
  await mount(canvas, page);
  await page.evaluate(async () => {
    await window.imageRenderer.applyTransition(window.frameUrls[0], 'none', 'none');
    await window.imageRenderer.playSequence(window.frameUrls, { frameDuration: 100, crossfadeDuration: 30, transitionDuration: 0 });
  });
  await expect(page.locator('img').first()).toHaveCSS('opacity', '0');
  await expect(page.locator('#background')).toHaveCSS('background-image', 'none');
  await page.clock.runFor(240);
  expect(await page.evaluate(() => {
    const pixels = document.querySelector('canvas')!.getContext('2d')!.getImageData(100, 50, 1, 1).data;
    return Array.from(pixels);
  })).toEqual([0, 0, 255, 255]);
  await page.evaluate(() => window.imageRenderer.stopSequence());
  await page.clock.runFor(500);
  expect(await page.evaluate(() => Array.from(document.querySelector('canvas')!.getContext('2d')!.getImageData(100, 50, 1, 1).data)))
    .toEqual([0, 0, 255, 255]);
  await page.evaluate(async () => {
    await window.imageRenderer.applyTransition(window.frameUrls[0], 'none', 'none');
  });
  await expect(page.locator('#background')).not.toHaveCSS('background-image', 'none');
  await expect(page.locator('img').first()).toHaveClass(/loaded/);
  expect(await page.evaluate(() => Array.from(document.querySelector('canvas')!.getContext('2d')!.getImageData(100, 50, 1, 1).data)))
    .toEqual([255, 0, 0, 255]);
});

test('layered animation composites in order and stops and resumes its effect loop', async ({ canvas, page }) => {
  await mount(canvas, page);
  await page.evaluate(async () => {
    window.requestedFrames = 0;
    const requestFrame = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = callback => {
      window.requestedFrames++;
      return requestFrame(callback);
    };
    await window.imageRenderer.playLayeredAnimation([
      { url: window.frameUrls[2], effect: 'twist', order: 2 },
      { url: window.frameUrls[0], effect: 'none', order: 1 },
    ], { crossfadeDuration: 0 });
  });
  await page.clock.runFor(32);
  await expect(page.locator('img').first()).toHaveCSS('opacity', '0');
  await expect(page.locator('#background')).toHaveCSS('background-image', 'none');
  await page.evaluate(() => window.setRendererEffects(false));
  await page.clock.runFor(32);
  expect(await page.evaluate(() => Array.from(document.querySelector('canvas')!.getContext('2d')!.getImageData(100, 50, 1, 1).data)))
    .toEqual([0, 0, 255, 255]);
  const stoppedFrames = await page.evaluate(() => window.requestedFrames);
  await page.clock.runFor(100);
  expect(await page.evaluate(() => window.requestedFrames)).toBe(stoppedFrames);
  await page.evaluate(() => window.setRendererEffects(true));
  await expect.poll(async () => {
    await page.clock.runFor(32);
    return page.evaluate(() => window.requestedFrames);
  }).toBeGreaterThan(stoppedFrames);
  await page.evaluate(() => window.imageRenderer.stopSequence());
});

async function mockVideoDecoder(page: Page): Promise<void> {
  await page.evaluate(() => {
    // Keep media loading local and deterministic; renderer event handling remains real.
    Object.defineProperty(HTMLMediaElement.prototype, 'src', {
      configurable: true,
      get() { return this.getAttribute('data-test-src') || ''; },
      set(url: string) { this.setAttribute('data-test-src', url); },
    });
    window.videoPlays = 0;
    HTMLMediaElement.prototype.play = async function (): Promise<void> { window.videoPlays++; };
    HTMLMediaElement.prototype.pause = function (): void {};
    HTMLMediaElement.prototype.load = function (): void {};
  });
}

test('videos stay silent, fall back locally, restart on ended, and detach on stop', async ({ canvas, page }) => {
  await mount(canvas, page);
  await mockVideoDecoder(page);
  await page.evaluate(() => {
    window.pendingVideo = window.imageRenderer.playVideoAnimation('https://fal.media/video.mp4', {
      local_video_url: '/local.mp4', video_duration_seconds: 5,
    });
  });
  await expect(page.locator('#scene video')).toHaveCount(1);
  expect(await page.locator('video').evaluate((video: HTMLVideoElement) => ({
    muted: video.muted, defaultMuted: video.defaultMuted, volume: video.volume,
    loop: video.loop, autoplay: video.autoplay, playsInline: video.playsInline, crossOrigin: video.crossOrigin,
    mutedAttribute: video.hasAttribute('muted'),
  }))).toEqual({ muted: true, defaultMuted: true, volume: 0, loop: true, autoplay: true, playsInline: true, crossOrigin: 'anonymous', mutedAttribute: true });
  await page.locator('video').dispatchEvent('error');
  await expect(page.locator('video')).toHaveAttribute('data-test-src', '/local.mp4');
  await page.locator('video').dispatchEvent('loadeddata');
  await page.evaluate(() => window.pendingVideo);
  expect(await page.evaluate(() => window.videoPlays)).toBe(1);
  await page.locator('video').evaluate((video: HTMLVideoElement) => {
    video.muted = false;
    video.volume = .5;
    video.dispatchEvent(new Event('volumechange'));
    video.currentTime = 4;
    video.dispatchEvent(new Event('ended'));
  });
  expect(await page.locator('video').evaluate((video: HTMLVideoElement) => ({
    muted: video.muted, volume: video.volume, time: video.currentTime, plays: window.videoPlays,
  }))).toEqual({ muted: true, volume: 0, time: 0, plays: 2 });
  await page.evaluate(() => window.imageRenderer.stopSequence());
  await expect(page.locator('video')).toHaveCount(0);
});

test('stopping a video while it loads prevents late playback', async ({ canvas, page }) => {
  await mount(canvas, page);
  await mockVideoDecoder(page);
  await page.evaluate(async () => {
    window.pendingVideo = window.imageRenderer.playVideoAnimation('/video.mp4');
    const video = document.querySelector('video')!;
    window.imageRenderer.stopSequence();
    video.dispatchEvent(new Event('loadeddata'));
    await window.pendingVideo;
  });
  expect(await page.evaluate(() => window.videoPlays)).toBe(0);
  await expect(page.locator('video')).toHaveCount(0);
});
