import { readFile, writeFile } from 'node:fs/promises';
import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';

// GLTFExporter assembles its binary container with the browser FileReader API.
globalThis.FileReader = class {
  async readAsArrayBuffer(blob) {
    this.result = await blob.arrayBuffer();
    this.onloadend();
  }
};

const fullTurn = Math.PI * 2;
const modelUrl = new URL('../example_theater/avatars/narratron/model.glb', import.meta.url);

function parseGlb(buffer) {
  const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);
  const magic = view.getUint32(0, true);
  if (magic !== 0x46546c67) throw new Error('Invalid GLB header.');
  const jsonLen = view.getUint32(12, true);
  const jsonText = new TextDecoder().decode(buffer.subarray(20, 20 + jsonLen));
  const gltf = JSON.parse(jsonText);
  const binOffset = 20 + jsonLen + 8;
  const binLen = view.getUint32(20 + jsonLen, true);
  const binChunk = buffer.subarray(binOffset, binOffset + binLen);
  return { gltf, binChunk };
}

function readAccessorSlice(gltf, binChunk, accessorIndex) {
  const acc = gltf.accessors[accessorIndex];
  const bv = gltf.bufferViews[acc.bufferView];
  const byteOffset = (bv.byteOffset ?? 0) + (acc.byteOffset ?? 0);
  return binChunk.subarray(byteOffset, byteOffset + bv.byteLength);
}

function readImageBytes(gltf, binChunk, imageName, fallbackIndex) {
  const images = gltf.images ?? [];
  const img = images.find((entry) => entry.name === imageName) ?? images[fallbackIndex];
  if (!img || img.bufferView === undefined) {
    throw new Error(`Missing embedded texture "${imageName}" in model.glb`);
  }
  const bv = gltf.bufferViews[img.bufferView];
  const offset = bv.byteOffset ?? 0;
  return Buffer.from(binChunk.subarray(offset, offset + bv.byteLength));
}

const existingBytes = await readFile(modelUrl);
const { gltf: sourceGltf, binChunk: sourceBin } = parseGlb(existingBytes);

const bodyMeshDef =
  sourceGltf.meshes.find((m) => m.primitives?.[0]?.attributes?.JOINTS_0 !== undefined) ??
  sourceGltf.meshes[0];
const prim = bodyMeshDef.primitives[0];

const posBytes = readAccessorSlice(sourceGltf, sourceBin, prim.attributes.POSITION);
const normBytes = readAccessorSlice(sourceGltf, sourceBin, prim.attributes.NORMAL);
const uvBytes = readAccessorSlice(sourceGltf, sourceBin, prim.attributes.TEXCOORD_0);
const jointsBytes = readAccessorSlice(sourceGltf, sourceBin, prim.attributes.JOINTS_0);
const weightsBytes = readAccessorSlice(sourceGltf, sourceBin, prim.attributes.WEIGHTS_0);
const indexBytes = readAccessorSlice(sourceGltf, sourceBin, prim.indices);

const normalWebp = readImageBytes(sourceGltf, sourceBin, 'normal', 0);
const baseColorWebp = readImageBytes(sourceGltf, sourceBin, 'baseColor', 1);
const emissiveWebp = readImageBytes(sourceGltf, sourceBin, 'emissive', 2);

const toFloat32 = (slice) =>
  new Float32Array(slice.buffer.slice(slice.byteOffset, slice.byteOffset + slice.byteLength));
const toUint16 = (slice) =>
  new Uint16Array(slice.buffer.slice(slice.byteOffset, slice.byteOffset + slice.byteLength));

const jointsAcc = sourceGltf.accessors[prim.attributes.JOINTS_0];
const jointsArray =
  jointsAcc.componentType === 5121
    ? new Uint16Array(new Uint8Array(jointsBytes.buffer, jointsBytes.byteOffset, jointsBytes.byteLength))
    : toUint16(jointsBytes);

const bodyGeometry = new THREE.BufferGeometry();
bodyGeometry.setAttribute('position', new THREE.BufferAttribute(toFloat32(posBytes), 3));
bodyGeometry.setAttribute('normal', new THREE.BufferAttribute(toFloat32(normBytes), 3));
bodyGeometry.setAttribute('uv', new THREE.BufferAttribute(toFloat32(uvBytes), 2));
bodyGeometry.setAttribute('skinIndex', new THREE.BufferAttribute(jointsArray, 4));
bodyGeometry.setAttribute('skinWeight', new THREE.BufferAttribute(toFloat32(weightsBytes), 4));
bodyGeometry.setIndex(new THREE.BufferAttribute(toUint16(indexBytes), 1));

// Materials
const astralBodyMat = new THREE.MeshStandardMaterial({
  name: 'NarratronAstral',
  color: 0xffffff,
  emissive: 0xd0d0e6,
  emissiveIntensity: 1.0,
  roughness: 0.54,
  metalness: 0.06,
  transparent: true,
  opacity: 0.92,
  side: THREE.DoubleSide,
});

