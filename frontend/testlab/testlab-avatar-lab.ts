import type { AnimationAction } from 'three';

interface AvatarAction {
  readonly id: string;
  readonly label: string;
  readonly clip: string;
  readonly description: string;
  readonly loop?: boolean;
}

interface AvatarManifest {
  readonly model: string;
  readonly poster: string;
  readonly idle: string;
  readonly reactions: { readonly hover: string; readonly activate: string };
  readonly actions: readonly AvatarAction[];
}

type AvatarSelection = 'cube' | 'light-cube' | 'narratron' | 'example_avatar' | 'umbral-sigil';

const packageUrl = new URL('/avatar-assets/cube/', window.location.href);
const avatarChoice = document.querySelector<HTMLSelectElement>('#avatar-choice')!;
const requestedAvatar = new URLSearchParams(location.search).get('avatar');
const selectedAvatar: AvatarSelection =
  requestedAvatar === 'light-cube' || requestedAvatar === 'narratron'
    || requestedAvatar === 'example_avatar' || requestedAvatar === 'umbral-sigil'
    ? requestedAvatar
    : 'cube';
const stage = document.querySelector<HTMLDivElement>('#stage')!;
const backdrop = document.querySelector<HTMLSelectElement>('#backdrop')!;
const viewport = document.querySelector<HTMLDivElement>('#avatar')!;
const poster = document.querySelector<HTMLImageElement>('#poster')!;
const avatarButton = document.querySelector<HTMLButtonElement>('#avatar-activate')!;
const interactionHint = document.querySelector<HTMLParagraphElement>('#avatar-interaction-hint')!;
const status = document.querySelector<HTMLParagraphElement>('#playback-status')!;
const actionList = document.querySelector<HTMLDivElement>('#action-list')!;
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');

avatarChoice.value = selectedAvatar;
avatarChoice.addEventListener('change', () => {
  location.search = avatarChoice.value === 'cube' ? '' : `?avatar=${encodeURIComponent(avatarChoice.value)}`;
});

backdrop.addEventListener('change', () => {
  stage.dataset.backdrop = backdrop.value;
});

