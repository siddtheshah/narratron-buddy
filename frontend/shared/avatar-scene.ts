import {
  ACESFilmicToneMapping, DirectionalLight, HemisphereLight, LoadingManager,
  Material, Mesh, OrthographicCamera, Scene, WebGLRenderer,
} from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

export async function createAvatarScene(
  canvas: HTMLCanvasElement,
  context: WebGL2RenderingContext,
  modelUrl: URL,
  fillLight = 1,
) {
  const response = await fetch(modelUrl, { cache: 'no-cache' });
  if (!response.ok) throw new Error(`Avatar model returned ${response.status}`);
  const manager = new LoadingManager().setURLModifier((url) => {
    if (url.startsWith('blob:') || url.startsWith('data:')) return url;
    throw new Error('Avatar models must embed their resources.');
  });
  const { scene: model, animations } = await new GLTFLoader(manager).parseAsync(await response.arrayBuffer(), '');
  model.traverse((node) => {
    if (node instanceof Mesh && node.material instanceof Material && node.material.name === 'NarratronAstral') {
      node.material.depthWrite = true;
    }
  });
  const scene = new Scene();
  scene.add(model, new HemisphereLight(0xe8ffff, 0x7e6470, 2.1 * fillLight));
  const key = new DirectionalLight(0xffdec0, 2.4 * fillLight);
  key.position.set(-3, 5, 5);
  const rim = new DirectionalLight(0x76f5ff, 1.4 * fillLight);
  rim.position.set(4, 2, -3);
  scene.add(key, rim);

  const camera = new OrthographicCamera(-1.16, 1.16, 1.16, -1.16, 0.1, 30);
  camera.position.set(2.4, 1.9, 5.6);
  camera.lookAt(0, 0.28, 0);
  const renderer = new WebGLRenderer({ canvas, context, alpha: true, antialias: true });
  renderer.setClearColor(0, 0);
  renderer.toneMapping = ACESFilmicToneMapping;
  return { model, animations, renderer, render: () => renderer.render(scene, camera) };
}