const cosmicCoreMat = new THREE.MeshStandardMaterial({
  name: 'CosmicCore',
  color: 0xffffff,
  emissive: 0x7ce8ff,
  emissiveIntensity: 1.4,
  roughness: 0.25,
  transparent: true,
  opacity: 0.40,
});

const starGoldMat = new THREE.MeshStandardMaterial({
  name: 'StarGold',
  color: 0xfff0c4,
  emissive: 0xffc65c,
  emissiveIntensity: 2.2,
  roughness: 0.2,
});

const starCyanMat = new THREE.MeshStandardMaterial({
  name: 'StarCyan',
  color: 0xdffbff,
  emissive: 0x52e2ff,
  emissiveIntensity: 2.2,
  roughness: 0.2,
});

// Scene & Skeleton Assembly
const root = new THREE.Group();
root.name = 'NarratronScene';

// Orient canonical Y-up/Z-front coordinates toward the avatar-scene camera at (2.4, 1.9, 5.6)
const viewGroup = new THREE.Group();
viewGroup.name = 'NarratronView';
viewGroup.rotation.set(-0.18, 0.34, 0.0, 'YXZ');
root.add(viewGroup);

// 11-Bone Skeleton in left-right reflected canonical space:
// 0: NarratronRig   (0.00, 0.28, 0.00)
// 1: Torso          (0.00, 0.32, 0.02)
// 2: Head           (-0.05, 0.56, 0.04)
// 3: HairMid        (+0.12, 0.72, -0.02)
// 4: HairTip        (+0.38, 0.76, -0.04)
// 5: QuillArm       (+0.04, 0.42, 0.05)  [right shoulder]
// 6: QuillForearm   (+0.02, 0.31, 0.08)  [right elbow]
// 7: QuillHand      (-0.24, 0.31, 0.12)  [writing hand & feather quill]
// 8: ScrollGroup    (-0.38, 0.38, 0.06)  [left scroll arm & scroll]
// 9: TailUpper      (+0.10, 0.12, 0.00)
// 10: TailLower     (+0.36, -0.14, -0.02)
const rig = new THREE.Bone();
rig.name = 'NarratronRig';
rig.position.set(0.0, 0.28, 0.0);
viewGroup.add(rig);

const torso = new THREE.Bone();
torso.name = 'Torso';
torso.position.set(0.0, 0.04, 0.02);
rig.add(torso);

const head = new THREE.Bone();
head.name = 'Head';
head.position.set(-0.05, 0.24, 0.02);
torso.add(head);

const hairMid = new THREE.Bone();
hairMid.name = 'HairMid';
hairMid.position.set(0.17, 0.16, -0.06);
head.add(hairMid);

const hairTip = new THREE.Bone();
hairTip.name = 'HairTip';
hairTip.position.set(0.26, 0.04, -0.02);
hairMid.add(hairTip);

const quillArm = new THREE.Bone();
quillArm.name = 'QuillArm';
quillArm.position.set(0.04, 0.10, 0.03);
torso.add(quillArm);

const quillForearm = new THREE.Bone();
quillForearm.name = 'QuillForearm';
quillForearm.position.set(-0.02, -0.11, 0.03);
quillArm.add(quillForearm);

const quillHand = new THREE.Bone();
quillHand.name = 'QuillHand';
quillHand.position.set(-0.26, 0.0, 0.04);
quillForearm.add(quillHand);

const scrollGroup = new THREE.Bone();
scrollGroup.name = 'ScrollGroup';
scrollGroup.position.set(-0.38, 0.06, 0.04);
torso.add(scrollGroup);

const tailUpper = new THREE.Bone();
tailUpper.name = 'TailUpper';
tailUpper.position.set(0.10, -0.20, -0.02);
torso.add(tailUpper);

const tailLower = new THREE.Bone();
tailLower.name = 'TailLower';
tailLower.position.set(0.26, -0.26, -0.02);
tailUpper.add(tailLower);

const bones = [
  rig,
  torso,
  head,
  hairMid,
  hairTip,
  quillArm,
  quillForearm,
  quillHand,
  scrollGroup,
  tailUpper,
  tailLower,
];
const skeleton = new THREE.Skeleton(bones);

const narratronMesh = new THREE.SkinnedMesh(bodyGeometry, astralBodyMat);
narratronMesh.name = 'NarratronBodyMesh';
viewGroup.add(narratronMesh);
root.updateMatrixWorld(true);
narratronMesh.bind(skeleton);

// Inner glowing heart core inside the ghostly translucent chest for hoverGlow
const cosmicCore = new THREE.Mesh(new THREE.SphereGeometry(0.055, 16, 12), cosmicCoreMat);
cosmicCore.name = 'CosmicCoreMesh';
cosmicCore.position.set(-0.02, 0.05, -0.01);
torso.add(cosmicCore);

