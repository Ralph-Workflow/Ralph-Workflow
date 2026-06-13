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

### D7. Long-running-body contract for the SYNC entry point

The user's reported scenario is "broken at times... when the task is
long running". A long-running agent's `begin_interrupt` body may take
many seconds to return (the `grace_period_s` plus the
`_wait_for_list_active_empty` block). When the user hits `Ctrl+C` a
second time while the first-SIGINT body is still in flight, the
contract is:

1. The second-SIGINT handler synchronously escalates via
   `dispatcher.force_exit(bridge_pgids=...)`. The dispatcher's
   `_force_exit_called` flag is set synchronously by the
   `force_exit` body (before the `hard_exit` callable runs), so
   any subsequent `force_exit` call is a no-op.
2. The first-SIGINT body, when it eventually completes, must NOT
   call `force_exit` a second time. The body's eventual
   completion is the entry point's `_begin_interrupt` returning
   normally (the `try/except Exception` block logged the recovery
   warning, if any, and the thread set `interrupt_done`). The
   dispatcher's `force_exit` was already invoked by the second
   SIGINT; the `_force_exit_called` guard fires on any subsequent
   call from any path (the body, the early-escalation poll, or
   an explicit second invocation by the test).
3. The `_force_exit_called` idempotency guard is the canonical
   surface that closes the double-invocation gap. Without it,
   the second-SIGINT force-exit AND the body's eventual
   escalation would each invoke `hard_exit(130)`, terminating
   the process twice (or re-entering the SIGINT handler with a
   second `os._exit` that defeats the cleanup the first one
   already did).

The contract is pinned by the SYNC-path black-box test
`tests/test_runner_interrupt.py::test_second_sigint_during_first_sigint_interrupt_thread`,
which uses a `_SlowBeginDispatcher` wrapper to block the
`begin_interrupt` body on a `threading.Event` so the test can
interleave the second SIGINT with the in-flight body without
depending on real wall-clock waits. The test asserts:

* `force_exit` is invoked exactly once (the second-SIGINT
  handler is the only caller).
* The dispatcher's `_force_exit_called` flag is set to `True`
  synchronously by the second-SIGINT handler.
* The interrupt thread's eventual completion does NOT call
  `hard_exit` a second time.
* An explicit `dispatcher.force_exit(bridge_pgids=[9999])`
  after the body completed does NOT add a second `hard_exit`
  call (the idempotency guard fires).

The test wall-clock is < 200ms; the dispatcher's
`poll_interval_s=0.001` override keeps the entry-point busy-wait
exiting in <1ms per cycle. The test does NOT use `time.sleep`
and does NOT depend on real subprocesses, real signals, or real
wall-clock waits.

Reference: `tests/test_runner_interrupt.py::test_second_sigint_during_first_sigint_interrupt_thread`
(the new test pin) and
`ralph/interrupt/dispatcher.py:281-303` (the `force_exit` method
with the `_force_exit_called` idempotency guard).

The corresponding async-path test pin
(`tests/test_asyncio_bridge_install_signal_handlers.py::test_second_sigint_during_first_sigint_executor_body`)
covers the same contract for the asyncio entry point. The two
tests together close the long-running-body contract for both
production paths.

### D8. `run_shutdown_block` as the canonical shutdown-block seam

The first-SIGINT shutdown block — `begin_interrupt` plus the
early-escalation poll in a daemon thread, plus a
`threading.Thread.join` with a bounded timeout — is
byte-for-byte equivalent in two places:

* `ralph/pipeline/_runner_interrupt.py:_begin_interrupt` (the
  SYNC entry point).
* `ralph/interrupt/asyncio_bridge.py:_shutdown_block` (the
  asyncio entry point).

The two call sites were duplicated and could drift. A new
module-level helper `run_shutdown_block` in
`ralph/interrupt/dispatcher.py` is the canonical seam; both
call sites route through it. The body is byte-for-byte
equivalent to the prior inline bodies. The 7th architectural
seam is `error_log_message`:

* The SYNC path passes
  `"Interrupt controller raised during KeyboardInterrupt"`
  (preserved for bit-for-bit production log output).
* The asyncio path passes `"Interrupt shutdown block raised"`
  (preserved for the same reason).

No other difference exists between the two paths. The helper
also extracts the `join_timeout_s` bound (default
`INTERRUPT_HARD_KILL_BUDGET_SECONDS + 0.1`) into a single
named parameter so the bound is a single source of truth
rather than a duplicated literal at the two call sites.

Reference: `ralph/interrupt/dispatcher.py:run_shutdown_block`
(the canonical seam), `ralph/pipeline/_runner_interrupt.py:_begin_interrupt`
(the SYNC call site), and `ralph/interrupt/asyncio_bridge.py:_shutdown_block`
(the asyncio call site).

The helper is added to `__all__` so `from
ralph.interrupt.dispatcher import *` exposes it. The existing
black-box tests in `tests/test_runner_interrupt.py` and
`tests/test_asyncio_bridge_install_signal_handlers.py` continue
to pass against the helper because they invoke the recorded
`begin_interrupt` + `run_early_escalation_poll` calls (the
helper's body) and assert on the recorded call shape, not on
the call site.

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

5c. **Long-running-body idempotency (D7 + D8).** The sync and async
    paths both route through `run_shutdown_block` (D8); the
    second-SIGINT force-kill handler is the same code in both paths
    (the installed `force_kill_handler` callable from
    `install_force_kill_handler`); the interrupt thread's eventual
    completion cannot trigger a second `force_exit` because the
    dispatcher's `_force_exit_called` flag is set synchronously by
    the second handler (D7). A future contributor who adds a third
    bridge path MUST route through `run_shutdown_block` and MUST
    rely on the `_force_exit_called` guard for double-invocation
    safety; bypassing either breaks the long-running-body contract
    pinned by the SYNC and async test pins.

5d. **Import-time invariant pin.** `INTERRUPT_EXIT_CODE = 130`,
    `INTERRUPT_HARD_KILL_BUDGET_SECONDS = 1.5`, and
    `SIGINT_PROGRESS_POLL_INTERVAL_SECONDS = 0.2` are pinned by
    `if`/`raise RuntimeError` invariants in
    `ralph/interrupt/controller.py` and
    `ralph/interrupt/dispatcher.py` (immune to `python -O`). A future
    change that picks a different in-range value (the existing
    range checks at the top of `dispatcher.py` accept any value in
    the valid range) is caught at import time, so the change is
    noticed by CI before the production code can run with the
    wrong budget. The public-surface tests in
    `tests/test_interrupt_constants.py` re-derive the constants
    from both import paths and assert the dispatcher's
    `force_exit` references the `INTERRUPT_EXIT_CODE` symbol
    (not a hardcoded literal) and that `run_early_escalation_poll`
    uses the dataclass field `self.hard_kill_budget_s` (not a
    module-level constant).
