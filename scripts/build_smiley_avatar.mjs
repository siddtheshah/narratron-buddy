import { writeFile } from 'node:fs/promises';
import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';

// The exporter uses the browser FileReader API to assemble the GLB.
globalThis.FileReader = class {
  async readAsArrayBuffer(blob) {
    this.result = await blob.arrayBuffer();
    this.onloadend();
  }
};

const smiley = new THREE.Group();
smiley.name = 'Smiley';
smiley.position.y = 0.28;

const face = new THREE.Group();
face.quaternion.setFromUnitVectors(
  new THREE.Vector3(0, 0, 1),
  new THREE.Vector3(2.4, 1.62, 5.6).normalize(),
);
smiley.add(face);

const yellow = new THREE.MeshStandardMaterial({ color: 0xffc329, roughness: 0.48 });
const ink = new THREE.MeshBasicMaterial({ color: 0x362315, toneMapped: false });
const disk = new THREE.Mesh(new THREE.SphereGeometry(0.9, 40, 24), yellow);
disk.scale.z = 0.32;
face.add(disk);

function oval(parent, x, y, width, height) {
  const shape = new THREE.Shape();
  shape.absellipse(x, y, width / 2, height / 2, 0, Math.PI * 2);
  feature(parent, shape);
}

function feature(parent, shape) {
  parent.add(new THREE.Mesh(new THREE.ShapeGeometry(shape, 20), ink));
}

function expression(name) {
  const group = new THREE.Group();
  group.name = name;
  group.position.z = 0.296;
  face.add(group);
  return group;
}

function closedEyes(parent) {
  for (const x of [-0.29, 0.29]) {
    const eye = new THREE.Shape();
    eye.moveTo(x - 0.13, 0.18);
    eye.quadraticCurveTo(x, 0.46, x + 0.13, 0.18);
    eye.quadraticCurveTo(x + 0.12, 0.12, x + 0.07, 0.16);
    eye.quadraticCurveTo(x, 0.30, x - 0.07, 0.16);
    eye.quadraticCurveTo(x - 0.12, 0.12, x - 0.13, 0.18);
    feature(parent, eye);
  }
}

const smile = expression('SmileFace');
for (const x of [-0.29, 0.29]) oval(smile, x, 0.24, 0.14, 0.24);
const curve = new THREE.Shape();
curve.moveTo(-0.33, -0.14);
curve.quadraticCurveTo(0, -0.49, 0.33, -0.14);
curve.quadraticCurveTo(0.40, -0.14, 0.36, -0.22);
curve.quadraticCurveTo(0, -0.64, -0.36, -0.22);
curve.quadraticCurveTo(-0.40, -0.14, -0.33, -0.14);
feature(smile, curve);

const laugh = expression('LaughFace');
closedEyes(laugh);
const grin = new THREE.Shape();
grin.moveTo(-0.38, -0.12);
grin.quadraticCurveTo(0, -0.24, 0.38, -0.12);
grin.bezierCurveTo(0.36, -0.62, -0.36, -0.62, -0.38, -0.12);
feature(laugh, grin);

const surprise = expression('SurpriseFace');
for (const x of [-0.29, 0.29]) oval(surprise, x, 0.26, 0.19, 0.29);
oval(surprise, 0, -0.28, 0.28, 0.32);

const think = expression('ThinkFace');
oval(think, -0.28, 0.24, 0.15, 0.22);
oval(think, 0.29, 0.24, 0.15, 0.10);
oval(think, 0.07, -0.27, 0.26, 0.075);

const expressions = [smile, laugh, surprise, think];
for (const group of [laugh, surprise, think]) group.scale.setScalar(0);

function expressionTracks(times, faces) {
  return expressions.map((group) => new THREE.VectorKeyframeTrack(
    `${group.name}.scale`,
    times,
    faces.flatMap((face) => group === face ? [1, 1, 1] : [0, 0, 0]),
    THREE.InterpolateDiscrete,
  ));
}

function motionTracks(times, heights, rolls) {
  return [
    new THREE.VectorKeyframeTrack(
      'Smiley.position', times, heights.flatMap((height) => [0, 0.28 + height, 0]),
    ),
    new THREE.QuaternionKeyframeTrack(
      'Smiley.quaternion', times,
      rolls.flatMap((roll) => new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0, roll)).toArray()),
    ),
  ];
}

function reaction(name, face, duration, heights, rolls) {
  return new THREE.AnimationClip(name, duration, [
    ...expressionTracks([0, 0.18, duration - 0.16, duration], [smile, face, smile, smile]),
    ...motionTracks([0, 0.25, duration * 0.60, duration], heights, rolls),
  ]);
}

const clips = [
  new THREE.AnimationClip('Smile', 3, [
    ...expressionTracks([0, 3], [smile, smile]),
    ...motionTracks([0, 1.5, 3], [0, 0.075, 0], [0, 0.035, 0]),
  ]),
  reaction('Laugh', laugh, 1.5, [0, 0.16, 0.12, 0], [0, 0.10, -0.09, 0]),
  reaction('Surprise', surprise, 1.35, [0, 0.20, 0.09, 0], [0, -0.08, 0.025, 0]),
  reaction('Think', think, 1.9, [0, 0.035, 0.03, 0], [0, -0.08, -0.16, 0]),
];

const binary = await new GLTFExporter().parseAsync(smiley, { binary: true, animations: clips });
const output = new URL('../example_theater/avatars/example_avatar/model.glb', import.meta.url);
await writeFile(output, Buffer.from(binary));
