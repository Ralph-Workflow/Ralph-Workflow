from __future__ import annotations

import io
import sys
from typing import TYPE_CHECKING

import pytest

from ralph.project_policy import markers, maybe_resolve_schema_upgrade, validators
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.policy_corpus import seed_complete_corpus, stack

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.project_policy.models import PolicyFinding

_V6 = "<!-- ralph-policy-schema: v6 -->"


class _Pipe(io.StringIO):
    def isatty(self) -> bool:
        return False


def _outdated_pack() -> MemoryWorkspace:
    ws = MemoryWorkspace()
    for name in markers.CORE_POLICY_FILES:
        ws.write(
            f"{markers.CANONICAL_DIR}{name}",
            f"{_V6}\n<!-- ralph-policy-id: {name} -->\n# Policy\n",
        )
    return ws


def _answering(answer: str, asked: list[str]):
    def select(question: str, choices: Sequence[object], default: str) -> str:
        del choices, default
        asked.append(question)
        return answer

    return select


def test_an_upgrade_choice_is_remembered_so_the_next_start_does_not_ask_again() -> None:
    ws = _outdated_pack()
    asked: list[str] = []
    assert maybe_resolve_schema_upgrade(
        ws, lambda _m: None, select=_answering("upgrade", asked), is_tty=lambda: True
    )

    assert maybe_resolve_schema_upgrade(
        ws, lambda _m: None, select=_answering("freeze", asked), is_tty=lambda: True
    )

    assert len(asked) == 1
    assert ws.read(f"{markers.CANONICAL_DIR}testing-policy.md").startswith(_V6)


def test_a_remembered_upgrade_lets_an_unattended_start_keep_its_readiness_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _outdated_pack()
    maybe_resolve_schema_upgrade(
        ws, lambda _m: None, select=_answering("upgrade", []), is_tty=lambda: True
    )
    monkeypatch.setattr(sys, "stdin", _Pipe())
    monkeypatch.setattr(sys, "stdout", _Pipe())
    messages: list[str] = []

    assert maybe_resolve_schema_upgrade(ws, messages.append) is True
    assert not any("rerun interactively" in m for m in messages)


def test_an_upgrade_recorded_for_an_older_schema_is_asked_again() -> None:
    ws = _outdated_pack()
    ws.write(markers.SCHEMA_CHOICE_REL_PATH, '{"choice": "upgrade", "schema": "v1"}')
    asked: list[str] = []

    maybe_resolve_schema_upgrade(
        ws, lambda _m: None, select=_answering("upgrade", asked), is_tty=lambda: True
    )

    assert len(asked) == 1


def _schema_findings(ws: MemoryWorkspace) -> list[PolicyFinding]:
    return [
        f
        for f in validators.validate_readiness(ws, stack())
        if f.requirement_id.startswith(f"{markers.ID_MARKER_MISSING}:schema-")
        or f.requirement_id.startswith(markers.ID_SCHEMA_CONTENT)
    ]


def test_an_outdated_policy_is_told_to_be_rewritten_not_relabelled() -> None:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    ws.write(path, ws.read(path).replace(markers.POLICY_SCHEMA_MARKER, _V6, 1))

    (finding,) = [f for f in _schema_findings(ws) if f.path == path]

    assert "rewrite" in finding.required_outcome
    assert "never change only the marker" in finding.required_outcome
    assert "freeze" not in finding.required_outcome.split("never")[0]


@pytest.mark.parametrize(
    ("filename", "retired"),
    [
        (
            "verification-policy.md",
            "Over a cap after optimizing: record the measured figure, with no\n"
            "headroom, as the enforced budget, so the gate fails the moment the\n"
            "project gets slower.",
        ),
        ("verification-policy.md", "enforced by the gate itself (fail on exceed)."),
        (
            "testing-policy.md",
            "record its measured time, with no headroom, as `suite_time_budget`.",
        ),
    ],
)
def test_a_current_marker_on_retired_rules_is_not_an_upgrade(filename: str, retired: str) -> None:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    path = f"{markers.CANONICAL_DIR}{filename}"
    ws.write(path, ws.read(path) + "\n" + retired + "\n")

    findings = [f for f in _schema_findings(ws) if f.path == path]

    assert [f.requirement_id for f in findings] == [f"{markers.ID_SCHEMA_CONTENT}:{filename}"]
    assert findings[0].blocking is True
    assert "only past the cap" in findings[0].required_outcome


def test_the_bundled_corpus_carries_no_retired_rules() -> None:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)

    assert _schema_findings(ws) == []
