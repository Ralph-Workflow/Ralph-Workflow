from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport
from ralph.mcp.session_plan import build_session_mcp_plan
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
        "review_analysis": AgentChainConfig(
            agents=["claude"], max_retries=2, retry_delay_ms=500
        ),
        "analysis": AgentChainConfig(agents=["claude"], max_retries=2, retry_delay_ms=500),
        "fix": AgentChainConfig(agents=["claude"], max_retries=3, retry_delay_ms=1000),
        "review_commit": AgentChainConfig(
            agents=["claude"], max_retries=2, retry_delay_ms=500
        ),
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


def test_session_mcp_plan_derives_web_and_upstream_capabilities_from_live_config(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    (isolated_home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {"command": "npx", "args": ["-y", "github-mcp"]}
                }
            }
        ),
        encoding="utf-8",
    )
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "mcp.toml").write_text(
        """
[mcp_servers.docs]
transport = "http"
url = "http://docs.example/mcp"

[web_search]
enabled = true

[web_visit]
enabled = true
""".strip(),
        encoding="utf-8",
    )

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="planning",
        workspace_path=tmp_path,
        agents_policy=_default_agents_policy(tmp_path),
    )

    assert "web.search" in plan.capabilities
    assert "web.visit" in plan.capabilities
    assert "upstream.tool_use" in plan.capabilities
    assert plan.server_env is not None

    upstreams = load_upstream_mcp_servers(plan.server_env[UPSTREAM_MCP_CONFIG_ENV])
    assert {server.name for server in upstreams} == {"github", "docs"}


def test_session_mcp_plan_omits_web_search_and_web_visit_for_commit_even_when_enabled(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    del isolated_home
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "mcp.toml").write_text(
        """
[web_search]
enabled = true

[web_visit]
enabled = true
""".strip(),
        encoding="utf-8",
    )

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="commit",
        workspace_path=tmp_path,
        agents_policy=_default_agents_policy(tmp_path),
    )

    assert "web.search" not in plan.capabilities
    assert "web.visit" not in plan.capabilities


def test_session_mcp_plan_grants_read_diff_and_exec_for_development_analysis(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    del isolated_home

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="development_analysis",
        workspace_path=tmp_path,
        agents_policy=_default_agents_policy(tmp_path),
    )

    assert "workspace.read" in plan.capabilities
    assert "git.status_read" in plan.capabilities
    assert "git.diff_read" in plan.capabilities
    assert "artifact.submit" in plan.capabilities
    assert "process.exec_bounded" in plan.capabilities
    assert "run.report_progress" in plan.capabilities
    assert "workspace.write_tracked" not in plan.capabilities


def test_session_mcp_plan_grants_read_diff_and_exec_for_review_analysis(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    del isolated_home

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="review_analysis",
        workspace_path=tmp_path,
        agents_policy=_default_agents_policy(tmp_path),
    )

    assert "workspace.read" in plan.capabilities
    assert "git.status_read" in plan.capabilities
    assert "git.diff_read" in plan.capabilities
    assert "artifact.submit" in plan.capabilities
    assert "process.exec_bounded" in plan.capabilities
    assert "run.report_progress" in plan.capabilities
    assert "workspace.write_tracked" not in plan.capabilities


