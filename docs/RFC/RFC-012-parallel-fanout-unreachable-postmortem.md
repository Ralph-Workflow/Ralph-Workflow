# RFC-012: Post-Mortem - Parallel Fan-Out Was Never Reachable In Production

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


**RFC Number**: RFC-012
**Title**: Post-Mortem - Parallel Fan-Out Was Never Reachable In Production
**Status**: Implemented
**Author**: Mistlight
**Created**: 2026-06-10

> NOTE: RFCs are historical design documents.
> For canonical architecture details, prefer `../architecture/event-loop-and-reducers.md` and `../architecture/effect-system.md`.

---

## Abstract

Ralph shipped a complete parallel fan-out subsystem — coordinator, wave scheduler, worker manifests, worker runtime, per-worker MCP servers, workspace isolation, policy defaults of `max_parallel_workers = 8` — and none of it ever ran in production. The router that decides between serial and parallel execution gated fan-out on `state.work_units`, and **no production code path ever populated `state.work_units` from the plan artifact**. The planning agent's `work_units` were parsed twice for *validation* and discarded both times. Every real run therefore launched exactly one agent CLI instance, serially, while the entire parallel machinery sat green-tested and unreachable.

The fix landed in two stages, and the second stage is itself part of the post-mortem: the first remediation (derive units from the plan artifact at routing time, bypassing state) was refuted in adversarial review because the **reducer's own completion contract was also broken** — `worker_states` is seeded from `state.work_units` on `FAN_OUT_STARTED`, and a successful wave could never advance the phase without it. Worse, the state machine had *preservation* machinery for `work_units` (a `copy_with` guard, a `_restore_work_units` wrapper on every reducer dispatch) but **no clearing path at all** — had fan-out ever run, the retained units would have hard-failed routing of the very next phase. The subsystem was not missing one wire; it was missing the entire state lifecycle, with the missing parts disguised by defensive code.

---

## Incident Summary

### User-visible symptom

- Parallel execution never launches multiple instances of the configured agent CLI (`opencode`, `claude`, `codex`, ...).
- Development always runs as one serial agent per iteration, regardless of how many `work_units` the planning agent declares.
- No error, no warning, no log line. Serial execution is silently presented as the normal outcome.

### Scope

- All production runs since the fan-out subsystem was ported to the Python pipeline. `FanOutEffect` was only ever constructed in tests (which pre-seed `state.work_units` directly) and in the resume path *inside* an already-running fan-out — which could never start.

### Impact

- The headline parallelization capability did not exist in practice; all advertised wall-clock benefits of fan-out were unrealized.
- Planning agents spent effort decomposing work into disjoint `work_units` whose only production effect was stricter proof validation on the serial developer.
- Significant maintenance cost was paid on a subsystem (coordinator, scheduler, worker runtime, isolation, displays) that produced zero production value.

---

## Root Cause

Not one missing edge — a missing *lifecycle*, discovered in layers.

**Layer 1 — no seeding (the reachability bug).** The producer existed: the planning agent submits `work_units` inside the plan artifact, and `parse_work_units_from_artifact` parses them. The consumer existed: `_parallel_or_agent_effect` (`effect_router.py`) emits `FanOutEffect` when `len(state.work_units) >= 2`. The bridge did not exist: nothing copied the parsed units from the artifact channel into the state channel the router reads. Grep-level proof — the only production assignments to `work_units` were a child worker seeding its own single-unit state (`worker_runtime.py`), resume filtering *inside* an already-running fan-out (`fan_out.py`), and preservation-only code (`reducer._restore_work_units`, the `copy_with` guard, and the same restore pattern at `runner.py:452`). All three call sites of `parse_work_units_from_artifact` in `ralph/phases/execution.py` (plan-output validation, plan-input validation, proof-id extraction) used the result transiently and dropped it.

**Layer 2 — no completion (found only when fixing layer 1).** The reducer seeds `worker_states` from `state.work_units` on `FAN_OUT_STARTED` (`_reducer_worker_state.py`), and `_handle_all_workers_complete` no-ops when `worker_states` is empty. So any fix that routed to fan-out without populating state would run the wave and then **never advance the phase** — an infinite re-fan-out loop, with worker failures silently dropped by the `unit_id not in state.worker_states` guards and checkpoints recording zero progress. The first remediation attempt had exactly this flaw; adversarial review caught it by replaying the coordinator's real event sequence through the real reducer.