// Background Nebula shader anchor behind Narratron
const nebulaAnchor = new THREE.Group();
nebulaAnchor.name = 'NebulaAnchor';
nebulaAnchor.position.set(0.06, 0.02, -0.32);
rig.add(nebulaAnchor);

// SparkLight right at the feather quill nib tip
const sparkLight = new THREE.PointLight(0x7ce8ff, 2.1);
sparkLight.name = 'SparkLight';
sparkLight.position.set(-0.10, -0.05, 0.05);
quillHand.add(sparkLight);

// Subtle twinkling cosmic star motes orbiting around Narratron
const orbitRing = new THREE.Group();
orbitRing.name = 'OrbitRing';
orbitRing.position.set(0.02, 0.02, -0.06);
rig.add(orbitRing);

const moteGeom = new THREE.OctahedronGeometry(0.015, 0);
for (let i = 0; i < 12; i++) {
  const angle = (i / 12) * fullTurn;
  const radius = 0.56 + (i % 3) * 0.10;
  const height = Math.sin(angle * 2.0 + 0.4) * 0.38;
  const mote = new THREE.Mesh(moteGeom, i % 2 === 0 ? starGoldMat : starCyanMat);
  mote.name = `StarMote${i + 1}`;
  mote.position.set(Math.cos(angle) * radius, height, Math.sin(angle) * radius * 0.55 - 0.08);
  mote.scale.setScalar(0.6 + (i % 3) * 0.22);
  orbitRing.add(mote);
}

// Rest pose capture for smooth animation tracks
const animatedNodes = [...bones, orbitRing];
const restPose = new Map(
  animatedNodes.map((node) => [
    node.name,
    { pos: node.position.clone(), quat: node.quaternion.clone(), scale: node.scale.clone() },
  ]),
);

function buildPosTrack(nodeName, times, offsets) {
  const base = restPose.get(nodeName).pos;
  const values = offsets.flatMap(([dx, dy, dz]) => [base.x + dx, base.y + dy, base.z + dz]);
  return new THREE.VectorKeyframeTrack(`${nodeName}.position`, times, values);
}

function buildQuatTrack(nodeName, times, eulerDeltas, order = 'YXZ') {
  const base = restPose.get(nodeName).quat;
  let prev = null;
  const values = eulerDeltas.flatMap(([rx, ry, rz]) => {
    const q = base.clone().multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(rx, ry, rz, order)));
    if (prev && q.dot(prev) < 0) q.set(-q.x, -q.y, -q.z, -q.w);
    prev = q;
    return q.toArray();
  });
  return new THREE.QuaternionKeyframeTrack(`${nodeName}.quaternion`, times, values);
}

function buildScaleTrack(nodeName, times, scales) {
  const base = restPose.get(nodeName).scale;
  const values = scales.flatMap(([sx, sy, sz]) => [base.x * sx, base.y * sy, base.z * sz]);
  return new THREE.VectorKeyframeTrack(`${nodeName}.scale`, times, values);
}

const z3 = [0, 0, 0];
const s1 = [1, 1, 1];

// 1. Idle (6.0s loop): Serene cosmic hover, gentle breath, undulating hair & ghostly ribbons
const idleTimes = [0, 1.5, 3.0, 4.5, 6.0];
const idleClip = new THREE.AnimationClip('Idle', 6.0, [
  buildPosTrack('NarratronRig', idleTimes, [z3, [0, 0.045, 0], [0, 0.068, 0], [0, 0.028, 0], z3]),
  buildQuatTrack('Torso', idleTimes, [z3, [0.035, 0.04, -0.03], [0.05, -0.035, 0.02], [0.02, -0.045, 0.03], z3]),
  buildQuatTrack('Head', idleTimes, [z3, [-0.05, 0.08, -0.05], [0.06, -0.07, 0.04], [-0.04, -0.09, -0.04], z3]),
  buildQuatTrack('HairMid', idleTimes, [z3, [0.06, -0.08, -0.11], [-0.05, 0.08, 0.09], [0.05, -0.06, -0.09], z3]),
  buildQuatTrack('HairTip', idleTimes, [z3, [-0.08, -0.12, -0.16], [0.07, 0.12, 0.14], [-0.06, -0.10, -0.12], z3]),
  buildQuatTrack('QuillArm', idleTimes, [z3, [0.03, 0.03, -0.04], [-0.02, -0.03, 0.03], [0.03, 0.02, -0.03], z3]),
  buildQuatTrack('QuillForearm', idleTimes, [z3, [-0.03, 0.03, -0.05], [0.03, -0.03, 0.04], [-0.02, 0.02, -0.03], z3]),
  buildQuatTrack('QuillHand', idleTimes, [z3, [-0.04, -0.04, 0.05], [0.04, 0.04, -0.05], [-0.03, -0.03, 0.04], z3]),
  buildQuatTrack('ScrollGroup', idleTimes, [z3, [0.03, -0.05, 0.04], [-0.02, 0.04, -0.03], [0.03, -0.04, 0.03], z3]),
  buildQuatTrack('TailUpper', idleTimes, [z3, [0.04, -0.06, -0.10], [-0.04, 0.06, 0.09], [0.03, -0.05, -0.08], z3]),
  buildQuatTrack('TailLower', idleTimes, [z3, [-0.06, -0.10, -0.16], [0.06, 0.10, 0.15], [-0.05, -0.08, -0.13], z3]),
  buildQuatTrack('OrbitRing', idleTimes, [z3, [0.03, -0.18, -0.03], [0, -0.36, 0.03], [-0.03, -0.18, -0.03], z3]),
]);

