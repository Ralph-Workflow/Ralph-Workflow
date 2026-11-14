from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.display.auto_integrate_message import format_auto_integrate_message
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline import build_integration_status
from ralph.pipeline.auto_integrate import auto_integrate_on_phase_transition
from ralph.pipeline.auto_integrate_agent import (
    build_agent_conflict_resolver,
    build_agent_rebase_stop_resolver,
)
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.conflict_resolution import RebaseStop
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.resolution_outcome import NextStrategyOutcome
from ralph.policy.loader import default_dir, load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.config.models import AgentConfig


def _config(*, enabled: bool):
    from ralph.config.models import UnifiedConfig

    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": enabled,
                "auto_integrate_target": "main",
                "auto_integrate_remote_enabled": False,
            }
        }
    )


class _EmptyRegistry:
    def get(self, name: str) -> AgentConfig | None:
        del name
        return None


def _display() -> tuple[ParallelDisplay, StringIO]:
    output = StringIO()
    display = ParallelDisplay(make_display_context(force_width=120, output_stream=output))
    return display, output


def test_phase_transition_public_seam_is_a_noop_when_integration_is_disabled(
    tmp_path: Path,
) -> None:
    outcome = auto_integrate_on_phase_transition(
        _config(enabled=False),
        WorkspaceScope(tmp_path),
        RebaseState(),
    )

    assert outcome is None


def test_startup_public_seam_is_a_noop_when_integration_is_disabled(tmp_path: Path) -> None:
    outcome = integrate_before_planning(
        _config(enabled=False),
        WorkspaceScope(tmp_path),
        RebaseState(),
    )

    assert outcome is None


def test_conflict_resolver_declines_with_a_typed_next_strategy_when_deps_are_missing(
    tmp_path: Path,
) -> None:
    display, output = _display()
    resolver = build_agent_conflict_resolver(
        policy_bundle=load_policy(default_dir()),
        registry=_EmptyRegistry(),
        display=display,
        config=_config(enabled=True),
        pipeline_deps=None,
        workspace_scope=WorkspaceScope(tmp_path),
    )

    outcome = resolver(tmp_path, "main")

    assert outcome == NextStrategyOutcome("completion_in_place")
    assert "pipeline_deps not threaded" in output.getvalue()


def test_rebase_stop_resolver_declines_with_the_same_typed_contract(
    tmp_path: Path,
) -> None:
    display, output = _display()
    resolver = build_agent_rebase_stop_resolver(
        policy_bundle=load_policy(default_dir()),
        registry=_EmptyRegistry(),
        display=display,
        config=_config(enabled=True),
        pipeline_deps=None,
        workspace_scope=WorkspaceScope(tmp_path),
    )
    stop = RebaseStop(
        sha="abc1234",
        subject="feat: alpha",
        conflicted_files=("src/alpha.py",),
        stop_index=1,
        stop_cap=10,
    )

    outcome = resolver(tmp_path, "main", stop)

    assert outcome == NextStrategyOutcome("completion_in_place")
    assert "pipeline_deps not threaded" in output.getvalue()


def test_status_model_and_operator_message_share_the_current_conflict_facts() -> None:
    status = build_integration_status(
        cause="CANDIDATE_EXHAUSTED",
        paths_with_reasons=(("conflict_a.py", "content conflict"),),
        command="git add -- conflict_a.py",
        agents_can_finish=False,
        agent_reasons=("primary spent", "fallback unavailable"),
    )

    message = format_auto_integrate_message(
        "conflict",
        "main",
        status.summary,
        fast_forwarded=False,
    )

    assert "conflict_a.py" in status.summary
    assert "git add -- conflict_a.py" in status.summary
    assert "spent/unavailable" in status.summary
    assert status.summary in message


def test_success_message_reports_the_verified_landing() -> None:
    message = format_auto_integrate_message(
        "rebased",
        "main",
        None,
        fast_forwarded=True,
        freshness_verdict="verified",
    )

    assert "rebased onto target" in message
    assert "fast-forwarded main" in message
    assert "verified-fresh" in message
