from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.artifacts.state_db import RunStateDB
from ralph.pipeline.factory import PipelineDeps
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import default_dir, load_policy
from ralph.project_policy import cli_integration
from ralph.project_policy.cli_integration import make_production_invoke_agent
from ralph.project_policy.pipeline_graph import PHASE_ANALYSIS, PHASE_REMEDIATION
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.workspace.scope import WorkspaceScope

_RUN_ID = "policy-readiness-run"
_OTHER_RUN_ID = "unrelated-run"
_DECISION = "policy_remediation_analysis_decision"


class _FirstAgentController:
    def preferred_agent_index(self, phase: str, agents: list[str]) -> object:
        del phase
        return SimpleNamespace(agent=agents[0])


def _write_completion(workspace: Path, run_id: str, marker: str) -> None:
    db = RunStateDB(workspace)
    try:
        db.upsert_completion_sentinel(run_id, marker)
        db.upsert_receipt(run_id, _DECISION, marker)
    finally:
        db.close()


def _completion(workspace: Path, run_id: str) -> tuple[object, object]:
    db = RunStateDB(workspace)
    try:
        return db.get_completion_sentinel_hmac(run_id), db.get_receipt_hmac(run_id, _DECISION)
    finally:
        db.close()


def test_each_policy_invocation_starts_without_the_previous_invocations_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".agent").mkdir()
    _write_completion(tmp_path, _OTHER_RUN_ID, "other-run")
    seen_at_launch: list[tuple[str, tuple[object, object]]] = []

    def run_attempt(*_args: object, **kwargs: object) -> object:
        effect = kwargs["effect"]
        phase = str(getattr(effect, "phase", ""))
        seen_at_launch.append((phase, _completion(tmp_path, _RUN_ID)))
        _write_completion(tmp_path, _RUN_ID, f"{phase}-{len(seen_at_launch)}")
        return SimpleNamespace(outcome=SimpleNamespace(kind=AttemptOutcomeKind.COMPLETED))

    monkeypatch.setattr(
        cli_integration,
        "build_entry_point_recovery_controller",
        lambda *args, **kwargs: _FirstAgentController(),
    )
    monkeypatch.setattr(cli_integration, "run_agent_attempt", run_attempt)
    scope = WorkspaceScope(tmp_path)
    display_context = make_display_context()
    invoke = make_production_invoke_agent(
        cli_integration.LoadResult(
            config=UnifiedConfig(),
            workspace_scope=scope,
            initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
            policy_bundle=load_policy(default_dir()),
            run_id=_RUN_ID,
        ),
        PipelineDeps(display_context=display_context),
        scope,
        None,
        display_context,
    )
    prompt = str(tmp_path / "prompt.md")

    assert invoke(phase=PHASE_ANALYSIS, prompt_path=prompt) is True
    assert invoke(phase=PHASE_REMEDIATION, prompt_path=prompt) is True
    assert invoke(phase=PHASE_ANALYSIS, prompt_path=prompt) is True

    assert [phase for phase, _ in seen_at_launch] == [
        PHASE_ANALYSIS,
        PHASE_REMEDIATION,
        PHASE_ANALYSIS,
    ]
    for _phase, (sentinel, receipt) in seen_at_launch:
        assert sentinel not in {"other-run"} and not str(sentinel).startswith("policy_")
        assert not str(receipt).startswith("policy_")
    assert _completion(tmp_path, _OTHER_RUN_ID) == ("other-run", "other-run")
