# ADR-0001: Interrupt subsystem architecture

* Status: Accepted
* Date: 2026-06-12

## Context

The interrupt subsystem must coordinate three concerns under the
constraint of a single shared SIGINT handler:

1. **Black-box testability.** Every test must run in well under one
   second of wall-clock time, must not depend on real subprocess
   lifetime, real OS signals, or `time.sleep`, and must not require
   a running asyncio event loop. The whole interrupt pipeline
   (record → graceful shutdown → block → escalate → force-exit) must
   be deterministically reproducible.
2. **No time / no sleep in core logic.** Core logic is testable only
   if the only thing it reads from the outside world is its
   injected dependencies. Production code reads the wall clock and
   blocks; the dispatcher's core logic must read a clock seam and
   block through a sleep seam.
3. **Single source of truth for live processes.** Two paths route
   SIGINT to Ralph: the sync `handle_keyboard_interrupt` in
   `ralph.pipeline._runner_interrupt` and the asyncio
   `install_signal_handlers` in `ralph.interrupt.asyncio_bridge`.
   Both must observe the SAME set of live tracked processes, kill
   them with the SAME strategy, and exit with the SAME code
   (`INTERRUPT_EXIT_CODE = 130`). A second `pids` set on the bridge
   would diverge the two paths and reintroduce the orphan-process
   bug the AC-01 fix closed.

The committed production code (`fix(interrupt): restore two-quick
ctrl-c via pgids`) is the result. This ADR documents the four
architectural decisions that produced it.

## Decision

### D1. InterruptController / InterruptDispatcher split

The interrupt subsystem is split into two layers:

* `InterruptController` (in `ralph/interrupt/controller.py`) is the
  pure coordination dataclass. It owns the four callables the
  interrupt needs: `record_interrupt`, `stop_connectivity`,
  `shutdown_all` / `shutdown_all_for_label`, `kill_process_group`,
  and `hard_exit`. It has no idea how long shutdown takes, whether
  the process manager is draining, or whether the call is in a
  sync vs asyncio context.
* `InterruptDispatcher` (in `ralph/interrupt/dispatcher.py`) is the
  seam. It wraps a controller, owns the timing policy (poll
  interval, hard-kill budget, kill label), and adds the
  `force_exit` idempotency the controller does not have. The
  dispatcher's `begin_interrupt(block=True, grace_period_s=...)`
  blocks until `process_manager.list_active()` is empty OR the
  grace deadline elapses; on deadline expiration with records
  still active, it escalates via `self.force_exit(bridge_pgids=...)`.

The split exists so the sync `handle_keyboard_interrupt` and the
asyncio `install_signal_handlers` both route through the same
factory (`dispatcher_from_process_manager`) and so the timing
policy and idempotency live in ONE place rather than being
duplicated in each path.

Reference: ralph/interrupt/dispatcher.py:189-211 (InterruptDispatcher
dataclass), ralph/interrupt/dispatcher.py:228-242 (begin_interrupt),
ralph/interrupt/dispatcher.py:244-269 (_wait_for_list_active_empty
escalation), ralph/interrupt/dispatcher.py:272-306 (force_exit with
the `_force_exit_called` idempotency guard).

Test pin: tests/test_interrupt_dispatcher.py::test_dispatcher_force_exit_is_idempotent_on_repeat_bridge_pids

### D2. Clock + sleep seams on the dispatcher

The dispatcher's `_wait_for_list_active_empty` and
`run_early_escalation_poll` both loop with a `sleep(...)` call
and a `clock() < deadline` check. Both reads of wall-clock time
and both sleep calls are routed through two dataclass fields —
`clock: Callable[[], float]` and `sleep: Callable[[float], None]`
— that default to `time.monotonic` and `time.sleep` in production
but are overridable in tests. The `__post_init__` validator
rejects a `clock` that does not return a `float`.

The seams exist so a 60-second escalation can be exercised in
fractions of a millisecond in a unit test (the test's `_FakeClock`
advances via explicit `clock.advance(s)` calls, and the test's
`fake_sleep` advances the clock on every call). They are also the
canonical injection point for any future test that needs to
assert deadline-bounded behavior.

Reference: ralph/interrupt/dispatcher.py:206-208 (clock and sleep
fields on the dataclass), ralph/interrupt/dispatcher.py:215-219
(`__post_init__` validates the clock returns float).

Test pin: tests/test_interrupt_dispatcher.py::test_dispatcher_uses_injected_clock_for_block_wait_deadline

### D3. PGID routing via pm.list_active() instead of a parallel pids set on the bridge

The asyncio bridge's `_second_sigint` handler reads
`process_manager.list_active()` and forwards the still-active
records' PGIDs to `dispatcher.force_exit(bridge_pgids=...)`. It
does NOT maintain a parallel `pids` set on `SignalBridge`.

