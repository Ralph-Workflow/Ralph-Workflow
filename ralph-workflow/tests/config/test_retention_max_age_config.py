from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import ralph.cli.commands.run as run_module
from ralph.config.general_config import GeneralConfig
from ralph.config.loader import load_config
from ralph.workspace.scope import WorkspaceScope

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "ralph" / "policy" / "defaults" / "ralph-workflow.toml"
LOCAL_TEMPLATE_PATH = REPO_ROOT / "ralph" / "policy" / "defaults" / "ralph-workflow-local.toml"
CONFIGURATION_DOC = REPO_ROOT / "docs" / "sphinx" / "configuration.md"


def test_retention_max_age_days_default_is_seven() -> None:
    assert GeneralConfig().retention_max_age_days == 7.0


def test_retention_max_age_days_accepts_stricter_value() -> None:
    config = GeneralConfig(retention_max_age_days=1.0)
    assert config.retention_max_age_days == 1.0


@pytest.mark.parametrize("invalid", [0.0, -1.0, 3650.0 + 0.5])
def test_retention_max_age_days_rejects_impossible_values(invalid: float) -> None:
    with pytest.raises(ValidationError, match="retention_max_age_days"):
        GeneralConfig(retention_max_age_days=invalid)


def test_load_config_rejects_invalid_retention_preference(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "ralph-workflow.toml").write_text(
        "[general]\nretention_max_age_days = 0.0\n", encoding="utf-8"
    )
    scope = WorkspaceScope(tmp_path)
    with pytest.raises(SystemExit):
        load_config(workspace_scope=scope)


def test_load_config_honors_stricter_retention_preference(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "ralph-workflow.toml").write_text(
        "[general]\nretention_max_age_days = 2.0\n", encoding="utf-8"
    )
    scope = WorkspaceScope(tmp_path)
    config = load_config(workspace_scope=scope)
    assert config.general.retention_max_age_days == 2.0


def test_both_templates_document_the_retention_preference() -> None:
    text = CONFIGURATION_DOC.read_text()
    assert "retention_max_age_days" in text, (
        "the configuration page must document the retention_max_age_days key"
    )
    assert ".agent/ralph-workflow.toml" in text, (
        "the configuration page must state that the project-level override "
        "carries the same retention key"
    )


def test_run_start_sweep_receives_configured_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def _fake_sweep(
        workspace_root: Path,
        *,
        keep_run_id: str | None,
        max_age_seconds: float = 0.0,
        now: object = None,
        coordinator: object = None,
        **_kwargs: object,
    ) -> int:
        captured["max_age_seconds"] = max_age_seconds
        return 0

    monkeypatch.setattr("ralph.workspace.agent_dir_retention.sweep_agent_dir", _fake_sweep)
    monkeypatch.setattr(
        "ralph.skills.manager.SkillManager.check_skills_for_updates",
        lambda _self: False,
        raising=True,
    )

    run_module._sync_shipped_skills_on_pipeline_run(
        workspace_root=tmp_path,
        keep_run_id="run-x",
        retention_max_age_seconds=2.0 * 86400.0,
    )
    assert captured["max_age_seconds"] == 2.0 * 86400.0
