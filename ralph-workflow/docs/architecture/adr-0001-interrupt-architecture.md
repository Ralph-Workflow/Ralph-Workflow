# ADR-0001: Interrupt subsystem architecture

* Status: Accepted
* Date: 2026-06-12 (updated 2026-06-13)

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

### D5. handle_keyboard_interrupt process_manager and poll_interval_s seams

The sync entry point `handle_keyboard_interrupt` in
`ralph/pipeline/_runner_interrupt.py` is the seam a real Ctrl+C reaches
inside the pipeline loop. Before the refactor it had a 50 ms hard-coded
busy-wait (`interrupt_done.wait(timeout=0.05)` at
`ralph/pipeline/_runner_interrupt.py:116`), caught `BaseException`
(anti-pattern at line 103), depended on the global
`get_process_manager()` singleton at line 74, and silently ignored
`monitor_stop` when a pre-built dispatcher was passed (the
`dispatcher_from_process_manager` call at lines 77-81 only forwarded
`stop_connectivity` when `dispatcher is None`). The refactor closes
all four gaps with the minimum seam surface:

- **`process_manager: ProcessManager | None = None`** replaces the
  hard-coded `get_process_manager()` call. Production callers omit the
  kwarg and the singleton is used; tests inject a fake.
- **`poll_interval_s: float = 0.05`** replaces the literal `0.05` in
  the busy-wait. The default is unchanged from production behavior;
  tests inject `0.001` so the busy-wait returns in <1ms.
- **RuntimeError guard** at the top of the function body (lines 67-72)
  raises `RuntimeError` when both `dispatcher` and `monitor_stop` are
  passed. The prior silent-ignore was a footgun that hid a real
  contract violation.
- **Exception not BaseException** (line 103) — see D6 below.

Clock and sleep seams are **NOT** added to the entry point because
the entry point only uses `threading.Event` coordination, not
`time.monotonic()` or `time.sleep()`. The dispatcher's clock and
sleep seams (D2) are sufficient for the timing tests.

The contract for every test in
`tests/test_runner_interrupt.py` and
`tests/pipeline/test_run_loop_interrupt.py` is:

- Pass `signal_getter` and `signal_setter` fakes so the test does NOT
  touch the real process SIGINT handler.
- Build a real factory-built `InterruptDispatcher` via
  `_build_dispatcher(poll_interval_s=0.001, process_manager=manager)`
  (the `poll_interval_s=0.001` override is MANDATORY so the
  dispatcher's per-iteration sleep is <1ms; the default
  `SIGINT_PROGRESS_POLL_INTERVAL_SECONDS = 0.2` would add 200ms to
  each test wall-clock because `run_early_escalation_poll` does
  `self.sleep(self.poll_interval_s)` before the first liveness check).
- Wrap the real dispatcher in a thin `_RecordingDispatcher` class
  (local to the test file) that records `begin_calls` and `poll_calls`
  (the real frozen `InterruptDispatcher` at
  `ralph/interrupt/dispatcher.py:188` cannot be monkey-patched).
- For tests that simulate a second SIGINT, inject a recording
  `hard_exit` callable so the test process is not killed by
  `os._exit(130)`.

Reference: `ralph/pipeline/_runner_interrupt.py:32-46` (new
signature), `ralph/pipeline/_runner_interrupt.py:67-72` (RuntimeError
guard), `ralph/pipeline/_runner_interrupt.py:73-81` (process_manager
injection), `ralph/pipeline/_runner_interrupt.py:116` (poll_interval_s
busy-wait).

Test pin: `tests/test_runner_interrupt.py::test_handle_keyboard_interrupt_uses_injected_poll_interval_for_polling`

### D6. Exception not BaseException in handle_keyboard_interrupt

The recovery block in `_begin_interrupt` at
`ralph/pipeline/_runner_interrupt.py:103` uses `except Exception` (not
`except BaseException`). The prior `except BaseException` silently
swallowed `KeyboardInterrupt` and `SystemExit` that must propagate so
the user's Ctrl+C still kills a hung process; the local
`interrupt_error` list is the recovery surface for non-fatal
dispatcher failures (`RuntimeError`, `ValueError`, `OSError`, etc.).

A single broken dispatch logs a warning via the existing
`"Interrupt controller raised during KeyboardInterrupt"` message at
`ralph/pipeline/_runner_interrupt.py:103` and recovers the SIGINT
handler instead of swallowing the user's Ctrl+C. Any future dispatcher
call that raises a `BaseException` subclass that is NOT an
`Exception` subclass (e.g. `KeyboardInterrupt`, `SystemExit`) is NOT
caught and propagates to `threading.excepthook`; this is the correct
behavior because the dispatcher is the only place that exits the
process (per D4), so a dispatcher raising `KeyboardInterrupt` is
itself a bug that should crash, not be recovered.

The discriminator for the `Exception`-not-`BaseException` change is
`tests/test_runner_interrupt.py::test_handle_keyboard_interrupt_propagates_baseexception_from_dispatcher`
(test 6): a custom `BaseException` subclass that is NOT an
`Exception` subclass (`_NotAnException`) is raised from the
dispatcher's `begin_interrupt`. The NEW `except Exception` code does
NOT catch it, so it propagates to `threading.excepthook`; the OLD
`except BaseException` code would silently catch it and the excepthook
would never fire. Test 2 (RuntimeError) is INSUFFICIENT: `RuntimeError`
is caught by both `except BaseException` and `except Exception`, so
test 2 would pass against the unmodified code.

Reference: `ralph/pipeline/_runner_interrupt.py:103` (the `except
Exception` clause with the inline policy-source comment).

Test pin: `tests/test_runner_interrupt.py::test_handle_keyboard_interrupt_propagates_baseexception_from_dispatcher`

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

5a. **Entry-point minimum seam surface.** `handle_keyboard_interrupt`
    exposes exactly two new kwargs (`process_manager`,
    `poll_interval_s`) and one `RuntimeError` guard. Clock and sleep
    seams are NOT added because the entry point only uses
    `threading.Event` coordination. Tests MUST build a real
    factory-built `InterruptDispatcher` with `poll_interval_s=0.001`
    override and wrap it in a thin `_RecordingDispatcher` class for
    call-argument capture, so the per-test wall-clock stays under
    100ms without monkeypatching `INTERRUPT_HARD_KILL_BUDGET_SECONDS`
    or `SIGINT_PROGRESS_POLL_INTERVAL_SECONDS`.

5b. **Exception not BaseException.** Any future dispatcher call that
    raises a `BaseException` subclass that is NOT an `Exception`
    subclass (`KeyboardInterrupt`, `SystemExit`) propagates to
    `threading.excepthook` and crashes the background thread; this is
    the correct behavior because the dispatcher is the only place
    that exits the process (per D4), so a dispatcher raising
    `KeyboardInterrupt` is itself a bug that should crash, not be
    recovered. The recovery branch's `"Interrupt controller raised
    during KeyboardInterrupt"` warning fires only for `Exception`
    subclasses (non-fatal dispatcher failures).
