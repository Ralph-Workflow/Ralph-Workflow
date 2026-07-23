# Artifacts

This page documents the artifacts Ralph Workflow produces and the contract each artifact exposes.


> **New to Ralph Workflow?** See [Getting Started](getting-started.md) first — it walks you through the full pipeline before these internals make sense.

Artifacts are the structured files Ralph Workflow leaves behind so later phases — and you — can understand what happened in a run. Instead of relying on terminal output alone, each phase submits a complete markdown document that Ralph Workflow validates against a per-type spec and stores as-is. The artifact **is** the readable markdown file — there is no separate machine-readable JSON envelope.

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

Each type's markdown grammar is declared as an `MdArtifactSpec` in `ralph/mcp/artifacts/markdown/specs/` and registered in `ralph.mcp.artifacts.markdown.registry`.

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

Agents submit artifacts through the markdown artifact tools. The handlers live in
`ralph.mcp.tools.md_artifact`:

| Tool | Purpose |
|---|---|
| `ralph_submit_md_artifact` | Validate one complete markdown artifact document and persist it canonically |
| `ralph_verify_md_artifact` | Run the same validation without persisting anything; returns the same diagnostics submission would |
| `ralph_stage_md_artifact` | Append to (or replace) a persisted markdown draft for one artifact type, without gating on validity |
| `ralph_get_md_draft` | Return the staged draft and its current diagnostics (resume after interruption) |
| `ralph_discard_md_draft` | Delete the staged draft for one artifact type |
| `ralph_finalize_md_artifact` | Validate the assembled draft with the submission gate and submit it canonically |
| `ralph_edit_md_plan_step` | Edit and persist one staged plan step by stable `S-<n>` ID (`insert`, `replace`, `remove`, `move`) |

Every artifact type — including `plan` — is submitted as one markdown document via
`ralph_submit_md_artifact` (parameters: `artifact_type`, `content`).
`ralph_verify_md_artifact` takes the same parameters and lets an agent check a draft
cheaply before submitting. After a plan is staged, `ralph_edit_md_plan_step`
loads the persisted draft, applies one ID-addressed step edit, preserves stable
step IDs and references, revalidates the result, and atomically saves the
updated draft.

### Staging large documents

Large artifacts (plans in particular) can be authored incrementally instead of in one
tool call. `ralph_stage_md_artifact` accumulates markdown into a persisted draft file
(`.<artifact_type>.draft.md` in the artifact directory; `mode` is `append`, the
default, or `replace_all`). Each staging call reports the draft length, a section
outline, and the same diagnostics submission would produce — but never fails on them,
since a partial document is expected to be incomplete. The draft survives an MCP
server restart; `ralph_get_md_draft` returns it with fresh diagnostics for resumption.
`ralph_finalize_md_artifact` runs the full submission gate over the assembled draft:
on success it persists the artifact canonically and deletes the draft, on validation
failure it keeps the draft intact for repair. A draft that would exceed the type's
character cap is rejected without modifying the existing draft. Draft persistence
lives in `ralph.mcp.artifacts.md_draft_io`.

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

## Validation

Submitted markdown is parsed and validated by `parse_and_validate` in
`ralph.mcp.artifacts.markdown` against the `MdArtifactSpec` registered for the
`artifact_type`. The result is a list of line-anchored diagnostics; both
`ralph_submit_md_artifact` and `ralph_verify_md_artifact` return the same payload:

```json
{
  "artifact_type": "plan",
  "valid": false,
  "diagnostics": [
    {"line": 12, "section": "Steps", "rule_id": "SPEC006",
     "message": "section requires list items", "severity": "error"}
  ]
}
```

Each diagnostic carries the source `line`, the `section` it applies to (when known),
a stable `rule_id`, a `message`, and a `severity`:

- **`error`** — hard failure. Structural rules (`SPEC001`–`SPEC008`: character limit,
  missing/unknown frontmatter, unknown/duplicate/missing sections, item-count limits,
  duplicate IDs) and per-type document rules produce errors. Any error rejects the
  submission: nothing is persisted, and the agent is expected to fix the document and
  retry the same tool.
- **`warning`** — the document is accepted as submitted. For example, `SPEC009` reports
  a lenient-enum frontmatter value that was coerced to its default.

The repair loop for a failed submission is: read the diagnostics (each names the line,
section, and rule), fix the markdown, and re-run `ralph_verify_md_artifact` or
`ralph_submit_md_artifact`. The bundled format docs under `.agent/artifact-formats/`
describe the expected document shape for each type.

## File backend and storage

Validated artifacts are persisted by `submit_artifact_canonical`
(`ralph.mcp.artifacts.canonical_submit`) through `ralph.mcp.artifacts.file_backend`
to the `.agent/artifacts/` directory in the workspace root. The stored file is the
submitted markdown, byte for byte:

```
.agent/
  artifacts/
    plan.md
    development_result.md
    issues.md
    fix_result.md
    commit_message.md
    development_analysis_decision.md
    review_analysis_decision.md
```

## Markdown handoffs

For selected artifact types, the same markdown is also written directly under `.agent/`
at a stable, well-known path. The handoff is a byte-identical copy of the canonical
artifact, written in the same canonical submission step, so downstream agents and human
reviewers always find the latest artifact at a predictable location:

