# OpenCode Child Liveness Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Ralph Workflow reliably distinguish OpenCode child agents that are truly still doing useful work from children that have already exited, are orphaned/stale, or are hung.

**Architecture:** Replace today's binary "child exists" checks with a layered liveness model. Keep process existence as one input, but add child progress leases, explicit child lifecycle acks, and a stronger post-exit reconciliation path so exit classification is based on fresh evidence rather than label presence alone.

**Tech Stack:** Python 3.12, psutil, ProcessManager, OpenCode transport, Ralph idle/post-exit watchdogs, pytest/FakeClock.

---

## Problem Statement

Today Ralph uses three weak signals to decide whether OpenCode child work is still alive:

1. `DefaultLivenessProbe.any_agent_active(label_prefix)`
2. `ManagedProcess.has_live_descendants()`
3. completion signals (`explicit_complete` marker or artifact file existence)

Those signals can answer "is something present?" but not reliably answer:
- did the child already exit but leave stale labels/process artifacts?
- is the child still alive but hung?
- is the child alive and making meaningful progress?

The hardening plan below upgrades Ralph from **existence detection** to **evidence-backed liveness classification**.

---

## Target End State

Ralph should classify each OpenCode child into one of four states using fresh evidence:

- **ACTIVE_PROGRESSING** — child lease is fresh and progress heartbeat advanced recently
- **ALIVE_BUT_QUIET** — child process exists but no recent progress heartbeat; within grace threshold
- **HUNG_OR_STALE** — child process/label exists but lease expired or progress stale beyond threshold
- **EXITED_CONFIRMED** — child emitted explicit terminal ack, or process/label cleared and no lease remains

Parent-side decisions should then map cleanly to:
- continue waiting when child is `ACTIVE_PROGRESSING`
- show suspected-frozen when child is `ALIVE_BUT_QUIET`
- stop waiting and raise resumable/hang outcome when child is `HUNG_OR_STALE`
- complete normally when all children are `EXITED_CONFIRMED`

---

## Design Principles

1. **Never trust process existence alone.** A PID or label only proves something exists, not that it is doing useful work.
2. **Use leases, not permanent claims.** Child activity must be renewed.
3. **Separate completion from liveness.** Completion ack and progress heartbeat are different signals.
4. **Make stale evidence expire automatically.** Labels and child state must age out.
5. **Keep watchdogs deterministic.** Continue using injected clocks and polling seams for tests.
6. **Prefer black-box tests.** Every failure mode in this plan needs a reproducible test.

---

## Proposed Data Model

### New concept: Child execution lease

Add a child-liveness record owned by Ralph, keyed by child label/scope:

- `child_id`
- `scope_prefix`
- `pid` (optional current pid)
- `started_at`
- `last_progress_at`
- `last_heartbeat_at`
- `last_ack_at`
- `last_known_phase` (`spawned`, `tool_call`, `llm_wait`, `writing_artifact`, `complete`, `failed`)
- `terminal_state` (`complete`, `failed`, `terminated`, `unknown`) optional
- `lease_expires_at`

This should live in Ralph runtime memory first, not in files. The goal is accurate real-time classification, not durable replay yet.

### New concept: Progress heartbeat

A progress heartbeat is a parent-observable signal that only advances when the child does meaningful work, for example:
- child emits a structured OpenCode lifecycle/progress line
- child writes/submits an artifact milestone
- child invokes a Ralph-visible tool / reports progress
- child updates a dedicated heartbeat channel

A raw process merely existing must not count as progress.

---

## Implementation Tasks

### Task 1: Define child-liveness vocabulary and policy knobs

**Files:**
- Modify: `ralph-workflow/ralph/agents/activity.py`
- Modify: `ralph-workflow/ralph/config/models.py`
- Test: `ralph-workflow/tests/test_config_loader.py`

**Step 1: Write failing tests for new config defaults**

Add tests asserting new knobs load with sane defaults:
- `agent_child_progress_ttl_seconds`
- `agent_child_heartbeat_ttl_seconds`
- `agent_child_stale_label_ttl_seconds`
- `agent_child_exit_reconcile_seconds`

**Step 2: Run targeted tests to confirm failure**

Run: `cd ralph-workflow && uv run pytest tests/test_config_loader.py -k child -v`
Expected: failures for missing fields/defaults.

**Step 3: Add new enums / activity kinds**

Extend `AgentActivityKind` with explicit child lifecycle/progress signal kinds such as:
- `CHILD_HEARTBEAT`
- `CHILD_PROGRESS`
- `CHILD_TERMINAL_ACK`

