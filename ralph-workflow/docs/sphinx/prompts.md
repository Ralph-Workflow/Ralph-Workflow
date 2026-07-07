# Prompts

This page documents the prompt templates that ralph-workflow ships and the contracts they expect from a spec.



> **Most operators do not need this page.** Start with [Getting Started](getting-started.md) unless you are customizing how Ralph Workflow builds prompts.

This page explains how Ralph Workflow builds the prompts it sends to agents for each phase.

## The short version

Ralph Workflow uses built-in templates to assemble prompts for planning, development, review, fix, commit, and related phases. Projects can override those templates locally when they need custom behavior.

## Skill injection

`ralph --init` installs Ralph Workflow's shipped skill bundle where Claude Code and OpenCode discover skills automatically (for example under `~/.claude/skills/`). Planning and development prompts include a short **SHIPPED SKILLS** section that tells agents to invoke skills through their environment's skill mechanism. Ralph Workflow does not enumerate skill names in prompts.

When `RALPH_INLINE_SKILLS_DIR` is set, prompts may inline skill content through `SKILLS_INLINE_CONTENT` instead.

### Planning prompts

Planning prompts tell the agent to use available skills before staging `skills_mcp`. Task-relevant skill names belong in the plan artifact (`skills_mcp.skills`), not in the prompt body.

### Developer prompts

Developer prompts tell the agent to invoke skills from the execution plan's **Skills and MCPs** section when present. The plan handoff is the task-specific skill record; the prompt does not duplicate it.

## Docs-aware adaptation

When `HAS_DOCS_MCP` is true, prompt materialization includes docs-aware guidance for `docs-mcp-server` only after it is configured and reachable on `http://localhost:6280/mcp` or `http://localhost:6280/sse`. If docs-mcp is unavailable, prompt templates include a concise hint that enabling it improves documentation lookup quality. Do not enable docs-aware guidance when the service is configured but unreachable.

## Template registry

`ralph.prompts.template_registry` discovers and loads Jinja2 templates from `ralph/prompts/templates/`.

Templates are keyed by `(drain, role)` pairs. For example, `(development, system)` resolves to the system prompt template for the development drain.

Project-level overrides take precedence when a matching template exists at `.agent/prompts/<drain>/<role>.md.j2`.

## Template engine

`ralph.prompts.template_engine` renders templates against a context dictionary and converts rendering failures into structured prompt errors.

## System prompt construction

`ralph.prompts.system_prompt` assembles the final system prompt for an invocation. In plain terms, it:

1. picks the right system template for the current drain
2. renders it against the current session context
3. includes shared partials such as capability lists and phase-specific instructions

## Payload refs

Large prompt inputs are not always inlined directly. `ralph.prompts.payload_refs` can replace oversized content with file references so prompts stay readable and within size limits.

Key helpers include:

- `build_prompt_payload_variables`
- `prompt_payload_relative_path`
- `write_payload_to_directory`

## Prompt materialization

`ralph.prompts.materialize` is the main entry point for producing rendered prompts.

`materialize_prompt_for_phase` resolves payload refs, renders the right template for the phase, and returns the final prompt string.

## Prompt modules

| Module | Purpose |
|---|---|
| `ralph.prompts.template_registry` | Template discovery and loading |
| `ralph.prompts.template_engine` | Jinja2 rendering engine |
| `ralph.prompts.system_prompt` | System prompt assembly |
| `ralph.prompts.payload_refs` | Oversized payload file-reference handling |
| `ralph.prompts.materialize` | Top-level prompt materialization entry point |
| `ralph.prompts.developer` | Developer prompt helpers |
| `ralph.prompts.reviewer` | Review prompt helpers |
| `ralph.prompts.commit` | Commit prompt helpers |
| `ralph.prompts.template_context` | Context object passed to templates |
| `ralph.prompts.template_variables` | Variable definitions for template rendering |
| `ralph.prompts.template_parsing` | Template source parsing utilities |
| `ralph.prompts.types` | Shared type definitions |
| `ralph.prompts.debug_dump` | Debug helper that dumps rendered prompts to disk |

## Planning and development variables

Planning and development prompts receive template variables assembled by `ralph.prompts.materialize`. Those variables can include plan handoffs, analysis feedback, and artifact-history references when the active workflow enables them.

If you are customizing prompt behavior, the most useful starting point is to inspect the built-in templates and the phase-specific materialization logic together.

See {doc}`artifacts` for how artifact history archival and clearing work.

## Related pages

- {doc}`concepts` — workflow terms used by prompt templates
- {doc}`artifacts` — artifact history and handoff contracts
- {py:mod}`ralph.prompts` — full API reference

## PROMPT.md as the run specification

The run *specification* a user authors and the agent-side *prompt
assembly* Ralph Workflow performs are two different things. This page
documents the agent-side assembly; the user-facing run spec lives in a
separate, distinct surface.

### What `PROMPT.md` is

`<workspace>/PROMPT.md` — that is, the `PROMPT.md` file at the **root
of the active workspace** — is the **run specification** the user
authors before each `ralph` run. It is the prose contract that says
what the agents should accomplish, with what acceptance criteria,
under what constraints. `ralph --init` creates this file at the
workspace root (see `ralph/cli/commands/init.py`), and the engine
resolves it through `ralph.pro_support.prompt.resolve_effective_prompt_path`,
which returns `<workspace>/PROMPT.md` by default and honours the
`PROMPT_PATH` environment variable for operators who want a
non-default location.

The run spec is what you edit between runs; everything else on this
docs page is machinery that consumes or renders the run spec. The run
spec is also what the user reads back when they come back to a
finished run — the morning-after review is about whether the run
satisfied the run spec, not whether the agents stayed inside their
prompt templates.

`PROMPT.md` is **not** the engine-owned materialised file at
`.agent/CURRENT_PROMPT.md`. The materialised file is what the
engine writes for its own consumption; the user never authors or
edits it. Operators who need to override the path go through
`PROMPT_PATH`; everyone else writes the run spec at
`<workspace>/PROMPT.md`.

### How the run spec differs from the agent-side prompts

The Jinja2 template assembly this page describes is **agent-side**:
Ralph Workflow builds the prompts it sends to each agent for each
phase. Those prompts include:

- the system prompt template for the active drain
- shared partials (capability lists, phase-specific instructions,
  skill injection hints)
- phase-specific payload materialization (plan handoffs, analysis
  feedback, artifact-history references)
- references back to the run spec — Ralph Workflow routes the agent
  back to the user-authored `PROMPT.md` for the user's intent and
  acceptance criteria

The **run spec** is the user's intent. The **agent-side prompts**
are how Ralph Workflow translates that intent into agent input.
Both surfaces exist; you only author the run spec. The templates
that produce the agent-side prompts are baked into the runtime;
they are the maintainer-contributed details this page documents.

### What to read next

- [concepts](concepts.md) — terminology the run spec uses
  (planning, development, review, fix, recovery) and the loop
  pattern they compose into.
- [first-task-guide](first-task-guide.md) — choosing a first task
  before you draft the spec.
- [first-task-prompt-templates](first-task-prompt-templates.md) —
  concrete templates you can copy into `PROMPT.md`.

### Review note

This page is intentionally scoped to the agent-side prompt
machinery. The user-facing run-spec role of `PROMPT.md` is the
workspace-root file resolved through
`ralph.pro_support.prompt.resolve_effective_prompt_path`, distinct
from the engine-owned materialised `.agent/CURRENT_PROMPT.md`; the
cross-reference above is the maintained path between the two
surfaces.

