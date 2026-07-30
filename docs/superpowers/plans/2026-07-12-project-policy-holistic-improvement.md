# Project Policy Holistic Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a comprehensive, portable initialized policy system with mandatory maintained quality gates, core architecture guidance, conservative conditional policies, and independently verified results.

**Architecture:** Keep deterministic policy inventory and applicability signals in `ralph.project_policy.markers` and `evidence`; keep seeding in `preflight`, structural checks in `validators`, and durable prose in bundled starter Markdown. Tests exercise public starter, evidence, validation, and preflight surfaces with `MemoryWorkspace`.

**Tech Stack:** Python 3.14, pytest, mypy, ruff, TOML-backed Ralph Workflow policy, Markdown starter resources.

---

### Task 1: Pin the expanded policy contract

**Files:**
- Modify: `ralph-workflow/tests/project_policy/test_starters.py`
- Modify: `ralph-workflow/tests/project_policy/test_starter_enforcement_prose.py`
- Modify: `ralph-workflow/tests/project_policy/test_evidence.py`
- Modify: `ralph-workflow/tests/project_policy/test_preflight.py`

- [ ] Add a failing inventory assertion for ten core policies, including `architecture-policy.md`.
- [ ] Add failing semantic assertions that testing is mandatory and maintained type-checking, linting, and formatting gates are mandatory when supported.
- [ ] Add failing assertions that tool choice remains product-neutral and first-party migration/compatibility code is not categorically excluded.
- [ ] Add focused conditional-domain tests with positive repository evidence and negative false-positive cases.
- [ ] Run `uv run pytest -q tests/project_policy/test_starters.py tests/project_policy/test_starter_enforcement_prose.py tests/project_policy/test_evidence.py tests/project_policy/test_preflight.py` and confirm failures identify missing inventory, prose, or detection behavior.

### Task 2: Expand deterministic inventory and applicability

**Files:**
- Modify: `ralph-workflow/ralph/project_policy/markers.py`
- Modify: `ralph-workflow/ralph/project_policy/evidence.py`
- Modify: `ralph-workflow/ralph/project_policy/preflight.py`
- Modify: `ralph-workflow/ralph/project_policy/validators.py`
- Modify: `ralph-workflow/ralph/project_policy/starters/__init__.py`

- [ ] Add architecture to `CORE_POLICY_FILES`, starter inventory, and required headings.
- [ ] Add only conditional domains with conservative, explainable signals; every accepted detector must return its exact trigger evidence.
- [ ] Centralize conditional detector dispatch so preflight, validation, and readiness evidence cannot drift as domains grow.
- [ ] Preserve customized files by continuing to seed only absent starters.
- [ ] Run the Task 1 focused tests and make the inventory/detection assertions pass.

### Task 3: Add and revise starter policies

**Files:**
- Create: `ralph-workflow/ralph/project_policy/starters/architecture-policy.md`
- Create conditional starter files accepted by Task 2.
- Modify: every existing file under `ralph-workflow/ralph/project_policy/starters/*.md`

- [ ] Run Level 1 fabrication guard on all existing starter files before editing.
- [ ] Write architecture policy around project-specific dependency direction, component boundaries, state ownership, external I/O, public contracts, error flow, extension evidence, ADR triggers, and architecture verification.
- [ ] Require maintained type checking for every supported language without prescribing a specific checker; allow inapplicability only for technical non-support.
- [ ] Require maintained linting and formatting whenever supported, with ratcheted baselines for legacy debt.
- [ ] Require automated testing for behavior-bearing software and distinguish unit isolation from controlled integration/system I/O.
- [ ] Revise dependency, verification, agent, clean-code, documentation, security, design-system, UX, performance, and memory policies to remove application/library confusion and unjustified absolutes while retaining strong outcomes.
- [ ] Give every new starter the standard template banner, facts, commands, exceptions, maintenance triggers, citations, living-document contract, and Ralph markers.
- [ ] Run Level 2 fabrication guard for starter files with new external references, then rerun Level 1 across all starters.
- [ ] Run starter and validator tests until green.

### Task 4: Align remediation and durable documentation

**Files:**
- Modify: `ralph-workflow/ralph/project_policy/remediation.py`
- Modify: `ralph-workflow/docs/architecture/project-policy-readiness-traceability.md`
- Modify other maintained docs found by exact inventory/count search.

- [ ] State that command allowlisting validates command form, not executable existence or success.
- [ ] Direct remediation to run all declared gates and reject preference-based inapplicability for mandatory supported gates.
- [ ] Update policy counts, taxonomy, implementing symbols, and focused verification evidence without retaining stale counts.
- [ ] Run fabrication guard before and after every public Markdown edit.

### Task 5: Verify the implementation

**Files:**
- Verify all files changed above.

- [ ] Run `uv run pytest -q tests/project_policy` and require zero failures or warnings.
- [ ] Run `uv run mypy ralph/project_policy` and require success.
- [ ] Run `uv run ruff check ralph/project_policy tests/project_policy` and require success.
- [ ] Run the policy loader smoke check from `docs/agents/verification.md`.
- [ ] Run `make verify` and fix every surfaced failure or warning without weakening any gate.

### Task 6: Conduct the independent review and close findings

**Files:**
- Reinspect the resulting project-policy implementation and starter bundle as a fresh system.

- [ ] Review only the resulting policy against engineering coverage, portability, mandatory supported gates, tool neutrality, deterministic enforceability, ownership overlap, conditional false positives/negatives, and maintenance burden.
- [ ] Do not describe edits, before/after differences, or implementation history in the review.
- [ ] Convert every material finding into a test-first correction, rerun focused checks, and repeat the review.
- [ ] Rerun `make verify` after the last correction and report only fresh evidence.
