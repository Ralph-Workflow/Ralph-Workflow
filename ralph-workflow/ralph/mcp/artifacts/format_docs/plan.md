# plan artifact format

## Version

version: 0.8.9
last_updated: 2026-06-10

## New in this revision

- **Typed `EvidenceRef`** for `PlanStep.expected_evidence`. Each entry is a `{kind, ref, note?}` object with `kind` in `{file, command_output, test_name}`. A string-coercion before-validator accepts bare strings (treated as `kind='file'`) so legacy fixtures keep working.
- **Top-level `PlanConstraints`** section. Optional `must_not_break` / `must_keep_working` lists (each 1-200 chars, deduped case-insensitively) plus `performance_budget` and `security_posture` strings (1-200 chars). Submitted via `ralph_submit_plan_section` with `section="constraints"` in `mode="replace"`. Rendered as `## Project Constraints` between `## Critical Files` and `## Risks and Mitigations`.
- **Typed `VerificationStep.timeout_seconds` and `cwd`.** `timeout_seconds: int | None` with `gt=0, le=3600`; `cwd: str | None` with `max_length=200`. Both are optional; defaults to platform / workspace root.
- **Typed `noop: bool | None` field on `PlanArtifact`.** Default `None`, `exclude=True` for mypy-completeness and `model_fields` introspection. The runtime noop short-circuit (`is_noop_plan`) is unchanged: it reads the raw `Mapping` and `normalize_plan_artifact_content` returns `{'noop': True}` directly without round-tripping through the model. The typed field is for discovery only; do NOT rely on `model.model_dump()` to preserve the noop marker (it is dropped by `exclude=True`).
- **`step_type` default is `action`** (closed enum: `file_change`, `action`, `research`, `verify`). File-modifying steps must explicitly set `step_type="file_change"` and provide `targets`.
- **Four new cross-section validators** in `PlanArtifact._validate_step_ac_cross_references`:
  1. `summary.intent_verb` -> `scope_item.category` compatibility (HARD error; widened 9-verb x 15-category mapping).
  2. `parallel_plan` and `work_units` mutual exclusion (HARD error).
  3. `verification_strategy[*].method` shell-invocation guard (HARD error; rejects `bash -c `, `sh -c `, `eval ` prefixes with trailing space).
  4. `design.acceptance_criteria` `satisfied_by_steps` cannot reference a `research` or `verify` step (HARD error).

The new symbols and the renamed heading are: `EvidenceRef`, `PlanConstraints`, `noop`, `timeout_seconds`, `cwd`, `SE-opinionated design surfaces` (the renamed "Universal SE bias" subsection).

## What you are doing

You are describing the implementation plan for a coding task. The plan captures the problem context, the executor-ready steps, the critical files to touch, the risks and mitigations, and the verification strategy. A development agent will read the plan and execute it without re-planning.

## How to submit

The plan artifact supports TWO submission paths. Pick the right one for the size of the plan.

### Step-wise path (preferred for non-trivial plans)

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

### Atomic path (for genuinely short plans only)

If the plan is small enough to fit in a single JSON blob, you may call `ralph_submit_artifact` with `artifact_type` set to `"plan"` and `content` set to a JSON string of the raw plan payload (no outer wrapper).

```json
{
  "artifact_type": "plan",
  "content": "{\"summary\": {\"context\": \"What is being changed, why it matters, and what the executor should accomplish.\", \"scope_items\": [{\"text\": \"Concrete outcome 1\"}, {\"text\": \"Concrete outcome 2\"}, {\"text\": \"Concrete outcome 3\"}]}, \"skills_mcp\": {\"skills\": [\"test-driven-development\"], \"mcps\": []}, \"steps\": [{\"number\": 1, \"title\": \"Imperative step title\", \"content\": \"Detailed executor-ready instructions.\", \"step_type\": \"file_change\", \"priority\": \"high\", \"targets\": [{\"path\": \"path/to/file.py\", \"action\": \"modify\"}], \"depends_on\": []}], \"critical_files\": {\"primary_files\": [{\"path\": \"path/to/file.py\", \"action\": \"modify\"}], \"reference_files\": []}, \"risks_mitigations\": [{\"risk\": \"Specific failure mode\", \"mitigation\": \"How to avoid or detect it\", \"severity\": \"medium\"}], \"verification_strategy\": [{\"method\": \"pytest tests/test_x.py\", \"expected_outcome\": \"All tests pass\"}], \"design\": {\"testability\": {\"must_be_black_box\": true, \"forbidden_in_tests\": [\"time.sleep\"]}}}"
}
```

