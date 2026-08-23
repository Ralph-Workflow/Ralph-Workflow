"""Outcome judging: in-scope work lands without declare_complete."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution.session import invoke_resolution_agent
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    import pytest


def test_invoke_resolution_agent_does_not_require_declare_complete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[bool] = []

    def _capture_execute(effect: object, *args: object, **kwargs: object) -> object:
        captured.append(bool(getattr(effect, "requires_completion_evidence", True)))
        raise RuntimeError("stop after capturing")

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        _capture_execute,
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve", encoding="utf-8")
    invoke_resolution_agent(
        agent_name="claude",
        prompt_path=prompt,
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=load_policy(
            Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
        ),
        display=None,
        display_context=None,
    )
    assert captured == [False]


def test_identical_blobs_are_mechanical() -> None:
    from ralph.pipeline.conflict_resolution.sight import ConflictSight, classify_stage_map

    kind = classify_stage_map(
        {2: ("100644", "abc"), 3: ("100644", "abc")},
        binary=False,
    )
    assert kind is ConflictSight.MECHANICAL


def test_binary_conflict_is_out_of_reach_without_ralph_side_choice() -> None:
    from ralph.pipeline.conflict_resolution.sight import ConflictSight, classify_stage_map

    kind = classify_stage_map(
        {2: ("100644", "aa"), 3: ("100644", "bb")},
        binary=True,
    )
    assert kind is ConflictSight.OUT_OF_REACH


def test_file_directory_collision_is_out_of_reach() -> None:
    from ralph.pipeline.conflict_resolution.sight import ConflictSight, classify_stage_map

    kind = classify_stage_map(
        {2: ("100644", "aa"), 3: ("040000", "bb")},
        binary=False,
    )
    assert kind is ConflictSight.OUT_OF_REACH
