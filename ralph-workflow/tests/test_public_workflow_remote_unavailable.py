from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from ralph.display.auto_integrate_message import (
    OperationDisplayState,
    format_auto_integrate_message,
)
from ralph.git.remote_push import PushResult, PushStatus
from ralph.pipeline._publication_state_machine import PublicationDecision, next_publication_action
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.auto_integrate_remote_sync import push_target_after_landing
from ralph.pipeline.rebase_state import RebaseState


class _FakeGeneral:
    auto_integrate_remote_enabled = True
    auto_integrate_remote = "origin"


class _FakeConfig:
    general = _FakeGeneral()


def test_remote_unavailable_defers_remote_and_renders_distinct_display(tmp_path: Path) -> None:
    """Local landing with remote unavailable: defer remote, keep landing/remote distinct.

    Drives the FULL public boundary: ``push_target_after_landing`` is the
    single public entry point for post-landing remote push. The asserted
    outcome is the **persisted** ``last_publication_decision`` (the
    post-push decision), NOT the pre-push eligibility check. A successful
    push must close the publication obligation (``PUBLICATION_TERMINAL``)
    and a failed push must keep the obligation open
    (``PUBLISH_DEFERRED_REMOTE``).
    """
    # 1. Local landing complete: integrated_feature_sha is established
    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        integrated_feature_sha="feat333",
        owning_worktree=str(tmp_path),
    )
    rebase = RebaseState(
        last_action="fast_forward",
        fast_forwarded=True,
        freshness_verdict="verified",
        operation_display_state=OperationDisplayState.LANDING.value,
    )

    # 2. With remote_unavailable=True and configured_remote="origin"
    decision = next_publication_action(
        rebase,
        record=record,
        remote_unavailable=True,
        configured_remote="origin",
    )
    assert decision == PublicationDecision.PUBLISH_DEFERRED_REMOTE

    # 3. Drive the FULL public boundary through push_target_after_landing
    #    with a typed UNREACHABLE push result. The persisted
    #    last_publication_decision MUST reflect the actual post-push
    #    outcome, not the pre-push eligibility check.
    with patch(
        "ralph.git.remote_push.push_branch_to_single_remote",
        return_value=PushResult(
            status=PushStatus.UNREACHABLE, remote="origin", branch="main", detail="network down"
        ),
    ):
        rebase_after_unavailable = push_target_after_landing(
            _FakeConfig(), tmp_path, "main", rebase
        )
    assert (
        rebase_after_unavailable.last_publication_decision
        == PublicationDecision.PUBLISH_DEFERRED_REMOTE.value
    )
    assert rebase_after_unavailable.last_action == "remote_failed"

    # 4. Drive the FULL public boundary with a typed PUSHED success
    #    result. The persisted last_publication_decision MUST be
    #    PUBLICATION_TERMINAL -- a successful push closes the
    #    publication obligation; reporting PUBLISH_NOW would
    #    leave the obligation open and the terminal-success
    #    authority would refuse to report success.
    with patch(
        "ralph.git.remote_push.push_branch_to_single_remote",
        return_value=PushResult(
            status=PushStatus.PUSHED, remote="origin", branch="main", detail="pushed main to origin"
        ),
    ):
        rebase_after_success = push_target_after_landing(_FakeConfig(), tmp_path, "main", rebase)
    assert (
        rebase_after_success.last_publication_decision
        == PublicationDecision.PUBLICATION_TERMINAL.value
    )
    assert rebase_after_success.last_action == "remote_pushed"

    # 5. Local landing (REBASE_STATE) and remote publication (deferred
    #    OR terminal) render with distinct messages. Use the post-push
    #    state for the deferred case so the formatter sees the
    #    deferred-remote display state.
    rebase_publishing = rebase.model_copy(
        update={
            "last_publication_decision": decision.value,
            "operation_display_state": OperationDisplayState.PUBLISHING.value,
        }
    )
    rebase_terminal = rebase.model_copy(
        update={
            "last_publication_decision": rebase_after_success.last_publication_decision,
            "operation_display_state": OperationDisplayState.TERMINAL.value,
        }
    )

    # 6. Format messages for the three distinct states and assert
    #    distinct rendering
    msg_landing = format_auto_integrate_message(
        rebase.last_action,
        rebase.last_target,
        rebase.last_reason,
        fast_forwarded=rebase.fast_forwarded,
        operation_display_state=rebase.operation_display_state,
    )
    msg_publishing = format_auto_integrate_message(
        rebase_publishing.last_action,
        rebase_publishing.last_target,
        rebase_publishing.last_reason,
        fast_forwarded=rebase_publishing.fast_forwarded,
        operation_display_state=rebase_publishing.operation_display_state,
    )
    msg_terminal = format_auto_integrate_message(
        rebase_terminal.last_action,
        rebase_terminal.last_target,
        rebase_terminal.last_reason,
        fast_forwarded=rebase_terminal.fast_forwarded,
        operation_display_state=rebase_terminal.operation_display_state,
    )
    assert msg_landing != msg_publishing
    assert msg_landing != msg_terminal
    assert msg_publishing != msg_terminal
    assert "landing" in msg_landing.lower() or "target" in msg_landing.lower()
    assert "publishing" in msg_publishing.lower() or "remote" in msg_publishing.lower()
    # Terminal success after push is the operator-facing signal that
    # the publication obligation is closed.
    assert (
        "terminal" in msg_terminal.lower()
        or "success" in msg_terminal.lower()
        or "complete" in msg_terminal.lower()
    )
