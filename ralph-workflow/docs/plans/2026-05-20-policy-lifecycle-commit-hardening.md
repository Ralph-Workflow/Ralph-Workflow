# Composable Policy Blocks and Lifecycle Accounting Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Redesign Ralph’s policy system so workflow semantics are expressed as composable policy blocks, with lifecycle completion owning budget progression and commit logic modeled as attachable hooks rather than as the thing that defines developer-cycle accounting.

**Architecture:** This is a deliberate breaking change. The current phase graph model is too low-level because budget/cycle semantics are indirectly coupled to whether commit phases exist and where they sit in the graph. The new model should let policy define a logical block such as an inner development-analysis lifecycle with its own loop counter, optional pre/post commit hooks, and an explicit completion event that advances the outer developer-cycle counter. Old `pipeline.toml` semantics are not preserved; legacy policy files should fail loudly and be replaced, not silently adapted.

**Tech Stack:** Python 3.12, Pydantic policy models, reducer/progress pipeline, policy validation, GitPython, pytest, Ruff, mypy, Sphinx docs.

---

## Planning Requirements Locked

This plan is not considered implementation-ready unless both of these are true:
- the composable-block runtime/schema direction is sound, and
- the redesign covers the full product surface: config sources, CLI behavior, checkpoints/resume, display/observability, docs, tests, artifacts, and drains.

These are hard requirements for implementation:

1. **100% policy-driven behavior**
   - Reverting to the old workflow must require only policy/config changes, not Python edits.
   - Runtime may gain a more expressive schema/interpreter, but no workflow behavior may be hardcoded.

2. **Composable blocks, not only flat phase adjacency**
   - Policy must express logical groupings such as an inner dev-analysis block.
   - A block can own loop counters, completion semantics, and optional hook phases.
   - The model must be structured enough to drive a future drag-and-drop workflow UI, meaning blocks, hooks, counters, and routes must be explicit serializable objects rather than hidden conventions.

3. **Lifecycle-owned budget progression**
   - The outer developer iteration counter must advance because a lifecycle/block completed, not because a commit hook happened to run.
   - Commit hooks must not be the authoritative source of developer-cycle accounting.

4. **Commit modeled as hookable logic**
   - Pre-analysis commit and final commit should be attachable behavior around lifecycle blocks.
   - Commit can participate in a grouping, but should not define the grouping’s identity.

5. **Hard break for old `pipeline.toml`**
   - Do not make this backward compatible.
   - Old-format `pipeline.toml` must fail loudly.
   - This includes more than the local file: bundled defaults, user-global config, project-local config, and generated copies must all follow the new schema boundary.
   - If product direction remains the same at execution time, we should strongly consider deleting/regenerating obsolete local pipeline config instead of trying to coerce it forward.

6. **Commit process hardening remains required**
   - Garbage/build artifacts must be removable safely.
   - Binary/untracked handling must be explicit.
   - Behavior must be broadly applicable across web, desktop, mobile, and mixed repositories.

7. **Black-box testable architecture is a hard requirement**
   - The redesign must be testable primarily through observable behavior, not private implementation details.
   - Refactor as much as needed to achieve that, including dependency injection, seam extraction, boundary reshaping, and decomposition of tightly coupled runtime code.
   - If any part of the composable-block model cannot be tested cleanly from the outside, that is a design failure and the architecture must be changed before sign-off.

---

## Target Model

### Required execution-model decision

This plan now explicitly chooses the execution model:

- **Blocks are authoring-time policy objects that compile into runtime phase leaves.**
- The runtime does **not** become an ad hoc tree-walker over arbitrary nested objects in the first implementation.
- Instead, the new `pipeline.toml` block syntax compiles deterministically into a flat internal phase graph plus explicit lifecycle metadata.
- That flat compiled form remains what reducer/orchestrator/handlers execute, checkpoint, and display.
- The authoring model is block-oriented and UI-grade; the runtime model is compiled-phase execution with no hidden semantic dependence on commit placement.

This decision is mandatory because too many current product surfaces (`loader`, `state`, `checkpoint`, `display`, `explain-policy`, handler registration) are phase-based today. A compiled representation gives us the composable authoring model without requiring a one-shot runtime rewrite.

### UI-grade block-oriented policy semantics

#### Block taxonomy

The policy model must explicitly distinguish between two block kinds:

