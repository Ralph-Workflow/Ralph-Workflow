"""Pin: clean-exit session-id recovery for OpenCodeResumableExitError.

The PROMPT log shows recurring ``OpenCodeResumableExitError`` raises with
``session_id=None`` even when the agent had emitted a session id on the
live stream. The completion path falls back to the legacy
``extract_transport_session_id(bounded_output)`` when ``captured_session_id``
is ``None``, but does NOT consult the per-line PTY-aware
``extract_transport_session_id_with_visible_tui`` so a session id carried
in a TUI banner wrapped in ANSI escape codes (the visible-TUI pattern)
is lost.

The fix: after the legacy extractor returns ``None``, iterate
``bounded_output`` and call the per-line PTY-aware extractor so a TUI
session-id line is recovered. The legacy extractor handles plain text
and JSON envelopes; the per-line PTY extractor handles ANSI-wrapped
text.

These tests pin four behaviors on the real
``check_process_result(...)`` + ``CompletionCheckOptions(...)`` seam:

1. ``captured_session_id`` wins: when set, it is the resumable id (no
   fallback re-extraction).
2. Legacy extractor recovery: when ``captured_session_id=None`` and
   ``bounded_output`` contains a plain ``Session ID: ...`` line, the
   legacy extractor recovers the id.
3. PTY/ANSI-wrapped recovery: when ``captured_session_id=None`` and
   ``bounded_output`` contains an ANSI-wrapped ``Session ID: ...`` line,
   the per-line PTY extractor recovers the id.
4. No fabrication: when ``captured_session_id=None`` and ``bounded_output``
   lacks any id, the resumable exception still carries ``session_id=None``
   (no fabricated id).

All tests use ``FakeClock`` and ``FakeLivenessProbe``; no real
subprocess; no real wall-clock waits. Default test layer: ``unit``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import OpenCodeExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    CompletionCheckOptions,
    OpenCodeResumableExitError,
    check_process_result,
)
from ralph.process.liveness import FakeLivenessProbe
from tests.fake_handle import _FakeHandle

if TYPE_CHECKING:
    from pathlib import Path


def _no_signals(
    workspace: object,
    raw_output: object,
    *,
    required_artifact: object = None,
    run_id: object = None,
    sentinel_secret: object = None,
    receipt_secret: object = None,
) -> CompletionSignals:
    return CompletionSignals(False, False, ())


def test_pi_session_event_id_is_preserved_for_clean_exit_recovery(tmp_path: Path) -> None:
    """Pi emits its resumable session identity as top-level ``id``."""
    handle = _FakeHandle(returncode=0, has_descendants=False)
    opts = CompletionCheckOptions(
        execution_strategy=OpenCodeExecutionStrategy(),
        workspace_path=tmp_path,
        liveness_probe=FakeLivenessProbe(active=False),
        policy=TimeoutPolicy(
            idle_timeout_seconds=None,
            parent_exit_grace_seconds=0.0,
            descendant_wait_timeout_seconds=0.0,
        ),
        evaluate_completion_fn=_no_signals,
    )

    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            handle,
            "pi",
            ['{"type":"session","id":"pi-main-session"}'],
            opts,
        )

    assert excinfo.value.resumable_session_id == "pi-main-session"


def test_captured_session_id_preserved(tmp_path: Path) -> None:
    """When ``captured_session_id`` is set on ``CompletionCheckOptions``
    the resumable exception carries that id (no fallback
    re-extraction).

    This pins the production behavior: the live-stream captured id is
    authoritative when present. The fallback chain only runs when
    ``captured_session_id`` is ``None``.
    """
    probe = FakeLivenessProbe(active=False)
    strategy = OpenCodeExecutionStrategy()
    handle = _FakeHandle(returncode=0, has_descendants=False)
    opts = CompletionCheckOptions(
        execution_strategy=strategy,
        workspace_path=tmp_path,
        liveness_probe=probe,
        policy=TimeoutPolicy(
            idle_timeout_seconds=None,
            parent_exit_grace_seconds=0.0,
            descendant_wait_timeout_seconds=0.0,
        ),
        evaluate_completion_fn=_no_signals,
        captured_session_id="sess-from-live-stream",
    )
    parsed_output: list[str] = []
    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            handle,
            "opencode",
            parsed_output,
            opts,
        )
    assert excinfo.value.resumable_session_id == "sess-from-live-stream", (
        f"captured_session_id MUST win over the bounded_output fallback;"
        f" got resumable_session_id={excinfo.value.resumable_session_id!r}"
    )


def test_legacy_extractor_recovers_from_bounded_output(tmp_path: Path) -> None:
    """When ``captured_session_id`` is None and ``parsed_output``
    contains a plain ``Session ID: ...`` line, the legacy
    ``extract_transport_session_id`` extractor recovers the id and
    the resumable exception carries it.

    Pre-fix this test would also pass (the legacy extractor was the
    primary path before the new fallback), but it pins the existing
    legacy path so a regression to the legacy path is caught.
    """
    probe = FakeLivenessProbe(active=False)
    strategy = OpenCodeExecutionStrategy()
    handle = _FakeHandle(returncode=0, has_descendants=False)
    opts = CompletionCheckOptions(
        execution_strategy=strategy,
        workspace_path=tmp_path,
        liveness_probe=probe,
        policy=TimeoutPolicy(
            idle_timeout_seconds=None,
            parent_exit_grace_seconds=0.0,
            descendant_wait_timeout_seconds=0.0,
        ),
        evaluate_completion_fn=_no_signals,
        captured_session_id=None,
    )
    parsed_output = ["Session ID: sess-abc123", "other line"]
    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            handle,
            "opencode",
            parsed_output,
            opts,
        )
    assert excinfo.value.resumable_session_id == "sess-abc123", (
        f"legacy extractor MUST recover 'sess-abc123' from bounded_output;"
        f" got resumable_session_id={excinfo.value.resumable_session_id!r}"
    )


def test_pty_visible_tui_recovers_from_ansi_wrapped_line(tmp_path: Path) -> None:
    """When ``captured_session_id`` is None and ``parsed_output``
    contains an ANSI-wrapped ``Session ID: ...`` line, the per-line
    PTY-aware extractor recovers the id.

    Pre-fix this would raise ``OpenCodeResumableExitError`` with
    ``session_id=None`` because the legacy
    ``extract_transport_session_id`` cannot match anchored text
    patterns against TUI-banner lines wrapped in ANSI escape codes.
    Post-fix the per-line PTY extractor
    (``extract_transport_session_id_with_visible_tui``) strips ANSI
    codes via ``_visible_tui_text`` and matches the underlying text
    so the resumable exception carries the captured id.
    """
    probe = FakeLivenessProbe(active=False)
    strategy = OpenCodeExecutionStrategy()
    handle = _FakeHandle(returncode=0, has_descendants=False)
    opts = CompletionCheckOptions(
        execution_strategy=strategy,
        workspace_path=tmp_path,
        liveness_probe=probe,
        policy=TimeoutPolicy(
            idle_timeout_seconds=None,
            parent_exit_grace_seconds=0.0,
            descendant_wait_timeout_seconds=0.0,
        ),
        evaluate_completion_fn=_no_signals,
        captured_session_id=None,
    )
    parsed_output = ["\x1b[32mSession ID: sess-pty-xyz\x1b[0m"]
    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            handle,
            "opencode",
            parsed_output,
            opts,
        )
    assert excinfo.value.resumable_session_id == "sess-pty-xyz", (
        f"per-line PTY extractor MUST recover 'sess-pty-xyz' from"
        f" ANSI-wrapped bounded_output;"
        f" got resumable_session_id={excinfo.value.resumable_session_id!r}"
    )


def test_no_session_id_in_bounded_output_still_raises_none_id(tmp_path: Path) -> None:
    """When ``captured_session_id`` is None AND ``parsed_output`` lacks
    any session id, the resumable exception still carries
    ``session_id=None`` (no fabrication).

    The fallback chain is conservative: it only fills in a
    ``resumable_session_id`` when the bounded output contains one.
    If neither the legacy nor the per-line PTY extractor finds an id
    the exception raises with ``session_id=None`` so the recovery
    controller knows the session cannot be resumed.
    """
    probe = FakeLivenessProbe(active=False)
    strategy = OpenCodeExecutionStrategy()
    handle = _FakeHandle(returncode=0, has_descendants=False)
    opts = CompletionCheckOptions(
        execution_strategy=strategy,
        workspace_path=tmp_path,
        liveness_probe=probe,
        policy=TimeoutPolicy(
            idle_timeout_seconds=None,
            parent_exit_grace_seconds=0.0,
            descendant_wait_timeout_seconds=0.0,
        ),
        evaluate_completion_fn=_no_signals,
        captured_session_id=None,
    )
    parsed_output = ["plain stdout line", "no id here"]
    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            handle,
            "opencode",
            parsed_output,
            opts,
        )
    assert excinfo.value.resumable_session_id is None, (
        f"resumable_session_id MUST be None when bounded_output lacks"
        f" an id (no fabrication);"
        f" got resumable_session_id={excinfo.value.resumable_session_id!r}"
    )
