import {
  AdditiveBlending,
  BoxGeometry,
  Color,
  DoubleSide,
  FrontSide,
  BackSide,
  MathUtils,
  Mesh,
  NormalBlending,
  PlaneGeometry,
  ShaderMaterial,
  SphereGeometry,
  Vector2,
  Vector3,
  Vector4,
} from 'three';
import type { BufferGeometry, IUniform, Material, Object3D } from 'three';

export type AvatarShaderUniformSpec =
  | number
  | string
  | readonly number[]
  | { readonly source: 'time' | 'brightness' | 'lightColor' | 'resolution' }
  | { readonly rest: number; readonly hover: number };

export interface AvatarShaderGeometrySpec {
  readonly type: 'sphere' | 'box' | 'plane';
  readonly radius?: number;
  readonly widthSegments?: number;
  readonly heightSegments?: number;
  readonly width?: number;
  readonly height?: number;
  readonly depth?: number;
}

export interface AvatarShaderSpec {
  readonly attachTo?: string;
  readonly replaceMaterial?: string;
  readonly geometry?: AvatarShaderGeometrySpec;
  readonly scale?: number | readonly [number, number, number];
  readonly position?: readonly [number, number, number];
  readonly vertex: string;
  readonly fragment: string;
  readonly transparent?: boolean;
  readonly depthWrite?: boolean;
  readonly blending?: 'normal' | 'additive';
  readonly side?: 'front' | 'back' | 'double';
  readonly uniforms?: Readonly<Record<string, AvatarShaderUniformSpec>>;
}

export interface AvatarShaderController {
  setEnabled(enabled: boolean): void;
  update(elapsedTime: number, brightness: number, width: number, height: number): void;
  dispose(): void;
}

interface BrightnessBinding {
  readonly uniform: IUniform<number>;
  readonly rest: number;
  readonly hover: number;
}

interface MaterialReplacement {
  readonly mesh: Mesh;
  readonly original: Material | Material[];
  readonly shader: ShaderMaterial;
}

function resolveShaderUrl(packageUrl: URL, relativePath: string): URL {
  if (
    !relativePath ||
    relativePath.startsWith('/') ||
    relativePath.includes('..') ||
    !/\.(glsl|vert|frag)$/i.test(relativePath)
  ) {
    throw new Error(`Invalid avatar shader path: ${relativePath}`);
  }
  const resolved = new URL(relativePath, packageUrl);
  if (!resolved.href.startsWith(packageUrl.href)) {
    throw new Error(`Avatar shader path escapes package: ${relativePath}`);
  }
  return resolved;
}