// 2. Greeting (2.2s one-shot): Head turns to face the screen (+Z), arm holding the quill crosses body center and rises up before waving!
const greetTimes = [0, 0.32, 0.64, 0.96, 1.28, 1.60, 1.92, 2.2];
const greetingClip = new THREE.AnimationClip('Greeting', 2.2, [
  buildPosTrack('NarratronRig', greetTimes, [
    z3,
    [0.01, 0.045, 0.02],
    [0.02, 0.065, 0.03],
    [0.02, 0.050, 0.03],
    [0.02, 0.068, 0.03],
    [0.02, 0.052, 0.03],
    [0.01, 0.025, 0.01],
    z3,
  ]),
  buildQuatTrack('Torso', greetTimes, [
    z3,
    [0.05, 0.14, -0.03],
    [0.06, 0.20, -0.05],
    [0.07, 0.22, 0.03],
    [0.06, 0.20, -0.04],
    [0.07, 0.22, 0.03],
    [0.03, 0.10, 0],
    z3,
  ]),
  // Positive Y rotation (ry > 0) turns the left-facing (-X) head toward +Z (directly facing the screen!)
  buildQuatTrack('Head', greetTimes, [
    z3,
    [-0.04, 0.34, -0.03],
    [-0.06, 0.48, -0.06],
    [-0.04, 0.50, 0.06],
    [-0.06, 0.48, -0.06],
    [-0.04, 0.50, 0.05],
    [-0.02, 0.22, 0],
    z3,
  ]),
  // Arm crosses body center (x=0 -> +X) and rises up as a cohesive limb (no bone dislocation!) before waving!
  buildPosTrack('QuillArm', greetTimes, [
    z3,
    [0.09, 0.08, 0.03],
    [0.18, 0.14, 0.04],
    [0.24, 0.16, 0.04],
    [0.17, 0.14, 0.04],
    [0.23, 0.16, 0.04],
    [0.08, 0.06, 0.02],
    z3,
  ]),
  buildQuatTrack('QuillArm', greetTimes, [
    z3,
    [-0.05, -0.06, -0.20],
    [-0.06, -0.10, -0.32],
    [-0.06, -0.18, -0.46],
    [-0.06, -0.08, -0.30],
    [-0.06, -0.18, -0.44],
    [-0.03, -0.04, -0.14],
    z3,
  ]),
  buildQuatTrack('QuillForearm', greetTimes, [
    z3,
    [-0.04, -0.06, -0.16],
    [-0.05, -0.10, -0.24],
    [-0.05, -0.16, -0.38],
    [-0.05, -0.08, -0.22],
    [-0.05, -0.16, -0.36],
    [-0.02, -0.04, -0.12],
    z3,
  ]),
  buildQuatTrack('QuillHand', greetTimes, [
    z3,
    [0.02, 0.06, 0.24],
    [0.04, 0.10, 0.42],
    [-0.04, -0.10, 0.20],
    [0.04, 0.12, 0.46],
    [-0.04, -0.10, 0.22],
    [0.02, 0.04, 0.16],
    z3,
  ]),
  buildQuatTrack('ScrollGroup', greetTimes, [
    z3,
    [-0.04, 0.08, 0.05],
    [-0.06, 0.12, 0.08],
    [-0.08, 0.14, 0.09],
    [-0.06, 0.12, 0.08],
    [-0.07, 0.14, 0.08],
    [-0.03, 0.05, 0.03],
    z3,
  ]),
  // Counter-rotate HairMid slightly against Head's +Y turn so the hair trails gracefully behind
  buildQuatTrack('HairMid', greetTimes, [
    z3,
    [-0.04, -0.22, -0.08],
    [-0.06, -0.32, -0.12],
    [-0.08, -0.28, 0.08],
    [-0.06, -0.32, -0.10],
    [-0.08, -0.28, 0.08],
    [-0.02, -0.14, -0.04],
    z3,
  ]),
  buildQuatTrack('HairTip', greetTimes, [
    z3,
    [-0.08, -0.10, -0.12],
    [-0.12, -0.14, -0.16],
    [-0.14, 0.10, 0.12],
    [-0.10, -0.12, -0.14],
    [-0.12, 0.10, 0.10],
    [0.04, -0.04, -0.04],
    z3,
  ]),
  buildQuatTrack('TailUpper', greetTimes, [
    z3,
    [-0.05, -0.08, -0.08],
    [-0.08, -0.12, -0.10],
    [-0.10, 0.06, 0.08],
    [-0.06, -0.10, -0.08],
    [-0.08, 0.06, 0.06],
    [0.03, -0.04, -0.03],
    z3,
  ]),
  buildQuatTrack('TailLower', greetTimes, [
    z3,
    [-0.08, -0.10, -0.12],
    [-0.12, -0.14, -0.16],
    [-0.16, 0.10, 0.12],
    [-0.10, -0.12, -0.12],
    [-0.12, 0.08, 0.10],
    [0.05, -0.05, -0.05],
    z3,
  ]),
  buildQuatTrack('OrbitRing', greetTimes, [
    z3,
    [0, -0.20, -0.04],
    [0, -0.42, -0.06],
    [0, -0.58, -0.07],
    [0, -0.44, -0.05],
    [0, -0.28, -0.03],
    [0, -0.10, 0],
    z3,
  ]),
]);

