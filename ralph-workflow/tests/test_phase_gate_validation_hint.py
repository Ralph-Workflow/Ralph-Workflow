"""Behavioral coverage for validation-phase retry hints."""

from __future__ import annotations

import pytest

from ralph.mcp.artifacts.commit_message import COMMIT_MESSAGE_ARTIFACT
from ralph.phases import PhaseContext
from ralph.phases.commit import handle_commit_phase
from ralph.phases.required_artifacts import (
    build_proof_failure_hint,
    build_retry_hint,
    retry_hint_path,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PhaseFailureEvent
from ralph.workspace.memory import MemoryWorkspace


def test_validation_retry_hint_demands_repair_before_resubmission() -> None:
    hint = build_retry_hint("development", "SPEC008 missing evidence", validation=True)

    assert hint.startswith("VALIDATION FAILURE")
    assert "fix the underlying issue" in hint.lower()
    assert "do not resubmit unchanged work" in hint.lower()


def test_validation_proof_hint_uses_the_canonical_banner() -> None:
    hint = build_proof_failure_hint("development", "S-2 is unproven", validation=True)

    assert hint.startswith("VALIDATION FAILURE")
    assert "S-2 is unproven" in hint
    assert "do not resubmit unchanged work" in hint.lower()


@pytest.mark.parametrize(
    ("artifact", "diagnostic"),
    [
        (None, "Missing commit_message artifact"),
        ("---\ntype: commit\nsubject: invalid subject\n---\n", "Invalid or empty commit_message artifact"),
    ],
)
def test_commit_gate_persists_validation_hint_for_missing_or_invalid_artifact(
    monkeypatch: pytest.MonkeyPatch,
    artifact: str | None,
    diagnostic: str,
) -> None:
    workspace = MemoryWorkspace()
    if artifact is not None:
        workspace.write(COMMIT_MESSAGE_ARTIFACT, artifact)
    context = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        agents_policy=object(),
        artifacts_policy=object(),
    )
    monkeypatch.setattr("ralph.phases.commit._has_no_diff", lambda _ctx: False)

    events = handle_commit_phase(
        InvokeAgentEffect(agent_name="commit", phase="commit", prompt_file="commit.md"), context
    )

    assert len(events) == 1
    assert isinstance(events[0], PhaseFailureEvent)
    hint = workspace.read(retry_hint_path("commit"))
    assert hint.startswith("VALIDATION FAILURE")
    assert diagnostic in hint