- **Group block**
  - A composite/container block.
  - Owns nested child blocks.
  - May own loop counters, outer budget counters, lifecycle completion rules, and hook attachment points.
  - Example: an outer developer-iteration block, or an inner planning-analysis feedback block.

- **Individual block**
  - A leaf/action block.
  - Represents one executable unit or one compiled hook leaf.
  - Compiles directly to one or more runtime phase leaves with explicit drain/artifact ownership.
  - Example: planning, planning_analysis, development, a compiled pre-commit hook leaf, or a compiled final commit hook leaf.

The new policy system should support a structure conceptually like, and this structure should be representable in a future drag-and-drop editor without Python-specific knowledge:

- **outer developer-iteration group block**
  - owns outer counter/cap, for example `iteration = 5`
  - repeats its nested workflow block until the cap is exhausted
- **nested workflow group block inside each developer iteration**
  - planning block
  - planning-analysis feedback block
  - development execution block
  - inner development-analysis feedback block
    - owns loop counter: `development_analysis_iteration`
    - owns decision routes: completed / request_changes / failed
  - optional pre-completion commit hook block
  - optional post-completion/finalization hook block
- lifecycle completion of the nested workflow increments the outer developer-iteration counter

### Key semantic split

- **Loop counters** belong to inner analysis blocks.
- **Budget counters** belong to lifecycle/block completion.
- **Commit hooks** may route, validate, cleanup, stage, skip, or finalize, but they do not implicitly own lifecycle accounting.

### Canonical lifecycle-completion event

The compiled runtime model must introduce one explicit lifecycle-completion seam used consistently across the product surface.

That seam must define:
- where outer budget counters increment
- where lifecycle-owned loop resets happen
- where post-lifecycle hooks attach
- what checkpoint/run-history state records as the completion boundary
- what explain/render/transcript surfaces call a completed iteration

This event replaces today's implicit "a commit phase consumed the developer cycle" logic.

### Policy implications

The policy schema likely needs first-class constructs for one or more of:
- block/lifecycle definitions
- hook definitions (`before_complete`, `after_complete`, `before_analysis`, etc.)
- explicit budget increment triggers on lifecycle completion
- explicit route semantics for hooks that do not consume budget

This is preferable to continuing to encode lifecycle meaning indirectly in a flat list of phases.

### pipeline.toml consequences

This should be treated as a full format redesign for `pipeline.toml`, not an additive tweak.

Conceptually, users should be able to say:
- "repeat this developer-iteration block 5 times"
- "inside it, run planning and planning-analysis as a feedback block"
- "then run development with an inner development-analysis feedback block"
- "attach commit hooks before or after lifecycle completion"

That is mostly the same workflow Ralph already has today, but expressed through composable block architecture instead of flat phase adjacency plus implicit semantics.

The new file format should prioritize:
- explicit group-block vs individual-block definitions
- explicit hook attachment points
- explicit counter ownership
- explicit completion semantics
- explicit edges/routes between blocks and phases
- explicit drain/artifact ownership for hook blocks and nested blocks

The format should be easy to map to a visual editor where users can drag blocks onto a canvas, connect them, and persist the same structure back into policy text.

---

## Full Product Surface Requirements

### 1) Policy source and migration contract

The implementation must specify behavior for every policy source, not just `pipeline.toml` in one place:
- bundled defaults under `ralph/policy/defaults/`
- user-global policy under `~/.config/ralph-workflow-pipeline.toml`
- project-local policy under `.agent/pipeline.toml`
- regenerated/bootstrap-created policy files
- any worktree or copied local policy derived from those sources

The hard-break behavior is now locked:
- **Bundled defaults**: ship only the new block format. No compatibility layer.
- **User-global policy**: if obsolete block-incompatible schema is detected during a mutation command (`--init`, `--init-local-config`, `--regenerate-config`), delete and rewrite it in the new format. On non-mutation commands (`run`, `--check-policy`, `--diagnose`, `--explain-policy`), fail loudly and instruct the user to regenerate.
- **Project-local `.agent/pipeline.toml`**: if obsolete schema is detected, delete and regenerate on mutation commands; fail loudly on non-mutation commands.
- **Bootstrap/generated copies**: never preserve old schema. Regeneration always replaces it with the new format.
- **Checkpoints/resume payloads**: never migrated in place. Obsolete checkpoints are rejected with an explicit incompatibility error and preserved on disk for inspection.