// 3. Amused (2.0s one-shot): Playful chuckle bounce and lively ribbon/hair wave
const amusedTimes = [0, 0.30, 0.62, 0.96, 1.32, 1.68, 2.0];
const amusedClip = new THREE.AnimationClip('Amused', 2.0, [
  buildPosTrack('NarratronRig', amusedTimes, [z3, [0, 0.095, 0], [0, -0.035, 0], [0, 0.110, 0], [0, -0.028, 0], [0, 0.055, 0], z3]),
  buildQuatTrack('Torso', amusedTimes, [z3, [-0.10, -0.08, 0.10], [0.07, 0.07, -0.08], [-0.12, -0.10, 0.12], [0.05, 0.05, -0.07], [-0.05, -0.03, 0.05], z3]),
  buildQuatTrack('Head', amusedTimes, [z3, [-0.10, 0.08, 0.18], [0.06, -0.08, -0.16], [-0.12, 0.09, 0.18], [0.06, -0.08, -0.15], [-0.06, 0, 0.08], z3]),
  buildQuatTrack('QuillArm', amusedTimes, [z3, [-0.08, -0.06, -0.22], [0.05, 0.04, 0.10], [-0.10, -0.08, -0.26], [0.05, 0.04, 0.08], [-0.04, -0.02, -0.08], z3]),
  buildQuatTrack('QuillForearm', amusedTimes, [z3, [-0.08, -0.08, -0.24], [0.06, 0.05, 0.12], [-0.10, -0.10, -0.28], [0.05, 0.04, 0.10], [-0.04, -0.03, -0.08], z3]),
  buildQuatTrack('QuillHand', amusedTimes, [z3, [0.08, -0.06, -0.12], [-0.05, 0.03, 0.08], [0.10, -0.07, -0.14], [-0.04, 0.02, 0.06], [0.03, -0.02, -0.04], z3]),
  buildQuatTrack('ScrollGroup', amusedTimes, [z3, [-0.08, -0.10, -0.10], [0.05, 0.06, 0.06], [-0.10, -0.12, -0.12], [0.05, 0.05, 0.05], z3, z3]),
  buildQuatTrack('HairMid', amusedTimes, [z3, [0.16, -0.18, -0.16], [-0.12, 0.18, 0.16], [0.20, -0.22, -0.18], [-0.10, 0.14, 0.12], z3, z3]),
  buildQuatTrack('HairTip', amusedTimes, [z3, [0.22, -0.24, -0.22], [-0.18, 0.24, 0.20], [0.26, -0.28, -0.24], [-0.14, 0.18, 0.16], z3, z3]),
  buildQuatTrack('TailUpper', amusedTimes, [z3, [0.12, -0.14, -0.16], [-0.10, 0.14, 0.14], [0.14, -0.16, -0.18], [-0.08, 0.10, 0.10], z3, z3]),
  buildQuatTrack('TailLower', amusedTimes, [z3, [0.18, -0.20, -0.24], [-0.16, 0.20, 0.20], [0.22, -0.24, -0.26], [-0.12, 0.16, 0.16], z3, z3]),
  buildQuatTrack('OrbitRing', amusedTimes, [z3, [0.06, -0.45, -0.08], [-0.05, -0.85, 0.06], [0.06, -1.25, -0.08], [-0.04, -0.70, 0.04], [0, -0.28, 0], z3]),
]);

