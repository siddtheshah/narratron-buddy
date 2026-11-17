import { writeFile } from 'node:fs/promises';
import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

// GLTFExporter assembles its binary container with the browser FileReader API.
globalThis.FileReader = class {
  async readAsArrayBuffer(blob) {
    this.result = await blob.arrayBuffer();
    this.onloadend();
  }
};

const cube = new THREE.Group();
cube.name = 'LightCube';
const tilt = new THREE.Group();
tilt.name = 'LightCubeTilt';
tilt.add(cube);

const glass = new THREE.MeshStandardMaterial({
  color: 0x968d80,
  roughness: 0.31, transparent: true, opacity: 0.21,
});
const frame = new THREE.MeshStandardMaterial({
  name: 'HammeredBronze', vertexColors: true, metalness: 0.36, roughness: 0.5,
});

const edgeScale = 0.75;
const cubeSide = 1.12 * edgeScale;
cube.add(new THREE.Mesh(new RoundedBoxGeometry(cubeSide, cubeSide, cubeSide, 3, 0.055 * edgeScale), glass));
const sparkLight = new THREE.PointLight(0xffc477, 2.5 * edgeScale ** 2);
sparkLight.name = 'SparkLight';
cube.add(sparkLight);

function tintBronze(geometry, scale) {
  const positions = geometry.getAttribute('position');
  const recess = new THREE.Color(0x716960);
  const highlight = new THREE.Color(0xb4a89c);
  const color = new THREE.Color();
  const colors = [];
  for (let index = 0; index < positions.count; index++) {
    const x = positions.getX(index) * scale;
    const y = positions.getY(index) * scale;
    const z = positions.getZ(index) * scale;
    const grain = 0.53
      + 0.23 * Math.sin(2.7 * x + 1.6 * y - 1.5 * z)
      + 0.15 * Math.sin(1.1 * x - 3.1 * y + 2.6 * z)
      + 0.09 * Math.cos(3.1 * x + 2.3 * y + 1.7 * z);
    color.lerpColors(recess, highlight, grain);
    colors.push(color.r, color.g, color.b);
  }
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  return geometry;
}

const railLength = 1.08 * edgeScale;
const railRadius = 0.0342;
const corners = [-railLength / 2, railLength / 2];
const beam = tintBronze(new THREE.CylinderGeometry(railRadius, railRadius, railLength, 10, 24), 29);
const bead = tintBronze(new THREE.SphereGeometry(railRadius * 1.1, 10, 8), 37);
for (const a of corners) {
  for (const b of corners) {
    const x = new THREE.Mesh(beam, frame);
    x.rotation.z = Math.PI / 2;
    x.position.set(0, a, b);
    const y = new THREE.Mesh(beam, frame);
    y.position.set(a, 0, b);
    const z = new THREE.Mesh(beam, frame);
    z.rotation.x = Math.PI / 2;
    z.position.set(a, b, 0);
    cube.add(x, y, z);
    for (const c of corners) {
      const corner = new THREE.Mesh(bead, frame);
      corner.position.set(a, b, c);
      cube.add(corner);
    }
  }
}

const spinPeriod = 20;
const precessionPeriod = 40;
const tiltAngle = THREE.MathUtils.degToRad(9);
const fullTurn = Math.PI * 2;
const spinTimes = Array.from({ length: 9 }, (_, index) => index * 5);
const vertical = new THREE.Vector3(0, 1, 0);
const spin = spinTimes.flatMap((time) =>
  new THREE.Quaternion().setFromAxisAngle(vertical, fullTurn * time / spinPeriod).toArray());
const tiltTimes = Array.from({ length: 17 }, (_, index) => index * 2.5);
const precession = tiltTimes.flatMap((time) => {
  const phase = fullTurn * time / precessionPeriod;
  const axis = new THREE.Vector3(Math.cos(phase), 0, Math.sin(phase));
  return new THREE.Quaternion().setFromAxisAngle(axis, tiltAngle).toArray();
});
tilt.quaternion.fromArray(precession);

const orbitLight = new THREE.MeshStandardMaterial({
  name: 'InnerLight', color: 0x996131, emissive: 0xffac50,
  emissiveIntensity: 1.2, roughness: 0.7,
});
const orbitGeometry = new THREE.IcosahedronGeometry(0.019);
const orbitTimes = Array.from({ length: 161 }, (_, index) => index * precessionPeriod / 160);
const orbitTracks = [
  { radius: 0.34, pitch: 0.45, yaw: 0.1, phase: 0.7, turns: 5 },
  { radius: 0.37, pitch: -0.9, yaw: 0.8, phase: 3.4, turns: -4 },
  { radius: 0.40, pitch: 0.72, yaw: -0.7, phase: 4.5, turns: 3 },
  { radius: 0.44, pitch: -0.28, yaw: 1.2, phase: 2.0, turns: -2 },
].map(({ radius, pitch, yaw, phase, turns }, index) => {
  const point = new THREE.Mesh(orbitGeometry, orbitLight);
  point.name = `OrbitLight${index + 1}`;
  const plane = new THREE.Quaternion().setFromEuler(new THREE.Euler(pitch, yaw, 0));
  const positions = orbitTimes.flatMap((time) => {
    const angle = phase + fullTurn * turns * time / precessionPeriod;
    return new THREE.Vector3(radius * Math.cos(angle), radius * Math.sin(angle), 0)
      .applyQuaternion(plane).multiplyScalar(edgeScale).toArray();
  });
  point.position.fromArray(positions);
  cube.add(point);
  return new THREE.VectorKeyframeTrack(`${point.name}.position`, orbitTimes, positions);
});

const clips = [
  new THREE.AnimationClip('SlowTurn', precessionPeriod, [
    new THREE.QuaternionKeyframeTrack('LightCube.quaternion', spinTimes, spin),
    new THREE.QuaternionKeyframeTrack('LightCubeTilt.quaternion', tiltTimes, precession),
    ...orbitTracks,
  ]),
];

const model = await new GLTFExporter().parseAsync(tilt, { binary: true, animations: clips });
await writeFile(new URL('../adventures/example_adventure/avatars/light-cube/model.glb', import.meta.url), Buffer.from(model));
