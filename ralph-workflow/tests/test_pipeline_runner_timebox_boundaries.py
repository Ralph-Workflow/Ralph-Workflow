from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import runner
from ralph.pipeline.effects import InvokeAgentEffect, SaveCheckpointEffect
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    import pytest

    from ralph.pipeline.events import PipelineEvent


def _bundle():
    return load_policy(Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults")


def _run(state: PipelineState, tmp_path: Path, deps: object) -> PipelineState | int:
    context = make_display_context()
    return runner.run_pipeline_step(
        state=state,
        policy_bundle=_bundle(),
        workspace_scope=WorkspaceScope(tmp_path),
        config=UnifiedConfig(),
        display=runner.ParallelDisplay(context),
        display_context=context,
        verbosity=Verbosity.QUIET,
        registry=MagicMock(),
        pipeline_subscriber=None,
        pipeline_deps=deps,
        _cycle_sample_box=[None],
        _development_sample_box=[None],
    )


def test_restored_warning_redirects_before_agent_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: list[str] = []

    def prepare(current: PipelineState, *_a: object, **_k: object):
        observed.append(current.phase)
        return current, SaveCheckpointEffect()

    monkeypatch.setattr(runner, "_prepare_pipeline_step_dispatch", prepare)
    monkeypatch.setattr(runner, "handle_inline_effect", lambda **kwargs: kwargs["state"])
    context = make_display_context()
    deps = dataclasses.replace(
        make_test_pipeline_deps(display_context=context),
        monotonic=lambda: 10.0,
        wall_time=lambda: 5200.0,
    )
    result = _run(
        PipelineState(
            phase="development",
            dev_timebox_active=True,
            dev_timebox_started_at_epoch=1000.0,
        ),
        tmp_path,
        deps,
    )
    assert isinstance(result, PipelineState)
    assert observed == ["development_commit_cleanup"]


def test_crash_crossing_warning_routes_without_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monotonic = [10.0]
    wall_time = [5199.0]
    monkeypatch.setattr(
        runner,
        "_prepare_pipeline_step_dispatch",
        lambda current, *_a, **_k: (
            current,
            InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md"),
        ),
    )
    monkeypatch.setattr(runner, "handle_inline_effect", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "materialize_agent_prompt_if_needed", lambda *_a, **_k: None)

    def crash(*_args: object, **_kwargs: object) -> PipelineEvent:
        monotonic[0] += 2.0
        wall_time[0] += 2.0
        raise RuntimeError("agent crashed")

    monkeypatch.setattr(runner, "invoke_execute_effect_with_optional_display", crash)
    monkeypatch.setattr(runner, "_save_checkpoint_or_log", lambda *_a, **_k: None)
    context = make_display_context()
    deps = dataclasses.replace(
        make_test_pipeline_deps(display_context=context),
        monotonic=lambda: monotonic[0],
        wall_time=lambda: wall_time[0],
    )
    result = _run(
        PipelineState(
            phase="development",
            dev_timebox_active=True,
            dev_timebox_consumed_seconds=4199.0,
            dev_timebox_started_at_epoch=1000.0,
        ),
        tmp_path,
        deps,
    )
    assert isinstance(result, PipelineState)
    assert result.phase == "development_commit_cleanup"
    assert result.dev_timebox_consumed_seconds == 4201.0
