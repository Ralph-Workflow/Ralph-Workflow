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
        stuck_job_sub_ceiling_seconds=None,
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


def test_record_subagent_work_description_redacts_lowercase_bearer_token() -> None:
    """A description containing ``authorization: bearer <token>`` (all lowercase)
    has the bearer prefix redacted. This is the analysis-feedback reproducer:
    a case-sensitive regex previously missed lowercase authorization headers
    and let ``SECRET123`` leak into the operator-visible
    ``subagent_activity`` field on ``WaitingStatusEvent``.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="hdr: authorization: bearer SECRET123")
    stored = wd._last_subagent_progress_description or ""
    assert "SECRET123" not in stored, (
        f"lowercase bearer token 'SECRET123' must NOT leak, got: {stored!r}"
    )
    assert "bearer" not in stored, f"lowercase 'bearer' marker must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_uppercase_bearer_token() -> None:
    """A description containing ``AUTHORIZATION: BEARER <token>`` (all uppercase)
    has the bearer prefix redacted. Mirrors the lowercase regression test to
    pin both ends of the case-insensitive contract.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="hdr: AUTHORIZATION: BEARER UPPERSECRET")
    stored = wd._last_subagent_progress_description or ""
    assert "UPPERSECRET" not in stored, (
        f"uppercase bearer token 'UPPERSECRET' must NOT leak, got: {stored!r}"
    )
    assert "BEARER" not in stored, f"uppercase 'BEARER' marker must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_mixed_case_bearer_token() -> None:
    """A description containing ``AuThOrIzAtIoN: BeArEr <token>`` (mixed case)
    has the bearer prefix redacted. Locks down the case-insensitive contract
    for arbitrary mixed-case header variants.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="hdr: AuThOrIzAtIoN: BeArEr MiXeDcAsE")
    stored = wd._last_subagent_progress_description or ""
    assert "MiXeDcAsE" not in stored, (
        f"mixed-case bearer token 'MiXeDcAsE' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored


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


# ---------------------------------------------------------------------------
# (t) Escaped-quote redaction regression (analysis-feedback: leaked suffix text)
# ---------------------------------------------------------------------------


def test_record_subagent_work_description_redacts_well_formed_escaped_quote() -> None:
    """A well-formed JSON ``"arguments": "secret\\"tail"`` is fully redacted.

    The strict regex ``"[^"\\n]*"`` would stop at the first
    unescaped quote and leave ``tail"`` visible. The sanitizer's
    multi-pass redaction (JSON parser + strict regex + fallback
    regex) handles the escaped quote and redacts the entire value.
    """
    wd, _clock = _make_watchdog()
    # Source: {"arguments":"secret\"tail"}  (the \" is a real escape)
    wd.record_subagent_work(description='{"arguments":"secret\\"tail"}')
    stored = wd._last_subagent_progress_description or ""
    assert "tail" not in stored, f"escaped-quote suffix 'tail' must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_malformed_inner_quote() -> None:
    """A malformed JSON with an UNESCAPED inner quote is fully redacted.

    The input ``{"arguments":"secret"tail"}`` is not valid JSON; the
    JSON parser rejects it and the fallback regex
    ``_SENSITIVE_MARKER_FALLBACK_RE`` matches the marker, opening
    quote, and everything up to the next JSON boundary character
    so the trailing ``tail"}`` never reaches operator-visible output.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"arguments":"secret"tail"}')
    stored = wd._last_subagent_progress_description or ""
    assert "tail" not in stored, f"malformed-JSON suffix 'tail' must be redacted, got: {stored!r}"
    assert "secret" not in stored, (
        f"malformed-JSON prefix 'secret' must be redacted, got: {stored!r}"
    )
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_malformed_args_inner_quote() -> None:
    """A malformed JSON ``args`` value is fully redacted.

    The analysis feedback confirmed that ``args`` was missing from the
    fallback regex, so ``{"args":"secret"tail"}`` leaked the suffix.
    This test pins the fix: the fallback regex MUST treat ``args`` the
    same as ``arguments`` and redact the entire malformed value.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"args":"secret"tail"}')
    stored = wd._last_subagent_progress_description or ""
    assert "tail" not in stored, (
        f"malformed-JSON args suffix 'tail' must be redacted, got: {stored!r}"
    )
    assert "secret" not in stored, (
        f"malformed-JSON args prefix 'secret' must be redacted, got: {stored!r}"
    )
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_escaped_prompt_content() -> None:
    """An escaped-quote ``prompt`` / ``content`` value is fully redacted.

    Regression for the analysis-feedback case: a raw provider line
    like ``{"prompt": "say \\"hi\\" please"}`` must NOT leak the
    ``"hi" please"`` suffix into operator-visible output.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"prompt": "say \\"hi\\" please"}')
    stored = wd._last_subagent_progress_description or ""
    assert "please" not in stored, (
        f"escaped-quote suffix 'please' must be redacted, got: {stored!r}"
    )
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_escaped_file_path() -> None:
    """An escaped-quote ``file_path`` value is fully redacted.

    ``{"file_path": "/etc/secret\\"name"}`` must NOT leak the
    ``name"`` suffix into operator-visible output.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"file_path": "/etc/secret\\"name"}')
    stored = wd._last_subagent_progress_description or ""
    assert "name" not in stored, (
        f"escaped-quote file_path suffix 'name' must be redacted, got: {stored!r}"
    )
    assert "/etc/" not in stored, f"sensitive path /etc/ must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_bearer_token_with_quotes() -> None:
    """A bearer token header with a quoted suffix is fully redacted.

    The provider format ``Authorization: Bearer abc"def`` (raw
    provider line, not JSON-wrapped) must NOT leak the ``def``
    suffix. The path regex
    ``_SENSITIVE_PATH_TOKEN_RE`` matches
    ``Authorization\\s*:\\s*Bearer[^\\n]*`` and consumes the rest of
    the line, so the quoted suffix is redacted.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='Authorization: Bearer abc"def')
    stored = wd._last_subagent_progress_description or ""
    assert "def" not in stored, f"bearer token suffix 'def' must be redacted, got: {stored!r}"
    assert "Bearer" not in stored, f"bearer token prefix 'Bearer' must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_input_field_with_quotes() -> None:
    """An ``input`` field with escaped quotes is fully redacted.

    ``{"input": "echo \\"hello\\" world"}`` must NOT leak the
    ``world"`` suffix into operator-visible output.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"input": "echo \\"hello\\" world"}')
    stored = wd._last_subagent_progress_description or ""
    assert "world" not in stored, f"input field suffix 'world' must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_repeated_escaped_quotes() -> None:
    """Multiple escaped quotes in a single value are all redacted.

    ``{"content": "say \\"hi\\" then \\"bye\\" now"}`` must NOT
    leak any of ``hi``, ``bye``, or ``now`` into operator-visible
    output.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"content": "say \\"hi\\" then \\"bye\\" now"}')
    stored = wd._last_subagent_progress_description or ""
    for forbidden in ("hi", "bye", "now"):
        assert forbidden not in stored, (
            f"escaped-quote value {forbidden!r} must be redacted, got: {stored!r}"
        )
    assert "<redacted>" in stored


