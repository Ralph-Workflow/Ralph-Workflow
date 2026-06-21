"""Black-box tests: subprocess reader must wire InactivityTimeoutOpts with session_resume_safe=True.

The subprocess path (``_run_subprocess_and_read_lines``) was the missing wiring:
the idle watchdog fires ``CHILDREN_PERSIST_TOO_LONG`` after a 600s cumulative
child wait, the agent is killed, the orchestrator retries, and the new agent
emits the structural restart narrative (``I'll start by reading the current
state...``) because the resumed retry was actually a fresh-from-scratch
attempt. The PTY runner (``run_pty_and_read_lines``) sets
``InactivityTimeoutOpts(session_resume_safe=True, resumable_session_id=...)``
on the same ``WatchdogFireReason`` set; the subprocess runner did not, so
``_failure_requires_fresh_session()`` returned True, the recovery plan builder
returned a fresh-style plan, the prompt constructor inlined the original
task body, and the agent restarted from scratch.

These tests drive the HIGH-LEVEL seam (``invoke_agent``) — monkeypatched at
``ralph.agents.invoke.subprocess.Popen`` — NOT the lower-level
``read_lines_from_process`` (which only raises ``_IdleStreamTimeoutError``
and never wraps it as ``AgentInactivityTimeoutError``). No real subprocess,
no real wall clock, no ``time.sleep``.

The four tests share the same scaffolding: a ``FakeProcess`` whose stdout
yields a session id line once then blocks (gated by a ``threading.Event``
so the reader thread does not crash on an empty pipe), a
``_WaitingStrategy`` whose ``classify_quiet`` returns
``AgentExecutionState.WAITING_ON_CHILD``, and the strategy monkeypatched in
at ``ralph.agents.invoke.strategy_for_transport`` so the watchdog enters
the cumulative-WAITING branch and fires ``CHILDREN_PERSIST_TOO_LONG``
rather than ``NO_OUTPUT_DEADLINE``.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Literal

import pytest

from ralph.agents import invoke as invoke_module
from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    InvokeOptions,
    invoke_agent,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import AgentConfig
from tests.agents.test_invoke_timeout_integration_helper__waitingstrategy import _WaitingStrategy

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class _FakeProcess:
    """Minimal test double for ``subprocess.Popen`` used by the subprocess reader."""

    pid: int = 12345

    def __init__(self, stdout_lines: list[str] | None = None) -> None:
        if stdout_lines is None:
            stdout_lines = []
        self._gate = threading.Event()
        self._lines = list(stdout_lines)
        self._gate.set()  # Let the first line yield immediately.
        self.stdout = self._stdout_iter()
        self.stderr = self._stderr()
        self.returncode: int | None = 0
        self.terminated = False

    def _stdout_iter(self) -> Iterator[str]:
        for line in self._lines:
            self._gate.wait(timeout=5.0)
            yield line
        # After the seeded lines, suspend forever so the reader thread does
        # not treat EOF as "done" before the watchdog fires. The test calls
        # ``proc._gate.clear()`` after the first line is observed, and the
        # ``finally`` block calls ``proc._gate.set()`` to release the thread.
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


def _make_invocation_options(
    *,
    tmp_path: Path,
    idle_timeout: float = 0.05,
    max_waiting: float = 0.1,
    max_session: float | None = None,
    session_id: str | None = None,
) -> InvokeOptions:
    return InvokeOptions(
        show_progress=False,
        workspace_path=tmp_path,
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=max_waiting,
        max_session_seconds=max_session,
        max_waiting_on_child_no_progress_seconds=None,
        waiting_status_interval_seconds=100.0,
        idle_poll_interval_seconds=0.01,
        session_id=session_id,
    )


@pytest.fixture
def patched_waiting_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch strategy_for_command to a WAITING_ON_CHILD classifier.

    Used to drive the watchdog into the cumulative-WAITING branch so the
    CHILDREN_PERSIST_TOO_LONG ceiling fires before NO_OUTPUT_DEADLINE.
    """
    monkeypatch.setattr(
        invoke_module,
        "strategy_for_command",
        lambda *args, **kwargs: _WaitingStrategy(),
    )