async function startLab(): Promise<void> {
  const response = await fetch(new URL('avatar.json', packageUrl));
  if (!response.ok) throw new Error(`Avatar manifest returned ${response.status}`);
  const manifest: AvatarManifest = await response.json();
  const buttons = new Map<string, HTMLButtonElement>();

  for (const action of manifest.actions) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'action';
    button.disabled = true;
    button.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-label', action.label);

    const text = document.createElement('span');
    const name = document.createElement('span');
    name.className = 'action-name';
    name.textContent = action.label;
    const description = document.createElement('span');
    description.className = 'action-description';
    description.textContent = action.description;
    const duration = document.createElement('span');
    duration.className = 'action-duration';
    text.append(name, description);
    button.append(text, duration);
    actionList.append(button);
    buttons.set(action.id, button);
  }

  const canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true');
  let frame = 0;
  function showPoster(message: string): void {
    cancelAnimationFrame(frame);
    canvas.remove();
    poster.src = new URL(manifest.poster, packageUrl).href;
    poster.hidden = false;
    avatarButton.hidden = true;
    interactionHint.hidden = true;
    status.textContent = message;
    for (const button of buttons.values()) {
      button.disabled = true;
      button.setAttribute('aria-pressed', 'false');
    }
  }

  const context = canvas.getContext('webgl2', { alpha: true, antialias: true });
  if (!context) {
    showPoster('WebGL 2 is unavailable. Showing the cube preview.');
    return;
  }

  const [THREE, { createAvatarScene }] = await Promise.all([
    import('three'),
    import('../shared/avatar-scene.js'),
  ]);
  const { model, animations, renderer, render } = await createAvatarScene(
    canvas, context, new URL(manifest.model, packageUrl),
  );
  viewport.append(canvas);

  const mixer = new THREE.AnimationMixer(model);
  const actions = new Map<string, { definition: AvatarAction; animation: AnimationAction }>();
  for (const definition of manifest.actions) {
    const clip = animations.find((candidate) => candidate.name === definition.clip);
    if (!clip) throw new Error(`Model clip is missing: ${definition.clip}`);
    const animation = mixer.clipAction(clip).setLoop(
      definition.loop ? THREE.LoopRepeat : THREE.LoopOnce,
      definition.loop ? Infinity : 1,
    );
    actions.set(definition.id, { definition, animation });
  }
  const idle = actions.get(manifest.idle);
  if (!idle) throw new Error('Avatar is missing its idle action');

  let active: AnimationAction | null = null;
  let previousTime = 0;
  let ready = true;
  function play(actionId: string, automatic = false): void {
    const action = actions.get(actionId);
    if (!action || !ready) return;
    cancelAnimationFrame(frame);
    mixer.stopAllAction();
    const paused = automatic && reducedMotion.matches;
    active = paused ? null : action.animation.reset().play();
    mixer.update(0);
    for (const [id, button] of buttons) {
      button.setAttribute('aria-pressed', String(!paused && id === actionId));
    }
    status.textContent = paused ? 'Idle paused for reduced motion. Select an action to play it.'
      : action.definition.loop ? `Looping ${action.definition.label}`
        : `Playing ${action.definition.label}`;
    render();
    previousTime = performance.now();
    if (active) frame = requestAnimationFrame(renderFrame);
  }

  function renderFrame(time: number): void {
    const delta = Math.max(0, Math.min((time - previousTime) / 1000, 0.1));
    previousTime = time;
    mixer.update(delta);
    render();
    frame = requestAnimationFrame(renderFrame);
  }

  mixer.addEventListener('finished', ({ action }) => {
    queueMicrotask(() => {
      if (active === action) play(idle.definition.id, true);
    });
  });

  function resize(): void {
    const size = viewport.clientWidth;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(size, size, false);
    render();
  }

  const observer = new ResizeObserver(resize);
  observer.observe(viewport);
  canvas.addEventListener('webglcontextlost', () => {
    ready = false;
    observer.disconnect();
    showPoster('3D rendering stopped. Reload the page to retry.');
  }, { once: true });

  for (const [id, { definition, animation }] of actions) {
    const button = buttons.get(id)!;
    button.disabled = false;
    button.lastElementChild!.textContent = definition.loop ? 'Loop' : `${animation.getClip().duration.toFixed(1)} s`;
    button.addEventListener('click', () => play(id));
  }
  avatarButton.addEventListener('pointerenter', (event) => {
    if (event.pointerType === 'mouse' && active === idle.animation && !reducedMotion.matches) {
      play(manifest.reactions.hover);
    }
  });
  avatarButton.addEventListener('click', () => play(manifest.reactions.activate));
  avatarButton.hidden = false;
  interactionHint.hidden = false;

  reducedMotion.addEventListener('change', () => play(idle.definition.id, true));
  resize();
  play(idle.definition.id, true);
}

async function startLightCube(): Promise<void> {
  document.getElementById('actions-heading')!.textContent = 'Light colors';
  viewport.replaceChildren();
  viewport.hidden = true;
  const { mountAvatar } = await import('../canvas/avatar.js');
  const player = await mountAvatar(viewport, '/avatar-assets/light-cube/');
  if (!player) {
    status.textContent = '3D playback is unavailable. Showing the cube preview.';
    return;
  }
  const buttons = new Map<string, HTMLButtonElement>();
  function selectColor(id: string, label: string): void {
    if (!player?.selectColor(id)) return;
    for (const [colorId, button] of buttons) {
      button.setAttribute('aria-pressed', String(id === colorId));
    }
    status.textContent = `${label} glow`;
  }
  for (const { id, label, color } of player.colors) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'action';
    button.style.setProperty('--action-light', color);
    const name = document.createElement('span');
    name.className = 'action-name';
    name.textContent = label;
    const swatch = document.createElement('span');
    swatch.className = 'action-color';
    button.append(name, swatch);
    button.addEventListener('click', () => selectColor(id, label));
    actionList.append(button);
    buttons.set(id, button);
    button.setAttribute('aria-pressed', 'false');
  }
  selectColor(player.idle, player.colors.find(({ id }) => id === player.idle)?.label ?? 'Idle');
  interactionHint.textContent = 'Hover over the cube to brighten the selected color.';
  interactionHint.hidden = false;
}

interface PackagedAvatarLabConfig {
  readonly heading: string;
  readonly loadingStatus: string;
  readonly unavailableStatus: string;
  readonly assetPath: string;
  readonly activateAriaLabel: string;
  readonly hint: string;
}

