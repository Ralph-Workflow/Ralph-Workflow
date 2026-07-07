# Policy-Driven Overhaul Migration Guide

This page is the historical migration note for the policy-driven pipeline overhaul.
!!! info "Historical migration note — not current product framing"
    This page documents the policy-driven overhaul as a historical
    migration. It is preserved so users upgrading from earlier
    releases can locate the change notes, but the page is **not**
    the current product framing. For the current operator manual,
    start with :doc:`reference` or :doc:`getting-started`.



This guide is for users upgrading from an earlier Ralph Workflow release to the current policy-driven model. It focuses on the assumptions that changed, the config updates you may need to make, and the commands to run before you trust the migrated workflow.

## What changed

Ralph Workflow's pipeline is now **fully policy-driven**. Routing, retry rules, analysis loops, commit semantics, verification gates, recovery routing, and terminal behavior all come from `pipeline.toml`.

Before this change, some workflow behavior still lived implicitly in the runtime. After the change, the runtime follows the declared policy. If a behavior is not expressed in `pipeline.toml`, Ralph Workflow does not invent it — it fails with an actionable error instead.

## Assumptions that no longer hold

### 1. Default recovery routing was `"phase_failed"` (or `"failed"`)

Earlier builds used `terminal_recovery_route = "phase_failed"` or `failed_route = "failed"`
as the recovery target. Both were pseudo-phase aliases, not real declared phases.

**After the change:** `failed_route` must point to a real phase declared in `pipeline.toml`
with `role = "terminal"` and `terminal_outcome = "failure"`. The values `"phase_failed"`,
`"exit_failure"`, and `"failed"` are all rejected at startup with a `ValueError`.

The default bundled pipeline declares:

```toml
[phases.failed_terminal]
role = "terminal"
terminal_outcome = "failure"

[recovery]
failed_route = "failed_terminal"
```

If your `pipeline.toml` referenced `"phase_failed"` or `"failed"` as the route target,
declare a terminal failure phase and point `failed_route` to it.

### 2. Loop iteration counters were implicit

Earlier versions tracked development and review iteration counts as implicit runtime
state without declaring them in policy.

**After the change:** All loop iteration counters must be declared in `pipeline.toml`
under `[loop_counters.*]`. Each counter has a `default_max` and optional `description`.
An undeclared counter referenced by a phase `loop_policy.iteration_state_field` is a
validation error at startup.

### 3. Budget counters were implicit

Commit progress tracking (how many iterations remain, whether review is still allowed)
was previously controlled by implicit runtime state and `--developer-iters` /
`--reviewer-reviews` CLI flags.

**After the change:** Budget counters are declared under `[budget_counters.*]` in
`pipeline.toml`. A counter with `tracks_budget = true` participates in the budget-exhaustion
routing logic. The `increments_counter` field in each commit-role phase's `commit_policy`
names the counter to increment.

The review-era CLI flag `--reviewer-reviews` (and its short form `-R`) has been removed.
`--developer-iters` (short form `-D`) remains fully supported. Use `--counter NAME=VALUE`
to override the initial cap of any policy-declared budget counter
(e.g. `--counter iteration=8` or `--counter reviewer_pass=0`).

### 4. Phase behavior was partly determined by phase name

Some routing decisions previously depended on the runtime recognizing specific phase
names like `development_commit` or `review_commit`.

**After the change:** Phase behavior comes entirely from the phase's `role`, `commit_policy`,
`loop_policy`, `transitions`, and `bypass_routes` fields. The runtime does not
recognize any specific phase names. Custom pipelines with non-default phase names work
the same as the built-in defaults.

## Hidden behavior removed in this iteration

The items below are the main hidden behaviors that were removed. If an older configuration depended on them, startup validation now fails with a `PolicyValidationError` instead of letting the workflow proceed with silent assumptions.

