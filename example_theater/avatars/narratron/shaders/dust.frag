#include <common>

uniform float uTime;
uniform float uActionPulse;
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
  return mix(
    mix(dot(x00, weights), dot(x10, weights), f.y),
    mix(dot(x01, weights), dot(x11, weights), f.y),
    f.z
  );
}

vec3 swirlPoint(vec3 p, float t) {
  float r2 = dot(p.xz, p.xz);
  float angle = t * 0.24 + (1.0 - min(r2, 1.0)) * 1.25;
  float c = cos(angle);
  float s = sin(angle);
  return vec3(p.x * c - p.z * s, p.y - t * 0.10, p.x * s + p.z * c);
}

float fbm(vec3 p) {
  float v = 0.58 * noise(p);
  v += 0.30 * noise(p * 2.13 + vec3(3.1, 1.7, 5.4));
  v += 0.12 * noise(p * 4.27 + vec3(6.8, 2.3, 1.9));
  return v;
}

float density(vec3 point, float t) {
  float r2 = dot(point, point);
  float envelope = max(1.0 - r2, 0.0);
  envelope = envelope * envelope * envelope;
  vec3 advected = swirlPoint(point, t);
  float warp = fbm(advected * 2.3 + vec3(1.5, 4.1, 2.7));
  float wisps = fbm(advected * 3.6 + vec3(warp * 1.5, -warp * 0.9, warp * 1.1));
  float filament = smoothstep(0.34, 0.80, wisps);
  return envelope * mix(0.10, 1.45, filament);
}

void main() {
  const int steps = 26;
  const float extinction = 1.15;
  vec3 towardEye = normalize(viewDirection);
  vec3 rayOrigin = surface - towardEye * dot(surface, towardEye);
  float halfLength = sqrt(max(1.0 - dot(rayOrigin, rayOrigin), 0.0));
  float stepLength = 2.0 * halfLength / float(steps);
  float viewTransmission = 1.0;
  vec3 scattered = vec3(0.0);
  float pulse = 1.0 + 0.85 * clamp(uActionPulse, 0.0, 1.5);

  vec3 cosmicViolet = vec3(0.36, 0.20, 0.88);
  vec3 warmGold = vec3(1.0, 0.68, 0.32);

  for (int step = 0; step < steps; step++) {
    float offset = halfLength - (float(step) + 0.5) * stepLength;
    vec3 point = rayOrigin + towardEye * offset;
    float distance2 = dot(point, point);
    float distanceToCore = sqrt(distance2);
    float dust = density(point, uTime);

    vec3 starPos = swirlPoint(point, uTime * 1.35) * 15.0 + vec3(4.7, 6.2, 1.3);
    float twinkle = 0.60 + 0.40 * sin(uTime * 3.0 + hash(floor(starPos)) * 6.2831);
    float edgeFade = smoothstep(0.95, 0.25, distance2);
    float grains = pow(noise(starPos), 8.0) * twinkle * edgeFade;

    float opticalDepth = extinction * dust * (0.70 + 1.9 * grains);
    float stepTransmission = exp(-opticalDepth * stepLength);

    float sourceDensity = (density(point * 0.22, uTime) + density(point * 0.55, uTime)) * 0.5;
    float incident = exp(-extinction * sourceDensity * distanceToCore) / (0.28 + distance2);
    float scatteringAngle = dot(point, towardEye) / max(distanceToCore, 0.001);
    float phase = 0.52 + 0.42 * scatteringAngle * scatteringAngle + 0.25 * max(-scatteringAngle, 0.0);

    float coreGlow = exp(-8.5 * distance2) * 0.22 * stepLength;
    float hueMix = clamp(0.5 + 0.5 * sin(point.y * 2.8 + point.x * 2.2 - uTime * 0.45), 0.0, 1.0);
    vec3 nebulaTint = mix(mix(cosmicViolet, sparkColor, 0.58), warmGold, 0.38 * hueMix);
    vec3 grainTint = mix(sparkColor, vec3(1.0, 0.90, 0.68), 0.55);

    vec3 stepLight = (1.0 - stepTransmission) * incident * phase * nebulaTint
      + (coreGlow * mix(sparkColor, warmGold, 0.45))
      + (grains * (0.35 + dust) * 2.2 * stepLength * grainTint);
    scattered += viewTransmission * stepLight;
    viewTransmission *= stepTransmission;
  }

  float opacity = 1.0 - viewTransmission;
  vec3 color = sparkStrength * pulse * scattered / max(opacity, 0.0001);
  gl_FragColor = vec4(color, opacity);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}
