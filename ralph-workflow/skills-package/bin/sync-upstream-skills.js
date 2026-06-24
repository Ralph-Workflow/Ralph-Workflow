#!/usr/bin/env node

'use strict';

const fs = require('node:fs');
const path = require('node:path');

const SCRIPT_DIR = __dirname;
const PACKAGE_DIR = path.join(SCRIPT_DIR, '..');
const DEFAULT_MANIFEST = path.join(PACKAGE_DIR, 'upstream-skills.json');
const DEFAULT_OUTPUT_DIRS = [
  path.join(PACKAGE_DIR, 'content'),
  path.join(PACKAGE_DIR, '..', 'ralph', 'skills', 'content'),
];
const DEFAULT_RAW_BASE = 'https://raw.githubusercontent.com';

function readManifest() {
  const manifestPath = process.env.RALPH_SKILLS_MANIFEST || DEFAULT_MANIFEST;
  return JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
}

function resolveOutputDirs() {
  const raw = process.env.RALPH_SKILLS_OUTPUT_DIRS;
  if (!raw) {
    return DEFAULT_OUTPUT_DIRS;
  }
  return raw
    .split(path.delimiter)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

async function readSkillContent(skill) {
  if (skill.source.type === 'package') {
    const sourcePath = skill.source.path;
    if (!sourcePath) {
      throw new Error(`Package source for ${skill.name} must define path`);
    }
    return fs.readFileSync(path.join(PACKAGE_DIR, '..', sourcePath), 'utf8');
  }

  if (skill.source.type !== 'upstream') {
    throw new Error(`Unsupported skill source type: ${skill.source.type}`);
  }

  const baseUrl = process.env.RALPH_SKILLS_UPSTREAM_RAW_BASE || DEFAULT_RAW_BASE;
  const repo = skill.source.repo;
  const ref = skill.source.ref;
  const sourcePath = skill.source.path;
  if (!repo || !ref || !sourcePath) {
    throw new Error(`Upstream source for ${skill.name} must define repo, ref, and path`);
  }

  const repoPath = repo.replace('https://github.com/', '');
  const url = `${baseUrl}/${repoPath}/${ref}/${sourcePath}`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch ${skill.name} from ${url}: ${response.status}`);
  }
  return await response.text();
}

function writeOutputs(outputDirs, skillName, content) {
  for (const outputDir of outputDirs) {
    fs.mkdirSync(outputDir, { recursive: true });
    fs.writeFileSync(path.join(outputDir, `${skillName}.md`), content, 'utf8');
  }
}

function writeMetadata(outputDirs, manifest) {
  const skillSources = Object.fromEntries(
    manifest.skills.map((skill) => [
      skill.name,
      {
        bundle: skill.bundle || 'core-workflow',
        upstream_name: skill.upstream_name || skill.name,
        catalog_repo: skill.catalog?.repo || null,
        catalog_ref: skill.catalog?.ref || null,
        catalog_path: skill.catalog?.path || null,
        repo: skill.source.repo || null,
        ref: skill.source.ref || null,
        path: skill.source.path,
      },
    ]),
  );
  const skillBundles = Object.fromEntries(
    manifest.skills.map((skill) => [skill.name, skill.bundle || 'core-workflow']),
  );
  const metadata = {
    source_repo: manifest.source_repo,
    source_ref: manifest.source_ref,
    source_version: manifest.source_version,
    source_commit: process.env.RALPH_SKILLS_SOURCE_COMMIT || manifest.source_ref,
    source_repos: manifest.source_repos || [],
    mirrored_at: process.env.RALPH_SKILLS_MIRRORED_AT || new Date().toISOString(),
    skills: manifest.skills.map((skill) => skill.name),
    bundles: skillBundles,
    skill_sources: skillSources,
  };

  for (const outputDir of outputDirs) {
    fs.mkdirSync(outputDir, { recursive: true });
    fs.writeFileSync(
      path.join(outputDir, 'metadata.json'),
      `${JSON.stringify(metadata, null, 2)}\n`,
      'utf8',
    );
  }
}

async function main() {
  const manifest = readManifest();
  const outputDirs = resolveOutputDirs();

  for (const skill of manifest.skills) {
    const content = await readSkillContent(skill);
    writeOutputs(outputDirs, skill.name, content);
  }

  writeMetadata(outputDirs, manifest);
}

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
});