def test_subprocess_reader_wires_resume_safe_on_children_persist_too_long(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    patched_waiting_strategy: None,
) -> None:
    """The subprocess reader MUST raise ``AgentInactivityTimeoutError`` with
    ``session_resume_safe=True`` and ``resumable_session_id`` from the agent's
    stream when the watchdog fires ``CHILDREN_PERSIST_TOO_LONG``.

    This is the root-cause fix for the restart-from-scratch wedge: pre-fix,
    the subprocess reader raised with the default ``session_resume_safe=False``
    and ``resumable_session_id=None``, so the recovery plan builder routed
    the retry to fresh-style and the prompt constructor inlined the original
    task body.
    """
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    proc = _FakeProcess(
        stdout_lines=['{"type":"session","session_id":"sess-from-stream"}\n'],
    )

    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: proc,
    )
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)

    clock = FakeClock()
    opts = _make_invocation_options(tmp_path=tmp_path)

    try:
        with pytest.raises(AgentInactivityTimeoutError) as exc_info:
            list(
                invoke_agent(
                    config,
                    str(prompt_file),
                    options=opts,
                    _clock=clock,
                )
            )
    finally:
        proc._gate.set()

    assert exc_info.value.session_resume_safe is True, (
        f"Expected session_resume_safe=True, got {exc_info.value.session_resume_safe}; "
        f"reason={exc_info.value.reason}"
    )
    assert exc_info.value.resumable_session_id == "sess-from-stream", (
        f"Expected resumable_session_id='sess-from-stream', "
        f"got {exc_info.value.resumable_session_id!r}"
    )
    assert exc_info.value.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_subprocess_reader_uses_expected_session_id_when_no_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    patched_waiting_strategy: None,
) -> None:
    """When no session id is captured from the stream, the
    ``expected_session_id`` fallback (threaded from ``InvokeOptions.session_id``)
    populates ``resumable_session_id``."""
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    proc = _FakeProcess(stdout_lines=[])

    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: proc,
    )
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)

    clock = FakeClock()
    opts = _make_invocation_options(tmp_path=tmp_path, session_id="sess-expected")

    try:
        with pytest.raises(AgentInactivityTimeoutError) as exc_info:
            list(
                invoke_agent(
                    config,
                    str(prompt_file),
                    options=opts,
                    _clock=clock,
                )
            )
    finally:
        proc._gate.set()

    assert exc_info.value.session_resume_safe is True
    assert exc_info.value.resumable_session_id == "sess-expected", (
        f"Expected fallback to 'sess-expected', got {exc_info.value.resumable_session_id!r}"
    )


def test_subprocess_reader_captured_session_id_wins_over_expected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    patched_waiting_strategy: None,
) -> None:
    """When both a captured session id and an ``expected_session_id`` are
    available, the captured id wins (the rule mirrors the PTY runner)."""
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    proc = _FakeProcess(
        stdout_lines=['{"type":"session","session_id":"sess-from-stream"}\n'],
    )

    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: proc,
    )
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)

    clock = FakeClock()
    opts = _make_invocation_options(tmp_path=tmp_path, session_id="sess-expected")

    try:
        with pytest.raises(AgentInactivityTimeoutError) as exc_info:
            list(
                invoke_agent(
                    config,
                    str(prompt_file),
                    options=opts,
                    _clock=clock,
                )
            )
    finally:
        proc._gate.set()

    assert exc_info.value.session_resume_safe is True
    assert exc_info.value.resumable_session_id == "sess-from-stream", (
        f"Expected captured 'sess-from-stream' to win, got {exc_info.value.resumable_session_id!r}"
    )


