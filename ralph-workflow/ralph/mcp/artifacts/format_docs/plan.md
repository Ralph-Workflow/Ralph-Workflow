# plan artifact format

## Version

version: 0.9.0
last_updated: 2026-06-12

## New in this revision

- **Generous-but-bounded plan size limits.** New `PlanSizeLimits` frozen dataclass (`max_total_bytes=4_000_000`, `max_steps=500`, `max_scope_items=200`, `max_acceptance_criteria=500`, `max_evidence_per_step=500`, `max_risks=200`, `max_verification_steps=100`, `max_primary_files=200`, `max_reference_files=200`, `max_parallel_plan_items=200`, `max_work_units=200`, `max_constraint_list_entries=500`) plus three string-length tiers (`max_string_short=1000`, `max_string_medium=8000`, `max_string_long=20000`) replace the previous tight caps. The new per-field cap table lives in the `## Plan size limits` section below and is the single source of truth — no model hard-codes a cap.
- **`depends_on` cycle detector.** `PlanArtifact._validate_depends_on_acyclic` (a new `@model_validator(mode='after')`) runs BEFORE the existing AC<->step cross-reference scan and rejects cyclic `steps[*].depends_on` graphs (e.g. step 1 -> 2 -> 3 -> 1) with the stable message `plan step depends_on cycle detected at step N`. Diamond-shaped DAGs (a node with multiple parents) are accepted. The pattern mirrors `ralph.pipeline.work_units._validate_acyclic` at `ralph/pipeline/work_units.py:158`.
- **Pure-return size guard.** `check_plan_size(content, *, limits=PlanSizeLimits.DEFAULT)` is a PURE helper that NEVER raises. It returns the FIRST violation as a `PlanArtifactSizeError` (with `.field`, `.actual`, `.cap` attributes populated) or `None`. The caller (`normalize_plan_artifact_content` in `_validation.py`) is the single point that raises `PlanArtifactValidationError` when the helper returns a non-`None` error. The 4 MB hard byte cap runs FIRST so a runaway payload is rejected in < 100 ms before Pydantic ever touches it.
- **Typed `EvidenceRef`** for `PlanStep.expected_evidence`. The canonical entry is a `{kind, ref, note?}` object with `kind` in `{file, command_output, test_name}`. A bare string is accepted as a compatibility shorthand and canonicalized to `{"kind":"file","ref":"..."}`.
- **Top-level `PlanConstraints`** section. Optional `must_not_break` / `must_keep_working` lists (each 1-1000 chars, deduped case-insensitively) plus `performance_budget` and `security_posture` strings (1-2000 chars). Submitted via `ralph_submit_plan_section` with `section="constraints"` in `mode="replace"`. Rendered as `## Project Constraints` between `## Critical Files` and `## Risks and Mitigations`.
- **Typed `VerificationStep.timeout_seconds` and `cwd`.** `timeout_seconds: int | None` with `gt=0, le=3600`; `cwd: str | None` with `max_length=500`. Both are optional; defaults to platform / workspace root.
- **Typed `noop: bool | None` field on `PlanArtifact`.** Default `None`, `exclude=True` for mypy-completeness and `model_fields` introspection. The runtime noop short-circuit (`is_noop_plan`) is unchanged: it reads the raw `Mapping` and `normalize_plan_artifact_content` returns `{"noop":true}` directly without round-tripping through the model. The typed field is for discovery only; do NOT rely on `model.model_dump()` to preserve the noop marker (it is dropped by `exclude=True`).
- **`step_type` default is `action`** (closed enum: `file_change`, `action`, `research`, `verify`). File-modifying steps must explicitly set `step_type="file_change"` and provide `targets`.
- **Four new cross-section validators** in `PlanArtifact._validate_step_ac_cross_references`:
  1. `summary.intent_verb` -> `scope_item.category` compatibility (HARD error; widened 9-verb x 15-category mapping).
  2. `parallel_plan` and `work_units` mutual exclusion (HARD error).
  3. `verification_strategy[*].method` shell-invocation guard (HARD error; rejects `bash -c `, `sh -c `, `eval ` prefixes with trailing space).
  4. `design.acceptance_criteria` `satisfied_by_steps` cannot reference a `research` or `verify` step (HARD error).

The new symbols and the renamed heading are: `EvidenceRef`, `PlanConstraints`, `noop`, `timeout_seconds`, `cwd`, `SE-opinionated design surfaces` (the renamed "Universal SE bias" subsection).

**Parallel execution delegated to the AI agent.** Plans that want to express parallelization intent for the executing agent's sub-agents should declare `work_units` (same-workspace) or `parallel_plan` (read-mostly chunks). Each work unit is dispatched to one of the **agent-managed sub-agents** in this build. Both `work_units` and `parallel_plan` are **agent-facing parallelization intent** in this build — Ralph-managed fan-out is dormant, and the bundled `fan-out is dormant` invariant is enforced by the `audit_parallelization_dormant` audit. The executing agent dispatches its own sub-agents (or runs sequentially when no sub-agent capability is available). Plans that do not declare either field remain valid and run sequentially.

## What you are doing

You are describing the implementation plan for a coding task. The plan captures the problem context, the executor-ready steps, the critical files to touch, the risks and mitigations, and the verification strategy. A development agent will read the plan and execute it without re-planning.

Parallel execution is delegated to the executing AI agent's native sub-agent tooling. Ralph does not run parallel workers in the bundled default: when a plan declares `work_units` or `parallel_plan`, the executing agent dispatches its own sub-agents (or runs sequentially when no sub-agent capability is available). Ralph-managed fan-out is **dormant** in this build and is retained only for future re-arming.

## How to submit

The plan artifact is submitted with the dedicated planning MCP tools. Stage
sections with `ralph_submit_plan_section` or `ralph_submit_plan_sections`,
then call `ralph_finalize_plan` after every required section is present.

### Step-wise path

Use the dedicated planning MCP tools. Submit one section at a time so each section is validated as you go:

- `ralph_submit_plan_section` with `section="summary"` and a summary object
- `ralph_submit_plan_section` with `section="skills_mcp"` and a skills/MCP object
- `ralph_submit_plan_section` with `section="steps"` and an array of step objects
- `ralph_submit_plan_section` with `section="critical_files"` and a critical-files object
- `ralph_submit_plan_section` with `section="risks_mitigations"` and an array of risk objects
- `ralph_submit_plan_section` with `section="design"` and a design object (optional but recommended)
- `ralph_submit_plan_section` with `section="verification_strategy"` and an array of verification objects
- `ralph_finalize_plan` once every required section is staged and valid

The `design` section is submitted via `ralph_submit_plan_section` with `section="design"` and `mode="replace"` (the only supported mode). The planning MCP `submit_plan_section` and `finalize_plan` tools are the source of truth for step-wise plan submission.

## Required fields (inside content)

- `summary` — a summary object with:
  - `context` — a string up to 8000 chars describing the task; if empty/absent, render_plan_markdown inserts the placeholder "No additional context provided."
  - `scope_items` — an array of at least 3 scope items, each with a non-empty `text` (optional `count` and `category`)
- `skills_mcp` — a skills/MCP object with:
  - `skills` — a non-empty array of task-relevant skill names
  - `mcps` — an optional array of MCP server names (empty array is fine)
- `steps` — an array of at least 1 step, each with:
  - `number` — a positive integer step number
  - `title` — a non-empty imperative title
  - `content` — detailed executor-ready instructions
  - `step_type` — one of `"file_change"`, `"action"`, `"research"`, or `"verify"` (defaults to `"action"`)
  - `priority` — optional, one of `"critical"`, `"high"`, `"medium"`, `"low"`
  - `targets` — optional array of `{path, action}` objects where `action` is one of `create`, `modify`, `delete`, `read`, `reference`
  - `depends_on` — optional array of integer step numbers this step depends on
- `critical_files` — a critical-files object with:
  - `primary_files` — an array of at least 1 `{path, action}` object where `action` is one of `create`, `modify`, or `delete`
  - `reference_files` — optional array of `{path, purpose}` objects
- `risks_mitigations` — an array of at least 1 risk object with `risk` and `mitigation` strings (optional `severity`: `low`, `medium`, `high`, `critical`)
- `verification_strategy` — an array of at least 1 verification object with `method` and `expected_outcome` strings

## Optional fields

- `design` — a design object (see Design sub-section below). Strongly recommended for any non-trivial multi-file task or refactor. May include an optional `planning_profile` preset (`strict` | `balanced`) that bias-fills the seven typed sub-sections; user values always win.
- `parallel_plan` — an array of `ParallelPlanItem` objects describing safe-to-parallelize work chunks. Use only when genuinely safe.
- `work_units` — an array of work unit objects for same-workspace parallel fan-out. Each unit must declare `allowed_directories` and pass the parallel preflight.
- `summary.intent` and `summary.intent_verb` — `Summary` gains two optional planning-analysis fields. `intent` is a ≤500-char one-line user-facing outcome (defaults to empty string; dropped from model_dump(exclude_defaults=True), mirroring `summary.context`); `intent_verb` is a closed enum (`add`|`fix`|`refactor`|`migrate`|`document`|`investigate`|`improve`|`configure`|`remove`) stored as `str` with default `""` (also dropped from model_dump(exclude_defaults=True) so the round-trip is clean). A before-validator lowercases the value BEFORE the closed-enum check (so `Add` and `ADD` both pass), and explicit `""` is rejected with `ValueError("intent_verb must not be empty")` to distinguish a deliberate empty value from an omitted field. When set, the rendered plan gets a new `## Intent` heading at the top with the verb and the outcome.
- `steps[*].satisfies`, `steps[*].expected_evidence`, `steps[*].verify_command` — `PlanStep` gains three optional fields. `satisfies` is a list of acceptance-criterion ids (each matching `^[A-Z]+-\d{2,}$`); `expected_evidence` lists the artifacts / files / test outputs that prove the step completed (deduped case-insensitively, blank entries dropped, last-wins for case collision); `verify_command` is an optional command the executor may run for a step and is REQUIRED whenever `step_type == "verify"` and `location` is absent. See the new tightened contract section.