| Removed hidden behavior | Replaced by |
|-------------------------|-------------|
| `drain_to_policy_mode()` resolved drain class by substring matching on the drain name | `AgentDrainConfig.drain_class` field — must be set explicitly in `agents.toml`; substring inference was removed completely |
| Analysis loop cap read from `PipelineState.loop_caps` only | Cap resolution now uses `pipeline.loop_counters[field].default_max`, with `loop_caps` as an optional runtime override |
| `_handle_analysis_decision` required `loop_caps` pre-populated | Now resolved from policy without requiring state initialization |
| `access_mode_for_drain()` / `build_session_mcp_plan()` ignored `AgentsPolicy` | Both now accept `agents_policy` and pass it to drain classification, so custom drain names declared with `drain_class` receive the correct MCP mode |
| Phase banner (`show_phase_transition`, `show_phase_start`, `show_phase_complete`) used hardcoded canonical phase name tables | All three functions accept an optional `pipeline_policy` parameter; when provided, styles and transition descriptions are derived from the phase's declared `role` so renamed phases render correctly |
| `artifact_renderer` rendered analysis/commit artifacts with hardcoded drain names | `render_analysis_decision` uses style `"analysis"` and `render_commit_message` uses style `"commit"`; `_analysis_handoff_artifact_type` uses the naming convention `{drain}_decision` so custom drain names work automatically |
| `drain_class_for_session` swallowed `PolicyValidationError` when no substring match found | Errors for drains with an explicit `drain_class` in agents policy are now propagated immediately; only drains with no explicit class fall through to the canonical enum fallback |
| `validate_policy_completeness` did not check post-commit route coverage | New check requires all three budget states (`remaining`, `exhausted`, `no_review`) to be covered by `[[post_commit_routes]]` entries for each commit-role phase that increments a `tracks_budget=true` counter |
| `validate_policy_completeness` did not check `clean_outcome` coverage | New check requires that every review-role phase whose `clean_outcome` is set also has a matching key in `bypass_routes` |

If you are writing integration tests that exercise these code paths, update them to
pass policy-declared `drain_class` values rather than relying on substring matching of
drain names.

## How to migrate an existing `pipeline.toml`

If you have a project-local `.agent/pipeline.toml` from before the policy-driven overhaul, start here:

```bash
ralph --regenerate-config
```

This regenerates the file from the bundled defaults (backing up your existing file to
`.agent/pipeline.toml.bak`). You can then diff the files to apply your custom changes
on top of the new structure.

### Manual migration checklist

1. **Add `[loop_counters.*]` entries** for each `iteration_state_field` referenced in
   `[phases.*.loop_policy]` blocks. Example:

   ```toml
   [loop_counters.development_analysis_iteration]
   default_max = 3
   description = "Development analysis loop iteration counter"
   ```

2. **Add `[budget_counters.*]` entries** for each `increments_counter` referenced in
   `[phases.*.commit_policy]` blocks. Example:

   ```toml
   [budget_counters.iteration]
   description = "Development iteration counter"
   tracks_budget = true
   ```

3. **Check `failed_route`** in `[recovery]`. The values `"phase_failed"`, `"exit_failure"`,
   and `"failed"` are all rejected. Declare a terminal failure phase and point
   `failed_route` to it:

   ```toml
   [phases.failed_terminal]
   drain = "done"
   role = "terminal"
   terminal_outcome = "failure"
   transitions = { on_success = "failed_terminal" }

   [recovery]
   failed_route = "failed_terminal"
   ```

4. **Ensure `entry_phase` and `terminal_phase`** are declared at the top level.

5. **Run the validator** to confirm completeness:

   ```bash
   ralph --check-config
   ```

## Verifying the migrated policy

After migration, use the policy explainer to confirm the active workflow matches your intent:

```bash
ralph --explain-policy
```

The output lists all phases, their roles, loop counters, budget counters, and recovery
routing. Compare it against your expected workflow before running the pipeline.

See [Policy Explanation](policy-explanation.md) for a full walkthrough of the explain
output.

## What has not changed

- The default phase names (`planning`, `development`, `development_analysis`, etc.) remain
  as the bundled defaults in `pipeline.toml`. No changes are required for projects that
  use the default workflow without customization.
- Agent chain and drain configuration in `ralph-workflow.toml` is unchanged.
- Checkpoint/resume behavior is unchanged.
- Recovery classification and retry behavior is unchanged.

## Removed in the latest iteration

The following hardcoded behaviors were removed in the current release. They previously
silently drove workflow behavior even when users renamed phases.

### Phase-name dispatch in `_render_phase_artifact_handoff`

The pipeline runner previously dispatched artifact rendering based on literal phase
names: `"planning"`, `"development"`, `"review"`, `"fix"`, and a set containing
`"development_analysis"` and `"review_analysis"`. Renamed phases received no artifact
UI.

