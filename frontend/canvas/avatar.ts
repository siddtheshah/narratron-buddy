import { AnimationMixer, Color, MathUtils, Mesh, MeshStandardMaterial, PointLight } from 'three';
import { createAvatarScene } from '../shared/avatar-scene.js';
import { loadAvatarShaders, type AvatarShaderController, type AvatarShaderSpec } from './avatar-shaders.js';

interface AvatarManifest {
  readonly title: string;
  readonly model: 'model.glb';
  readonly poster?: 'preview.webp';
  readonly idle: string;
  readonly actions: readonly {
    readonly id: string;
    readonly label?: string;
    readonly clip?: string;
    readonly lightColor?: string;
  }[];
  readonly renderScale?: number;
  readonly fillLight?: number;
  readonly hoverGlow?: {
    readonly material: string;
    readonly intensity: number;
    readonly light?: { readonly name: string; readonly intensity: number };
  };
  readonly shaders?: readonly AvatarShaderSpec[];
}

interface AvatarColorAction {
  readonly id: string;
  readonly label: string;
  readonly color: string;
}

interface AvatarPlayback {
  readonly idle: string;
  readonly colors: readonly AvatarColorAction[];
  selectColor(actionId: string): boolean;
}

function isMinimalModeActive(): boolean {
  return document.documentElement.classList.contains('minimal-mode');
}