## Tightened step contract

Every plan must satisfy the following per-step and cross-section rules or it will be rejected at `normalize_plan_artifact_content` time:

- **Every `file_change` step must declare at least one `targets` entry.** A `file_change` step without `targets` is rejected with `ValueError("file_change step must declare at least one target")`. Steps that touch zero files should use a different `step_type` (e.g. `action` or `research`) or declare a `read`/`reference` target.
- **Every `verify` step must declare either `verify_command` or `location`.** A `verify` step with neither is rejected with `ValueError("verify step must declare verify_command or location")`. `verify_command` is the shell command to run (e.g. `pytest tests/test_x.py -q`); `location` is the test-file path (e.g. `tests/test_x.py`).
- **`step.satisfies` entries must reference real AC ids.** Each entry must match `^[A-Z]+-\d{2,}$` AND must appear in `design.acceptance_criteria.criteria[*].id`. Orphan references raise `PlanArtifactValidationError("step N satisfies unknown acceptance criterion 'X'")`.
- **`step.satisfies` is rejected when the plan has no `design.acceptance_criteria`.** Set the design sub-section first, then link steps to it.
- **`AC.satisfied_by_steps` entries must reference real step numbers.** Each entry must be an integer ≥1 that appears in the plan's `steps[*].number`. Orphan references raise `PlanArtifactValidationError("acceptance criterion 'X' references unknown step number N")`.
- **`AC.satisfied_by_steps` cannot reference a `research` or `verify` step.** Only `file_change` and `action` steps can satisfy an AC. References to `step_type="research"` or `step_type="verify"` raise `PlanArtifactValidationError("satisfied_by_steps cannot reference a research or verify step")`.
- **`parallel_plan` and `work_units` are mutually exclusive.** A plan that declares BOTH sections is rejected with `PlanArtifactValidationError("plan cannot declare both parallel_plan and work_units; pick one")`. Pick the section that fits the work shape — `parallel_plan` for safe-to-parallelize read-mostly chunks, `work_units` for same-workspace parallelization intent consumed by the executing agent's sub-agents. Both fields are **agent-facing parallelization intent**, NOT Ralph fan-out instructions.
- **`verification_strategy[*].method` must not invoke a shell interpreter directly.** Methods that start with `bash -c ` (note the trailing space), `sh -c `, or `eval ` are rejected with `PlanArtifactValidationError("verification method must not invoke a shell interpreter directly; use the executable path")`. Legitimate invocations like `bash ./scripts/check.sh` (prefix `bash `, not `bash -c `) are NOT blocked.

### Step-mutation read-after-write echo

The five step-mutation tools (`ralph_insert_plan_step`, `ralph_replace_plan_step`, `ralph_patch_step`, `ralph_remove_plan_step`, `ralph_move_plan_step`) auto-reindex the entire steps list AND remap `depends_on` / `AC.satisfied_by_steps` references in the design sub-section. After the mutation, the tool returns a JSON echo payload so the agent does not need to call `ralph_get_plan_draft` to learn the new numbering. The echo shape per tool:

- `ralph_insert_plan_step` returns `{"action":"insert","index":2,"new_step_number":2,"reindex_map":{"2":3},"rewritten_depends_on":[3],"rewritten_ac_satisfied_by_steps":["AC-02"],"dropped_ac_satisfied_by_steps":[],"validation_warnings":[],"total_steps":3}`.
- `ralph_replace_plan_step` returns `{"action":"replace","step_number":2,"reindex_map":{"2":2},"rewritten_depends_on":[],"rewritten_ac_satisfied_by_steps":[],"dropped_ac_satisfied_by_steps":[],"validation_warnings":[],"total_steps":3}`.
- `ralph_patch_step` returns `{"action":"replace","step_number":2,"reindex_map":{"2":2},"rewritten_depends_on":[],"rewritten_ac_satisfied_by_steps":[],"dropped_ac_satisfied_by_steps":[],"validation_warnings":[],"total_steps":3}`.
- `ralph_remove_plan_step` returns `{"action":"remove","removed_step_number":2,"reindex_map":{"3":2},"rewritten_depends_on":[2],"rewritten_ac_satisfied_by_steps":["AC-02"],"dropped_ac_satisfied_by_steps":[],"validation_warnings":[],"total_steps":2}`.
- `ralph_move_plan_step` returns `{"action":"move","from_step_number":3,"to_index":1,"reindex_map":{"3":1},"rewritten_depends_on":[1],"rewritten_ac_satisfied_by_steps":["AC-02"],"dropped_ac_satisfied_by_steps":[],"validation_warnings":[],"total_steps":3}`.

`removed_step_number` / `from_step_number` are the source identifiers (pre-mutation). `reindex_map` is the `{old: new}` number mapping. `rewritten_depends_on` lists step numbers whose `depends_on` array was rewritten. `rewritten_ac_satisfied_by_steps` lists AC ids whose `satisfied_by_steps` was rewritten. `dropped_ac_satisfied_by_steps` is retained for compatibility; the lenient staging path preserves unresolved JSON and reports it through `validation_warnings` so `ralph_validate_draft` / `ralph_finalize_plan` reject the final plan without losing data.

### Net-new step-mutation tool: `ralph_patch_step`

`ralph_patch_step` is a partial-update of one step. Pass `step_number` and a step dict with ANY subset of step fields; the missing fields are preserved from the existing step. The provided `step.number` is ignored (replace_plan_step forces the number to `step_number`). The step-mutation auto-reindex of `depends_on` and `AC.satisfied_by_steps` runs as for `ralph_replace_plan_step`. The same echo payload shape is returned. Use this instead of `ralph_replace_plan_step` when only one or two fields change. Capability: `artifact.plan_write`.

### Net-new read-only tool: `ralph_validate_draft`

`ralph_validate_draft` runs the full `PlanArtifact` cross-section validator (depends_on cycle, intent_verb vs scope_item category, parallel_plan XOR work_units, shell-invocation guard, research/verify steps in AC.satisfied_by_steps, AC id pattern, non-empty `skills_mcp.skills`, 4 MB size cap) without writing `plan.json` and without deleting the in-progress draft. Returns `{"valid":true}` on success or `{"valid":false,"errors":["summary: required field is missing"]}` on failure. If no draft exists yet, it returns `{"valid":false}` with a named missing-draft error instead of a false-green success. Cross-section invariants only run at `finalize_plan` in the write path; `ralph_validate_draft` exposes the same checks in a read-only path. Capability: `artifact.plan_read`.

### Net-new batched tool: `ralph_submit_plan_sections`

`ralph_submit_plan_sections` accepts an object with an `entries` array, like `{"entries":[{"section":"summary","content":{"context":"Fix foo() out-of-range index handling after reading src/foo.py and tests/test_foo.py","intent":"Clamp foo() indexes and prove the regression with a focused test","intent_verb":"fix","scope_items":[{"text":"Add tests/test_foo.py::test_clamp_handles_out_of_range_index","category":"test"},{"text":"Update src/foo.py to clamp negative and oversized indexes without changing the public foo() signature","category":"bugfix"},{"text":"Run pytest tests/test_foo.py -q to prove the regression is fixed","category":"test"}]},"mode":"replace"}]}` and parses ALL entries BEFORE any merge; malformed JSON, unknown sections, and impossible modes reject the batch (`{"submitted":[],"failed_at":1,"error":"..."}`) and leave the draft unchanged. Schema-invalid but valid JSON is staged and returned in `validation_warnings`. On success it returns `{"submitted":["summary"],"staged_sections":["summary"],"total_bytes":123,"validation_warnings":[]}`. Use this when you have built complete, analysis-ready section payloads, then run `ralph_validate_draft`. The full cross-section validator still runs at `validate_draft` / `finalize_plan`. Capability: `artifact.plan_write`.

### Design sub-section

The `design` field is OPTIONAL. When present, it carries seven typed sub-models that bias the executor toward good Software Engineering practices:

- `constraints` — `DesignConstraints` with a non-empty `text` (1-2000 chars), optional `invariants` (each 1-500 chars), and optional `architecture_style` (one of `monolith`, `modular-monolith`, `microservice`, `library`, `cli`, `spa`, `mobile`, `serverless`, `embedded`, `unknown`).
- `non_goals` — `NonGoals` with a non-empty `items` list (each 1-500 chars).
- `dependency_injection` — `DependencyInjection` with `required_for_testability` (bool), optional `preferred_patterns` and `forbidden_patterns` from the schema enums, and optional `notes` (max 2000 chars).
- `drift_detection` — `DriftDetection` with optional `guard_commands` (each must use only `[A-Za-z0-9 _./\-:=+]`), `expected_outputs`, `sources` (one of `ruff`, `mypy`, `pytest`, `make`, `custom-script`, `ci`, `unknown`), and `on_drift_action` (`fail-verify`, `log-only`, `open-issue`, `ignore`).
- `testability` — `Testability` with `must_be_black_box` (bool), optional `forbidden_in_tests` and `required_test_layers` from the schema enums, optional `clock_injection_required`, and `max_unit_test_seconds` (0 < N <= 60).
- `refactor_strategy` — `RefactorStrategy` with `approach` (one of `greenfield`, `incremental`, `strangler`, `branch-by-abstraction`, `rebuild-in-parallel`, `no-refactor`), optional `preserve_public_api`, `dead_code_policy` (default `delete-immediately`), and `allow_temporary_hacks` (default `false`).
- `acceptance_criteria` — `AcceptanceCriteria` with at least 1 `AcceptanceCriterion` entry. Each criterion has `id` matching `^[A-Z]+-\d{2,}$`, `description` (1-8000 chars), optional `satisfied_by_steps` that references concrete `file_change` or `action` step numbers, and optional `verification_step` and `evidence_path`.
- `notes` — optional free-form rationale. Populate it with rationale, alternative designs, and trade-offs for any non-trivial plan.

Detailed `design` example payload:

```json
{
  "design": {
    "testability": {
      "must_be_black_box": true,
      "forbidden_in_tests": ["time.sleep"],
      "required_test_layers": ["unit"]
    },
    "acceptance_criteria": {
      "criteria": [{"id": "AC-01", "description": "Round-trips through the normalizer"}]
    }
  }
}
```

## Complete example (detailed bugfix plan)

This example is a detailed plan style that should be copied for real work:
it is specific enough for planning analysis to evaluate and executor-ready
enough that the development agent does not need to invent scope, files,
dependencies, or verification. It uses the dedicated planning payload shape,
non-empty skills, acceptance criteria, step links, concrete targets, and
evidence.

```json
{
  "summary": {
    "context": "Fix the foo() off-by-one regression and prove it with a focused unit test.",
    "intent": "Clamp foo() index so the off-by-one regression cannot recur.",
    "intent_verb": "improve",
    "scope_items": [
      {"text": "Add a regression test for the out-of-range foo() index", "category": "test"},
      {"text": "Modify src/foo.py to clamp the index before lookup", "category": "file_change"},
      {"text": "Run the focused pytest command that proves the regression is fixed", "category": "test"}
    ]
  },
  "skills_mcp": {
    "skills": ["test-driven-development", "systematic-debugging"],
    "mcps": []
  },
  "steps": [
    {
      "number": 1,
      "title": "Add the foo() regression test",
      "content": "Add tests/test_foo.py::test_clamp_handles_out_of_range_index that fails on the current off-by-one behavior before production code is changed.",
      "step_type": "file_change",
      "priority": "high",
      "targets": [{"path": "tests/test_foo.py", "action": "modify"}],
      "satisfies": ["AC-01"],
      "expected_evidence": [
        {"kind": "file", "ref": "tests/test_foo.py"},
        {"kind": "test_name", "ref": "tests/test_foo.py::test_clamp_handles_out_of_range_index"}
      ],
      "depends_on": []
    },
    {
      "number": 2,
      "title": "Clamp the foo() index",
      "content": "Update src/foo.py so the lookup index is clamped to the valid range while preserving the public foo() signature.",
      "step_type": "file_change",
      "priority": "high",
      "targets": [{"path": "src/foo.py", "action": "modify"}],
      "satisfies": ["AC-02"],
      "expected_evidence": [
        {"kind": "file", "ref": "src/foo.py"},
        {"kind": "test_name", "ref": "tests/test_foo.py::test_clamp_handles_out_of_range_index"}
      ],
      "depends_on": [1]
    },
    {
      "number": 3,
      "title": "Run the focused regression test",
      "content": "Run the focused pytest command from the repository root and confirm it passes.",
      "step_type": "verify",
      "verify_command": "pytest tests/test_foo.py -q",
      "expected_evidence": [
        {"kind": "command_output", "ref": "pytest tests/test_foo.py -q"}
      ],
      "depends_on": [2]
    }
  ],
  "critical_files": {
    "primary_files": [
      {"path": "src/foo.py", "action": "modify"},
      {"path": "tests/test_foo.py", "action": "modify"}
    ],
    "reference_files": []
  },
  "risks_mitigations": [
    {
      "risk": "Clamping could hide a caller bug that should still be visible in debug output.",
      "mitigation": "Keep the public signature stable and add focused assertions that document the intended clamping behavior.",
      "severity": "medium"
    }
  ],
  "verification_strategy": [
    {
      "method": "pytest tests/test_foo.py -q",
      "expected_outcome": "The focused regression test passes.",
      "timeout_seconds": 60,
      "cwd": "."
    }
  ],
  "constraints": {
    "must_not_break": ["public foo() signature"],
    "must_keep_working": ["pytest tests/test_foo.py -q"]
  },
  "design": {
    "planning_profile": "strict",
    "outcome": "foo() handles out-of-range indexes without crashing and the regression test passes.",
    "testability": {
      "must_be_black_box": true,
      "forbidden_in_tests": ["time.sleep"],
      "required_test_layers": ["unit"]
    },
    "acceptance_criteria": {
      "criteria": [
        {
          "id": "AC-01",
          "description": "A focused regression test covers the out-of-range index.",
          "satisfied_by_steps": [1]
        },
        {
          "id": "AC-02",
          "description": "src/foo.py clamps the index while preserving the public signature.",
          "satisfied_by_steps": [2]
        }
      ]
    }
  }
}
```

This example is exercised by tests/test_artifact_format_docs.py and must round-trip.

## Complete example (detailed architecture plan)

This example is the canonical detailed plan. It exercises the full `PlanArtifact` shape (not just a preset) so the executor and the format-doc reader can both see how every typed sub-section fits together. Populates:

- `summary.intent` + `summary.intent_verb` + `summary.coverage_areas`.
- `design.notes` (≥500 chars of rationale).
- `design.drift_detection.guard_commands` (≥2 commands) + `expected_outputs` + `sources` + `on_drift_action`.
- `design.testability.must_be_black_box` + `forbidden_in_tests` + `required_test_layers` (every layer that applies).
- `design.dependency_injection.preferred_patterns` + `forbidden_patterns` (each populated).
- `design.refactor_strategy.approach` + `dead_code_policy`.
- `design.acceptance_criteria` with 3+ criteria, each with `satisfied_by_steps` populated.
- `steps[*].depends_on` forming a 4-step diamond (1 -> 2, 1 -> 3, 2 -> 4, 3 -> 4) so the cycle guard is exercised.
- `steps[*].expected_evidence` with 3+ entries per step using the typed `EvidenceRef` shape (`{kind, ref, note?}`).
- The top-level `PlanConstraints` section (must_not_break + must_keep_working + performance_budget + security_posture).