// 4. Surprised (1.6s one-shot): Startled upward lift and radiant flare
const surprisedTimes = [0, 0.20, 0.55, 0.95, 1.30, 1.6];
const surprisedClip = new THREE.AnimationClip('Surprised', 1.6, [
  buildPosTrack('NarratronRig', surprisedTimes, [z3, [-0.04, 0.145, -0.06], [-0.03, 0.115, -0.045], [0, 0.048, -0.015], [0, 0.015, 0], z3]),
  buildScaleTrack('NarratronRig', surprisedTimes, [s1, [1.07, 1.07, 1.07], [1.05, 1.05, 1.05], [1.02, 1.02, 1.02], s1, s1]),
  buildQuatTrack('Torso', surprisedTimes, [z3, [-0.20, -0.08, 0.06], [-0.15, -0.05, 0.05], [-0.05, -0.02, 0], z3, z3]),
  buildQuatTrack('Head', surprisedTimes, [z3, [-0.15, 0.08, -0.04], [-0.12, 0.06, -0.03], [-0.04, 0.03, 0], z3, z3]),
  buildQuatTrack('QuillArm', surprisedTimes, [z3, [-0.14, -0.14, -0.34], [-0.10, -0.10, -0.26], [-0.04, -0.04, -0.08], z3, z3]),
  buildQuatTrack('QuillForearm', surprisedTimes, [z3, [-0.12, -0.12, -0.36], [-0.09, -0.09, -0.28], [-0.03, -0.03, -0.08], z3, z3]),
  buildQuatTrack('QuillHand', surprisedTimes, [z3, [-0.10, -0.08, -0.16], [-0.08, -0.06, -0.12], [-0.03, -0.02, -0.04], z3, z3]),
  buildQuatTrack('ScrollGroup', surprisedTimes, [z3, [-0.12, 0.18, 0.18], [-0.09, 0.14, 0.14], [-0.03, 0.05, 0.05], z3, z3]),
  buildQuatTrack('HairMid', surprisedTimes, [z3, [0.24, -0.16, -0.20], [0.18, 0.12, 0.12], [0.06, -0.06, -0.06], z3, z3]),
  buildQuatTrack('HairTip', surprisedTimes, [z3, [0.32, -0.20, -0.28], [0.24, 0.16, 0.16], [0.08, -0.08, -0.08], z3, z3]),
  buildQuatTrack('TailUpper', surprisedTimes, [z3, [0.18, -0.10, 0.16], [0.14, -0.08, 0.12], [0.05, -0.02, 0.04], z3, z3]),
  buildQuatTrack('TailLower', surprisedTimes, [z3, [0.26, -0.16, 0.24], [0.20, -0.12, 0.18], [0.08, -0.04, 0.06], z3, z3]),
  buildScaleTrack('OrbitRing', surprisedTimes, [s1, [1.24, 1.24, 1.24], [1.18, 1.18, 1.18], [1.06, 1.06, 1.06], s1, s1]),
]);

// 5. Confused (2.4s one-shot): Curious head-tilt side-to-side and pondering quill tap
const confusedTimes = [0, 0.42, 0.92, 1.42, 1.92, 2.4];
const confusedClip = new THREE.AnimationClip('Confused', 2.4, [
  buildPosTrack('NarratronRig', confusedTimes, [z3, [0.04, 0.032, 0], [0.035, 0.042, 0], [-0.04, 0.032, 0], [-0.02, 0.015, 0], z3]),
  buildQuatTrack('Torso', confusedTimes, [z3, [0.05, 0.08, -0.12], [0.06, 0.05, -0.10], [0.03, -0.10, 0.12], [0.02, -0.05, 0.06], z3]),
  buildQuatTrack('Head', confusedTimes, [z3, [0.06, 0.12, -0.24], [0.05, 0.10, -0.20], [0.06, -0.14, 0.22], [0.03, -0.08, 0.12], z3]),
  buildQuatTrack('QuillArm', confusedTimes, [z3, [-0.10, 0.06, -0.28], [-0.08, 0.08, -0.24], [-0.12, 0.05, -0.32], [-0.05, 0.02, -0.12], z3]),
  buildQuatTrack('QuillForearm', confusedTimes, [z3, [-0.08, 0.06, -0.26], [-0.12, 0.04, -0.34], [-0.08, 0.06, -0.24], [-0.03, 0.02, -0.10], z3]),
  buildQuatTrack('QuillHand', confusedTimes, [z3, [-0.08, 0.05, -0.14], [-0.14, 0.04, -0.22], [-0.08, 0.05, -0.12], [-0.03, 0.02, -0.05], z3]),
  buildQuatTrack('ScrollGroup', confusedTimes, [z3, [0.05, -0.08, 0.06], [0.03, -0.06, 0.05], [-0.03, 0.06, -0.06], [-0.01, 0.03, -0.03], z3]),
  buildQuatTrack('HairMid', confusedTimes, [z3, [0.06, 0.10, -0.18], [0.04, 0.08, -0.14], [0.06, -0.12, 0.16], [0.02, -0.06, 0.08], z3]),
  buildQuatTrack('TailUpper', confusedTimes, [z3, [0.04, 0.06, -0.10], [0.04, 0.04, -0.08], [0.02, -0.08, 0.10], [0.02, -0.04, 0.04], z3]),
  buildQuatTrack('OrbitRing', confusedTimes, [z3, [0, 0.24, -0.06], [0, 0.18, -0.04], [0, -0.22, 0.06], [0, -0.10, 0.03], z3]),
]);

