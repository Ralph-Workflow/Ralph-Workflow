# Policy v2 Migration Guide

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


This guide covers the conceptual changes and breaking changes introduced when Ralph Workflow
moved to fully policy-driven orchestration, and explains how to migrate existing configurations.

See also: [docs/migration/parallel-mode.md](parallel-mode.md) for parallel execution migration.

---

## What changed conceptually

Before this overhaul, Ralph Workflow had a real policy layer for phase declarations,
transitions, drain bindings, and artifact contracts — but the runtime still privately owned several workflow-level behaviors:

- The reducer recognized specific decision-key literals (`completed`, `request_changes`,
  `review_clean`) and used them for routing, independent of what policy declared.
- Review routing depended on a hardcoded bypass key (`review_clean`) and a hardcoded
  outcome label (`has_issues`).
- The loader filtered agent drains through a hardcoded built-in set, silently dropping
  custom drain names.
- Legacy config fields (`terminal_recovery_route`) were silently migrated at load time,
  masking configuration errors.

After this overhaul, Ralph Workflow is a **policy-defined orchestration framework**:

- The workflow shape, routing decisions, retry budgets, analysis loops, commit semantics,
  recovery paths, and terminal outcomes are all declared in TOML policy files.
- The runtime validates that policy is semantically complete and rejects incomplete
  configurations with actionable errors — it does not silently fall back to hidden built-in
  semantics.
- The reducer is decision-key-agnostic: all routing dispatches through `resolve_next_phase()`
  and policy fields (`transitions`, `bypass_routes`, `post_commit_routes`, `failed_route`).
  No routing decision in the reducer depends on recognizing a specific string literal.
- Every routing decision a user would reasonably expect to control is now represented in
  policy.

The four canonical policy files are:

| File | What it declares |
|------|------------------|
| `.agent/pipeline.toml` | Phase graph, roles, transitions, loops, commit semantics, recovery, parallel |
| `.agent/ralph-workflow.toml` | Agent chains and drain-to-chain bindings |
| `.agent/artifacts.toml` | Artifact contracts and decision vocabularies per drain |
| `.agent/mcp.toml` | MCP servers, web search, and tool access |

---

## Removed legacy fields

### `recovery.terminal_recovery_route` renamed to `recovery.failed_route`

**Old (now rejected at startup):**
```toml
[recovery]
terminal_recovery_route = "phase_failed"
```

**New (required):**
```toml
[recovery]
failed_route = "phase_failed"
```

The runtime raises `PolicyValidationError` at startup if `terminal_recovery_route` is present
in `pipeline.toml`. The error message names the replacement field and points to this document.

### Hardcoded decision-key routing removed from the reducer

The reducer previously recognized the literal decision keys `completed` and `request_changes`
for analysis routing, and `review_clean` for review bypass routing. These lookups are removed.

**What this means for configs:** No change is required. Existing `decisions` entries remain
valid and are still required for analysis phases (vocabulary validation by `artifacts.toml`).
The routing behavior change is internal: the reducer now routes exclusively through
`transitions.on_success` and `transitions.on_loopback`, which for the default configuration
point to the same targets as the old decision-key lookups.

### Hardcoded built-in drain filter removed from loader

`build_agents_policy_from_config` no longer applies a hardcoded allowlist of built-in drain
names. All drains declared in `agent_drains` are now included unconditionally.

**What this means for configs:** Transparent to existing configs. Custom drain names that were
previously silently dropped will now flow through to `AgentsPolicy`. The existing
`PolicyBundle.all_pipeline_drains_are_bound` validator still enforces consistency between
pipeline drains and agent drains.

---

## New required policy fields

### `issues_outcome` on `role='review'` phases

All phases with `role = 'review'` must declare `issues_outcome`. This is the label stored
as `review_outcome` in pipeline state when the reviewer finds issues.

```toml
[phases.review]
role = "review"
drain = "review"
issues_outcome = "has_issues"  # required for role='review'
[phases.review.transitions]
on_success = "review_analysis"
on_loopback = "fix"
```

The default value (`has_issues`) is back-compatible with existing configs, but the field must
be explicitly declared. `validate_policy_completeness` raises `PolicyValidationError` if a
`role='review'` phase omits it.

### `clean_outcome` on `role='review'` phases with `bypass_routes`

If a `role='review'` phase declares `bypass_routes`, it must also declare `clean_outcome`. This
is the key in `bypass_routes` that the reducer uses to find the bypass target when the reviewer
finds no issues.

```toml
[phases.review]
role = "review"
drain = "review"
clean_outcome = "clean"    # required when bypass_routes is non-empty
issues_outcome = "has_issues"
[phases.review.transitions]
on_success = "review_analysis"
on_loopback = "fix"
[phases.review.bypass_routes]
clean = "review_commit"
```

`validate_policy_completeness` raises `PolicyValidationError` if a `role='review'` phase has
`bypass_routes` but omits `clean_outcome`.

---

## How to migrate

