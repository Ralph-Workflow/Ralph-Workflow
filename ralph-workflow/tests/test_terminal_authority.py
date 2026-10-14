"""Unit tests for the terminal-success authority (Authority 7)."""

from __future__ import annotations

from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.rebase_state import RebaseState


def test_terminal_success_false_when_record_present() -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
    )
    assert is_terminal_success_eligible(None, record, RebaseState()) is False


def test_terminal_success_false_when_integration_unresolved() -> None:
    rebase_state = RebaseState(last_action="conflict")
    assert is_terminal_success_eligible(None, None, rebase_state) is False


def test_terminal_success_false_when_publication_not_terminal() -> None:
    rebase_state = RebaseState(last_publication_decision="publish_now")
    assert is_terminal_success_eligible(None, None, rebase_state) is False


def test_terminal_success_false_when_fan_out_pending() -> None:
    rebase_state = RebaseState(
        last_publication_decision="publication_terminal",
        last_fan_out_decision="join_pending",
    )
    assert is_terminal_success_eligible(None, None, rebase_state) is False


def test_terminal_success_true_when_all_clear() -> None:
    rebase_state = RebaseState(
        last_publication_decision="publication_terminal",
        last_fan_out_decision="join_complete",
    )
    assert is_terminal_success_eligible(None, None, rebase_state) is True


def test_terminal_success_true_when_publication_none() -> None:
    """A None publication decision is treated as terminal when nothing else is open."""
    rebase_state = RebaseState(last_fan_out_decision="join_complete")
    assert is_terminal_success_eligible(None, None, rebase_state) is True
