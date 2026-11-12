import { expect, test } from '../../testing/ui/canvas_fixtures';
import type { AnimationRenderer } from './media.js';

type MediaModule = typeof import('./media.js');

test('legacy media shim exports the canonical typed functions', async ({ canvas, page }) => {
  await canvas.mount();
  expect(await page.evaluate(async () => {
    const canonical = await window.__importModule<MediaModule>('/static/generated/canvas/media.js');
    const shim = await window.__importModule<MediaModule>('/static/js/canvas-media.js');
    return {
      signature: canonical.getAnimationSignature === shim.getAnimationSignature,
      playback: canonical.playCanvasAnimation === shim.playCanvasAnimation,
    };
  })).toEqual({ signature: true, playback: true });
});

test('animation signatures track frame order, layer URL/effect, and video fallbacks', async ({ canvas, page }) => {
  await canvas.mount();
  expect(await page.evaluate(async () => {
    const { getAnimationSignature: signature } = await window.__importModule<MediaModule>('/static/generated/canvas/media.js');
    return [
      signature(null), signature(undefined),
      signature({ type: 'triframe', frames: ['a', 'b', 'c'] }),
      signature({ type: 'triframe', frames: ['c', 'b', 'a'] }),
      signature({ type: 'layered', layers: [{ url: 'a', effect: 'twist' }, { url: 'b', effect: 'pulse' }] }),
      signature({ type: 'video', video_url: 'cdn', url: 'alternate', id: 'id' }),
      signature({ type: 'video', video_url: '', url: 'alternate', id: 'id' }),
      signature({ type: 'video', video_url: '', id: 'id' }),
      signature({ type: 'video', video_url: '' }),
    ];
  })).toEqual(['', '', 'a|b|c', 'c|b|a', 'a:twist|b:pulse', 'cdn', 'alternate', 'id', '']);
});

test('animation dispatch resolves URLs, preserves options, and leaves input untouched', async ({ canvas, page }) => {
  await canvas.mount();
  expect(await page.evaluate(async () => {
    const { playCanvasAnimation } = await window.__importModule<MediaModule>('/static/generated/canvas/media.js');
    const calls: unknown[] = [];
    const renderer: AnimationRenderer = {
      async playSequence(urls, options) { calls.push({ kind: 'sequence', urls, options }); },
      async playLayeredAnimation(layers) { calls.push({ kind: 'layered', layers }); },
      async playVideoAnimation(url, options) { calls.push({ kind: 'video', url, options }); },
    };
    const resolve = (url: string): string => `${url}?join_key=test`;
    const layers = [{ url: '/piece.png', effect: 'twist', order: 2, opacity: .5, speed: 3 }];
    playCanvasAnimation(renderer, { type: 'layered', layers }, resolve);
    playCanvasAnimation(renderer, { type: 'triframe', frames: ['a', 'b'], frame_duration_ms: 900, crossfade_duration_ms: 200 }, resolve);
    playCanvasAnimation(renderer, { type: 'triframe', frames: ['a', 'b'], frame_duration_ms: 0, crossfade_duration_ms: 0 });
    playCanvasAnimation(renderer, { type: 'video', video_url: 'cdn.mp4', loop: false, video_duration_seconds: 7, local_video_url: '/local.mp4' }, resolve);
    playCanvasAnimation(renderer, { type: 'video', video_url: '', url: '/alternate.mp4' }, resolve);
    return { calls, layers };
  })).toEqual({
    calls: [
      { kind: 'layered', layers: [{ url: '/piece.png?join_key=test', effect: 'twist', order: 2, opacity: .5, speed: 3 }] },
      { kind: 'sequence', urls: ['a?join_key=test', 'b?join_key=test'], options: { frameDuration: 900, crossfadeDuration: 200 } },
      { kind: 'sequence', urls: ['a', 'b'], options: { frameDuration: 1400, crossfadeDuration: 500 } },
      { kind: 'video', url: 'cdn.mp4?join_key=test', options: { type: 'video', video_url: 'cdn.mp4', loop: false, video_duration_seconds: 7, local_video_url: '/local.mp4' } },
      { kind: 'video', url: '/alternate.mp4?join_key=test', options: { type: 'video', video_url: '', url: '/alternate.mp4' } },
    ],
    layers: [{ url: '/piece.png', effect: 'twist', order: 2, opacity: .5, speed: 3 }],
  });
});
