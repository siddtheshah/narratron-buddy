import {
  AnimationMixer,
  Color,
  LoopOnce,
  LoopRepeat,
  MathUtils,
  Mesh,
  MeshStandardMaterial,
  PointLight,
} from 'three';
import type { AnimationAction } from 'three';
import { createAvatarScene } from '../shared/avatar-scene.js';
import { loadAvatarShaders, type AvatarShaderController, type AvatarShaderSpec } from './avatar-shaders.js';

const ACTION_CROSSFADE_SECONDS = 0.25;
const ACTION_PULSE_DURATION_SECONDS = 1.2;

interface AvatarActionManifest {
  readonly id: string;
  readonly label?: string;
  readonly clip?: string;
  readonly description?: string;
  readonly loop?: boolean;
  readonly lightColor?: string;
}

interface AvatarReactions {
  readonly hover?: string;
  readonly activate?: string;
}

interface AvatarManifest {
  readonly title: string;
  readonly model: 'model.glb';
  readonly poster?: 'preview.webp';
  readonly idle: string;
  readonly reactions?: AvatarReactions;
  readonly actions: readonly AvatarActionManifest[];
  readonly renderScale?: number;
  readonly fillLight?: number;
  readonly hoverGlow?: {
    readonly material: string;
    readonly intensity: number;
    readonly light?: { readonly name: string; readonly intensity: number };
  };
  readonly shaders?: readonly AvatarShaderSpec[];
}

export interface AvatarActionInfo {
  readonly id: string;
  readonly label: string;
  readonly description?: string;
  readonly loop: boolean;
  readonly duration: number | null;
  readonly lightColor?: string;
}

export interface AvatarColorAction {
  readonly id: string;
  readonly label: string;
  readonly color: string;
}

export interface MountAvatarOptions {
  readonly isVisible?: () => boolean;
}

export interface AvatarPlayback {
  readonly idle: string;
  readonly currentAction: string;
  readonly actions: readonly AvatarActionInfo[];
  readonly colors: readonly AvatarColorAction[];
  readonly reactions?: AvatarReactions;
  playAction(actionId: string): boolean;
  selectColor(actionId: string): boolean;
  onActionChange(listener: (actionId: string) => void): () => void;
  dispose(): void;
}

interface RegisteredAction {
  readonly info: AvatarActionInfo;
  readonly clipAction: AnimationAction | null;
}

function isHexColor(value: string | undefined): value is string {
  return typeof value === 'string' && /^#[\da-f]{6}$/i.test(value);
}

function configureClipAction(clipAction: AnimationAction, loop: boolean): void {
  clipAction.setLoop(loop ? LoopRepeat : LoopOnce, loop ? Infinity : 1);
  clipAction.clampWhenFinished = !loop;
}

function isMinimalModeActive(): boolean {
  return document.documentElement.classList.contains('minimal-mode');
}