User-facing command ownership is also locked:
- `--init`, `--init-local-config`, `--regenerate-config` are the only commands allowed to rewrite obsolete policy files.
- `run`, `--check-policy`, `--diagnose`, and `--explain-policy` must be non-mutating and fail loudly when they see obsolete policy.

This must be implemented as a **hard-break matrix** covering:
- bundled defaults
- user-global policy
- project-local policy
- regenerated/bootstrap-copied policy
- checkpoint/resume payloads tied to obsolete policy semantics

### 2) CLI and config contract

The redesign must explicitly define:
- how outer developer-iteration blocks map to existing CLI controls like `-D/-Q/-T`, `developer_iters`, and `--counter`
- whether `iteration` remains a reserved conventional alias or becomes fully user-defined
- how `ralph --explain-policy` and `ralph --check-policy` present block-based workflows to users
- how initialization/onboarding/welcome text changes to describe blocks instead of flat phase chains

This contract is now locked:
- `iteration` remains the reserved conventional alias for the bundled default outer developer-iteration block.
- `-Q`, default mode, `-T`, and `developer_iters` continue to target that reserved `iteration` counter for the bundled/default workflow.
- Fully custom workflows may define additional counters freely, but generic tuning of those uses `--counter`, not the shorthand iteration presets.
- `--counter` remains the escape hatch for user-defined block counters in custom workflows.
- Explain/check-policy output must show both the authored block name and the compiled runtime counter/phase mapping so users can understand the shorthand/default behavior.

### 3) Checkpoint and resume contract

This contract is now locked:
- Old checkpoints whose saved phases or compiled lifecycle metadata do not match the new block model are rejected.
- Checkpoints created under flat-phase semantics are never migrated in place.
- Rejected checkpoints are preserved on disk for inspection and must produce an explicit incompatibility error.
- `--resume` against an obsolete checkpoint fails loudly.
- `--no-resume` continues to start a fresh run and does not mutate the obsolete checkpoint.
- Run-context progress mirrors must be rebuilt around lifecycle completion boundaries rather than commit-order heuristics.
- Checkpoint inspection flows must surface that the payload is obsolete under the new policy model rather than pretending it is partially compatible.

### 4) Observability and product explanation contract

The plan must explicitly cover all user-visible explanation surfaces:
- policy explanation output
- policy rendering / ASCII rendering
- transcript / run banners / completion summaries
- snapshots and progress displays
- docs that currently teach the flat phase graph

These surfaces must describe blocks, hooks, lifecycle completion, and loop ownership clearly enough for users to reason about the workflow without reading Python code.

### 5) Artifact and drain contract for blocks/hooks

This contract is now locked:
- Blocks are authoring syntax compiled down to phase leaves.
- Every compiled hook leaf must resolve to an explicit drain; hook blocks do not get implicit magical drain behavior.
- Artifact contracts bind to the compiled hook/phase leaves, not to vague container nodes.
- Fresh-entry artifact clearing is evaluated on compiled lifecycle boundaries and compiled hook boundaries, not on the old flat-phase assumptions.
- Fixed-path handlers like commit cleanup are preserved only as compiled hook leaves with explicit artifact and drain mappings.
- No hook block may rely on canonical phase names for artifact or drain ownership.

### 6) Required test matrix

The plan must enumerate required coverage for:
- schema/loader/validation of the new format
- global/local/bundled config migration behavior
- checkpoint/resume hard-break behavior
- routing/progress/orchestrator/reducer behavior under composable blocks
- explain-policy / rendering / transcript / docs expectations
- custom pipeline and renamed-phase workflows
- commit hook safety across mixed repos
- black-box lifecycle tests that prove the authored block model behaves correctly without asserting on private implementation details

## Implementation Plan

### Task 1: Define the new policy schema for composable lifecycle blocks

**Files likely involved:**
- `ralph/policy/models/`
- block taxonomy / compilation models
- `ralph/policy/loader.py`
- `ralph/policy/validation/`
- `ralph/policy/defaults/pipeline.toml`
- any policy rendering / explanation surface that assumes flat phase-only semantics
- `ralph/phases/__init__.py` (role-handler registration surface)
- `tests/test_policy_validation_*.py`
- `tests/test_policy_loader.py`
- custom-pipeline validation and explanation tests

