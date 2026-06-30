<!--
  Review note (docs rubric §"Required review note for meaningful docs changes"):
  - What changed: rewrote the opening paragraph so the page leads with the
    canonical autopilot positioning language instead of the older "AI agent
    orchestrator built around a simple ... Ralph-loop core" lead category.
  - Why it belongs here: this page is part of the maintained Sphinx manual;
    it must agree with the README and the manual home so the product story
    is coherent across surfaces (rubric hard failure: surfaces fight each
    other).
  - What was pruned: nothing material; the page's page-specific argument is
    preserved.
  - How the route is clearer: the lead now matches the canonical autopilot
    framing used by the README and the manual home.
-->

# Artifacts

Ralph Workflow is **the autopilot for coding agents** — a free and open-source operating system for autonomous coding, an AI agent orchestrator built around a simple Ralph-loop core that becomes powerful through composition.
**Hand it a well-specified coding task, let the agents plan, build, verify, and fix, and come back to reviewable, tested work.**
The default workflow is strong enough to adopt as-is, before you customize anything.

> **New to Ralph Workflow?** See [Getting Started](getting-started.md) first — it walks you through the full pipeline before these internals make sense.

Artifacts are the structured files Ralph Workflow leaves behind so later phases — and you — can understand what happened in a run. Instead of relying on terminal output alone, each phase submits a typed payload that Ralph Workflow validates and stores.

## Artifact types

| Artifact type | Submitted by | Purpose | Required? |
|---|---|---|---|
| `plan` | planning agent | Implementation plan with steps and work units | yes |
| `development_result` | development agent | Summary of changes made (context for analysis) | policy-controlled |
| `issues` | review agent | List of issues found in the development output | yes |
| `fix_result` | fix agent | Summary of fixes applied | yes |
| `commit_message` | commit agent | Proposed commit subject and body | yes |
| `commit_cleanup` | commit cleanup agent | Cleanup actions for transient or internal files before commit | yes |
| `development_analysis_decision` | analysis agent | Go/no-go for development output | yes |
| `planning_analysis_decision` | analysis agent | Go/no-go for planning output | yes |
| `review_analysis_decision` | analysis agent | Go/no-go for review output | yes |
| `smoke_test_result` | smoke-test agent | Structured result of a smoke-test run | no |
| `product_spec` | planning/product-spec agent | Structured product spec | no |

> **Required artifacts:** When *Required?* is **yes**, the phase must submit the artifact before completion. A submitted artifact is still fully validated against its schema. `development_result` is policy-controlled: whether a phase requires it is set by `pipeline.toml` on the phase definition. Artifact contracts in `artifacts.toml` only describe the artifact itself.

See `ralph.mcp.artifacts.typed_artifacts` for Pydantic schema definitions for each type.

## Format docs

Each artifact type with a bundled format guide — including `plan` — ships with a small Markdown reference that agents can read at runtime before they submit data. The bundled source files live in `ralph/mcp/artifacts/format_docs/`, and Ralph Workflow materializes them into the workspace at `.agent/artifact-formats/` before each agent invocation.

The format doc loader is in `ralph.mcp.artifacts.format_docs`. The `FORMAT_DOC_ARTIFACT_TYPES` tuple lists all types that have bundled docs:

```
commit_message, commit_cleanup, development_result, issues, fix_result,
development_analysis_decision, planning_analysis_decision,
review_analysis_decision, smoke_test_result, product_spec, plan
```

An index doc (`artifact_formats_index.md`) is also materialized at `.agent/artifact-formats/artifact_formats_index.md` and lists every available type with a one-line description.

## MCP submission tools

Agents submit artifacts through these MCP tools. `ralph_submit_artifact`, the plan-draft
read/finalize helpers, and batch section submit live in `ralph.mcp.tools.artifact`.
The step-edit tools live in `ralph.mcp.tools.plan_draft_edit`:

| Tool | Purpose |
|---|---|
| `ralph_submit_artifact` | Submit any artifact type as a JSON string |
| `ralph_submit_plan_section` | Submit one section of a `plan` artifact incrementally |
| `ralph_submit_plan_sections` | Submit multiple plan sections atomically in one batch |
| `ralph_validate_draft` | Run the full read-only validator against the staged plan draft |
| `ralph_insert_plan_step` | Insert one numbered step into the staged plan draft |
| `ralph_replace_plan_step` | Replace one numbered step in the staged plan draft |
| `ralph_remove_plan_step` | Remove one numbered step from the staged plan draft |
| `ralph_move_plan_step` | Move one numbered step within the staged plan draft |
| `ralph_patch_step` | Patch one numbered step while preserving the other fields |
| `ralph_finalize_plan` | Validate the staged plan draft and write `plan.json` |
| `ralph_get_plan_draft` | Read the currently staged plan draft |
| `ralph_discard_plan_draft` | Delete the staged plan draft to start fresh |

