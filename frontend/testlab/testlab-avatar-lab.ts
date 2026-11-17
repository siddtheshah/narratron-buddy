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

const packageUrl = new URL('/avatar-assets/cube/', window.location.href);
const stage = document.querySelector<HTMLDivElement>('#stage')!;
const backdrop = document.querySelector<HTMLSelectElement>('#backdrop')!;
const viewport = document.querySelector<HTMLDivElement>('#avatar')!;
const poster = document.querySelector<HTMLImageElement>('#poster')!;
const avatarButton = document.querySelector<HTMLButtonElement>('#avatar-activate')!;
const interactionHint = document.querySelector<HTMLParagraphElement>('#avatar-interaction-hint')!;
const status = document.querySelector<HTMLParagraphElement>('#playback-status')!;
const actionList = document.querySelector<HTMLDivElement>('#action-list')!;
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');

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

  const [THREE, { GLTFLoader }] = await Promise.all([
    import('three'),
    import('three/addons/loaders/GLTFLoader.js'),
  ]);
  const { scene: model, animations } = await new GLTFLoader().loadAsync(new URL(manifest.model, packageUrl).href);
  const scene = new THREE.Scene();
  scene.add(model, new THREE.HemisphereLight(0xe8ffff, 0x7e6470, 2.1));
  const keyLight = new THREE.DirectionalLight(0xffdec0, 2.4);
  keyLight.position.set(-3, 5, 5);
  const rimLight = new THREE.DirectionalLight(0x76f5ff, 1.4);
  rimLight.position.set(4, 2, -3);
  scene.add(keyLight, rimLight);

  const camera = new THREE.OrthographicCamera(-1.16, 1.16, 1.16, -1.16, 0.1, 30);
  camera.position.set(2.4, 1.9, 5.6);
  camera.lookAt(0, 0.28, 0);
  const renderer = new THREE.WebGLRenderer({ canvas, context, alpha: true, antialias: true });
  renderer.setClearColor(0, 0);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
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
    renderer.render(scene, camera);
    previousTime = performance.now();
    if (active) frame = requestAnimationFrame(renderFrame);
  }

  function renderFrame(time: number): void {
    const delta = Math.max(0, Math.min((time - previousTime) / 1000, 0.1));
    previousTime = time;
    mixer.update(delta);
    renderer.render(scene, camera);
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
    renderer.render(scene, camera);
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

startLab().catch((error: unknown) => {
  status.textContent = 'Could not load the cube preview.';
  console.error('Avatar lab failed to start', error);
});