async function startPackagedAvatar(config: PackagedAvatarLabConfig): Promise<void> {
  document.getElementById('actions-heading')!.textContent = config.heading;
  status.textContent = config.loadingStatus;
  viewport.replaceChildren();
  viewport.hidden = true;
  const { mountAvatar } = await import('../canvas/avatar.js');
  const player = await mountAvatar(viewport, config.assetPath);
  if (!player) {
    status.textContent = config.unavailableStatus;
    return;
  }
  const buttons = new Map<string, HTMLButtonElement>();
  function syncActiveAction(activeId: string): void {
    const activeInfo = player?.actions.find(({ id }) => id === activeId);
    for (const [id, button] of buttons) {
      button.setAttribute('aria-pressed', String(id === activeId));
    }
    if (reducedMotion.matches && activeId === player?.idle) {
      status.textContent = 'Idle paused for reduced motion. Select an action to preview it.';
    } else if (activeInfo) {
      status.textContent = activeInfo.loop ? `Looping ${activeInfo.label}` : `Playing ${activeInfo.label}`;
    }
  }
  for (const action of player.actions) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'action';
    button.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-label', action.label);
    if (action.lightColor) {
      button.style.setProperty('--action-light', action.lightColor);
    }

    const text = document.createElement('span');
    const name = document.createElement('span');
    name.className = 'action-name';
    name.textContent = action.label;
    text.append(name);
    if (action.description) {
      const description = document.createElement('span');
      description.className = 'action-description';
      description.textContent = action.description;
      text.append(description);
    }

    const duration = document.createElement('span');
    duration.className = 'action-duration';
    duration.textContent = action.loop
      ? 'Loop'
      : action.duration !== null
        ? `${action.duration.toFixed(1)} s`
        : 'Once';
    button.append(text, duration);
    button.addEventListener('click', () => {
      player.playAction(action.id);
    });
    actionList.append(button);
    buttons.set(action.id, button);
  }
  player.onActionChange(syncActiveAction);
  syncActiveAction(player.currentAction);
  reducedMotion.addEventListener('change', () => syncActiveAction(player.currentAction));

  const activateAction = player.reactions?.activate;
  if (activateAction) {
    avatarButton.setAttribute('aria-label', config.activateAriaLabel);
    avatarButton.hidden = false;
    avatarButton.addEventListener('click', (event) => {
      event.stopPropagation();
      player.playAction(activateAction);
    });
    viewport.append(avatarButton);
  }
  interactionHint.textContent = config.hint;
  interactionHint.hidden = false;
}

const packagedAvatars: Record<Exclude<AvatarSelection, 'cube' | 'light-cube'>, PackagedAvatarLabConfig> = {
  example_avatar: {
    heading: 'Smiley expressions',
    loadingStatus: 'Loading Example Avatar…',
    unavailableStatus: '3D playback is unavailable. Showing the static smile.',
    assetPath: '/avatar-assets/example_avatar/',
    activateAriaLabel: 'Make the example avatar laugh',
    hint: 'Click or tap the face to make it laugh.',
  },
  narratron: {
    heading: 'Narratron actions',
    loadingStatus: 'Loading Narratron…',
    unavailableStatus: '3D playback is unavailable. Showing the Narratron preview.',
    assetPath: '/avatar-assets/narratron/',
    activateAriaLabel: 'Greet Narratron',
    hint: 'Hover over Narratron to brighten its aura. Click or tap to greet it.',
  },
  'umbral-sigil': {
    heading: 'Umbral Sigil actions',
    loadingStatus: 'Loading Umbral Sigil…',
    unavailableStatus: '3D playback is unavailable. Showing the Umbral Sigil preview.',
    assetPath: '/avatar-assets/umbral-sigil/',
    activateAriaLabel: 'Provoke the Umbral Sigil',
    hint: 'Hover over the Umbral Sigil to flare its baleful core. Click or tap to provoke a spiky snap.',
  },
};

const startup = selectedAvatar === 'cube' ? startLab()
  : selectedAvatar === 'light-cube' ? startLightCube()
  : startPackagedAvatar(packagedAvatars[selectedAvatar]);

startup.catch((error: unknown) => {
  status.textContent = 'Could not load the avatar preview.';
  console.error('Avatar lab failed to start', error);
});