def test_record_subagent_work_description_reproducer_no_leaked_suffix() -> None:
    """Reproducer for the analysis-feedback reproducer.

    The exact input from the analysis feedback (``{"arguments":"secret\\"tail"}``)
    must produce sanitized output that does NOT contain the
    forbidden ``tail`` suffix. This is the contract that motivated
    the fallback regex fix.
    """
    wd, _clock = _make_watchdog()
    # Reproducer line verbatim from the analysis feedback.
    wd.record_subagent_work(description='{"arguments":"secret\\"tail"}')
    stored = wd._last_subagent_progress_description or ""
    # The reproducer must NOT print leaked suffix text.
    assert "tail" not in stored, (
        f"analysis-feedback reproducer: 'tail' suffix leaked, got: {stored!r}"
    )
    # The redaction marker must be present.
    assert "<redacted>" in stored
    # The output must be a safe operator-visible summary (no JSON
    # structural characters that could be exploited).
    assert "{" not in stored or "<redacted>" in stored


# ---------------------------------------------------------------------------
# (u) Nested sensitive-payload redaction (analysis-feedback: object/list
# under sensitive keys must be redacted in full, not walked recursively)
# ---------------------------------------------------------------------------


def test_record_subagent_work_description_redacts_nested_object_arguments() -> None:
    """Nested OBJECT under ``arguments`` is redacted in full.

    The pre-fix ``_redact_json_values`` only redacted scalar values
    under sensitive keys. A nested object like
    ``{"arguments": {"command": "rm -rf /", "token": "abc"}}`` was
    walked recursively -- only the ``token`` field was redacted,
    and the ``command`` field leaked into operator-visible output.

    The fix: when a key is sensitive, the ENTIRE value is replaced
    with ``<redacted>`` regardless of whether that value is a
    scalar, an object, or a list. The surrounding JSON structure
    remains well-formed.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"arguments": {"command": "rm -rf /", "token": "abc"}}')
    stored = wd._last_subagent_progress_description or ""
    assert "rm -rf /" not in stored, f"nested 'command' value must NOT leak, got: {stored!r}"
    assert "token" not in stored, f"nested 'token' key must NOT leak, got: {stored!r}"
    assert "abc" not in stored, f"nested 'abc' value must NOT leak, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_nested_list_arguments() -> None:
    """Nested LIST under ``arguments`` is redacted in full.

    A list value like ``["rm -rf /", "secret"]`` under a
    sensitive key must NOT have any of its elements leak into
    operator-visible output.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"arguments": ["rm -rf /", "secret"]}')
    stored = wd._last_subagent_progress_description or ""
    assert "rm -rf /" not in stored
    assert "secret" not in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_nested_object_input() -> None:
    """Nested OBJECT under ``input`` is redacted in full."""
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"input": {"echo": "hello", "user": "admin"}}')
    stored = wd._last_subagent_progress_description or ""
    assert "hello" not in stored
    assert "admin" not in stored
    assert "echo" not in stored
    assert "user" not in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_nested_array_content() -> None:
    """Nested ARRAY under ``content`` is redacted in full."""
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"content": [{"text": "secret message"}]}')
    stored = wd._last_subagent_progress_description or ""
    assert "secret message" not in stored
    assert "text" not in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_nested_prompt() -> None:
    """Nested OBJECT under ``prompt`` is redacted in full."""
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"prompt": {"role": "system", "content": "do the thing"}}')
    stored = wd._last_subagent_progress_description or ""
    assert "do the thing" not in stored
    assert "role" not in stored
    assert "system" not in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_nested_file_path() -> None:
    """Nested OBJECT under ``file_path`` is redacted in full.

    The pre-fix walker would have leaked the ``name`` field of a
    nested object under ``file_path``. The fix redacts the whole
    value in one shot.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"file_path": {"path": "/etc/passwd", "name": "shadow"}}')
    stored = wd._last_subagent_progress_description or ""
    assert "/etc/passwd" not in stored
    assert "shadow" not in stored
    assert "name" not in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_reproducer_nested_token() -> None:
    """Reproducer for the analysis-feedback nested-token case.

    The exact nested payload from the analysis feedback
    (``arguments`` holding a ``command`` and ``token`` pair under
    a nested object structure) must produce sanitized output that
    does NOT contain the forbidden ``command`` text or the
    ``token`` value.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(
        description='{"name": "tool", "arguments": {"command": "echo secret", "token": "abc123"}}'
    )
    stored = wd._last_subagent_progress_description or ""
    assert "echo secret" not in stored, (
        f"nested 'command' value 'echo secret' must NOT leak, got: {stored!r}"
    )
    assert "abc123" not in stored, f"nested 'token' value 'abc123' must NOT leak, got: {stored!r}"
    assert "secret" not in stored, f"nested 'secret' value must NOT leak, got: {stored!r}"
    # The non-sensitive key 'name' is preserved so the operator
    # still sees WHICH tool was invoked.
    assert "tool" in stored, f"non-sensitive 'name' key should survive, got: {stored!r}"
    assert "<redacted>" in stored


