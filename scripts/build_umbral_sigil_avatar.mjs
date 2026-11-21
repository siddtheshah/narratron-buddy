import { mkdir, writeFile } from 'node:fs/promises';
import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';

// GLTFExporter assembles its binary container with the browser FileReader API.
globalThis.FileReader = class {
  async readAsArrayBuffer(blob) {
    this.result = await blob.arrayBuffer();
    this.onloadend();
  }
};

const TAU = Math.PI * 2;
const SEGMENTS = 160;

// Deterministic PRNG for organic hand-forged irregularity
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function wrapAngleDiff(a, b) {
  let d = (a - b) % TAU;
  if (d > Math.PI) d -= TAU;
  if (d < -Math.PI) d += TAU;
  return d;
}

function smoothstep(edge0, edge1, x) {
  const t = Math.max(0, Math.min(1, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

function generateSpikes(seed, count, config) {
  const rand = mulberry32(seed);
  const spikes = [];
  for (let i = 0; i < count; i++) {
    const baseAngle = (i / count) * TAU;
    const jitter = (rand() - 0.5) * (TAU / count) * 0.44;
    const isMajor = i % 2 === 0 || rand() > 0.62;
    const height = isMajor
      ? config.majorMin + rand() * (config.majorMax - config.majorMin)
      : config.minorMin + rand() * (config.minorMax - config.minorMin);
    const halfWidth = isMajor
      ? config.widthMin + rand() * (config.widthMax - config.widthMin)
      : (config.widthMin * 0.72) + rand() * ((config.widthMax - config.widthMin) * 0.65);
    const skew = (rand() - 0.5) * 0.42 + config.biasSkew;
    spikes.push({
      angle: (baseAngle + jitter + TAU) % TAU,
      height,
      halfWidth,
      skew,
    });
  }
  return spikes;
}

function evaluateSpikeField(theta, spikes) {
  let best = 0;
  for (const s of spikes) {
    let d = wrapAngleDiff(theta, s.angle);
    // Asymmetric blade-like skew so spikes hook slightly rather than looking like blunt cones
    const width = d >= 0 ? s.halfWidth * (1 + s.skew) : s.halfWidth * (1 - s.skew);
    const norm = Math.abs(d) / Math.max(0.02, width);
    if (norm < 1) {
      const val = s.height * Math.pow(1 - norm, 1.38);
      if (val > best) best = val;
    }
  }
  return best;
}

const RING_SPECS = [
  {
    name: 'Ring1',
    zOffset: 0.016,
    baseRadius: 0.258,
    baseWidth: 0.056,
    halfDepth: 0.044,
    eccentricity: [0.011, -0.008],
    harmonics: [
      { n: 2, amp: 0.012, phase: 0.4 },
      { n: 3, amp: 0.009, phase: 2.1 },
      { n: 5, amp: 0.005, phase: 4.3 },
    ],
    widthHarmonics: [
      { n: 1, amp: 0.20, phase: 0.9 },
      { n: 2, amp: 0.14, phase: 2.7 },
      { n: 4, amp: 0.08, phase: 1.2 },
    ],
    notches: [
      { angle: 1.15, depth: 0.22, width: 0.14 },
      { angle: 4.35, depth: 0.18, width: 0.12 },
    ],
    outerSpikes: generateSpikes(101, 7, {
      majorMin: 0.068,
      majorMax: 0.095,
      minorMin: 0.036,
      minorMax: 0.055,
      widthMin: 0.16,
      widthMax: 0.24,
      biasSkew: 0.14,
    }),
    innerSpikes: generateSpikes(102, 5, {
      majorMin: 0.038,
      majorMax: 0.056,
      minorMin: 0.022,
      minorMax: 0.034,
      widthMin: 0.18,
      widthMax: 0.26,
      biasSkew: -0.10,
    }),
    warpAxis: 0.55,
    warpAmp2: 0.026,
    warpAmp3: 0.014,
    warpZ: 0.014,
    expandDelta: 0.034,
    expandZ: 0.018,
    splitInnerRim: true,
    colorSeed: 1.7,
  },
  {
    name: 'Ring2',
    zOffset: -0.018,
    baseRadius: 0.438,
    baseWidth: 0.068,
    halfDepth: 0.048,
    eccentricity: [-0.016, 0.013],
    harmonics: [
      { n: 2, amp: 0.018, phase: 1.85 },
      { n: 3, amp: 0.013, phase: 0.5 },
      { n: 4, amp: 0.007, phase: 3.7 },
    ],
    widthHarmonics: [
      { n: 1, amp: 0.24, phase: 3.1 },
      { n: 2, amp: 0.15, phase: 0.8 },
      { n: 3, amp: 0.10, phase: 4.5 },
    ],
    notches: [
      { angle: 2.35, depth: 0.24, width: 0.13 },
      { angle: 5.20, depth: 0.19, width: 0.11 },
    ],
    outerSpikes: generateSpikes(201, 9, {
      majorMin: 0.082,
      majorMax: 0.115,
      minorMin: 0.042,
      minorMax: 0.064,
      widthMin: 0.13,
      widthMax: 0.20,
      biasSkew: -0.16,
    }),
    innerSpikes: generateSpikes(202, 7, {
      majorMin: 0.042,
      majorMax: 0.062,
      minorMin: 0.022,
      minorMax: 0.038,
      widthMin: 0.14,
      widthMax: 0.21,
      biasSkew: 0.12,
    }),
    warpAxis: 2.10,
    warpAmp2: 0.038,
    warpAmp3: 0.019,
    warpZ: -0.018,
    expandDelta: 0.048,
    expandZ: -0.020,
    splitInnerRim: false,
    colorSeed: 4.3,
  },
  {
    name: 'Ring3',
    zOffset: 0.018,
    baseRadius: 0.628,
    baseWidth: 0.072,
    halfDepth: 0.050,
    eccentricity: [0.019, 0.014],
    harmonics: [
      { n: 2, amp: 0.022, phase: 3.2 },
      { n: 3, amp: 0.016, phase: 4.9 },
      { n: 5, amp: 0.009, phase: 1.4 },
    ],
    widthHarmonics: [
      { n: 1, amp: 0.25, phase: 5.0 },
      { n: 2, amp: 0.16, phase: 2.2 },
      { n: 3, amp: 0.11, phase: 0.7 },
    ],
    notches: [
      { angle: 0.65, depth: 0.26, width: 0.12 },
      { angle: 3.70, depth: 0.20, width: 0.10 },
      { angle: 5.65, depth: 0.18, width: 0.10 },
    ],
    outerSpikes: generateSpikes(301, 11, {
      majorMin: 0.095,
      majorMax: 0.132,
      minorMin: 0.048,
      minorMax: 0.074,
      widthMin: 0.11,
      widthMax: 0.17,
      biasSkew: 0.17,
    }),
    innerSpikes: generateSpikes(302, 8, {
      majorMin: 0.045,
      majorMax: 0.068,
      minorMin: 0.024,
      minorMax: 0.040,
      widthMin: 0.12,
      widthMax: 0.18,
      biasSkew: -0.12,
    }),
    warpAxis: 3.65,
    warpAmp2: 0.046,
    warpAmp3: 0.022,
    warpZ: 0.020,
    expandDelta: 0.060,
    expandZ: 0.022,
    splitInnerRim: false,
    colorSeed: 7.9,
  },
  {
    name: 'Ring4',
    zOffset: -0.016,
    baseRadius: 0.818,
    baseWidth: 0.066,
    halfDepth: 0.044,
    eccentricity: [-0.022, -0.015],
    harmonics: [
      { n: 2, amp: 0.026, phase: 0.9 },
      { n: 3, amp: 0.020, phase: 2.8 },
      { n: 5, amp: 0.011, phase: 5.1 },
    ],
    widthHarmonics: [
      { n: 1, amp: 0.26, phase: 1.7 },
      { n: 2, amp: 0.18, phase: 4.1 },
      { n: 4, amp: 0.10, phase: 2.9 },
    ],
    notches: [
      { angle: 1.75, depth: 0.25, width: 0.11 },
      { angle: 4.65, depth: 0.23, width: 0.11 },
    ],
    outerSpikes: generateSpikes(401, 13, {
      majorMin: 0.135,
      majorMax: 0.195,
      minorMin: 0.068,
      minorMax: 0.108,
      widthMin: 0.095,
      widthMax: 0.155,
      biasSkew: -0.18,
    }),
    innerSpikes: generateSpikes(402, 9, {
      majorMin: 0.042,
      majorMax: 0.066,
      minorMin: 0.022,
      minorMax: 0.038,
      widthMin: 0.10,
      widthMax: 0.16,
      biasSkew: 0.11,
    }),
    warpAxis: 5.05,
    warpAmp2: 0.052,
    warpAmp3: 0.025,
    warpZ: -0.018,
    expandDelta: 0.072,
    expandZ: -0.020,
    splitInnerRim: false,
    colorSeed: 11.4,
  },
];

function sampleRingProfile(spec, theta, state) {
  let rMid = spec.baseRadius;
  for (const h of spec.harmonics) {
    rMid += h.amp * Math.cos(h.n * theta + h.phase);
  }
  let widthFactor = 1.0;
  for (const w of spec.widthHarmonics) {
    widthFactor += w.amp * Math.cos(w.n * theta + w.phase);
  }
  for (const notch of spec.notches) {
    const d = Math.abs(wrapAngleDiff(theta, notch.angle));
    if (d < notch.width) {
      const k = Math.cos((d / notch.width) * Math.PI * 0.5);
      widthFactor -= notch.depth * k * k;
    }
  }
  let width = Math.max(0.026, spec.baseWidth * widthFactor);
  let rOuter = rMid + width * 0.5;
  let rInner = rMid - width * 0.5;
  let zCenter = 0;
  let halfDepth = spec.halfDepth;

  // Subtle hand-hammered surface ripple along circumference
  const hammerWave = 0.0028 * Math.sin(13 * theta + spec.colorSeed)
    + 0.0020 * Math.cos(23 * theta - spec.colorSeed * 1.7);
  rOuter += hammerWave;
  rInner -= hammerWave * 0.6;

  if (state === 'spiky') {
    const outSpike = evaluateSpikeField(theta, spec.outerSpikes);
    const inSpike = evaluateSpikeField(theta, spec.innerSpikes);
    rOuter += outSpike;
    rInner = Math.max(0.145, rInner - inSpike);
    // Taper depth slightly at sharp spike tips like forged blades, thicken slightly along spine
    const spikeFactor = Math.min(1, outSpike / 0.12);
    halfDepth *= (1.0 - 0.24 * spikeFactor);
    zCenter += 0.009 * spikeFactor;
  } else if (state === 'warped') {
    const a = theta - spec.warpAxis;
    const pinch2 = spec.warpAmp2 * Math.cos(2 * a);
    const pinch3 = spec.warpAmp3 * Math.sin(3 * a + 0.7);
    const delta = pinch2 + pinch3;
    rOuter += delta * 1.12;
    rInner = Math.max(0.145, rInner + delta * 0.88);
    // Subtle 2.5D out-of-plane torsion wave
    zCenter += spec.warpZ * Math.sin(2 * a + 0.45);
    halfDepth *= (1.0 + 0.10 * Math.cos(2 * a));
  } else if (state === 'expanded') {
    const serration = 0.018 * Math.pow(Math.abs(Math.sin(9 * theta + spec.colorSeed)), 2.2);
    rOuter += spec.expandDelta + serration;
    rInner += spec.expandDelta * 1.08;
    zCenter += spec.expandZ;
    halfDepth *= 0.90;
  }

  // Clamp outer radius safely within orthographic frame [-1.16, 1.16]
  rOuter = Math.min(rOuter, 1.045);
  rInner = Math.min(rInner, rOuter - 0.022);

  const [ex, ey] = spec.eccentricity;
  const cos = Math.cos(theta);
  const sin = Math.sin(theta);

  const curWidth = rOuter - rInner;
  const bevel = Math.min(curWidth * 0.30, halfDepth * 0.46);
  const frontSlope = Math.min(0.008, halfDepth * 0.18);

  // 8-point forged iron chamfered cross-section in local (r, z).
  // Gently crowned front face (inner edge slightly recessed vs outer ridge) catches a soft
  // gradient of baleful light across the hammered gray iron instead of a harsh hairline wire.
  const profileRZ = [
    [rInner + bevel, zCenter + halfDepth - frontSlope], // 0: FI (Front Inner)
    [rOuter - bevel, zCenter + halfDepth],              // 1: FO (Front Outer)
    [rOuter, zCenter + halfDepth - bevel],              // 2: OF (Outer Front)
    [rOuter, zCenter - halfDepth + bevel],              // 3: OB (Outer Back)
    [rOuter - bevel, zCenter - halfDepth],              // 4: BO (Back Outer)
    [rInner + bevel, zCenter - halfDepth],              // 5: BI (Back Inner)
    [rInner, zCenter - halfDepth + bevel],              // 6: IB (Inner Back)
    [rInner, zCenter + halfDepth - bevel - frontSlope], // 7: IF (Inner Front)
  ];

  return profileRZ.map(([r, z]) => [ex + r * cos, ey + r * sin, z]);
}

// 8 strips around the 8-point profile:
// 0: Front face (0->1)
// 1: Front outer bevel (1->2)
// 2: Outer rim wall (2->3)
// 3: Back outer bevel (3->4)
// 4: Back face (4->5)
// 5: Back inner bevel (5->6)
// 6: Inner rim wall (6->7)
// 7: Front inner bevel (7->0)
function buildStripGeometryData(spec, stripIndices, state) {
  const ringProfiles = [];
  for (let i = 0; i < SEGMENTS; i++) {
    const theta = (i / SEGMENTS) * TAU;
    ringProfiles.push(sampleRingProfile(spec, theta, state));
  }

  const positions = [];
  const indices = [];
  let vertexOffset = 0;

  for (const strip of stripIndices) {
    const pA = strip;
    const pB = (strip + 1) % 8;

    // 2 vertices per angular slice (inner/left edge and outer/right edge of this bevel strip)
    for (let i = 0; i < SEGMENTS; i++) {
      const a = ringProfiles[i][pA];
      const b = ringProfiles[i][pB];
      positions.push(a[0], a[1], a[2]);
      positions.push(b[0], b[1], b[2]);
    }

    for (let i = 0; i < SEGMENTS; i++) {
      const next = (i + 1) % SEGMENTS;
      const v00 = vertexOffset + i * 2;
      const v01 = vertexOffset + i * 2 + 1;
      const v10 = vertexOffset + next * 2;
      const v11 = vertexOffset + next * 2 + 1;
      // Winding oriented outward from the ring cross-section
      indices.push(v00, v01, v11);
      indices.push(v00, v11, v10);
    }

    vertexOffset += SEGMENTS * 2;
  }

  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geom.setIndex(indices);
  geom.computeVertexNormals();
  return {
    positions: geom.getAttribute('position'),
    normals: geom.getAttribute('normal'),
    index: geom.getIndex(),
  };
}

function buildIronVertexColors(positions, stripIndices, colorSeed) {
  const recess = new THREE.Color(0x474e59);
  const mid = new THREE.Color(0x747d8a);
  const burnished = new THREE.Color(0xa6b0be);
  const color = new THREE.Color();
  const colors = [];

  const vertsPerStrip = SEGMENTS * 2;
  for (let sIdx = 0; sIdx < stripIndices.length; sIdx++) {
    const strip = stripIndices[sIdx];
    const isOuterFrontBevel = strip === 1;
    const isInnerFrontBevel = strip === 7;
    const isFrontFace = strip === 0;
    const isSideWall = strip === 2 || strip === 6;

    for (let v = 0; v < vertsPerStrip; v++) {
      const idx = sIdx * vertsPerStrip + v;
      const x = positions.getX(idx);
      const y = positions.getY(idx);
      const z = positions.getZ(idx);

      // Multi-frequency hammered dark gray iron texture
      const sx = x * 17.0 + colorSeed;
      const sy = y * 17.0 - colorSeed * 0.7;
      const sz = z * 29.0;
      const hammer = 0.50
        + 0.22 * Math.sin(2.4 * sx + 1.9 * sy - 1.3 * sz)
        + 0.16 * Math.sin(1.3 * sx - 3.3 * sy + 2.5 * sz)
        + 0.10 * Math.cos(4.1 * sx + 2.8 * sy + 1.9 * sz)
        + 0.06 * Math.sin(8.5 * sx - 6.7 * sy);

      let t = Math.max(0, Math.min(1, hammer));
      if (isOuterFrontBevel) {
        // Burnished forged iron edge wear along outer bevels
        color.lerpColors(mid, burnished, 0.35 + 0.55 * t);
      } else if (isInnerFrontBevel) {
        // Slightly darker forged inner bevel so BalefulLight illuminates it organically rather than neon-bright
        color.lerpColors(recess, mid, 0.28 + 0.52 * t);
      } else if (isFrontFace) {
        color.lerpColors(recess, mid, 0.28 + 0.72 * t);
        if (t > 0.68) {
          color.lerp(burnished, (t - 0.68) * 0.85);
        }
      } else if (isSideWall) {
        color.lerpColors(recess, mid, 0.18 + 0.52 * t);
      } else {
        color.lerpColors(recess, mid, 0.12 + 0.42 * t);
      }
      colors.push(color.r, color.g, color.b);
    }
  }
  return new THREE.Float32BufferAttribute(colors, 3);
}

function createRingMesh(spec, meshName, stripIndices, material) {
  const base = buildStripGeometryData(spec, stripIndices, 'base');
  const spiky = buildStripGeometryData(spec, stripIndices, 'spiky');
  const warped = buildStripGeometryData(spec, stripIndices, 'warped');
  const expanded = buildStripGeometryData(spec, stripIndices, 'expanded');

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', base.positions);
  geometry.setAttribute('normal', base.normals);
  geometry.setIndex(base.index);

  if (material.vertexColors) {
    geometry.setAttribute('color', buildIronVertexColors(base.positions, stripIndices, spec.colorSeed));
  }

  geometry.morphTargetsRelative = false;
  geometry.morphAttributes.position = [spiky.positions, warped.positions, expanded.positions];
  geometry.morphAttributes.normal = [spiky.normals, warped.normals, expanded.normals];

  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = meshName;
  mesh.updateMorphTargets();
  mesh.morphTargetInfluences = [0, 0, 0];
  return mesh;
}

// Materials
const forgedIronMat = new THREE.MeshStandardMaterial({
  name: 'ForgedIron',
  color: 0xffffff,
  vertexColors: true,
  metalness: 0.40,
  roughness: 0.48,
});

// Innermost ring inner throat material: dark gray iron that catches emissive bounce from the baleful core
const balefulRimMat = new THREE.MeshStandardMaterial({
  name: 'BalefulRim',
  color: 0x505864,
  emissive: 0x8fb399,
  emissiveIntensity: 0.34,
  metalness: 0.40,
  roughness: 0.42,
});

// Scene assembly
const root = new THREE.Group();
root.name = 'UmbralSigilScene';

// Camera is at (2.4, 1.9, 5.6) looking at (0, 0.28, 0) => yaw ~0.405 rad, pitch ~-0.260 rad.
// Face nearly straight at the camera (-0.17, 0.29) leaving ~6 deg oblique angle for subtle 2.5D depth.
const viewGroup = new THREE.Group();
viewGroup.name = 'SigilView';
viewGroup.position.set(0, 0.28, 0);
viewGroup.rotation.set(-0.17, 0.29, 0.0, 'YXZ');
root.add(viewGroup);

const rig = new THREE.Group();
rig.name = 'SigilRig';
viewGroup.add(rig);

// Shader anchors (behind and slightly in front of the 2.5D iron ring planes)
const backGlowAnchor = new THREE.Group();
backGlowAnchor.name = 'BackGlowAnchor';
backGlowAnchor.position.set(0, 0, -0.105);
rig.add(backGlowAnchor);

const frontWispAnchor = new THREE.Group();
frontWispAnchor.name = 'FrontWispAnchor';
frontWispAnchor.position.set(0, 0, 0.105);
rig.add(frontWispAnchor);

// Bodyless central baleful light source (no solid orb mesh!)
const balefulLight = new THREE.PointLight(0x8fb399, 2.8);
balefulLight.name = 'BalefulLight';
balefulLight.position.set(0, 0, 0.175);
rig.add(balefulLight);

// Build the 4 concentric irregular iron rings
const ringGroups = [];
const morphMeshes = [];

for (const spec of RING_SPECS) {
  const group = new THREE.Group();
  group.name = `${spec.name}Group`;
  group.position.set(0, 0, spec.zOffset);
  rig.add(group);
  ringGroups.push(group);

  if (spec.splitInnerRim) {
    const ironMesh = createRingMesh(spec, `${spec.name}Iron`, [0, 1, 2, 3, 4, 5], forgedIronMat);
    const rimMesh = createRingMesh(spec, `${spec.name}Rim`, [6, 7], balefulRimMat);
    group.add(ironMesh, rimMesh);
    morphMeshes.push({ ringIndex: 0, mesh: ironMesh }, { ringIndex: 0, mesh: rimMesh });
  } else {
    const ironMesh = createRingMesh(spec, `${spec.name}Iron`, [0, 1, 2, 3, 4, 5, 6, 7], forgedIronMat);
    group.add(ironMesh);
    morphMeshes.push({ ringIndex: ringGroups.length - 1, mesh: ironMesh });
  }
}

// Animation helpers
function sampleTimes(duration, steps) {
  return Array.from({ length: steps + 1 }, (_, i) => (i / steps) * duration);
}

function buildZSpinTrack(nodeName, times, angles) {
  const axis = new THREE.Vector3(0, 0, 1);
  let prev = null;
  const values = angles.flatMap((angle) => {
    const q = new THREE.Quaternion().setFromAxisAngle(axis, angle);
    if (prev && q.dot(prev) < 0) {
      q.set(-q.x, -q.y, -q.z, -q.w);
    }
    prev = q;
    return q.toArray();
  });
  return new THREE.QuaternionKeyframeTrack(`${nodeName}.quaternion`, times, values);
}

/**
 * Integrates an angular velocity profile omegaFn(t) over `times`, then scales it
 * so the total rotation equals `targetTurns * TAU` (ensuring seamless wrapping/crossfade).
 */
function integrateSpinAngles(times, omegaFn, targetTurns) {
  const raw = [0];
  for (let i = 1; i < times.length; i++) {
    const dt = times[i] - times[i - 1];
    const tMid = 0.5 * (times[i - 1] + times[i]);
    const w0 = omegaFn(times[i - 1]);
    const wMid = omegaFn(tMid);
    const w1 = omegaFn(times[i]);
    raw.push(raw[i - 1] + (dt / 6) * (w0 + 4 * wMid + w1));
  }
  const total = raw[raw.length - 1];
  if (targetTurns === null || Math.abs(total) < 1e-6) {
    return raw;
  }
  const scale = (targetTurns * TAU) / total;
  return raw.map((a) => a * scale);
}

function buildMorphTracks(times, weightFnPerRing) {
  return morphMeshes.map(({ ringIndex, mesh }) => {
    const values = times.flatMap((t) => weightFnPerRing(ringIndex, t));
    return new THREE.NumberKeyframeTrack(`${mesh.name}.morphTargetInfluences`, times, values);
  });
}

function buildRigPosTrack(times, fn) {
  const values = times.flatMap((t) => fn(t));
  return new THREE.VectorKeyframeTrack('SigilRig.position', times, values);
}

function buildRigQuatTrack(times, fn) {
  let prev = null;
  const values = times.flatMap((t) => {
    const [rx, ry, rz] = fn(t);
    const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(rx, ry, rz, 'YXZ'));
    if (prev && q.dot(prev) < 0) q.set(-q.x, -q.y, -q.z, -q.w);
    prev = q;
    return q.toArray();
  });
  return new THREE.QuaternionKeyframeTrack('SigilRig.quaternion', times, values);
}

function buildRigScaleTrack(times, fn) {
  const values = times.flatMap((t) => {
    const s = fn(t);
    return Array.isArray(s) ? s : [s, s, s];
  });
  return new THREE.VectorKeyframeTrack('SigilRig.scale', times, values);
}

// ---------------------------------------------------------------------------
// 1. Idle (12.0s loop): Slow, creeping counter-rotation & subtle breathing warp
// ---------------------------------------------------------------------------
const idleTimes = sampleTimes(12.0, 96);
const idleDirs = [1, -1, 1, -1];
const idleClip = new THREE.AnimationClip('Idle', 12.0, [
  ...RING_SPECS.map((spec, idx) => {
    const turns = idleDirs[idx] * (idx % 2 === 0 ? 1 : 1);
    const angles = integrateSpinAngles(
      idleTimes,
      (t) => 1.0 + 0.28 * Math.sin((t / 12.0) * TAU + idx * 1.3),
      turns,
    );
    return buildZSpinTrack(`${spec.name}Group`, idleTimes, angles);
  }),
  ...buildMorphTracks(idleTimes, (ringIndex, t) => {
    const p = (t / 12.0) * TAU;
    const spiky = 0.03 + 0.035 * (0.5 - 0.5 * Math.cos(p * 2 + ringIndex * 0.9));
    const warped = 0.06 + 0.11 * (0.5 - 0.5 * Math.cos(p + ringIndex * 1.1));
    const expanded = 0.02 * (0.5 - 0.5 * Math.cos(p + ringIndex * 0.5));
    return [spiky, warped, expanded];
  }),
  buildRigPosTrack(idleTimes, (t) => {
    const p = (t / 12.0) * TAU;
    return [0.004 * Math.sin(p), 0.014 * Math.sin(p), 0.006 * Math.cos(p)];
  }),
  buildRigQuatTrack(idleTimes, (t) => {
    const p = (t / 12.0) * TAU;
    return [0.03 * Math.sin(p), 0.04 * Math.cos(p), 0.015 * Math.sin(p * 2)];
  }),
]);

// ---------------------------------------------------------------------------
// 2. Omen (3.0s one-shot): Slows almost to a dead halt, warps wrong-angled,
//    then snaps into a brief eerie counter-twitch before settling
// ---------------------------------------------------------------------------
const omenTimes = sampleTimes(3.0, 75);
const omenDirs = [-1, 1, -1, 1];
const omenClip = new THREE.AnimationClip('Omen', 3.0, [
  ...RING_SPECS.map((spec, idx) => {
    const dir = omenDirs[idx];
    const angles = integrateSpinAngles(
      omenTimes,
      (t) => {
        // Creep slows to near-stillness around 0.9s, then twitches sharply at 1.45s
        const stall = 0.14 * (1 - smoothstep(0.15, 0.85, t));
        const twitch = 5.4 * Math.exp(-Math.pow((t - 1.50) / 0.18, 2));
        const recoil = -1.6 * Math.exp(-Math.pow((t - 1.98) / 0.16, 2));
        const tail = 0.18 * smoothstep(2.2, 2.9, t);
        return dir * (stall + twitch + recoil + tail);
      },
      null,
    );
    return buildZSpinTrack(`${spec.name}Group`, omenTimes, angles);
  }),
  ...buildMorphTracks(omenTimes, (ringIndex, t) => {
    const enter = smoothstep(0.0, 0.75, t);
    const exit = 1 - smoothstep(2.15, 3.0, t);
    const envelope = enter * exit;
    const twitchPulse = Math.exp(-Math.pow((t - 1.50) / 0.24, 2));
    const warped = Math.min(1, envelope * 0.88 + 0.14 * twitchPulse);
    const spiky = Math.min(1, 0.24 * twitchPulse + 0.08 * envelope);
    const expanded = 0.08 * Math.sin(t * 2.1 + ringIndex) * envelope;
    return [Math.max(0, spiky), Math.max(0, warped), Math.max(0, expanded)];
  }),
  buildRigQuatTrack(omenTimes, (t) => {
    const env = smoothstep(0.0, 0.65, t) * (1 - smoothstep(2.1, 3.0, t));
    const twitch = Math.exp(-Math.pow((t - 1.50) / 0.18, 2));
    return [0.07 * env + 0.04 * twitch, -0.10 * env, 0.05 * twitch];
  }),
  buildRigScaleTrack(omenTimes, (t) => {
    const env = smoothstep(0.0, 0.7, t) * (1 - smoothstep(2.1, 3.0, t));
    return 1.0 - 0.045 * env;
  }),
]);

// ---------------------------------------------------------------------------
// 3. Bristle (2.6s one-shot): Full jagged iron spike eruption with ratcheting
//    counter-rotation and tense metallic shudder
// ---------------------------------------------------------------------------
const bristleTimes = sampleTimes(2.6, 65);
const bristleDirs = [1, -1, 1, -1];
const bristleClip = new THREE.AnimationClip('Bristle', 2.6, [
  ...RING_SPECS.map((spec, idx) => {
    const dir = bristleDirs[idx];
    const speedMul = 1.25 - idx * 0.14;
    const angles = integrateSpinAngles(
      bristleTimes,
      (t) => {
        const kick1 = 5.2 * Math.exp(-Math.pow((t - 0.36) / 0.14, 2));
        const ratchet = 2.6 * Math.exp(-Math.pow((t - 1.15) / 0.24, 2));
        const settle = 1.4 * Math.exp(-Math.pow((t - 1.85) / 0.22, 2));
        return dir * speedMul * (0.25 + kick1 + ratchet + settle);
      },
      null,
    );
    return buildZSpinTrack(`${spec.name}Group`, bristleTimes, angles);
  }),
  ...buildMorphTracks(bristleTimes, (ringIndex, t) => {
    const snapIn = smoothstep(0.06, 0.34, t);
    const retract = 1 - smoothstep(1.95, 2.58, t);
    const hold = snapIn * retract;
    const shudder = 0.08 * Math.sin(t * 28.0 + ringIndex * 1.4) * hold;
    const spiky = Math.max(0, Math.min(1, hold * 0.96 + shudder));
    const warped = 0.16 * hold;
    const expanded = 0.26 * hold;
    return [spiky, warped, expanded];
  }),
  buildRigPosTrack(bristleTimes, (t) => {
    const hold = smoothstep(0.1, 0.35, t) * (1 - smoothstep(1.95, 2.55, t));
    return [0.008 * Math.sin(t * 34.0) * hold, 0.006 * Math.cos(t * 29.0) * hold, 0.02 * hold];
  }),
  buildRigScaleTrack(bristleTimes, (t) => {
    const snap = smoothstep(0.08, 0.32, t) * (1 - smoothstep(1.95, 2.58, t));
    return 1.0 + 0.03 * snap;
  }),
]);

// ---------------------------------------------------------------------------
// 4. Frenzy (3.2s one-shot): SIGNATURE slow-to-suddenly-fast spiky whirl!
//    Deceptively slow creep (0..0.85s) -> violent sudden high-speed spin +
//    full spike eruption (0.85..2.25s) -> hard shuddering brake (2.25..3.2s)
// ---------------------------------------------------------------------------
const frenzyTimes = sampleTimes(3.2, 128); // 25ms step => max step angle < 0.65 rad even at 24 rad/s!
const frenzyTargetTurns = [4, -3, 3, -2];
const frenzyClip = new THREE.AnimationClip('Frenzy', 3.2, [
  ...RING_SPECS.map((spec, idx) => {
    const turns = frenzyTargetTurns[idx];
    const angles = integrateSpinAngles(
      frenzyTimes,
      (t) => {
        // Deceptively slow creeping lead-in (0.0 -> 0.85s), then VIOLENT sudden surge!
        const slowCreep = 0.45 * (1 - smoothstep(0.70, 0.90, t));
        const surgeUp = smoothstep(0.82, 1.02, t);
        const surgeDown = 1 - smoothstep(2.12, 2.58, t);
        const fastBurst = 19.5 * surgeUp * surgeDown;
        const tail = 0.35 * smoothstep(2.60, 3.15, t);
        return slowCreep + fastBurst + tail;
      },
      turns,
    );
    return buildZSpinTrack(`${spec.name}Group`, frenzyTimes, angles);
  }),
  ...buildMorphTracks(frenzyTimes, (ringIndex, t) => {
    // Coiled & warped during the slow lead-in, then snaps violently spiky during the fast whirl
    const coil = smoothstep(0.0, 0.72, t) * (1 - smoothstep(0.84, 1.02, t));
    const burst = smoothstep(0.82, 0.98, t) * (1 - smoothstep(2.28, 3.12, t));
    const tremor = 0.07 * Math.sin(t * 42.0 + ringIndex * 1.7) * burst;
    const spiky = Math.max(0, Math.min(1, 0.08 * coil + burst * 0.98 + tremor));
    const warped = Math.max(0, Math.min(1, 0.38 * coil + 0.18 * burst));
    const expanded = Math.max(0, Math.min(1, 0.42 * burst));
    return [spiky, warped, expanded];
  }),
  buildRigPosTrack(frenzyTimes, (t) => {
    const burst = smoothstep(0.85, 1.02, t) * (1 - smoothstep(2.15, 2.75, t));
    return [
      0.014 * Math.sin(t * 53.0) * burst,
      0.014 * Math.cos(t * 47.0) * burst,
      0.025 * burst,
    ];
  }),
  buildRigScaleTrack(frenzyTimes, (t) => {
    const coil = smoothstep(0.0, 0.78, t) * (1 - smoothstep(0.84, 1.00, t));
    const burst = smoothstep(0.84, 1.00, t) * (1 - smoothstep(2.25, 3.10, t));
    return 1.0 - 0.06 * coil + 0.04 * burst;
  }),
  buildRigQuatTrack(frenzyTimes, (t) => {
    const burst = smoothstep(0.85, 1.02, t) * (1 - smoothstep(2.20, 2.90, t));
    return [0.06 * Math.sin(t * 21.0) * burst, 0.08 * Math.cos(t * 17.0) * burst, 0];
  }),
]);

// ---------------------------------------------------------------------------
// 5. Dread (6.0s loop): Sustained eerie stalking mood — unsteady creeping spin
//    punctuated by two sudden whipping bursts, half-spiky & warped
// ---------------------------------------------------------------------------
const dreadTimes = sampleTimes(6.0, 150);
const dreadTargetTurns = [3, -3, 2, -2];
const dreadClip = new THREE.AnimationClip('Dread', 6.0, [
  ...RING_SPECS.map((spec, idx) => {
    const turns = dreadTargetTurns[idx];
    const angles = integrateSpinAngles(
      dreadTimes,
      (t) => {
        const base = 0.55;
        const burst1 = 10.5 * Math.exp(-Math.pow((t - 1.55) / 0.24, 2));
        const burst2 = 12.0 * Math.exp(-Math.pow((t - 4.45) / 0.22, 2));
        return base + burst1 + burst2;
      },
      turns,
    );
    return buildZSpinTrack(`${spec.name}Group`, dreadTimes, angles);
  }),
  ...buildMorphTracks(dreadTimes, (ringIndex, t) => {
    const p = (t / 6.0) * TAU;
    const burst1 = Math.exp(-Math.pow((t - 1.55) / 0.34, 2));
    const burst2 = Math.exp(-Math.pow((t - 4.45) / 0.32, 2));
    const spikeSurge = Math.max(burst1, burst2);
    const spiky = Math.min(1, 0.40 + 0.50 * spikeSurge + 0.06 * Math.sin(p * 2 + ringIndex));
    const warped = Math.min(1, 0.58 + 0.26 * (0.5 + 0.5 * Math.sin(p + ringIndex * 0.9)));
    const expanded = Math.min(1, 0.10 + 0.22 * spikeSurge);
    return [spiky, warped, expanded];
  }),
  buildRigQuatTrack(dreadTimes, (t) => {
    const p = (t / 6.0) * TAU;
    return [0.055 * Math.sin(p), -0.075 * Math.cos(p), 0.025 * Math.sin(p * 2)];
  }),
]);

// ---------------------------------------------------------------------------
// 6. Revelation (2.8s one-shot): Expanding harmonic bloom & accelerating flare
// ---------------------------------------------------------------------------
const revTimes = sampleTimes(2.8, 70);
const revTargetTurns = [2, -2, 1, -1];
const revelationClip = new THREE.AnimationClip('Revelation', 2.8, [
  ...RING_SPECS.map((spec, idx) => {
    const turns = revTargetTurns[idx];
    const angles = integrateSpinAngles(
      revTimes,
      (t) => 0.4 + 6.8 * Math.exp(-Math.pow((t - 1.15) / 0.48, 2)),
      turns,
    );
    return buildZSpinTrack(`${spec.name}Group`, revTimes, angles);
  }),
  ...buildMorphTracks(revTimes, (ringIndex, t) => {
    const bloom = smoothstep(0.08, 0.72, t) * (1 - smoothstep(1.80, 2.75, t));
    const peak = Math.exp(-Math.pow((t - 1.15) / 0.45, 2));
    const spiky = Math.min(1, 0.36 * peak + 0.10 * bloom);
    const warped = 0.04 * (1 - bloom);
    const expanded = Math.min(1, 0.92 * bloom + 0.10 * peak);
    return [spiky, warped, expanded];
  }),
  buildRigScaleTrack(revTimes, (t) => {
    const bloom = smoothstep(0.08, 0.72, t) * (1 - smoothstep(1.80, 2.75, t));
    return 1.0 + 0.045 * bloom;
  }),
  buildRigQuatTrack(revTimes, (t) => {
    const bloom = smoothstep(0.08, 0.72, t) * (1 - smoothstep(1.80, 2.75, t));
    return [-0.06 * bloom, 0.07 * bloom, 0];
  }),
]);

// ---------------------------------------------------------------------------
// 7. Shudder (1.8s one-shot — click/activate reaction): Instant whip-snap
//    spiky recoil like snapping iron jaws
// ---------------------------------------------------------------------------
const shudderTimes = sampleTimes(1.8, 72);
const shudderTurns = [2, -2, 1, -1];
const shudderClip = new THREE.AnimationClip('Shudder', 1.8, [
  ...RING_SPECS.map((spec, idx) => {
    const turns = shudderTurns[idx];
    const angles = integrateSpinAngles(
      shudderTimes,
      (t) => {
        const snap = 18.0 * Math.exp(-Math.pow((t - 0.24) / 0.14, 2));
        const echo = 4.0 * Math.exp(-Math.pow((t - 0.78) / 0.16, 2));
        return 0.25 + snap + echo;
      },
      turns,
    );
    return buildZSpinTrack(`${spec.name}Group`, shudderTimes, angles);
  }),
  ...buildMorphTracks(shudderTimes, (ringIndex, t) => {
    const snap1 = Math.exp(-Math.pow((t - 0.24) / 0.18, 2));
    const snap2 = 0.85 * Math.exp(-Math.pow((t - 0.76) / 0.22, 2));
    const endFade = 1 - smoothstep(1.30, 1.78, t);
    const spiky = Math.min(1, Math.max(snap1, snap2) * endFade);
    const warped = 0.28 * snap2 * endFade;
    const expanded = 0.35 * snap1 * endFade;
    return [spiky, warped, expanded];
  }),
  buildRigPosTrack(shudderTimes, (t) => {
    const kick = Math.exp(-Math.pow((t - 0.22) / 0.14, 2));
    return [0.012 * Math.sin(t * 58.0) * kick, 0.010 * Math.cos(t * 51.0) * kick, 0.024 * kick];
  }),
  buildRigScaleTrack(shudderTimes, (t) => {
    const kick = Math.exp(-Math.pow((t - 0.22) / 0.14, 2));
    return 1.0 + 0.05 * kick;
  }),
]);

const clips = [
  idleClip,
  omenClip,
  bristleClip,
  frenzyClip,
  dreadClip,
  revelationClip,
  shudderClip,
];

const exported = await new GLTFExporter().parseAsync(root, { binary: true, animations: clips });
const glbBuffer = Buffer.from(exported);

const targets = [
  new URL('../adventures/umbral-dungeon/avatars/umbral-sigil/model.glb', import.meta.url),
  new URL('../avatars/umbral-sigil/model.glb', import.meta.url),
];

for (const target of targets) {
  await mkdir(new URL('./', target), { recursive: true });
  await writeFile(target, glbBuffer);
}

console.log(`Built umbral-sigil model.glb (${glbBuffer.byteLength} bytes, ${clips.length} clips)`);
