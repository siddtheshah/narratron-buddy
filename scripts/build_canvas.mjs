import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const rootDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const frontendDir = path.join(rootDir, 'frontend');
const outputDir = path.join(frontendDir, 'static', 'generated');
const testlabDir = path.join(rootDir, 'testlab');
const testlabFrontendDir = path.join(testlabDir, 'frontend');
const testlabOutputDir = path.join(testlabFrontendDir, 'static', 'generated');
const testlabTsconfig = path.join(testlabDir, 'tsconfig.json');
const excludedDirs = new Set(['static', 'templates']);
const tscBin = path.join(
  rootDir,
  'node_modules',
  '.bin',
  process.platform === 'win32' ? 'tsc.cmd' : 'tsc',
);

function hasSourceFiles(root, dir = root) {
  if (!fs.existsSync(dir)) {
    return false;
  }
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (dir === root && excludedDirs.has(entry.name)) {
        continue;
      }
      if (hasSourceFiles(root, path.join(dir, entry.name))) {
        return true;
      }
    } else if (/\.ts$/i.test(entry.name) && !/_test\.ts$/i.test(entry.name)) {
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

function hasDeclarationFiles(dir) {
  if (!fs.existsSync(dir)) {
    return false;
  }
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (hasDeclarationFiles(path.join(dir, entry.name))) {
        return true;
      }
    } else if (/\.d\.ts$/i.test(entry.name)) {
      return true;
    }
  }
  return false;
}

const args = process.argv.slice(2);
const isCheck = args.includes('--check');
const isWatch = args.includes('--watch');
const hasInputs = hasSourceFiles(frontendDir);
const hasTestlabInputs = fs.existsSync(testlabTsconfig) && hasSourceFiles(testlabFrontendDir);

if (isCheck) {
  const hasBuiltDeclarations = hasDeclarationFiles(outputDir);
  runTsc(['-p', 'tsconfig.json', '--noEmit']);
  if (hasInputs) {
    runTsc(['-p', 'tsconfig.build.json', '--noEmit']);
  }
  if (hasTestlabInputs && hasBuiltDeclarations) {
    runTsc(['-p', 'testlab/tsconfig.json', '--noEmit']);
  }
  process.exit(0);
}

if (!isWatch) {
  fs.rmSync(outputDir, { recursive: true, force: true });
  if (hasTestlabInputs) {
    fs.rmSync(testlabOutputDir, { recursive: true, force: true });
  }
}
fs.mkdirSync(outputDir, { recursive: true });
if (hasTestlabInputs) {
  fs.mkdirSync(testlabOutputDir, { recursive: true });
}

if (!hasInputs && !hasTestlabInputs && !isWatch) {
  console.log('No source files in frontend/ or testlab/frontend/; ensured generated directories exist.');
  process.exit(0);
}

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
  const watchProjects = [];
  if (hasInputs) {
    watchProjects.push('tsconfig.build.json');
  }
  if (hasTestlabInputs) {
    watchProjects.push('testlab/tsconfig.json');
  }
  runTsc(['-b', ...watchProjects, '--watch', '--preserveWatchOutput']);
  process.exit(0);
}

if (hasInputs) {
  runTsc(['-p', 'tsconfig.build.json']);
}
if (hasTestlabInputs) {
  runTsc(['-p', 'testlab/tsconfig.json']);
}