**After the change:** dispatch is role-based. When an analysis-role phase completes,
`render_analysis_decision` is called regardless of the phase name. When no renderer
applies, a debug log records `policy: no renderer for phase '...' (role=...); skipping
artifact handoff render`. Artifact contracts declared in `artifacts.toml` continue to
drive the primary dispatch path (unchanged).

### Hardcoded drain mapping in `_analysis_decision_artifact_type`

The MCP artifact tool previously used a hardcoded dict mapping canonical drain names to
their decision artifact types:

```python
# OLD — removed
mapping = {
    "development_analysis": "development_analysis_decision",
    "review_analysis": "review_analysis_decision",
}
```

**After the change:** the artifact type is derived as `"{drain}_decision"` for any drain
bound to a phase with `role = "analysis"` in the active `PipelinePolicy`. When no
policy is available, the fallback applies only when the drain name ends with
`_analysis` (naming convention). Custom drains work automatically when their phase
declares `role = "analysis"` in `pipeline.toml`.

### Literal phase-name style lookups in the display layer

Four display components previously hardcoded canonical phase names in their style
lookups:

- `completion_summary.py` — `_phase_style("planning")`, `_phase_style("development_commit")`,
  `_phase_style("fix")`, `_phase_style("failed")`
- `artifact_renderer.py` — style literals `"planning"` and `"development"`
- `plain_renderer.py` — `LEVELS` dict keyed by canonical phase names

**After the change:** style lookups resolve through phase role when a `PipelinePolicy`
is provided. Role names (`"execution"`, `"analysis"`, `"review"`, `"commit"`,
`"terminal"`, etc.) are the primary keys. Canonical phase names remain as a
compatibility layer for contexts without a policy object.

### What this means for configuration

Users who renamed phases previously saw no artifact UI, incorrect milestone log levels,
and wrong section styles in the completion summary for those phases. All three are now
correct for any policy-declared phase name.

No `pipeline.toml` changes are required — the new dispatch is automatic when the
phase role is correctly declared.

## Fast verification commands

After migration, use the policy validator for a fast pass/fail check:

```bash
ralph --check-policy
```

This validates the active policy and prints a structured summary (phase count, drain
count, artifact contracts, loop counters, budget counters) without running the full
pipeline. Exit 0 means the policy is valid; exit 2 means `PolicyValidationError`.

Then use the policy explainer to confirm the active workflow matches your intent:

```bash
ralph --explain-policy
```

The output lists all phases, their roles, loop counters, budget counters, and recovery
routing. Compare it against your expected workflow before running the pipeline.

See [Policy Explanation](policy-explanation.md) for a full walkthrough of the explain
output.

## Policy-driven iteration: latest changes

This section documents the specific items removed or tightened in the most recent
iterative improvement to the policy-driven model.

### Removed legacy PipelineState budget fields

The following four fields were removed from `PipelineState`:

- `total_iterations`
- `total_reviewer_passes`
- `development_budget_remaining`
- `review_budget_remaining`

All code that previously read these fields now uses the generic policy-keyed accessors
`state.get_outer_progress(counter)` and `state.get_budget_remaining(counter)`. Old
checkpoint JSON containing these field names is still loaded correctly — the migration
validator converts them to the generic `outer_progress`, `budget_remaining`, and
`budget_caps` dicts automatically at load time.

**What this means for you:** If you have custom code outside Ralph Workflow that reads
these fields from a checkpoint JSON, update it to read from the corresponding
generic dicts using the policy-declared counter name (e.g., `outer_progress["iteration"]`
for the development iteration count).

### FanOutEffect rename (FanOutDevelopmentEffect deprecated)

The internal effect class `FanOutDevelopmentEffect` was renamed to `FanOutEffect` to
make it phase-agnostic. The old name is kept as a backward-compat module-level alias
and emits a `DeprecationWarning` when accessed.

**What this means for you:** Update any in-tree imports from
`from ralph.pipeline.effects import FanOutDevelopmentEffect` to
`from ralph.pipeline.effects import FanOutEffect`. The alias works in this release but
will be removed in a future version.

### New strict validation rules

Three new checks were added to `validate_policy_completeness`:

1. **`skip_invocation` requires `on_success`:** A phase with `skip_invocation = true` must
   declare `transitions.on_success`. Without it, the routing cannot proceed after the
   phase is skipped. Fix: add `on_success = "<next-phase>"` to the phase's
   `[phases.<name>.transitions]` block.

