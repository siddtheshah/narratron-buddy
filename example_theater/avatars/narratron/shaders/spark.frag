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

float density(vec3 point, float t) {
  float envelope = max(1.0 - dot(point, point), 0.0);
  envelope = envelope * envelope * envelope;
  vec3 drift = point * 4.5 + vec3(sin(t * 0.7) * 0.6, -t * 0.42, cos(t * 0.6) * 0.6);
  float wisps = noise(drift + vec3(2.1, 5.7, 3.4));
  return envelope * mix(0.14, 1.25, smoothstep(0.24, 0.82, wisps));
}

void main() {
  const int steps = 20;
  const float extinction = 0.65;
  vec3 towardEye = normalize(viewDirection);
  vec3 rayOrigin = surface - towardEye * dot(surface, towardEye);
  float halfLength = sqrt(max(1.0 - dot(rayOrigin, rayOrigin), 0.0));
  float stepLength = 2.0 * halfLength / float(steps);
  float viewTransmission = 1.0;
  vec3 scattered = vec3(0.0);
  float pulse = 1.0 + 0.9 * clamp(uActionPulse, 0.0, 1.5);

  for (int step = 0; step < steps; step++) {
    float offset = halfLength - (float(step) + 0.5) * stepLength;
    vec3 point = rayOrigin + towardEye * offset;
    float distance2 = dot(point, point);
    float distanceToSpark = sqrt(distance2);
    float dust = density(point, uTime);
    vec3 grainSample = point * 18.0 + vec3(3.1, -uTime * 1.4, 2.7);
    float grains = pow(noise(grainSample), 6.2) * smoothstep(0.95, 0.20, distance2);
    float opticalDepth = extinction * dust * (0.65 + 2.4 * grains);
    float stepTransmission = exp(-opticalDepth * stepLength);

    float sourceDensity = (density(point * 0.2, uTime) + density(point * 0.55, uTime)) * 0.5;
    float incident = exp(-extinction * sourceDensity * distanceToSpark) / (0.28 + distance2);
    float scatteringAngle = dot(point, towardEye) / max(distanceToSpark, 0.001);
    float phase = 0.55 + 0.4 * scatteringAngle * scatteringAngle + 0.25 * max(-scatteringAngle, 0.0);
    float sparkCore = exp(-34.0 * distance2) * 0.45 * stepLength;

    vec3 glowTint = mix(sparkColor, vec3(1.0, 0.84, 0.46), 0.30 + 0.50 * grains);
    scattered += viewTransmission * ((1.0 - stepTransmission) * incident * phase + sparkCore + grains * 1.4 * stepLength) * glowTint;
    viewTransmission *= stepTransmission;
  }

  float opacity = 1.0 - viewTransmission;
  gl_FragColor = vec4(sparkStrength * pulse * scattered / max(opacity, 0.0001), opacity);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}
