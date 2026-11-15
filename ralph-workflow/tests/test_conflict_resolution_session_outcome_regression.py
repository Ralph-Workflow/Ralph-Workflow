from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    invoke_resolution_agent,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.resolution_outcome import ResolvedOutcome, UnknownOutcome
from ralph.policy.models import PolicyBundle
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps


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
    def fake_run_agent_attempt(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return SimpleNamespace(event=event, outcome=None)

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session.run_agent_attempt",
        fake_run_agent_attempt,
    )
    session = ResolutionSession(ledger=AgentFailureLedger(clock=FakeClock()))
    display_context = make_display_context()
    policy_bundle = PolicyBundle.model_construct()
    pipeline_deps = make_test_pipeline_deps(
        display_context,
        policy_bundle=policy_bundle,
    )

    outcome = invoke_resolution_agent(
        agent_name="resolver",
        prompt_path=tmp_path / "prompt.md",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=pipeline_deps,
        workspace_scope=WorkspaceScope(tmp_path),
        policy_bundle=policy_bundle,
        display=None,
        display_context=display_context,
        session=session,
    )

    assert isinstance(outcome, expected_type)


def test_conflict_resolution_session_legacy_boolean_compatibility(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run_agent_attempt(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return SimpleNamespace(event=PipelineEvent.AGENT_FAILURE, outcome=None)

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session.run_agent_attempt",
        fake_run_agent_attempt,
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

    assert outcome is False
