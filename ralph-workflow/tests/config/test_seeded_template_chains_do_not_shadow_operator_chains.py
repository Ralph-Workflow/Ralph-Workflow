from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config.loader import load_config
from ralph.policy.loader import default_dir, load_policy

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_OPERATOR_CHAINS = """
[agent_chains]
planning = ["opencode/zai/glm-5.2"]
development = ["opencode/minimax/MiniMax-M3", "cursor/auto"]
analysis = ["codex/gpt-5.6-terra"]
commit = ["codex/gpt-5.6-terra"]

[agent_drains]
planning = "planning"
planning_analysis = "analysis"
development = "development"
development_analysis = "analysis"
development_commit = "commit"
analysis = "analysis"
commit = "commit"
"""

_UNCONFIGURED_DRAINS = (
    "rebase_conflict_resolution",
    "policy_remediation",
    "policy_remediation_analysis",
)


def _write_global(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str) -> None:
    config_home = tmp_path / "xdg"
    config_home.mkdir()
    (config_home / "ralph-workflow.toml").write_text(text, encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))


def _seed_project_local_template_copy(tmp_path: Path) -> Path:
    local_path = tmp_path / "project" / ".agent" / "ralph-workflow.toml"
    local_path.parent.mkdir(parents=True)
    local_path.write_text(
        (default_dir() / "ralph-workflow.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return local_path


def _claude_agents_by_drain(tmp_path: Path, local_path: Path) -> dict[str, list[str]]:
    config = load_config(config_path=local_path)
    agents = load_policy(tmp_path / "no-policy-dir", config=config).agents
    return {
        drain: [
            agent
            for agent in agents.agent_chains[binding.chain].agents
            if agent.split("/", 1)[0] == "claude"
        ]
        for drain, binding in agents.agent_drains.items()
    }


def test_a_project_local_template_copy_does_not_put_claude_on_unconfigured_drains(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_global(monkeypatch, tmp_path, _OPERATOR_CHAINS)
    local_path = _seed_project_local_template_copy(tmp_path)

    claude_by_drain = _claude_agents_by_drain(tmp_path, local_path)

    assert {drain: claude_by_drain[drain] for drain in _UNCONFIGURED_DRAINS} == {
        drain: [] for drain in _UNCONFIGURED_DRAINS
    }


def test_a_project_local_template_copy_keeps_the_operators_chains_on_every_drain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_global(monkeypatch, tmp_path, _OPERATOR_CHAINS)
    local_path = _seed_project_local_template_copy(tmp_path)

    claude_by_drain = _claude_agents_by_drain(tmp_path, local_path)

    assert [drain for drain, claude in claude_by_drain.items() if claude] == []


def test_a_bare_install_keeps_the_bundled_claude_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_global(
        monkeypatch,
        tmp_path,
        (default_dir() / "ralph-workflow.toml").read_text(encoding="utf-8"),
    )
    local_path = _seed_project_local_template_copy(tmp_path)

    claude_by_drain = _claude_agents_by_drain(tmp_path, local_path)

    assert claude_by_drain["rebase_conflict_resolution"] == ["claude/sonnet"]


def test_a_project_local_chain_the_operator_edited_still_overrides_the_global_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_global(monkeypatch, tmp_path, _OPERATOR_CHAINS)
    local_path = tmp_path / "project" / ".agent" / "ralph-workflow.toml"
    local_path.parent.mkdir(parents=True)
    local_path.write_text('[agent_chains]\ndevelopment = ["claude/opus"]\n', encoding="utf-8")

    config = load_config(config_path=local_path)

    assert config.agent_chains["development"].agents == ["claude/opus"]
