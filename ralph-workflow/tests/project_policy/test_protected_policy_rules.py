from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.project_policy import markers, validators
from ralph.project_policy.starters import read_starter
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.policy_corpus import seed_complete_corpus, stack

if TYPE_CHECKING:
    from ralph.project_policy.models import PolicyFinding


def _normalized(text: str) -> str:
    return " ".join(text.replace("*", "").split()).lower()


def _starter(filename: str) -> str:
    return read_starter(filename)


def _ready_workspace() -> MemoryWorkspace:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    return ws


def _protected(ws: MemoryWorkspace) -> list[PolicyFinding]:
    return [
        f
        for f in validators.validate_readiness(ws, stack())
        if f.requirement_id.startswith(markers.ID_PROTECTED_RULE)
    ]


@pytest.mark.parametrize(
    ("filename", "clause"),
    [(name, clause) for name, clauses in markers.PROTECTED_RULES.items() for clause in clauses],
)
def test_every_protected_rule_is_shipped_by_its_starter(filename: str, clause: str) -> None:
    assert clause in _normalized(_starter(filename))


def test_a_complete_corpus_keeps_every_protected_rule() -> None:
    assert _protected(_ready_workspace()) == []


def test_weakening_the_comment_ban_blocks_readiness() -> None:
    ws = _ready_workspace()
    path = f"{markers.CANONICAL_DIR}clean-code-policy.md"
    content = ws.read(path)
    ws.write(
        path,
        content.replace(
            "source code contains no comments.",
            "Source code carries a comment only when it records a constraint a reader cannot infer.",
        ),
    )

    findings = _protected(ws)

    assert [f.requirement_id for f in findings] == [
        f"{markers.ID_PROTECTED_RULE}:clean-code-policy.md"
    ]
    assert "source code contains no comments" in findings[0].missing_evidence
    assert "restore" in findings[0].required_outcome
    assert "never" in findings[0].required_outcome


@pytest.mark.parametrize("filename", ["linting-policy.md", "typechecking-policy.md"])
def test_widening_the_permitted_bypass_reasons_blocks_readiness(filename: str) -> None:
    ws = _ready_workspace()
    path = f"{markers.CANONICAL_DIR}{filename}"
    ws.write(
        path,
        ws.read(path).replace(
            "rule strength only increases.",
            "A rule may be disabled when it fires on common safe use.",
        ),
    )

    findings = _protected(ws)

    assert [f.requirement_id for f in findings] == [f"{markers.ID_PROTECTED_RULE}:{filename}"]
    assert "rule strength only increases" in findings[0].missing_evidence


def test_raising_a_size_limit_blocks_readiness() -> None:
    ws = _ready_workspace()
    path = f"{markers.CANONICAL_DIR}clean-code-policy.md"
    ws.write(path, ws.read(path).replace("exceeds 500 lines", "exceeds 2000 lines"))

    findings = _protected(ws)

    assert [f.requirement_id for f in findings] == [
        f"{markers.ID_PROTECTED_RULE}:clean-code-policy.md"
    ]