The plan tools exist because plans are large and submitted section-by-section to avoid agent context window pressure. All other artifact types are submitted as single JSON blobs via `ralph_submit_artifact`.

## Proof policy

The development phase can also enforce proof requirements through `[phases.development.artifact_proof_policy]` in `pipeline.toml`.

```toml
[phases.development.artifact_proof_policy]
require_plan_proof = true
require_analysis_proof = true
```

The bundled defaults enable both checks. Omitting the block in a project-local policy inherits the bundled defaults; to disable proof enforcement, set both fields to `false` explicitly in `.agent/pipeline.toml`.

- `require_plan_proof` controls whether `plan_items_proven` must cover the plan's canonical step refs or assigned work unit ids.
- `require_analysis_proof` controls whether `analysis_items_addressed` must cover prior `how_to_fix` items when analysis feedback exists.

## Schema validation

Submitted artifacts are parsed and validated by `ralph.mcp.artifacts.typed_artifacts`. Each `artifact_type` maps to a Pydantic model. If validation fails, the MCP server returns an error that includes the validation detail plus a pointer to `.agent/artifact-formats/<type>.md` for payload-shape failures, or `.agent/artifact-formats/artifact_formats_index.md` for artifact-type selection failures. The agent is expected to read the referenced file, rebuild the payload or artifact_type, and retry the same tool. The planning artifact additionally runs through a staging layer (`ralph_submit_plan_section`, `ralph_submit_plan_sections`, the step-edit tools, `ralph_validate_draft`, then `ralph_finalize_plan`) before final validation. If `ralph_validate_draft` or `ralph_finalize_plan` fails, the repair loop is: use the staging tools to fix the draft, then rerun `ralph_validate_draft` or `ralph_finalize_plan`.

## File backend and storage

Validated artifacts are persisted by `ralph.mcp.artifacts.file_backend` to the `.agent/artifacts/` directory in the workspace root:

```
.agent/
  artifacts/
    plan.json
    development_result.json
    issues.json
    fix_result.json
    commit_message.json
    development_analysis_decision.json
    review_analysis_decision.json
```

The store layer (`ralph.mcp.artifacts.store`) wraps the file backend and provides read/write helpers used by the pipeline phases.

## Markdown handoffs

Each validated artifact is also written as a human-readable Markdown file directly under `.agent/`. These handoff files let the next agent (or a human reviewer) inspect the artifact without parsing JSON:

| Artifact | Handoff file |
|---|---|
| `plan` | `.agent/PLAN.md` |
| `development_result` | `.agent/DEVELOPMENT_RESULT.md` |
| `issues` | `.agent/ISSUES.md` |
| `fix_result` | `.agent/FIX_RESULT.md` |
| `planning_analysis_decision` | `.agent/PLANNING_ANALYSIS_DECISION.md` |
| `development_analysis_decision` | `.agent/DEVELOPMENT_ANALYSIS_DECISION.md` |
| `review_analysis_decision` | `.agent/REVIEW_ANALYSIS_DECISION.md` |

Handoff rendering is handled by `ralph.mcp.artifacts.handoffs`.

### Plan handoff precondition

`.agent/PLAN.md` is the authoritative human- and agent-readable form of the finalized plan. Every downstream prompt (planning loopback/edit, planning analysis, development, development analysis, review, and any other non-fresh-planning template) **must** have either a `plan.json` artifact or an existing `.agent/PLAN.md` before prompt materialization runs. If neither is present, `materialize_prompt_for_phase` raises `MissingPlanHandoffError`.

The only templates that are allowed to run without a plan are `planning.jinja` and `planning_fallback.jinja`, because those are the phases that *create* the plan in the first place. All other templates — including `planning_edit.jinja`, `planning_analysis.jinja`, `developer_iteration.jinja`, `development_analysis.jinja`, and `review.jinja` — require the plan to already exist.

When `plan.json` is present but `.agent/PLAN.md` is absent, the materialization layer regenerates the Markdown handoff automatically from the JSON artifact before rendering the prompt.

## Artifact history

When a phase has `artifact_history.enabled = true` in its `pipeline.toml` policy, the artifact layer archives the current canonical artifact and its Markdown handoff **before** overwriting them with the new submission. Archives are stored under `.agent/artifacts/history/<artifact_type>/` with a timestamped filename:

```
.agent/
  artifacts/
    plan.json          ← canonical latest
    history/
      plan/
        20260415T120000_plan.json
        20260415T120000_PLAN.md
        20260416T093000_plan.json
        20260416T093000_PLAN.md
        index.md       ← chronological index rebuilt after each archive
```

