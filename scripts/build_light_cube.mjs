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
const glass = new THREE.MeshStandardMaterial({
  color: 0x347f8f, emissive: 0x22768a, emissiveIntensity: 0.14,
  roughness: 0.24, transparent: true, opacity: 0.25,
});
const frame = new THREE.MeshStandardMaterial({
  color: 0x9be0e3, emissive: 0x51b4bc, emissiveIntensity: 0.3,
  metalness: 0.55, roughness: 0.3,
});
const innerLight = new THREE.MeshStandardMaterial({
  name: 'InnerLight', color: 0x704225, emissive: 0xffab38,
  emissiveIntensity: 0.26, flatShading: true,
});
const innerMist = new THREE.MeshStandardMaterial({
  name: 'InnerLight', color: 0xe9a860, emissive: 0xff982e,
  emissiveIntensity: 0.06, opacity: 0.058, transparent: true,
});

cube.add(new THREE.Mesh(new RoundedBoxGeometry(1.12, 1.12, 1.12, 3, 0.055), glass));
const core = new THREE.Mesh(new THREE.IcosahedronGeometry(0.30), innerLight);
core.rotation.set(0.19, -0.27, 0.16);
cube.add(core);
const halo = new THREE.SphereGeometry(1, 24, 16);
for (const radius of [0.31, 0.34, 0.37, 0.41, 0.45]) {
  const mist = new THREE.Mesh(halo, innerMist);
  mist.scale.setScalar(radius);
  cube.add(mist);
}

const beam = new THREE.CylinderGeometry(0.012, 0.012, 1.08, 6);
const bead = new THREE.SphereGeometry(0.028, 8, 6);
for (const a of [-0.54, 0.54]) {
  for (const b of [-0.54, 0.54]) {
    const x = new THREE.Mesh(beam, frame);
    x.rotation.z = Math.PI / 2;
    x.position.set(0, a, b);
    const y = new THREE.Mesh(beam, frame);
    y.position.set(a, 0, b);
    const z = new THREE.Mesh(beam, frame);
    z.rotation.x = Math.PI / 2;
    z.position.set(a, b, 0);
    cube.add(x, y, z);
    for (const c of [-0.54, 0.54]) {
      const corner = new THREE.Mesh(bead, frame);
      corner.position.set(a, b, c);
      cube.add(corner);
    }
  }
}

const sparkGeometry = new THREE.OctahedronGeometry(0.024);
for (const [x, y, z] of [
  [-0.29, -0.30, 0.18], [0.14, 0.37, 0.06], [0.37, 0.09, -0.15],
  [-0.31, 0.19, -0.22], [0.08, -0.35, -0.22], [0.21, 0.23, 0.30],
]) {
  const spark = new THREE.Mesh(sparkGeometry, innerLight);
  spark.position.set(x, y, z);
  cube.add(spark);
}

const turn = [0, 0.5, 1, 1.5, 2].flatMap((halfTurns) =>
  new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI * halfTurns).toArray());
const clips = [
  new THREE.AnimationClip('SlowTurn', 20, [
    new THREE.QuaternionKeyframeTrack('LightCube.quaternion', [0, 5, 10, 15, 20], turn),
  ]),
];

const model = await new GLTFExporter().parseAsync(cube, { binary: true, animations: clips });
await writeFile(new URL('../adventures/example_adventure/avatars/light-cube/model.glb', import.meta.url), Buffer.from(model));
