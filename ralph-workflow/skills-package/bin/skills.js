#!/usr/bin/env node

'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const CONTENT_DIR_PKG = path.join(__dirname, '..', 'content');
const CONTENT_DIR_REPO = path.join(__dirname, '..', '..', 'ralph', 'skills', 'content');
const CONTENT_DIR = fs.existsSync(CONTENT_DIR_PKG) ? CONTENT_DIR_PKG : CONTENT_DIR_REPO;
const DEFAULT_INSTALL_DIR = path.join(os.homedir(), '.claude', 'plugins', 'ralph-workflow-skills', 'skills');

function readMetadata() {
  return JSON.parse(fs.readFileSync(path.join(CONTENT_DIR, 'metadata.json'), 'utf8'));
}

function listSkillNames() {
  return readMetadata().skills.slice();
}

function readSkill(name) {
  const filePath = path.join(CONTENT_DIR, `${name}.md`);
  if (!fs.existsSync(filePath)) {
    throw new Error(`Unknown skill: ${name}`);
  }
  return fs.readFileSync(filePath, 'utf8');
}

function installSkills(targetDir) {
  fs.mkdirSync(targetDir, { recursive: true });
  for (const name of listSkillNames()) {
    const sourcePath = path.join(CONTENT_DIR, `${name}.md`);
    const destPath = path.join(targetDir, `${name}.md`);
    fs.copyFileSync(sourcePath, destPath);
  }
}

function main(argv) {
  const [command, ...rest] = argv;
  if (!command || command === '--help' || command === '-h') {
    process.stdout.write('Usage: skills <list|read|install> [name] [--target DIR]\n');
    return 0;
  }

  if (command === 'list') {
    process.stdout.write(`${listSkillNames().join('\n')}\n`);
    return 0;
  }

  if (command === 'read') {
    const name = rest[0];
    if (!name) {
      throw new Error('skills read requires a skill name');
    }
    process.stdout.write(readSkill(name));
    return 0;
  }

  if (command === 'install') {
    let targetDir = DEFAULT_INSTALL_DIR;
    for (let i = 0; i < rest.length; i += 1) {
      if (rest[i] === '--target') {
        targetDir = rest[i + 1];
        i += 1;
      }
    }
    if (!targetDir) {
      throw new Error('skills install requires a target directory');
    }
    installSkills(targetDir);
    process.stdout.write(`Installed ${listSkillNames().length} skills to ${targetDir}\n`);
    return 0;
  }

  throw new Error(`Unknown command: ${command}`);
}

try {
  process.exitCode = main(process.argv.slice(2));
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
}