`index.md` lists every archived entry with its filename and a one-line excerpt, newest last, so an agent can quickly scan what plans have been tried before.

### Policy

Artifact history is a per-phase option configured in `pipeline.toml`. Any execution phase can opt in by declaring an `artifact_history` block:

```toml
[phases.planning.artifact_history]
enabled = true
clear_on_fresh_entry = true

[phases.planning_analysis.artifact_history]
enabled = true
clear_on_fresh_entry = false

# Example: development phase opting in to artifact history
[phases.development.artifact_history]
enabled = true
clear_on_fresh_entry = true
```

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Archive the current artifact before each overwrite |
| `clear_on_fresh_entry` | `true` | Wipe the history archive at the start of a new (non-loopback) phase entry |

`clear_on_fresh_entry = true` means each fresh phase entry starts with a clean history so history from a prior iteration does not leak into the next run. Set it to `false` on analysis phases (like `planning_analysis`) so the editor agent can still see history during the same iteration.

Phases that share a drain must agree on `artifact_history.enabled`; the policy loader raises a `PolicyValidationError` if they do not.

**Default pipeline behavior:** The default pipeline enables `artifact_history` on `planning` (with `clear_on_fresh_entry = true`), `planning_analysis` (with `clear_on_fresh_entry = false`), and `development` (with `clear_on_fresh_entry = true`). This means each fresh planning or development cycle starts with a clean history, while the planning editor agent retains history across analysis loopbacks within the same planning cycle.

### Prompt integration

Phases that have `artifact_history` enabled receive an `ARTIFACT_HISTORY_PATH` template variable that points to the history `index.md` when it exists, plus an `ARTIFACT_HISTORY_DIR` variable that points to the containing archive directory. These variables are empty when no history is present, and the template renders no history section in that case.

This applies to both planning prompts (`planning.jinja`, `planning_edit.jinja`, and their fallbacks) and development prompts (`developer_iteration.jinja`, `developer_iteration_continuation.jinja`, `developer_iteration_fallback.jinja`). See {doc}`prompts` for details.

### Implementation

Archival is handled by `ralph.mcp.artifacts.history`. The `archive_artifact_before_overwrite` function copies the current `.agent/artifacts/<type>.json` and `.agent/<TYPE>.md` to the history directory and rebuilds `index.md`. This runs as the first `_SubmitOp` inside the artifact submission transaction so that a submission failure triggers rollback and removes the orphaned archive files.

## Fresh-entry drain clearing

In addition to the artifact history archive, Ralph Workflow can delete the primary artifact files (JSON + Markdown handoff) for one or more drains at the start of a fresh phase entry. This is controlled by the `clear_drains_on_fresh_entry` field on a phase definition in `pipeline.toml`:

```toml
[phases.planning]
clear_drains_on_fresh_entry = ["planning", "planning_analysis", "development_analysis"]

[phases.development]
clear_drains_on_fresh_entry = ["planning_analysis", "development", "development_analysis"]

[phases.development_commit]
clear_drains_on_fresh_entry = ["development", "development_analysis"]
```

Each entry is a drain name. On genuine fresh phase entry Ralph Workflow deletes the primary artifact JSON and its Markdown handoff for each listed drain, preventing stale context from a previous cycle from leaking into the current one.

**Difference from `artifact_history.clear_on_fresh_entry`:** That field clears only the history archive (`.agent/artifacts/history/<type>/`), leaving the canonical artifact files in place. `clear_drains_on_fresh_entry` removes the primary files themselves (`.agent/artifacts/<type>.json` and `.agent/<TYPE>.md`).

**When clearing fires:** On program start, cross-phase transition (including last-commit → planning re-entry after a successful commit), and any entry where the incoming previous phase is not an analysis loopback back into this phase.

**When clearing is suppressed:** Analysis loopbacks (planning_analysis → planning), same-phase retries, and resume (checkpoint restore).

**Default pipeline behavior:** Fresh planning entry clears `planning`, `planning_analysis`, and `development_analysis` drain artifacts. Fresh development entry clears `planning_analysis`, `development`, and `development_analysis` drain artifacts. Fresh `development_commit` and `development_final_commit` entries both clear `development` and `development_analysis` drain artifacts so each commit phase sees only current-cycle evidence.

## Audit adapter

`ralph.mcp.artifacts.audit_adapter` wraps the store and records every artifact submission to the pipeline transcript so operators can trace exactly what each agent produced.

## Related pages

- {doc}`mcp-architecture` — the MCP server that exposes artifact submission tools
- {doc}`concepts` — artifact types as first-class concepts
- {py:mod}`ralph.mcp.artifacts` — full API reference
