import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const rootDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const frontendDir = path.join(rootDir, 'frontend');
const outputDir = path.join(rootDir, 'static', 'generated');
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
  console.log('No source files in frontend/; ensured static/generated exists.');
  process.exit(0);
}

const tscArgs = ['-p', 'tsconfig.build.json'];
if (isWatch) {
  tscArgs.push('--watch', '--preserveWatchOutput');
}
runTsc(tscArgs);