Keep existing kinds intact.

**Step 4: Add new config fields**

In `config/models.py`, add conservative defaults such as:
- progress TTL: 45s
- heartbeat TTL: 15s
- stale label TTL: 10s
- exit reconcile window: 5s

**Step 5: Re-run targeted tests**

Run: `cd ralph-workflow && uv run pytest tests/test_config_loader.py -k child -v`
Expected: PASS.

---

### Task 2: Introduce an in-memory child lease registry

**Files:**
- Create: `ralph-workflow/ralph/process/child_liveness.py`
- Modify: `ralph-workflow/ralph/process/__init__.py`
- Test: `ralph-workflow/tests/test_child_liveness.py`

**Step 1: Write failing tests for lease lifecycle**

Cover:
- register child
- renew heartbeat only
- renew progress
- mark terminal ack
- stale lease expiry
- stale label expiry without progress

**Step 2: Run new failing test file**

Run: `cd ralph-workflow && uv run pytest tests/test_child_liveness.py -v`
Expected: module import / symbol failures.

**Step 3: Implement minimal registry**

Create a focused `ChildLivenessRegistry` with APIs like:
- `register_child(...)`
- `record_heartbeat(...)`
- `record_progress(...)`
- `record_terminal_ack(...)`
- `snapshot(scope_prefix)`
- `prune_stale(now)`

Use injected time source or pass `now` explicitly for testability.

**Step 4: Keep state model simple**

Do not persist to disk in v1. In-memory registry is enough for parent-side live orchestration.

**Step 5: Re-run tests**

Run: `cd ralph-workflow && uv run pytest tests/test_child_liveness.py -v`
Expected: PASS.

---

### Task 3: Teach ProcessManager/LivenessProbe about freshness, not just presence

**Files:**
- Modify: `ralph-workflow/ralph/process/liveness.py`
- Modify: `ralph-workflow/ralph/process/manager.py`
- Test: `ralph-workflow/tests/test_process_audit.py`
- Test: `ralph-workflow/tests/test_liveness_probe.py`

**Step 1: Write failing tests for stale-label false positives**

Cases:
- active record with expired freshness should not count as active child work
- exited record must not count even if label matches
- unrelated scope must never count

**Step 2: Run targeted failing tests**

Run: `cd ralph-workflow && uv run pytest tests/test_process_audit.py tests/test_liveness_probe.py -v`
Expected: failures because freshness logic does not exist.

**Step 3: Add richer probe API**

Evolve `LivenessProbe` from `any_agent_active(prefix) -> bool` toward something like:
- `child_snapshot(prefix) -> ChildActivitySnapshot`

The snapshot should include:
- `has_process`
- `has_fresh_label`
- `has_fresh_progress`
- `oldest_live_child_seconds`
- `active_count`

Keep a boolean compatibility shim while migrating callers.

**Step 4: Harden descendant detection**

In `manager.py`, keep `has_live_descendants()` but add a richer method that returns counts/ages and excludes zombie/defunct edge cases as aggressively as psutil allows.

**Step 5: Re-run tests**

Run: `cd ralph-workflow && uv run pytest tests/test_process_audit.py tests/test_liveness_probe.py -v`
Expected: PASS.

---

### Task 4: Remove the unscoped-sentinel blind spot

**Files:**
- Modify: `ralph-workflow/ralph/agents/execution_state.py`
- Test: `ralph-workflow/tests/test_opencode_session_execution.py`
- Test: `ralph-workflow/tests/test_agents_invoke.py`

**Step 1: Write failing tests for `label_scope=None`**

Add cases asserting that unscoped OpenCode runs still use a meaningful liveness fallback and are not silently blind to child work.

**Step 2: Run targeted tests**

Run: `cd ralph-workflow && uv run pytest tests/test_opencode_session_execution.py tests/test_agents_invoke.py -k scope -v`
Expected: failures against current sentinel behavior.

**Step 3: Replace sentinel-only logic**

Current behavior returns `agent:__unscoped_disabled__:`. Replace this with explicit semantics:
- if scoped label available: use scoped Ralph-tracked snapshot first
- if not scoped: use descendant snapshot + explicit progress lease registry
- never silently treat unscoped as “no child activity possible”

**Step 4: Re-run tests**

Run: `cd ralph-workflow && uv run pytest tests/test_opencode_session_execution.py tests/test_agents_invoke.py -k scope -v`
Expected: PASS.