# ---------------------------------------------------------------------------
# (v) Embedded/malformed JSON redaction regression (analysis feedback)
# ---------------------------------------------------------------------------


def test_record_subagent_work_description_redacts_embedded_json_after_prefix() -> None:
    """A JSON fragment embedded AFTER free-form text is redacted in full.

    Analysis-feedback reproducer: lines from raw provider output
    frequently mix free-form text with one or more embedded JSON
    fragments (``prefix {"prompt": "hello, world"}``). The previous
    sanitizer only inspected lines starting with ``{`` or ``[``,
    so the fragment after ``prefix `` was missed. The pre-fix
    fallback regex stopped at the first comma and left
    ``, world"}`` visible in operator output.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='prefix {"prompt": "hello, world"}')
    stored = wd._last_subagent_progress_description or ""
    assert "world" not in stored, f"comma-bearing value 'world' must NOT leak, got: {stored!r}"
    assert "hello" not in stored, (
        f"comma-bearing value prefix 'hello' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored, f"<redacted> marker must appear, got: {stored!r}"


def test_record_subagent_work_description_redacts_embedded_json_with_comma() -> None:
    """A JSON fragment with an embedded comma is redacted in full.

    Analysis-feedback reproducer: ``prefix {"arguments": "abc,def", "x":1}``
    previously left ``,def"`` visible because the fallback regex
    stopped at the first comma.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='prefix {"arguments": "abc,def", "x":1}')
    stored = wd._last_subagent_progress_description or ""
    assert "abc" not in stored, f"comma-bearing value 'abc' must NOT leak, got: {stored!r}"
    assert "def" not in stored, f"comma-bearing value 'def' must NOT leak, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_embedded_nested_object() -> None:
    r"""A nested-object JSON fragment embedded after free-form text is redacted.

    Analysis-feedback reproducer:
    ``prefix {"name": "tool", "arguments": {"command": "echo secret", "token": "abc123"}}``
    was completely UN-redacted by the previous sanitizer because the
    line did not START with ``{`` (the JSON parse path was skipped)
    and the fallback regex's `.*?` non-greedy with positive-lookahead
    ``[,\}\]\n]`` consumed only a partial value.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(
        description=(
            'prefix {"name": "tool", "arguments": {"command": "echo secret", "token": "abc123"}}'
        )
    )
    stored = wd._last_subagent_progress_description or ""
    assert "echo secret" not in stored, (
        f"nested 'command' value 'echo secret' must NOT leak, got: {stored!r}"
    )
    assert "abc123" not in stored, f"nested 'token' value 'abc123' must NOT leak, got: {stored!r}"
    assert "secret" not in stored, f"nested 'secret' value must NOT leak, got: {stored!r}"
    # The non-sensitive key 'name' is preserved so the operator
    # still sees WHICH tool was invoked.
    assert "tool" in stored, f"non-sensitive 'name' key should survive, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_multiple_embedded_fragments() -> None:
    """Multiple JSON fragments on a single line are ALL redacted.

    Verifies the scanner finds and walks every ``{...}`` it can
    parse rather than only the first one.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(
        description=('prefix {"arguments": "first"} middle {"arguments": "second"}')
    )
    stored = wd._last_subagent_progress_description or ""
    assert "first" not in stored, f"first fragment 'first' must NOT leak, got: {stored!r}"
    assert "second" not in stored, f"second fragment 'second' must NOT leak, got: {stored!r}"
    assert stored.count("<redacted>") == 2, f"both fragments must be redacted, got: {stored!r}"


