"""Tests for system prompt materialization."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import ralph.prompts.system_prompt as system_prompt_module
from ralph.prompts.system_prompt import materialize_system_prompt

if TYPE_CHECKING:
    import pytest


def test_materialize_system_prompt_creates_prompt_history_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        system_prompt_module,
        "_history_timestamp",
        lambda: "20260508T120000Z",
        raising=False,
    )
    (tmp_path / "PROMPT.md").write_text("new prompt", encoding="utf-8")

    materialize_system_prompt(
        workspace_root=tmp_path,
        name="planning",
    )

    history_path = tmp_path / ".agent" / "prompt_history" / "PROMPT_20260508T120000Z.md"
    assert history_path.read_text(encoding="utf-8") == "new prompt"


def test_materialize_system_prompt_refreshes_current_prompt_from_prompt_md(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        system_prompt_module,
        "_history_timestamp",
        lambda: "20260508T120001Z",
        raising=False,
    )
    (tmp_path / ".agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "PROMPT.md").write_text("new prompt", encoding="utf-8")
    current_prompt_path = tmp_path / ".agent" / "CURRENT_PROMPT.md"
    current_prompt_path.write_text("old prompt", encoding="utf-8")

    system_prompt_path = materialize_system_prompt(
        workspace_root=tmp_path,
        name="planning",
    )

    assert current_prompt_path.read_text(encoding="utf-8") == "new prompt"
    history_path = tmp_path / ".agent" / "prompt_history" / "PROMPT_20260508T120001Z.md"
    assert history_path.read_text(encoding="utf-8") == "new prompt"
    system_prompt = Path(system_prompt_path).read_text(encoding="utf-8")
    assert str(current_prompt_path) in system_prompt
    assert "source of truth for the current goal" in system_prompt


def test_materialize_system_prompt_includes_current_plan_handoff_when_present(
    tmp_path: Path,
) -> None:
    (tmp_path / ".agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "PROMPT.md").write_text("Implement the current plan", encoding="utf-8")
    plan_path = tmp_path / ".agent" / "PLAN.md"
    plan_path.write_text(
        "# Execution Plan\n\n1. Keep this pinned across compacts.\n",
        encoding="utf-8",
    )

    system_prompt_path = materialize_system_prompt(
        workspace_root=tmp_path,
        name="development",
    )

    system_prompt = Path(system_prompt_path).read_text(encoding="utf-8")
    assert str(plan_path) in system_prompt
    assert "Use the canonical task context from this file" in system_prompt
    assert "Treat that file as background context for the current task" in system_prompt
    assert (
        "Treat that file as the source of truth for the current goal and execution steps"
        in system_prompt
    )


def test_materialize_system_prompt_ignores_plan_handoff_during_planning(
    tmp_path: Path,
) -> None:
    (tmp_path / ".agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "PROMPT.md").write_text("Plan the work", encoding="utf-8")
    plan_path = tmp_path / ".agent" / "PLAN.md"
    plan_path.write_text(
        "# Stale Plan\n\nDo not reuse this during planning.\n",
        encoding="utf-8",
    )

    system_prompt_path = materialize_system_prompt(
        workspace_root=tmp_path,
        name="planning",
    )

    system_prompt = Path(system_prompt_path).read_text(encoding="utf-8")
    assert str(plan_path) not in system_prompt
    assert "source of truth for the current goal and execution steps" not in system_prompt
    assert "source of truth for the current goal" in system_prompt