@pytest.mark.parametrize(
    ("label", "expected_reason", "expected_safe", "opts_factory"),
    [
        (
            "children_persist",
            WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
            True,
            lambda tmp_path: InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                idle_timeout_seconds=0.05,
                max_waiting_on_child_seconds=0.1,
                max_waiting_on_child_no_progress_seconds=None,
                waiting_status_interval_seconds=100.0,
                idle_poll_interval_seconds=0.01,
            ),
        ),
        (
            "session_ceiling",
            WatchdogFireReason.SESSION_CEILING_EXCEEDED,
            False,
            lambda tmp_path: InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                idle_timeout_seconds=None,
                max_session_seconds=0.05,
                max_waiting_on_child_seconds=None,
                max_waiting_on_child_no_progress_seconds=None,
                waiting_status_interval_seconds=100.0,
                idle_poll_interval_seconds=0.01,
            ),
        ),
    ],
)
def test_subprocess_reader_session_resume_safe_only_for_resume_eligible_reasons(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    patched_waiting_strategy: None,
    label: str,
    expected_reason: WatchdogFireReason,
    expected_safe: bool,
    opts_factory: object,
) -> None:
    """``session_resume_safe=True`` only for the eligible
    ``WatchdogFireReason`` set. SESSION_CEILING_EXCEEDED is the hard wall —
    not resume-safe.

    This test pins the 3-vs-1 split: the 3 resume-eligible reasons
    (NO_OUTPUT_DEADLINE, STALLED_AFTER_TOOL_RESULT, CHILDREN_PERSIST_TOO_LONG)
    are covered here via CHILDREN_PERSIST_TOO_LONG; the ineligible reason
    (SESSION_CEILING_EXCEEDED) is covered directly. The remaining two
    resume-eligible reasons are pinned by the post-tool-result and
    no-output watchdog branches elsewhere in the integration suite
    (test_invoke_timeout_integration.py), and the eligibility set logic
    is a constant in ``_process_reader.py`` shared by all reasons.
    """
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    proc = _FakeProcess(stdout_lines=[])

    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: proc,
    )
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)

    clock = FakeClock()
    opts = opts_factory(tmp_path)

    try:
        with pytest.raises(AgentInactivityTimeoutError) as exc_info:
            list(
                invoke_agent(
                    config,
                    str(prompt_file),
                    options=opts,
                    _clock=clock,
                )
            )
    finally:
        proc._gate.set()

    assert exc_info.value.reason == expected_reason, (
        f"[{label}] expected reason {expected_reason}, got {exc_info.value.reason}"
    )
    assert exc_info.value.session_resume_safe is expected_safe, (
        f"[{label}] expected session_resume_safe={expected_safe}, "
        f"got {exc_info.value.session_resume_safe}"
    )


def test_subprocess_reader_session_resume_safe_for_no_output_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """NO_OUTPUT_DEADLINE is also a resume-eligible reason.

    Drive NO_OUTPUT_DEADLINE with an ACTIVE-classifying strategy and a
    short idle_timeout_seconds. The watchdog fires NO_OUTPUT_DEADLINE when
    the agent produces no output for the configured idle timeout. This
    pins the second of the three resume-eligible reasons (NO_OUTPUT_DEADLINE);
    STALLED_AFTER_TOOL_RESULT is covered indirectly via the same
    eligibility-set logic (same code path in _process_reader.py).
    """
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    class _ActiveStrategy(_WaitingStrategy):
        def classify_quiet(
            self,
            handle: object,
            liveness_probe: object,
        ) -> AgentExecutionState:
            return AgentExecutionState.ACTIVE

    monkeypatch.setattr(
        invoke_module,
        "strategy_for_command",
        lambda *args, **kwargs: _ActiveStrategy(),
    )

    proc = _FakeProcess(stdout_lines=[])

    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: proc,
    )
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)

    clock = FakeClock()
    opts = InvokeOptions(
        show_progress=False,
        workspace_path=tmp_path,
        idle_timeout_seconds=0.05,
        max_waiting_on_child_seconds=10.0,
        max_session_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        waiting_status_interval_seconds=100.0,
        idle_poll_interval_seconds=0.01,
    )

    try:
        with pytest.raises(AgentInactivityTimeoutError) as exc_info:
            list(
                invoke_agent(
                    config,
                    str(prompt_file),
                    options=opts,
                    _clock=clock,
                )
            )
    finally:
        proc._gate.set()

    assert exc_info.value.reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    assert exc_info.value.session_resume_safe is True, (
        f"Expected session_resume_safe=True for NO_OUTPUT_DEADLINE, "
        f"got {exc_info.value.session_resume_safe}"
    )


