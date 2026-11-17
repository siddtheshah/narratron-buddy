import { writeFile } from 'node:fs/promises';
import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';

// GLTFExporter uses FileReader to assemble the binary container in the browser.
globalThis.FileReader = class {
  async readAsArrayBuffer(blob) {
    this.result = await blob.arrayBuffer();
    this.onloadend();
  }
};

const cube = new THREE.Group();
cube.name = 'Cube';
const eyes = new THREE.Group();
eyes.name = 'Eyes';
eyes.position.set(0, 0.08, 0.608);
cube.add(eyes);

const shell = new THREE.MeshStandardMaterial({ color: 0x267688, metalness: 0.28, roughness: 0.32 });
const brass = new THREE.MeshStandardMaterial({ color: 0xf1aa62, metalness: 0.35, roughness: 0.37 });
const screen = new THREE.MeshStandardMaterial({ color: 0x10232d, roughness: 0.54 });
const mint = new THREE.MeshStandardMaterial({
  color: 0x97ffe5, emissive: 0x36eebd, emissiveIntensity: 0.65, roughness: 0.4,
});

function roundedPanel(parent, name, width, height, radius, depth, bevel, material, x, y, z) {
  const halfWidth = width / 2;
  const halfHeight = height / 2;
  const outline = new THREE.Shape();
  outline.moveTo(-halfWidth + radius, -halfHeight);
  outline.lineTo(halfWidth - radius, -halfHeight);
  outline.quadraticCurveTo(halfWidth, -halfHeight, halfWidth, -halfHeight + radius);
  outline.lineTo(halfWidth, halfHeight - radius);
  outline.quadraticCurveTo(halfWidth, halfHeight, halfWidth - radius, halfHeight);
  outline.lineTo(-halfWidth + radius, halfHeight);
  outline.quadraticCurveTo(-halfWidth, halfHeight, -halfWidth, halfHeight - radius);
  outline.lineTo(-halfWidth, -halfHeight + radius);
  outline.quadraticCurveTo(-halfWidth, -halfHeight, -halfWidth + radius, -halfHeight);
  const geometry = new THREE.ExtrudeGeometry(outline, {
    depth, bevelSize: bevel, bevelThickness: bevel, bevelSegments: 3, curveSegments: 4,
  });
  geometry.translate(0, 0, -depth / 2);
  const panel = new THREE.Mesh(geometry, material);
  panel.name = name;
  panel.position.set(x, y, z);
  parent.add(panel);
  return panel;
}

roundedPanel(cube, 'Shell', 1, 1, 0.15, 0.98, 0.065, shell, 0, 0, 0);
roundedPanel(cube, 'FaceRim', 0.88, 0.72, 0.14, 0.032, 0.016, brass, 0, 0, 0.55);
roundedPanel(cube, 'Face', 0.81, 0.65, 0.12, 0.022, 0.012, screen, 0, 0, 0.578);
roundedPanel(eyes, 'LeftEye', 0.105, 0.183, 0.052, 0.009, 0.012, mint, -0.18, 0, 0);
roundedPanel(eyes, 'RightEye', 0.105, 0.183, 0.052, 0.009, 0.012, mint, 0.18, 0, 0);
roundedPanel(cube, 'Mouth', 0.14, 0.045, 0.022, 0.009, 0.009, mint, 0, -0.143, 0.608);

const sideRing = new THREE.Mesh(new THREE.TorusGeometry(0.20, 0.026, 8, 24), brass);
sideRing.name = 'SideRing';
sideRing.rotation.y = Math.PI / 2;
sideRing.position.set(0.56, 0, 0);
cube.add(sideRing);

for (const offset of [-0.075, 0, 0.075]) {
  const vent = roundedPanel(cube, 'Vent', 0.16, 0.022, 0.01, 0.01, 0.008, mint, 0.568, offset, 0);
  vent.rotation.y = Math.PI / 2;
}
const badge = roundedPanel(cube, 'TopBadge', 0.17, 0.17, 0.046, 0.012, 0.012, brass, 0, 0.565, 0);
badge.rotation.set(-Math.PI / 2, 0, Math.PI / 4);

function poseTrack(times, poses) {
  return new THREE.QuaternionKeyframeTrack('Cube.quaternion', times, poses.flatMap(
    ([pitch, yaw, roll]) => new THREE.Quaternion().setFromEuler(new THREE.Euler(pitch, yaw, roll)).toArray(),
  ));
}

function positionTrack(times, heights) {
  return new THREE.VectorKeyframeTrack('Cube.position', times, heights.flatMap((height) => [0, height, 0]));
}

function scaleTrack(part, times, heights) {
  return new THREE.VectorKeyframeTrack(`${part}.scale`, times, heights.flatMap((height) => [1, height, 1]));
}