def test_record_subagent_work_description_handles_malformed_inner_quote_after_prefix() -> None:
    """Malformed JSON with unescaped inner quote embedded after a
    prefix is fully redacted (fallback regex handles it).
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='prefix {"arguments": "secret"tail"}')
    stored = wd._last_subagent_progress_description or ""
    assert "secret" not in stored, f"malformed-JSON 'secret' must NOT leak, got: {stored!r}"
    assert "tail" not in stored, f"malformed-JSON 'tail' must NOT leak, got: {stored!r}"
    assert "<redacted>" in stored


# ---------------------------------------------------------------------------
# (vi) ``args`` key redaction regression (analysis feedback)
# ---------------------------------------------------------------------------
#
# The pre-fix ``_SENSITIVE_JSON_KEYS`` set listed only ``arguments``.
# Tool-call payloads using the JSON-RPC / OpenAI-style ``args`` key
# (e.g. ``{"name":"bash","args":{"command":"rm -rf /","token":"abc"}}``)
# were NOT redacted: the ``args`` value walked recursively and
# ``command`` / ``token`` fields leaked into operator-visible
# ``subagent_activity`` and waiting-status output. The fix adds
# ``args`` to the set so the ENTIRE value is replaced with
# ``<redacted>`` (full-value replacement rule; no recursive walk).


def test_record_subagent_work_description_redacts_args_payload() -> None:
    """A description containing ``"args": "<secret>"`` has the
    ``args`` value replaced with ``<redacted>``.

    This is the analysis-feedback reproducer for the missing
    ``args`` entry in ``_SENSITIVE_JSON_KEYS``. The
    ``tool_call`` line ``{"type":"tool_call","args":{<payload>}}``
    previously leaked the nested payload via the recursive
    ``_redact_json_values`` walk because the ``args`` key was
    not in the sensitive set.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(
        description='{"type":"tool_call","name":"bash","args":{"command":"rm -rf /","token":"abc"}}'
    )
    stored = wd._last_subagent_progress_description or ""
    # The payload contents MUST NOT leak.
    assert "rm -rf /" not in stored, (
        f"nested 'command' value 'rm -rf /' must NOT leak, got: {stored!r}"
    )
    assert "abc" not in stored, f"nested 'token' value 'abc' must NOT leak, got: {stored!r}"
    assert "command" not in stored, f"nested 'command' KEY must NOT leak, got: {stored!r}"
    assert "token" not in stored, f"nested 'token' KEY must NOT leak, got: {stored!r}"
    # The non-sensitive 'type' and 'name' fields are preserved so
    # the operator still sees WHICH tool was invoked.
    assert "tool_call" in stored, (
        f"non-sensitive 'type' value 'tool_call' should survive, got: {stored!r}"
    )
    assert "bash" in stored, f"non-sensitive 'name' value 'bash' should survive, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_scalar_args_value() -> None:
    """A scalar ``args`` value is redacted (not just nested objects).

    The analysis-feedback fix must also redact ``"args": "scalar"``
    (a scalar value, not a nested object). Pre-fix the scalar
    value walked recursively and was preserved as-is.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"name":"bash","args":"secret_payload_value"}')
    stored = wd._last_subagent_progress_description or ""
    assert "secret_payload_value" not in stored, (
        f"scalar 'args' value 'secret_payload_value' must NOT leak, got: {stored!r}"
    )
    assert "bash" in stored, f"non-sensitive 'name' value 'bash' should survive, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_list_args_value() -> None:
    """A LIST ``args`` value is redacted in full (no element leak)."""
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"args": ["rm -rf /", "secret"]}')
    stored = wd._last_subagent_progress_description or ""
    assert "rm -rf /" not in stored
    assert "secret" not in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_tool_call_line_with_nested_args() -> None:
    """The exact ``type=tool_call`` analysis-feedback reproducer line.

    The pre-fix ``_sanitize_subagent_description`` left the
    ``args`` payload intact because the ``args`` key was not in
    ``_SENSITIVE_JSON_KEYS``. This test pins the no-leak contract
    for the exact line shape used in the analysis-feedback probe:
    ``{"type":"tool_call","args":{"command":"echo secret","token":"abc123"}}``.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(
        description=(
            '{"type":"tool_call","name":"bash","args":{"command":"echo secret","token":"abc123"}}'
        )
    )
    stored = wd._last_subagent_progress_description or ""
    assert "echo secret" not in stored, (
        f"nested 'command' value 'echo secret' MUST NOT leak, got: {stored!r}"
    )
    assert "abc123" not in stored, f"nested 'token' value 'abc123' MUST NOT leak, got: {stored!r}"
    # The non-sensitive 'type' and 'name' fields are preserved.
    assert "tool_call" in stored
    assert "bash" in stored
    assert "<redacted>" in stored