**Layer 3 — no teardown (latent in the "working" design).** Had units ever reached state, nothing could remove them: `copy_with` silently drops any change to non-empty `work_units`, and `reduce()` wraps every handler dispatch in `_restore_work_units` (eight call sites in `reducer.py`, plus the same pattern at `runner.py:452`). After a successful wave the retained units would hit the next phase's routing — `development_commit_cleanup` declares no parallelization — and `_fan_out_effect` would return `ExitFailureEffect`, hard-failing the run *after all work succeeded*. The state machine could enter the parallel lifecycle but could neither complete nor exit it. (Layer 1 alone proves the path never ran; layer 3 corroborates it — a completed wave would have crashed loudly at the next phase, and no such crash was ever reported.)

**Layer 4 — checkpoint ordering (found in the second adversarial review).** Even with seed + complete + clear in place, the post-wave checkpoint was initially written *before* clearing, persisting advanced-phase + retained-units state for the entire commit-cleanup window. A crash there would resume into the layer-3 hard failure. The lifecycle tests had stubbed `ckpt.save` to a no-op — which is exactly why this escaped the first corrected version; the tests now capture checkpointed states and assert the last one is routable.

**Layers 5–7 — runtime breaks found only by running it live.** With routing, completion, teardown, and persistence all fixed and `make verify` fully green, a manual end-to-end run (real `ralph --resume` in a sandbox repo, fake agent CLI recording timestamps) immediately hit three more dead-path failures, one per retry wave:

- **MCP factory protocol break** (`ralph/mcp/server/factory_impl.py:84`): `DynamicBindingMcpServerFactory._bridge_pid` requires the bridge to expose `process.pid`, but the production `start_mcp_server` had since been hardened to return `RestartAwareMcpBridge` — which exposed no `process` attribute. Every worker died at session setup with `TypeError`. The factory's tests injected a `FakeBridge` that *did* have `.process`, so the type drift between the real producer and the real consumer was invisible. Fixed by adding a lock-guarded `process` property to `RestartAwareMcpBridge`, pinned by a test that wires the real bridge class through the real factory.
- **Worker bootstrap blocked by its own write scope** (`ralph/pipeline/parallel/worker_runtime.py`): the worker built its prompt-materialization workspace from the *agent-facing* restricted scope (repo root deliberately excluded from `allowed_roots`), so reading `PROMPT.md` — a shared input at the repo root — raised `ValueError: resolves outside workspace root` before any agent launched. The bootstrap test had monkeypatched `run_parallel_worker_from_manifest` itself. Fixed: the trusted bootstrap reads through a root-scoped workspace; the restricted scope still governs the agent's MCP surface via `workspace_scope`.
- **Worker pre-run cleanup touched shared artifacts** (`ralph/pipeline/effect_executor.py`): `clear_phase_output_artifacts` tried to delete the shared repo-root `development_result.json` through the worker's restricted workspace — `ValueError` again. The one existing test of this exact path had monkeypatched `clear_phase_output_artifacts` to a no-op. Fixed: parallel workers skip shared-output cleanup (it is the parent's job), pinned by the same test un-stubbed.

Seven layers, one pattern: every break sat exactly where a test had substituted a fake for the production counterpart.

---

## Why This Was Never Caught

1. **Every test of the gate pre-seeded the state.** All fan-out routing tests built `PipelineState(work_units=(...))` by hand and asserted `FanOutEffect` came out. They proved the consumer worked *given* units in state — exactly the precondition production never satisfied. The coordinator, scheduler, and worker-runtime suites likewise started downstream of the missing edge. Component coverage was excellent; the un-owned edge between components had zero coverage.

2. **The failure mode was a valid success.** Serial execution is the correct outcome for plans with fewer than two units, so the degraded behavior was indistinguishable from "the planner chose not to parallelize." There was no error to alert on, no log line to grep, no failing artifact. Silent fallback to a legitimate sibling behavior is the hardest class of regression to notice.

3. **Validation created the illusion of integration.** The plan's `work_units` were demonstrably *used* — parsed, schema-validated, policy-validated, cross-checked against `plan_items_proven` in development proofs. Anyone tracing the data saw it flowing through real code. The flow just never reached the router.

