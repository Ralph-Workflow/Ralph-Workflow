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


def test_modify_delete_is_the_resolvers_decision_not_out_of_reach() -> None:
    """One side edited, the other deleted: a judgement, not an impossibility.

    Git records stages 1+3 (or 1+2) for a modify/delete and leaves the
    surviving file in the worktree. Escalating it on sight spent no
    resolver on one of the commonest conflicts there is.
    """
    from ralph.pipeline.conflict_resolution.sight import ConflictSight, classify_stage_map

    modified_by_them = {1: ("100644", "base"), 3: ("100644", "theirs")}
    assert classify_stage_map(modified_by_them, binary=False) is ConflictSight.AGENT_DECISION
    modified_by_us = {1: ("100644", "base"), 2: ("100644", "ours")}
    assert classify_stage_map(modified_by_us, binary=False) is ConflictSight.AGENT_DECISION


def test_a_one_sided_submodule_or_directory_conflict_stays_out_of_reach() -> None:
    """Only a text file is something the resolver can actually rewrite."""
    from ralph.pipeline.conflict_resolution.sight import ConflictSight, classify_stage_map

    gitlink = {1: ("160000", "base"), 3: ("160000", "theirs")}
    assert classify_stage_map(gitlink, binary=False) is ConflictSight.OUT_OF_REACH
    tree = {1: ("100644", "base"), 2: ("040000", "ours")}
    assert classify_stage_map(tree, binary=False) is ConflictSight.OUT_OF_REACH
    assert (
        classify_stage_map({1: ("100644", "b"), 3: ("100644", "t")}, binary=True)
        is ConflictSight.OUT_OF_REACH
    )


def test_a_markerless_decision_demands_declared_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ralph cannot SEE a keep-or-delete decision, so it must be declared.

    A modify/delete has no markers, so the textual proof every other
    conflict is judged by is satisfied before anyone touches the file.
    Without declared completion an agent that did nothing would silently
    land "keep", quietly reversing a deletion the other side intended.
    """
    from ralph.pipeline.conflict_resolution import driver as driver_module
    from ralph.pipeline.conflict_resolution.sight import ConflictSight

    captured: list[bool] = []

    def _capture(**kwargs: object) -> bool:
        captured.append(bool(kwargs.get("require_completion_evidence")))
        return False

    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: ["gone.py"])
    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", lambda _r, _p: [])
    monkeypatch.setattr(
        driver_module,
        "classify_unmerged_conflicts",
        lambda _root, paths: dict.fromkeys(paths, ConflictSight.AGENT_DECISION),
    )
    monkeypatch.setattr(driver_module, "stage_mechanical_conflicts", lambda _root, _kinds: ())
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one",))
    monkeypatch.setattr(driver_module, "invoke_resolution_agent", _capture)

    driver_module.run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=load_policy(
            Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
        ),
        display=None,
        display_context=None,
    )
    assert captured, "the resolver must be invoked for a markerless decision"
    assert all(captured), "and it must be required to declare its decision"
