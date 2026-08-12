# Active prompt-template revision plan

## Outcome

Every production-reachable Jinja prompt gives an agent only the context, actions, and completion contract required for its phase. Unreachable top-level templates are removed after reachability is proved. Runtime behavior and artifact contracts remain unchanged.

## Global constraints

- Work under `ralph-workflow/` for implementation and tests.
- Follow test-driven development: add a failing behavioral check before each production change.
- Preserve template variables, artifact schemas, tool references, phase routing, fallback behavior, safety boundaries, and workspace overrides.
- Keep only production-reachable top-level templates. A production path is a default-policy selection or a direct loader/caller under `ralph-workflow/ralph/`; test-only references do not establish reachability.
- Structure prompts as objective, authoritative inputs, ordered actions, and output/completion contract when those elements apply.
- Remove filler, repeated motivation, duplicated rules, and obsolete references. Keep a rule once at the narrowest shared location that serves every consumer.
- Do not commit.

### [S-1] Characterize production reachability and prompt contracts

Add black-box tests that derive or assert the production template inventory from default policy and direct runtime loaders. Prove `review.jinja`, `review_analysis.jinja`, and `fix_mode.jinja` have no production selector before deleting them. Add focused prompt assertions for critical artifact, safety, verification, fallback, and completion behavior. Run the focused tests and confirm the new reachability/deletion expectation fails for the current tree.

Evidence: focused tests fail for the intended current-state reason, not a setup error.

### [S-2] Remove unreachable prompt templates and stale references

Delete only top-level templates proved unreachable by S-1. Remove test fixtures, audit inventories, or documentation that incorrectly treats them as shipped production prompts, while retaining generic workspace-override support. Do not remove artifact formats or historical compatibility surfaces unless production analysis independently proves those are dead and the task requires it.

Evidence: focused reachability and template-integrity tests pass; repository search finds no production selector for a deleted template.

### [S-3] Tighten planning and development prompts

Revise `planning.jinja`, `planning_edit.jinja`, their fallbacks, `developer_iteration.jinja`, its continuation and fallback, and `worker_developer.jinja`. Consolidate shared planning/development partials where the same instruction appears across consumers. Preserve read/write boundaries, parallel-worker isolation, plan/result proof, retry handling, and artifact completion.

Evidence: render and contract tests pass for initial, loopback, continuation, fallback, and worker variants.

### [S-4] Tighten verification and policy prompts

Revise `planning_analysis.jinja`, `development_analysis.jinja`, `policy_remediation.jinja`, and `policy_remediation_analysis.jinja`. Keep criterion-by-criterion evidence, expected observations fixed before probes, verdict vocabulary, localization, gate semantics, and artifact submission explicit. Remove narrative repetition and ensure criteria/context are clearly data rather than instructions.

Evidence: focused render tests and verification-prompt evaluation pass.

### [S-5] Tighten commit, cleanup, and conflict prompts

Revise `commit_message.jinja`, `commit_simplified.jinja`, `commit_cleanup.jinja`, and `conflict_resolution.jinja`. Preserve exact cleanup security boundaries, commit artifact schemas, skip behavior, conflict scope, validation, and completion signals. Compress enumerations only where a shared or validator-backed source can remain authoritative without weakening the prompt.

Evidence: focused commit, cleanup, conflict, and template-integrity tests pass.

### [S-6] Independently review until the rubric passes

Dispatch parallel read-only agents over disjoint groups: planning/development; verification/policy; commit/cleanup/conflict; and production reachability/contracts. Each agent evaluates rendered prompts against the same criteria: clear objective, authoritative context, ordered necessary actions, explicit output/completion contract, no filler or redundancy, and no lost runtime/safety requirement. Fix every substantive finding with focused red-green coverage, then re-run review for the affected group. Repeat until every reviewer reports no substantive criterion failure.

Evidence: final independent review reports contain no unresolved substantive findings.

### [S-7] Verify the complete repository

Run prompt render integrity, prompt single-sourcing, artifact-format, and verification-prompt evaluation checks required by the touched area. Run `make verify` from `ralph-workflow/`. Fix every error or warning and repeat the failed review/check until all output is clean.

Evidence: `make verify` exits zero within the combined test budget with no ERROR or WARNING diagnostics; `git diff --check` passes.
