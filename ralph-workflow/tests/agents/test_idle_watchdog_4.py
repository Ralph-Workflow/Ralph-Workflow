"""Black-box tests for activity-aware watchdog teardown wiring.

These tests lock the AC-08 contract that ``check_process_result`` calls
``teardown_subtree`` on every error/crash path before raising
``AgentInvocationError``. Each test uses in-memory fakes and mocks; no real
subprocess, no real wall-clock waits, no real I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy
from ralph.agents.idle_watchdog._evidence_tier import ChannelName
from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
from ralph.agents.invoke import CompletionCheckOptions, check_process_result
from ralph.agents.invoke._errors import AgentInvocationError
from ralph.agents.invoke._workspace_change_classifier import (
    DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS,
    WorkspaceChangeClassifier,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.teardown import teardown_subtree

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.process.manager import ManagedProcess


IDLE_TIMEOUT = 0.1
DRAIN_WINDOW = 0.0
MAX_WAITING = 10.0
ACTIVITY_TTL = 30.0


def _make_watchdog(
    *,
    idle_timeout: float = IDLE_TIMEOUT,
    drain_window: float = DRAIN_WINDOW,
    max_waiting: float = MAX_WAITING,
    max_session: float | None = None,
    activity_ttl: float | None = ACTIVITY_TTL,
    start: float = 0.0,
    suspect: float | None = None,
    no_progress_ceiling: float | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    config = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=drain_window,
        max_waiting_on_child_seconds=max_waiting,
        max_session_seconds=max_session,
        suspect_waiting_on_child_seconds=suspect,
        max_waiting_on_child_no_progress_seconds=no_progress_ceiling,
        activity_evidence_ttl_seconds=activity_ttl,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=start)
    return IdleWatchdog(config, clock), clock


def _default_classifier() -> WorkspaceChangeClassifier:
    """Return the conservative default classifier used in production."""
    return WorkspaceChangeClassifier(weights=dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS))


class _FakeHandle:
    """Minimal handle stand-in for completion-check tests."""

    def __init__(self, returncode: int, pid: int = 42) -> None:
        self.returncode = returncode
        self.pid = pid
        self.stderr = _FakeStderr("boom")


class _FakeStderr:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text


class _CompletionEnforcingStrategy:
    """Strategy that supports completion enforcement and reports incomplete exit."""

    def classify_exit(
        self,
        handle: object,
        signals: object,
        liveness_probe: object | None = None,
    ) -> AgentExecutionState:
        del handle, signals, liveness_probe
        return AgentExecutionState.RESUMABLE_CONTINUE

    def supports_session_continuation(self) -> bool:
        return False

    def supports_completion_enforcement(self) -> bool:
        return True


def test_check_process_result_nonzero_exit_calls_teardown_subtree(tmp_path: Path) -> None:
    """When the host process exits with a non-zero code, ``check_process_result``
    calls ``teardown_subtree`` on the handle's PID before raising
    ``AgentInvocationError``.

    This locks the AC-08 error/crash path: subagents must not outlive the
    phase even when the host crashes.
    """
    handle = _FakeHandle(returncode=1, pid=1234)

    with (
        patch("ralph.agents.invoke._completion.teardown_subtree") as mock_teardown,
        pytest.raises(AgentInvocationError),
    ):
        check_process_result(
            cast("ManagedProcess", handle),
            "test-agent",
            parsed_output=[],
            check_options=None,
        )

    mock_teardown.assert_called_once_with(1234)
    # Also verify the real function can still be imported (sanity).
    assert teardown_subtree is not None


def test_check_process_result_missing_completion_evidence_calls_teardown_subtree(
    tmp_path: Path,
) -> None:
    """When a completion-enforcing agent exits without required completion
    evidence, ``check_process_result`` calls ``teardown_subtree`` before
    raising ``AgentInvocationError``.
    """
    handle = _FakeHandle(returncode=0, pid=5678)
    options = CompletionCheckOptions(
        execution_strategy=_CompletionEnforcingStrategy(),
        workspace_path=tmp_path,
        policy=TimeoutPolicy(idle_timeout_seconds=None),
    )

    with (
        patch("ralph.agents.invoke._completion.teardown_subtree") as mock_teardown,
        pytest.raises(AgentInvocationError),
    ):
        check_process_result(
            cast("ManagedProcess", handle),
            "test-agent",
            parsed_output=[],
            check_options=options,
        )

    mock_teardown.assert_called_once_with(5678)


def test_check_process_result_error_path_does_not_mutate_clock() -> None:
    """The error-path teardown call must not advance the injected FakeClock.

    This is a regression guard: the completion check should be a pure
    decision + side-effect (teardown), not a wall-clock wait.
    """
    clock = FakeClock(start=0.0)
    handle = _FakeHandle(returncode=1, pid=9999)

    with (
        patch("ralph.agents.invoke._completion.teardown_subtree"),
        pytest.raises(AgentInvocationError),
    ):
        check_process_result(
            cast("ManagedProcess", handle),
            "test-agent",
            parsed_output=[],
            check_options=None,
            _clock=clock,
        )

    assert clock.monotonic() == 0.0


# ---------------------------------------------------------------------------
# Per-channel recorder invariants
# ---------------------------------------------------------------------------


def test_record_mcp_tool_call_does_not_mutate_last_activity() -> None:
    """``record_mcp_tool_call`` updates the mcp_tool channel timestamp but
    does NOT touch ``_last_activity`` (the stdout baseline)."""
    wd, clock = _make_watchdog()
    wd.record_activity()
    clock.advance(1.0)
    baseline = wd._last_activity
    now = clock.monotonic()
    wd.record_mcp_tool_call(now=now)
    assert wd._last_activity == baseline
    assert wd._last_mcp_tool_call_at == now


def test_record_subagent_work_does_not_mutate_last_activity() -> None:
    """``record_subagent_work`` updates the subagent channel timestamp but
    does NOT touch ``_last_activity`` (the stdout baseline)."""
    wd, clock = _make_watchdog()
    wd.record_activity()
    clock.advance(1.0)
    baseline = wd._last_activity
    now = clock.monotonic()
    wd.record_subagent_work(now=now)
    assert wd._last_activity == baseline
    assert wd._last_subagent_progress_at == now


def test_record_workspace_event_weight_zero_does_not_advance_channel() -> None:
    """A workspace event with ``weight=0.0`` is short-circuited: the channel
    timestamp, counter, and kind counter are NOT updated."""
    wd, _ = _make_watchdog()
    wd.record_workspace_event(kind=WorkspaceChangeKind.OTHER, weight=0.0)
    assert wd.workspace_kind_counts == {}
    summary = wd.last_evidence_summary(0.0)
    workspace_summary = summary.channels[-1]
    assert workspace_summary.channel_name == ChannelName.WORKSPACE
    assert workspace_summary.last_at is None


def test_record_workspace_event_source_weight_advances_channel() -> None:
    """A workspace event with ``kind=SOURCE`` and ``weight=1.0`` advances the
    workspace channel timestamp and the per-kind source counter, and the
    channel summary reports ``can_defer=True``."""
    wd, clock = _make_watchdog()
    now = clock.monotonic()
    wd.record_workspace_event(kind=WorkspaceChangeKind.SOURCE, weight=1.0, now=now)
    assert wd.workspace_kind_counts == {"source": 1}
    assert wd._last_workspace_event_at == now
    summary = wd.last_evidence_summary(now)
    workspace_summary = summary.channels[-1]
    assert workspace_summary.channel_name == ChannelName.WORKSPACE
    assert workspace_summary.last_at == now
    assert workspace_summary.can_defer is True


# ---------------------------------------------------------------------------
# (s) Subagent description sanitization (security: control-char + payload scrub)
# ---------------------------------------------------------------------------


def test_record_subagent_work_description_strips_control_characters() -> None:
    """``record_subagent_work`` strips control characters from ``description``.

    Newlines, CRs, tabs, and other C0 control codes from a raw provider
    line must NOT survive into the operator-visible
    ``subagent_activity`` field. A leaked newline would split a single
    waiting-status line into many rows in the UI.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="hello\nworld\rmore\tchars\x00\x01")
    assert "\n" not in (wd._last_subagent_progress_description or "")
    assert "\r" not in (wd._last_subagent_progress_description or "")
    assert "\t" not in (wd._last_subagent_progress_description or "")
    assert "\x00" not in (wd._last_subagent_progress_description or "")
    assert "\x01" not in (wd._last_subagent_progress_description or "")
    assert wd._last_subagent_progress_description == "helloworldmorechars"