# (vii) Mixed-case sensitive-key redaction (analysis-feedback:
# ``Prompt`` / ``Arguments`` / ``Input`` / ``Content`` must redact
# exactly like lowercase variants).


def test_record_subagent_work_description_redacts_mixed_case_prompt_key() -> None:
    """A description with ``\"Prompt\": \"<secret>\"`` has the value redacted.

    The JSON walker must normalize keys when checking the sensitive set;
    mixed-case provider keys must not leak just because they are capitalized.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"Prompt": "SECRET-upper"}')
    stored = wd._last_subagent_progress_description or ""
    assert "SECRET-upper" not in stored, (
        f"mixed-case 'Prompt' value 'SECRET-upper' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_mixed_case_arguments_key() -> None:
    """A description with ``\"Arguments\": {...}`` has the nested value redacted."""
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"Arguments": {"token": "SECRET-mixed"}}')
    stored = wd._last_subagent_progress_description or ""
    assert "SECRET-mixed" not in stored, (
        f"mixed-case 'Arguments' value 'SECRET-mixed' must NOT leak, got: {stored!r}"
    )
    assert "token" not in stored, f"nested sibling 'token' must NOT leak, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_mixed_case_input_key() -> None:
    """A description with ``\"Input\": \"<secret>\"`` has the value redacted."""
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"Input": "SECRET-input"}')
    stored = wd._last_subagent_progress_description or ""
    assert "SECRET-input" not in stored, (
        f"mixed-case 'Input' value 'SECRET-input' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_mixed_case_content_key() -> None:
    """A description with ``\"Content\": \"<secret>\"`` has the value redacted."""
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"Content": "SECRET-content"}')
    stored = wd._last_subagent_progress_description or ""
    assert "SECRET-content" not in stored, (
        f"mixed-case 'Content' value 'SECRET-content' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_malformed_mixed_case_arguments() -> None:
    """A malformed JSON value under ``\"Arguments\"`` is fully redacted.

    The fallback regex is also case-insensitive, so mixed-case markers in
    malformed JSON are caught exactly like lowercase markers.
    """
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"Arguments": "secret"tail"}')
    stored = wd._last_subagent_progress_description or ""
    assert "secret" not in stored, (
        f"malformed mixed-case 'Arguments' prefix 'secret' must NOT leak, got: {stored!r}"
    )
    assert "tail" not in stored, (
        f"malformed mixed-case 'Arguments' suffix 'tail' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored
