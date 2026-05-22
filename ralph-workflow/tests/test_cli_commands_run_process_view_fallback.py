"""Tests for process-scoped skill view fallback wiring in run.py."""

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING

from ralph.cli.commands import run as run_module

if TYPE_CHECKING:
    import pytest


def test_maybe_enter_process_view_returns_none_when_machine_global_skills_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_module, "has_machine_global_skills", lambda: True)
    with ExitStack() as stack:
        assert run_module._maybe_enter_process_view(stack) is None


def test_maybe_enter_process_view_enters_context_when_machine_global_skills_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered: list[str] = []

    class FakeView:
        def __enter__(self) -> str:
            entered.append("enter")
            return "target-dir"

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: object | None,
        ) -> bool:
            entered.append("exit")
            return False

    def _has_machine_global_skills() -> bool:
        return False

    monkeypatch.setattr(run_module, "has_machine_global_skills", _has_machine_global_skills)
    monkeypatch.setattr(run_module, "SkillsProcessView", FakeView)
    with ExitStack() as stack:
        target = run_module._maybe_enter_process_view(stack)
        assert target == "target-dir"
    assert entered == ["enter", "exit"]
