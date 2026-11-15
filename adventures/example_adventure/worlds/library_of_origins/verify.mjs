// Run from the repository root: node adventures/example_adventure/worlds/library_of_origins/verify.mjs
import assert from 'node:assert/strict';
import { readFile, stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const root = path.dirname(fileURLToPath(import.meta.url));
const manifest = JSON.parse(await readFile(path.join(root, 'world.json'), 'utf8'));
function parseGLB(buffer) {
  assert.equal(buffer.toString('utf8', 0, 4), 'glTF');
  assert.equal(buffer.readUInt32LE(4), 2);
  assert.equal(buffer.readUInt32LE(8), buffer.length);
  assert.equal(buffer.readUInt32LE(16), 0x4e4f534a);
  return JSON.parse(buffer.toString('utf8', 20, 20 + buffer.readUInt32LE(12)));
}
const bytes = await readFile(path.join(root, manifest.scene));
assert.ok(bytes.length < 5_000_000);
assert.equal(bytes.length, manifest.initial_download_bytes);
const gltf = parseGLB(bytes);
const nodes = new Map(gltf.nodes.map(node => [node.name, node]));
assert.equal(nodes.size, gltf.nodes.length, 'Node names must be unique');
for (const entity of manifest.entities) {
  const node = nodes.get(entity.id);
  assert.equal(node.extras.entity_id, entity.id);
  const actual = node.translation ?? [0, 0, 0];
  actual.forEach((value, index) => assert.ok(Math.abs(value - entity.position[index]) < .00001));
  const reusable = parseGLB(await readFile(path.join(root, entity.model)));
  const localRoot = reusable.nodes.find(node => node.name === entity.id);
  assert.deepEqual(localRoot.translation ?? [0, 0, 0], [0, 0, 0]);
  assert.ok(!reusable.nodes.some(node => node.name === 'environment'));
}
const environment = parseGLB(await readFile(path.join(root, manifest.environment)));
assert.ok(!environment.nodes.some(node => manifest.entities.some(entity => entity.id === node.name)));
for (const name of Object.keys(manifest.spawns)) assert.ok(nodes.has(`spawn_${name}`));
for (const target of manifest.interactions) {
  assert.ok(nodes.has(`target_${target.id}`));
  assert.ok(nodes.has(target.entity_id));
}
for (const trigger of manifest.triggers) assert.ok(nodes.has(`trigger_${trigger.id}`));
for (const collider of manifest.colliders) {
  assert.ok(nodes.has(collider.id));
  assert.ok(nodes.has(collider.entity_id));
  assert.ok(collider.size.every(value => value > 0));
}
const doorClip = gltf.animations.find(clip => clip.name === 'door_open');
assert.ok(doorClip.channels.some(channel => gltf.nodes[channel.target.node].name === 'door_hinge' && channel.target.path === 'rotation'));
assert.equal(gltf.asset.version, '2.0');
const triangles = gltf.meshes.reduce((total, mesh) => total + mesh.primitives.reduce((sum, primitive) => sum + gltf.accessors[primitive.indices].count / 3, 0), 0);
console.log(`Asset checks passed: ${bytes.length.toLocaleString()} bytes, ${triangles.toLocaleString()} triangles, ${manifest.entities.length} independent entities.`);

const types = { '.html': 'text/html', '.glb': 'model/gltf-binary', '.json': 'application/json' };
const server = createServer(async (request, response) => {
  const name = decodeURIComponent(new URL(request.url, 'http://localhost').pathname).slice(1) || 'preview.html';
  const target = path.resolve(root, name);
  if (!target.startsWith(root + path.sep)) { response.writeHead(403).end(); return; }
  try {
    response.writeHead(200, { 'Content-Type': types[path.extname(target)] ?? 'application/octet-stream' });
    response.end(await readFile(target));
  } catch { response.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  const failures = [];
  page.on('pageerror', error => failures.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/preview.html`);
  await page.waitForSelector('body[data-ready="true"]', { timeout: 60000 });
  // Read geometry after the loader's Y-up conversion, as actually consumed by Three.js.
  const metrics = await page.evaluate(async () => {
    const THREE = await import('three');
    const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
    const gltf = await new GLTFLoader().loadAsync('./library.glb');
    const bounds = new THREE.Box3().setFromObject(gltf.scene);
    const size = bounds.getSize(new THREE.Vector3()).toArray();
    const hinge = gltf.scene.getObjectByName('door_hinge');
    const mixer = new THREE.AnimationMixer(gltf.scene);
    const clip = THREE.AnimationClip.findByName(gltf.animations, 'door_open');
    mixer.clipAction(clip).play();
    mixer.setTime(1);
    return { size, hingeAngle: hinge.rotation.y, duration: clip.duration };
  });
  assert.ok(Math.abs(metrics.size[0] - 12.5) < .01);
  assert.ok(Math.abs(metrics.size[2] - 6.5) < .01);
  assert.equal(metrics.duration, 2);
  assert.ok(metrics.hingeAngle < -.2, 'Door must rotate about browser Y, not X or Z');
  await page.screenshot({ path: path.join(root, 'preview_browser.png') });
  await page.getByRole('button', { name: 'Open door', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('#door').disabled);
  await page.getByRole('button', { name: 'Hide key', exact: true }).click();
  await page.getByRole('status').filter({ hasText: 'Door open · Key hidden' }).waitFor();
  await page.getByRole('button', { name: 'Close door', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('#door').disabled);
  await page.getByRole('button', { name: 'Reset view', exact: true }).click();
  await page.getByRole('status').filter({ hasText: 'Door closed · Key visible' }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390);
  await page.screenshot({ path: path.join(root, 'preview_mobile.png') });
  assert.deepEqual(failures, []);
  console.log(`Browser checks passed: ${metrics.size.map(value => value.toFixed(2)).join(' × ')} m bounds, Y-up door animation, key toggle, reset, mobile layout.`);
  assert.ok((await stat(path.join(root, 'library.blend'))).size > 0);
} finally {
  if (browser) await browser.close();
  await new Promise(resolve => server.close(resolve));
}