4. **No end-to-end reachability test existed.** Nothing asserted "a plan artifact on disk with N≥2 units causes multiple agent processes (or even a `FanOutEffect`) from the production entry path." The black-box boundary every test chose was one layer below or one layer above the broken seam.

---

## Architectural Analysis: What Caused The Drift

### Two parallel data channels with no owned junction

The architecture moves data along two independent channels:

- the **artifact channel**: agent → MCP `submit` tool → JSON on disk under `.agent/artifacts/` → validated by phase handlers;
- the **state channel**: events → reducer → `PipelineState` → checkpoint.

Plan content lives in the artifact channel. Effect routing reads the state channel. Crossing between channels requires someone to deliberately write a bridge (an event the reducer folds, or a routing-time read), and that bridge belonged to no module: the phase handler's job ended at "validate the artifact," the reducer's job ended at "fold the events it knows," the router's job ended at "read the state." Each component honored its contract; the system requirement fell between contracts. RFC-010 (success-ordering regression) is the same *family* of failure — an invariant spanning two signals with no single owner — though a different mechanism (a timing gap on an edge that existed, versus an edge that never existed). The recurring Ralph failure mode is *inter-channel edges, not intra-component logic*.

### Defensive code born without a producer

The codebase contained machinery to *protect* `state.work_units`: `_restore_work_units` re-attaching units on every reducer dispatch, and a `copy_with` guard refusing to overwrite non-empty units. Preservation logic this deliberate reads as proof the value is alive. Git archaeology shows it never was: commit `47161a8fe` (2026-04-16) introduced the field, the restore wrapper, the guard, *and* tests like `test_work_units_immutable_once_set` in a single commit — with no producer anywhere. The machinery was not drift from a removed producer; it was **born sourceless**, written speculatively for a seeding edge that never arrived. From day one it actively misled later readers (including agents) into assuming the seeding existed somewhere they hadn't looked.

### The Rust→Python port changed the channel topology, not just the language

RFC-008 specified the fan-out design in the Rust era — and the Rust implementation had a structurally different shape: `src/reducer/boundary/parallel.rs` validated the plan and emitted **payload-carrying events** (`ParallelPlanValidated { plan }`), so the reducer was self-contained and no state field needed external seeding. The Python port replaced payload-carrying events with a bare `FAN_OUT_STARTED` enum plus a `state.work_units` field that someone else was supposed to populate — and never built that someone. Each component was carried over with its tests (coordinator, scheduler, manifests, worker bootstrap) because components are what tests pin down; the cross-cutting invariant "a unit-bearing plan must route to fan-out" had no test, so no port or refactor was obligated to preserve it. Refactors preserve what is asserted; everything else is free to drift — and a port that silently swaps event-sourced data flow for pre-seeded-state data flow is the largest drift of all.

### Agent-driven development amplifies completion bias

This repo is substantially built by unattended agents. An agent asked to "implement parallel workers" builds and green-tests the subsystem; an agent asked to "validate plan work_units" wires validation. Each task completes honestly. No task was ever phrased as "make a real run with a real plan launch two real agent processes," so no agent ever discovered the seam — and each agent that later *read* the code saw a complete-looking subsystem (see below) and moved on.

---

## Why We Believed It Was Already Implemented

- **The machinery was complete and polished.** Eight-deep call chain from `FanOutEffect` to `ProcessManager.spawn_async`, per-worker manifests, namespaces, MCP endpoints, escalating tree-kill, a `parallel_development_summary.json` reporter, dashboards. Nothing about it looked vestigial.
- **The default policy advertised it.** `ralph/policy/defaults/pipeline.toml` declared `[blocks.development.phase.parallelization]` with `max_parallel_workers = 8` — configuration that strongly implies a live feature.
- **Thousands of passing tests touched it.** Coordinator stress tests, isolation tests, checkpoint-resume tests. Green tests over dead code are indistinguishable from green tests over live code unless a test starts from the production entry path.
- **The data was visibly consumed.** Validation, policy checks, and proof enforcement all touched `work_units`, so every partial trace "confirmed" integration.
- **Docs and RFC-008 described it as the design.** Historical design documents narrate intent in the present tense; absent a reachability check, intent reads as fact.

