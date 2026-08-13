"""Black-box tests for the user-facing retention preference (S-7 / DA-107).

``[general] retention_max_age_days`` lets an operator express a stricter
(or looser) Ralph-managed retention bound without knowing the internal
data organization. Impossible values must fail visibly at config load.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ralph.config.general_config import GeneralConfig
from ralph.config.loader import load_config
from ralph.workspace.scope import WorkspaceScope

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "ralph" / "policy" / "defaults" / "ralph-workflow.toml"
LOCAL_TEMPLATE_PATH = REPO_ROOT / "ralph" / "policy" / "defaults" / "ralph-workflow-local.toml"


def test_retention_max_age_days_default_is_seven() -> None:
    """The conservative default matches the previous hard-coded 7-day bound."""
    assert GeneralConfig().retention_max_age_days == 7.0


def test_retention_max_age_days_accepts_stricter_value() -> None:
    """A stricter preference (fewer days) validates and is honored."""
    config = GeneralConfig(retention_max_age_days=1.0)
    assert config.retention_max_age_days == 1.0


@pytest.mark.parametrize("invalid", [0.0, -1.0, 3650.0 + 0.5])
def test_retention_max_age_days_rejects_impossible_values(invalid: float) -> None:
    """Zero, negative, and absurd values are rejected with a field-name error."""
    with pytest.raises(ValidationError, match="retention_max_age_days"):
        GeneralConfig(retention_max_age_days=invalid)


def test_load_config_rejects_invalid_retention_preference(tmp_path: Path) -> None:
    """End-to-end: an impossible value in the TOML fails visibly (SystemExit)."""
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "ralph-workflow.toml").write_text(
        "[general]\nretention_max_age_days = 0.0\n", encoding="utf-8"
    )
    scope = WorkspaceScope(tmp_path)
    with pytest.raises(SystemExit):
        load_config(workspace_scope=scope)


def test_load_config_honors_stricter_retention_preference(tmp_path: Path) -> None:
    """End-to-end: a valid stricter value flows into the merged config."""
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "ralph-workflow.toml").write_text(
        "[general]\nretention_max_age_days = 2.0\n", encoding="utf-8"
    )
    scope = WorkspaceScope(tmp_path)
    config = load_config(workspace_scope=scope)
    assert config.general.retention_max_age_days == 2.0


def test_both_templates_document_the_retention_preference() -> None:
    """Both bundled templates carry the commented-out retention key."""
    for path in (TEMPLATE_PATH, LOCAL_TEMPLATE_PATH):
        text = path.read_text()
        assert "retention_max_age_days" in text, (
            f"{path.name} must document the retention_max_age_days key"
        )


def test_run_start_sweep_receives_configured_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run-start bookkeeping path forwards the configured age to the sweep."""
    import ralph.cli.commands.run as run_module

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

    monkeypatch.setattr(
        "ralph.workspace.agent_dir_retention.sweep_agent_dir", _fake_sweep
    )
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