// 6. AtWork (4.8s loop): Back-and-forth feather-quill writing sweeps across the scroll paper (50% range)
const workTimes = [0, 0.3, 0.6, 0.9, 1.2, 1.5, 1.8, 2.1, 2.4, 2.7, 3.0, 3.3, 3.6, 3.9, 4.2, 4.5, 4.8];

const workQuillArmPoses = workTimes.map((t) => {
  const sweepX = Math.sin((t / 4.8) * fullTurn * 4);
  const lineY = Math.cos((t / 4.8) * fullTurn * 2);
  return [0.01 * lineY, 0.025 + 0.07 * sweepX, 0.025 - 0.03 * lineY + 0.04 * sweepX];
});

const workQuillForearmPos = workTimes.map((t) => {
  const sweepX = Math.sin((t / 4.8) * fullTurn * 4);
  const lineY = Math.cos((t / 4.8) * fullTurn * 2);
  return [-0.03 + 0.04 * sweepX - 0.015 * lineY, -0.005 + 0.025 * lineY - 0.01 * sweepX, 0.01];
});

const workQuillForearmPoses = workTimes.map((t) => {
  const sweepX = Math.sin((t / 4.8) * fullTurn * 4);
  const lineY = Math.cos((t / 4.8) * fullTurn * 2);
  return [0.01 * lineY, 0.03 + 0.10 * sweepX, 0.04 - 0.04 * lineY + 0.07 * sweepX];
});

const workQuillHandPos = workTimes.map((t) => {
  const sweepX = Math.sin((t / 4.8) * fullTurn * 4);
  const flourish = Math.sin((t / 4.8) * fullTurn * 8);
  const lineY = Math.cos((t / 4.8) * fullTurn * 2);
  return [-0.02 + 0.045 * sweepX - 0.01 * lineY, -0.005 + 0.02 * lineY + 0.012 * flourish, 0.01];
});

const workQuillHandPoses = workTimes.map((t) => {
  const sweepX = Math.sin((t / 4.8) * fullTurn * 4);
  const flourish = Math.sin((t / 4.8) * fullTurn * 8);
  return [0.02 * flourish, 0.11 * sweepX, 0.10 * flourish + 0.09 * sweepX];
});

const workBodyTimes = [0, 1.2, 2.4, 3.6, 4.8];

const atWorkClip = new THREE.AnimationClip('AtWork', 4.8, [
  buildPosTrack('NarratronRig', workBodyTimes, [
    [-0.015, -0.012, 0.015],
    [-0.020, -0.005, 0.020],
    [-0.015, -0.018, 0.015],
    [-0.010, -0.005, 0.012],
    [-0.015, -0.012, 0.015],
  ]),
  buildQuatTrack('Torso', workBodyTimes, [
    [0.08, 0.06, 0.02],
    [0.10, 0.04, 0.01],
    [0.07, 0.08, 0.03],
    [0.09, 0.05, 0.02],
    [0.08, 0.06, 0.02],
  ]),
  buildQuatTrack('Head', workBodyTimes, [
    [-0.01, 0.07, 0.025],
    [0.01, 0.02, -0.02],
    [-0.015, 0.08, 0.03],
    [0.005, 0.03, -0.015],
    [-0.01, 0.07, 0.025],
  ]),
  buildQuatTrack('QuillArm', workTimes, workQuillArmPoses),
  buildPosTrack('QuillForearm', workTimes, workQuillForearmPos),
  buildQuatTrack('QuillForearm', workTimes, workQuillForearmPoses),
  buildPosTrack('QuillHand', workTimes, workQuillHandPos),
  buildQuatTrack('QuillHand', workTimes, workQuillHandPoses),
  buildQuatTrack('ScrollGroup', workBodyTimes, [
    [0.04, -0.06, 0.05],
    [0.06, -0.04, 0.03],
    [0.02, -0.08, 0.06],
    [0.05, -0.05, 0.04],
    [0.04, -0.06, 0.05],
  ]),
  buildQuatTrack('HairMid', workBodyTimes, [
    [0.06, -0.10, -0.10],
    [-0.06, 0.10, 0.10],
    [0.08, -0.12, -0.12],
    [-0.06, 0.08, 0.08],
    [0.06, -0.10, -0.10],
  ]),
  buildQuatTrack('HairTip', workBodyTimes, [
    [-0.08, -0.14, -0.14],
    [0.08, 0.14, 0.14],
    [-0.10, -0.16, -0.16],
    [0.08, 0.12, 0.12],
    [-0.08, -0.14, -0.14],
  ]),
  buildQuatTrack('TailUpper', workBodyTimes, [
    [0.05, -0.08, -0.10],
    [-0.05, 0.08, 0.10],
    [0.06, -0.10, -0.12],
    [-0.05, 0.06, 0.08],
    [0.05, -0.08, -0.10],
  ]),
  buildQuatTrack('TailLower', workBodyTimes, [
    [-0.06, -0.12, -0.16],
    [0.06, 0.12, 0.16],
    [-0.08, -0.14, -0.18],
    [0.06, 0.10, 0.14],
    [-0.06, -0.12, -0.16],
  ]),
  buildQuatTrack('OrbitRing', workBodyTimes, [
    [0.08, 0, -0.05],
    [0.08, -Math.PI * 0.5, -0.05],
    [0.08, -Math.PI, -0.05],
    [0.08, -Math.PI * 1.5, -0.05],
    [0.08, -fullTurn, -0.05],
  ]),
]);

