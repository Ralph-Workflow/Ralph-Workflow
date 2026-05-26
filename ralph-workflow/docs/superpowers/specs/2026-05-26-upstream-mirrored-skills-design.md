# Upstream Mirrored Skills Design

## Goal

Change Ralph Workflow's default skill bundle from a repo-authored/first-party assumption to a Ralph-managed mirrored bundle sourced from an upstream repository, while preserving an offline, stable, end-user experience.

## Scope

In scope:
- default baseline skills currently represented under `ralph/skills/content/`
- bundle provenance and metadata for the shipped default skills
- runtime skill composition so Ralph does not depend on undocumented Claude plugin path auto-discovery
- maintainer sync flow for refreshing the mirrored bundle from upstream
- install/update/status logic for the default skill bundle
- documentation and tests that currently claim the bundle is first-party or repo-owned authored content

Out of scope:
- replacing user-installed personal/project/plugin skills
- changing the supported default skill subset beyond provenance-driven restructuring
- adding network fetch requirements to end-user install or runtime flows
- broad prompt or pipeline redesign unrelated to skill sourcing/composition

## Problem Statement

The current implementation makes three incorrect assumptions at once:

1. Ralph describes the default skills as first-party or repo-owned authored assets.
2. The package treats local markdown files as the authoring source of truth instead of a mirrored snapshot.
3. The install path `~/.claude/plugins/ralph-workflow-skills/skills/` is treated as if Claude skill discovery there were a sufficient runtime contract.

External evidence points to `obra/superpowers` as the upstream source for most of the shipped workflow skills, and the repo's own `run.py` fallback logic already shows that Ralph cannot safely rely on the machine-global plugin path alone. Ralph needs a clearer contract: Ralph owns the runtime skill view and release packaging, while upstream owns the authored skill content.

## Product Contract

### 1. Ralph-managed defaults with upstream provenance

End users should continue to experience the bundle as Ralph's default baseline skills. Upstream should remain invisible to them during normal install and runtime flows.

Internally, the bundle must be described as:
- **Ralph-managed** for distribution, compatibility gating, runtime composition, and release packaging
- **upstream-sourced** for authorship/provenance

The local bundle in this repo is therefore a **mirrored snapshot**, not hand-authored truth.

### 2. Offline default behavior remains mandatory

Ralph must continue to work without network access during `ralph --init`, runtime prompt injection, and process-scoped fallback materialization. Upstream access is a maintainer concern, not an end-user runtime dependency.

## Architecture

### 1. Upstream mirror source

Introduce a dedicated upstream metadata layer that defines:
- upstream repository URL
- upstream ref/commit used for the mirrored snapshot
- supported mirrored skill names
- optional upstream version/tag when available

This metadata becomes the authoritative provenance record for the shipped bundle.

### 2. Mirror artifact layer

Keep `ralph/skills/content/` as the runtime/package asset directory, but reclassify it as generated or mirrored content. Add a machine-readable metadata file adjacent to the skill content so the bundle can be traced back to its upstream revision.

The mirrored artifact layer should be deterministic: given the same upstream ref and supported-skill subset, the same local files and metadata should be produced.

### 3. Ralph-owned runtime skill view

Ralph should not rely on `~/.claude/plugins/ralph-workflow-skills/skills/` as the authoritative discovery contract.

Instead, Ralph should own a merged process-scoped runtime skill view:
- Ralph's mirrored default bundle is always materialized into the runtime view.
- Existing user/global/project skills can be copied into that runtime view when composing the final per-run skill directory.
- The run-scoped directory exported through `RALPH_SKILLS_PROCESS_DIR` becomes the authoritative runtime surface.

This isolates correctness from Claude plugin-path behavior and lets Ralph compose its defaults with other skills explicitly.

### 4. Machine-global install as cache, not truth

The existing machine-global install directory can remain as a convenience cache/distribution location, but correctness must not depend on Claude reading directly from that directory. Its role becomes:
- a durable installed copy of Ralph's mirrored defaults
- an input source Ralph may merge into the run-scoped skill view
- a status/update target for `ralph --init`

### 5. Compatibility gate

Maintainer sync must fail loudly when:
- a required upstream skill disappears
- upstream file layout no longer matches Ralph's importer expectations
- normalized mirrored output cannot be produced safely

Ralph should never silently ship a broken upstream change.

## Design Details

### Provenance model

Add a metadata file under `ralph/skills/` describing:
- `source_repo`
- `source_ref`
- `source_commit`
- `source_version` (if available)
- `mirrored_at`
- `skills`

This metadata should be used by installers, update checks, docs, and tests.

### Runtime composition model

Refactor process-view creation so `SkillsProcessView` can build a merged directory from multiple sources in precedence order:

1. Ralph mirrored defaults
2. machine-global installed Ralph defaults (if needed as a source for unchanged mirrored content or durable cache)
3. other known skill directories Ralph chooses to preserve

