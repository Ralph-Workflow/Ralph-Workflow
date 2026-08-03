from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return home


_DEFAULT_AGENTS_POLICY = AgentsPolicy(
    agent_chains={
        "planning": AgentChainConfig(agents=["claude"], max_retries=2, retry_delay_ms=1000),
        "development": AgentChainConfig(
            agents=["claude", "opencode"], max_retries=3, retry_delay_ms=1000
        ),
        "development_analysis": AgentChainConfig(
            agents=["claude"], max_retries=2, retry_delay_ms=500
        ),
        "development_commit": AgentChainConfig(
            agents=["claude"], max_retries=2, retry_delay_ms=500
        ),
        "review": AgentChainConfig(agents=["claude"], max_retries=3, retry_delay_ms=1000),
        "review_analysis": AgentChainConfig(agents=["claude"], max_retries=2, retry_delay_ms=500),
        "analysis": AgentChainConfig(agents=["claude"], max_retries=2, retry_delay_ms=500),
        "fix": AgentChainConfig(agents=["claude"], max_retries=3, retry_delay_ms=1000),
        "review_commit": AgentChainConfig(agents=["claude"], max_retries=2, retry_delay_ms=500),
        "commit": AgentChainConfig(agents=["claude"], max_retries=2, retry_delay_ms=500),
    },
    agent_drains={
        "planning": AgentDrainConfig(chain="planning", drain_class="planning"),
        "development": AgentDrainConfig(chain="development", drain_class="development"),
        "development_analysis": AgentDrainConfig(
            chain="development_analysis", drain_class="analysis"
        ),
        "development_commit": AgentDrainConfig(chain="development_commit", drain_class="commit"),
        "review": AgentDrainConfig(chain="review", drain_class="review"),
        "review_analysis": AgentDrainConfig(chain="review_analysis", drain_class="analysis"),
        "analysis": AgentDrainConfig(chain="analysis", drain_class="analysis"),
        "fix": AgentDrainConfig(chain="fix", drain_class="fix"),
        "review_commit": AgentDrainConfig(chain="review_commit", drain_class="commit"),
        "commit": AgentDrainConfig(chain="commit", drain_class="commit"),
    },
)


def _default_agents_policy(_workspace_path: Path) -> AgentsPolicy:
    return _DEFAULT_AGENTS_POLICY


class TestWorkspaceEditAndDeleteGrantedToDevAndFixDrains:
    """workspace.edit and workspace.delete are granted to development and fix drains only."""

    @pytest.mark.parametrize(
        "drain",
        ["development", "fix"],
    )
    def test_edit_and_delete_granted_to_dev_and_fix(
        self,
        isolated_home: Path,
        tmp_path: Path,
        drain: str,
    ) -> None:
        del isolated_home

        plan = build_session_mcp_plan(
            transport=AgentTransport.CLAUDE,
            drain=drain,
            workspace_path=tmp_path,
            agents_policy=_default_agents_policy(tmp_path),
        )

        assert "workspace.edit" in plan.capabilities
        assert "workspace.delete" in plan.capabilities

    @pytest.mark.parametrize(
        "drain",
        [
            "planning",
            "development_analysis",
            "development_commit",
            "analysis",
            "review",
            "review_analysis",
            "review_commit",
            "commit",
        ],
    )
    def test_edit_and_delete_not_granted_to_read_only_drains(
        self,
        isolated_home: Path,
        tmp_path: Path,
        drain: str,
    ) -> None:
        del isolated_home

        plan = build_session_mcp_plan(
            transport=AgentTransport.CLAUDE,
            drain=drain,
            workspace_path=tmp_path,
            agents_policy=_default_agents_policy(tmp_path),
        )

        assert "workspace.edit" not in plan.capabilities
        assert "workspace.delete" not in plan.capabilities