---

## Remediation (Implemented)

The fix wires the full lifecycle: derive → seed → track → complete → clear.

1. **Derive (routing)** — `ralph/pipeline/effect_router.py`: new `_work_units_from_plan_artifact(workspace_root)` reads `.agent/artifacts/plan.json` through the same seams the phase handlers use (`load_phase_artifact`, `unwrap_phase_artifact_content`, `is_noop_plan`, `normalize_plan_artifact_content`, `parse_work_units_from_artifact`). `_parallel_or_agent_effect` consults the artifact only when `state.work_units` is empty **and** the phase declares parallelization. Missing/no-op/corrupt plans log a warning and fall back to serial. Worker recursion is structurally blocked: a child worker carries exactly one unit in state, so the plan is never consulted.
2. **Seed (wave entry)** — `ralph/pipeline/fan_out.py`: `_run_fan_out_async` seeds the wave-local state with the effect's units before reducing any event, so `FAN_OUT_STARTED` builds `worker_states` and the whole existing reducer contract (per-worker tracking, failure attribution, `ALL_WORKERS_COMPLETE` phase advancement, checkpointing) operates as designed.
3. **Clear (wave exit)** — new `PipelineState.with_parallel_execution_cleared()` is the single sanctioned seam that bypasses the preservation guard; `fan_out._cleared_after_successful_wave` applies it only when every worker SUCCEEDED. A failed or interrupted wave keeps `work_units` + `worker_states` so the next development entry resumes only the unfinished units. The previously-stalling edge case — resume after all units already succeeded — now reduces `ALL_WORKERS_COMPLETE` and advances instead of returning unchanged (which would have looped forever).
4. **Persist cleared (crash safety)** — clearing happens *before* the post-wave checkpoint, so the state persisted during the commit-cleanup window is routable on resume; the pre-clear state is retained in memory only for the per-worker summary artifact. A failed wave's checkpoint keeps full tracking state for resume.

Tests pinning the previously un-owned edges:

- `tests/test_effect_router_plan_work_units_fanout.py` (9 tests): disk artifact → emitted effect. Fan-out trigger, serial fallbacks (0/1 units, no-op, missing, corrupt), worker non-recursion, non-parallelized-phase isolation, resume, unsafe-plan rejection.
- `tests/integration/test_fan_out_state_lifecycle.py` (6 tests): production-shaped (empty-`work_units`) state through `execute_fan_out_sync` → phase advancement, tracking cleared on success, tracking preserved on partial failure, all-succeeded resume advances instead of stalling, and — capturing real checkpoint writes instead of stubbing them — the last checkpointed state is cleared on success and resume-capable on failure.
- Six pre-existing integration tests asserted post-wave `worker_states` retention as their "workers succeeded" observable; they now assert the durable summary artifact (`parallel_development_summary.json`) plus cleared tracking state — which under the corrected lifecycle *is* the success signal.
- Live-run regression pins: the real `RestartAwareMcpBridge` wired through the real factory (`tests/test_mcp_factory_impl.py`), worker prompt materialization from shared inputs with real `materialize_prompt_for_phase` (`tests/integration/test_parallel_worker_prompt_materialization.py`), and the worker-mode `execute_agent_effect` path with `clear_phase_output_artifacts` un-stubbed (`tests/test_pipeline_runner_execute_agent_effect_2_a.py`).

5. **MCP bridge pid** — `RestartAwareMcpBridge.process` property (lock-guarded) restores the `_BridgeWithProcess` protocol the worker-session factory depends on.
6. **Worker bootstrap reads** — `run_parallel_worker_from_manifest` materializes prompts through a root-scoped `FsWorkspace`; the restricted scope remains the agent-facing enforcement surface.
7. **Worker cleanup scope** — `execute_agent_effect` skips `clear_phase_output_artifacts` when `parallel_worker=True`.

**Live verification (2026-06-10):** a real `ralph --resume` run in a sandbox repo (3-unit plan, fake agent CLI logging timestamps) launched three agent CLI processes with overlapping execution windows — all three concurrent for ~2.4 s per wave — and the final wave completed with all workers SUCCEEDED and `parallel_development_summary.json` reporting `all_succeeded: true`. This is the first time the fan-out path is known to have executed end-to-end.

