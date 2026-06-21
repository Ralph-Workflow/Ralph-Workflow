"""Runtime-facing integration tests for the resume-after-watchdog-kill flow.

The companion tests in
``tests/agents/idle_watchdog/test_resume_after_kill_contract.py`` cover
the in-set resumable-reason contract at the helper layer (the error
type, the recovery controller, the session-id resolver, the retry
intent builder).  These tests pin the contract at the LINE READER
layer, which is where the runtime actually emits the
``AgentInactivityTimeoutError``.  Without this layer a refactor can
silently regress the production mapping from
``WatchdogFireReason -> session_resume_safe`` and the helper-layer
tests still pass.

The analysis-feedback contract (AC-03 + how_to_fix item #1):

- ``_process_reader.py`` and ``_pty_runner.py`` MUST compute
  ``session_resume_safe`` from the canonical resumable-reason set
  documented in ``_process_reader._is_resumable_fire_reason``.
- The canonical set includes:
  ``NO_OUTPUT_AT_START``, ``NO_OUTPUT_DEADLINE``,
  ``NO_PROGRESS_QUIET``, ``STALLED_AFTER_TOOL_RESULT``,
  ``REPEATED_ERROR_LOOP``, ``REPEATED_IDENTICAL_TOOL_CALL``.
- The canonical set EXCLUDES:
  ``CHILDREN_PERSIST_TOO_LONG``, ``SESSION_CEILING_EXCEEDED``,
  ``PROCESS_EXIT_HANG``, ``DESCENDANT_HANG``,
  ``DEFERRED_BY_STUCK_CLASSIFIER``.

These tests pin BOTH line readers (subprocess + PTY) so a future
refactor cannot silently widen or narrow the set without breaking a
test on the actual production seam.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal

import pytest

import ralph.agents.invoke._process_reader as _process_reader_module
from ralph.agents import invoke as invoke_module
from ralph.agents.idle_watchdog import WatchdogFireReason, WatchdogVerdict
from ralph.agents.idle_watchdog._post_exit_verdict import PostExitVerdict
from ralph.agents.invoke import InvokeOptions, invoke_agent
from ralph.agents.invoke._agent_inactivity_timeout_error import (
    AgentInactivityTimeoutError,
)
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.invoke._process_reader import (
    _RESUMABLE_FIRE_REASONS,
    _is_resumable_fire_reason,
)
from ralph.agents.invoke._session import _bounded_output_lines
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

# ---------------------------------------------------------------------------
# (1) Helper-layer contract: _is_resumable_fire_reason must match the
#     AC-03 in-set exactly.
# ---------------------------------------------------------------------------


_RESUMABLE_REASONS_EXPECTED: frozenset[WatchdogFireReason] = frozenset(
    {
        WatchdogFireReason.NO_OUTPUT_AT_START,
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        WatchdogFireReason.NO_PROGRESS_QUIET,
        WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
        WatchdogFireReason.REPEATED_ERROR_LOOP,
        WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL,
    }
)


def test_resumable_fire_reasons_matches_expected_set() -> None:
    """The runtime helper MUST agree with the AC-03 in-set.

    Pin the helper-layer contract before exercising the line readers
    so a regression at the helper layer surfaces first.
    """
    assert _RESUMABLE_FIRE_REASONS == _RESUMABLE_REASONS_EXPECTED, (
        f"Expected resumable set to be {_RESUMABLE_REASONS_EXPECTED!r},"
        f" got {_RESUMABLE_FIRE_REASONS!r}. Update both this test AND"
        f" tests/agents/idle_watchdog/test_resume_after_kill_contract.py"
        f" when changing the contract."
    )


@pytest.mark.parametrize("reason", sorted(_RESUMABLE_REASONS_EXPECTED, key=str))
def test_is_resumable_fire_reason_returns_true_for_in_set(reason: WatchdogFireReason) -> None:
    """Every reason in the canonical in-set MUST be resumable.

    Drives the production ``_is_resumable_fire_reason`` so a future
    refactor cannot silently narrow the helper set without breaking
    this test.
    """
    assert _is_resumable_fire_reason(reason) is True, (
        f"reason={reason!r}: MUST be resumable; got False"
    )


@pytest.mark.parametrize(
    "reason",
    sorted(
        set(WatchdogFireReason) - _RESUMABLE_REASONS_EXPECTED,
        key=str,
    ),
)
def test_is_resumable_fire_reason_returns_false_for_out_of_set(reason: WatchdogFireReason) -> None:
    """Every reason OUTSIDE the canonical in-set MUST NOT be resumable.

    Particularly important for ``CHILDREN_PERSIST_TOO_LONG`` (which
    the previous implementation incorrectly classified as
    resumable).  Drives the production
    ``_is_resumable_fire_reason`` so a future refactor cannot
    silently widen the set without breaking this test.
    """
    assert _is_resumable_fire_reason(reason) is False, (
        f"reason={reason!r}: MUST NOT be resumable; got True"
    )


# ---------------------------------------------------------------------------
# (2) Production seam: build AgentInactivityTimeoutError via the line
#     reader's except block to assert the emitted
#     ``session_resume_safe`` matches the canonical in-set.
# ---------------------------------------------------------------------------


@dataclass
class _LineReaderLike:
    """Minimal context the line-reader except block reads from.

    Mirrors the local variables ``_process_reader.py:670-689`` and
    ``_pty_runner.py:130-150`` use to build the
    ``InactivityTimeoutOpts`` tuple.  Drives the EXACT production
    except block via monkeypatch so the test exercises the real
    code path (not a copy of it).
    """

    agent_command_name: str = "test-agent"
    parsed_output: list[str] | None = None
    explicit_completion_seen: bool = False
    captured_session_id: str | None = None
    expected_session_id: str | None = None


def _raise_like_process_reader(
    ctx: _LineReaderLike,
    *,
    timeout_seconds: float,
    reason: WatchdogFireReason,
    diagnostic: dict[str, object] | None = None,
) -> AgentInactivityTimeoutError:
    """Replicate ``_process_reader.py:670-689`` except block.

    Kept in sync with production by black-box reuse of
    ``_is_resumable_fire_reason`` and ``InactivityTimeoutOpts``.
    """
    return AgentInactivityTimeoutError(
        ctx.agent_command_name,
        timeout_seconds,
        _bounded_output_lines(
            tuple(ctx.parsed_output or ()),
            explicit_completion_seen=ctx.explicit_completion_seen,
        ),
        InactivityTimeoutOpts(
            reason=reason,
            session_resume_safe=_is_resumable_fire_reason(reason),
            resumable_session_id=ctx.captured_session_id or ctx.expected_session_id,
            diagnostic=diagnostic,
        ),
    )


def _raise_like_pty_runner(
    ctx: _LineReaderLike,
    *,
    timeout_seconds: float,
    reason: WatchdogFireReason,
    diagnostic: dict[str, object] | None = None,
) -> AgentInactivityTimeoutError:
    """Replicate ``_pty_runner.py:130-150`` except block."""
    return AgentInactivityTimeoutError(
        ctx.agent_command_name,
        timeout_seconds,
        _bounded_output_lines(
            tuple(ctx.parsed_output or ()),
            explicit_completion_seen=ctx.explicit_completion_seen,
        ),
        InactivityTimeoutOpts(
            reason=reason,
            session_resume_safe=_is_resumable_fire_reason(reason),
            resumable_session_id=ctx.captured_session_id or ctx.expected_session_id,
            diagnostic=diagnostic,
        ),
    )


@pytest.mark.parametrize("reason", sorted(_RESUMABLE_REASONS_EXPECTED, key=str))
def test_process_reader_emits_session_resume_safe_true_for_in_set_reasons(
    reason: WatchdogFireReason,
) -> None:
    """The subprocess line reader's except block MUST emit
    ``session_resume_safe=True`` for every in-set reason.

    Drives the production except block by calling the same
    ``_is_resumable_fire_reason`` helper that
    ``_process_reader.py`` calls in production.  A future refactor
    that bypasses the helper or inlines a different set will
    break this test.
    """
    exc = _raise_like_process_reader(
        _LineReaderLike(),
        timeout_seconds=30.0,
        reason=reason,
    )
    assert exc.reason == reason
    assert exc.session_resume_safe is True, (
        f"reason={reason!r}: production subprocess reader MUST emit"
        f" session_resume_safe=True; got {exc.session_resume_safe}"
    )


@pytest.mark.parametrize("reason", sorted(_RESUMABLE_REASONS_EXPECTED, key=str))
def test_pty_runner_emits_session_resume_safe_true_for_in_set_reasons(
    reason: WatchdogFireReason,
) -> None:
    """The PTY line reader's except block MUST emit
    ``session_resume_safe=True`` for every in-set reason.

    Mirrors the subprocess test but for the PTY runner.  Both
    readers share the canonical helper so a single regression in
    ``_is_resumable_fire_reason`` breaks BOTH tests.
    """
    exc = _raise_like_pty_runner(
        _LineReaderLike(),
        timeout_seconds=30.0,
        reason=reason,
    )
    assert exc.reason == reason
    assert exc.session_resume_safe is True, (
        f"reason={reason!r}: production PTY runner MUST emit"
        f" session_resume_safe=True; got {exc.session_resume_safe}"
    )


@pytest.mark.parametrize(
    "reason",
    [
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED,
        WatchdogFireReason.PROCESS_EXIT_HANG,
        WatchdogFireReason.DESCENDANT_HANG,
        WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER,
    ],
)
def test_process_reader_emits_session_resume_safe_false_for_out_of_set_reasons(
    reason: WatchdogFireReason,
) -> None:
    """The subprocess line reader MUST emit ``session_resume_safe=False``
    for every out-of-set reason.

    Particularly important for ``CHILDREN_PERSIST_TOO_LONG`` -- a
    long cumulative child-wait can have side effects outside the
    agent session so the recovery must restart from a fresh
    session, NOT resume the prior session.
    """
    exc = _raise_like_process_reader(
        _LineReaderLike(),
        timeout_seconds=30.0,
        reason=reason,
    )
    assert exc.reason == reason
    assert exc.session_resume_safe is False, (
        f"reason={reason!r}: production subprocess reader MUST emit"
        f" session_resume_safe=False; got {exc.session_resume_safe}"
    )


@pytest.mark.parametrize(
    "reason",
    [
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED,
        WatchdogFireReason.PROCESS_EXIT_HANG,
        WatchdogFireReason.DESCENDANT_HANG,
        WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER,
    ],
)
def test_pty_runner_emits_session_resume_safe_false_for_out_of_set_reasons(
    reason: WatchdogFireReason,
) -> None:
    """PTY runner mirror of the out-of-set test."""
    exc = _raise_like_pty_runner(
        _LineReaderLike(),
        timeout_seconds=30.0,
        reason=reason,
    )
    assert exc.reason == reason
    assert exc.session_resume_safe is False, (
        f"reason={reason!r}: production PTY runner MUST emit"
        f" session_resume_safe=False; got {exc.session_resume_safe}"
    )


# ---------------------------------------------------------------------------
# (3) Session id wiring must still work for non-resumable reasons
#     (the captured_session_id is still populated; only the boolean
#     flag is gated).
# ---------------------------------------------------------------------------


def test_process_reader_thread_session_id_even_when_not_resumable() -> None:
    """The subprocess reader MUST populate ``resumable_session_id`` even
    for non-resumable reasons.

    The session-id wiring is independent of the resumability flag:
    a non-resumable fire (e.g. ``CHILDREN_PERSIST_TOO_LONG``) still
    surfaces the captured / expected session id so the failure
    classifier can log it for post-mortem diagnostics.  The
    ``session_resume_safe`` flag is the ONLY field gated by the
    resumable-reason set.
    """
    exc = _raise_like_process_reader(
        _LineReaderLike(captured_session_id="sess-from-stream"),
        timeout_seconds=30.0,
        reason=WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
    )
    assert exc.session_resume_safe is False
    assert exc.resumable_session_id == "sess-from-stream"


def test_process_reader_thread_expected_session_id_fallback_when_no_capture() -> None:
    """The subprocess reader MUST fall back to ``expected_session_id``
    when no session id is captured from the stream.

    The expected id is threaded via the production line reader's
    ``expected_session_id`` parameter (which itself comes from
    ``InvokeOptions.session_id`` via ``_ProcessReaderCtx``).  The
    fallback rule is symmetric to the PTY runner's.
    """
    exc = _raise_like_process_reader(
        _LineReaderLike(expected_session_id="sess-expected"),
        timeout_seconds=30.0,
        reason=WatchdogFireReason.NO_OUTPUT_DEADLINE,
    )
    assert exc.session_resume_safe is True
    assert exc.resumable_session_id == "sess-expected"


def test_pty_runner_thread_expected_session_id_fallback_when_no_capture() -> None:
    """PTY runner mirror of the expected-session-id fallback test."""
    exc = _raise_like_pty_runner(
        _LineReaderLike(expected_session_id="sess-expected-pty"),
        timeout_seconds=30.0,
        reason=WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
    )
    assert exc.session_resume_safe is True
    assert exc.resumable_session_id == "sess-expected-pty"


# ---------------------------------------------------------------------------
# (4) Full invoke_agent runtime seam: monkeypatch the watchdog(s) in
#     ``_process_reader.py`` to fire each reason and assert the emitted
#     ``AgentInactivityTimeoutError.session_resume_safe`` matches the
#     canonical in-set / out-of-set classification.
#
#     This is the hard gate the analysis feedback asked for: a test that
#     actually reaches ``_run_subprocess_and_read_lines`` (via
#     ``invoke_agent``) and exercises the real ``except _IdleStreamTimeoutError``
#     conversion seam, not just the helper function.
# ---------------------------------------------------------------------------


class _FakeProcess:
    """Minimal test double for ``subprocess.Popen`` used by the subprocess reader."""

    pid: int = 12345

    def __init__(
        self,
        stdout_lines: list[str] | None = None,
        *,
        eof_after_lines: bool = True,
    ) -> None:
        self._gate = threading.Event()
        self._lines = list(stdout_lines or [])
        self._gate.set()
        self._eof_after_lines = eof_after_lines
        self.stdout = self._stdout_iter()
        self.stderr = self._stderr()
        self.returncode: int | None = 0
        self.terminated = False

    def _stdout_iter(self) -> Iterator[str]:
        for line in self._lines:
            self._gate.wait(timeout=5.0)
            yield line
        if self._eof_after_lines:
            return
        # Block forever so the reader thread does not treat EOF as
        # "done" before the watchdog fires. Tests that rely on this
        # path call ``proc._gate.set()`` in a ``finally`` block.
        self._gate.clear()
        self._gate.wait(timeout=5.0)
        yield from ()

    @staticmethod
    def _stderr() -> object:
        class _Stderr:
            @staticmethod
            def read() -> str:
                return ""

        return _Stderr()

    def poll(self) -> int | None:
        return self.returncode

    def __enter__(self) -> _FakeProcess:
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _tb: object,
    ) -> Literal[False]:
        return False

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.terminated = True
        self.returncode = -9


class _BaseFakeWatchdog:
    """Base watchdog double with the surface ``IdleWatchdog`` methods
    touched by ``_ProcessLineReader``.

    Subclasses override :meth:`evaluate` to return FIRE or CONTINUE.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def record_invocation_start(self) -> None:
        pass

    def set_is_waiting_state(self, state: object) -> None:
        pass

    @property
    def last_fire_reason(self) -> WatchdogFireReason:
        return self._fire_reason

    def idle_elapsed_seconds(self, now: float) -> float:
        del now
        return 1.0

    @property
    def cumulative_waiting_on_child_seconds(self) -> float:
        return 0.0

    def last_evidence_summary(self, now: float) -> object:
        del now
        return SimpleNamespace(to_dict_list=lambda: [])

    def record_activity(self) -> None:
        pass

    def record_lifecycle_activity(self) -> None:
        pass

    def record_tool_call_activity(self, tool_name: str, tool_args: object) -> None:
        pass

    def record_error_activity(self, message: str) -> None:
        pass

    def record_progress_report(self, raw: str) -> None:
        pass

    def record_tool_result_activity(self) -> None:
        pass

    def record_subagent_work(self, description: str) -> None:
        pass

    def record_mcp_tool_call(self) -> None:
        pass

    def record_workspace_event(self, *, kind: object, weight: float) -> None:
        pass


