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
    const jitter = (rand() - 0.5) * (TAU / count) * 0.42;
    const isMajor = i % 2 === 0 || rand() > 0.6;
    const height = isMajor
      ? config.majorMin + rand() * (config.majorMax - config.majorMin)
      : config.minorMin + rand() * (config.minorMax - config.minorMin);
    const halfWidth = isMajor
      ? config.widthMin + rand() * (config.widthMax - config.widthMin)
      : config.widthMin * 0.72 + rand() * ((config.widthMax - config.widthMin) * 0.65);
    const skew = (rand() - 0.5) * 0.4 + config.biasSkew;
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
    const d = wrapAngleDiff(theta, s.angle);
    const width = d >= 0 ? s.halfWidth * (1 + s.skew) : s.halfWidth * (1 - s.skew);
    const norm = Math.abs(d) / Math.max(0.02, width);
    if (norm < 1) {
      const val = s.height * Math.pow(1 - norm, 1.35);
      if (val > best) best = val;
    }
  }
  return best;
}

// Compute irregular polygon radius factor for the Angular/Sigil morph target
function polygonWarp(theta, sides, rotOffset, sharpness, cornerScale) {
  const sector = TAU / sides;
  let local = ((theta - rotOffset) % sector + sector) % sector;
  local -= sector * 0.5;
  const flatCos = Math.cos(sector * 0.5) / Math.max(0.35, Math.cos(local));
  // Subtle concave scalloping along flats + pronounced horned vertices at corners
  const cornerDist = Math.abs(Math.abs(local) - sector * 0.5) / (sector * 0.5);
  const horn = cornerScale * Math.pow(Math.max(0, 1 - cornerDist * 2.4), 1.6);
  return 1.0 + sharpness * (flatCos - 1.0) + horn;
}

