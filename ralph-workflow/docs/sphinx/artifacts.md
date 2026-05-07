# Artifacts

> **New to Ralph Workflow?** See [Getting Started](getting-started.md) first — it walks you through the full pipeline before these internals make sense.

Structured JSON payloads submitted by agents through the MCP layer. Every phase that produces output does so by submitting a typed artifact rather than writing to stdout.

## Artifact types

| Artifact type | Submitted by | Purpose | Required? |
|---|---|---|---|
| `plan` | planning agent | Implementation plan with steps and work units | yes |
| `development_result` | development agent | Summary of changes made (context for analysis) | **no** |
| `issues` | review agent | List of issues found in the development output | yes |
| `fix_result` | fix agent | Summary of fixes applied | yes |
| `commit_message` | commit agent | Proposed commit subject and body | yes |
| `development_analysis_decision` | analysis agent | Go/no-go for development output | yes |
| `review_analysis_decision` | analysis agent | Go/no-go for review output | yes |

> **Optional artifacts:** When *Required?* is **no**, phase success does not depend on the artifact
> being present and no explicit `declare_complete` call is required. The development agent *may*
> submit `development_result` to give the analysis agent richer context, but a clean exit (exit
> code 0) alone is sufficient for terminal success. A submitted optional artifact is still fully
> validated against its schema. The `artifact_required` flag in `pipeline.toml` controls this
> behaviour on the phase definition; artifact contracts in `artifacts.toml` only describe the
> artifact itself.

See `ralph.mcp.artifacts.typed_artifacts` for Pydantic schema definitions for each type.

## Format docs

Each non-plan artifact type ships with a bundled Markdown reference document that agents can read at runtime to understand the expected payload shape. These format docs live in `ralph/mcp/artifacts/format_docs/` and are materialized into the workspace at `.agent/artifact-formats/` before each agent invocation.

The format doc loader is in `ralph.mcp.artifacts.format_docs`. The `FORMAT_DOC_ARTIFACT_TYPES` tuple lists all types that have bundled docs:

```
commit_message, development_result, issues, fix_result,
development_analysis_decision, review_analysis_decision
```

An index doc (`artifact_formats_index.md`) is also materialized at `.agent/artifact-formats/artifact_formats_index.md` and lists every available type with a one-line description.

## MCP submission tools

Agents submit artifacts through the following MCP tools, exposed by `ralph.mcp.tools.artifact`:

| Tool | Purpose |
|---|---|
| `ralph_submit_artifact` | Submit any artifact type as a JSON string |
| `ralph_submit_plan_section` | Submit one section of a `plan` artifact incrementally |
| `ralph_finalize_plan` | Validate the staged plan draft and write `plan.json` |
| `ralph_get_plan_draft` | Read the currently staged plan draft |
| `ralph_discard_plan_draft` | Delete the staged plan draft to start fresh |

The plan tools exist because plans are large and submitted section-by-section to avoid agent context window pressure. All other artifact types are submitted as single JSON blobs via `ralph_submit_artifact`.

## Schema validation

Submitted artifacts are parsed and validated by `ralph.mcp.artifacts.typed_artifacts`. Each `artifact_type` maps to a Pydantic model. If validation fails, the MCP server returns an error and the agent must retry. The planning artifact additionally runs through a staging layer (`ralph_submit_plan_section` / `ralph_finalize_plan`) before final validation.

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
| `development_analysis_decision` | `.agent/DEVELOPMENT_ANALYSIS_DECISION.md` |
| `review_analysis_decision` | `.agent/REVIEW_ANALYSIS_DECISION.md` |

Handoff rendering is handled by `ralph.mcp.artifacts.handoffs`.

### Plan handoff precondition

`.agent/PLAN.md` is the authoritative human- and agent-readable form of the finalized plan. Every downstream prompt (planning loopback/edit, planning analysis, development, development analysis, review, and any other non-fresh-planning template) **must** have either a `plan.json` artifact or an existing `.agent/PLAN.md` before prompt materialization runs. If neither is present, `materialize_prompt_for_phase` raises `MissingPlanHandoffError`.

The only templates that are allowed to run without a plan are `planning.jinja` and `planning_fallback.jinja`, because those are the phases that *create* the plan in the first place. All other templates — including `planning_edit.jinja`, `planning_analysis.jinja`, `developer_iteration.jinja`, `development_analysis.jinja`, and `review.jinja` — require the plan to already exist.

When `plan.json` is present but `.agent/PLAN.md` is absent, the materialization layer regenerates the Markdown handoff automatically from the JSON artifact before rendering the prompt.

## Audit adapter

`ralph.mcp.artifacts.audit_adapter` wraps the store and records every artifact submission to the pipeline transcript so operators can trace exactly what each agent produced.

## Related pages

- {doc}`mcp-architecture` — the MCP server that exposes artifact submission tools
- {doc}`concepts` — artifact types as first-class concepts
- {py:mod}`ralph.mcp.artifacts` — full API reference
