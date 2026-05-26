# Prompts

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple core loop inspired by the original Ralph loop.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


> **Most operators do not need this page.** Start with [Getting Started](getting-started.md) unless you are customizing how Ralph Workflow builds prompts.

This page explains how Ralph Workflow builds the prompts it sends to agents for each phase.

## The short version

Ralph Workflow uses built-in templates to assemble prompts for planning, development, review, fix, commit, and related phases. Projects can override those templates locally when they need custom behavior.

## Skill injection

Planning and development prompts receive the shipped baseline skill bundle through `SKILLS_INLINE_CONTENT` when repo-owned skills are available.

### Planning prompts

Planning prompts use these baseline rules:

- `using-superpowers` for all planning flows
- `brainstorming` before creative or open-ended solution shaping
- `writing-plans` for multi-step implementation planning
- `dispatching-parallel-agents` or `subagent-driven-development` when the workflow is intentionally parallelized
- `using-git-worktrees` when isolated feature work or risky parallel work is required
- `verification-loop` and `coding-standards` as baseline quality controls

### Developer prompts

Developer prompts use these baseline rules:

- `using-superpowers` for all developer flows
- `executing-plans` when a written plan is being turned into execution guidance
- `test-driven-development` for feature work and bugfix work
- `systematic-debugging` for errors, regressions, and failing verification
- `verification-before-completion` before any success claim
- `requesting-code-review` before merge-ready or handoff-ready completion
- `receiving-code-review` when acting on review findings
- `finishing-a-development-branch` when implementation is complete and integration choices must be made
- `security-review`, `verification-loop`, and `coding-standards` as baseline quality controls

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