The `content` argument must be a JSON string whose decoded object is the RAW plan payload. Do NOT wrap the payload in outer `type` or `content` fields — Ralph Workflow adds artifact metadata itself.

## Required fields (inside content)

- `summary` — a summary object with:
  - `context` — a string up to 2000 chars describing the task; if empty/absent, render_plan_markdown inserts the placeholder "No additional context provided."
  - `scope_items` — an array of at least 3 scope items, each with a non-empty `text` (optional `count` and `category`)
- `skills_mcp` — a skills/MCP object with:
  - `skills` — an array of skill names; required to be non-empty UNLESS `design.planning_profile == "minimal"` (under minimal, an empty list is auto-filled with `["writing-plans"]`)
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

- `design` — a design object (see Design sub-section below). Strongly recommended for any non-trivial multi-file task or refactor. May include an optional `planning_profile` preset (`strict` | `balanced` | `minimal`) that bias-fills the seven typed sub-sections for cheap models; user values always win, and `minimal` permits `skills_mcp.skills = []` (auto-filled with `["writing-plans"]`).
- `parallel_plan` — an array of `ParallelPlanItem` objects describing safe-to-parallelize work chunks. Use only when genuinely safe.
- `work_units` — an array of work unit objects for same-workspace parallel fan-out. Each unit must declare `allowed_directories` and pass the parallel preflight.
- `summary.intent` and `summary.intent_verb` — `Summary` gains two optional, cheap-model-friendly fields. `intent` is a ≤200-char one-line user-facing outcome (defaults to empty string; dropped from model_dump(exclude_defaults=True), mirroring `summary.context`); `intent_verb` is a closed enum (`add`|`fix`|`refactor`|`migrate`|`document`|`investigate`|`improve`|`configure`|`remove`) stored as `str` with default `""` (also dropped from model_dump(exclude_defaults=True) so the round-trip is clean). A before-validator lowercases the value BEFORE the closed-enum check (so `Add` and `ADD` both pass), and explicit `""` is rejected with `ValueError("intent_verb must not be empty")` to distinguish a deliberate empty value from an omitted field. When set, the rendered plan gets a new `## Intent` heading at the top with the verb and the outcome.
- `steps[*].satisfies`, `steps[*].expected_evidence`, `steps[*].verify_command` — `PlanStep` gains three optional fields. `satisfies` is a list of acceptance-criterion ids (each matching `^[A-Z]+-\d{2,}$`); `expected_evidence` lists the artifacts / files / test outputs that prove the step completed (deduped case-insensitively, blank entries dropped, last-wins for case collision); `verify_command` is the shell command the executor should run when `step_type == "verify"`. See the new tightened contract section.

## Tightened step contract

Every plan must satisfy the following per-step and cross-section rules or it will be rejected at `normalize_plan_artifact_content` time:

- **Every `file_change` step must declare at least one `targets` entry.** A `file_change` step without `targets` is rejected with `ValueError("file_change step must declare at least one target")`. Steps that touch zero files should use a different `step_type` (e.g. `action` or `research`) or declare a `read`/`reference` target.
- **Every `verify` step must declare either `verify_command` or `location`.** A `verify` step with neither is rejected with `ValueError("verify step must declare verify_command or location")`. `verify_command` is the shell command to run (e.g. `pytest tests/test_x.py -q`); `location` is the test-file path (e.g. `tests/test_x.py`).
- **`step.satisfies` entries must reference real AC ids.** Each entry must match `^[A-Z]+-\d{2,}$` AND must appear in `design.acceptance_criteria.criteria[*].id`. Orphan references raise `PlanArtifactValidationError("step N satisfies unknown acceptance criterion 'X'")`.
- **`step.satisfies` is rejected when the plan has no `design.acceptance_criteria`.** Set the design sub-section first, then link steps to it.
- **`AC.satisfied_by_steps` entries must reference real step numbers.** Each entry must be an integer ≥1 that appears in the plan's `steps[*].number`. Orphan references raise `PlanArtifactValidationError("acceptance criterion 'X' references unknown step number N")`.
- **`AC.satisfied_by_steps` cannot reference a `research` or `verify` step.** Only `file_change` and `action` steps can satisfy an AC. References to `step_type="research"` or `step_type="verify"` raise `PlanArtifactValidationError("satisfied_by_steps cannot reference a research or verify step")`.
- **`parallel_plan` and `work_units` are mutually exclusive.** A plan that declares BOTH sections is rejected with `PlanArtifactValidationError("plan cannot declare both parallel_plan and work_units; pick one")`. Pick the section that fits the work shape — `parallel_plan` for safe-to-parallelize read-mostly chunks, `work_units` for same-workspace parallel fan-out.
- **`verification_strategy[*].method` must not invoke a shell interpreter directly.** Methods that start with `bash -c ` (note the trailing space), `sh -c `, or `eval ` are rejected with `PlanArtifactValidationError("verification method must not invoke a shell interpreter directly; use the executable path")`. Legitimate invocations like `bash ./scripts/check.sh` (prefix `bash `, not `bash -c `) are NOT blocked.