The single source of truth is the `ProcessManager`. Maintaining
a parallel set on the bridge would mean: (a) the bridge must
subscribe to the manager's process events to populate the set;
(b) any records added by code that does not emit those events
would be invisible to the bridge; (c) the sync and async paths
would diverge because the sync path uses the manager directly
while the async path uses the bridge's set. The PGID-routing
choice closes all three gaps at once. The kill is sent to the
PGID (not the PID) so the entire process group dies, matching
the real-world `os.killpg` semantics the manager's
`kill_process_group` seam already exposes.

Reference: ralph/interrupt/asyncio_bridge.py:149-152
(`_second_sigint` reads `list_active()` and forwards PGIDs).

Test pin: tests/test_asyncio_bridge_install_signal_handlers.py::test_second_sigint_force_kills_uses_pgid_not_pid

### D4. handle_keyboard_interrupt_at_cli propagates dispatcher failures (Strategy A)

The canonical CLI-level entry point
`handle_keyboard_interrupt_at_cli` in `ralph.interrupt.dispatcher`
does NOT wrap its dispatcher call in `try/except`. It builds a
dispatcher via `dispatcher_from_process_manager`, calls
`begin_interrupt(grace_period_s=..., block=True)`, and returns
the exit code.

The two CLI catch sites
(`ralph/cli/main.py:_run_pipeline` and
`ralph/cli/commands/run.py:run`) each wrap the helper call in
their own `try/except` and emit the verbatim "Interrupt dispatcher
failed during outer CLI catch" / "during CLI catch" log warning.
This is Strategy A (propagation, not absorption). Strategy B
(absorb at the helper) would emit a single log line and hide
the dispatcher failure from the outer catch, making the
catch-block behavior untestable in isolation. Strategy A
preserves bit-for-bit production output (each CLI catch
emits its own historically-anchored message) and lets the
canonical `block=True` contract be black-box tested in
isolation.

Reference: ralph/interrupt/dispatcher.py:394-422
(handle_keyboard_interrupt_at_cli), ralph/cli/main.py:934-960
(_run_pipeline catch), ralph/cli/commands/run.py:429-434
(run catch).

Test pin: tests/test_interrupt_cli_helper.py::test_handle_keyboard_interrupt_at_cli_propagates_dispatcher_failures

## Consequences

The four decisions above produce the following durable rules for
future contributors. A contributor who is about to add a third
bridge path (e.g. for a new event loop) MUST follow them.

1. **Canonical wiring seam: `dispatcher_from_process_manager`.**
   Every new caller that needs a dispatcher — sync, async, or
   otherwise — MUST go through this factory. Bypassing it means
   duplicating the timing policy and idempotency logic.

2. **Canonical CLI catch seam: `handle_keyboard_interrupt_at_cli`.**
   Every new CLI entry point that catches a `KeyboardInterrupt`
   MUST route through this helper. Bypassing it means the
   two-`try/except` log-line contract is not preserved.

3. **Single source of truth for live processes:
   `process_manager.list_active()`.** The bridge, the dispatcher,
   and any future caller MUST read live processes from the
   `ProcessManager` — never from a parallel set on a bridge or a
   dataclass. A parallel set reintroduces the orphan-process bug
   the AC-01 fix closed.

4. **Forbidden patterns:**

   * `os._exit(...)` outside `force_exit`. The dispatcher is the
     only place that exits the process; `os._exit` in a CLI catch
     or in a controller path skips cleanup.
   * A parallel `pids` set on `SignalBridge` (or any future
     bridge). The bridge is a thin signal-counter only; the
     `ProcessManager` owns process state.
   * `time.sleep(...)` or `time.monotonic()` in non-`subprocess_e2e`
     tests. All timing in unit / integration tests MUST go
     through the dispatcher's `clock` and `sleep` seams. The
     audit module `ralph.testing.audit_test_policy` enforces this.
   * Bare `# noqa` or blanket `# type: ignore`. The audit
     modules `ralph.testing.audit_lint_bypass` and
     `ralph.testing.audit_typecheck_bypass` enforce specific
     codes and policy-compliant reason markers.

5. **Long-running-task contract.** When the first SIGINT's
   executor body (or the dispatcher's `begin_interrupt` body) is
   mid-flight, a second SIGINT must still synchronously escalate
   via `force_exit`; the executor body, when it eventually runs,
   must NOT call `force_exit` a second time (the
   `_force_exit_called` guard fires). When `begin_interrupt`'s
   body itself is slow, the dispatcher's
   `_wait_for_list_active_empty` escalation still fires after
   the body returns and the new grace deadline elapses; the
   `hard_exit` callable is called exactly once across the
   two SIGINTs and the body's eventual completion
   (idempotency survives body delay). These two invariants are
   pinned by the long-running-task black-box tests added in
   the same commit as this ADR.
