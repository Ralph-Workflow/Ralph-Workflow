# Upstream Mirrored Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework Ralph Workflow's shipped default skills into a Ralph-managed mirrored bundle with upstream provenance and a Ralph-owned runtime skill view that does not rely on Claude plugin-path auto-discovery.

**Architecture:** Keep the default skill bundle as local mirrored package assets for offline install/runtime, add provenance metadata for the upstream source, and refactor runtime skill injection around a merged process-scoped directory exported through `RALPH_SKILLS_PROCESS_DIR`.

**Tech Stack:** Python 3.12, pytest, importlib.resources, existing Ralph skills/install/state infrastructure, Node.js helper package for `skills-package`.

---

### Task 1: Add the design and plan artifacts

**Files:**
- Create: `docs/superpowers/specs/2026-05-26-upstream-mirrored-skills-design.md`
- Create: `docs/superpowers/plans/2026-05-26-upstream-mirrored-skills.md`

- [ ] **Step 1: Write the approved design contract**
- [ ] **Step 2: Write this implementation plan with file-level change set and verification path**
- [ ] **Step 3: Self-review both docs for scope drift, placeholders, and contradictions**

### Task 2: Add mirrored provenance metadata support

**Files:**
- Modify: `ralph/skills/_content.py`
- Create: `ralph/skills/content/metadata.json` or adjacent provenance file
- Test: `tests/test_skills_content.py`

- [ ] **Step 1: Write a failing test asserting the mirrored bundle exposes upstream provenance metadata**
- [ ] **Step 2: Run the focused test to verify the metadata helper is missing or incomplete**
- [ ] **Step 3: Add metadata-loading helpers and a committed mirrored provenance file**
- [ ] **Step 4: Re-run the focused test to verify metadata is now exposed and valid**

### Task 3: Reframe installer/update semantics around mirrored snapshots

**Files:**
- Modify: `ralph/skills/_installer.py`
- Modify: `ralph/skills/_baseline_catalog.py`
- Modify: `ralph/skills/manager.py`
- Test: `tests/test_skills_installer_baseline.py`
- Test: `tests/test_skills_installer_update.py`
- Test: `tests/test_skills_baseline_catalog.py`

- [ ] **Step 1: Write failing tests for mirrored/provenance-aware wording and update detection expectations**
- [ ] **Step 2: Run the focused tests to verify current first-party/repo-owned assumptions fail**
- [ ] **Step 3: Update installer and capability text to treat local content as a Ralph-managed mirrored snapshot**
- [ ] **Step 4: Keep update checks offline by comparing installed files/metadata against the current shipped mirrored snapshot**
- [ ] **Step 5: Re-run the focused tests to verify mirrored semantics pass**

### Task 4: Make the process view the authoritative runtime skill surface

**Files:**
- Modify: `ralph/skills/_process_view.py`
- Modify: `ralph/cli/commands/run.py`
- Test: `tests/test_skills_process_view.py`
- Test: `tests/test_cli_commands_run_process_view_fallback.py`

- [ ] **Step 1: Write failing tests for merged process-scoped skill directory behavior and authoritative runtime export through `RALPH_SKILLS_PROCESS_DIR`**
- [ ] **Step 2: Run the focused tests to confirm current behavior only materializes Ralph defaults and does not model explicit merge semantics**
- [ ] **Step 3: Refactor `SkillsProcessView` around a merge helper that can build the final run-scoped directory deterministically**
- [ ] **Step 4: Keep `run.py` using the process-scoped directory as the runtime truth when machine-global defaults are absent or insufficient**
- [ ] **Step 5: Re-run the focused tests to verify the runtime composition contract**

### Task 5: Align package helper semantics with mirrored ownership

**Files:**
- Modify: `skills-package/package.json`
- Modify: `skills-package/bin/skills.js`
- Test: `tests/test_skills_package_skill_names_parity.py`
- Test: `tests/test_skills_package_version_parity.py`

- [ ] **Step 1: Write failing tests or assertions covering provenance-aware wording and any changed package metadata contract**
- [ ] **Step 2: Run the package-focused tests to verify the old repo-owned language still exists**
- [ ] **Step 3: Update `skills-package` description/install semantics to describe mirrored upstream content managed by Ralph**
- [ ] **Step 4: Re-run the package-focused tests to verify parity still holds where intended**

### Task 6: Rewrite docs and prompt wording to remove first-party authorship claims

**Files:**
- Modify: `CONTRIBUTING.md`
- Modify: `docs/first-task-guide.md`
- Modify: `docs/sphinx/quickstart.md`
- Modify: `docs/sphinx/cli.md`
- Modify: `docs/sphinx/prompts.md`
- Modify: `docs/sphinx/versioning.md`
- Modify: relevant templates under `ralph/prompts/templates/`

- [ ] **Step 1: Write a failing content assertion or grep-based test strategy for stale first-party/repo-owned wording if a suitable test seam exists; otherwise record exact files and phrases to replace**
- [ ] **Step 2: Replace authorship claims with the approved Ralph-managed mirrored upstream wording**
- [ ] **Step 3: Update contributor guidance so skill changes describe sync/provenance maintenance instead of in-repo original authorship**
- [ ] **Step 4: Verify no stale “first-party”, “repo-owned”, or “ships inside the Python package assets” claims remain where they are now misleading**

### Task 7: Verify the full skill bundle slice

**Files:**
- Test: `tests/test_skills_content.py`
- Test: `tests/test_skills_installer_baseline.py`
- Test: `tests/test_skills_installer_update.py`
- Test: `tests/test_skills_process_view.py`
- Test: `tests/test_cli_commands_run_process_view_fallback.py`
- Test: `tests/test_skills_baseline_catalog.py`
- Test: `tests/test_skills_package_skill_names_parity.py`
- Test: `tests/test_skills_package_version_parity.py`

- [ ] **Step 1: Run the focused regression suite for the touched skills/install/process-view/package tests**
- [ ] **Step 2: Run `make verify`**
- [ ] **Step 3: Confirm the final repo state has no placeholder docs, no stale authorship claims in touched areas, and no broken offline install/runtime behavior**