const neutral = [0, 0, 0];
const clips = [
  new THREE.AnimationClip('Hover', 3.6, [
    positionTrack([0, 0.9, 1.8, 2.7, 3.6], [0, 0.055, 0.095, 0.055, 0]),
    poseTrack([0, 0.9, 1.8, 2.7, 3.6], [neutral, [0.025, 0.055, 0.03], neutral, [-0.025, -0.055, -0.03], neutral]),
    scaleTrack('Eyes', [0, 2.4, 2.48, 2.58, 3.6], [1, 1, 0.12, 1, 1]),
  ]),
  new THREE.AnimationClip('Bow', 1.4, [
    positionTrack([0, 0.3, 0.62, 0.95, 1.4], [0, 0.16, 0.03, 0.08, 0]),
    poseTrack([0, 0.3, 0.62, 0.95, 1.4], [neutral, [-0.13, -0.16, 0.16], [0.42, -0.08, 0.08], [-0.08, 0, -0.04], neutral]),
  ]),
  new THREE.AnimationClip('Ponder', 2.4, [
    poseTrack([0, 0.4, 0.8, 1.5, 2, 2.4], [neutral, [0.08, -0.12, 0.22], [-0.12, -0.24, 0.26], [-0.1, -0.28, 0.24], [0.08, 0.08, -0.08], neutral]),
    scaleTrack('Eyes', [0, 0.4, 1.7, 2.1, 2.4], [1, 0.65, 0.65, 1, 1]),
  ]),
  new THREE.AnimationClip('Chatter', 1.95, [
    poseTrack([0, 0.25, 0.52, 0.8, 1.1, 1.4, 1.7, 1.95], [neutral, [0.08, -0.06, 0.06], [-0.08, 0.03, -0.04], [0.1, 0.08, -0.07], [-0.04, 0.03, 0.04], [0.08, -0.04, 0.03], [-0.06, 0, 0], neutral]),
    scaleTrack('Mouth', [0, 0.16, 0.30, 0.42, 0.56, 0.69, 0.82, 0.96, 1.18, 1.34, 1.49, 1.65, 1.82, 1.95], [1, 2.9, 1.1, 2.3, 1, 3, 1.4, 2.7, 1, 3.1, 1.2, 2.5, 1, 1]),
  ]),
  new THREE.AnimationClip('SpinHop', 1.65, [
    positionTrack([0, 0.22, 0.5, 0.8, 1.2, 1.42, 1.65], [0, -0.10, 0.22, 0.42, 0.12, -0.08, 0]),
    poseTrack([0, 0.22, 0.5, 0.78, 1.04, 1.34, 1.65], [
      neutral, [0.13, -0.25, -0.10], [0, 1.1, 0.18], [0, 2.65, 0.12],
      [0, 4.3, -0.18], [0.12, 6.05, 0.09], [0, 2 * Math.PI, 0],
    ]),
  ]),
  new THREE.AnimationClip('Startle', 1.35, [
    positionTrack([0, 0.18, 0.4, 0.65, 0.95, 1.35], [0, 0.28, 0.21, -0.08, 0.05, 0]),
    poseTrack([0, 0.18, 0.4, 0.65, 0.95, 1.35], [neutral, [-0.26, 0.03, -0.13], [-0.21, -0.08, 0.1], [0.10, 0.07, -0.05], neutral, neutral]),
    scaleTrack('Eyes', [0, 0.16, 0.49, 0.8, 1.35], [1, 1.45, 1.4, 0.35, 1]),
    scaleTrack('Mouth', [0, 0.18, 0.55, 1.1, 1.35], [1, 3.2, 2.7, 1, 1]),
  ]),
  new THREE.AnimationClip('PerkUp', 1.15, [
    positionTrack([0, 0.23, 0.58, 1.15], [0, 0.11, 0.085, 0]),
    poseTrack([0, 0.23, 0.58, 1.15], [neutral, [-0.16, 0.16, 0.14], [-0.12, 0.14, 0.11], neutral]),
    scaleTrack('Eyes', [0, 0.23, 0.58, 1.15], [1, 1.35, 1.24, 1]),
  ]),
  new THREE.AnimationClip('PokeBounce', 0.95, [
    positionTrack([0, 0.12, 0.32, 0.56, 0.75, 0.95], [0, -0.12, 0.23, -0.05, 0.045, 0]),
    poseTrack([0, 0.12, 0.32, 0.56, 0.75, 0.95], [
      neutral, [0.24, 0, -0.18], [-0.14, -0.12, 0.2], [0.08, 0.10, -0.07], [-0.04, 0, 0.04], neutral,
    ]),
    scaleTrack('Cube', [0, 0.12, 0.32, 0.56, 0.75, 0.95], [1, 0.75, 1.12, 0.92, 1.04, 1]),
    scaleTrack('Eyes', [0, 0.08, 0.25, 0.4, 0.62, 0.95], [1, 0.16, 0.16, 1.22, 1, 1]),
  ]),
];

const binary = await new GLTFExporter().parseAsync(cube, { binary: true, animations: clips });
await writeFile(new URL('./avatar_packages/cube/model.glb', import.meta.url), Buffer.from(binary));