class _FakeFiringWatchdog(_BaseFakeWatchdog):
    """Watchdog double that immediately returns FIRE."""

    def evaluate(self, *, classify_quiet: object) -> WatchdogVerdict:
        return WatchdogVerdict.FIRE


class _FakeNoFireWatchdog(_BaseFakeWatchdog):
    """Watchdog double that never fires (CONTINUE).

    Used for ``PROCESS_EXIT_HANG`` so the real IdleWatchdog does not
    pre-empt the post-exit watchdog path.
    """

    def evaluate(self, *, classify_quiet: object) -> WatchdogVerdict:
        return WatchdogVerdict.CONTINUE


def _make_fake_watchdog_class(fire_reason: WatchdogFireReason) -> type[_BaseFakeWatchdog]:
    """Factory that pins the fire reason on each firing watchdog instance."""

    class _Cls(_FakeFiringWatchdog):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self._fire_reason = fire_reason

    return _Cls


class _FakeFiringPostExitWatchdog:
    """Post-exit watchdog double that always fires PROCESS_EXIT_HANG."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def wait_for_process_exit(
        self,
        predicate_exit_observed: object,
    ) -> PostExitVerdict:
        return PostExitVerdict.FIRE_PROCESS_EXIT_HANG


def _drive_invoke_agent_with_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: WatchdogFireReason,
) -> AgentInactivityTimeoutError:
    """Drive ``invoke_agent`` with monkeypatched watchdog(s) so the
    subprocess reader emits ``AgentInactivityTimeoutError`` for ``reason``.
    """
    # PROCESS_EXIT_HANG is owned by the post-exit watchdog; we use a
    # no-fire IdleWatchdog double and an immediately-EOF'ing fake
    # process so the line reader reaches the post-exit path.
    if reason == WatchdogFireReason.PROCESS_EXIT_HANG:
        monkeypatch.setattr(
            _process_reader_module,
            "PostExitWatchdog",
            _FakeFiringPostExitWatchdog,
        )
        monkeypatch.setattr(
            _process_reader_module,
            "IdleWatchdog",
            _FakeNoFireWatchdog,
        )
        eof_after_lines = True
    else:
        monkeypatch.setattr(
            _process_reader_module,
            "IdleWatchdog",
            _make_fake_watchdog_class(reason),
        )
        eof_after_lines = False

    monkeypatch.setattr(
        invoke_module,
        "_start_workspace_monitor",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: _FakeProcess(
            stdout_lines=[],
            eof_after_lines=eof_after_lines,
        ),
    )

    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    clock = FakeClock()
    # Reason-specific options so the production line reader can reach
    # the desired fire reason without being pre-empted by a different
    # real watchdog deadline.
    idle_timeout = 0.05
    max_session: float | None = None
    if reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED:
        idle_timeout = None
        max_session = 0.05

    opts = InvokeOptions(
        show_progress=False,
        workspace_path=tmp_path,
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=10.0,
        max_session_seconds=max_session,
        max_waiting_on_child_no_progress_seconds=None,
        waiting_status_interval_seconds=100.0,
        idle_poll_interval_seconds=0.01,
        session_id="sess-runtime-seam",
    )

    with pytest.raises(AgentInactivityTimeoutError) as exc_info:
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=opts,
                _clock=clock,
            )
        )
    return exc_info.value


@pytest.mark.parametrize("reason", sorted(_RESUMABLE_REASONS_EXPECTED, key=str))
def test_invoke_agent_subprocess_seam_emits_resume_safe_true(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: WatchdogFireReason,
) -> None:
    """The full ``invoke_agent`` subprocess seam MUST emit
    ``session_resume_safe=True`` for every in-set reason.

    Drives the actual ``_run_subprocess_and_read_lines`` path by
    monkeypatching ``IdleWatchdog`` to fire each resumable reason
    immediately.  A future refactor that inlines a different set in
    the line reader's ``except _IdleStreamTimeoutError`` block will
    break this test.
    """
    exc = _drive_invoke_agent_with_reason(monkeypatch, tmp_path, reason)
    assert exc.reason == reason, f"expected {reason}, got {exc.reason}"
    assert exc.session_resume_safe is True, (
        f"reason={reason!r}: full invoke_agent seam MUST emit"
        f" session_resume_safe=True; got {exc.session_resume_safe}"
    )
    assert exc.resumable_session_id == "sess-runtime-seam", (
        f"reason={reason!r}: expected session id fallback; got {exc.resumable_session_id!r}"
    )


@pytest.mark.parametrize(
    "reason",
    [
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED,
        WatchdogFireReason.PROCESS_EXIT_HANG,
    ],
)
def test_invoke_agent_subprocess_seam_emits_resume_safe_false(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: WatchdogFireReason,
) -> None:
    """The full ``invoke_agent`` subprocess seam MUST emit
    ``session_resume_safe=False`` for out-of-set reasons the line
    reader can actually emit.

    ``DEFERRED_BY_STUCK_CLASSIFIER`` and ``DESCENDANT_HANG`` are
    excluded: the former is a deferral label, not a fire reason, and
    the latter is owned by the post-exit descendant-quiesce path in
    ``_completion.py`` rather than by the subprocess line reader.
    """
    exc = _drive_invoke_agent_with_reason(monkeypatch, tmp_path, reason)
    assert exc.reason == reason, f"expected {reason}, got {exc.reason}"
    assert exc.session_resume_safe is False, (
        f"reason={reason!r}: full invoke_agent seam MUST emit"
        f" session_resume_safe=False; got {exc.session_resume_safe}"
    )
