from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport
from ralph.mcp.session_plan import build_session_mcp_plan, resolve_model_identity
from ralph.mcp.upstream.config import UPSTREAM_MCP_CONFIG_ENV, load_upstream_mcp_servers
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


class TestMediaReadExplicitOptOut:
    """Explicit [media] enabled = false removes media.read for all drains."""

    @pytest.mark.parametrize(
        "drain",
        [
            "planning",
            "development",
            "development_analysis",
            "development_commit",
            "analysis",
            "review",
            "review_analysis",
            "review_commit",
            "fix",
            "commit",
        ],
    )
    def test_media_read_absent_when_disabled(
        self,
        isolated_home: Path,
        tmp_path: Path,
        drain: str,
    ) -> None:
        """When [media] enabled = false, media.read is absent for all drains."""
        del isolated_home
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "mcp.toml").write_text(
            "[media]\nenabled = false\n",
            encoding="utf-8",
        )

        plan = build_session_mcp_plan(
            transport=AgentTransport.CLAUDE,
            drain=drain,
            workspace_path=tmp_path,
            agents_policy=_DEFAULT_AGENTS_POLICY,
        )

        assert "media.read" not in plan.capabilities
