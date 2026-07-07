# Developer Internals

This page documents Ralph Workflow internals for contributors who need to read the code that backs the policy-driven pipeline.


This section is for contributors and maintainers working on Ralph Workflow itself. These pages explain the runtime architecture and internal contracts behind the operator-facing product.

If you only need to run Ralph Workflow, start with [Operator Reference](reference.md) instead.

## What lives here

- [MCP Architecture](mcp-architecture.md) — server lifecycle, capability gates, and upstream proxying
- [Artifacts](artifacts.md) — typed handoffs and artifact storage contracts
- [Prompts](prompts.md) — prompt template loading, rendering, and payload materialization
- [Transcript and Display Reference](transcript.md) — output event structure and rendering behavior
- [Supervising API](supervising-api.md) — trackable instance model for orchestration use cases

```{toctree}
:maxdepth: 1
:hidden:

mcp-architecture
artifacts
prompts
transcript
supervising-api
```

## Configuration-loading internals

This page documents how Ralph Workflow loads and merges its
configuration at process startup. The contract is: deterministic
merge order, hard failure on bad config, and clear precedence so a
contributor can predict what a change will do.

### Layered TOML configuration

[`ralph.config.loader`](../../modules.html#ralph.config.loader) loads
`ralph-workflow.toml` through a four-layer merge (lowest to highest
priority):

1. **Embedded defaults.** Pydantic field defaults on
   `ralph.config.models.UnifiedConfig` and its sub-models. These are
   the fallbacks shipped with the package.
2. **User-global config.** `~/.config/ralph-workflow.toml`. Settings
   here apply to every project the user runs Ralph Workflow against.
3. **Project-local config.** `.agent/ralph-workflow.toml` inside the
   active workspace. The user-global file is laid down by
   `ralph --init`; the project-local file is the per-repo override
   that ships with version control.
4. **CLI flag overrides.** Applied last, just before Pydantic
   validation; CLI wins on conflict.

The merge uses [`deep_merge`](../../modules.html#ralph.config.loader.deep_merge)
which is recursive: nested tables merge key-by-key so a project-local
override of a single sub-section never clobbers the unrelated
sections above it.

### Policy defaults

[`ralph.policy.loader`](../../modules.html#ralph.policy.loader) loads
the policy tables (`agents.toml`, `pipeline.toml`, `artifacts.toml`,
`mcp.toml`) from the same `.agent/` directory the layered config uses
for `ralph-workflow.toml`, with the same fallback to bundled
defaults under
[`ralph/policy/defaults/`](../../modules.html#ralph.policy.defaults).

User-global policy overrides prefer the **branded** filenames:

- `ralph-workflow-pipeline.toml`
- `ralph-workflow-artifacts.toml`

The legacy unprefixed names (`pipeline.toml`, `artifacts.toml`)
remain accepted for backward compatibility.

All loading goes through Pydantic validation via
[`ralph.policy.validation`](../../modules.html#ralph.policy.validation).
Malformed config surfaces as a `PolicyValidationError` with
field-level detail so a contributor can point at the exact key that
needs to change.

### How defaults are declared and overridden

Defaults are declared in two places:

- **Pydantic field defaults** on `UnifiedConfig` /
  `GeneralConfig` / `AgentConfig` / the relevant sub-models in
  `ralph/config/models.py`. These are the lowest-priority defaults.
- **Bundled TOML defaults** under `ralph/policy/defaults/` (one
  per policy table). These are the lowest-priority policy defaults
  and also serve as the schema sample new users see.

To override a default for a single field:

- **Per-user, every project:** add the key to
  `~/.config/ralph-workflow.toml` (or the relevant TOML in the user
  policy directory).
- **Per-project, this repo only:** add the key to
  `.agent/ralph-workflow.toml` under version control.
- **Per-run, this invocation only:** pass the corresponding CLI flag
  (for example, `--max-same-agent-retries 5`). CLI wins on conflict.

To add a brand-new default for a new feature, set the field default
on the Pydantic model in `ralph/config/models.py` and also update
the bundled user-global template at
`ralph/policy/defaults/ralph-workflow.toml` so new users see the
documented default in their first `--init` output.

### What the loader guarantees

- **Deterministic merge order.** Two runs with the same input files
  produce byte-identical merged configs. There is no nondeterministic
  resolution step that could surprise a contributor.
- **Hard failure on bad config.** A malformed TOML file, a value
  that fails Pydantic validation, or a contract inconsistency (for
  example, a drain reference that does not exist) aborts the run
  before any side effect. The error carries enough context for the
  contributor to fix the field, not just the file path.
- **Workspace propagation.** Linked worktrees inherit defaults from
  the main worktree unless the linked worktree has its own override;
  this is resolved by
  [`ralph.workspace.scope.WorkspaceScope`](../../modules.html#ralph.workspace.scope.WorkspaceScope)
  at startup and used by both the policy loader and the layered
  config loader.
- **Same-layer isolation.** The merge does NOT cross-contaminate
  unrelated sub-sections of the config; a project-local override of
  one section leaves every other section untouched.

## Related pages

- [Developer Reference](developer-reference.md) — top-level developer docs index
- [Configuration](configuration.md) — operator-facing configuration reference
- [Policy-Driven Overhaul Migration](policy-driven-overhaul-migration.md) — migration details
- [Python API Reference](modules.rst) — autodoc for the `ralph.*` package
- [Release & Versioning](versioning.md) — release and publishing policy
