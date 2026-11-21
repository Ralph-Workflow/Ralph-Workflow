from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.pipeline.publication_gate import (
    MAX_INCONCLUSIVE_GATE_RUNS,
    PublicationGateRun,
    blocked_publication,
    check_publication_gate,
    held_publication_reason,
)

if TYPE_CHECKING:
    from pathlib import Path

_TREE = "c" * 40


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


def test_an_inconclusive_tree_is_held_only_after_its_last_allowed_rerun(tmp_path: Path) -> None:
    _declare(tmp_path, "`make verify`")
    held: list[bool] = []

    for _run in range(MAX_INCONCLUSIVE_GATE_RUNS):
        check_publication_gate(tmp_path, "HEAD", runner=_runner(-15, ""), tree_of=_tree)
        held.append(blocked_publication(tmp_path, "HEAD", tree_of=_tree) is not None)

    assert held == [False] * (MAX_INCONCLUSIVE_GATE_RUNS - 1) + [True]


@pytest.mark.parametrize(
    ("declaration", "exit_code", "output", "named"),
    [
        pytest.param("`make verify`", 1, "[verify] FAILED: lint\n", "already refused", id="red"),
        pytest.param(
            "`make verify`",
            3,
            "[DEADLINE REACHED] budget exceeded\n",
            "was inconclusive",
            id="inconclusive",
        ),
        pytest.param(
            "`make verify`", 127, "sh: 1: make: not found\n", "misconfigured", id="invocation"
        ),
        pytest.param("run the tests (see the wiki", 0, "", "misconfigured", id="misconfigured"),
    ],
)
def test_a_held_publication_names_why_the_gate_holds_the_commits(
    tmp_path: Path, declaration: str, exit_code: int, output: str, named: str
) -> None:
    _declare(tmp_path, declaration)
    for _run in range(MAX_INCONCLUSIVE_GATE_RUNS):
        check_publication_gate(tmp_path, "HEAD", runner=_runner(exit_code, output), tree_of=_tree)

    blocked = blocked_publication(tmp_path, "HEAD", tree_of=_tree)

    assert blocked is not None
    assert named in held_publication_reason(blocked)
    assert _TREE in held_publication_reason(blocked) or named == "misconfigured"
