"""Black-box tests for the 'Skill root coverage' table in init_command output."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

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

    import pytest


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
    monkeypatch.setattr(
        "ralph.skills._agent_paths.agent_skill_roots", lambda: roots
    )
    # Pre-create SKILL.md in every baseline skill name under each root so
    # the table renders 'OK' for every row.
    for root_dir in (canonical_dir, opencode_dir, agy_dir):
        for name in BASELINE_SKILL_NAMES:
            skill_dir = root_dir / name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                f"# {name}\n", encoding="utf-8"
            )
    return canonical_dir, codex_dir, opencode_dir, agy_dir


def test_init_command_skill_summary_mentions_canonical_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console_with_buffer(monkeypatch)
    _install_fake_roots(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)

    def _fake_ensure(
        _self_obj: object, *, workspace_root: object
    ) -> CapabilityState:
        return CapabilityState(
            web_search=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            visit_url=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            docs_mcp=CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED),
            skills=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
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
    ) -> CapabilityState:
        return CapabilityState(
            web_search=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            visit_url=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            docs_mcp=CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED),
            skills=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
        )

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        _fake_ensure,
    )

    init_module.init_command(template="default")

    output = stream.getvalue()
    assert "Skipped" in output, (
        f"Expected 'Skipped' for the opencode sibling row, got: {output!r}"
    )
    assert "opencode" in output