### Step 1: Rename `terminal_recovery_route` to `failed_route`

In every `pipeline.toml` (`.agent/pipeline.toml` and any custom policy directories):

**Before:**
```toml
[recovery]
terminal_recovery_route = "phase_failed"
max_recovery_cycles = 200
```

**After:**
```toml
[recovery]
failed_route = "phase_failed"
max_recovery_cycles = 200
```

### Step 2: Add `issues_outcome` and `clean_outcome` to review phases

**Before:**
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

**After:**
```toml
[phases.review]
drain = "review"
role = "review"
clean_outcome = "clean"
issues_outcome = "has_issues"
[phases.review.transitions]
on_success = "review_analysis"
on_loopback = "fix"
[phases.review.bypass_routes]
clean = "review_commit"
```

Key changes:
- `clean_outcome = "clean"` — the key that signals a clean review (must match a key in `bypass_routes`)
- `issues_outcome = "has_issues"` — the value set as `review_outcome` when issues are found
- `bypass_routes` key is now `clean` (not `review_clean`)

### Step 3: Verify the updated policy

```bash
ralph --explain-policy
```

Exit 0 with no warnings about migrated fields confirms the policy is clean.

```bash
ralph --check-config
```

Prints `Configuration is valid` when policy is semantically complete.

---

## How to verify your policy

After migration, confirm the configuration loads correctly:

```bash
# Inspect the active policy visually
ralph --explain-policy

# Inspect a specific policy directory
ralph --explain-policy --explain-policy-dir /path/to/.agent

# Validate policy completeness without running a pipeline
ralph --check-config
```

If `validate_policy_completeness` finds missing required fields, it raises
`PolicyValidationError` with the phase name and the specific missing field. Fix the field
and re-run until the command exits 0.

---

## How to read `--explain-policy` output

`ralph --explain-policy` renders the active policy as an ASCII workflow diagram and a
text-based explanation. The text output includes key fields for each phase:

- **For `role='review'` phases**: `clean_outcome` and `issues_outcome` are listed
  to make the review routing contract explicit.
- **Bypass routes**: Rendered as `Bypass [key] → target` showing which outcome keys
  trigger which bypass routes.
- **Decisions**: Rendered with the decision vocabulary and their target phases.

The diagram contract is defined in `ralph/policy/render.py`. Key visual elements:

| Element | Meaning |
|---------|---------|
| `=ENTRY=>` | Entry phase marker (appears above the entry phase box) |
| `+------+` box | A pipeline phase with its name and role |
| `\|` then `v` | Happy-path arrow to the next phase on the success spine |
| `+--[decision]--> target` | Decision branch whose target differs from on_success |
| `\| loop back to target` | Loopback annotation when on_loopback differs from on_success |
| `+---^` | Loopback return arrow |
| `[LOOPBACK: counter=NAME, max=N]` | Loop counter annotation on loopback |
| `[fanout: max_workers=N, max_units=M]` | Parallel fan-out annotation |
| `[loop: counter=NAME, max=N]` | Loop counter annotation on analysis phases |
| `==SUCCESS==>` | Terminal success marker (end of the diagram) |
| `==FAILURE==>` | Terminal failure marker |

Example abbreviated output:

```
=ENTRY=>
+------------------+
|    planning      |
| role=execution   |
+------------------+
    |
    v
[fanout: max_workers=8, max_units=50]
+--------------------+
|    development     |
|  role=execution    |
+--------------------+
    | loop back to development
    +---^  (returns to 'development' phase)
    |
    v
+----------------------------+
|   development_analysis     |
|     role=analysis          |
+----------------------------+
    +--[failed]--> failed
    +--[request_changes]--> development
    | loop back to development
    +---^  (returns to 'development' phase)
    |
    v
...
+------------------+
|    complete      |
| role=terminal    |
+------------------+
==SUCCESS==>
```

Reading tips:

- The main vertical flow (`|` then `v`) is the happy path.
- `loop back to X` with `+---^` lines indicate a return to an earlier phase — these are
  loopbacks, not first-pass forward progression. The phase they return to is named explicitly.
- `+--[decision]--> target` lines show decision branches that route elsewhere on the decision
  event.
- `[fanout: ...]` marks the phase where parallel workers are spawned.
- Terminal markers (`==SUCCESS==>` / `==FAILURE==>`) appear only on policy-declared terminal
  phases.

The full visual contract, including box sizing, spine ordering, and glyph rules, is documented
in `ralph/policy/render.py::render_explanation_ascii`.

---

## Known remaining work

The following items are tracked for a future cycle and are not part of the v2 migration:

- Renaming `RecoveryExplanation.terminal_recovery_route` dataclass field to `failed_route`
  in `ralph/policy/explain.py` (currently kept as a user-facing name; populated from
  `r.failed_route`).
- Checkpoint compatibility: old checkpoints storing legacy `loop_iteration` / budget field
  names are still tolerated on resume; that compatibility shim will be removed in v3.
