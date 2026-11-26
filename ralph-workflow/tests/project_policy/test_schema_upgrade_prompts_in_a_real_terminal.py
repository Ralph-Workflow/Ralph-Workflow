from __future__ import annotations

import io
import sys
from collections.abc import Sequence

import pytest

from ralph.project_policy import markers, maybe_resolve_schema_upgrade
from ralph.workspace.memory import MemoryWorkspace


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


class _Pipe(io.StringIO):
    def isatty(self) -> bool:
        return False


def _outdated_pack() -> MemoryWorkspace:
    ws = MemoryWorkspace()
    for name in markers.CORE_POLICY_FILES:
        ws.write(
            f"{markers.CANONICAL_DIR}{name}",
            f"<!-- ralph-policy-schema: v6 -->\n<!-- ralph-policy-id: {name} -->\n# Policy\n",
        )
    return ws


def test_a_real_terminal_is_offered_the_upgrade_without_an_injected_tty_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", _Terminal())
    monkeypatch.setattr(sys, "stdout", _Terminal())
    asked: list[str] = []

    def select(question: str, choices: Sequence[object], default: str) -> str:
        asked.append(question)
        return "upgrade"

    messages: list[str] = []
    resolved = maybe_resolve_schema_upgrade(_outdated_pack(), messages.append, select=select)

    assert resolved is True
    assert asked, "an interactive terminal must be asked to upgrade or freeze"
    assert not any("rerun interactively" in m for m in messages)


def test_a_pipe_still_refuses_to_guess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _Pipe())
    monkeypatch.setattr(sys, "stdout", _Pipe())
    messages: list[str] = []

    resolved = maybe_resolve_schema_upgrade(_outdated_pack(), messages.append)

    assert resolved is False
    assert any("rerun interactively" in m for m in messages)
