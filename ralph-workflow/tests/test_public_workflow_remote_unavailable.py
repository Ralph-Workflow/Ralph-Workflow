from __future__ import annotations

from pathlib import Path

from ralph.display.auto_integrate_message import (
    OperationDisplayState,
    format_auto_integrate_message,
)
from ralph.pipeline._publication_state_machine import PublicationDecision, next_publication_action
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.rebase_state import RebaseState


def test_remote_unavailable_defers_remote_and_renders_distinct_display(tmp_path: Path) -> None:
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

    # 3. Update operation_display_state to PUBLISHING
    rebase_publishing = rebase.model_copy(
        update={
            "last_publication_decision": decision.value,
            "operation_display_state": OperationDisplayState.PUBLISHING.value,
        }
    )

    # 4. Format messages for both states and assert distinct rendering
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
    assert msg_landing != msg_publishing
    assert "landing" in msg_landing.lower() or "target" in msg_landing.lower()
    assert "publishing" in msg_publishing.lower() or "remote" in msg_publishing.lower()