The exact precedence should ensure Ralph's default bundle is present without overwriting unrelated user skills.

### Update model

End-user update checks should compare installed metadata against the mirrored metadata shipped in the current Ralph release. That preserves offline behavior and stable semantics:
- if Ralph ships a newer mirrored snapshot, update is available
- if installed files are partial/missing/mismatched, repair is needed

Upstream freshness checks belong to a maintainer sync workflow, not end-user runtime/update logic.

### Packaging model

`skills-package/` should continue to publish/install the same Ralph-managed default bundle, but its wording and provenance need to reflect that the content is mirrored from upstream rather than authored here. The package should copy the mirrored snapshot and metadata, not claim repo-owned authorship.

## File-Level Change Set

### Core skill source/provenance

- `ralph/skills/_content.py`
  - keep baseline skill list and content reads
  - add metadata access helpers for mirrored provenance
  - rename/reframe module docs to remove authored-source implication

- new metadata module/file under `ralph/skills/`
  - store and load mirrored upstream provenance

- `ralph/skills/content/`
  - keep mirrored skill markdown runtime assets

### Installer and runtime composition

- `ralph/skills/_installer.py`
  - stop treating local markdown as authored source; treat them as mirrored snapshot assets
  - use metadata-aware checks for update/repair detection

- `ralph/skills/_process_view.py`
  - extend from simple materialization to merged runtime skill view creation
  - keep `RALPH_SKILLS_PROCESS_DIR` as the authoritative per-run export

- `ralph/cli/commands/run.py`
  - continue using process-view fallback, but align docs/comments with the new runtime contract

### Capability/state text

- `ralph/skills/_baseline_catalog.py`
  - replace “first-party skill bundle” wording with provenance-accurate Ralph-managed mirrored wording

- `ralph/skills/manager.py`
  - keep health/update flow, but ensure descriptions and update semantics match mirrored snapshots instead of authored local assets

### Packaging

- `skills-package/package.json`
  - update description and packaging semantics

- `skills-package/bin/skills.js`
  - keep install/read/list behavior against mirrored content
  - stop implying the default install dir alone is the runtime correctness contract

### Documentation and prompts

- `docs/first-task-guide.md`
- `docs/sphinx/quickstart.md`
- `docs/sphinx/cli.md`
- `docs/sphinx/prompts.md`
- `docs/sphinx/versioning.md`
- `CONTRIBUTING.md`
- prompt templates under `ralph/prompts/templates/`

All of these need wording changes from “first-party/repo-owned/authored here” to “Ralph-managed mirrored default bundle sourced from upstream.”

### Tests

- update existing tests around installer/content/package parity/process view
- add tests for mirrored metadata loading
- add tests for merged process-scoped runtime view behavior
- replace authoring-assumption assertions with provenance-aware assertions

## Testability Requirements

### Pure unit tests

- mirrored metadata load/validation helper
- installer update check against mirrored metadata
- runtime skill merge helper precedence rules

### Thin integration tests

- `ralph --init` still installs a usable offline mirrored bundle
- process-view fallback produces a complete run-scoped directory when machine-global defaults are missing
- merged process view includes Ralph defaults and preserves unrelated skills when configured to do so
- prompt skill injection still receives the complete Ralph default bundle through `RALPH_SKILLS_PROCESS_DIR`

## Acceptance Criteria

- The repo no longer describes the shipped default skill bundle as first-party authored or repo-owned authored content.
- Ralph ships a mirrored upstream snapshot with machine-readable provenance metadata.
- `ralph --init` and runtime skill injection continue working offline.
- Runtime correctness no longer depends on Claude auto-discovering `~/.claude/plugins/ralph-workflow-skills/skills/`.
- The process-scoped runtime skill view becomes the authoritative run surface.
- Existing default skills still appear in planning/developer prompt flows.
- `make verify` passes.

## Risks and Mitigations

- **Risk:** Scope creep into a full external plugin-management system.
  - **Mitigation:** Keep this change focused on Ralph's default mirrored bundle and runtime composition only.

- **Risk:** Regressing existing prompt skill injection.
  - **Mitigation:** Preserve `RALPH_SKILLS_PROCESS_DIR` contract and add regression tests around prompt materialization.

- **Risk:** Accidentally overwriting user skills when composing runtime views.
  - **Mitigation:** Implement explicit merge precedence and test collision behavior.

- **Risk:** Upstream structure drift breaks maintainer sync unexpectedly.
  - **Mitigation:** Add a strict sync gate that fails loudly on upstream layout mismatches.

## Review Notes

- The design intentionally separates authorship provenance from runtime ownership.
- It keeps Ralph's end-user experience stable while making source and compatibility responsibility honest.
- It treats the machine-global plugin path as a cache/distribution detail rather than a trusted runtime API.