### Design sub-section

The `design` field is OPTIONAL. When present, it carries seven typed sub-models that bias the executor toward good Software Engineering practices:

- `constraints` — `DesignConstraints` with a non-empty `text` (1-2000 chars), optional `invariants` (each 1-500 chars), and optional `architecture_style` (one of `monolith`, `modular-monolith`, `microservice`, `library`, `cli`, `spa`, `mobile`, `serverless`, `embedded`, `unknown`).
- `non_goals` — `NonGoals` with a non-empty `items` list (each 1-500 chars).
- `dependency_injection` — `DependencyInjection` with `required_for_testability` (bool), optional `preferred_patterns` and `forbidden_patterns` from the schema enums, and optional `notes` (max 2000 chars).
- `drift_detection` — `DriftDetection` with optional `guard_commands` (each must use only `[A-Za-z0-9 _./\-:=+]`), `expected_outputs`, `sources` (one of `ruff`, `mypy`, `pytest`, `make`, `custom-script`, `ci`, `unknown`), and `on_drift_action` (`fail-verify`, `log-only`, `open-issue`, `ignore`).
- `testability` — `Testability` with `must_be_black_box` (bool), optional `forbidden_in_tests` and `required_test_layers` from the schema enums, optional `clock_injection_required`, and `max_unit_test_seconds` (0 < N <= 60).
- `refactor_strategy` — `RefactorStrategy` with `approach` (one of `greenfield`, `incremental`, `strangler`, `branch-by-abstraction`, `rebuild-in-parallel`, `no-refactor`), optional `preserve_public_api`, `dead_code_policy` (default `delete-immediately`), and `allow_temporary_hacks` (default `false`).
- `acceptance_criteria` — `AcceptanceCriteria` with at least 1 `AcceptanceCriterion` entry. Each criterion has `id` matching `^[A-Z]+-\d{2,}$`, `description` (1-1000 chars), and optional `verification_step` and `evidence_path`.
- `notes` — optional free-form rationale. High-quality models should populate it with rationale, alternative designs, and trade-offs; cheap models may leave it unset. Either is acceptable.

Minimal `design` example payload:

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

## Complete example (minimal valid plan — cheap model baseline)

This minimal example is the cheapest valid plan the executor can run. It uses the new `design.planning_profile="strict"` preset (so the seven typed sub-sections are bias-filled and do not need to be handcrafted), exercises one new `ScopeCategory` value (`bugfix`), one legacy value (`file_change`) for test-fixture compatibility, one new `StepType="verify"` value, and the new optional `summary.context` placeholder. It also demonstrates the new `summary.intent` / `summary.intent_verb` cheap-model shortcut, the tightened `verify` step contract (via `verify_command`), the new `design.outcome` line, and a step↔AC link via `steps[*].satisfies`. Copy it verbatim as your starting template:

```json
{
  "artifact_type": "plan",
  "content": "{\"summary\": {\"intent\": \"Clamp foo() index so the off-by-one regression cannot recur.\", \"intent_verb\": \"fix\", \"scope_items\": [{\"text\": \"Fix the off-by-one bug in foo()\", \"category\": \"bugfix\"}, {\"text\": \"Modify src/foo.py to clamp the index\", \"category\": \"file_change\"}, {\"text\": \"Add a regression test for foo()\", \"category\": \"file_change\"}]}, \"skills_mcp\": {\"skills\": [\"test-driven-development\"], \"mcps\": []}, \"steps\": [{\"number\": 1, \"title\": \"Add a regression test\", \"content\": \"Write a unit test that exposes the off-by-one.\", \"step_type\": \"verify\", \"verify_command\": \"pytest tests/test_foo.py -q\", \"targets\": [{\"path\": \"tests/test_foo.py\", \"action\": \"modify\"}], \"satisfies\": [\"AC-01\"], \"expected_evidence\": [{\"kind\": \"file\", \"ref\": \"tests/test_foo.py\"}, {\"kind\": \"test_name\", \"ref\": \"tests/test_foo.py::test_clamp\"}], \"depends_on\": []}, {\"number\": 2, \"title\": \"Fix foo()\", \"content\": \"Clamp the index in foo() and re-run the test.\", \"step_type\": \"file_change\", \"targets\": [{\"path\": \"src/foo.py\", \"action\": \"modify\"}], \"satisfies\": [\"AC-02\"], \"depends_on\": [1]}], \"critical_files\": {\"primary_files\": [{\"path\": \"src/foo.py\", \"action\": \"modify\"}]}, \"risks_mitigations\": [{\"risk\": \"Clamp could hide deeper bugs\", \"mitigation\": \"Log the original index in DEBUG\"}], \"constraints\": {\"must_not_break\": [\"public API\"]}, \"verification_strategy\": [{\"method\": \"pytest tests/test_foo.py -q\", \"expected_outcome\": \"All tests pass\", \"timeout_seconds\": 60, \"cwd\": \"ralph-workflow\"}], \"noop\": false, \"design\": {\"planning_profile\": \"strict\", \"outcome\": \"foo() never returns out-of-range and pytest tests/test_foo.py -q is green.\", \"acceptance_criteria\": {\"criteria\": [{\"id\": \"AC-01\", \"description\": \"Regression test for foo() passes\"}, {\"id\": \"AC-02\", \"description\": \"foo() clamps out-of-range index without crashing\"}]}}}"
}
```

This example is exercised by tests/test_artifact_format_docs.py and must round-trip.

## StepType reference

The `steps[*].step_type` field is a closed enum. Pick exactly one:

| Value         | When to use                                                            |
|---------------|------------------------------------------------------------------------|
| `file_change` | The step creates, modifies, or deletes one or more source files. (Note: this is NOT the default — the default is action; a file_change step must be declared explicitly and must list every file in targets.) |
| `action`      | The step runs a non-mutating executor action (a command, a tool call). |
| `research`    | The step is exploratory and may not produce a code change.             |
| `verify`      | The step is a pure-verification step (e.g. `run ruff`, `run pytest`) with no file changes. |

## Cheap-model shortcut examples

Copy-paste-able reference shapes for the four fields that cheap models most often get wrong. The fenced JSON blocks show the canonical shape and the `When to use` annotation above each fence names the trigger.

### `summary.intent` + `summary.intent_verb`

When to use: you want a one-line user-facing outcome plus a closed verb that drives the cross-section `intent_verb` -> `scope_item.category` compatibility check.

```json
{
  "intent": "Clamp foo() index so the off-by-one regression cannot recur.",
  "intent_verb": "fix"
}
```

### `ScopeCategory` closed-set usage

When to use: you are filling the `summary.scope_items[*].category` field and need a quick reminder of the 15 valid values.