// Two bold, sculptural concentric forged-iron bands broken into interlocking arc segments.
const RING_SPECS = [
  {
    name: 'InnerRing',
    zOffset: 0.018,
    baseRadius: 0.345,
    baseWidth: 0.088,
    halfDepth: 0.048,
    eccentricity: [0.016, -0.012],
    // 4 irregular arc segments that interlock seamlessly at rest
    segmentAngles: [0.18, 1.62, 3.32, 4.86],
    segmentSlices: 40,
    seamGap: 0.005,
    harmonics: [
      { n: 2, amp: 0.018, phase: 0.45 },
      { n: 3, amp: 0.013, phase: 2.15 },
      { n: 5, amp: 0.007, phase: 4.25 },
    ],
    widthHarmonics: [
      { n: 1, amp: 0.22, phase: 0.9 },
      { n: 2, amp: 0.14, phase: 2.7 },
      { n: 3, amp: 0.09, phase: 4.9 },
    ],
    notches: [
      { angle: 0.92, depth: 0.22, width: 0.14 },
      { angle: 4.12, depth: 0.20, width: 0.13 },
    ],
    outerSpikes: generateSpikes(107, 8, {
      majorMin: 0.105,
      majorMax: 0.148,
      minorMin: 0.052,
      minorMax: 0.082,
      widthMin: 0.14,
      widthMax: 0.22,
      biasSkew: 0.16,
    }),
    innerSpikes: generateSpikes(108, 6, {
      majorMin: 0.068,
      majorMax: 0.098,
      minorMin: 0.036,
      minorMax: 0.056,
      widthMin: 0.16,
      widthMax: 0.24,
      biasSkew: -0.14,
    }),
    // Ocular (narrowed eldritch eye/almond) morph parameters
    ocularAxis: 1.42, // slightly canted vertical slit
    ocularStretchX: 1.24,
    ocularSquashY: 0.68,
    ocularPointAmp: 0.075,
    ocularZ: 0.016,
    // Angular (wrong-angled occult polygon) morph parameters
    polySides: 4,
    polyRot: 0.38,
    polySharpness: 0.65,
    polyHorn: 0.075,
    polyZ: 0.020,
    splitInnerRim: true,
    colorSeed: 2.1,
  },
  {
    name: 'OuterRing',
    zOffset: -0.018,
    baseRadius: 0.680,
    baseWidth: 0.096,
    halfDepth: 0.052,
    eccentricity: [-0.022, 0.015],
    // 5 irregular arc segments (coprime with 4 so seams never align symmetrically!)
    segmentAngles: [0.42, 1.56, 2.94, 4.18, 5.42],
    segmentSlices: 36,
    seamGap: 0.005,
    harmonics: [
      { n: 2, amp: 0.026, phase: 2.05 },
      { n: 3, amp: 0.020, phase: 0.65 },
      { n: 5, amp: 0.011, phase: 3.85 },
    ],
    widthHarmonics: [
      { n: 1, amp: 0.24, phase: 3.2 },
      { n: 2, amp: 0.16, phase: 0.9 },
      { n: 3, amp: 0.11, phase: 4.4 },
    ],
    notches: [
      { angle: 2.25, depth: 0.24, width: 0.12 },
      { angle: 4.82, depth: 0.22, width: 0.11 },
      { angle: 0.95, depth: 0.18, width: 0.11 },
    ],
    outerSpikes: generateSpikes(207, 11, {
      majorMin: 0.155,
      majorMax: 0.225,
      minorMin: 0.078,
      minorMax: 0.124,
      widthMin: 0.105,
      widthMax: 0.17,
      biasSkew: -0.18,
    }),
    innerSpikes: generateSpikes(208, 9, {
      majorMin: 0.072,
      majorMax: 0.112,
      minorMin: 0.036,
      minorMax: 0.062,
      widthMin: 0.115,
      widthMax: 0.18,
      biasSkew: 0.14,
    }),
    ocularAxis: 1.48,
    ocularStretchX: 1.20,
    ocularSquashY: 0.72,
    ocularPointAmp: 0.115,
    ocularZ: -0.018,
    polySides: 5,
    polyRot: -0.22,
    polySharpness: 0.78,
    polyHorn: 0.095,
    polyZ: -0.022,
    splitInnerRim: false,
    colorSeed: 7.4,
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
  const width = Math.max(0.038, spec.baseWidth * widthFactor);
  let rOuter = rMid + width * 0.5;
  let rInner = rMid - width * 0.5;
  let zCenter = 0;
  let halfDepth = spec.halfDepth;

  // Subtle hand-hammered surface ripple along circumference
  const hammerWave =
    0.0032 * Math.sin(13 * theta + spec.colorSeed) +
    0.0022 * Math.cos(21 * theta - spec.colorSeed * 1.7);
  rOuter += hammerWave;
  rInner -= hammerWave * 0.6;

  let sx = 1.0;
  let sy = 1.0;
  let rotTransform = 0.0;

  if (state === 'spiky') {
    const outSpike = evaluateSpikeField(theta, spec.outerSpikes);
    const inSpike = evaluateSpikeField(theta, spec.innerSpikes);
    rOuter += outSpike;
    rInner = Math.max(0.155, rInner - inSpike);
    const spikeFactor = Math.min(1, outSpike / 0.14);
    halfDepth *= 1.0 - 0.26 * spikeFactor;
    zCenter += 0.011 * spikeFactor;
  } else if (state === 'ocular') {
    // Morph into a dramatic, uncanny almond/slit eye or pinched diamond lozenge
    rotTransform = spec.ocularAxis;
    sx = spec.ocularStretchX;
    sy = spec.ocularSquashY;
    const localA = theta - spec.ocularAxis;
    // Pointed almond tips along localA = 0 and pi
    const tipSharp = Math.pow(Math.abs(Math.cos(localA)), 4.5);
    const pinchSide = Math.pow(Math.abs(Math.sin(localA)), 2.2);
    rOuter += spec.ocularPointAmp * tipSharp - 0.018 * pinchSide;
    rInner = Math.max(0.145, rInner + spec.ocularPointAmp * 0.58 * tipSharp - 0.012 * pinchSide);
    zCenter += spec.ocularZ * Math.cos(2 * localA);
    halfDepth *= 1.0 + 0.14 * pinchSide - 0.16 * tipSharp;
  } else if (state === 'angular') {
    // Morph into a severe, wrong-angled faceted occult polygon with horned vertices
    const pFactor = polygonWarp(
      theta,
      spec.polySides,
      spec.polyRot,
      spec.polySharpness,
      spec.polyHorn,
    );
    const targetMid = spec.baseRadius * pFactor;
    const delta = targetMid - rMid;
    rOuter += delta * 1.06;
    rInner = Math.max(0.155, rInner + delta * 0.94);
    const wave = Math.sin(spec.polySides * (theta - spec.polyRot));
    zCenter += spec.polyZ * wave;
    halfDepth *= 1.0 + 0.08 * Math.cos(spec.polySides * (theta - spec.polyRot));
  }

  rOuter = Math.min(rOuter, 0.96);
  rInner = Math.min(rInner, rOuter - 0.032);

  const [ex, ey] = spec.eccentricity;
  const curWidth = rOuter - rInner;
  const bevel = Math.min(curWidth * 0.28, halfDepth * 0.45);
  const frontSlope = Math.min(0.009, halfDepth * 0.18);

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

  const cos = Math.cos(theta);
  const sin = Math.sin(theta);

  return profileRZ.map(([r, z]) => {
    let x = ex + r * cos;
    let y = ey + r * sin;
    if (rotTransform !== 0) {
      const c = Math.cos(-rotTransform);
      const s = Math.sin(-rotTransform);
      const lx = (x * c - y * s) * sx;
      const ly = (x * s + y * c) * sy;
      const rc = Math.cos(rotTransform);
      const rs = Math.sin(rotTransform);
      x = lx * rc - ly * rs;
      y = lx * rs + ly * rc;
    }
    return [x, y, z];
  });
}

/**
 * Builds watertight geometry for a single forged-iron arc segment spanning [thetaStart, thetaEnd],
 * including crisp beveled side strips and optional triangulated end-caps.
 */
function buildSegmentGeometryData(spec, thetaStart, thetaEnd, stripIndices, includeCaps, state) {
  const slices = spec.segmentSlices;
  const ringProfiles = [];
  for (let i = 0; i <= slices; i++) {
    const t = i / slices;
    const theta = thetaStart + t * (thetaEnd - thetaStart);
    ringProfiles.push(sampleRingProfile(spec, theta, state));
  }

  const positions = [];
  const indices = [];
  const stripMeta = []; // records { strip, count } for vertex coloring
  let vertexOffset = 0;

  for (const strip of stripIndices) {
    const pA = strip;
    const pB = (strip + 1) % 8;

    for (let i = 0; i <= slices; i++) {
      const a = ringProfiles[i][pA];
      const b = ringProfiles[i][pB];
      positions.push(a[0], a[1], a[2]);
      positions.push(b[0], b[1], b[2]);
    }

    for (let i = 0; i < slices; i++) {
      const v00 = vertexOffset + i * 2;
      const v01 = vertexOffset + i * 2 + 1;
      const v10 = vertexOffset + (i + 1) * 2;
      const v11 = vertexOffset + (i + 1) * 2 + 1;
      indices.push(v00, v01, v11);
      indices.push(v00, v11, v10);
    }

    const vertCount = (slices + 1) * 2;
    stripMeta.push({ strip, count: vertCount });
    vertexOffset += vertCount;
  }

  if (includeCaps) {
    // Start cap (slice 0) and end cap (slice = slices) so fractured iron pieces are solid & 2.5D!
    const capSpecs = [
      { profile: ringProfiles[0], reverse: false },
      { profile: ringProfiles[slices], reverse: true },
    ];
    for (const { profile, reverse } of capSpecs) {
      let cx = 0;
      let cy = 0;
      let cz = 0;
      for (const pt of profile) {
        cx += pt[0];
        cy += pt[1];
        cz += pt[2];
      }
      cx /= 8;
      cy /= 8;
      cz /= 8;

      const centerIdx = vertexOffset;
      positions.push(cx, cy, cz);
      for (const pt of profile) {
        positions.push(pt[0], pt[1], pt[2]);
      }

      for (let k = 0; k < 8; k++) {
        const a = centerIdx + 1 + k;
        const b = centerIdx + 1 + ((k + 1) % 8);
        if (reverse) {
          indices.push(centerIdx, a, b);
        } else {
          indices.push(centerIdx, b, a);
        }
      }
      stripMeta.push({ strip: 8, count: 9 }); // strip 8 = forged fracture end-cap
      vertexOffset += 9;
    }
  }

  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geom.setIndex(indices);
  geom.computeVertexNormals();
  return {
    positions: geom.getAttribute('position'),
    normals: geom.getAttribute('normal'),
    index: geom.getIndex(),
    stripMeta,
  };
}

function buildIronVertexColors(positions, stripMeta, colorSeed) {
  // Darker, heavy weathered gray forged iron palette
  const recess = new THREE.Color(0x3a404a);
  const mid = new THREE.Color(0x636c78);
  const burnished = new THREE.Color(0x909aa8);
  const fractureEdge = new THREE.Color(0x7c8593);
  const color = new THREE.Color();
  const colors = [];

  let idx = 0;
  for (const { strip, count } of stripMeta) {
    const isOuterFrontBevel = strip === 1;
    const isInnerFrontBevel = strip === 7;
    const isFrontFace = strip === 0;
    const isSideWall = strip === 2 || strip === 6;
    const isCap = strip === 8;

    for (let v = 0; v < count; v++) {
      const x = positions.getX(idx);
      const y = positions.getY(idx);
      const z = positions.getZ(idx);
      idx++;

      const sx = x * 17.5 + colorSeed;
      const sy = y * 17.5 - colorSeed * 0.7;
      const sz = z * 28.0;
      const hammer =
        0.5 +
        0.22 * Math.sin(2.4 * sx + 1.9 * sy - 1.3 * sz) +
        0.16 * Math.sin(1.3 * sx - 3.3 * sy + 2.5 * sz) +
        0.10 * Math.cos(4.1 * sx + 2.8 * sy + 1.9 * sz) +
        0.06 * Math.sin(8.5 * sx - 6.7 * sy);
      const t = Math.max(0, Math.min(1, hammer));

      if (isCap) {
        // Freshly fractured forged iron cross-section
        color.lerpColors(recess, fractureEdge, 0.35 + 0.5 * t);
      } else if (isOuterFrontBevel) {
        color.lerpColors(mid, burnished, 0.3 + 0.55 * t);
      } else if (isInnerFrontBevel) {
        color.lerpColors(recess, mid, 0.25 + 0.5 * t);
      } else if (isFrontFace) {
        color.lerpColors(recess, mid, 0.26 + 0.74 * t);
        if (t > 0.7) {
          color.lerp(burnished, (t - 0.7) * 0.75);
        }
      } else if (isSideWall) {
        color.lerpColors(recess, mid, 0.16 + 0.5 * t);
      } else {
        color.lerpColors(recess, mid, 0.1 + 0.4 * t);
      }
      colors.push(color.r, color.g, color.b);
    }
  }
  return new THREE.Float32BufferAttribute(colors, 3);
}

function createSegmentMesh(spec, meshName, thetaStart, thetaEnd, stripIndices, includeCaps, material) {
  const base = buildSegmentGeometryData(spec, thetaStart, thetaEnd, stripIndices, includeCaps, 'base');
  const spiky = buildSegmentGeometryData(spec, thetaStart, thetaEnd, stripIndices, includeCaps, 'spiky');
  const ocular = buildSegmentGeometryData(spec, thetaStart, thetaEnd, stripIndices, includeCaps, 'ocular');
  const angular = buildSegmentGeometryData(spec, thetaStart, thetaEnd, stripIndices, includeCaps, 'angular');

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', base.positions);
  geometry.setAttribute('normal', base.normals);
  geometry.setIndex(base.index);

  if (material.vertexColors) {
    geometry.setAttribute('color', buildIronVertexColors(base.positions, base.stripMeta, spec.colorSeed));
  }

  geometry.morphTargetsRelative = false;
  geometry.morphAttributes.position = [spiky.positions, ocular.positions, angular.positions];
  geometry.morphAttributes.normal = [spiky.normals, ocular.normals, angular.normals];

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
  metalness: 0.44,
  roughness: 0.48,
});

