# Prompts

> **New to Ralph Workflow?** See [Getting Started](getting-started.md) first — it introduces the pipeline before these internals.

How Ralph Workflow builds the prompts that are sent to agents for each pipeline phase.

## Template registry

`ralph.prompts.template_registry` discovers and loads Jinja2 templates from the `ralph/prompts/templates/` directory tree. Templates are keyed by a `(drain, role)` pair — for example `(development, system)` resolves to the system prompt template for the development drain.

The registry supports template overrides: if a project-level template file exists at `.agent/prompts/<drain>/<role>.md.j2`, it takes precedence over the built-in template.

## Template engine

`ralph.prompts.template_engine` wraps the Jinja2 environment with Ralph Workflow-specific filters and globals. It renders a named template against a context dictionary and returns the rendered string. Error handling converts Jinja2 exceptions to `PromptRenderError` so callers get a structured failure.

## System prompt construction

`ralph.prompts.system_prompt` assembles the full system prompt for an agent invocation. It:

1. Looks up the correct system prompt template for the active drain
2. Renders it against the session context
3. Injects shared partials (capability list, workspace scope, phase-specific instructions)

Shared partials live under `ralph/prompts/templates/shared/` and are included by the main templates via Jinja2 `{% include %}`.

## Payload refs

`ralph.prompts.payload_refs` handles oversized prompt content by replacing large values with file references. When a template variable exceeds the inline size limit, `build_prompt_payload_variables` replaces the content with a path to a written file. This keeps templates DRY and ensures agents can reference large artifacts via file paths rather than inlining them.

Key functions:
- `build_prompt_payload_variables`: Returns template variables with oversized values replaced by file references
- `prompt_payload_relative_path`: Generates a normalised relative path for a payload file
- `write_payload_to_directory`: Writes content to a payload file and returns the absolute path

## Prompt materialisation

`ralph.prompts.materialize` is the main entry point for producing rendered prompts. The top-level function is `materialize_prompt_for_phase`, which:

1. Resolves payload refs for oversized content
2. Renders the appropriate template for the given phase
3. Returns the rendered prompt string

The function routes to phase-specific rendering logic internally based on the `phase` parameter (planning, development, review, fix, commit, etc.).

## Prompt modules

| Module | Purpose |
|---|---|
| `ralph.prompts.template_registry` | Template discovery and loading |
| `ralph.prompts.template_engine` | Jinja2 rendering engine |
| `ralph.prompts.system_prompt` | System prompt assembly |
| `ralph.prompts.payload_refs` | Oversized payload file reference handling |
| `ralph.prompts.materialize` | Top-level prompt materialisation entry point |
| `ralph.prompts.developer` | Developer prompt helpers |
| `ralph.prompts.reviewer` | Reviewer prompt helpers |
| `ralph.prompts.commit` | Commit prompt helpers |
| `ralph.prompts.template_context` | Context object passed to templates |
| `ralph.prompts.template_variables` | Variable definitions for template rendering |
| `ralph.prompts.template_parsing` | Template source parsing utilities |
| `ralph.prompts.types` | Shared type definitions |
| `ralph.prompts.debug_dump` | Debug helper that dumps rendered prompts to disk |

## Planning prompt variables

Planning prompts receive a set of template variables assembled by `ralph.prompts.materialize`. Key variables:

| Variable | Source | Description |
|---|---|---|
| `PLAN_MD` | `.agent/PLAN.md` | Full text of the current plan (loopback / edit paths only) |
| `ANALYSIS_FEEDBACK` | `.agent/PLANNING_ANALYSIS_DECISION.md` | Feedback from the latest planning-analysis decision (edit paths only) |
| `ARTIFACT_HISTORY_PATH` | `.agent/artifacts/history/plan/index.md` | Absolute path to the artifact history index, or empty string when no history exists |

When `ARTIFACT_HISTORY_PATH` is non-empty, planning templates render an **ARTIFACT HISTORY** section that points agents to the archive so they can review past plans and avoid repeating already-rejected approaches. When no history exists (first iteration or after `clear_on_fresh_entry` wipes it) the section is omitted entirely.

See {doc}`artifacts` for how artifact history archival and clearing works.

## Related pages

- {doc}`concepts` — phase and drain concepts
- {doc}`artifacts` — artifact history archival and policy
- {py:mod}`ralph.prompts` — full API reference
