# Pipeline Failure Slice Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden commit execution, artifact/proof validation relay, and reducer failure attribution so this failure family is black-box testable and materially harder to reintroduce.

**Architecture:** Extract pure decision helpers inside the existing pipeline slice, keep repository I/O in thin wrappers, and add table-driven plus relay-path tests that prove failure provenance stays correct from phase validation through commit failure handling.

**Tech Stack:** Python 3.12, pytest, mypy, ruff, existing Ralph pipeline/recovery infrastructure.

---

### Task 1: Add the hardening spec and plan artifacts

**Files:**
- Create: `docs/superpowers/specs/2026-05-21-pipeline-failure-slice-hardening-design.md`
- Create: `docs/superpowers/plans/2026-05-21-pipeline-failure-slice-hardening.md`

- [ ] **Step 1: Write the spec and plan docs**
- [ ] **Step 2: Self-review for scope drift, ambiguity, and placeholder text**

### Task 2: Extract pure commit scope resolution

**Files:**
- Modify: `ralph/pipeline/commit_executor.py`
- Test: `tests/test_pipeline_runner_execute_commit_effect.py`

- [ ] **Step 1: Add a pure helper that resolves commit include paths from payload + changed paths**
- [ ] **Step 2: Keep `_commit_include_paths()` as a thin wrapper around repository changed-set lookup**
- [ ] **Step 3: Add/extend tests covering `files`, `excluded_files`, neither, invalid paths, and changed-set mismatches**

### Task 3: Harden failure attribution in the reducer

**Files:**
- Modify: `ralph/pipeline/reducer.py`
- Test: `tests/test_reducer_phase_failure_event.py`

- [ ] **Step 1: Extract a pure helper for commit failure reason construction**
- [ ] **Step 2: Replace hardcoded categorized phase-failure prefixing with category-aware formatting**
- [ ] **Step 3: Add tests proving commit failures overwrite stale `last_error` and categorized relay text matches actual category**

### Task 4: Cover recovery relay and proof-validation provenance

**Files:**
- Modify/Test: `tests/test_development_result_proof.py`
- Modify/Test: `tests/recovery/test_ambiguous_failure.py` or adjacent recovery test file as needed

- [ ] **Step 1: Add a black-box test proving artifact-validation failures preserve category through recovery handling**
- [ ] **Step 2: Add a black-box relay test proving non-artifact categorized failures are not mislabeled as artifact validation faults**

### Task 5: Harden commit-message contract tests

**Files:**
- Modify: `tests/test_commit_message.py`

- [ ] **Step 1: Add contract coverage for excluded-file payload normalization**
- [ ] **Step 2: Add negative coverage for invalid excluded-file reasons and shapes**

### Task 6: Verify the full slice

**Files:**
- Test: `tests/test_pipeline_runner_execute_commit_effect.py`
- Test: `tests/test_reducer_phase_failure_event.py`
- Test: `tests/test_development_result_proof.py`
- Test: `tests/test_commit_message.py`
- Test: relevant `tests/recovery/*.py`

- [ ] **Step 1: Run focused regression tests for the touched slice**
- [ ] **Step 2: Run `make verify`**
- [ ] **Step 3: Confirm no placeholders, no dead code, and no weakened checks remain**