// Dim, restrained inner-throat bounce material on the innermost band
const balefulRimMat = new THREE.MeshStandardMaterial({
  name: 'BalefulRim',
  color: 0x454c57,
  emissive: 0x6f967c,
  emissiveIntensity: 0.12,
  metalness: 0.42,
  roughness: 0.44,
});

// Scene assembly
const root = new THREE.Group();
root.name = 'UmbralSigilScene';

// Camera is at (2.4, 1.9, 5.6) looking at (0, 0.28, 0).
// Face nearly straight at camera with ~6 deg oblique angle for subtle 2.5D forged depth.
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
backGlowAnchor.position.set(0, 0, -0.11);
rig.add(backGlowAnchor);

const frontWispAnchor = new THREE.Group();
frontWispAnchor.name = 'FrontWispAnchor';
frontWispAnchor.position.set(0, 0, 0.11);
rig.add(frontWispAnchor);

// Restrained, dim bodyless central baleful light source (no solid orb mesh!)
const balefulLight = new THREE.PointLight(0x6f967c, 1.25);
balefulLight.name = 'BalefulLight';
balefulLight.position.set(0, 0, 0.145);
rig.add(balefulLight);

// Build the 2 concentric bands, each composed of interlocking iron arc segments
const segmentEntries = []; // { ringIndex, segIndex, groupName, midAngle, dirSign, meshes }
const morphMeshes = [];

