# Parallel Development Mode

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


## IMPORTANT: v1 is same-workspace only

Ralph parallel workers in v1 share a single checkout. There are no per-worker git branches and no post-development merge step. Isolation is enforced by `allowed_directories` path restrictions and per-worker namespaces under `.agent/workers/<unit_id>/`. v1 uses the same git checkout for all workers; per-worker git checkout isolation is not part of the v1 product surface.

This document covers the parallelization feature introduced in the Python implementation. If you are upgrading from an earlier Ralph version or migrating from the retired Rust implementation, read this first.

---

## Checkpoint Compatibility

Old checkpoints load transparently. No migration step is required.

When Ralph loads a checkpoint that was created before parallel mode existed, the missing fields (`work_units`, `worker_states`) are absent. Ralph initializes these as empty on load, so the pipeline resumes correctly in serial mode. No data is lost and no conversion is needed.

If your checkpoint predates parallel mode and you want to use parallel mode, simply run the pipeline again from your current state. The planning phase will produce a `work_units` array if your prompt decomposes the work into multiple units, and parallel mode will activate automatically.

---

## Policy Configuration

Parallelization is now configured **per phase** under `[phases.<phase>.parallelization]`.
The global `[parallel_execution]` block has been removed; a `ValidationError` is raised if it appears
in your `.agent/pipeline.toml`.

### Migration: `[parallel_execution]` → `[phases.development.parallelization]`

If your pipeline.toml contained:

```toml
# OLD — no longer accepted
[parallel_execution]
max_parallel_workers = 4
max_work_units = 25
```

Replace it with:

```toml
# NEW — per-phase scoped
[phases.development.parallelization]
mode = "same_workspace"
max_parallel_workers = 4
max_work_units = 25
```

The `mode` field is required and must be `"same_workspace"`.

### Fail-Closed Behavior

A phase without a `[phases.<phase>.parallelization]` block **fails closed** when a plan declares
2+ work units for that phase. The pipeline exits with an error before any worker is launched.

This means you must explicitly opt each phase into parallelization. The default bundled
configuration declares parallelization only on the `development` phase.

### Available Fields

| Field | Default | Description |
|-------|---------|-------------|
| `mode` | — | Must be `"same_workspace"` |
| `max_parallel_workers` | `8` | Maximum concurrent workers |
| `max_work_units` | `50` | Upper bound on work units in a plan |
| `require_allowed_directories` | `true` | Reject units missing `allowed_directories` |
| `post_fanout_verification` | `false` | Run workspace verification after all workers finish |

---

## New CLI Command: `ralph cleanup`

After a hard-kill interrupt or a failed parallel run, stale per-worker namespace directories may remain under `.agent/workers/`. The cleanup command removes them.

```bash
# See what would be deleted (dry-run)
ralph cleanup --dry-run

# Remove stale namespaces (with confirmation prompt)
ralph cleanup

# Remove without confirmation (for scripts)
ralph cleanup --force
```

### What it cleans

The cleanup command:
1. Scans `.agent/workers/<unit_id>/` directories
2. For each stale namespace, removes it with all contents
3. Reports the number of namespaces removed

There is no git operation involved. Workers in v1 operate on the shared checkout directly; there are no per-worker branches or separate checkouts to clean up.

### Exit codes

- `0`: No stale namespaces found, or all cleaned successfully
- `1`: Error (not in a git repository, etc.)

---

## Opting In to Parallel Mode

Parallel mode activates automatically when the planning phase produces a `work_units` array with **more than one entry**.

To opt in, write your planning prompt to explicitly request a `work_units` array in the final artifact:

```
After analyzing the requirements, produce a plan that:

1. Identifies distinct, independent areas of work (e.g., separate modules,
   different features, distinct infrastructure components)
2. For each area, specifies which directories the work will touch
3. Ensures units have no circular dependencies

Return your plan as a JSON artifact with a `work_units` array.
```

Each unit must include:
- `unit_id`: 1-64 characters from `[a-zA-Z0-9_-]`
- `description`: Clear description of what the unit covers
- `allowed_directories`: List of directories the unit may modify
- `dependencies`: Array of other unit_ids that must complete first (may be empty)

See `docs/agents/parallelization.md` for the full authoring guide.

---

## Reverting to Serial Mode

If parallel mode causes issues and you want to revert to serial behavior:

1. **Abort the current run** if a pipeline is in progress:
   ```bash
   # Press Ctrl-C to hard-kill, then:
   ralph cleanup --force
   ```
   This removes stale `.agent/workers/` directories left behind after a hard-kill.

2. **Remove or limit work units** in your PROMPT.md planning instructions. Specifically, avoid requesting a `work_units` array, or ensure your planning phase produces only a single unit.

3. **Start fresh**:
   ```bash
   ralph
   ```

The pipeline runs in serial mode as it did before parallelization was introduced.

---

## Key Differences from Serial Mode

| Concern | Serial | Parallel |
|---------|--------|----------|
| Checkpoint format | No `work_units` | Includes `work_units` array |
| Agent instances | One per phase | One per work unit |
| Per-worker scratch | Not used | `.agent/workers/<unit_id>/` namespace |
| Merge behavior | N/A | None — workers share the same checkout, post-fan-out is state aggregation only |
| Cleanup needed | No | After hard-kill or failures |

---

## Full Documentation

For complete details on parallel mode, see:
- `docs/agents/parallelization.md` — user guide for parallel development fan-out
- `docs/architecture/parallel-fan-out.md` — architecture and implementation details
