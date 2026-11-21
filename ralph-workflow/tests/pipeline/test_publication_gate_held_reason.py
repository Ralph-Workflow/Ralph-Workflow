from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.publication_gate import (
    MAX_INCONCLUSIVE_GATE_RUNS,
    PublicationGateRun,
    check_publication_gate,
    red_publication_gate,
)

if TYPE_CHECKING:
    from pathlib import Path

_TREE = "c" * 40

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_a_held_publication_names_why_the_gate_holds_the_commits.removed": (
        "user requirement: the publication gate never holds the commits, so "
        "held_publication_reason and its parametrized test are deleted"
    ),
    "test_an_inconclusive_tree_is_held_only_after_its_last_allowed_rerun": (
        "operator directive: a tree whose inconclusive re-runs are used up lands as time-only "
        "instead of being held forever; renamed to "
        "test_an_inconclusive_tree_is_never_held_and_lands_once_its_reruns_are_used_up"
    ),
    "test_a_held_publication_names_why_the_gate_holds_the_commits": (
        "user requirement: the publication gate never holds the commits, so there is no held "
        "reason to name; the advisory landing is pinned by tests/test_publication_gate_e2e.py"
    ),
}


def _declare(root: Path, value: str) -> None:
    policy = root / "docs" / "ralph-workflow-policy" / "verification-policy.md"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(
        f"# Verification\n\nRALPH-FACT: authoritative_verify_command: {value}\n", encoding="utf-8"
    )


def _tree(_root: Path, _commit: str) -> str:
    return _TREE


def _runner(exit_code: int, output: str) -> object:
    def run(_command: str, _cwd: Path, _timeout: float) -> PublicationGateRun:
        return PublicationGateRun(exit_code=exit_code, output=output, timed_out=False)

    return run


def test_an_inconclusive_tree_is_never_held_and_lands_once_its_reruns_are_used_up(
    tmp_path: Path,
) -> None:
    _declare(tmp_path, "`make verify`")
    held: list[bool] = []

    for _run in range(MAX_INCONCLUSIVE_GATE_RUNS):
        check_publication_gate(tmp_path, "HEAD", runner=_runner(-15, ""), tree_of=_tree)
        held.append(red_publication_gate(tmp_path, "HEAD", tree_of=_tree) is not None)

    assert held == [False] * MAX_INCONCLUSIVE_GATE_RUNS