export async function mountAvatar(mount: HTMLElement, packagePath: string): Promise<AvatarPlayback | null> {
  const packageUrl = new URL(packagePath, window.location.href);
  const response = await fetch(new URL('avatar.json', packageUrl));
  if (!response.ok) throw new Error(`Avatar manifest returned ${response.status}`);
  const manifest: AvatarManifest = await response.json();
  if (manifest.model !== 'model.glb') throw new Error('Expected an avatar model.glb.');
  mount.setAttribute('role', 'img');
  mount.setAttribute('aria-label', manifest.title);
  if (typeof manifest.renderScale === 'number' && Number.isFinite(manifest.renderScale)) {
    mount.style.setProperty('--canvas-avatar-scale', String(MathUtils.clamp(manifest.renderScale, 0.5, 1.5)));
  }

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
    return null;
  }

  const fillLight = typeof manifest.fillLight === 'number' && Number.isFinite(manifest.fillLight)
    ? MathUtils.clamp(manifest.fillLight, 0, 1) : 1;
  const view = await createAvatarScene(canvas, context, new URL(manifest.model, packageUrl), fillLight).catch(() => null);
  if (!view) {
    showPoster();
    return null;
  }
  const { model, animations, renderer, render } = view;
  const idle = manifest.actions.find((action) => action.id === manifest.idle);
  const clip = animations.find((animation) => animation.name === idle?.clip);
  const mixer = new AnimationMixer(model);
  if (clip) mixer.clipAction(clip).play();

  const glow = manifest.hoverGlow;
  const lightSetting = glow?.light;
  const lightNode = lightSetting ? model.getObjectByName(lightSetting.name) : undefined;
  const lightGlow = lightNode instanceof PointLight && lightSetting && Number.isFinite(lightSetting.intensity)
    ? { light: lightNode, rest: lightNode.intensity, peak: Math.max(0, lightSetting.intensity) }
    : null;
  const materials = new Map<MeshStandardMaterial, number>();
  if (glow && Number.isFinite(glow.intensity)) {
    model.traverse((node) => {
      if (node instanceof Mesh && node.material instanceof MeshStandardMaterial && node.material.name === glow.material) {
        materials.set(node.material, node.material.emissiveIntensity);
      }
    });
  }

  const shaderSpecs = Array.isArray(manifest.shaders) ? manifest.shaders : [];
  const supportsColor = Boolean(lightGlow) || shaderSpecs.length > 0;
  const colors = supportsColor ? manifest.actions.flatMap(({ id, label, lightColor }) => (
    typeof lightColor === 'string' && /^#[\da-f]{6}$/i.test(lightColor)
      ? [{ id, label: label ?? id, color: lightColor }]
      : []
  )) : [];
  const activeColor = lightGlow ? lightGlow.light.color : new Color(0xffffff);
  const targetColor = supportsColor ? activeColor.clone() : undefined;
  const idleColor = colors.find((action) => action.id === manifest.idle);
  if (idleColor && targetColor) {
    targetColor.set(idleColor.color);
    activeColor.copy(targetColor);
  }

  let shaderController: AvatarShaderController | null = null;
  let shaderLoadStarted = false;
  let ready = true;
  let brightness = 0;
  let elapsedShaderTime = 0;

  async function ensureShaders(): Promise<void> {
    if (shaderLoadStarted || shaderSpecs.length === 0) return;
    shaderLoadStarted = true;
    try {
      const controller = await loadAvatarShaders(model, packageUrl, shaderSpecs, activeColor);
      if (!ready) {
        controller.dispose();
        return;
      }
      shaderController = controller;
      const enabled = !isMinimalModeActive();
      controller.setEnabled(enabled);
      if (enabled) {
        controller.update(elapsedShaderTime, brightness, canvas.width, canvas.height);
      }
    } catch (error) {
      shaderLoadStarted = false;
      console.warn('Could not load avatar shaders', error);
    }
  }

  if (!isMinimalModeActive() && shaderSpecs.length > 0) {
    await ensureShaders();
  }

  mount.append(canvas);
  mount.hidden = false;
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let lastTime = 0;
  let hovered = false;
  let focused = false;

  function update(time: number): void {
    const delta = Math.min((time - (lastTime || time)) / 1000, 0.1);
    lastTime = time;
    if (!reducedMotion.matches) {
      mixer.update(delta);
      elapsedShaderTime += delta;
    }
    const target = Number(hovered || focused);
    brightness = reducedMotion.matches ? target : MathUtils.damp(brightness, target, 10, delta);
    if (glow) {
      for (const [material, restingIntensity] of materials) {
        material.emissiveIntensity = MathUtils.lerp(restingIntensity, glow.intensity, brightness);
      }
      if (lightGlow) {
        lightGlow.light.intensity = MathUtils.lerp(lightGlow.rest, lightGlow.peak, brightness);
      }
    }
    if (targetColor && colors.length > 0) {
      activeColor.lerp(targetColor, reducedMotion.matches ? 1 : MathUtils.damp(0, 1, 8, delta));
      for (const material of materials.keys()) material.emissive.copy(activeColor);
    }
    if (shaderController && !isMinimalModeActive()) {
      shaderController.update(elapsedShaderTime, brightness, canvas.width, canvas.height);
    }
    render();
  }

  if (materials.size > 0 || lightGlow || shaderSpecs.length > 0) {
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
    if (shaderController && !isMinimalModeActive()) {
      shaderController.update(elapsedShaderTime, brightness, canvas.width, canvas.height);
    }
    render();
  }

  const observer = new ResizeObserver(resize);
  observer.observe(mount);
  const minimalObserver = new MutationObserver(() => {
    const minimal = isMinimalModeActive();
    if (!minimal && !shaderController && !shaderLoadStarted && shaderSpecs.length > 0) {
      void ensureShaders().then(() => { if (ready) render(); });
      return;
    }
    shaderController?.setEnabled(!minimal);
    if (!minimal && shaderController) {
      shaderController.update(elapsedShaderTime, brightness, canvas.width, canvas.height);
    }
    render();
  });
  minimalObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

  canvas.addEventListener('webglcontextlost', () => {
    ready = false;
    observer.disconnect();
    minimalObserver.disconnect();
    shaderController?.dispose();
    shaderController = null;
    renderer.setAnimationLoop(null);
    showPoster();
  }, { once: true });
  resize();
  renderer.setAnimationLoop(update);
  return {
    idle: manifest.idle,
    colors,
    selectColor(actionId) {
      const action = colors.find(({ id }) => id === actionId);
      if (!ready || !action || !targetColor) return false;
      targetColor.set(action.color);
      return true;
    },
  };
}