```json
{
  "summary": {
    "intent": "Improve src/foo.py testability by refactoring it to use constructor injection.",
    "intent_verb": "improve",
    "coverage_areas": ["refactor", "test"],
    "scope_items": [
      {"text": "Replace module-level mutable state in src/foo.py with constructor injection", "category": "refactor"},
      {"text": "Add a unit test for the new constructor-injected foo()", "category": "test"},
      {"text": "Wire the new foo() from src/main.py", "category": "refactor"}
    ]
  },
  "skills_mcp": {
    "skills": ["test-driven-development", "systematic-debugging"],
    "mcps": []
  },
  "steps": [
    {
      "number": 1,
      "title": "Audit foo() module-level state",
      "content": "Read src/foo.py and identify every module-level mutable and the test seams it blocks.",
      "step_type": "research",
      "depends_on": [],
      "expected_evidence": [
        {"kind": "file", "ref": "src/foo.py", "note": "current module-level state"},
        {"kind": "command_output", "ref": "grep -n '^[A-Za-z_]\\+ =' src/foo.py", "note": "all assignments at module level"}
      ]
    },
    {
      "number": 2,
      "title": "Refactor foo() to take its dependencies as constructor parameters",
      "content": "Add a FooConfig dataclass; convert module-level lookups to constructor params; preserve the public foo() signature for callers; add the focused unit test that proves FooConfig injection works.",
      "step_type": "file_change",
      "targets": [
        {"path": "src/foo.py", "action": "modify"},
        {"path": "tests/test_foo.py", "action": "modify"}
      ],
      "depends_on": [1],
      "expected_evidence": [
        {"kind": "file", "ref": "src/foo.py", "note": "FooConfig dataclass + injected deps"},
        {"kind": "test_name", "ref": "tests/test_foo.py::test_foo_uses_injected_dep"}
      ]
    },
    {
      "number": 3,
      "title": "Wire the new foo() from main.py",
      "content": "Construct FooConfig in main.py and pass it to foo().",
      "step_type": "file_change",
      "targets": [{"path": "src/main.py", "action": "modify"}],
      "depends_on": [1],
      "expected_evidence": [
        {"kind": "file", "ref": "src/main.py", "note": "FooConfig construction"},
        {"kind": "command_output", "ref": "grep -n 'FooConfig' src/main.py"}
      ]
    },
    {
      "number": 4,
      "title": "Run ruff + mypy + pytest",
      "content": "Run the full verification chain to prove the refactor is clean.",
      "step_type": "verify",
      "verify_command": "ruff check src/ && mypy src/ && pytest tests/test_foo.py -q",
      "depends_on": [2, 3],
      "expected_evidence": [
        {"kind": "command_output", "ref": "ruff check src/"},
        {"kind": "command_output", "ref": "mypy src/"},
        {"kind": "test_name", "ref": "tests/test_foo.py::test_foo_uses_injected_dep"}
      ]
    }
  ],
  "critical_files": {
    "primary_files": [
      {"path": "src/foo.py", "action": "modify"},
      {"path": "src/main.py", "action": "modify"}
    ]
  },
  "risks_mitigations": [
    {"risk": "Public foo() signature changes and breaks callers", "mitigation": "Keep foo() signature stable; only internal lookups move to constructor params.", "severity": "high"}
  ],
  "verification_strategy": [
    {"method": "pytest tests/test_foo.py -q", "expected_outcome": "tests/test_foo.py::test_foo_uses_injected_dep passes and proves foo() uses the injected dependency seam."}
  ],
  "constraints": {
    "must_not_break": ["public foo() signature", "CLI entry point"],
    "must_keep_working": ["pytest tests/test_foo.py -q"],
    "performance_budget": "foo() must stay under 1 ms in the hot loop",
    "security_posture": "No new file reads; FooConfig values are passed by callers"
  },
  "design": {
    "outcome": "foo() is testable in isolation with no module-level state.",
    "notes": "The current foo() reads from a module-level dict that prevents isolated tests. Move the dict into a FooConfig dataclass and inject it via the constructor. The diamond-shaped depends_on graph (1->2, 1->3, 2->4, 3->4) lets the audit step 4 run only after BOTH refactor paths finish. The design surface (notes, drift_detection guard commands, testability, dependency_injection patterns, refactor_strategy) is populated explicitly so the executor does not need to derive any defaults.",
    "drift_detection": {
      "guard_commands": [
        "ruff check src/",
        "uv run python -m mypy src/",
        "pytest tests/test_foo.py -q"
      ],
      "expected_outputs": [
        "All checks passed",
        "Success: no issues found",
        "passed"
      ],
      "sources": ["ruff", "mypy", "pytest"],
      "on_drift_action": "fail-verify"
    },
    "testability": {
      "must_be_black_box": true,
      "forbidden_in_tests": ["time.sleep", "subprocess.run-no-timeout"],
      "required_test_layers": ["unit", "integration"]
    },
    "dependency_injection": {
      "required_for_testability": true,
      "preferred_patterns": ["constructor", "parameter"],
      "forbidden_patterns": ["global-singleton", "module-level-mutable-state", "import-time-side-effects"],
      "notes": "FooConfig is the constructor-injected seam; the module-level dict must be removed in the same commit."
    },
    "refactor_strategy": {
      "approach": "incremental",
      "dead_code_policy": "delete-immediately",
      "allow_temporary_hacks": false
    },
    "acceptance_criteria": {
      "criteria": [
        {"id": "AC-01", "description": "foo() accepts FooConfig via constructor", "satisfied_by_steps": [2]},
        {"id": "AC-02", "description": "main.py constructs FooConfig and passes it to foo()", "satisfied_by_steps": [3]},
        {"id": "AC-03", "description": "tests/test_foo.py covers FooConfig injection", "satisfied_by_steps": [2]},
        {"id": "AC-04", "description": "foo.py and main.py both use FooConfig without module-level mutable state", "satisfied_by_steps": [2, 3]}
      ]
    }
  }
}
```

This example round-trips through `normalize_plan_artifact_content` and is exercised by `tests/test_artifact_format_docs.py`.

## StepType reference

The `steps[*].step_type` field is a closed enum. Pick exactly one:

| Value         | When to use                                                            |
|---------------|------------------------------------------------------------------------|
| `file_change` | The step creates, modifies, or deletes one or more source files. (Note: this is NOT the default — the default is action; a file_change step must be declared explicitly and must list every file in targets.) |
| `action`      | The step runs a non-mutating executor action (a command, a tool call). |
| `research`    | The step is exploratory and may not produce a code change.             |
| `verify`      | The step is a pure-verification step (e.g. `run ruff`, `run pytest`) with no file changes. |

### StepType aliases

`step_type` accepts the four canonical values above ONLY. Always write the
canonical value explicitly:

| Wrong value       | Canonical value | Rationale                                        |
|-------------------|-----------------|--------------------------------------------------|
| `test`, `tests`   | `verify`        | Test-running steps are verification steps.       |
| `check`, `run`    | `verify`        | Check-running steps are verification steps.      |

The closed enum is unchanged: `file_change`, `action`, `research`, `verify`
are the only canonical values.

## Closed enums

The plan schema uses closed-string enums (Pydantic `Literal` types) for every field where the value is bounded. The closed set is enforced at the field-validator level; a value outside the set is rejected with a `ValueError` naming the closed set. This is the single source of truth — the values are also the canonical values in the format doc tables below.

### `StepType` — `steps[*].step_type` (4 values)

`file_change`, `action`, `research`, `verify`. See the `## StepType reference` section above for usage.

### `ScopeCategory` — `summary.scope_items[*].category` (15 values, 3 legacy)

`bugfix`, `feature`, `refactor`, `test`, `docs`, `infra`, `migration`, `security`, `performance`, `cleanup`, `research`, `unknown`, plus the legacy aliases `file_change`, `prompt`, `other` (kept for backward compatibility with test fixtures and existing plan examples). See the `## ScopeCategory reference` section for usage.

### `CoverageArea` — `summary.coverage_areas[*]` (10 values)

`bugfix`, `feature`, `refactor`, `test`, `docs`, `infra`, `security`, `performance`, `migration`, `release`. See the `## CoverageArea reference` section for usage.

### `Summary.intent_verb` — closed 9-verb set

`add`, `fix`, `refactor`, `migrate`, `document`, `investigate`, `improve`, `configure`, `remove`. Empty string (`""`) is also accepted (default) to skip the cross-section check; explicit `""` is rejected with `ValueError("intent_verb must not be empty")` to distinguish a deliberate empty from an omitted field. A before-validator lowercases the value BEFORE the closed-set check (so `Add` and `ADD` both pass). The 9-verb x 15-category cross-section mapping is documented in `## Cross-section invariants`.

### `PlanStep.priority` — 4 values

`critical`, `high`, `medium`, `low`. Optional; default `None`.

### `RiskMitigation.severity` — 4 values

`low`, `medium`, `high`, `critical`. Optional; default `None`.

### `StepTarget.action` — 5 values

`create`, `modify`, `delete`, `read`, `reference`. The first three are required for `critical_files.primary_files[*].action`; the last two are for read-only / reference targets on a `file_change` step that touches zero files.

### `DriftDetection.sources` — 7 values

`ruff`, `mypy`, `pytest`, `make`, `custom-script`, `ci`, `unknown`. List values; closed per element.

### `DriftDetection.on_drift_action` — 4 values

`fail-verify`, `log-only`, `open-issue`, `ignore`. Optional; default `None`.

### `RefactorStrategy.approach` — 6 values

`greenfield`, `incremental`, `strangler`, `branch-by-abstraction`, `rebuild-in-parallel`, `no-refactor`. Required.

### `RefactorStrategy.dead_code_policy` — 4 values

`delete-immediately`, `delete-after-feature`, `keep-for-trace`, `unknown`. Default `delete-immediately` (the SE-opinionated default).

### `Testability.forbidden_in_tests` — 7 values

`time.sleep`, `subprocess.run-no-timeout`, `real-file-IO`, `real-network`, `global-mutation`, `monkeypatch-of-prod`, `unknown`. List values; closed per element.

### `Testability.required_test_layers` — 7 values

`unit`, `integration`, `subprocess_e2e`, `property`, `snapshot`, `contract`, `unknown`. List values; closed per element.

### `DependencyInjection.preferred_patterns` — 6 values

`constructor`, `parameter`, `interface`, `service-locator`, `ambient-context`, `unknown`. List values; closed per element.

### `DependencyInjection.forbidden_patterns` — 6 values

`global-singleton`, `module-level-mutable-state`, `import-time-side-effects`, `subprocess-time-random`, `env-var-direct-read`, `unknown`. List values; closed per element.

### `DesignConstraints.architecture_style` — 10 values

`monolith`, `modular-monolith`, `microservice`, `library`, `cli`, `spa`, `mobile`, `serverless`, `embedded`, `unknown`. Optional; default `None`.

### `AcceptanceCriterion.id` — regex `^[A-Z]+-\d{2,}$`

Required; case-sensitive; must be unique within `design.acceptance_criteria.criteria[*].id` (case-insensitive uniqueness — `AC-01` and `ac-01` collide). The pattern is enforced at the field-validator level.

### `PlanningProfile` — `design.planning_profile` (2 values)

`strict`, `balanced`. Optional; default `None`. Each preset bias-fills the seven typed `design` sub-sections with SE-opinionated defaults (user values always win). See the `### Preset-by-preset SE-bias defaults` subsection in `## SE-opinionated design surfaces`.

### `EvidenceKind` — `EvidenceRef.kind` (3 values)

