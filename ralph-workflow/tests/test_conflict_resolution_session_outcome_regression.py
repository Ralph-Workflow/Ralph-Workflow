from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    invoke_resolution_agent,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.resolution_outcome import ResolvedOutcome, UnknownOutcome
from ralph.policy.models import PolicyBundle
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.workspace.scope import WorkspaceScope


@pytest.mark.parametrize(
    ("event", "expected_type"),
    (
        (PipelineEvent.AGENT_SUCCESS, ResolvedOutcome),
        (PipelineEvent.AGENT_FAILURE, UnknownOutcome),
    ),
)
def test_conflict_resolution_session_regression_returns_canonical_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    event: PipelineEvent,
    expected_type: type[ResolvedOutcome] | type[UnknownOutcome],
) -> None:
    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session.run_agent_attempt",
        lambda *args, **kwargs: SimpleNamespace(event=event, outcome=None),
    )
    session = ResolutionSession(ledger=AgentFailureLedger(clock=FakeClock()))

    outcome = invoke_resolution_agent(
        agent_name="resolver",
        prompt_path=tmp_path / "prompt.md",
        config=UnifiedConfig.model_validate({"general": {}}),
        workspace_scope=WorkspaceScope(tmp_path),
        policy_bundle=PolicyBundle.model_construct(),
        display=None,
        display_context=None,
        session=session,
    )

    assert isinstance(outcome, expected_type)