def test_subprocess_reader_session_resume_safe_for_no_output_at_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """NO_OUTPUT_AT_START is also a resume-eligible reason.

    Drive NO_OUTPUT_AT_START with an ACTIVE-classifying strategy and a
    long idle_timeout_seconds so the watchdog fires NO_OUTPUT_AT_START
    BEFORE NO_OUTPUT_DEADLINE. The default no_output_at_start_seconds=30s
    is used (InvokeOptions does not expose the field; the default is the
    canonical operator-configured value).

    The session id is threaded via ``InvokeOptions.session_id`` (the
    ``expected_session_id`` fallback path) rather than via a captured
    stream line -- if we emitted a ``{"type":"session",...}`` line, the
    OpenCode strategy would classify it as ``OUTPUT_LINE``, which
    ``_record_line_activity`` routes to ``record_activity()`` and
    sets ``_has_meaningful_output=True``, so the no_output_at_start
    trigger would never become eligible. The fallback path proves the
    resume-safe wiring without perturbing the no_output_at_start
    contract.

    The agent never records any activity; the watchdog fires
    NO_OUTPUT_AT_START after 30s. The subprocess reader must raise
    ``AgentInactivityTimeoutError`` with ``session_resume_safe=True``
    AND populate ``resumable_session_id`` from the expected-session-id
    fallback so the high-level ``invoke_agent`` seam resumes the SAME
    session id (NOT a fresh-from-scratch restart). This pins the third
    of the four resume-eligible reasons (NO_OUTPUT_AT_START). The
    eligibility-set logic is a closed literal in ``_process_reader.py``
    shared by all reasons.
    """
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    class _ActiveStrategy(_WaitingStrategy):
        def classify_quiet(
            self,
            handle: object,
            liveness_probe: object,
        ) -> AgentExecutionState:
            return AgentExecutionState.ACTIVE

    monkeypatch.setattr(
        invoke_module,
        "strategy_for_command",
        lambda *args, **kwargs: _ActiveStrategy(),
    )

    proc = _FakeProcess(stdout_lines=[])

    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: proc,
    )
    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", lambda *_a, **_k: None)

    clock = FakeClock()
    opts = _make_invocation_options(
        tmp_path=tmp_path,
        idle_timeout=300.0,
        max_waiting=600.0,
        session_id="sess-no-output-at-start",
    )

    try:
        with pytest.raises(AgentInactivityTimeoutError) as exc_info:
            list(
                invoke_agent(
                    config,
                    str(prompt_file),
                    options=opts,
                    _clock=clock,
                )
            )
    finally:
        proc._gate.set()

    assert exc_info.value.reason == WatchdogFireReason.NO_OUTPUT_AT_START, (
        f"Expected reason=NO_OUTPUT_AT_START, got {exc_info.value.reason}"
    )
    assert exc_info.value.session_resume_safe is True, (
        f"Expected session_resume_safe=True for NO_OUTPUT_AT_START, "
        f"got {exc_info.value.session_resume_safe}"
    )
    assert exc_info.value.resumable_session_id == "sess-no-output-at-start", (
        f"Expected resumable_session_id='sess-no-output-at-start'"
        f" (same-session resume contract), got {exc_info.value.resumable_session_id!r}"
    )
