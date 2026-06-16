"""Black-box tests for the auto-skill-install contract on every `ralph --init` invocation.

The prompt requires skill installation to be automatic on every `ralph --init`
regardless of which bootstrap path fires (first run OR re-run where every
bootstrap result is `skipped`). These tests pin that contract.
"""

from __future__ import annotations

from io import StringIO
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

    from ralph.skills.manager import SkillManager


def _attach_console(monkeypatch: pytest.MonkeyPatch) -> StringIO:
    stream = StringIO()
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
) -> tuple[Path, Path, Path, Path]:
    """Pre-create fake canonical + sibling roots under tmp_path and redirect
    agent_skill_roots() to return entries that resolve there.
    """
    canonical_dir = tmp_path / "claude"
    codex_dir = tmp_path / "codex"
    opencode_dir = tmp_path / "opencode"
    agy_dir = tmp_path / "agy"
    for d in (canonical_dir, codex_dir, opencode_dir, agy_dir):
        d.mkdir(parents=True, exist_ok=True)
    for root_dir in (canonical_dir, codex_dir, opencode_dir, agy_dir):
        for name in BASELINE_SKILL_NAMES:
            skill_dir = root_dir / name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
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
    return canonical_dir, codex_dir, opencode_dir, agy_dir


def test_init_command_always_runs_ensure_baseline_capabilities_on_first_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """init_command on a fresh empty dir must call ensure_baseline_capabilities."""
    _attach_console(monkeypatch)
    _install_fake_roots(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)

    called: list[Path] = []

    def fake_ensure(
        _self_obj: SkillManager, *, workspace_root: Path
    ) -> tuple[CapabilityState, list[str]]:
        called.append(workspace_root)
        return CapabilityState(), []

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        fake_ensure,
    )

    init_module.init_command(template="default")

    assert called == [tmp_path], (
        f"Expected ensure_baseline_capabilities called once with workspace_root=tmp_path; "
        f"got {called!r}"
    )


def test_init_command_runs_ensure_baseline_capabilities_when_configs_already_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pre-existing .agent/ must NOT skip the skill install on a re-run.

    The full capability summary table must be printed in the captured output
    on the re-run path so a regression that silently breaks the
    per-agent symlink fan-out is visible to the user.
    """
    stream = _attach_console(monkeypatch)
    _install_fake_roots(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    # Pre-create local support configs so the bootstrap results are all 'skipped'.
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "mcp.toml").write_text("# existing\n", encoding="utf-8")
    (agent_dir / "pipeline.toml").write_text("# existing\n", encoding="utf-8")
    (agent_dir / "artifacts.toml").write_text("# existing\n", encoding="utf-8")

    def fake_ensure(
        _self_obj: SkillManager, *, workspace_root: Path
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
        fake_ensure,
    )

    init_module.init_command(template="default")

    output = stream.getvalue()
    assert "Built-in" in output, f"Expected 'Built-in' label in re-run output, got: {output!r}"
    assert "Managed" in output, f"Expected 'Managed' label in re-run output, got: {output!r}"
    assert "Skill root coverage" in output, (
        f"Expected 'Skill root coverage' in re-run output, got: {output!r}"
    )


def test_init_command_runs_capability_refresh_on_every_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Replacing the legacy `test_init_command_fallback_path_skips_capability_refresh`.

    The legacy test pinned the buggy behavior (capability refresh runs only on
    first run). The new contract is: capability refresh runs on EVERY
    `ralph --init`, including the re-run path. Assert the call count is 2
    across two invocations on a re-run.
    """
    _attach_console(monkeypatch)
    _install_fake_roots(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    calls = 0

    def fake_ensure(
        _self_obj: SkillManager, *, workspace_root: Path
    ) -> tuple[CapabilityState, list[str]]:
        nonlocal calls
        calls += 1
        return CapabilityState(), []

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        fake_ensure,
    )

    init_module.init_command(template="default")
    assert calls == 1
    init_module.init_command(template="default")
    assert calls == 2


def test_init_command_runs_ensure_baseline_capabilities_when_global_config_path_passed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """config_path=<custom> branch must still trigger the skill install.

    The legacy one-shot init path is the `config_path is not None and not
    config_path.exists()` branch. When that branch fires, the skill
    install MUST still be invoked so a one-shot init copy is a complete
    setup.
    """
    _attach_console(monkeypatch)
    _install_fake_roots(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    # Note: we intentionally do NOT pre-create config_path — the branch
    # under test is the one where config_path does not yet exist.
    config_path = tmp_path / "custom.toml"

    called: list[Path] = []

    def fake_ensure(
        _self_obj: SkillManager, *, workspace_root: Path
    ) -> tuple[CapabilityState, list[str]]:
        called.append(workspace_root)
        return CapabilityState(), []

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        fake_ensure,
    )

    init_module.init_command(template="default", config_path=config_path)

    assert called == [tmp_path], (
        f"Expected ensure_baseline_capabilities called with workspace_root=tmp_path; got {called!r}"
    )


