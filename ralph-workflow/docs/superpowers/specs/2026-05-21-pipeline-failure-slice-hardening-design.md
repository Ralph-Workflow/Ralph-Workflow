# Pipeline Failure Slice Hardening Design

## Goal

Harden the Ralph pipeline slice around commit execution, artifact/proof validation, reducer failure attribution, and runner/recovery relay paths so this failure family is black-box testable and materially harder to reintroduce.

## Scope

In scope:
- commit message payload interpretation during commit execution
- commit scope selection (`files`, `excluded_files`, changed-set filtering)
- reducer handling of commit failures and phase failures
- recovery-controller relay of typed failure categories for this slice
- proof-validation relay tests proving artifact-validation failures do not get confused with later commit failures

Out of scope:
- repo-wide framework rewrites
- weakening proof validation strictness
- unrelated pipeline roles or generic policy redesign

## Problem Statement

The observed failure came from two independent defects that interacted badly:

1. Commit execution contained branch-coupled logic where the `excluded_files` path used state that only existed in the `files` path.
2. Commit failure reporting reused stale `last_error`, which made a real commit failure appear to be a proof-validation failure from an earlier phase.

The deeper issue is architectural: key decisions in this slice were not explicit pure decisions with black-box contracts, and relay layers were allowed to reinterpret older state instead of constructing fresh failure provenance.

## Architecture

### 1. Commit scope resolution becomes a pure decision seam

Commit scope selection should be expressed as a pure helper that accepts:
- normalized commit payload
- changed file list

and returns one explicit staging decision:
- stage all
- stage specific included paths
- stage changed paths minus exclusions
- reject invalid request

This helper must not depend on branch-local variables or repository I/O. Repository I/O remains in a thin wrapper that only fetches the changed set.

### 2. Failure attribution becomes phase-local and explicit

Commit failure reasons must be created from the active phase and the active failure event, not inherited from ambient `PipelineState.last_error`.

Artifact/proof failures should continue to use typed failure categories, but relay code must format those failures according to the actual category rather than assuming every categorized phase failure is artifact validation.

### 3. Relay behavior becomes black-box provable

The runner/recovery path does not need a major rewrite, but this slice needs targeted relay tests proving:
- artifact-validation failures preserve category and provenance through recovery handling
- commit failures overwrite stale error text with commit-local failure text
- ambiguous/category-specific failures are not mislabeled as artifact validation faults

## Design Details

### Commit executor hardening

- Extract a pure helper from `ralph.pipeline.commit_executor` that resolves commit include paths from payload + changed paths.
- Keep path normalization and changed-set membership validation strict.
- Cover `files`, `excluded_files`, neither, invalid types, invalid paths, and changed-set mismatches with table-driven tests.

### Reducer hardening

- Introduce a small helper for commit failure reason construction.
- Introduce a category-aware helper for classified phase-failure relay text when recovery is active.
- Ensure reducer behavior never reuses stale `last_error` for `COMMIT_FAILURE`.

### Contract and relay tests

- Extend commit-message tests to cover excluded-file contract behavior.
- Extend commit-executor tests to cover the pure decision seam and payload matrix.
- Extend reducer/recovery tests to prove category relay is phase-correct and not hardcoded to artifact validation.
- Extend proof-validation tests to keep the black-box artifact-validation path covered through recovery.

## Testability Requirements

### Pure black-box unit tests

- commit scope decision helper: payload + changed paths → staged path result / validation failure
- commit failure reason helper: state/event → exact failure reason
- classified phase failure relay helper: category + phase + raw reason → correctly prefixed failure text

### Thin integration tests

- `execute_commit_effect` stages the expected files for valid payloads
- `execute_commit_effect` fails cleanly for invalid payloads
- reducer routes `COMMIT_FAILURE` to terminal failure with commit-local error text
- proof-validation failures preserve category through recovery controller
- ambiguous or other categorized relay paths do not get mislabeled as artifact validation faults

## Acceptance Criteria

- No branch in commit scope selection can reference data initialized only in a different branch.
- Commit failure reporting never reuses stale prior-phase `last_error`.
- Categorized phase failures routed through recovery are labeled by their actual category, not a hardcoded artifact-validation prefix.
- The full failure family is covered by black-box tests at the pure helper seam and the relay seam.
- `make verify` passes.

## Risks and Mitigations

- **Risk:** Over-hardening by introducing a broad framework.
  - **Mitigation:** Keep new helpers local to this slice and only extract pure seams that are directly needed for testability.

- **Risk:** Behavior drift in commit payload semantics.
  - **Mitigation:** Add contract tests for the existing supported payload forms before or alongside refactoring.

- **Risk:** Relay tests become too implementation-coupled.
  - **Mitigation:** Assert observable outputs only: event category, phase, and final `last_error` text.

## Review Notes

- This spec belongs in `docs/superpowers/specs/` because it defines an implementation-scoped hardening contract for a contained pipeline slice.
- It intentionally leaves broader repo-wide safety frameworks alone.
- It makes the route clearer by separating pure-decision hardening from relay-path hardening rather than treating the original bug as a single-file patch.