export async function mountAvatar(
  mount: HTMLElement,
  packagePath: string,
  options?: MountAvatarOptions,
): Promise<AvatarPlayback | null> {
  const packageUrl = new URL(packagePath, window.location.href);
  const response = await fetch(new URL('avatar.json', packageUrl));
  if (!response.ok) throw new Error(`Avatar manifest returned ${response.status}`);
  const manifest: AvatarManifest = await response.json();
  if (manifest.model !== 'model.glb') throw new Error('Expected an avatar model.glb.');
  const shouldShow = (): boolean => (options?.isVisible ? options.isVisible() : true);
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
    poster.addEventListener('load', () => {
      if (shouldShow()) mount.hidden = false;
    }, { once: true });
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
  const mixer = new AnimationMixer(model);
  const actionMap = new Map<string, RegisteredAction>();
  const actionInfos: AvatarActionInfo[] = [];

  for (const entry of manifest.actions) {
    const clip = entry.clip ? animations.find((animation) => animation.name === entry.clip) : undefined;
    const clipAction = clip ? mixer.clipAction(clip) : null;
    const loop = entry.loop === true;
    const info: AvatarActionInfo = {
      id: entry.id,
      label: entry.label ?? entry.id,
      description: entry.description,
      loop,
      duration: clip ? clip.duration : null,
      lightColor: isHexColor(entry.lightColor) ? entry.lightColor : undefined,
    };
    actionMap.set(entry.id, { info, clipAction });
    actionInfos.push(info);
  }

  const idleAction = actionMap.get(manifest.idle) ?? null;
  let activeClipAction: AnimationAction | null = idleAction?.clipAction ?? null;
  if (idleAction && activeClipAction) {
    configureClipAction(activeClipAction, idleAction.info.loop);
    activeClipAction.reset().setEffectiveTimeScale(1).setEffectiveWeight(1).play();
    mixer.update(0);
  }

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
  const colors = supportsColor ? actionInfos.flatMap(({ id, label, lightColor }) => (
    lightColor ? [{ id, label, color: lightColor }] : []
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
  let actionPulse = 0;
  let currentActionId = manifest.idle;
  const actionListeners = new Set<(actionId: string) => void>();

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
        controller.update(elapsedShaderTime, brightness, canvas.width, canvas.height, actionPulse);
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
  mount.hidden = !shouldShow();
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const abortController = new AbortController();
  const listenerOptions = { signal: abortController.signal };
  let lastTime = 0;
  let hovered = false;
  let focused = false;

  function isMountVisible(): boolean {
    return !mount.hidden && mount.clientWidth > 0 && mount.clientHeight > 0;
  }

  function activateAction(actionId: string, triggerPulse: boolean): boolean {
    if (!ready) return false;
    const next = actionMap.get(actionId);
    if (!next) return false;
    currentActionId = next.info.id;

    if (targetColor) {
      if (next.info.lightColor) {
        targetColor.set(next.info.lightColor);
      } else if (next.info.id === manifest.idle && idleColor) {
        targetColor.set(idleColor.color);
      }
      if (reducedMotion.matches && colors.length > 0) {
        activeColor.copy(targetColor);
        for (const material of materials.keys()) material.emissive.copy(activeColor);
      }
    }

    if (triggerPulse && !reducedMotion.matches) {
      actionPulse = 1;
    }

    if (next.clipAction) {
      configureClipAction(next.clipAction, next.info.loop);
      const fadeSeconds = reducedMotion.matches ? 0 : ACTION_CROSSFADE_SECONDS;
      if (activeClipAction === next.clipAction) {
        if (!next.info.loop || !next.clipAction.isRunning()) {
          next.clipAction.reset().setEffectiveTimeScale(1).setEffectiveWeight(1).play();
          mixer.update(0);
        }
      } else {
        for (const entry of actionMap.values()) {
          if (
            entry.clipAction
            && entry.clipAction !== next.clipAction
            && (fadeSeconds <= 0 || entry.clipAction !== activeClipAction)
          ) {
            entry.clipAction.stop();
          }
        }
        next.clipAction.reset().setEffectiveTimeScale(1).setEffectiveWeight(1).play();
        if (fadeSeconds > 0 && activeClipAction) {
          activeClipAction.crossFadeTo(next.clipAction, fadeSeconds, false);
        } else {
          mixer.update(0);
        }
        activeClipAction = next.clipAction;
      }
    }

    for (const listener of actionListeners) {
      listener(currentActionId);
    }
    if (reducedMotion.matches && isMountVisible()) {
      if (canvas.width === 0 || canvas.height === 0) {
        resize();
      } else {
        if (shaderController && !isMinimalModeActive()) {
          shaderController.update(elapsedShaderTime, brightness, canvas.width, canvas.height, 0);
        }
        render();
      }
    }
    return true;
  }

  mixer.addEventListener('finished', (event) => {
    const finishedAction = (event as { action?: AnimationAction }).action;
    queueMicrotask(() => {
      if (!ready || !idleAction) return;
      if (finishedAction === activeClipAction && currentActionId !== manifest.idle) {
        activateAction(idleAction.info.id, false);
      }
    });
  });

  function update(time: number): void {
    if (!isMountVisible()) {
      lastTime = time;
      return;
    }
    const delta = Math.min((time - (lastTime || time)) / 1000, 0.1);
    lastTime = time;
    if (!reducedMotion.matches) {
      mixer.update(delta);
      elapsedShaderTime += delta;
      if (actionPulse > 0) {
        actionPulse = Math.max(0, actionPulse - delta / ACTION_PULSE_DURATION_SECONDS);
      }
    } else {
      actionPulse = 0;
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
      shaderController.update(elapsedShaderTime, brightness, canvas.width, canvas.height, actionPulse);
    }
    render();
  }

  const hoverReaction = manifest.reactions?.hover;
  const activateReaction = manifest.reactions?.activate;
  const hasHoverReaction = Boolean(hoverReaction && actionMap.has(hoverReaction));
  const hasActivateReaction = Boolean(activateReaction && actionMap.has(activateReaction));

  if (materials.size > 0 || lightGlow || shaderSpecs.length > 0 || hasHoverReaction || hasActivateReaction) {
    mount.tabIndex = 0;
    mount.addEventListener('pointerenter', (event) => {
      hovered = event.pointerType !== 'touch';
      if (hovered && hoverReaction && hasHoverReaction && currentActionId === manifest.idle && !reducedMotion.matches) {
        activateAction(hoverReaction, true);
      }
    }, listenerOptions);
    mount.addEventListener('pointerleave', () => { hovered = false; }, listenerOptions);
    mount.addEventListener('focus', () => { focused = true; }, listenerOptions);
    mount.addEventListener('blur', () => { focused = false; }, listenerOptions);
  }

  if (hasActivateReaction && activateReaction) {
    const stopPointer = (event: Event): void => {
      event.stopPropagation();
    };
    mount.style.cursor = 'pointer';
    mount.addEventListener('pointerdown', stopPointer, listenerOptions);
    mount.addEventListener('mousedown', stopPointer, listenerOptions);
    mount.addEventListener('click', () => {
      activateAction(activateReaction, true);
    }, listenerOptions);
    mount.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        activateAction(activateReaction, true);
      }
    }, listenerOptions);
  }

  function resize(): void {
    const size = Math.min(mount.clientWidth, mount.clientHeight || mount.clientWidth);
    if (size <= 0) return;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(size, size, false);
    if (shaderController && !isMinimalModeActive()) {
      shaderController.update(elapsedShaderTime, brightness, canvas.width, canvas.height, actionPulse);
    }
    render();
  }

  const observer = new ResizeObserver(resize);
  observer.observe(mount);
  const minimalObserver = new MutationObserver(() => {
    const minimal = isMinimalModeActive();
    if (!minimal && !shaderController && !shaderLoadStarted && shaderSpecs.length > 0) {
      void ensureShaders().then(() => { if (ready && isMountVisible()) render(); });
      return;
    }
    shaderController?.setEnabled(!minimal);
    if (!minimal && shaderController) {
      shaderController.update(elapsedShaderTime, brightness, canvas.width, canvas.height, actionPulse);
    }
    if (isMountVisible()) render();
  });
  minimalObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

  function teardown(disposeRenderer: boolean): void {
    ready = false;
    abortController.abort();
    observer.disconnect();
    minimalObserver.disconnect();
    actionListeners.clear();
    mixer.stopAllAction();
    shaderController?.dispose();
    shaderController = null;
    renderer.setAnimationLoop(null);
    if (disposeRenderer) {
      renderer.dispose();
    }
  }

  canvas.addEventListener('webglcontextlost', () => {
    teardown(false);
    showPoster();
  }, { once: true, signal: abortController.signal });
  resize();
  renderer.setAnimationLoop(update);
  return {
    idle: manifest.idle,
    get currentAction() {
      return currentActionId;
    },
    actions: actionInfos,
    colors,
    reactions: manifest.reactions,
    playAction(actionId: string) {
      return activateAction(actionId, actionId !== manifest.idle);
    },
    selectColor(actionId) {
      const action = colors.find(({ id }) => id === actionId);
      if (!ready || !action || !targetColor) return false;
      targetColor.set(action.color);
      if (reducedMotion.matches && isMountVisible()) {
        activeColor.copy(targetColor);
        for (const material of materials.keys()) material.emissive.copy(activeColor);
        if (shaderController && !isMinimalModeActive()) {
          shaderController.update(elapsedShaderTime, brightness, canvas.width, canvas.height, 0);
        }
        render();
      }
      return true;
    },
    onActionChange(listener) {
      actionListeners.add(listener);
      return () => { actionListeners.delete(listener); };
    },
    dispose() {
      if (ready) {
        teardown(true);
      }
      mount.replaceChildren();
      mount.style.cursor = '';
      mount.tabIndex = -1;
      mount.hidden = true;
    },
  };
}