2. **Parallelization consistency:** When a phase declares `[parallelization]`,
   `max_work_units` must be >= `max_parallel_workers`. A `max_work_units` smaller than
   `max_parallel_workers` is a configuration error — the excess workers can never be
   used, and the policy is misleading. Fix: reduce `max_parallel_workers` or increase
   `max_work_units`.

3. **Unknown `--counter` names rejected:** When `--counter NAME=VALUE` is passed,
   `NAME` must be declared in `pipeline.toml` under `[budget_counters.NAME]`. Unknown
   counter names are rejected at startup with a `PolicyValidationError` that lists the
   declared counters. Fix: either add the counter to `pipeline.toml` or correct the
   counter name in the CLI invocation.

### Rejected legacy fields in this iteration

The following fields and values are now rejected at model construction time (before
`validate_policy_completeness` even runs):

- **`recovery.failed_route = "failed"`** — the pseudo-phase alias `"failed"` is no longer
  accepted. Declare a real terminal failure phase and reference it. See migration step 3
  above.

- **`PhaseDefinition.requires_commit = true`** — this field was removed. Use
  `role = "commit"` on the phase definition instead.

- **`PhaseDefinition.embeds_analysis = true`** — this field was removed. Use
  `role = "analysis"` on the phase definition instead.

- **Drain class substring inference** — `drain_class_for_session` no longer infers a
  drain class from the drain name. Custom drains (any drain not in the canonical
  `SessionDrain` enum) must declare `drain_class` explicitly in `agents.toml`:

  ```toml
  [agent_drains.my_custom_drain]
  chain = "my_chain"
  drain_class = "development"  # required for non-canonical drain names
  ```

  The `capability_class` field is also available to decouple the drain’s workflow role
  from the MCP capability surface it receives:  
  `capability_class = "analysis"` gives a development-role drain analysis-level MCP
  permissions.

The `--check-policy` command now validates these rules as well, so you can verify your
configuration before a full pipeline run.

## Related pages

- [Configuration](configuration.md) — full `pipeline.toml` field reference
- [Policy Explanation](policy-explanation.md) — `ralph --explain-policy` walkthrough
- [Concepts](concepts.md) — phase roles, loop counters, budget counters

## Display layer migrated to role-only resolution

### Display milestone levels, banner styles, and section styles are now role-only

The display layer previously consulted hardcoded canonical phase-name tables
(`LEVELS`, `_PHASE_STYLES`, `_TRANSITION_DESCRIPTIONS`, `_style_for_role` fallbacks)
as a silent compatibility shim. This meant a user with renamed phases could
still silently fall through to canonical-name styling when the role-based
lookup did not resolve.

**After the change:**

- `plain_renderer.LEVELS` is keyed by phase ROLE only (`execution`, `analysis`,
  `review`, `commit`, `verification`, `terminal`, `fanout_join`). Milestone
  level resolution uses `snapshot.current_phase_role` (populated from policy)
  rather than the phase name string. Terminal failure / interruption are
  resolved from explicit semantic flags (`is_terminal_failure`,
  `interrupted_by_user`).
- `phase_banner._PHASE_STYLES` is keyed by role only. Without a `PipelinePolicy`,
  the function returns `theme.text.muted` (no canonical-name lookup).
- The phase-name-pair description table `_TRANSITION_DESCRIPTIONS` and
  `_MAJOR_TRANSITIONS` were removed. Banner descriptions and major-transition
  detection use only `_ROLE_PAIR_DESCRIPTIONS` and `_MAJOR_ROLE_PAIRS`.
- `completion_summary._style_for_role` and `_style_for_terminal_failure` no
  longer accept canonical-phase-name fallback arguments. When no policy phase
  matches the requested role, they return `theme.text.muted`.
- `cli/commands/run.py` `_print_dry_run` displays `policy.entry_phase` instead
  of the literal string `"planning"`.

**What this means for you:** No `pipeline.toml` changes are required. If you
were relying on the canonical-name compatibility fallback (e.g., your custom
phase happened to be named `"planning"` so it picked up the planning style),
update to use the role-derived style by setting the appropriate `role` on the
phase. Custom-named phases now produce correct, role-driven UI deterministically.

## Final iteration: removed legacy state mirrors and silent fallbacks

This section documents the last wave of policy-driven cleanup that removed every
remaining hardcoded surface inside the runtime.

### Removed `PipelineState.iteration` and `PipelineState.reviewer_pass` scalar fields