for (let ringIndex = 0; ringIndex < RING_SPECS.length; ringIndex++) {
  const spec = RING_SPECS[ringIndex];
  const ringGroup = new THREE.Group();
  ringGroup.name = `${spec.name}Group`;
  ringGroup.position.set(0, 0, spec.zOffset);
  rig.add(ringGroup);

  const starts = spec.segmentAngles;
  const segCount = starts.length;

  for (let segIndex = 0; segIndex < segCount; segIndex++) {
    const rawStart = starts[segIndex];
    const rawEnd = segIndex + 1 < segCount ? starts[segIndex + 1] : starts[0] + TAU;
    const thetaStart = rawStart + spec.seamGap * 0.5;
    const thetaEnd = rawEnd - spec.seamGap * 0.5;
    const midAngle = 0.5 * (thetaStart + thetaEnd);

    const segGroup = new THREE.Group();
    const segName = `${spec.name}Seg${segIndex}`;
    segGroup.name = segName;
    ringGroup.add(segGroup);

    const meshes = [];
    if (spec.splitInnerRim) {
      const ironMesh = createSegmentMesh(
        spec,
        `${segName}Iron`,
        thetaStart,
        thetaEnd,
        [0, 1, 2, 3, 4, 5],
        true,
        forgedIronMat,
      );
      const rimMesh = createSegmentMesh(
        spec,
        `${segName}Rim`,
        thetaStart,
        thetaEnd,
        [6, 7],
        false,
        balefulRimMat,
      );
      segGroup.add(ironMesh, rimMesh);
      meshes.push(ironMesh, rimMesh);
      morphMeshes.push(
        { ringIndex, segIndex, mesh: ironMesh },
        { ringIndex, segIndex, mesh: rimMesh },
      );
    } else {
      const ironMesh = createSegmentMesh(
        spec,
        `${segName}Iron`,
        thetaStart,
        thetaEnd,
        [0, 1, 2, 3, 4, 5, 6, 7],
        true,
        forgedIronMat,
      );
      segGroup.add(ironMesh);
      meshes.push(ironMesh);
      morphMeshes.push({ ringIndex, segIndex, mesh: ironMesh });
    }

    segmentEntries.push({
      ringIndex,
      segIndex,
      segCount,
      groupName: segName,
      midAngle,
      dirSign: segIndex % 2 === 0 ? 1 : -1,
      meshes,
    });
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
    if (prev && q.dot(prev) < 0) q.set(-q.x, -q.y, -q.z, -q.w);
    prev = q;
    return q.toArray();
  });
  return new THREE.QuaternionKeyframeTrack(`${nodeName}.quaternion`, times, values);
}

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

function buildMorphTracks(times, weightFn) {
  return morphMeshes.map(({ ringIndex, segIndex, mesh }) => {
    const values = times.flatMap((t) => weightFn(ringIndex, segIndex, t));
    return new THREE.NumberKeyframeTrack(`${mesh.name}.morphTargetInfluences`, times, values);
  });
}

/**
 * Generates position & rotation tracks that fracture/separate individual iron arc segments
 * outward, tangentially, and slightly in 2.5D depth, then pull them cleanly back together.
 *
 * `evalSeg(entry, t)` returns `{ radial, dz, dTheta, tiltX, tiltY }`.
 */
