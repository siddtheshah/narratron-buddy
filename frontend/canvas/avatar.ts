import { AnimationMixer, MathUtils, Mesh, MeshStandardMaterial } from 'three';
import { createAvatarScene } from '../shared/avatar-scene.js';

interface AvatarManifest {
  readonly title: string;
  readonly model: 'model.glb';
  readonly poster?: 'preview.webp';
  readonly idle: string;
  readonly actions: readonly { readonly id: string; readonly clip: string }[];
  readonly hoverGlow?: { readonly material: string; readonly intensity: number };
}

export async function mountAvatar(mount: HTMLElement, packagePath: string): Promise<void> {
  const packageUrl = new URL(packagePath, window.location.href);
  const response = await fetch(new URL('avatar.json', packageUrl));
  if (!response.ok) throw new Error(`Avatar manifest returned ${response.status}`);
  const manifest: AvatarManifest = await response.json();
  if (manifest.model !== 'model.glb') throw new Error('Expected an avatar model.glb.');
  mount.setAttribute('role', 'img');
  mount.setAttribute('aria-label', manifest.title);

  function showPoster(): void {
    mount.tabIndex = -1;
    mount.replaceChildren();
    if (manifest.poster !== 'preview.webp') {
      mount.hidden = true;
      return;
    }
    const poster = document.createElement('img');
    poster.alt = '';
    poster.src = new URL(manifest.poster, packageUrl).href;
    poster.addEventListener('load', () => { mount.hidden = false; }, { once: true });
    mount.append(poster);
  }

  const canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true');
  const context = canvas.getContext('webgl2', { alpha: true, antialias: true });
  if (!context) {
    showPoster();
    return;
  }

  const view = await createAvatarScene(canvas, context, new URL(manifest.model, packageUrl)).catch(() => null);
  if (!view) {
    showPoster();
    return;
  }
  const { model, animations, renderer, render } = view;
  const idle = manifest.actions.find((action) => action.id === manifest.idle);
  const clip = animations.find((animation) => animation.name === idle?.clip);
  const mixer = new AnimationMixer(model);
  if (clip) mixer.clipAction(clip).play();

  const glow = manifest.hoverGlow;
  const materials = new Map<MeshStandardMaterial, number>();
  if (glow && Number.isFinite(glow.intensity)) {
    model.traverse((node) => {
      if (node instanceof Mesh && node.material instanceof MeshStandardMaterial && node.material.name === glow.material) {
        materials.set(node.material, node.material.emissiveIntensity);
      }
    });
  }

  mount.append(canvas);
  mount.hidden = false;
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let lastTime = 0;
  let brightness = 0;
  let hovered = false;
  let focused = false;

  function update(time: number): void {
    const delta = Math.min((time - (lastTime || time)) / 1000, 0.1);
    lastTime = time;
    if (!reducedMotion.matches) mixer.update(delta);
    if (glow) {
      const target = Number(hovered || focused);
      brightness = reducedMotion.matches ? target : MathUtils.damp(brightness, target, 10, delta);
      for (const [material, restingIntensity] of materials) {
        material.emissiveIntensity = MathUtils.lerp(restingIntensity, glow.intensity, brightness);
      }
    }
    render();
  }

  if (materials.size > 0) {
    mount.tabIndex = 0;
    mount.addEventListener('pointerenter', (event) => { hovered = event.pointerType !== 'touch'; });
    mount.addEventListener('pointerleave', () => { hovered = false; });
    mount.addEventListener('focus', () => { focused = true; });
    mount.addEventListener('blur', () => { focused = false; });
  }

  function resize(): void {
    const size = mount.clientWidth;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(size, size, false);
    render();
  }

  const observer = new ResizeObserver(resize);
  observer.observe(mount);
  canvas.addEventListener('webglcontextlost', () => {
    observer.disconnect();
    renderer.setAnimationLoop(null);
    showPoster();
  }, { once: true });
  resize();
  renderer.setAnimationLoop(update);
}