---

### Task 5: Add explicit child terminal acknowledgments and progress heartbeats

**Files:**
- Modify: `ralph-workflow/ralph/agents/invoke.py`
- Modify: `ralph-workflow/ralph/agents/execution_state.py`
- Modify: `ralph-workflow/ralph/agents/completion_signals.py`
- Test: `ralph-workflow/tests/test_agents_invoke.py`
- Test: `ralph-workflow/tests/agents/test_invoke_timeout_integration.py`

**Step 1: Write failing tests for three distinct outcomes**

Add tests that simulate:
- child terminal ack seen → `EXITED_CONFIRMED`
- child process exists but progress stale → hung/stale path
- child process exists and progress keeps renewing → still active path

**Step 2: Run targeted tests to confirm failure**

Run: `cd ralph-workflow && uv run pytest tests/test_agents_invoke.py tests/agents/test_invoke_timeout_integration.py -k child -v`
Expected: failures for missing classification machinery.

**Step 3: Parse/propagate structured child signals**

In `execution_state.py` and/or `invoke.py`, recognize provider output lines that indicate:
- child started
- child heartbeat/progress
- child complete/fail

Route those events into the new child lease registry.

**Step 4: Strengthen completion signals**

In `completion_signals.py`, stop treating raw artifact existence as sufficient. Require:
- file exists
- artifact parses
- artifact schema is minimally valid for the phase

If a child terminal ack is present, that should outrank ambiguous process existence.

**Step 5: Re-run targeted tests**

Run: `cd ralph-workflow && uv run pytest tests/test_agents_invoke.py tests/agents/test_invoke_timeout_integration.py -k child -v`
Expected: PASS.

---

### Task 6: Rework OpenCode exit classification around evidence precedence

**Files:**
- Modify: `ralph-workflow/ralph/agents/execution_state.py`
- Modify: `ralph-workflow/ralph/agents/invoke.py`
- Modify: `ralph-workflow/ralph/agents/post_exit_watchdog.py`
- Test: `ralph-workflow/tests/agents/test_post_exit_watchdog.py`
- Test: `ralph-workflow/tests/test_agents_invoke.py`

**Step 1: Write failing tests for precedence rules**

Rules to lock in:
1. terminal ack or valid required artifact wins over child existence
2. fresh progress lease wins over quiet process existence
3. stale process existence without fresh progress becomes `HUNG_OR_STALE`, not endless waiting
4. no lease + no process + no valid completion becomes resumable retry path

**Step 2: Run targeted tests**

Run: `cd ralph-workflow && uv run pytest tests/agents/test_post_exit_watchdog.py tests/test_agents_invoke.py -k exit -v`
Expected: failures until precedence logic is implemented.

**Step 3: Implement evidence ordering**

Refactor `classify_quiet()` / `classify_exit()` around a clear priority model:
- `TERMINAL_COMPLETE` if valid completion evidence exists
- `WAITING_ON_CHILD` only if child has fresh progress lease or fresh heartbeat within grace
- `RESUMABLE_CONTINUE` if no valid completion and no fresh child evidence
- a separate stale/hung diagnostic path when process/label exists but progress lease expired

**Step 4: Tighten post-exit watchdog semantics**

`wait_parent_exit_grace()` and `wait_descendant_quiesce()` should poll for *fresh child evidence*, not just raw `WAITING_ON_CHILD` classification from existence-only checks.

**Step 5: Re-run tests**

Run: `cd ralph-workflow && uv run pytest tests/agents/test_post_exit_watchdog.py tests/test_agents_invoke.py -k exit -v`
Expected: PASS.

---

### Task 7: Surface operator-visible diagnostics for why Ralph still thinks child work is alive

**Files:**
- Modify: `ralph-workflow/ralph/agents/idle_watchdog.py`
- Modify: `ralph-workflow/ralph/display/subscriber.py`
- Test: `ralph-workflow/tests/display/test_subscriber.py`
- Test: `ralph-workflow/tests/agents/test_idle_watchdog.py`

**Step 1: Write failing tests for richer waiting diagnostics**

Required output should distinguish:
- `alive_by=fresh_progress`
- `alive_by=fresh_heartbeat_only`
- `alive_by=os_descendant_only_stale_progress`
- `alive_by=stale_label_only` (warn-worthy)

**Step 2: Run targeted tests**

Run: `cd ralph-workflow && uv run pytest tests/display/test_subscriber.py tests/agents/test_idle_watchdog.py -k waiting -v`
Expected: failures for missing fields/output.

