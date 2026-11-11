from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.events import (
    PipelineEvent,
    WorkerCompletedEvent,
    WorkerFailedEvent,
)
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.workspace.scope import WorkspaceScope
from tests.integration._fake_display_partial_failure import _FakeDisplay
from tests.plan_fixtures import development_result_markdown


def _seed_artifact(tmp_path: Path, unit_id: str) -> None:
    artifact_dir = tmp_path / ".agent" / "workers" / unit_id / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "development_result.md").write_text(
        development_result_markdown(unit_id),
        encoding="utf-8",
    )


def _make_policy_bundle(max_workers: int = 2) -> MagicMock:
    bundle = MagicMock()
    dev_phase = MagicMock()
    dev_phase.drain = "development"
    bundle.pipeline.phases = {"development": dev_phase}
    bundle.pipeline.recovery.failed_route = "failed_terminal"
    bundle.agents = AgentsPolicy(
        agent_chains={"developer": AgentChainConfig(agents=["developer"])},
        agent_drains={
            "development": AgentDrainConfig(chain="developer", drain_class="development")
        },
    )
    return bundle


def _make_work_unit(uid: str, deps: list[str] | None = None) -> WorkUnit:
    return WorkUnit(
        unit_id=uid,
        description=f"Work unit {uid}",
        dependencies=list(deps or []),
        allowed_directories=[f"src/{uid}"],
    )


class TestPartialFailureHandoffContent:
    def test_partial_failure_development_result_md_contains_both_unit_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")

        _seed_artifact(tmp_path, "unit-a")

        scope = WorkspaceScope(root=tmp_path, allowed_roots=frozenset([tmp_path]))
        initial_state = PipelineState(
            phase="development",
            work_units=(unit_a, unit_b),
        )

        partial_events = [
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
            WorkerFailedEvent(unit_id="unit-b", exit_code=1, error="unit-b: no artifact"),
            PipelineEvent.ALL_WORKERS_COMPLETE,
        ]

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            return partial_events

        monkeypatch.setattr(coordinator, "run_fan_out", _fake_run_fan_out)
        monkeypatch.setattr(ckpt, "save", lambda state, *_args, **_kwargs: None)

        bundle = _make_policy_bundle(max_workers=2)
        effect = FanOutEffect(
            work_units=(unit_a, unit_b),
            max_workers=2,
            run_post_fanout_verification=False,
        )

        final_state = runner_module.execute_fan_out_sync(
            effect=effect,
            state=initial_state,
            display=_FakeDisplay(),
            policy_bundle=bundle,
            workspace_scope=scope,
        )

        assert final_state.phase == "failed_terminal", (
            f"Partial failure must produce 'failed_terminal', got {final_state.phase!r}. "
            "'development_analysis' must only be reached when ALL workers succeed."
        )

        handoff_path = tmp_path / ".agent" / "DEVELOPMENT_RESULT.md"
        assert handoff_path.exists(), (
            ".agent/DEVELOPMENT_RESULT.md must be written on partial failure"
        )
        content = handoff_path.read_text()

        assert "unit-a" in content, "DEVELOPMENT_RESULT.md must name unit-a (the successful worker)"
        assert "unit-b" in content, "DEVELOPMENT_RESULT.md must name unit-b (the failed worker)"
        assert "any_failed: true" in content, "DEVELOPMENT_RESULT.md must report any_failed: true"
        assert "all_succeeded: false" in content, (
            "DEVELOPMENT_RESULT.md must report all_succeeded: false"
        )

    def test_parallel_development_summary_markdown_has_per_unit_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")

        _seed_artifact(tmp_path, "unit-a")

        scope = WorkspaceScope(root=tmp_path, allowed_roots=frozenset([tmp_path]))
        initial_state = PipelineState(
            phase="development",
            work_units=(unit_a, unit_b),
        )

        partial_events = [
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
            WorkerFailedEvent(unit_id="unit-b", exit_code=1, error="unit-b failed"),
            PipelineEvent.ALL_WORKERS_COMPLETE,
        ]

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            return partial_events

        monkeypatch.setattr(coordinator, "run_fan_out", _fake_run_fan_out)
        monkeypatch.setattr(ckpt, "save", lambda state, *_args, **_kwargs: None)

        bundle = _make_policy_bundle(max_workers=2)
        effect = FanOutEffect(
            work_units=(unit_a, unit_b),
            max_workers=2,
            run_post_fanout_verification=False,
        )

        runner_module.execute_fan_out_sync(
            effect=effect,
            state=initial_state,
            display=_FakeDisplay(),
            policy_bundle=bundle,
            workspace_scope=scope,
        )

        summary_path = tmp_path / ".agent" / "artifacts" / "parallel_development_summary.md"
        assert summary_path.exists(), (
            ".agent/artifacts/parallel_development_summary.md must be written after fan-out"
        )
        summary = summary_path.read_text(encoding="utf-8")

        assert "- any_failed: true" in summary
        assert "- all_succeeded: false" in summary
        assert "- **unit-a**: succeeded" in summary
        assert "- **unit-b**: failed" in summary