`file`, `command_output`, `test_name`. Required; discriminator. Use objects like `{"kind": "file", "ref": "src/foo.py"}`.

### `SectionMode` — `ralph_submit_plan_section.mode` (2 values)

`replace` (default), `append`. Object sections (`summary`, `skills_mcp`, `critical_files`, `constraints`, `design`) only accept `mode="replace"`; list sections (`steps`, `risks_mitigations`, `verification_strategy`, `parallel_plan`) accept both.

### `StepMode` — `ralph_insert_plan_step.index` semantics

1-based insert index. Integers and numeric strings are accepted. Values `<= 0` insert at the beginning; values greater than `len(steps) + 1` append at the end. The echo returns the normalized `index`.

## Canonical field examples

Copy-paste-able reference shapes for fields that commonly cause validation
failures. The fenced JSON blocks show the canonical shape and the `When to
use` annotation above each fence names the trigger.

### `summary.intent` + `summary.intent_verb`

When to use: you want a one-line user-facing outcome plus a closed verb that drives the cross-section `intent_verb` -> `scope_item.category` compatibility check.

```json
{
  "intent": "Improve foo() index handling so the off-by-one regression cannot recur.",
  "intent_verb": "improve"
}
```

### `ScopeCategory` closed-set usage

When to use: you are filling the `summary.scope_items[*].category` field and need a quick reminder of the 15 valid values.

```json
{
  "scope_items": [
    {"text": "Refactor foo() index handling", "category": "refactor"},
    {"text": "Modify src/foo.py to clamp the index", "category": "file_change"},
    {"text": "Add a regression test for foo()", "category": "test"}
  ]
}
```

### `StepType` reference usage

When to use: you are drafting the `steps` array and want a copy-paste template that exercises one step of each kind so the executor can locate the contract requirements.

```json
{
  "steps": [
    {
      "number": 1,
      "title": "Add a regression test",
      "content": "Write a unit test that exposes the off-by-one.",
      "step_type": "file_change",
      "targets": [{"path": "tests/test_foo.py", "action": "modify"}]
    },
    {
      "number": 2,
      "title": "Run the regression test",
      "content": "Run the unit test that proves the off-by-one stays fixed.",
      "step_type": "verify",
      "verify_command": "pytest tests/test_foo.py -q"
    },
    {
      "number": 3,
      "title": "Run the executor action",
      "content": "Trigger a non-mutating command via ralph.",
      "step_type": "action"
    },
    {
      "number": 4,
      "title": "Investigate the regression",
      "content": "Read git log to understand the original change.",
      "step_type": "research"
    }
  ]
}
```

### `EvidenceRef` shape

When to use: you are filling the `steps[*].expected_evidence` field and need the canonical `EvidenceRef` shape (the `kind` discriminator in `{file, command_output, test_name}`). The validator also accepts a bare string as a compatibility shorthand for `{"kind":"file","ref":"that string"}`; examples should still prefer the object shape because it is more explicit.

```json
{
  "expected_evidence": [
    {"kind": "file", "ref": "src/foo.py"},
    {"kind": "test_name", "ref": "tests/test_foo.py::test_clamp"}
  ]
}
```

## ScopeCategory reference

The `summary.scope_items[*].category` field is a closed enum (15 values). The three trailing values (`file_change`, `prompt`, `other`) are legacy aliases kept for backward compatibility with test fixtures and existing plan examples.

| Value           | When to use                                                  |
|-----------------|--------------------------------------------------------------|
| `bugfix`        | Fixing a defect, regression, or incorrect behavior.          |
| `feature`       | Adding a new user-facing feature.                            |
| `refactor`      | Restructuring existing code without changing behavior.       |
| `test`          | Adding or improving tests only.                              |
| `docs`          | Documentation-only changes.                                  |
| `infra`         | CI/CD, build, deployment, or repository infrastructure.      |
| `migration`     | Data, schema, or large-scale code migration.                 |
| `security`      | Security hardening or CVE remediation.                        |
| `performance`   | Performance or scalability work.                             |
| `cleanup`       | Dead-code, lint, or housekeeping cleanup.                    |
| `research`      | Investigation or spike with no committed change.             |
| `unknown`       | Use only when no other value fits.                            |
| `file_change`   | Legacy alias for `file_change`-class scope items.            |
| `prompt`        | Legacy alias for prompt-template changes.                    |
| `other`         | Legacy catch-all for categories that do not map above.       |

## CoverageArea reference

The `summary.coverage_areas` field is a closed enum (10 values) used to
tag the high-level kind of change the plan covers. The field is optional
and the empty default is accepted. The values are independent of
`ScopeCategory` (which classifies per-scope-item work) and independent of
`step_type` (which classifies per-step action).

| Value           | When to use                                                  |
|-----------------|--------------------------------------------------------------|
| `bugfix`        | Defect, regression, or incorrect behavior remediation.       |
| `feature`       | New user-facing feature work.                                |
| `refactor`      | Code restructuring without observable behavior change.       |
| `test`          | Test-only changes (new tests, refactored tests, no prod code).|
| `docs`          | Documentation-only changes.                                  |
| `infra`         | CI/CD, build, deployment, or repository infrastructure.      |
| `security`      | Security hardening or CVE remediation.                       |
| `performance`   | Performance or scalability work.                             |
| `migration`     | Data, schema, or large-scale code migration.                 |
| `release`       | Release-process changes (changelog, version bump, tagging).  |

## Planning for any coding project

The plan artifact is intentionally universal: the same shape works
for any coding project, not just Ralph Workflow internals. Pick a
profile based on engineering risk, not on the project domain. CLI
tools, libraries, refactors, migrations, infra changes, security
hardening, performance work, and multi-stack projects with one
codepath change all use the same `PlanArtifact` shape.

| Profile     | Bias-fills                                              | Pick this when...                                      |
|-------------|---------------------------------------------------------|--------------------------------------------------------|
| `balanced`  | `dependency_injection`, `testability`, `refactor_strategy`. | A single-file feature or a one-off fix where the plan still names concrete files, risks, and verification. |
| `strict`    | All seven design sub-sections, plus the typed `PlanConstraints` defaults. | A multi-file refactor, security/performance-sensitive change, or any plan that must not regress. |

Use `strict` whenever the executor would otherwise have to infer
architecture, testability, acceptance criteria, or drift detection.

### Project shape coverage

The artifact covers eight project shapes out of the box. The same
`PlanArtifact` shape works for every shape; only the preset depth
differs. Pick a preset based on the project shape, not the project
domain.

- **CLI tools** — single-binary, subcommand-based, or plugin systems. Use `balanced` for one-file CLI tweaks; `strict` for multi-binary CLIs with a published API contract.
- **Libraries** — single-package, multi-package, monorepo. Use `balanced` for single-package work; `strict` for any change that has a documented public API.
- **Refactors** — single-file, multi-file, cross-stack. Use `strict` for any refactor that touches > 3 `file_change` steps or that preserves a public API.
- **Migrations** — data, schema, code, dependency, platform. Use `strict` for any migration with a rollback path; `balanced` for one-off dependency bumps.
- **Infra** — CI/CD, build, deployment, repository configuration. Use `balanced` for typical CI tweaks; `strict` for production deploy changes with a runbook.
- **Security** — hardening or CVE remediation. Always use `strict` so the executor populates the full design surface and the drift detection locks the verify command.
- **Performance** — profiling, optimization, caching. Use `strict` for any change with a measurable latency budget; `balanced` for one-off cleanup.
- **Multi-stack** — one codepath change across stacks. Use `strict` when the codepath change touches > 1 language or > 1 runtime.

The profile table above maps each shape to the right planning depth.

## Common mistakes

- Do NOT use the generic `ralph_submit_artifact` path for plan artifacts — plan artifacts must use the planning tools (`ralph_submit_plan_section`, `ralph_submit_plan_sections`, and `ralph_finalize_plan`).
- Do NOT instruct the executor to invoke Ralph-managed fan-out — fan-out is dormant in this build and the bundled CLI exposes no coordination command for plan work. The executing agent dispatches its own sub-agents per the plan's `work_units` / `parallel_plan`.
- Do NOT leave `step_type` blank — set it to one of `file_change`, `action`, `research`, or `verify`.
- Do NOT use `step_type: "test"` (or `"check"` / `"run"` / any ad-hoc label) — the closed set is `file_change`, `action`, `research`, `verify`. For test-running steps use `step_type: "verify"` with `verify_command: "pytest tests/test_x.py -q"`.
- Do NOT set `summary.scope_items` to fewer than 3 entries — `_summary.py` requires `min_length=3`.
- Do NOT use an arbitrary `summary.scope_items[*].category` — it must be one of the 15 closed `ScopeCategory` values (see the ScopeCategory reference above).
- Do NOT use arbitrary strings for `drift_detection.guard_commands` — each entry must match `^[A-Za-z0-9 _./\-:=+]+$`.
- Do NOT use lowercase or short ids in `acceptance_criteria.criteria[*].id` — the regex is `^[A-Z]+-\d{2,}$` (e.g. `AC-01`, `REF-12`).
- Do NOT duplicate `acceptance_criteria.criteria[*].id` case-insensitively (e.g. `AC-01` and `ac-01`) — the wrapper rejects duplicates.
- Do NOT submit the `design` section with `mode="append"` — only `mode="replace"` is supported for object sections.
- Do NOT mix generic artifact submission with the planning tools.
- Do NOT omit `targets` on a `file_change` step — the per-step validator rejects it. Use a `read` or `reference` target action for steps that touch zero files.
- Do NOT omit both `verify_command` and `location` on a `verify` step — the per-step validator rejects it. Pick the most natural one for the step (a shell command or a test file path).
- Do NOT let `step.satisfies` reference a criterion id that is missing from `design.acceptance_criteria.criteria` — the cross-section validator rejects orphan references. Define the AC first, then link to it.
- Do NOT let `AC.satisfied_by_steps` reference a `step_type="research"` or `step_type="verify"` step — the cross-section validator rejects it. Only `file_change` and `action` steps can satisfy an AC.
- Do NOT declare BOTH `parallel_plan` and `work_units` in the same plan — the cross-section validator rejects it. Pick one.
- Do NOT set `summary.intent_verb` to a verb that does not admit one of the `scope_items[*].category` values — the cross-section validator rejects it. See the `## Cross-section invariants` section.
- Do NOT use `verification_strategy[*].method` that starts with `bash -c `, `sh -c `, or `eval ` — the shell-invocation guard rejects it. Use the executable path instead.
- If you set `design.planning_profile = "strict"` and omit `acceptance_criteria`, the preset seeds `PRESET-01` as temporary scaffolding. Replace it with task-specific acceptance criteria before finalizing an analysis-ready plan.

