# Policy-Driven Overhaul Migration Guide

This guide is for users upgrading from an earlier version of Ralph Workflow to the current
policy-driven release. It explains what changed conceptually, which old assumptions no
longer hold, and how to update existing configurations.

## What changed

Ralph Workflow's pipeline was overhauled to be **completely policy-driven**. All
workflow behavior — routing, retry rules, analysis loops, commit semantics, verification
gates, recovery routing, and terminal behavior — is now declared in `pipeline.toml`
and enforced by a generic runtime without any hardcoded phase knowledge.

Before this change, some workflow behavior was silently owned by the runtime code.
Users could configure some edges but not actually reshape the full workflow. After the
change, the runtime is a generic policy interpreter: if it is not in `pipeline.toml`,
it does not happen.

## Assumptions that no longer hold

### 1. Default recovery routing was `"phase_failed"`

Earlier builds used `terminal_recovery_route = "phase_failed"` as the internal default.
This was an internal implementation artifact, not a real pseudo-phase name.

**After the change:** The default is `terminal_recovery_route = "failed"` which matches
the built-in pseudo-phase constant. The valid values are:

- `"failed"` — route terminal failures to the built-in failed state (default)
- `"exit_failure"` — exit immediately with a non-zero code
- Any declared phase name — route terminal failures to that phase

If your `pipeline.toml` or config code referenced `"phase_failed"` as a route target,
change it to `"failed"`.

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

The CLI flags `--developer-iters` and `--reviewer-reviews` still work but they now
override the `default_max` of the named counters rather than controlling implicit
state.

### 4. Phase behavior was partly determined by phase name

Some routing decisions previously depended on the runtime recognizing specific phase
names like `development_commit` or `review_commit`.

**After the change:** Phase behavior comes entirely from the phase's `role`, `commit_policy`,
`loop_policy`, `transitions`, and `bypass_routes` fields. The runtime does not
recognize any specific phase names. Custom pipelines with non-default phase names work
the same as the built-in defaults.

## Hidden behavior removed in this iteration

The following runtime behaviors were previously hardcoded and are now fully replaced by
policy declarations. Configurations that relied on the old implicit behavior will fail
`validate_policy_completeness()` at startup with a `PolicyValidationError`.

| Removed hidden behavior | Replaced by |
|-------------------------|-------------|
| `drain_to_policy_mode()` recognized only built-in drain name substrings | `AgentDrainConfig.drain_class` field — set explicitly in `ralph-workflow.toml` agent_drains entries; substring matching is the fallback, not the authority |
| Analysis loop cap read from `PipelineState.loop_caps` only | Cap resolution now falls back to `pipeline.loop_counters[field].default_max`, then `loop_policy.max_iterations`; `loop_caps` is an optional runtime override |
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

If you have a project-local `.agent/pipeline.toml` that predates the policy-driven
overhaul, run:

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

3. **Check `terminal_recovery_route`** in `[recovery]`. If it is set to `"phase_failed"`,
   change it to `"failed"`.

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
- The CLI flags `--developer-iters` and `--reviewer-reviews` still work.
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

## Verifying the migrated policy

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

## Related pages

- [Configuration](configuration.md) — full `pipeline.toml` field reference
- [Policy Explanation](policy-explanation.md) — `ralph --explain-policy` walkthrough
- [Concepts](concepts.md) — phase roles, loop counters, budget counters
