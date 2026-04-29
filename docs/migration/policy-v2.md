# Policy v2 Migration Guide

This guide covers breaking changes introduced when Ralph moved to fully policy-driven
orchestration, removing all hardcoded workflow semantics from the runtime code.

## Breaking Changes

### 1. `recovery.terminal_recovery_route` renamed to `recovery.failed_route`

**Old (rejected):**
```toml
[recovery]
terminal_recovery_route = "phase_failed"
```

**New (required):**
```toml
[recovery]
failed_route = "phase_failed"
```

The runtime now raises an error at startup if `terminal_recovery_route` is present in
`pipeline.toml`. Rename the key to `failed_route`.

### 2. Review phases require `issues_outcome` and `clean_outcome`

Review-role phases must now declare two new fields so the reducer can route without
hardcoded string literals.

**Old (no longer sufficient):**
```toml
[phases.review]
drain = "review"
role = "review"
[phases.review.transitions]
on_success = "review_analysis"
on_loopback = "fix"
[phases.review.bypass_routes]
review_clean = "review_commit"
```

**New (required):**
```toml
[phases.review]
drain = "review"
role = "review"
clean_outcome = "review_clean"   # bypass_routes key for a clean review
issues_outcome = "has_issues"    # review_outcome label when issues are found
[phases.review.transitions]
on_success = "review_analysis"
on_loopback = "fix"
[phases.review.bypass_routes]
review_clean = "review_commit"
```

- `issues_outcome`: required for all `role='review'` phases. The value is stored as
  `review_outcome` in pipeline state when `REVIEW_ISSUES_FOUND` is emitted.
- `clean_outcome`: required when `bypass_routes` is non-empty. The value must be a key
  in `bypass_routes`; the reducer uses it to find the bypass target phase.

The runtime enforces these requirements at startup via `validate_policy_completeness`.

### 3. Analysis routing is transitions-only

The reducer no longer inspects `decisions` entries for routing during
`ANALYSIS_SUCCESS` or `ANALYSIS_LOOPBACK` events. Routing comes exclusively from
`transitions.on_success` and `transitions.on_loopback`. The `decisions` map is now
a vocabulary contract validated at startup against artifact `decision_vocabulary`.

No config change is needed for this — `decisions` entries remain valid and are still
required for analysis phases (vocabulary validation). Only the runtime routing
behavior changed.

### 4. Custom drain filtering removed from loader

`build_agents_policy_from_config` no longer applies a hardcoded list of built-in drain
names. All drains declared in `agent_drains` are included unconditionally. This is
transparent to existing configs.

## Upgrading

1. Rename `recovery.terminal_recovery_route` → `recovery.failed_route` in all
   `pipeline.toml` files (`.agent/pipeline.toml` and any custom config directories).

2. Add `issues_outcome` and `clean_outcome` (when `bypass_routes` is non-empty) to
   every `role='review'` phase in your `pipeline.toml`.

3. Run `ralph --explain-policy` to verify the updated policy loads correctly.