class TestWorkspaceMetadataReadGrantedToAllDrains:
    """workspace.metadata_read is granted to ALL drains."""

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
    def test_metadata_read_granted_to_all_drains(
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

        assert "workspace.metadata_read" in plan.capabilities


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


class TestCommitDrainIsStrictlyReadOnly:
    """Commit drains must be strictly read-only; git.write is reserved to the orchestrator."""

    @pytest.fixture
    def commit_drain_workspace(self, tmp_path: Path, isolated_home: Path) -> Path:
        del isolated_home
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "mcp.toml").write_text(
            """
[web_search]
enabled = true

[web_visit]
enabled = true
""".strip(),
            encoding="utf-8",
        )
        return tmp_path

    @pytest.mark.parametrize(
        "drain",
        ["development_commit", "review_commit", "commit"],
    )
    def test_commit_drain_does_not_grant_write_capabilities(
        self,
        commit_drain_workspace: Path,
        drain: str,
    ) -> None:
        plan = build_session_mcp_plan(
            transport=AgentTransport.CLAUDE,
            drain=drain,
            workspace_path=commit_drain_workspace,
            agents_policy=_default_agents_policy(commit_drain_workspace),
        )

        assert "git.write" not in plan.capabilities
        assert "workspace.write_ephemeral" not in plan.capabilities
        assert "workspace.write_tracked" not in plan.capabilities
        assert "process.exec_bounded" not in plan.capabilities
        assert "upstream.tool_use" not in plan.capabilities
        assert "web.visit" not in plan.capabilities
        assert "web.search" not in plan.capabilities

    @pytest.mark.parametrize(
        "drain",
        ["development_commit", "review_commit", "commit"],
    )
    def test_commit_drain_keeps_read_capabilities(
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

        assert "workspace.read" in plan.capabilities
        assert "git.status_read" in plan.capabilities
        assert "git.diff_read" in plan.capabilities
        assert "artifact.submit" in plan.capabilities
        assert "workspace.metadata_read" in plan.capabilities
        assert "run.report_progress" in plan.capabilities


def test_capabilities_use_policy_declared_drain_capability_class(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    """When a drain declares capability_class in agents.toml, build_session_mcp_plan
    resolves MCP capabilities using that class rather than the drain_class.
    """
    del isolated_home
    from ralph.policy.models import (  # noqa: PLC0415
        AgentChainConfig,
        AgentDrainConfig,
        AgentsPolicy,
    )

    agents_policy = AgentsPolicy(
        agent_chains={"my_chain": AgentChainConfig(agents=["claude"])},
        agent_drains={
            "my_planning_drain": AgentDrainConfig(
                chain="my_chain",
                drain_class="planning",
                capability_class="development",
            )
        },
    )

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="my_planning_drain",
        workspace_path=tmp_path,
        agents_policy=agents_policy,
    )

    # capability_class='development' overrides drain_class='planning', so we get
    # development write capabilities rather than planning's read-only surface.
    assert "workspace.write_tracked" in plan.capabilities
    assert "workspace.edit" in plan.capabilities
    assert "workspace.read" in plan.capabilities


def test_capability_class_commit_suppresses_web_search_when_enabled(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    """capability_class='commit' suppresses web.search/web.visit even when the mcp.toml
    enables them, proving that is_commit uses the resolved capability_class not drain_class.
    """
    del isolated_home
    from ralph.policy.models import (  # noqa: PLC0415
        AgentChainConfig,
        AgentDrainConfig,
        AgentsPolicy,
    )

    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "mcp.toml").write_text(
        "[web_search]\nenabled = true\n[web_visit]\nenabled = true\n",
        encoding="utf-8",
    )

    agents_policy = AgentsPolicy(
        agent_chains={"my_chain": AgentChainConfig(agents=["claude"])},
        agent_drains={
            "my_planning_drain": AgentDrainConfig(
                chain="my_chain",
                drain_class="planning",
                capability_class="commit",
            )
        },
    )

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="my_planning_drain",
        workspace_path=tmp_path,
        agents_policy=agents_policy,
    )

    # capability_class='commit' suppresses web.search and web.visit even though
    # drain_class='planning' wouldn't normally suppress them.
    assert "web.search" not in plan.capabilities
    assert "web.visit" not in plan.capabilities


class TestMediaReadGrantedToAllDrainsByDefault:
    """media.read is granted to ALL drains when media.enabled defaults to true.

    This includes commit-class drains (commit, development_commit, review_commit).
    Web search/visit remain restricted on commit drains per existing behavior.
    """

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
    def test_media_read_granted_to_all_drains_under_default_config(
        self,
        isolated_home: Path,
        tmp_path: Path,
        drain: str,
    ) -> None:
        """Under default config (no [media] section), media.read is present for all drains."""
        del isolated_home

        plan = build_session_mcp_plan(
            transport=AgentTransport.CLAUDE,
            drain=drain,
            workspace_path=tmp_path,
            agents_policy=_DEFAULT_AGENTS_POLICY,
        )

        assert "media.read" in plan.capabilities


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
