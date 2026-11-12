import type { AnimationLayer, AnimationOptions, ImageRenderer } from './image-renderer.js';

export interface TriframeAnimation {
  type: 'triframe';
  frames: string[];
  frame_duration_ms?: number;
  crossfade_duration_ms?: number;
}

export interface LayeredAnimation {
  type: 'layered';
  layers: AnimationLayer[];
}

export interface VideoAnimation extends AnimationOptions {
  type: 'video';
  video_url: string;
  url?: string;
  id?: string | null;
  scene_prompt?: string;
  poster_url?: string | null;
  muted?: boolean;
}

export type CanvasAnimation = TriframeAnimation | LayeredAnimation | VideoAnimation;
export type AnimationRenderer = Pick<ImageRenderer, 'playLayeredAnimation' | 'playVideoAnimation' | 'playSequence'>;

/** Keep animation selection and URL handling consistent across canvas viewers. */
export function getAnimationSignature(animation: CanvasAnimation | null | undefined): string {
  if (!animation) return '';
  if (animation.type === 'triframe' && Array.isArray(animation.frames)) return animation.frames.join('|');
  if (animation.type === 'layered' && Array.isArray(animation.layers)) return animation.layers.map(layer => `${layer.url}:${layer.effect}`).join('|');
  if (animation.type === 'video') return animation.video_url || animation.url || animation.id || '';
  return '';
}

export function playCanvasAnimation(
  renderer: AnimationRenderer,
  animation: CanvasAnimation,
  resolveUrl: (url: string) => string = url => url,
): void {
  if (animation.type === 'layered') {
    renderer.playLayeredAnimation(animation.layers.map(layer => ({ ...layer, url: resolveUrl(layer.url) })));
  } else if (animation.type === 'video') {
    renderer.playVideoAnimation(resolveUrl(animation.video_url || animation.url || ''), animation);
  } else if (animation.type === 'triframe') {
    renderer.playSequence(animation.frames.map(resolveUrl), {
      frameDuration: animation.frame_duration_ms || 1400,
      crossfadeDuration: animation.crossfade_duration_ms || 500,
    });
  }
}
