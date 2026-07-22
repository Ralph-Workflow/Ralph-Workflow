"""Tests for the conflict-only resolution prompt.

The load-bearing assertion is the NEGATIVE one: the resolution session
must carry the conflict and nothing else, so the rendered prompt may not
contain the run's project payload. A resolution agent that can see the
plan is an agent that may resume feature work inside an in-progress
merge.
"""

from __future__ import annotations

from pathlib import Path

from ralph.pipeline.conflict_resolution.prompt import render_conflict_prompt

_CONFLICTED = ("src/alpha.py", "docs/beta.md")


def _render(
    tmp_path: Path,
    *,
    round_index: int = 1,
    surviving: tuple[str, ...] = (),
    conflicted: tuple[str, ...] = _CONFLICTED,
) -> str:
    prompt_path = render_conflict_prompt(
        root=tmp_path,
        target="main",
        conflicted_paths=conflicted,
        round_index=round_index,
        round_cap=3,
        surviving_marker_paths=surviving,
    )
    assert prompt_path is not None
    return prompt_path.read_text(encoding="utf-8")


def test_every_conflicted_path_appears(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    for path in _CONFLICTED:
        assert path in rendered


def test_target_branch_and_repo_root_appear(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    assert "main" in rendered
    assert str(tmp_path) in rendered


def test_completion_contract_is_stated(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    assert "declare_complete" in rendered


def test_round_counter_is_rendered(tmp_path: Path) -> None:
    assert "Round 2 of 3" in _render(tmp_path, round_index=2)


def test_first_round_carries_no_surviving_marker_feedback(tmp_path: Path) -> None:
    rendered = _render(tmp_path, round_index=1, surviving=())
    assert "Why this round exists" not in rendered


def test_later_round_names_the_surviving_files(tmp_path: Path) -> None:
    rendered = _render(tmp_path, round_index=2, surviving=("src/alpha.py",))
    assert "Why this round exists" in rendered
    assert "src/alpha.py" in rendered


def test_prompt_carries_no_project_payload(tmp_path: Path) -> None:
    """The session sees the conflict only -- never the run's plan or prompt."""
    rendered = _render(tmp_path, round_index=2, surviving=("src/alpha.py",))
    for forbidden in ("PROMPT.md", "PLAN.md", ".agent/PLAN.md"):
        assert forbidden not in rendered


def test_unlistable_conflicts_fall_back_to_a_search_instruction(
    tmp_path: Path,
) -> None:
    rendered = _render(tmp_path, conflicted=())
    assert "could not be listed" in rendered


def test_the_agent_is_told_not_to_run_git(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    assert "DO NOT run any git command" in rendered


def test_rendering_is_idempotent_for_the_same_round(tmp_path: Path) -> None:
    first = _render(tmp_path)
    second = _render(tmp_path)
    assert first == second