**Step 3: Expand corroboration snapshot**

Add diagnostics that explain *why* WAITING_ON_CHILD is being held alive:
- fresh progress age
- heartbeat age
- label freshness age
- descendant count
- completion-evidence state

**Step 4: Re-run tests**

Run: `cd ralph-workflow && uv run pytest tests/display/test_subscriber.py tests/agents/test_idle_watchdog.py -k waiting -v`
Expected: PASS.

---

### Task 8: Add end-to-end regression coverage for the three operator-critical scenarios

**Files:**
- Create: `ralph-workflow/tests/integration/test_opencode_child_liveness_states.py`
- Test: `ralph-workflow/tests/integration/test_opencode_child_liveness_states.py`

**Step 1: Write failing black-box scenarios**

Create three integration scenarios:
1. **Child exited cleanly** — terminal ack / valid artifact appears, no indefinite waiting
2. **Child hung** — process exists but no progress renewal; watchdog escalates to stale/hung path
3. **Child still working** — child renews progress lease and parent keeps waiting until complete

**Step 2: Run the new failing test file**

Run: `cd ralph-workflow && uv run pytest tests/integration/test_opencode_child_liveness_states.py -v`
Expected: failures until the full stack is wired.

**Step 3: Implement only missing glue exposed by the tests**

Do not add extra features here. Use this task to finish wiring and fix gaps exposed by the integration scenarios.

**Step 4: Re-run tests**

Run: `cd ralph-workflow && uv run pytest tests/integration/test_opencode_child_liveness_states.py -v`
Expected: PASS.

---

### Task 9: Verification and rollout safety

**Files:**
- Modify: `ralph-workflow/docs/sphinx/recovery.md`
- Modify: `ralph-workflow/docs/sphinx/troubleshooting.md`
- Test: existing suites

**Step 1: Document the new semantics**

Update docs to explain:
- raw process existence no longer implies active child work
- difference between heartbeat, progress, and completion ack
- how stale child detection surfaces in logs

**Step 2: Run focused suites**

Run:
- `cd ralph-workflow && uv run pytest tests/test_child_liveness.py tests/test_liveness_probe.py -v`
- `cd ralph-workflow && uv run pytest tests/test_opencode_session_execution.py tests/test_agents_invoke.py tests/agents/test_post_exit_watchdog.py tests/agents/test_idle_watchdog.py tests/agents/test_invoke_timeout_integration.py -v`
- `cd ralph-workflow && uv run pytest tests/integration/test_opencode_child_liveness_states.py -v`

Expected: all pass.

**Step 3: Run full verification**

Run: `cd ralph-workflow && make verify`
Expected: full suite passes with no warnings/errors.

---

## Rollout Order

1. Vocabulary + config
2. Child lease registry
3. Freshness-aware liveness probe
4. Remove sentinel blind spot
5. Explicit progress + terminal ack plumbing
6. Exit-classification precedence rewrite
7. Operator diagnostics
8. End-to-end regressions
9. Docs + full verify

This order keeps the design testable and avoids rewriting all exit logic at once.

---

## Risks and Mitigations

- **Risk:** Overfitting to OpenCode output format.
  **Mitigation:** keep child signal parsing narrow and add fallback to process/lease evidence.

- **Risk:** Freshness TTLs too aggressive cause false hung detection.
  **Mitigation:** start conservative, make all thresholds configurable, add integration coverage.

- **Risk:** More state surfaces more race conditions.
  **Mitigation:** all registry APIs take explicit time and remain single-purpose; test with FakeClock.

- **Risk:** Artifact validation breaks existing loose callers.
  **Mitigation:** phase in minimal schema validation first, then tighten only where required.

- **Risk:** Unscoped runs stay ambiguous.
  **Mitigation:** make ambiguity explicit in diagnostics and prefer descendant+lease evidence over silent sentinel bypass.

---

## Acceptance Criteria

This plan is complete when Ralph can, in automated tests and live logs, reliably distinguish:

1. **Exited child** — process/label gone or explicit terminal ack / valid artifact seen; parent stops waiting.
2. **Hung child** — process/label may still exist, but progress/heartbeat lease expired; parent escalates with explicit stale reason.
3. **Still-working child** — progress or heartbeat keeps renewing within TTL; parent keeps waiting and explains why.

And when the old failure mode — “process exists, therefore maybe still working” — is no longer enough on its own to hold `WAITING_ON_CHILD` open.
