import { Mesh, ShaderMaterial, SphereGeometry } from 'three';
import type { PointLight } from 'three';

const vertexShader = /* glsl */ `
  varying vec3 surface;
  varying vec3 viewDirection;

  void main() {
    surface = position;
    // In the avatar's orthographic camera, every ray travels along view-space -Z.
    viewDirection = normalize(vec3(modelViewMatrix[0].z, modelViewMatrix[1].z, modelViewMatrix[2].z));
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const fragmentShader = /* glsl */ `
  #include <common>

  uniform vec3 sparkColor;
  uniform float sparkStrength;
  varying vec3 surface;
  varying vec3 viewDirection;

  float hash(vec3 cell) {
    vec3 p = fract(cell * vec3(0.1031, 0.11369, 0.13787));
    p += dot(p, p.yzx + 19.19);
    return fract((p.x + p.y) * p.z);
  }

  float noise(vec3 p) {
    vec3 cell = floor(p);
    vec3 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    vec2 x00 = vec2(hash(cell), hash(cell + vec3(1.0, 0.0, 0.0)));
    vec2 x10 = vec2(hash(cell + vec3(0.0, 1.0, 0.0)), hash(cell + vec3(1.0, 1.0, 0.0)));
    vec2 x01 = vec2(hash(cell + vec3(0.0, 0.0, 1.0)), hash(cell + vec3(1.0, 0.0, 1.0)));
    vec2 x11 = vec2(hash(cell + vec3(0.0, 1.0, 1.0)), hash(cell + vec3(1.0, 1.0, 1.0)));
    vec2 weights = vec2(1.0 - f.x, f.x);
    return mix(mix(dot(x00, weights), dot(x10, weights), f.y),
               mix(dot(x01, weights), dot(x11, weights), f.y), f.z);
  }

  float density(vec3 point) {
    float envelope = max(1.0 - dot(point, point), 0.0);
    float wisps = noise(point * 4.8 + vec3(7.5, 2.9, 4.2));
    return envelope * envelope * mix(0.22, 1.4, smoothstep(0.2, 0.8, wisps));
  }

  void main() {
    const int steps = 28;
    const float extinction = 1.65;
    vec3 towardEye = normalize(viewDirection);
    vec3 rayOrigin = surface - towardEye * dot(surface, towardEye);
    float halfLength = sqrt(max(1.0 - dot(rayOrigin, rayOrigin), 0.0));
    float stepLength = 2.0 * halfLength / float(steps);
    float viewTransmission = 1.0;
    float scattered = 0.0;

    for (int step = 0; step < steps; step++) {
      float offset = halfLength - (float(step) + 0.5) * stepLength;
      vec3 point = rayOrigin + towardEye * offset;
      float distance2 = dot(point, point);
      float distanceToSpark = sqrt(distance2);
      float dust = density(point);
      float grains = pow(noise(point * 19.0 + vec3(4.7, 6.2, 1.3)), 7.0);
      float opticalDepth = extinction * dust * (0.84 + 2.0 * grains);
      float stepTransmission = exp(-opticalDepth * stepLength);

      // Sample the path from the spark to this grain, then the path to the eye.
      float sourceDensity = (density(point * 0.17) + density(point * 0.5) + density(point * 0.83)) / 3.0;
      float incident = exp(-extinction * sourceDensity * distanceToSpark) / (0.24 + distance2);
      float scatteringAngle = dot(point, towardEye) / max(distanceToSpark, 0.001);
      float phase = 0.52 + 0.45 * scatteringAngle * scatteringAngle + 0.27 * max(-scatteringAngle, 0.0);
      float spark = exp(-42.0 * distance2) * 0.47 * stepLength;
      scattered += viewTransmission * ((1.0 - stepTransmission) * incident * phase + spark);
      viewTransmission *= stepTransmission;
    }

    float opacity = 1.0 - viewTransmission;
    gl_FragColor = vec4(sparkColor * sparkStrength * scattered / max(opacity, 0.0001), opacity);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;

export function addAvatarDust(light: PointLight, radius: number): (brightness: number) => void {
  const idleStrength = 0.62;
  const uniforms = {
    sparkColor: { value: light.color },
    sparkStrength: { value: idleStrength },
  };
  const material = new ShaderMaterial({ uniforms, vertexShader, fragmentShader, transparent: true, depthWrite: false });
  const cloud = new Mesh(new SphereGeometry(1, 32, 24), material);
  cloud.scale.setScalar(radius);
  light.add(cloud);
  return (brightness) => { uniforms.sparkStrength.value = idleStrength + 1.12 * brightness; };
}
