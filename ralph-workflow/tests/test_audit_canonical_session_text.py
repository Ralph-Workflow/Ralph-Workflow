"""Regression coverage for the canonical interactive-session text audit."""

from __future__ import annotations

from typing import TYPE_CHECKING

import ralph.testing.audit_canonical_session_text as audit_module
from ralph.testing.audit_canonical_session_text import main as audit_main

if TYPE_CHECKING:
    import pytest


def test_audit_pins_the_five_canonical_session_text_patterns() -> None:
    """Changing the canonical vocabulary requires an explicit audit/test update."""
    assert audit_module._CANONICAL_SESSION_TEXT_PATTERN_SOURCES == (
        r"^Claude session ready\. Session ID:\s*([A-Za-z0-9._:-]+)$",
        r"^Session ID:\s*([A-Za-z0-9._:-]+)$",
        r"^Resume this session with --resume\s+([A-Za-z0-9._:-]+)$",
        r"^--resume\s+([A-Za-z0-9._:-]+)$",
        r"^--session\s+([A-Za-z0-9._:-]+)$",
    )


def test_audit_pins_the_three_interactive_pty_transports() -> None:
    """The PTY exemption remains intentionally limited to its canonical set."""
    assert frozenset({"CLAUDE_INTERACTIVE", "NANOCODER", "AGY"}) == (
        audit_module._INTERACTIVE_PTY_TRANSPORT_NAMES
    )


def test_audit_invariant_count_is_two() -> None:
    """A new protected source contract must add focused regression coverage."""
    assert audit_module._INVARIANT_COUNT == 2


def test_audit_blocks_regression_when_session_pattern_literal_is_removed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Removing Claude's production-failure text shape produces a labeled violation."""
    real_read = audit_module._read

    def _read_with_pattern_removed(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == audit_module._SESSION_SOURCE_PATH:
            return content.replace(
                r'^Claude session ready\. Session ID:\s*([A-Za-z0-9._:-]+)$',
                r'^removed$',
            )
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_pattern_removed)
    assert audit_main([]) == 1
    assert "canonical session text patterns" in capsys.readouterr().out


def test_audit_blocks_regression_when_interactive_transport_is_removed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Removing Claude's PTY transport exemption produces a labeled violation."""
    real_read = audit_module._read

    def _read_with_transport_removed(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path == audit_module._RAW_LOG_BREAKS_SOURCE_PATH:
            return content.replace("AgentTransport.CLAUDE_INTERACTIVE, ", "")
        return content

    monkeypatch.setattr(audit_module, "_read", _read_with_transport_removed)
    assert audit_main([]) == 1
    assert "interactive PTY transports" in capsys.readouterr().out