`make verify` passes (7447 tests, within the 60 s budget).

---

## Residual Risks (Open, Now Load-Bearing)

The fix makes one previously-academic question real: **the plan artifact has no retirement lifecycle.** `plan.json` is never consumed or cleared on wave success — `artifact_history.clear_on_fresh_entry` governs history copies, not the live artifact, and only fresh *planning* entry rewrites it. After a successful wave clears `state.work_units`, a loopback re-entry into development (e.g., `development_analysis` → `request_changes`) re-consults the same `plan.json` and re-fans-out **all** original units against already-completed work. Re-running development on loopback is the serial semantic too, and workers see analysis feedback artifacts, so this is defensible — but it is now a *decision being made implicitly by an artifact nobody retires*. Follow-up: pin the intended loopback behavior with a test, and decide between explicit plan retirement on wave success or scoped re-planning on loopback.

## Lessons / Prevention

1. **Every `Effect` variant needs a reachability test from the production entry path.** Not "given the right state, the effect is handled" but "given real on-disk inputs, the router emits it." A dead effect type should fail CI, not sit green for months.
2. **Stateful subsystems must be tested as a lifecycle, not as stages.** Every fan-out test seeded its stage's precondition by hand (router tests seeded `state.work_units`, reducer tests seeded `worker_states`, coordinator tests seeded both), so each stage passed while the chain enter → track → complete → exit had never been executed once. At least one test per stateful subsystem must start from the production-shaped state (here: empty `work_units`) and run to the subsystem's *exit*, asserting the state is fit for whatever comes next.
3. **Persistence is part of the lifecycle.** Stubbing `ckpt.save` to a no-op hid the layer-4 bug: the in-memory return value was correct while the on-disk checkpoint was poisoned. Lifecycle tests must capture what is *persisted*, not only what is returned.
4. **Silent fallback to a sibling behavior must log.** The router now warns when a plan artifact exists but cannot yield units. Any future "degrade to the simpler path" decision should leave a trace that distinguishes *chose serial* from *fell back to serial*.
5. **Treat preservation code as a claim that needs both a producer and a destructor.** `_restore_work_units` and the `copy_with` guard should have been challenged with "who sets this, and who clears it?" — defensive code guarding a value that nobody produces *or* nobody can remove is a drift signature, and under the zero-dead-code rule it deserves the same suspicion as an unused function.
6. **When two data channels (artifact ↔ state) must agree, the bridge needs an owner and a test.** RFC-010 is the same failure family — an invariant spanning two signals with no single owner; cross-channel invariants are where this architecture drifts.
7. **Adversarial review of "complete" fixes pays for itself — three times here.** The first remediation passed all 7371 existing tests plus 9 new ones and full verification, and was refuted by replaying real coordinator events through the real reducer. The second passed 7441 and was refuted on checkpoint ordering. The third passed 7443 *and two architect reviews* — and the live run refuted it three more times. Green CI measures the asserted surface, not the system.
8. **Execution evidence outranks review evidence.** Two reviewers read every changed line and replayed event sequences, and still missed layers 5–7, because those breaks live in the *integration between real components* (real bridge type vs factory expectation, real scope vs real file layout) — exactly what reading cannot exercise and stubs deliberately hide. The evidence hierarchy this incident establishes: unit test < lifecycle test < adversarial review < running the real binary. A "complete" claim on a previously-dead path requires the top rung.
9. **A stub at a seam is a claim that the seam cannot drift — and every such claim here was false.** All seven layers were hidden by a test double standing in for the exact production counterpart: pre-seeded state, no-op'd `ckpt.save`, `FakeBridge` with `.process`, monkeypatched `run_parallel_worker_from_manifest`, no-op'd `clear_phase_output_artifacts`. Rule of thumb going forward: every monkeypatch of a production function/type needs at least one companion test somewhere that lets the real counterpart flow through that seam.
10. **`runtime_checkable` Protocols hide type drift from mypy.** `_BridgeWithProcess` conformance was only ever checked by `isinstance` at runtime, so when `start_mcp_server`'s return type changed to `RestartAwareMcpBridge`, no static check failed. Where a Protocol guards a production seam, pin conformance statically (a typed assignment or a dedicated conformance test naming the real producer types).
11. **One workspace object served two trust levels — and both worker bugs (layers 6–7) came from that conflation.** The same restricted `FsWorkspace` was used as the *agent enforcement surface* (correct) and as the *trusted orchestrator's own I/O handle* (wrong — it blocked reading shared inputs and triggered illegal shared-artifact writes). Read scope and write scope are different capabilities; trusted-bootstrap I/O and agent-enforced I/O deserve explicitly distinct objects, not one object reused under two assumptions.

