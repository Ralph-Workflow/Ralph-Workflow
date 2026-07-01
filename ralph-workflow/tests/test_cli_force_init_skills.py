"""Black-box tests for the ``ralph --force-init-skills`` early-exit flag.

Reuses the ``typer.testing.CliRunner`` pattern from the other CLI tests
in this directory.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
import typer.testing

from ralph.cli import main as main_module

if TYPE_CHECKING:
    from ralph.skills._capability_state import CapabilityState

_RUNNER = typer.testing.CliRunner()


def _invoke(args: list[str], tmp_path: Path) -> typer.testing.Result:
    return _RUNNER.invoke(main_module.app, args, catch_exceptions=False)


@pytest.mark.subprocess_e2e
def test_force_init_skills_flag_invokes_reinstall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The flag must invoke reinstall_baseline_skills exactly once and exit 0."""
    monkeypatch.chdir(tmp_path)
    fake_state = MagicMock()
    fake_state.skills.status = "installed_healthy"
    captured: dict[str, object] = {}

    def fake_reinstall(
        self_obj: object, *, workspace_root: Path
    ) -> tuple[CapabilityState, list[str]]:
        captured["workspace_root"] = workspace_root
        return fake_state, []

    monkeypatch.setattr(
        "ralph.skills.manager.SkillManager.reinstall_baseline_skills",
        fake_reinstall,
    )

    result = _invoke(["--force-init-skills"], tmp_path)
    assert result.exit_code == 0, f"exit_code={result.exit_code}; stdout={result.stdout!r}"
    assert captured.get("workspace_root") == tmp_path, (
        f"Expected workspace_root=tmp_path, got {captured.get('workspace_root')!r}"
    )


@pytest.mark.subprocess_e2e
def test_force_init_skills_flag_does_not_run_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The pipeline runner must NOT be invoked when --force-init-skills is set."""
    monkeypatch.chdir(tmp_path)
    fake_state = MagicMock()
    fake_state.skills.status = "installed_healthy"
    monkeypatch.setattr(
        "ralph.skills.manager.SkillManager.reinstall_baseline_skills",
        lambda *a, **kw: (fake_state, []),
    )

    pipeline_called = {"count": 0}

    def fake_pipeline(*args: object, **kwargs: object) -> int:
        pipeline_called["count"] += 1
        return 0

    monkeypatch.setattr(main_module, "invoke_pipeline", fake_pipeline)

    result = _invoke(["--force-init-skills"], tmp_path)
    assert result.exit_code == 0
    assert pipeline_called["count"] == 0, (
        f"Pipeline was called {pipeline_called['count']} times; expected 0"
    )


@pytest.mark.subprocess_e2e
def test_force_init_skills_flag_early_exit_reached_in_standalone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Standalone (no --init) must still hit the early-exit branch (PA-008 verification)."""
    monkeypatch.chdir(tmp_path)
    fake_state = MagicMock()
    fake_state.skills.status = "installed_healthy"
    captured: dict[str, object] = {}

    def fake_reinstall(
        self_obj: object, *, workspace_root: Path
    ) -> tuple[CapabilityState, list[str]]:
        captured["called"] = True
        return fake_state, []

    monkeypatch.setattr(
        "ralph.skills.manager.SkillManager.reinstall_baseline_skills",
        fake_reinstall,
    )

    result = _invoke(["--force-init-skills"], tmp_path)
    assert result.exit_code == 0
    assert captured.get("called") is True, "Early-exit branch was not reached"


@pytest.mark.subprocess_e2e
def test_force_init_skills_surfaces_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Failures are surfaced as a warning line and the exit code is still 0 (non-fatal)."""
    monkeypatch.chdir(tmp_path)
    fake_state = MagicMock()
    fake_state.skills.status = "installed_healthy"
    monkeypatch.setattr(
        "ralph.skills.manager.SkillManager.reinstall_baseline_skills",
        lambda *a, **kw: (fake_state, ["sibling-conflict-using-superpowers"]),
    )

    result = _invoke(["--force-init-skills"], tmp_path)
    assert result.exit_code == 0
    assert "sibling-conflict-using-superpowers" in result.stdout, (
        f"Expected failure code in stdout, got: {result.stdout!r}"
    )


def test_force_init_skills_imports_public_capability_summary() -> None:
    """main.py must import print_capability_summary from ralph.cli._capability_summary (PA-002)."""
    main_py = Path(main_module.__file__).resolve()
    tree = ast.parse(main_py.read_text(encoding="utf-8"))
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "ralph.cli._capability_summary":
            continue
        for alias in node.names:
            if alias.name == "print_capability_summary":
                found = True
                break
        if found:
            break
    assert found, "main.py must import print_capability_summary from ralph.cli._capability_summary"


@pytest.mark.subprocess_e2e
def test_force_init_skills_branch_surfaces_force_init_skills_hint_when_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """main.py --force-init-skills branch includes the force-init-skills hint on failures."""
    monkeypatch.chdir(tmp_path)
    fake_state = MagicMock()
    fake_state.skills.status = "installed_healthy"
    monkeypatch.setattr(
        "ralph.skills.manager.SkillManager.reinstall_baseline_skills",
        lambda *a, **kw: (fake_state, ["sibling-conflict-using-superpowers"]),
    )

    result = _invoke(["--force-init-skills"], tmp_path)
    assert result.exit_code == 0
    normalized = " ".join(result.stdout.split())
    assert "ralph --force-init-skills" in normalized, (
        f"Expected `ralph --force-init-skills` hint in stdout, got: {normalized!r}"
    )
    assert "sibling-conflict-using-superpowers" in normalized, (
        f"Expected failure-code prefix in stdout, got: {normalized!r}"
    )
    assert "ralph --diagnose" in normalized, (
        f"Expected diagnose hint in stdout, got: {normalized!r}"
    )