function buildSegmentFractureTracks(times, evalSeg) {
  const tracks = [];
  for (const entry of segmentEntries) {
    const cos = Math.cos(entry.midAngle);
    const sin = Math.sin(entry.midAngle);
    const posValues = [];
    const quatValues = [];
    let prevQ = null;

    for (const t of times) {
      const { radial = 0, dz = 0, dTheta = 0, tiltX = 0, tiltY = 0 } = evalSeg(entry, t);
      posValues.push(radial * cos, radial * sin, dz);
      const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(tiltX, tiltY, dTheta, 'YXZ'));
      if (prevQ && q.dot(prevQ) < 0) q.set(-q.x, -q.y, -q.z, -q.w);
      prevQ = q;
      quatValues.push(...q.toArray());
    }

    tracks.push(
      new THREE.VectorKeyframeTrack(`${entry.groupName}.position`, times, posValues),
      new THREE.QuaternionKeyframeTrack(`${entry.groupName}.quaternion`, times, quatValues),
    );
  }
  return tracks;
}

function buildRigPosTrack(times, fn) {
  return new THREE.VectorKeyframeTrack('SigilRig.position', times, times.flatMap(fn));
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
// 1. Idle (12.0s loop): Slow creeping counter-rotation where segments subtly
//    crack apart into fissures and silently draw back together
// ---------------------------------------------------------------------------
const idleTimes = sampleTimes(12.0, 96);
const idleClip = new THREE.AnimationClip('Idle', 12.0, [
  buildZSpinTrack(
    'InnerRingGroup',
    idleTimes,
    integrateSpinAngles(idleTimes, (t) => 1.0 + 0.3 * Math.sin((t / 12.0) * TAU), 1),
  ),
  buildZSpinTrack(
    'OuterRingGroup',
    idleTimes,
    integrateSpinAngles(idleTimes, (t) => 1.0 + 0.28 * Math.cos((t / 12.0) * TAU), -1),
  ),
  ...buildSegmentFractureTracks(idleTimes, (seg, t) => {
    // Periodic gentle unsealing & resealing of the iron segments as the rings slowly drift
    const waveA = Math.pow(Math.max(0, Math.sin((t / 12.0) * TAU - seg.ringIndex * 1.1)), 2.4);
    const waveB = Math.pow(Math.max(0, Math.sin((t / 12.0) * TAU * 2 + seg.segIndex * 1.25)), 3.0);
    const open = 0.042 * waveA + 0.022 * waveB;
    return {
      radial: open * (seg.ringIndex === 1 ? 1.15 : 0.85),
      dz: open * 0.35 * seg.dirSign,
      dTheta: 0.032 * waveA * seg.dirSign,
      tiltX: 0.025 * waveA * seg.dirSign,
      tiltY: -0.025 * waveB * seg.dirSign,
    };
  }),
  ...buildMorphTracks(idleTimes, (ringIndex, _segIndex, t) => {
    const p = (t / 12.0) * TAU;
    const spiky = 0.03 * (0.5 - 0.5 * Math.cos(p * 2 + ringIndex));
    const ocular = 0.12 * (0.5 - 0.5 * Math.cos(p + ringIndex * 0.7));
    const angular = 0.08 * (0.5 - 0.5 * Math.cos(p - ringIndex * 1.1));
    return [spiky, ocular, angular];
  }),
  buildRigPosTrack(idleTimes, (t) => {
    const p = (t / 12.0) * TAU;
    return [0.004 * Math.sin(p), 0.014 * Math.sin(p), 0.005 * Math.cos(p)];
  }),
  buildRigQuatTrack(idleTimes, (t) => {
    const p = (t / 12.0) * TAU;
    return [0.028 * Math.sin(p), 0.038 * Math.cos(p), 0.012 * Math.sin(p * 2)];
  }),
]);

// ---------------------------------------------------------------------------
// 2. Omen (3.2s one-shot): Bands warp into a narrowed vertical eldritch eye
//    while outer segments part slightly like iron lids, twitch, then reseal
// ---------------------------------------------------------------------------
const omenTimes = sampleTimes(3.2, 80);
const omenClip = new THREE.AnimationClip('Omen', 3.2, [
  buildZSpinTrack(
    'InnerRingGroup',
    omenTimes,
    integrateSpinAngles(
      omenTimes,
      (t) => {
        const stall = 0.16 * (1 - smoothstep(0.12, 0.85, t));
        const twitch = -5.0 * Math.exp(-Math.pow((t - 1.65) / 0.17, 2));
        const recoil = 1.5 * Math.exp(-Math.pow((t - 2.15) / 0.18, 2));
        return stall + twitch + recoil;
      },
      null,
    ),
  ),
  buildZSpinTrack(
    'OuterRingGroup',
    omenTimes,
    integrateSpinAngles(
      omenTimes,
      (t) => {
        const stall = -0.14 * (1 - smoothstep(0.12, 0.85, t));
        const twitch = 3.8 * Math.exp(-Math.pow((t - 1.65) / 0.19, 2));
        const recoil = -1.2 * Math.exp(-Math.pow((t - 2.18) / 0.18, 2));
        return stall + twitch + recoil;
      },
      null,
    ),
  ),
  ...buildSegmentFractureTracks(omenTimes, (seg, t) => {
    const env = smoothstep(0.15, 0.85, t) * (1 - smoothstep(2.25, 3.15, t));
    const twitch = Math.exp(-Math.pow((t - 1.65) / 0.22, 2));
    const radial = (seg.ringIndex === 1 ? 0.095 : 0.055) * env + 0.035 * twitch;
    return {
      radial,
      dz: 0.028 * env * seg.dirSign,
      dTheta: 0.06 * env * seg.dirSign + 0.04 * twitch * -seg.dirSign,
      tiltX: 0.06 * env * Math.sin(seg.midAngle),
      tiltY: 0.06 * env * Math.cos(seg.midAngle),
    };
  }),
  ...buildMorphTracks(omenTimes, (_ringIndex, _segIndex, t) => {
    const env = smoothstep(0.06, 0.72, t) * (1 - smoothstep(2.28, 3.16, t));
    const twitch = Math.exp(-Math.pow((t - 1.65) / 0.24, 2));
    const spiky = Math.min(1, 0.18 * twitch);
    const ocular = Math.min(1, env * 0.96 + 0.08 * twitch);
    const angular = 0.06 * env;
    return [spiky, ocular, angular];
  }),
  buildRigQuatTrack(omenTimes, (t) => {
    const env = smoothstep(0.08, 0.7, t) * (1 - smoothstep(2.2, 3.15, t));
    const twitch = Math.exp(-Math.pow((t - 1.65) / 0.18, 2));
    return [0.065 * env + 0.035 * twitch, -0.095 * env, 0.045 * twitch];
  }),
]);

// ---------------------------------------------------------------------------
// 3. Bristle (2.8s one-shot): Bold jagged iron thorns erupt while segments
//    split into ratcheting barbed jaws, then snap cleanly back together
// ---------------------------------------------------------------------------
const bristleTimes = sampleTimes(2.8, 70);
const bristleClip = new THREE.AnimationClip('Bristle', 2.8, [
  buildZSpinTrack(
    'InnerRingGroup',
    bristleTimes,
    integrateSpinAngles(
      bristleTimes,
      (t) =>
        0.25 +
        6.0 * Math.exp(-Math.pow((t - 0.38) / 0.14, 2)) +
        3.2 * Math.exp(-Math.pow((t - 1.25) / 0.22, 2)) +
        1.8 * Math.exp(-Math.pow((t - 2.0) / 0.22, 2)),
      1,
    ),
  ),
  buildZSpinTrack(
    'OuterRingGroup',
    bristleTimes,
    integrateSpinAngles(
      bristleTimes,
      (t) =>
        0.25 +
        4.8 * Math.exp(-Math.pow((t - 0.42) / 0.15, 2)) +
        2.6 * Math.exp(-Math.pow((t - 1.28) / 0.22, 2)) +
        1.5 * Math.exp(-Math.pow((t - 2.02) / 0.22, 2)),
      -1,
    ),
  ),
  ...buildSegmentFractureTracks(bristleTimes, (seg, t) => {
    const open = smoothstep(0.08, 0.36, t) * (1 - smoothstep(2.02, 2.68, t));
    const shudder = 0.016 * Math.sin(t * 28.0 + seg.segIndex * 1.9) * open;
    return {
      radial: (seg.ringIndex === 1 ? 0.09 : 0.065) * open + shudder,
      dz: 0.025 * open * seg.dirSign,
      dTheta: 0.055 * open * seg.dirSign,
      tiltX: 0.05 * open * seg.dirSign,
      tiltY: 0.04 * open * -seg.dirSign,
    };
  }),
  ...buildMorphTracks(bristleTimes, (ringIndex, _segIndex, t) => {
    const hold = smoothstep(0.06, 0.34, t) * (1 - smoothstep(2.06, 2.74, t));
    const shudder = 0.07 * Math.sin(t * 28.0 + ringIndex * 1.4) * hold;
    return [Math.max(0, Math.min(1, hold * 0.96 + shudder)), 0.0, 0.14 * hold];
  }),
  buildRigPosTrack(bristleTimes, (t) => {
    const hold = smoothstep(0.1, 0.36, t) * (1 - smoothstep(2.02, 2.7, t));
    return [0.008 * Math.sin(t * 34.0) * hold, 0.006 * Math.cos(t * 29.0) * hold, 0.018 * hold];
  }),
]);

// ---------------------------------------------------------------------------
// 4. Frenzy (3.4s one-shot): SIGNATURE slow-to-suddenly-fast spiky shatter!
//    Slow ominous creep (0..0.85s) -> bands shatter apart & whip violently at
//    high speed with full razor spikes (0.85..2.35s) -> slam back together!
// ---------------------------------------------------------------------------
const frenzyTimes = sampleTimes(3.4, 136); // 25ms step => safe dense quaternion sampling
const frenzyClip = new THREE.AnimationClip('Frenzy', 3.4, [
  buildZSpinTrack(
    'InnerRingGroup',
    frenzyTimes,
    integrateSpinAngles(
      frenzyTimes,
      (t) => {
        const slowCreep = 0.42 * (1 - smoothstep(0.7, 0.88, t));
        const fastBurst = 21.0 * smoothstep(0.84, 1.04, t) * (1 - smoothstep(2.22, 2.68, t));
        const tail = 0.32 * smoothstep(2.7, 3.3, t);
        return slowCreep + fastBurst + tail;
      },
      4,
    ),
  ),
  buildZSpinTrack(
    'OuterRingGroup',
    frenzyTimes,
    integrateSpinAngles(
      frenzyTimes,
      (t) => {
        const slowCreep = 0.4 * (1 - smoothstep(0.7, 0.88, t));
        const fastBurst = 19.5 * smoothstep(0.84, 1.04, t) * (1 - smoothstep(2.22, 2.68, t));
        const tail = 0.3 * smoothstep(2.7, 3.3, t);
        return slowCreep + fastBurst + tail;
      },
      -3,
    ),
  ),
  ...buildSegmentFractureTracks(frenzyTimes, (seg, t) => {
    const coil = smoothstep(0.0, 0.72, t) * (1 - smoothstep(0.84, 0.98, t));
    const shatter = smoothstep(0.84, 1.04, t) * (1 - smoothstep(2.3, 3.12, t));
    // Stagger each piece's radial throw slightly so the shattering feels violent & organic
    const pieceBias = 0.84 + 0.32 * ((seg.segIndex * 37) % 5) / 4;
    const flutter = 0.018 * Math.sin(t * 36.0 + seg.segIndex * 2.1) * shatter;
    const baseThrow = seg.ringIndex === 1 ? 0.125 : 0.105;
    return {
      radial: -0.022 * coil + baseThrow * pieceBias * shatter + flutter,
      dz: 0.044 * shatter * seg.dirSign * Math.cos(t * 9.0 + seg.segIndex),
      dTheta: 0.11 * shatter * seg.dirSign,
      tiltX: 0.11 * shatter * Math.sin(t * 11.0 + seg.segIndex),
      tiltY: 0.11 * shatter * Math.cos(t * 9.5 + seg.segIndex * 1.4),
    };
  }),
  ...buildMorphTracks(frenzyTimes, (ringIndex, _segIndex, t) => {
    const coil = smoothstep(0.0, 0.72, t) * (1 - smoothstep(0.84, 1.0, t));
    const burst = smoothstep(0.82, 0.98, t) * (1 - smoothstep(2.38, 3.24, t));
    const tremor = 0.06 * Math.sin(t * 42.0 + ringIndex * 1.7) * burst;
    const spiky = Math.max(0, Math.min(1, burst * 0.98 + tremor));
    const ocular = 0.38 * coil;
    const angular = 0.22 * burst;
    return [spiky, ocular, angular];
  }),
  buildRigPosTrack(frenzyTimes, (t) => {
    const burst = smoothstep(0.86, 1.04, t) * (1 - smoothstep(2.25, 2.85, t));
    return [
      0.014 * Math.sin(t * 53.0) * burst,
      0.014 * Math.cos(t * 47.0) * burst,
      0.022 * burst,
    ];
  }),
  buildRigScaleTrack(frenzyTimes, (t) => {
    const coil = smoothstep(0.0, 0.76, t) * (1 - smoothstep(0.84, 1.0, t));
    const burst = smoothstep(0.84, 1.0, t) * (1 - smoothstep(2.32, 3.18, t));
    return 1.0 - 0.06 * coil + 0.02 * burst;
  }),
]);

// ---------------------------------------------------------------------------
// 5. Dread (6.0s loop): Sustained wrong-angled angular+spiky ward with two
//    sudden whipping bursts where segments break apart and snap back together
// ---------------------------------------------------------------------------
const dreadTimes = sampleTimes(6.0, 150);
const dreadClip = new THREE.AnimationClip('Dread', 6.0, [
  buildZSpinTrack(
    'InnerRingGroup',
    dreadTimes,
    integrateSpinAngles(
      dreadTimes,
      (t) =>
        0.5 +
        11.0 * Math.exp(-Math.pow((t - 1.55) / 0.24, 2)) +
        12.5 * Math.exp(-Math.pow((t - 4.45) / 0.22, 2)),
      3,
    ),
  ),
  buildZSpinTrack(
    'OuterRingGroup',
    dreadTimes,
    integrateSpinAngles(
      dreadTimes,
      (t) =>
        0.5 +
        10.0 * Math.exp(-Math.pow((t - 1.55) / 0.24, 2)) +
        11.5 * Math.exp(-Math.pow((t - 4.45) / 0.22, 2)),
      -2,
    ),
  ),
  ...buildSegmentFractureTracks(dreadTimes, (seg, t) => {
    const burst1 = Math.exp(-Math.pow((t - 1.55) / 0.38, 2));
    const burst2 = Math.exp(-Math.pow((t - 4.45) / 0.36, 2));
    const split = Math.max(burst1, burst2);
    const drift = 0.025 * (0.5 + 0.5 * Math.sin((t / 6.0) * TAU + seg.segIndex * 1.3));
    return {
      radial: drift + (seg.ringIndex === 1 ? 0.115 : 0.088) * split,
      dz: 0.036 * split * seg.dirSign,
      dTheta: 0.095 * split * seg.dirSign,
      tiltX: 0.075 * split * seg.dirSign,
      tiltY: 0.065 * split * -seg.dirSign,
    };
  }),
  ...buildMorphTracks(dreadTimes, (ringIndex, _segIndex, t) => {
    const p = (t / 6.0) * TAU;
    const burst1 = Math.exp(-Math.pow((t - 1.55) / 0.34, 2));
    const burst2 = Math.exp(-Math.pow((t - 4.45) / 0.32, 2));
    const surge = Math.max(burst1, burst2);
    const spiky = Math.min(1, 0.32 + 0.54 * surge);
    const ocular = 0.16 * (0.5 + 0.5 * Math.cos(p + ringIndex));
    const angular = Math.min(1, 0.68 + 0.24 * (0.5 + 0.5 * Math.sin(p + ringIndex * 0.9)));
    return [spiky, ocular, angular];
  }),
  buildRigQuatTrack(dreadTimes, (t) => {
    const p = (t / 6.0) * TAU;
    return [0.05 * Math.sin(p), -0.07 * Math.cos(p), 0.022 * Math.sin(p * 2)];
  }),
]);

// ---------------------------------------------------------------------------
// 6. Revelation (2.8s one-shot): Bands transform into an angular horned iron
//    crown & break wide apart into a suspended fractured halo, then reseal
// ---------------------------------------------------------------------------
const revTimes = sampleTimes(2.8, 70);
const revelationClip = new THREE.AnimationClip('Revelation', 2.8, [
  buildZSpinTrack(
    'InnerRingGroup',
    revTimes,
    integrateSpinAngles(
      revTimes,
      (t) => 0.35 + 6.5 * Math.exp(-Math.pow((t - 1.1) / 0.46, 2)),
      2,
    ),
  ),
  buildZSpinTrack(
    'OuterRingGroup',
    revTimes,
    integrateSpinAngles(
      revTimes,
      (t) => 0.35 + 5.2 * Math.exp(-Math.pow((t - 1.1) / 0.48, 2)),
      -1,
    ),
  ),
  ...buildSegmentFractureTracks(revTimes, (seg, t) => {
    const bloom = smoothstep(0.1, 0.78, t) * (1 - smoothstep(1.82, 2.72, t));
    return {
      radial: (seg.ringIndex === 1 ? 0.14 : 0.10) * bloom,
      dz: 0.032 * bloom * (seg.ringIndex === 0 ? 1 : -1) * seg.dirSign,
      dTheta: 0.08 * bloom * seg.dirSign,
      tiltX: 0.05 * bloom * Math.cos(seg.midAngle),
      tiltY: 0.05 * bloom * Math.sin(seg.midAngle),
    };
  }),
  ...buildMorphTracks(revTimes, (_ringIndex, _segIndex, t) => {
    const bloom = smoothstep(0.08, 0.72, t) * (1 - smoothstep(1.82, 2.74, t));
    const peak = Math.exp(-Math.pow((t - 1.15) / 0.44, 2));
    return [0.14 * peak, 0.0, Math.min(1, 0.94 * bloom + 0.08 * peak)];
  }),
  buildRigQuatTrack(revTimes, (t) => {
    const bloom = smoothstep(0.08, 0.72, t) * (1 - smoothstep(1.8, 2.74, t));
    return [-0.06 * bloom, 0.075 * bloom, 0];
  }),
]);

// ---------------------------------------------------------------------------
// 7. Shudder (1.8s one-shot — click/activate reaction): Instant fracture-snap
//    spiky recoil where pieces jolt apart and slam right back together
// ---------------------------------------------------------------------------
const shudderTimes = sampleTimes(1.8, 72);
const shudderClip = new THREE.AnimationClip('Shudder', 1.8, [
  buildZSpinTrack(
    'InnerRingGroup',
    shudderTimes,
    integrateSpinAngles(
      shudderTimes,
      (t) =>
        0.25 +
        18.0 * Math.exp(-Math.pow((t - 0.24) / 0.14, 2)) +
        4.2 * Math.exp(-Math.pow((t - 0.78) / 0.16, 2)),
      2,
    ),
  ),
  buildZSpinTrack(
    'OuterRingGroup',
    shudderTimes,
    integrateSpinAngles(
      shudderTimes,
      (t) =>
        0.25 +
        14.0 * Math.exp(-Math.pow((t - 0.24) / 0.14, 2)) +
        3.4 * Math.exp(-Math.pow((t - 0.78) / 0.16, 2)),
      -1,
    ),
  ),
  ...buildSegmentFractureTracks(shudderTimes, (seg, t) => {
    const snap1 = Math.exp(-Math.pow((t - 0.24) / 0.16, 2));
    const snap2 = 0.7 * Math.exp(-Math.pow((t - 0.78) / 0.18, 2));
    const endFade = 1 - smoothstep(1.26, 1.76, t);
    const jolt = Math.max(snap1, snap2) * endFade;
    return {
      radial: (seg.ringIndex === 1 ? 0.125 : 0.09) * jolt,
      dz: 0.038 * jolt * seg.dirSign,
      dTheta: 0.11 * jolt * seg.dirSign,
      tiltX: 0.08 * jolt * seg.dirSign,
      tiltY: 0.07 * jolt * -seg.dirSign,
    };
  }),
  ...buildMorphTracks(shudderTimes, (_ringIndex, _segIndex, t) => {
    const snap1 = Math.exp(-Math.pow((t - 0.24) / 0.18, 2));
    const snap2 = 0.85 * Math.exp(-Math.pow((t - 0.76) / 0.22, 2));
    const endFade = 1 - smoothstep(1.3, 1.78, t);
    const spiky = Math.min(1, Math.max(snap1, snap2) * endFade);
    const ocular = 0.2 * snap2 * endFade;
    const angular = 0.35 * snap1 * endFade;
    return [spiky, ocular, angular];
  }),
  buildRigPosTrack(shudderTimes, (t) => {
    const kick = Math.exp(-Math.pow((t - 0.22) / 0.14, 2));
    return [0.012 * Math.sin(t * 58.0) * kick, 0.01 * Math.cos(t * 51.0) * kick, 0.02 * kick];
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
