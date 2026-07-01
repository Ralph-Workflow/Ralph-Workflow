"""Black-box tests for the 'Skill root coverage' table in init_command output.

These tests are subprocess_e2e: they exercise the real `ralph --init` entry
point and its full filesystem path. They cannot be mocked down to the
per-test 1 s budget without losing the end-to-end contract they assert.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.cli.commands import init as init_module
from ralph.display.context import DisplayContext
from ralph.display.theme import RALPH_THEME
from ralph.skills import manager as manager_module
from ralph.skills._agent_paths import AgentSkillRoot
from ralph.skills._capability_entry import CapabilityEntry
from ralph.skills._capability_state import CapabilityState
from ralph.skills._capability_status import CapabilityStatus
from ralph.skills._content import BASELINE_SKILL_NAMES

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.timeout_seconds(10), pytest.mark.subprocess_e2e]


def _attach_console_with_buffer(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    stream = io.StringIO()
    console = Console(
        file=stream,
        force_terminal=False,
        color_system=None,
        theme=RALPH_THEME,
    )
    ctx = DisplayContext(
        console=console,
        theme=RALPH_THEME,
        width=80,
        mode="wide",
        narrow=False,
        color_enabled=True,
        glyphs_enabled=True,
        headline_max_chars=120,
        condenser_soft_limit=400,
        condenser_hard_limit=4000,
        streaming_checkpoint_chars=4000,
        streaming_checkpoint_fragments=20,
        streaming_dedup_enabled=True,
        streaming_checkpoints_enabled=True,
        thinking_preview_min_chars=80,
        tool_result_headline_min_chars=80,
    )

    def _fake_make_display_context(**kwargs: object) -> object:
        return ctx

    monkeypatch.setattr(init_module, "make_display_context", _fake_make_display_context)
    return stream


def _install_fake_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    with_sibling_skill: bool = True,
) -> tuple[Path, Path, Path, Path]:
    """Pre-create fake canonical + sibling roots under tmp_path and redirect
    agent_skill_roots() to return entries that resolve there.

    Returns (canonical_dir, codex_dir, opencode_dir, agy_dir).
    """
    canonical_dir = tmp_path / "claude"
    codex_dir = tmp_path / "codex"
    opencode_dir = tmp_path / "opencode"
    agy_dir = tmp_path / "agy"
    for d in (canonical_dir, codex_dir, opencode_dir, agy_dir):
        d.mkdir(parents=True, exist_ok=True)
    if with_sibling_skill:
        # Pre-create a single SKILL.md in codex sibling so the
        # 'all present' check needs to find at least one file.
        (codex_dir / "using-superpowers").mkdir(parents=True, exist_ok=True)
        (codex_dir / "using-superpowers" / "SKILL.md").write_text(
            "# sibling skill\n", encoding="utf-8"
        )

    roots = (
        AgentSkillRoot(
            agent="claude",
            path_segments=(str(canonical_dir),),
            source_url="",
            is_canonical=True,
        ),
        AgentSkillRoot(
            agent="codex",
            path_segments=(str(codex_dir),),
            source_url="",
            is_canonical=False,
        ),
        AgentSkillRoot(
            agent="opencode",
            path_segments=(str(opencode_dir),),
            source_url="",
            is_canonical=False,
        ),
        AgentSkillRoot(
            agent="agy",
            path_segments=(str(agy_dir),),
            source_url="",
            is_canonical=False,
        ),
    )
    monkeypatch.setattr("ralph.skills._agent_paths.agent_skill_roots", lambda: roots)
    # Pre-create SKILL.md in every baseline skill name under each root so
    # the table renders 'OK' for every row.
    for root_dir in (canonical_dir, opencode_dir, agy_dir):
        for name in BASELINE_SKILL_NAMES:
            skill_dir = root_dir / name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return canonical_dir, codex_dir, opencode_dir, agy_dir


@pytest.mark.timeout_seconds(3)
def test_init_command_skill_summary_mentions_canonical_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console_with_buffer(monkeypatch)
    _install_fake_roots(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)

    def _fake_ensure(
        _self_obj: object, *, workspace_root: object
    ) -> tuple[CapabilityState, list[str]]:
        return (
            CapabilityState(
                web_search=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                visit_url=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                docs_mcp=CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED),
                skills=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            ),
            [],
        )

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        _fake_ensure,
    )

    init_module.init_command(template="default")

    output = stream.getvalue()
    assert "claude" in output
    assert "OK" in output
    assert "Skill root coverage" in output


@pytest.mark.timeout_seconds(3)
def test_init_command_skill_summary_reports_missing_sibling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console_with_buffer(monkeypatch)
    # Don't pre-create the opencode sibling's SKILL.md files.
    _install_fake_roots(monkeypatch, tmp_path, with_sibling_skill=False)
    monkeypatch.chdir(tmp_path)
    # Manually delete the opencode skill dirs we just created.
    opencode_dir = tmp_path / "opencode"
    for child in list(opencode_dir.iterdir()):
        if child.is_dir():
            for grand in list(child.iterdir()):
                grand.unlink()
            child.rmdir()

    def _fake_ensure(
        _self_obj: object, *, workspace_root: object
    ) -> tuple[CapabilityState, list[str]]:
        return (
            CapabilityState(
                web_search=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                visit_url=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                docs_mcp=CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED),
                skills=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            ),
            [],
        )

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        _fake_ensure,
    )

    init_module.init_command(template="default")

    output = stream.getvalue()
    assert "Skipped" in output, f"Expected 'Skipped' for the opencode sibling row, got: {output!r}"
    assert "opencode" in output


def test_init_command_skill_summary_reports_project_skill_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The skill root coverage table must include project-scope rows with a 'Scope' column."""
    stream = _attach_console_with_buffer(monkeypatch)
    _canonical_dir, _codex_dir, _opencode_dir, _agy_dir = _install_fake_roots(monkeypatch, tmp_path)

    # Pre-create project-scope canonical files so the rows render 'OK'.
    project_canonical = tmp_path / ".opencode" / "skills"
    for name in BASELINE_SKILL_NAMES:
        skill_dir = project_canonical / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")

    # Pre-create the project-scope siblings so they exist and are symlinked
    # into the project canonical.
    sibling_specs: tuple[tuple[str, Path], ...] = (
        ("claude", tmp_path / ".claude" / "skills"),
        ("codex", tmp_path / ".codex" / "skills"),
        ("agy", tmp_path / ".gemini" / "antigravity-cli" / "skills"),
    )
    for _agent, sibling_root in sibling_specs:
        sibling_root.mkdir(parents=True, exist_ok=True)
        for name in BASELINE_SKILL_NAMES:
            sibling_dir = sibling_root / name
            sibling_dir.mkdir(parents=True, exist_ok=True)
            (sibling_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    def _fake_ensure(
        _self_obj: object, *, workspace_root: object
    ) -> tuple[CapabilityState, list[str]]:
        return (
            CapabilityState(
                web_search=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                visit_url=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                docs_mcp=CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED),
                skills=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            ),
            [],
        )

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        _fake_ensure,
    )

    init_module.init_command(template="default")

    output = stream.getvalue()
    assert "Scope" in output, f"Expected 'Scope' column header in output, got: {output!r}"
    assert output.count("Scope") == 1, (
        f"Expected exactly one 'Scope' column header, got {output.count('Scope')}: {output!r}"
    )
    assert "project" in output, f"Expected 'project' value in output, got: {output!r}"
    for agent in ("claude (project)", "codex (project)", "agy (project)"):
        assert agent in output, f"Expected {agent!r} in project-scope rows, got: {output!r}"