def test_init_command_skill_install_failure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A raising skill install must NOT block init from creating PROMPT.md / .agent/."""
    stream = _attach_console(monkeypatch)
    _install_fake_roots(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)

    def fake_ensure_that_raises(*args: object, **kwargs: object) -> object:
        raise RuntimeError("simulated skill install failure")

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        fake_ensure_that_raises,
    )

    init_module.init_command(template="default")

    assert (tmp_path / "PROMPT.md").exists()
    assert (tmp_path / ".agent" / "mcp.toml").exists()
    output = stream.getvalue()
    assert "Ralph" in output
    assert "Created" in output


def test_init_command_surfaces_skill_install_failure_codes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the manager returns a failure code, init must print a visible signal."""
    stream = _attach_console(monkeypatch)
    _install_fake_roots(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)

    def fake_ensure(
        _self_obj: SkillManager, *, workspace_root: Path
    ) -> tuple[CapabilityState, list[str]]:
        return (
            CapabilityState(
                web_search=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                visit_url=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                docs_mcp=CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED),
                skills=CapabilityEntry(status=CapabilityStatus.NEEDS_REPAIR),
            ),
            ["sibling-conflict-using-superpowers"],
        )

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        fake_ensure,
    )

    init_module.init_command(template="default")

    output = stream.getvalue()
    assert "Skills auto-install reported: sibling-conflict-using-superpowers" in output, (
        f"Expected literal failure-code line in output, got: {output!r}"
    )
    # The diagnose hint may be word-wrapped by the rich console, so we
    # normalize whitespace before checking the literal hint.
    normalized = " ".join(output.split())
    assert "ralph --diagnose" in normalized, (
        f"Expected 'ralph --diagnose' hint in normalized output, got: {normalized!r}"
    )


def test_init_command_surfaces_skill_install_failure_codes_on_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The re-run (all-skipped) path must also surface skill-install failure codes."""
    stream = _attach_console(monkeypatch)
    _install_fake_roots(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "mcp.toml").write_text("# existing\n", encoding="utf-8")
    (agent_dir / "pipeline.toml").write_text("# existing\n", encoding="utf-8")
    (agent_dir / "artifacts.toml").write_text("# existing\n", encoding="utf-8")

    def fake_ensure(
        _self_obj: SkillManager, *, workspace_root: Path
    ) -> tuple[CapabilityState, list[str]]:
        return (
            CapabilityState(
                web_search=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                visit_url=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                docs_mcp=CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED),
                skills=CapabilityEntry(status=CapabilityStatus.NEEDS_REPAIR),
            ),
            ["sibling-conflict-using-superpowers"],
        )

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        fake_ensure,
    )

    init_module.init_command(template="default")

    output = stream.getvalue()
    assert "Skills auto-install reported: sibling-conflict-using-superpowers" in output, (
        f"Expected literal failure-code line in re-run output, got: {output!r}"
    )


def test_init_command_surfaces_force_init_skills_hint_in_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The init-path conflict warning must include the `ralph --force-init-skills` hint."""
    stream = _attach_console(monkeypatch)
    _install_fake_roots(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)

    def fake_ensure(
        _self_obj: SkillManager, *, workspace_root: Path
    ) -> tuple[CapabilityState, list[str]]:
        return (
            CapabilityState(
                web_search=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                visit_url=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                docs_mcp=CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED),
                skills=CapabilityEntry(status=CapabilityStatus.NEEDS_REPAIR),
            ),
            ["sibling-conflict-using-superpowers"],
        )

    monkeypatch.setattr(
        manager_module.SkillManager,
        "ensure_baseline_capabilities",
        fake_ensure,
    )

    init_module.init_command(template="default")

    output = stream.getvalue()
    normalized = " ".join(output.split())
    assert "ralph --force-init-skills" in normalized, (
        f"Expected 'ralph --force-init-skills' hint in normalized output, got: {normalized!r}"
    )
    assert "ralph --diagnose" in normalized, (
        f"Expected 'ralph --diagnose' hint in normalized output, got: {normalized!r}"
    )
    assert "Skills auto-install reported: sibling-conflict-using-superpowers" in output, (
        f"Expected failure-code prefix to be preserved, got: {output!r}"
    )