function finiteOr(value: number | undefined, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function clampSegments(value: number | undefined, fallback: number): number {
  return Math.max(3, Math.min(64, Math.round(finiteOr(value, fallback))));
}

function createGeometry(spec?: AvatarShaderGeometrySpec): BufferGeometry {
  if (!spec || spec.type === 'sphere') {
    return new SphereGeometry(
      Math.max(0.01, finiteOr(spec?.radius, 1)),
      clampSegments(spec?.widthSegments, 32),
      clampSegments(spec?.heightSegments, 24),
    );
  }
  if (spec.type === 'box') {
    return new BoxGeometry(
      Math.max(0.01, finiteOr(spec.width, 1)),
      Math.max(0.01, finiteOr(spec.height, 1)),
      Math.max(0.01, finiteOr(spec.depth, 1)),
    );
  }
  return new PlaneGeometry(
    Math.max(0.01, finiteOr(spec.width, 1)),
    Math.max(0.01, finiteOr(spec.height, 1)),
  );
}

function parseStaticUniform(spec: AvatarShaderUniformSpec): IUniform | null {
  if (typeof spec === 'number' && Number.isFinite(spec)) {
    return { value: spec };
  }
  if (typeof spec === 'string' && /^#[\da-f]{6}$/i.test(spec)) {
    return { value: new Color(spec) };
  }
  if (Array.isArray(spec) && spec.every((item) => typeof item === 'number' && Number.isFinite(item))) {
    if (spec.length === 2) return { value: new Vector2(spec[0], spec[1]) };
    if (spec.length === 3) return { value: new Vector3(spec[0], spec[1], spec[2]) };
    if (spec.length === 4) return { value: new Vector4(spec[0], spec[1], spec[2], spec[3]) };
  }
  return null;
}

function isRangeUniform(spec: AvatarShaderUniformSpec): spec is { readonly rest: number; readonly hover: number } {
  return typeof spec === 'object' && spec !== null && !Array.isArray(spec) && 'rest' in spec && 'hover' in spec;
}

function isSourceUniform(
  spec: AvatarShaderUniformSpec,
): spec is { readonly source: 'time' | 'brightness' | 'lightColor' | 'resolution' } {
  return typeof spec === 'object' && spec !== null && !Array.isArray(spec) && 'source' in spec;
}

export async function loadAvatarShaders(
  model: Object3D,
  packageUrl: URL,
  specs: readonly AvatarShaderSpec[],
  activeColor: Color,
): Promise<AvatarShaderController> {
  const sources = new Map<string, Promise<string>>();
  function fetchShader(relativePath: string): Promise<string> {
    const url = resolveShaderUrl(packageUrl, relativePath);
    let pending = sources.get(url.href);
    if (!pending) {
      pending = fetch(url).then((response) => {
        if (!response.ok) throw new Error(`Shader ${relativePath} returned ${response.status}`);
        return response.text();
      });
      sources.set(url.href, pending);
    }
    return pending;
  }

  const compiledSources = await Promise.all(
    specs.map(async (spec) => ({
      spec,
      vertexShader: await fetchShader(spec.vertex),
      fragmentShader: await fetchShader(spec.fragment),
    })),
  );

  const resolution = new Vector2(1, 1);
  const timeUniforms: Array<IUniform<number>> = [];
  const brightnessBindings: BrightnessBinding[] = [];
  const attachedMeshes: Mesh[] = [];
  const geometries: BufferGeometry[] = [];
  const materials: ShaderMaterial[] = [];
  const replacements: MaterialReplacement[] = [];

  for (const { spec, vertexShader, fragmentShader } of compiledSources) {
    const timeUniform: IUniform<number> = { value: 0 };
    const brightnessUniform: IUniform<number> = { value: 0 };
    timeUniforms.push(timeUniform);
    brightnessBindings.push({ uniform: brightnessUniform, rest: 0, hover: 1 });

    const uniforms: Record<string, IUniform> = {
      uTime: timeUniform,
      uBrightness: brightnessUniform,
      uColor: { value: activeColor },
      uResolution: { value: resolution },
    };

    if (spec.uniforms) {
      for (const [name, valueSpec] of Object.entries(spec.uniforms)) {
        if (isSourceUniform(valueSpec)) {
          if (valueSpec.source === 'time') {
            const uniform: IUniform<number> = { value: 0 };
            uniforms[name] = uniform;
            timeUniforms.push(uniform);
          } else if (valueSpec.source === 'brightness') {
            const uniform: IUniform<number> = { value: 0 };
            uniforms[name] = uniform;
            brightnessBindings.push({ uniform, rest: 0, hover: 1 });
          } else if (valueSpec.source === 'lightColor') {
            uniforms[name] = { value: activeColor };
          } else if (valueSpec.source === 'resolution') {
            uniforms[name] = { value: resolution };
          }
          continue;
        }
        if (isRangeUniform(valueSpec)) {
          const rest = finiteOr(valueSpec.rest, 0);
          const hover = finiteOr(valueSpec.hover, 1);
          const uniform: IUniform<number> = { value: rest };
          uniforms[name] = uniform;
          brightnessBindings.push({ uniform, rest, hover });
          continue;
        }
        const staticUniform = parseStaticUniform(valueSpec);
        if (staticUniform) uniforms[name] = staticUniform;
      }
    }

    const material = new ShaderMaterial({
      uniforms,
      vertexShader,
      fragmentShader,
      transparent: spec.transparent ?? false,
      depthWrite: spec.depthWrite ?? true,
      blending: spec.blending === 'additive' ? AdditiveBlending : NormalBlending,
      side: spec.side === 'double' ? DoubleSide : spec.side === 'back' ? BackSide : FrontSide,
    });
    materials.push(material);

    if (spec.replaceMaterial) {
      model.traverse((node) => {
        if (
          node instanceof Mesh &&
          !Array.isArray(node.material) &&
          node.material.name === spec.replaceMaterial
        ) {
          replacements.push({ mesh: node, original: node.material, shader: material });
          node.material = material;
        }
      });
      continue;
    }

    const parent = spec.attachTo ? model.getObjectByName(spec.attachTo) : model;
    if (!parent) {
      throw new Error(`Avatar shader target node not found: ${spec.attachTo}`);
    }
    const geometry = createGeometry(spec.geometry);
    geometries.push(geometry);
    const mesh = new Mesh(geometry, material);
    if (typeof spec.scale === 'number' && Number.isFinite(spec.scale)) {
      mesh.scale.setScalar(spec.scale);
    } else if (Array.isArray(spec.scale) && spec.scale.length === 3) {
      mesh.scale.set(
        finiteOr(spec.scale[0], 1),
        finiteOr(spec.scale[1], 1),
        finiteOr(spec.scale[2], 1),
      );
    }
    if (Array.isArray(spec.position) && spec.position.length === 3) {
      mesh.position.set(
        finiteOr(spec.position[0], 0),
        finiteOr(spec.position[1], 0),
        finiteOr(spec.position[2], 0),
      );
    }
    parent.add(mesh);
    attachedMeshes.push(mesh);
  }

  let enabled = true;
  return {
    setEnabled(nextEnabled: boolean): void {
      if (enabled === nextEnabled) return;
      enabled = nextEnabled;
      for (const mesh of attachedMeshes) {
        mesh.visible = enabled;
      }
      for (const entry of replacements) {
        entry.mesh.material = enabled ? entry.shader : entry.original;
      }
    },
    update(elapsedTime: number, brightness: number, width: number, height: number): void {
      if (!enabled) return;
      resolution.set(Math.max(1, width), Math.max(1, height));
      for (const uniform of timeUniforms) {
        uniform.value = elapsedTime;
      }
      for (const binding of brightnessBindings) {
        binding.uniform.value = MathUtils.lerp(binding.rest, binding.hover, brightness);
      }
    },
    dispose(): void {
      for (const mesh of attachedMeshes) {
        mesh.removeFromParent();
      }
      for (const entry of replacements) {
        entry.mesh.material = entry.original;
      }
      for (const geometry of geometries) {
        geometry.dispose();
      }
      for (const material of materials) {
        material.dispose();
      }
    },
  };
}