```json
{
  "scope_items": [
    {"text": "Fix the off-by-one bug in foo()", "category": "bugfix"},
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
      "step_type": "verify",
      "verify_command": "pytest tests/test_foo.py -q"
    },
    {
      "number": 2,
      "title": "Modify src/foo.py",
      "content": "Clamp the index in foo() and re-run the test.",
      "step_type": "file_change",
      "targets": [{"path": "src/foo.py", "action": "modify"}]
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

When to use: you are filling the `steps[*].expected_evidence` field and need the canonical `EvidenceRef` shape (the `kind` discriminator in `{file, command_output, test_name}`).

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

## Common mistakes

- Do NOT wrap the atomic payload in `{"type":"plan","content":...}` — only the step-wise flow accepts that envelope; atomic `content` must be the raw plan payload as a JSON string.
- Do NOT use `ralph_submit_artifact` with `artifact_type="plan"` for a long plan — prefer the step-wise flow (`ralph_submit_plan_section` + `ralph_finalize_plan`) so each section validates independently.
- Do NOT leave `step_type` blank — set it to one of `file_change`, `action`, `research`, or `verify`.
- Do NOT use `step_type: "test"` (or `"check"` / `"run"` / any ad-hoc label) — the closed set is `file_change`, `action`, `research`, `verify`. For test-running steps use `step_type: "verify"` with `verify_command: "pytest tests/test_x.py -q"`.
- Do NOT set `summary.scope_items` to fewer than 3 entries — `_summary.py` requires `min_length=3`.
- Do NOT use an arbitrary `summary.scope_items[*].category` — it must be one of the 15 closed `ScopeCategory` values (see the ScopeCategory reference above).
- Do NOT use arbitrary strings for `drift_detection.guard_commands` — each entry must match `^[A-Za-z0-9 _./\-:=+]+$`.
- Do NOT use lowercase or short ids in `acceptance_criteria.criteria[*].id` — the regex is `^[A-Z]+-\d{2,}$` (e.g. `AC-01`, `REF-12`).
- Do NOT duplicate `acceptance_criteria.criteria[*].id` case-insensitively (e.g. `AC-01` and `ac-01`) — the wrapper rejects duplicates.
- Do NOT submit the `design` section with `mode="append"` — only `mode="replace"` is supported for object sections.
- Do NOT mix atomic and step-wise flows in the same session — pick one.
- Do NOT omit `targets` on a `file_change` step — the per-step validator rejects it. Use a `read` or `reference` target action for steps that touch zero files.
- Do NOT omit `verify_command` and `location` on a `verify` step — the per-step validator rejects it. Pick the most natural one for the step (a shell command or a test file path).
- Do NOT let `step.satisfies` reference a criterion id that is missing from `design.acceptance_criteria.criteria` — the cross-section validator rejects orphan references. Define the AC first, then link to it.
- Do NOT let `AC.satisfied_by_steps` reference a `step_type="research"` or `step_type="verify"` step — the cross-section validator rejects it. Only `file_change` and `action` steps can satisfy an AC.
- Do NOT declare BOTH `parallel_plan` and `work_units` in the same plan — the cross-section validator rejects it. Pick one.
- Do NOT set `summary.intent_verb` to a verb that does not admit one of the `scope_items[*].category` values — the cross-section validator rejects it. See the `## Cross-section invariants` section.
- Do NOT use `verification_strategy[*].method` that starts with `bash -c `, `sh -c `, or `eval ` — the shell-invocation guard rejects it. Use the executable path instead.
- If you set `design.planning_profile = "strict"` and also pass `acceptance_criteria.criteria[*].id` values, the preset's `PRESET-01` sentinel is added on top — strip or replace the sentinel yourself if you want clean AC ids.

## Dumb-proof checklist

- Did you set `artifact_type` to `"plan"` (atomic flow) or use `ralph_submit_plan_section` (step-wise flow)?
- Did you include all required fields: `summary` (with at least 3 scope items; `summary.context` is now OPTIONAL — a string up to 2000 chars; if empty/absent, render_plan_markdown inserts the placeholder "No additional context provided."), `skills_mcp` (with at least 1 skill UNLESS `design.planning_profile == "minimal"`, under which an empty list is auto-filled with `["writing-plans"]`), `steps` (at least 1), `critical_files` (at least 1 primary file), `risks_mitigations` (at least 1), `verification_strategy` (at least 1)?
- Did every step include a non-empty `title`, `content`, and a valid `step_type`?
- Did every `targets[*].action` and `critical_files.primary_files[*].action` use one of `create`, `modify`, or `delete`?
- Did you provide exact commands in `verification_strategy` (not vague "run tests")?
- If you included `design`, did every sub-section's shape match the schema (enums for `architecture_style`, `step_type`, `forbidden_patterns`, `required_test_layers`, `approach`, `dead_code_policy`, `sources`, `on_drift_action`; regex `^[A-Z]+-\d{2,}$` for `acceptance_criteria.criteria[*].id`)?
- If you used `step_type="file_change"`, did you list every file in `targets`?
- If you used `step_type="verify"`, did you set `verify_command` (or `location` for a test file)?
- Did you use exactly one of `file_change`, `action`, `research`, `verify` for `step_type` (never `test`, `check`, `run`, or any other label)?
- Did you set `summary.intent_verb` to one of the 9 closed values (or leave it blank), and `summary.intent` to a ≤200-char one-line outcome (or leave it blank)?
- Did you stringify the content object into a JSON string for the `content` field (atomic flow only)?

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
  field is typed as `list[EvidenceRef]`. A string-coercion
  before-validator accepts bare strings (treated as `kind='file'`).
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

## Step-wise submission

Use the dedicated planning MCP tools to stage sections one at a time
so each section validates independently. The exact order is:

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
