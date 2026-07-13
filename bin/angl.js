#!/usr/bin/env node
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const root = path.resolve(__dirname, '..');
const pkg = require(path.join(root, 'package.json'));
const venvRoot = process.env.ANGL_NPM_VENV || path.join(os.homedir(), '.angl', 'npm', pkg.version);
const isWindows = process.platform === 'win32';
const venvPython = isWindows
  ? path.join(venvRoot, 'Scripts', 'python.exe')
  : path.join(venvRoot, 'bin', 'python');
const anglBin = isWindows
  ? path.join(venvRoot, 'Scripts', 'angl.exe')
  : path.join(venvRoot, 'bin', 'angl');
const marker = path.join(venvRoot, '.angl-npm-installed');
const packageSource = path.join(venvRoot, 'package-src');
const runtimeIdentity = `${pkg.name}@${pkg.version}\n`;

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    stdio: options.stdio || 'inherit',
    encoding: 'utf8',
  });
  if (result.error) {
    if (result.error.code === 'ENOENT') return { status: 127, missing: true };
    throw result.error;
  }
  return result;
}

function findPython() {
  const candidates = process.env.ANGL_PYTHON
    ? [process.env.ANGL_PYTHON]
    : isWindows
      ? ['py', 'python3', 'python']
      : ['python3', 'python'];

  for (const command of candidates) {
    const args = command === 'py' ? ['-3', '--version'] : ['--version'];
    const result = run(command, args, { stdio: 'pipe' });
    if (!result.missing && result.status === 0) return command;
  }
  return null;
}

function pythonArgs(command, args) {
  return command === 'py' ? ['-3', ...args] : args;
}

function installSource() {
  if (!root.includes('.zip/')) return root;

  fs.rmSync(packageSource, { recursive: true, force: true });
  fs.mkdirSync(packageSource, { recursive: true });
  for (const entry of ['package.json', 'README.md', 'setup.py', 'angl', 'bin']) {
    copyTree(path.join(root, entry), path.join(packageSource, entry));
  }
  return packageSource;
}

function copyTree(src, dest) {
  const stat = fs.statSync(src);
  if (stat.isDirectory()) {
    fs.mkdirSync(dest, { recursive: true });
    for (const entry of fs.readdirSync(src)) {
      copyTree(path.join(src, entry), path.join(dest, entry));
    }
    return;
  }

  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, fs.readFileSync(src));
  fs.chmodSync(dest, stat.mode);
}

function ensureRuntime() {
  const installedIdentity = fs.existsSync(marker)
    ? fs.readFileSync(marker, 'utf8')
    : '';
  if (fs.existsSync(anglBin) && installedIdentity === runtimeIdentity) return;

  const python = findPython();
  if (!python) {
    console.error('error: angl requires Python 3.9+ on PATH');
    console.error('install python3, or set ANGL_PYTHON to a Python executable');
    process.exit(1);
  }

  fs.mkdirSync(path.dirname(venvRoot), { recursive: true });
  console.error(`installing angl python runtime in ${venvRoot}`);

  let result;
  if (!fs.existsSync(venvPython)) {
    result = run(python, pythonArgs(python, ['-m', 'venv', venvRoot]));
    if (result.status !== 0) process.exit(result.status || 1);
  }

  result = run(venvPython, ['-m', 'pip', 'install', '--upgrade', 'pip']);
  if (result.status !== 0) process.exit(result.status || 1);

  result = run(venvPython, ['-m', 'pip', 'install', '--upgrade', installSource()]);
  if (result.status !== 0) process.exit(result.status || 1);

  fs.writeFileSync(marker, runtimeIdentity);
}

ensureRuntime();

const result = run(anglBin, process.argv.slice(2));
process.exit(result.status === null ? 1 : result.status);