def test_record_subagent_work_description_strips_ansi_escapes() -> None:
    """ANSI CSI / OSC sequences are stripped from the description.

    A raw provider line like ``"\\x1b[31mred\\x1b[0m text"`` must lose
    the ESC bytes so the terminal does not interpret the colour code
    inside operator-visible waiting-status output.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="\x1b[31mhello\x1b[0m world")
    stored = wd._last_subagent_progress_description
    assert stored is not None
    assert "\x1b" not in stored
    # The text content survives; only the escape introducer is removed.
    assert "hello" in stored
    assert "world" in stored


def test_record_subagent_work_description_redacts_tool_arguments() -> None:
    """A description that contains ``"arguments": "<secret>"`` has the value redacted."""
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='tool {"arguments": "secret_payload_value"}')
    stored = wd._last_subagent_progress_description or ""
    assert "secret_payload_value" not in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_sensitive_paths() -> None:
    """A description mentioning sensitive roots (/etc, /proc, /sys, /root, ~/.ssh)
    has the sensitive marker replaced with ``<redacted>`` so the path does
    not leak verbatim into operator-visible text."""
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="reading /etc/passwd then /proc/self/maps")
    stored = wd._last_subagent_progress_description or ""
    assert "/etc/" not in stored
    assert "/proc/" not in stored
    assert stored.count("<redacted>") >= 2


def test_record_subagent_work_description_redacts_bearer_token() -> None:
    """A description containing ``Authorization: Bearer <token>`` has the
    bearer prefix redacted (the marker reveals the leak category without
    echoing the token)."""
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="hdr: Authorization: Bearer abc123token")
    stored = wd._last_subagent_progress_description or ""
    assert "Bearer" not in stored


def test_record_subagent_work_description_redacts_private_key_marker() -> None:
    """A description containing a PEM ``-----BEGIN ... PRIVATE KEY-----``
    marker is redacted to prevent private-key fragments leaking into logs."""
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="key fragment -----BEGIN RSA PRIVATE KEY----- data")
    stored = wd._last_subagent_progress_description or ""
    assert "PRIVATE KEY" not in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_truncates_to_200_chars() -> None:
    """A description longer than 200 chars after sanitization is truncated."""
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="a" * 500)
    stored = wd._last_subagent_progress_description or ""
    assert len(stored) == 200


def test_record_subagent_work_description_only_whitespace_stores_empty() -> None:
    """A description that is purely whitespace (after sanitization) stores
    an empty string so the subscriber does not render ``subagent=``."""
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="   \n\n  \t\t  ")
    stored = wd._last_subagent_progress_description
    assert stored == ""


def test_record_subagent_work_description_none_leaves_field_none() -> None:
    """``record_subagent_work(description=None)`` does NOT update the
    stored description (preserves the prior value). This is the
    legacy behavior used by tests that exercise the channel
    timestamp without supplying a description."""
    wd, _clock = _make_watchdog()
    prior = wd._last_subagent_progress_description
    wd.record_subagent_work(description=None)
    assert wd._last_subagent_progress_description == prior


def test_record_subagent_work_description_does_not_mutate_last_activity() -> None:
    """``record_subagent_work(description=...)`` does NOT touch the
    stdout baseline ``_last_activity``. The description update is a
    presentation-layer concern; it must not perturb the idle deadline."""
    wd, _clock = _make_watchdog()
    wd.record_activity()
    _clock.advance(1.0)
    baseline = wd._last_activity
    wd.record_subagent_work(description="anything goes here")
    assert wd._last_activity == baseline