## Dumb-proof checklist

- Did you use `ralph_submit_plan_section` or `ralph_submit_plan_sections`, then `ralph_finalize_plan`?
- Did you include all required fields: `summary` (with at least 3 scope items; `summary.context` is optional — a string up to 8000 chars; if empty/absent, render_plan_markdown inserts the placeholder "No additional context provided."), `skills_mcp` (with at least 1 skill), `steps` (at least 1), `critical_files` (at least 1 primary file), `risks_mitigations` (at least 1), `verification_strategy` (at least 1)?
- Did every step include a non-empty `title`, `content`, and a valid `step_type`?
- Did every `targets[*].action` use one of `create`, `modify`, `delete`, `read`, or `reference`, and did every `critical_files.primary_files[*].action` use one of `create`, `modify`, or `delete`?
- Did you provide exact commands in `verification_strategy` (not vague "run tests")?
- If you included `design`, did every sub-section's shape match the schema (enums for `architecture_style`, `step_type`, `forbidden_patterns`, `required_test_layers`, `approach`, `dead_code_policy`, `sources`, `on_drift_action`; regex `^[A-Z]+-\d{2,}$` for `acceptance_criteria.criteria[*].id`)?
- If you used `step_type="file_change"`, did you list every file in `targets`?
- If you used `step_type="verify"`, did you set `verify_command` (or `location` for a test file)?
- Did you use exactly one of `file_change`, `action`, `research`, `verify` for `step_type` (never `test`, `check`, `run`, or any other label)?
- Did you set `summary.intent_verb` to one of the 9 closed values (or leave it blank), and `summary.intent` to a ≤500-char one-line outcome (or leave it blank)?
- Did you pass section `content` as the native JSON object or array for that section?
- Did you verify the plan fits within the size limits in `## Plan size limits`? (4 MB total, 500 steps max, 200 scope_items max, 500 AC max, 500 evidence per step max)
- Is the `depends_on` graph a strict DAG (no cycles)? A cycle raises `plan step depends_on cycle detected at step N` from `ralph/mcp/artifacts/plan/_validation.py`.
- Did you set `parallel_plan` XOR `work_units` (not both)? Declaring both raises `plan cannot declare both parallel_plan and work_units; pick one`.
- Does every `verification_strategy[*].method` start with an executable path (not `bash -c ...`, `sh -c ...`, or `eval ...`)? The shell-invocation guard rejects any of those prefixes.
- Does every `design.acceptance_criteria.criteria[*].satisfied_by_steps` reference a step whose `step_type` is one of `file_change` or `action`? Referencing a `research` or `verify` step raises `satisfied_by_steps cannot reference a research or verify step; step N is T for criterion ID`.
- Does every `satisfied_by_steps` integer reference a real `step.number` that exists in `steps`? An orphan integer raises `acceptance criterion ID references unknown step number N`.
- Is `skills_mcp.skills` non-empty? Empty skills raise `skills_mcp.skills must contain at least one skill name`.

## Canonical validator errors to fix

When `ralph_finalize_plan` or `ralph_validate_draft` rejects a draft, the error message comes from one of the cross-section validators in `ralph/mcp/artifacts/plan/_validation.py`. This section enumerates every literal error string the agent will see and the canonical fix.

### Cross-section validator errors (raised by `_validation.py`)

- `plan step depends_on cycle detected at step N` - fix: remove the cycle by editing one `depends_on` entry to break the loop.
- `plan cannot declare both parallel_plan and work_units; pick one` - fix: declare exactly one of `parallel_plan` or `work_units`, leave the other empty.
- `verification method must not invoke a shell interpreter directly; use the executable path` - fix: replace `bash -c "..."` with the executable name and pass args as a list.
- `satisfied_by_steps cannot reference a research or verify step; step N is T for criterion ID` - fix: only `file_change` and `action` step_types may appear in `satisfied_by_steps`; replace research/verify refs with the concrete `file_change`/`action` step that delivers the AC.
- `skills_mcp.skills must contain at least one skill name` - fix: add at least one task-relevant skill to `skills_mcp.skills`.
- `acceptance criterion ID references unknown step number N` - fix: the step number must match an existing `step.number` in `steps`.
- `step N satisfies unknown acceptance criterion ID` - fix: add the cited criterion id to `design.acceptance_criteria.criteria` first.
- `plan envelope has no valid 'content' object` / `plan payload must decode to a JSON object` - fix: submit each section through the planning tools with the documented section content shape.
- `plan draft is missing a 'sections' object` - fix: every staged draft must contain all 6 required sections.

## Module family

The plan artifact is implemented as a focused module family under
`ralph/mcp/artifacts/plan/`. Each submodule owns a single concern so a
cheaper model can locate a helper without reading 1100+ lines of code.
The package is organized as a 7-submodule reading-order family plus
per-section typed submodels (EvidenceRef, PlanConstraints, etc.) under
the package `__init__.py`:

- `_section_models` — Pydantic sub-models (`Summary`, `PlanStep`,
  `AcceptanceCriteria`, etc.) plus the canonical `PlanArtifactDict`
  type alias for normalized plan dicts.
- `_section_registry` — section-to-model maps (`PLAN_SECTION_OBJECT_MODELS`,
  `PLAN_SECTION_LIST_ITEM_MODELS`, `PLAN_SECTION_NAMES`) plus the
  `SectionMode` literal.
- `_validation` — `PlanArtifact` (the top-level validated schema),
  `normalize_plan_artifact_content`, `validate_plan_section`,
  `merge_plan_section`, `finalize_plan_draft`, and the strict/lenient
  plan-payload decoders (`parse_plan_payload_strict` /
  `parse_plan_payload_lenient`).
- `_step_edit` — `insert_plan_step`, `replace_plan_step`,
  `remove_plan_step` (with auto-reindex and AC-link remap).
- `_renderers` — `render_plan_markdown`, `write_plan_markdown`,
  `extract_plan_payload`, `extract_plan_skill_names`.
- `_draft_io` — on-disk I/O for the plan draft (`.plan_draft.json`) and
  the finalized artifact (`plan.json`).
- `_step_contract` — `StepType` StrEnum plus the per-step contract
  helpers `requires_targets` / `requires_verify_handle`.
- `_noop` — noop short-circuit (`is_noop_plan`) and
  `PlanArtifactValidationError` re-export. The noop marker is a typed
  field on `PlanArtifact` and a runtime check in
  `_validation.is_noop_plan`, both re-exported through `_noop` for
  backward compatibility. The `_noop` module is a re-export surface;
  it does NOT own the typed field helper.

Two leaf-model submodules are imported via `_section_models`:

- `_evidence_ref` — `EvidenceRef` (with `kind: file|command_output|test_name`)
  and the `ExpectedEvidence` alias. The `PlanStep.expected_evidence`
  field is typed as `list[EvidenceRef]`.
- `_plan_constraints` — `PlanConstraints` (with `must_not_break`,
  `must_keep_working`, `performance_budget`, `security_posture`).
  Submitted via `ralph_submit_plan_section` with `section="constraints"`
  in `mode="replace"`. Rendered as `## Project Constraints` in the
  agent-facing markdown.

Reading order for a new contributor:
`_section_models` → `_section_registry` → `_validation` →
`_step_edit` → `_renderers` → `_draft_io` → `_noop`.

The public `ralph.mcp.artifacts.plan` package is a thin re-export
surface (under 60 lines) that re-exports every public symbol from the
submodules above. The new public symbols added by this revision are
`EvidenceRef`, `ExpectedEvidence`, `PlanConstraints`, and the typed
`noop` field on `PlanArtifact`.

## Cross-section invariants

In addition to the per-step rules in `## Tightened step contract`,
`PlanArtifact._validate_step_ac_cross_references` enforces four
cross-section invariants. They are checked in this order:

1. **`summary.intent_verb` -> `scope_item.category` compatibility.** Skipped
   when `intent_verb` is empty. Otherwise every scope item whose
   category is NOT in the verb's allowed set is rejected. See the
   full 9-verb x 15-category mapping table below.
2. **`parallel_plan` and `work_units` mutual exclusion.** A plan that
   declares both sections is rejected.
3. **`verification_strategy[*].method` shell-invocation guard.** Methods
   that start with `bash -c `, `sh -c `, or `eval ` (with trailing
   space) are rejected.
4. **`design.acceptance_criteria` `satisfied_by_steps` cannot reference
   a `research` or `verify` step.** Only `file_change` and `action`
   steps can satisfy an AC.

### Closed `intent_verb` -> `ScopeCategory` mapping

| intent_verb | allowed ScopeCategory values |
| --- | --- |
| `fix` | bugfix, file_change, other, unknown |
| `add` | feature, infra, test, security, performance, docs, migration, refactor, cleanup, other, file_change, prompt, unknown |
| `refactor` | refactor, cleanup, file_change, other, unknown |
| `migrate` | migration, refactor, other, file_change, unknown |
| `document` | docs, other, unknown |
| `investigate` | research, other, unknown |
| `improve` | refactor, feature, performance, test, security, docs, infra, cleanup, other, file_change, prompt, unknown |
| `configure` | infra, security, other, unknown |
| `remove` | cleanup, refactor, other, file_change, unknown |

A plan with `summary.intent_verb` that has a scope item whose category
is NOT in the verb's allowed set is REJECTED at
`normalize_plan_artifact_content` time. Leave `intent_verb` empty to
skip this check.

## Plan size limits

The plan artifact is bounded so a multi-page plan fits but a runaway
loop is detected. The hard caps are defined in the `PlanSizeLimits`
frozen dataclass (single source of truth — no model hard-codes a
cap). `check_plan_size` is a PURE helper that runs BEFORE Pydantic
validation in `normalize_plan_artifact_content` and returns the FIRST
violation as a `PlanArtifactSizeError` (with `.field`, `.actual`,
`.cap` attributes populated) or `None`. The caller raises
`PlanArtifactValidationError` only when the helper returns a
non-`None` error. The 4 MB hard cap and the per-list caps keep
runaway loops detectable; any plan that hits the 4 MB hard cap is
unambiguously runaway (10x the realistic worst case).

### Hard caps (top-level)

| Cap | Value | Purpose |
| --- | --- | --- |
| `max_total_bytes` | 4_000_000 (4 MB) | Hard cap on the JSON-serialized plan payload. A 5 MB payload fails in < 100 ms before Pydantic ever touches it. |
| `max_steps` | 500 | Maximum number of steps in a single plan. |
| `max_scope_items` | 200 | Maximum number of `summary.scope_items`. |
| `max_acceptance_criteria` | 500 | Maximum number of `design.acceptance_criteria.criteria`. |
| `max_evidence_per_step` | 500 | Maximum number of `steps[*].expected_evidence`. |
| `max_risks` | 200 | Maximum number of `risks_mitigations`. |
| `max_verification_steps` | 100 | Maximum number of `verification_strategy`. |
| `max_primary_files` | 200 | Maximum number of `critical_files.primary_files`. |
| `max_reference_files` | 200 | Maximum number of `critical_files.reference_files`. |
| `max_parallel_plan_items` | 200 | Maximum number of `parallel_plan`. |
| `max_work_units` | 200 | Maximum number of `work_units`. |
| `max_constraint_list_entries` | 500 | Maximum length of `constraints.must_not_break` and `constraints.must_keep_working`. |

### String-length tiers

Three tiers cover every string field in the artifact: `short=1000`
(for titles, ids, names), `medium=8000` (for descriptions, rationale,
risk text, expected_outcome), and `long=20000` (for step content,
design notes). The tiers form a strictly non-decreasing sequence
(`short <= medium <= long`) and the per-field cap table below maps
every plan-artifact string field to one of the three tiers. A
contributor who flips the tiers or the per-field caps must update
both `PlanSizeLimits` (in `ralph/mcp/artifacts/plan/_size_limits.py`)
and this section in lockstep.

### Per-field cap table

| field | tier | new cap |
| --- | --- | --- |
| `PlanStep.title` | short | 500 |
| `PlanStep.content` | long | 20000 |
| `PlanStep.rationale` | medium | 8000 |
| `PlanStep.location` | short | 500 |
| `PlanStep.verify_command` | medium | 2000 |
| `PlanStep.targets[*]` (list) | short | 100 |
| `PlanStep.depends_on` (list) | short | 50 |
| `PlanStep.satisfies` (list) | short | 50 |
| `StepTarget.path` | short | 1000 |
| `Summary.context` | medium | 8000 |
| `Summary.intent` | short | 500 |
| `Summary.scope_items` (list) | short | 200 |
| `Summary.coverage_areas` (list) | short | 50 |
| `ScopeItem.text` | short | 1000 |
| `ScopeItem.count` | short | 200 |
| `PlanConstraints.performance_budget` | medium | 2000 |
| `PlanConstraints.security_posture` | medium | 2000 |
| `PlanConstraints.must_not_break[*]` | short | 1000 |
| `PlanConstraints.must_keep_working[*]` | short | 1000 |
| `SkillsMcp.skills` (list) | short | 100 |
| `SkillsMcp.mcps` (list) | short | 50 |
| `RiskMitigation.risk` | medium | 8000 |
| `RiskMitigation.mitigation` | medium | 8000 |
| `RiskMitigation.risks_mitigations` (list) | short | 200 |
| `CriticalPrimaryFile.path` | short | 1000 |
| `CriticalPrimaryFile.estimated_changes` | short | 500 |
| `ReferenceFile.path` | short | 1000 |
| `ReferenceFile.purpose` | medium | 2000 |
| `VerificationStep.method` | medium | 2000 |
| `VerificationStep.expected_outcome` | medium | 8000 |
| `VerificationStep.cwd` | short | 500 |
| `AcceptanceCriterion.description` | medium | 8000 |
| `AcceptanceCriterion.verification_step` | medium | 2000 |
| `AcceptanceCriterion.evidence_path` | short | 1000 |
| `AcceptanceCriterion.satisfied_by_steps` (list) | short | 50 |
| `EvidenceRef.ref` | short | 1000 |
| `EvidenceRef.note` | short | 1000 |
| `DesignSection.outcome` | short | 1000 |
| `DesignSection.notes` | long | 20000 |
| `DesignConstraints.text` | long | 10000 |
| `DesignConstraints.invariants[*]` | medium | 2000 |
| `NonGoals.items[*]` | medium | 2000 |
| `DependencyInjection.notes` | medium | 8000 |
| `DependencyInjection.preferred_patterns` (list) | short | 20 |
| `DependencyInjection.forbidden_patterns` (list) | short | 50 |
| `DriftDetection.guard_commands[*]` | short | 500 |
| `DriftDetection.expected_outputs[*]` | medium | 2000 |
| `DriftDetection.sources` (list) | short | 20 |
| `Testability.forbidden_in_tests` (list) | short | 50 |
| `Testability.required_test_layers` (list) | short | 20 |

## Planning quality guidance

A passing plan is more than schema-valid. It must satisfy planning analysis
criteria and give the executor enough information to act without re-planning.
Populate these fields for non-trivial plans:

- `steps[*].rationale` — explains WHY the step exists, not just what it does.
- `steps[*].expected_evidence` — lists the artifacts / files / test outputs that prove the step completed.
- `design.notes` — captures trade-offs and alternative designs.
- `design.outcome` — one-sentence user-facing outcome.
- `design.drift_detection.guard_commands` and `expected_outputs` — locks the executor to specific verification commands.

### Analysis-ready plan criteria

1. Every requirement from the prompt maps to a `summary.scope_items` entry,
   one or more implementation steps, and one or more verification entries.
2. Every implementation step names concrete files in `targets` or explains
   why the step is `research`, `action`, or `verify`.
3. `design.acceptance_criteria.criteria` contains observable outcomes with
   `satisfied_by_steps` pointing only to `file_change` or `action` steps.
4. `verification_strategy` uses exact commands and expected outcomes, not
   vague instructions such as "run tests".
5. `risks_mitigations` names specific failure modes and how the executor will
   prevent or detect them.
6. `skills_mcp.skills` lists every skill the executor should apply for the
   task, especially TDD, debugging, security, accessibility, or frontend skills
   when those domains are in scope.

#### Size-cap awareness

The hard 4 MB cap and the per-list caps in `## Plan size limits` are
checked BEFORE Pydantic validation by `check_plan_size`. A plan that
exceeds any cap is rejected in < 100 ms with a structured
`PlanArtifactSizeError` (with `.field`, `.actual`, `.cap` attributes
populated). The cap table in `## Plan size limits` is the single
source of truth — no model hard-codes a cap. The three string-length
tiers (`short=1000`, `medium=8000`, `long=20000`) cover every
plan-artifact string field; the per-field cap table maps each field
to one of the three tiers. The cycle detector (see
`## Cross-section invariants`) rejects cyclic `depends_on` graphs
(e.g. step 1 -> 2 -> 3 -> 1) with the stable message
`plan step depends_on cycle detected at step N`; diamond-shaped DAGs
are accepted.

## SE-opinionated design surfaces