## Architecture Improvement Backlog

Mechanically enforceable follow-ups, judged individually. The repo's existing pattern is enforcement-by-audit (`ralph/testing/audit_*.py`); lessons that stay prose are policy-by-memory, which is the failure mode this incident demonstrates.

| # | Improvement | Verdict | Rationale |
|---|---|---|---|
| 1 | **Effect-reachability audit**: CI check that each of the 10 `Effect` dataclasses is emitted by at least one production-entry-path test | **Adopt** | Small, fits the existing `audit_*` pattern, converts Lesson 1 from prose to a gate |
| 2 | **Payload-carrying `FanOutStartedEvent`** (work units on the event, reducer self-contained) | **Adopt (follow-up)** | Restores the Rust-era event-sourced topology; removes the hidden "someone must pre-seed state" temporal coupling that produced layer 2 |
| 3 | **Single plan-projection module** consumed by phases and router | **Adopt (directional)** | The fix added a fourth ad-hoc `plan.json` reader with its own legacy-shape branching — a fresh drift surface; one owned projection collapses it |
| 4 | **Make the `copy_with` work_units guard loud** (log or raise on a dropped update) | **Adopt** | A silent strip disguised layer 3 once already; the next drift should fail loudly |
| 5 | Generic lifecycle registry for preserved state fields | **Omit** | Only one field has preservation semantics; a registry is speculative machinery — the same instinct that produced preservation-without-producer. Cheap alternative: a test asserting `work_units` has exactly one producer seam and one destructor seam |
| 6 | **Effect-fired telemetry**: per-run summary of which effect types fired | **Adopt** | The only measure that catches this class in the field: "FanOutEffect: 0 across all runs" would have surfaced this in week one |
| 7 | **Plan-artifact retirement lifecycle** (see Residual Risks) | **Adopt** | The stale-plan loopback decision is currently implicit; needs an owner and a pinned test |
| 8 | **Named coordinator-event-replay fixture** (real event sequences through the real reducer) | **Adopt** | The technique that caught layer 2 deserves to outlive this incident's test file |
| 9 | **Checkpoint-compat test** loading a pre-fix-shaped mid-wave checkpoint through the new resume path | **Adopt** | The resume path changed; old on-disk checkpoints must still resume |
| 10 | Static audit for state fields written only by tests | **Omit** | Hard to make precise (a legitimate producer may not be a state write at all); #1 catches the same class more reliably downstream |
| 11 | **`subprocess_e2e` fan-out smoke test**: sandbox repo + multi-unit plan + fake agent CLI, asserting overlapping agent-process execution windows and a succeeded summary | **Adopt (highest value)** | This exact rig, built ad hoc during live verification, caught layers 5–7 that 7443 green tests and two architect reviews missed; it belongs in the `subprocess_e2e` tier (outside the unit-test I/O policy), runnable on demand or nightly |
| 12 | **First-class test/null agent transport** | **Adopt** | `generic` transport refuses MCP wiring by design, so the smoke rig had to masquerade a bash script as `opencode`; a built-in test transport makes E2E smoke cheap and honest instead of dependent on transport-impersonation quirks |
| 13 | **Orphan cleanup on parent death** | **Adopt** | Observed live: killing the parent run left per-worker MCP servers respawning across waves (68 stray processes at peak); worker/MCP lifetimes should be bound to the parent (process group / parent-death signal), not just to graceful shutdown |
| 14 | **Static conformance pins for runtime-checkable Protocols** | **Adopt** | One typed assignment per (real producer, protocol) pair turns the layer-5 class of drift into a mypy failure instead of a runtime TypeError on a dead path |
| 15 | Mechanical audit flagging monkeypatches of same-module production functions | **Omit (as audit)** | Too noisy to enforce mechanically; adopt as a review rule instead (lesson 9): every stubbed seam needs a companion test with the real counterpart |