const clips = [idleClip, greetingClip, amusedClip, surprisedClip, confusedClip, atWorkClip];

const exportedBuffer = Buffer.from(
  await new GLTFExporter().parseAsync(root, { binary: true, animations: clips }),
);

// Inject the 3 embedded WebP textures (normal, baseColor RGBA, emissive) into the exported GLB container
const { gltf: outGltf, binChunk: outBin } = parseGlb(exportedBuffer);

const binParts = [Buffer.from(outBin)];
let currentByteLength = binParts[0].length;

function pad4(len) {
  return (4 - (len % 4)) % 4;
}

function appendImageBufferView(imgBuffer) {
  const padBefore = pad4(currentByteLength);
  if (padBefore > 0) {
    binParts.push(Buffer.alloc(padBefore));
    currentByteLength += padBefore;
  }
  const byteOffset = currentByteLength;
  binParts.push(imgBuffer);
  currentByteLength += imgBuffer.length;
  const padAfter = pad4(currentByteLength);
  if (padAfter > 0) {
    binParts.push(Buffer.alloc(padAfter));
    currentByteLength += padAfter;
  }
  const bvIndex = outGltf.bufferViews.length;
  outGltf.bufferViews.push({
    buffer: 0,
    byteOffset,
    byteLength: imgBuffer.length,
  });
  return bvIndex;
}

const bvNormal = appendImageBufferView(normalWebp);
const bvBaseColor = appendImageBufferView(baseColorWebp);
const bvEmissive = appendImageBufferView(emissiveWebp);

outGltf.samplers = outGltf.samplers ?? [
  { magFilter: 9729, minFilter: 9987, wrapS: 10497, wrapT: 10497 },
];
outGltf.images = [
  { bufferView: bvNormal, mimeType: 'image/webp', name: 'normal' },
  { bufferView: bvBaseColor, mimeType: 'image/webp', name: 'baseColor' },
  { bufferView: bvEmissive, mimeType: 'image/webp', name: 'emissive' },
];
outGltf.textures = [
  { sampler: 0, source: 0, extensions: { EXT_texture_webp: { source: 0 } } },
  { sampler: 0, source: 1, extensions: { EXT_texture_webp: { source: 1 } } },
  { sampler: 0, source: 2, extensions: { EXT_texture_webp: { source: 2 } } },
];

const astralMatDef = outGltf.materials.find((m) => m.name === 'NarratronAstral');
if (astralMatDef) {
  astralMatDef.doubleSided = true;
  astralMatDef.alphaMode = 'BLEND';
  astralMatDef.normalTexture = { index: 0, scale: 0.70 };
  astralMatDef.pbrMetallicRoughness = {
    baseColorTexture: { index: 1 },
    metallicFactor: 0.06,
    roughnessFactor: 0.54,
  };
  astralMatDef.emissiveTexture = { index: 2 };
  astralMatDef.emissiveFactor = [0.82, 0.82, 0.90];
}

outGltf.extensionsUsed = Array.from(
  new Set([...(outGltf.extensionsUsed ?? []), 'EXT_texture_webp']),
);
outGltf.buffers[0].byteLength = currentByteLength;

let jsonChunkBytes = Buffer.from(JSON.stringify(outGltf), 'utf8');
const jsonPad = pad4(jsonChunkBytes.length);
if (jsonPad > 0) {
  jsonChunkBytes = Buffer.concat([jsonChunkBytes, Buffer.alloc(jsonPad, 0x20)]);
}

const finalBinChunk = Buffer.concat(binParts);
const totalLength = 12 + 8 + jsonChunkBytes.length + 8 + finalBinChunk.length;
const header = Buffer.alloc(12);
header.writeUInt32LE(0x46546c67, 0);
header.writeUInt32LE(2, 4);
header.writeUInt32LE(totalLength, 8);

const jsonHeader = Buffer.alloc(8);
jsonHeader.writeUInt32LE(jsonChunkBytes.length, 0);
jsonHeader.writeUInt32LE(0x4e4f534a, 4);

const binHeader = Buffer.alloc(8);
binHeader.writeUInt32LE(finalBinChunk.length, 0);
binHeader.writeUInt32LE(0x004e4942, 4);

const finalGlb = Buffer.concat([header, jsonHeader, jsonChunkBytes, binHeader, finalBinChunk]);
await writeFile(modelUrl, finalGlb);
console.log(`Built model.glb (${finalGlb.byteLength} bytes, ${clips.length} clips)`);