`PipelineState` previously carried two named scalar fields that mirrored the
generic `outer_progress` dict entries `'iteration'` and `'reviewer_pass'`. These
fields were dead mirrors — any code that wanted a progress counter was already
expected to call `state.get_outer_progress(counter_name)`.

**After the change:**

- `PipelineState` has no `.iteration` or `.reviewer_pass` attribute. Accessing
  them raises `AttributeError`.
- Use `state.get_outer_progress("iteration")` (or whichever counter name your
  policy declares) everywhere.
- `state.with_outer_progress(counter_name, value)` is the only write path.

**Migration:** Replace every `state.iteration` / `state.reviewer_pass` access
with `state.get_outer_progress("<counter_name>")`. The counter name comes from
your `pipeline.toml` `[budget_counters.*]` key.

### `BudgetCounterConfig.default_max` is now a required field

Previously `BudgetCounterConfig.default_max` defaulted to `None`, and the
runner silently fell back to `_DEFAULT_BUDGET_CAP = 5` when it was absent.
This hidden default violated the policy-driven contract: a user who omitted
`default_max` had a secret cap of 5 that appeared nowhere in policy.

**After the change:**

- `default_max` is a required field (`int`, `ge=0`). Omitting it raises a
  Pydantic `ValidationError` at policy-load time.
- The bundled `pipeline.toml` supplies explicit `default_max` for each counter.
- `_DEFAULT_BUDGET_CAP = 5` is deleted from `runner.py`.

**Migration:** Add `default_max = <value>` to every `[budget_counters.*]`
section in your `pipeline.toml`. For the standard counters the recommended
values are `default_max = 5` for `iteration` and `default_max = 1` for
`reviewer_pass`.

### `PipelineSnapshot` budget fields replaced with `budget_progress` map

The four legacy scalar fields on `PipelineSnapshot` —
`iteration`, `total_iterations`, `reviewer_pass`, `total_reviewer_passes` —
have been replaced with a single generic mapping:

```python
budget_progress: dict[str, BudgetProgress]
```

Each key is a policy-declared counter name. `BudgetProgress` carries:

| Field | Meaning |
|-------|---------|
| `completed` | current `outer_progress[counter]` value |
| `cap` | effective budget cap for this run |
| `description` | human-readable label from `BudgetCounterConfig.description` |
| `tracks_budget` | whether exhausting this counter terminates the pipeline |

**Migration:** Replace `snapshot.iteration` with
`snapshot.budget_progress["iteration"].completed` (and similarly for other
fields). Iterate `snapshot.budget_progress.values()` when you need to render
all tracked counters generically.

### Removed dead `AnalysisDecision` StrEnum from `ralph.config.enums`

`ralph.config.enums.AnalysisDecision` had zero callers and was removed.
The `AnalysisDecision` BaseModel in `ralph.mcp.artifacts.typed_artifacts` is
unaffected and continues to validate analysis artifact JSON.

**Migration:** If you imported `from ralph.config.enums import AnalysisDecision`,
switch to `from ralph.mcp.artifacts.typed_artifacts import AnalysisDecision`.

### `ralph/phases/review.py` uses `effect.phase` for failure events

The review-role handler previously hardcoded the literal string `'review'` in
`PhaseFailureEvent.phase` and `_write_retry_hint`. This meant a custom phase
that used the review role but had a different name (e.g. `'audit'`) would emit
events and write retry-hint files with the wrong phase name.

**After the change:**

- `PhaseFailureEvent(phase=effect.phase, ...)` — uses the runtime phase name
  carried on the `InvokeAgentEffect`.
- `_write_retry_hint(ctx, effect.phase, detail)` — retry-hint file path matches
  the active phase name.

**Migration:** No `pipeline.toml` changes required. Custom review-role phases
now automatically get correctly named failure events and retry-hint files.

### Config template keys removed

The commented-out keys `max_development_analysis_iterations` and
`max_review_analysis_iterations` have been removed from the bundled config
templates (`ralph-workflow.toml` and `ralph-workflow-local.toml`). They were
already non-functional (the fields were removed from the config model in a
prior release); the comments are now replaced with a pointer to the canonical
location:

```toml
# Loop iteration caps live in pipeline.toml [loop_counters.*] (see ralph --explain-policy).
# Override budget caps with: ralph --counter <name>=<value> (e.g. --counter iteration=8).
```

**Migration:** Set `default_max` in `pipeline.toml [budget_counters.*]` and
use `--counter <name>=<value>` on the CLI to override for a single run.