This section is a survey of the SE-bias surfaces the plan artifact
exposes. It is split into two clearly different groups, NOT a single
count:

- **Seven design sub-sections under the `design` field** (these are
  bias-filled under `planning_profile="strict"`): `constraints`,
  `non_goals`, `dependency_injection`, `drift_detection`, `testability`,
  `refactor_strategy`, `acceptance_criteria`. One-line rationale per
  sub-section: each one encodes a single SE opinion the executor can
  enforce (e.g. `dependency_injection.required_for_testability=true`,
  `testability.must_be_black_box=true`,
  `refactor_strategy.dead_code_policy="delete-immediately"`).
- **One top-level Project Constraints section** (`PlanConstraints`:
  `must_not_break`, `must_keep_working`, `performance_budget`,
  `security_posture`) that captures cross-cutting do-not-break rules
  the executor must respect independently of any single design
  decision. One-line rationale: the design sub-sections describe HOW
  the work is shaped, but the top-level Constraints section pins down
  WHAT MUST KEEP WORKING for the plan to be considered complete.

### Profile-by-profile SE-bias defaults

The `planning_profile` values bias-fill the design sub-sections only when the
field is explicitly set. If `planning_profile` is omitted, no preset defaults
are applied; provide explicit design sub-sections or set a profile.

- **`strict`:** every typed sub-section is bias-filled with
  a SE-opinionated default. Use this for multi-file work and refactors
  that must not regress. The preset's defaults include
  `testability.must_be_black_box=true`,
  `dependency_injection.required_for_testability=true`,
  `refactor_strategy.dead_code_policy="delete-immediately"`, and temporary
  `PRESET-01` acceptance-criteria scaffolding. Replace that scaffolding with
  task-specific AC ids and descriptions before finalizing an analysis-ready
  plan.
- **`balanced`:** biases `testability`, `dependency_injection`, and
  `refactor_strategy` but leaves the executor free to choose on `drift_detection`
  and `acceptance_criteria`. Use this for single-file features and
  one-off fixes where the executor should pick the verification
  commands.

## Flexibility boundaries

The plan artifact is flexible for almost any coding project, but it
is NOT a fit for every workflow. The "Fits" list names the project
shapes the plan artifact covers; the "Does NOT fit" list names the
shapes it does not. The "How to know" paragraph names the soft
heuristic that should make a developer reach for a lighter artifact
instead.

**Fits:**

- CLI tools (single-binary, subcommand-based, or plugin systems).
- Libraries (single-package, multi-package, monorepo).
- Refactors (single-file, multi-file, cross-stack).
- Migrations (data, schema, code, dependency, platform).
- Infra changes (CI/CD, build, deployment, repo config).
- Security hardening and CVE remediation.
- Performance work (profiling, optimization, caching).
- Multi-stack projects with one codepath change.
- Single-file features and bugfixes.
- Doc-only changes.
- Test-only changes.
- Plans that want to express parallelization intent for agent-managed sub-agents via `work_units` (same-workspace) or `parallel_plan` (read-mostly chunks).

**Does NOT fit:**

- One-line shell command tweaks with no code change (use a single commit message).
- Read-only audit reports with no committed change (use a markdown report).
- Conversational-only tasks with no executor action (no artifact needed).
- Real-time interactive UIs where the executor cannot pause (no plan artifact can capture event-driven flows).
- Batch data processing with no per-item plan needed (use a script + runbook).

**How to know:** if a project has zero `file_change` steps, zero
`verify` steps, or fewer than 3 meaningful `scope_items`, the
developer should use a lighter artifact (`issues.md` for a single
defect, or a single commit message for a trivial edit) instead of
the plan artifact. The schema requires at least 3 `scope_items`.

## Step-wise submission

Use the dedicated planning MCP tools to stage sections one at a time
so each section validates independently. The exact order is:

### Step-wise quickstart

The first 3 MCP tool calls stage the highest-context sections. The remaining
calls (`critical_files`, `risks_mitigations`, `design`,
`verification_strategy`) repeat the same `ralph_submit_plan_section`
pattern with the corresponding payload shape; the final call is
`ralph_finalize_plan`.

| Call | Section        | Payload shape |
|------|----------------|---------------|
| `ralph_submit_plan_section` | `section="summary"` | Detailed `Summary` (3+ scope_items, `intent`, `intent_verb`, optional `coverage_areas`). |
| `ralph_submit_plan_section` | `section="skills_mcp"` | `{"skills": ["writing-plans"], "mcps": []}`. |
| `ralph_submit_plan_section` | `section="steps"` | List of `PlanStep` objects. |

Repeat the same pattern for `critical_files`, `risks_mitigations`,
`design`, `verification_strategy`, then call `ralph_finalize_plan`.

A single `ralph_submit_plan_section` call for the summary section
looks like this:

```json
{
  "section": "summary",
  "content": {
    "context": "Fix the foo() out-of-range index regression after reading src/foo.py and tests/test_foo.py.",
    "intent": "Clamp foo() indexes safely while preserving valid-index behavior.",
    "intent_verb": "fix",
    "scope_items": [
      {"text": "Add a focused regression test for negative and oversized foo() indexes", "category": "test"},
      {"text": "Modify src/foo.py to clamp indexes without changing the public foo() signature", "category": "file_change"},
      {"text": "Run pytest tests/test_foo.py -q and confirm the focused regression passes", "category": "test"}
    ]
  }
}
```

### Worked example: staged section submission

These are complete MCP argument objects for a valid staged submission. Each
call validates independently so a failure short-circuits the rest of the
pipeline with a structured error.

```json
{"section":"summary","mode":"replace","content":{"context":"Fix the foo() off-by-one regression and prove it with a focused unit test.","intent":"Clamp foo() index so the regression cannot recur.","intent_verb":"improve","scope_items":[{"text":"Add a regression test for the out-of-range foo() index","category":"test"},{"text":"Modify src/foo.py to clamp the index before lookup","category":"file_change"},{"text":"Run pytest tests/test_foo.py -q to prove the regression is fixed","category":"test"}]}}
```

```json
{"section":"skills_mcp","mode":"replace","content":{"skills":["test-driven-development","systematic-debugging"],"mcps":[]}}
```

```json
{"section":"steps","mode":"replace","content":[{"number":1,"title":"Add the foo() regression test","content":"Add tests/test_foo.py::test_clamp_handles_out_of_range_index before changing production code.","step_type":"file_change","targets":[{"path":"tests/test_foo.py","action":"modify"}],"satisfies":["AC-01"],"expected_evidence":[{"kind":"file","ref":"tests/test_foo.py"},{"kind":"test_name","ref":"tests/test_foo.py::test_clamp_handles_out_of_range_index"}],"depends_on":[]},{"number":2,"title":"Clamp the foo() index","content":"Update src/foo.py so the lookup index is clamped to the valid range while preserving the public foo() signature.","step_type":"file_change","targets":[{"path":"src/foo.py","action":"modify"}],"satisfies":["AC-02"],"expected_evidence":[{"kind":"file","ref":"src/foo.py"},{"kind":"test_name","ref":"tests/test_foo.py::test_clamp_handles_out_of_range_index"}],"depends_on":[1]},{"number":3,"title":"Run the focused regression test","content":"Run pytest tests/test_foo.py -q from the repository root and confirm it passes.","step_type":"verify","verify_command":"pytest tests/test_foo.py -q","expected_evidence":[{"kind":"command_output","ref":"pytest tests/test_foo.py -q"}],"depends_on":[2]}]}
```

```json
{"section":"critical_files","mode":"replace","content":{"primary_files":[{"path":"src/foo.py","action":"modify"},{"path":"tests/test_foo.py","action":"modify"}],"reference_files":[]}}
```

```json
{"section":"risks_mitigations","mode":"replace","content":[{"risk":"Clamping could hide a caller bug that should remain visible in behavior expectations.","mitigation":"Preserve the public signature and add focused assertions documenting the intended clamping behavior.","severity":"medium"}]}
```

```json
{"section":"verification_strategy","mode":"replace","content":[{"method":"pytest tests/test_foo.py -q","expected_outcome":"The focused regression test passes.","timeout_seconds":60,"cwd":"."}]}
```

```json
{"section":"design","mode":"replace","content":{"planning_profile":"strict","outcome":"foo() handles out-of-range indexes without crashing and the regression test passes.","acceptance_criteria":{"criteria":[{"id":"AC-01","description":"A focused regression test covers the out-of-range index.","satisfied_by_steps":[1]},{"id":"AC-02","description":"src/foo.py clamps the index while preserving the public signature.","satisfied_by_steps":[2]}]}}}
```

Then call `ralph_validate_draft` with `{}`. If it returns
`{"valid": true}`, call `ralph_finalize_plan` with `{}`.

The full section order (with optional `parallel_plan` and `constraints`
slots) is below.

1. `summary`
2. `skills_mcp`
3. `steps`
4. `critical_files`
5. `risks_mitigations`
6. `constraints` (NEW top-level section; the `design.constraints`
   sub-section is unrelated)
7. `design`
8. `verification_strategy`
9. `parallel_plan` (mutually exclusive with `work_units`)
10. `finalize_plan` (call `ralph_finalize_plan` once every required
    section is staged and valid)

Submit each section via `ralph_submit_plan_section` with
`mode="replace"`. The `design` section is the only one that supports
`mode="replace"` exclusively.
