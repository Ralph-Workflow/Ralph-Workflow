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

`ralph.prompts.payload_refs` resolves references to workspace files and artifacts that are embedded in prompts. When a template includes `{{ payload_ref("plan") }}`, the payload ref resolver reads the current plan artifact from `.agent/artifacts/plan.json` and injects its content inline. This keeps templates DRY and ensures agents always see the latest artifact content.

## Prompt materialisation

`ralph.prompts.materialize` is the main entry point called by the pipeline phases. Given a drain and a pipeline context, it:

1. Resolves payload refs
2. Renders the user-turn prompt template
3. Optionally renders the system prompt
4. Returns a `MaterializedPrompt` with both strings

Phase-specific materialisation functions:

| Function | Phase |
|---|---|
| `materialize_planning_prompt` | planning |
| `materialize_developer_prompt` | development, fix |
| `materialize_reviewer_prompt` | review |
| `materialize_commit_prompt` | commit |

## Prompt modules

| Module | Purpose |
|---|---|
| `ralph.prompts.template_registry` | Template discovery and loading |
| `ralph.prompts.template_engine` | Jinja2 rendering engine |
| `ralph.prompts.system_prompt` | System prompt assembly |
| `ralph.prompts.payload_refs` | Artifact and file reference injection |
| `ralph.prompts.materialize` | Top-level materialisation entry point |
| `ralph.prompts.developer` | Developer prompt helpers |
| `ralph.prompts.reviewer` | Reviewer prompt helpers |
| `ralph.prompts.commit` | Commit prompt helpers |
| `ralph.prompts.template_context` | Context object passed to templates |
| `ralph.prompts.template_variables` | Variable definitions for template rendering |
| `ralph.prompts.template_parsing` | Template source parsing utilities |
| `ralph.prompts.types` | Shared type definitions |
| `ralph.prompts.debug_dump` | Debug helper that dumps rendered prompts to disk |

## Related pages

- {doc}`agents` — agents that receive the rendered prompts
- {doc}`concepts` — phase and drain concepts
- {py:mod}`ralph.prompts` — full API reference