**Deliverables:**
- New policy schema types for lifecycle/block composition.
- Explicit schema distinction between group blocks and individual blocks.
- Explicit ownership of loop counters vs budget counters.
- A redesigned `pipeline.toml` format centered on composable blocks.
- No backward-compatibility shim for old pipeline semantics.

**TDD steps:**
1. Write failing policy-model tests that describe a development lifecycle block with:
   - inner analysis loop counter
   - pre-commit hook
   - final commit hook
   - lifecycle completion incrementing `iteration`
2. Run those tests and verify they fail for missing schema support.
3. Add minimal schema models and loader support.
4. Re-run tests until the schema parses and validates.

### Task 2: Make reducer/progress interpret lifecycle completion, not commit placement

**Files likely involved:**
- `ralph/pipeline/reducer.py`
- `ralph/pipeline/progress.py`
- `ralph/pipeline/handoffs.py`
- `ralph/pipeline/state.py`
- `ralph/checkpoint/run_context.py`
- `ralph/checkpoint/builder.py`
- `tests/integration/test_pipeline_iterations.py`
- `tests/test_progress_accounting.py`
- `tests/test_pipeline_orchestration_flow.py`

**Deliverables:**
- Budget progression triggered by lifecycle/block completion.
- Commit hooks remain policy-driven but non-authoritative for lifecycle counting.
- Dev cycle counts remain stable regardless of whether pre/post commit hooks exist.
- One canonical lifecycle-completion event/metadata seam is defined and consumed consistently.
- Runtime seams are refactored as needed so lifecycle behavior can be tested black-box through observable outputs, counters, routing, checkpoints, and rendered explanations.

**TDD steps:**
1. Write failing integration tests proving:
   - 2-cycle and 3-cycle dev runs count correctly
   - analysis loopbacks preserve outer budget
   - adding/removing commit hooks does not change lifecycle accounting
   - the same authored workflow produces correct observable behavior through runner, checkpoints, and explain/render surfaces without relying on private state assertions
2. Run tests to verify failure under the current model.
3. Refactor boundaries as needed (including dependency injection and seam extraction) before implementing the minimal behavior change.
4. Re-run until stable.

### Task 3: Enforce the hard break for old policy/config sources

**Files likely involved:**
- `ralph/policy/loader.py`
- `ralph/policy/validation/_api.py`
- `ralph/policy/validation/_checkpoint_policy_mismatch_error.py`
- `ralph/config/bootstrap.py`
- `ralph/config/welcome.py`
- `ralph/onboarding.py`
- `ralph/cli/commands/explain.py`
- `ralph/cli/commands/check_policy.py`
- CLI/config init/regenerate/check-policy/diagnose flows
- migration-related tests/docs

**Deliverables:**
- Old pipeline semantics rejected loudly.
- Clear failure message telling the user their local/global/generated policy is obsolete.
- The incompatible policy redesign is intentional and documented.
- Delete/regenerate behavior is explicit and documented, never silent.
- Bootstrap/regeneration behavior is defined for bundled, global, and project-local policy sources.

**TDD steps:**
1. Write failing tests for legacy `pipeline.toml` loading.
2. Write failing tests for stale global policy, bootstrap-copied policy, and obsolete checkpoint behavior.
3. Assert the error clearly identifies obsolete policy format/semantics.
4. Implement the hard-break matrix with explicit reject/delete/regenerate behavior.
5. Re-run tests.

### Task 4: Remove commit-centric assumptions and harden commit hooks for universal repo applicability

**Files likely involved:**
- `ralph/phases/commit.py`
- `ralph/phases/commit_cleanup.py`
- `ralph/git/commit_cleanup.py`
- `ralph/pipeline/commit_executor.py`
- `ralph/prompts/materialize.py`
- `ralph/pipeline/progress.py`
- `ralph/pipeline/state_init.py`
- `ralph/pipeline/phase_transition.py`
- `ralph/checkpoint/builder.py`
- commit- and progress-related tests in `tests/`

**Deliverables:**
- Safe garbage-file cleanup rules.
- No unsafe deletions outside obvious generated/build junk.
- Binary/untracked behavior explicitly represented in prompts and cleanup hooks.
- Commit scope shown to the agent must match what can actually be staged.
- The implementation explicitly removes current commit-centric lifecycle/accounting assumptions from progress/state-init/checkpoint surfaces.
- Commit-hook behavior is black-box testable via observable repo/pipeline outcomes, even if that requires DI or deeper refactoring of commit executor and cleanup boundaries.

