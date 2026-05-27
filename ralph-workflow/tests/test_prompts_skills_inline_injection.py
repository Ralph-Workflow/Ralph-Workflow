"""Tests for inline skill content injection into prompt templates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.prompts.developer import (
    DeveloperPromptInputs,
    PlanningPromptInputs,
    prompt_developer_iteration_xml_with_context,
    prompt_planning_xml_with_context,
)
from ralph.prompts.materialize import get_inline_skill_content
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _render_developer(tmp_path: Path, inline_content: str, template_name: str) -> str:
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    inputs = DeveloperPromptInputs(
        prompt_content="Fix the bug",
        plan_content="1. Find the bug\n2. Fix it",
        skills_inline_content=inline_content,
    )
    return prompt_developer_iteration_xml_with_context(
        context=context,
        inputs=inputs,
        workspace=workspace,
        session_caps=session_caps,
        template_name=template_name,
    )


def _render_planning(tmp_path: Path, inline_content: str, template_name: str) -> str:
    context = TemplateContext.default()
    workspace = MemoryWorkspace(root=str(tmp_path))
    session_caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    inputs = PlanningPromptInputs(
        prompt_content="Implement the feature",
        skills_inline_content=inline_content,
    )
    return prompt_planning_xml_with_context(
        context=context,
        inputs=inputs,
        workspace=workspace,
        session_caps=session_caps,
        template_name=template_name,
    )


def test_get_inline_skill_content_importable_from_materialize() -> None:
    assert callable(get_inline_skill_content)


def test_developer_inline_content_appears_in_rendered_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "injected-skill.md").write_text(
        "# INJECTED_SKILL_MARKER\nInjected guidance text.",
        encoding="utf-8",
    )
    monkeypatch.delenv("RALPH_SKILLS_PROCESS_DIR", raising=False)
    monkeypatch.setenv("RALPH_INLINE_SKILLS_DIR", str(tmp_path))
    inline = get_inline_skill_content()
    assert "INJECTED_SKILL_MARKER" in inline
    rendered = _render_developer(tmp_path, inline, "developer_iteration.jinja")
    assert "INJECTED_SKILL_MARKER" in rendered


def test_developer_empty_inline_content_shows_normal_skill_names(tmp_path: Path) -> None:
    rendered = _render_developer(tmp_path, "", "developer_iteration.jinja")
    assert "test-driven-development" in rendered or "systematic-debugging" in rendered


def test_planning_inline_content_appears_in_rendered_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "injected-skill.md").write_text(
        "# INJECTED_SKILL_MARKER\nInjected planning guidance.",
        encoding="utf-8",
    )
    monkeypatch.delenv("RALPH_SKILLS_PROCESS_DIR", raising=False)
    monkeypatch.setenv("RALPH_INLINE_SKILLS_DIR", str(tmp_path))
    inline = get_inline_skill_content()
    assert "INJECTED_SKILL_MARKER" in inline
    rendered = _render_planning(tmp_path, inline, "planning.jinja")
    assert "INJECTED_SKILL_MARKER" in rendered


def test_process_skill_dir_does_not_inline_into_planning_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "discoverable-skill.md").write_text(
        "# DISCOVERABLE_ONLY\nDo not inline this.",
        encoding="utf-8",
    )
    monkeypatch.setenv("RALPH_SKILLS_PROCESS_DIR", str(tmp_path))
    monkeypatch.delenv("RALPH_INLINE_SKILLS_DIR", raising=False)
    inline = get_inline_skill_content()
    assert inline == ""


def test_planning_empty_inline_content_shows_normal_skill_names(tmp_path: Path) -> None:
    rendered = _render_planning(tmp_path, "", "planning.jinja")
    assert "writing-plans" in rendered or "brainstorming" in rendered
