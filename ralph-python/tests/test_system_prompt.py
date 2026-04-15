from __future__ import annotations

from pathlib import Path

from ralph.prompts.system_prompt import build_system_prompt, materialize_system_prompt


def test_build_system_prompt_includes_unattended_mode_and_current_prompt_reference() -> None:
    prompt = build_system_prompt(current_prompt_path="/tmp/project/.agent/CURRENT_PROMPT.md")

    assert "UNATTENDED MODE" in prompt
    assert "/tmp/project/.agent/CURRENT_PROMPT.md" in prompt
    assert "source of truth" in prompt


def test_materialize_system_prompt_writes_file(tmp_path: Path) -> None:
    current_prompt = tmp_path / ".agent" / "CURRENT_PROMPT.md"
    current_prompt.parent.mkdir(parents=True, exist_ok=True)
    current_prompt.write_text("Fix the bug", encoding="utf-8")

    system_prompt_path = Path(materialize_system_prompt(workspace_root=tmp_path, name="review"))

    assert system_prompt_path.exists()
    assert system_prompt_path.read_text(encoding="utf-8")
    assert str(current_prompt) in system_prompt_path.read_text(encoding="utf-8")