| Artifact | Handoff file |
|---|---|
| `plan` | `.agent/PLAN.md` |
| `development_result` | `.agent/DEVELOPMENT_RESULT.md` |
| `issues` | `.agent/ISSUES.md` |
| `fix_result` | `.agent/FIX_RESULT.md` |
| `planning_analysis_decision` | `.agent/PLANNING_ANALYSIS_DECISION.md` |
| `development_analysis_decision` | `.agent/DEVELOPMENT_ANALYSIS_DECISION.md` |
| `review_analysis_decision` | `.agent/REVIEW_ANALYSIS_DECISION.md` |

The handoff paths are declared in `ralph.mcp.artifacts.handoffs` (`HANDOFF_PATHS`);
`parallel_development_summary` reuses `.agent/DEVELOPMENT_RESULT.md` so the analysis
phase picks it up through the same path.

### Plan handoff precondition

`.agent/PLAN.md` is the human- and agent-readable form of the finalized plan. Every downstream prompt (planning loopback/edit, planning analysis, development, development analysis, review, and any other non-fresh-planning template) **must** have a plan handoff available before prompt materialization runs. If none is present, `materialize_prompt_for_phase` raises `MissingPlanHandoffError` naming `.agent/PLAN.md`.

The only templates that are allowed to run without a plan are `planning.jinja` and `planning_fallback.jinja`, because those are the phases that *create* the plan in the first place. All other templates — including `planning_edit.jinja`, `planning_analysis.jinja`, `developer_iteration.jinja`, `development_analysis.jinja`, and `review.jinja` — require the plan to already exist.

Because the canonical submission step writes `.agent/artifacts/plan.md` and `.agent/PLAN.md` together, a successfully submitted plan always satisfies this precondition.

## Artifact history

When a phase has `artifact_history.enabled = true` in its `pipeline.toml` policy, the artifact layer archives the current canonical artifact (and its Markdown handoff, when one exists) **before** overwriting them with the new submission. Archives are stored under `.agent/artifacts/history/<artifact_type>/` with a timestamped filename:

```
.agent/
  artifacts/
    plan.md            ← canonical latest
    history/
      plan/
        20260415T120000_plan.md
        20260416T093000_plan.md
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

This applies to both planning prompts (`planning.jinja`, `planning_edit.jinja`, and their fallbacks) and development prompts (`developer_iteration.jinja`, `developer_iteration_continuation.jinja`, `developer_iteration_fallback.jinja`). See [Getting Started](getting-started.md) for the run-spec / agent-prompt role.

### Implementation

Archival is handled by `ralph.mcp.artifacts.history`. When history is enabled and a canonical `.agent/artifacts/<type>.md` already exists, `submit_artifact_canonical` calls `snapshot_current_artifact` before overwriting it: the current artifact (and its `.agent/<TYPE>.md` handoff, when present) is copied into the history directory under a timestamped `.md` filename and `index.md` is rebuilt.

## Fresh-entry drain clearing

In addition to the artifact history archive, Ralph Workflow can delete the primary artifact files (the canonical markdown artifact plus its handoff copy) for one or more drains at the start of a fresh phase entry. This is controlled by the `clear_drains_on_fresh_entry` field on a phase definition in `pipeline.toml`:

```toml
[phases.planning]
clear_drains_on_fresh_entry = ["planning", "planning_analysis", "development_analysis"]

[phases.development]
clear_drains_on_fresh_entry = ["planning_analysis", "development", "development_analysis"]

[phases.development_commit]
clear_drains_on_fresh_entry = ["development", "development_analysis"]
```

Each entry is a drain name. On genuine fresh phase entry Ralph Workflow deletes the canonical artifact and its Markdown handoff for each listed drain, preventing stale context from a previous cycle from leaking into the current one.

**Difference from `artifact_history.clear_on_fresh_entry`:** That field clears only the history archive (`.agent/artifacts/history/<type>/`), leaving the canonical artifact files in place. `clear_drains_on_fresh_entry` removes the primary files themselves (`.agent/artifacts/<type>.md` and `.agent/<TYPE>.md`).

**When clearing fires:** On program start, cross-phase transition (including last-commit → planning re-entry after a successful commit), and any entry where the incoming previous phase is not an analysis loopback back into this phase.

**When clearing is suppressed:** Analysis loopbacks (planning_analysis → planning), same-phase retries, and resume (checkpoint restore).

**Default pipeline behavior:** Fresh planning entry clears `planning`, `planning_analysis`, and `development_analysis` drain artifacts. Fresh development entry clears `planning_analysis`, `development`, and `development_analysis` drain artifacts. Fresh `development_commit` and `development_final_commit` entries both clear `development` and `development_analysis` drain artifacts so each commit phase sees only current-cycle evidence.

## Audit adapter

`ralph.mcp.artifacts.audit_adapter` wraps the store and records every artifact submission to the pipeline transcript so operators can trace exactly what each agent produced.

## Related pages

- {doc}`mcp-architecture` — the MCP server that exposes artifact submission tools
- {doc}`concepts` — artifact types as first-class concepts
- {py:mod}`ralph.mcp.artifacts` — full API reference