**TDD steps:**
1. Add failing tests for:
   - traversal/absolute/symlink cleanup attempts
   - protected source/config/doc files in web/mobile/desktop repos
   - untracked-only commit scenarios
   - binary garbage visibility in cleanup scope
2. Implement minimal hardening.
3. Re-run focused tests.

### Task 5: Update default policy to demonstrate the new model

**Files likely involved:**
- `ralph/policy/defaults/pipeline.toml`
- docs that explain bundled default workflow
- snapshot/explanation tests
- any visualization/export surface that should expose blocks cleanly

**Deliverables:**
- Default development workflow expressed with composable semantics.
- Inner dev-analysis block explicitly owns `development_analysis_iteration`.
- Developer-cycle accounting owned by lifecycle completion.
- Commit behavior remains configurable at policy level.
- The resulting structure is legible enough to serve as the data model for a future drag-and-drop workflow UI.
- The compiled output preserves enough metadata for explain/display/checkpoint tooling to describe the authored block structure meaningfully.

### Task 6: Observability, documentation, and verification

**Files likely involved:**
- `docs/sphinx/policy-explanation.md`
- `docs/sphinx/artifacts.md`
- `docs/sphinx/configuration.md`
- `docs/sphinx/cli.md`
- `docs/sphinx/concepts.md`
- `docs/sphinx/transcript.md`
- `docs/sphinx/recovery.md`
- migration docs / configuration docs
- policy rendering / explanation code
- `ralph/cli/commands/explain.py`
- `ralph/cli/commands/check_policy.py`
- this plan file

**Deliverables:**
- Docs describe blocks/lifecycles, not only flat phase chains.
- Policy explanation, rendering, transcript, and completion-summary surfaces describe the new model clearly.
- Breaking-change migration guidance is explicit.
- Verification ends with full `make verify`.

### Task 7: Full-stack proof scenarios before sign-off

**Required proof scenarios:**
1. One bundled-default workflow proof:
   - authoring block model
   - compiled phase model
   - loader/validation
   - handler registration (`ralph/phases/__init__.py`)
   - reducer/progress
   - checkpoint/save-resume behavior
   - explain-policy / render / transcript surfaces
2. One renamed custom-workflow proof with no canonical phase-name dependence, including loader → handler registration → reducer/progress → checkpoint → explain/render surfaces.

These scenarios must be represented in tests and documentation before the redesign is considered safe.

---

## Risks to watch during execution

1. **Half-migrated semantics**
   - Worst outcome is mixing old phase-graph assumptions with new lifecycle semantics.
   - Avoid compatibility layers that let old configs partially work.

2. **Hook semantics becoming implicit again**
   - If commit hooks still accidentally own counters, the redesign failed.

3. **Config-source drift**
   - The redesign can still fail for users if global bootstrap/regeneration paths silently keep reintroducing obsolete policy.

4. **Checkpoint ambiguity**
   - Resume behavior must fail clearly when old checkpoints no longer match the new block model.

5. **Poor testability forcing white-box tests**
   - If the architecture keeps hidden coupling and cannot be verified black-box, the redesign is not acceptable and must be refactored further.

6. **Execution-model drift**
   - If implementation quietly mixes first-class blocks and compiled flat phases without a single chosen model, the redesign will become internally inconsistent.

6. **Overfitting default workflow**
   - The new model must work for webapps, desktop apps, mobile repos, and mixed monorepos, not just the current Ralph default flow.

4. **Unsafe cleanup generalization**
   - Cross-codebase garbage-file handling must be conservative and policy-driven where possible.

---

## Definition of done

This planning requirement is satisfied only when:
- lifecycle accounting is defined at the policy/lifecycle level,
- commit hooks are policy-driven and composable,
- old policy/config sources are intentionally rejected or regenerated by explicit product rules,
- the default workflow is expressed entirely through the new policy model,
- the policy structure is suitable for a future drag-and-drop UI/editor,
- all major product surfaces (CLI, bootstrap, explain-policy, docs, checkpoints, artifacts, displays, tests) are covered by the plan,
- one explicit execution model is chosen and enforced,
- the distinction between group blocks and individual blocks is explicit in the policy model,
- one canonical lifecycle-completion seam is defined,
- full-stack proof scenarios exist for bundled and renamed custom workflows,
- the architecture is black-box testable without depending on private implementation details,
- and reverting workflow behavior requires only config/policy edits.
