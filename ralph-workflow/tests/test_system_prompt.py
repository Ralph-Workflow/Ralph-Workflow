"""Tests for system prompt materialization."""

from __future__ import annotations

from pathlib import Path

import ralph.prompts.system_prompt as system_prompt_module
from ralph.prompts.system_prompt import materialize_system_prompt


def test_materialize_system_prompt_creates_prompt_history_snapshot(
    tmp_path: Path,
    monkeypatch,
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
    monkeypatch,
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
