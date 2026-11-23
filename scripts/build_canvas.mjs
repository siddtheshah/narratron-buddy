import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const rootDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const frontendDir = path.join(rootDir, 'frontend');
const outputDir = path.join(rootDir, 'frontend', 'static', 'generated');
const excludedFrontendDirs = new Set(['static', 'templates']);
const tscBin = path.join(
  rootDir,
  'node_modules',
  '.bin',
  process.platform === 'win32' ? 'tsc.cmd' : 'tsc',
);

function hasSourceFiles(dir) {
  if (!fs.existsSync(dir)) {
    return false;
  }
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (dir === frontendDir && excludedFrontendDirs.has(entry.name)) {
        continue;
      }
      if (hasSourceFiles(path.join(dir, entry.name))) {
        return true;
      }
    } else if (/\.ts$/i.test(entry.name)) {
      return true;
    }
  }
  return false;
}

function runTsc(args) {
  const result = spawnSync(tscBin, args, {
    cwd: rootDir,
    stdio: 'inherit',
    shell: process.platform === 'win32',
  });
  if (result.error) {
    throw result.error;
  }
  if ((result.status ?? 1) !== 0) {
    process.exit(result.status ?? 1);
  }
}

const args = process.argv.slice(2);
const isCheck = args.includes('--check');
const isWatch = args.includes('--watch');
const hasInputs = hasSourceFiles(frontendDir);

if (isCheck) {
  runTsc(['-p', 'tsconfig.json', '--noEmit']);
  if (hasInputs) {
    runTsc(['-p', 'tsconfig.build.json', '--noEmit']);
  }
  process.exit(0);
}

if (!isWatch) {
  fs.rmSync(outputDir, { recursive: true, force: true });
}
fs.mkdirSync(outputDir, { recursive: true });

if (!hasInputs && !isWatch) {
  console.log('No source files in frontend/; ensured frontend/static/generated exists.');
  process.exit(0);
}

const tscArgs = ['-p', 'tsconfig.build.json'];
const threeRoot = path.join(rootDir, 'node_modules', 'three');
for (const file of [
  'LICENSE',
  'build/three.module.min.js',
  'build/three.core.min.js',
  'examples/jsm/loaders/GLTFLoader.js',
  'examples/jsm/utils/BufferGeometryUtils.js',
]) {
  const destination = path.join(outputDir, 'vendor', 'three', file);
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.copyFileSync(path.join(threeRoot, file), destination);
}
if (isWatch) {
  tscArgs.push('--watch', '--preserveWatchOutput');
}
runTsc(tscArgs);
