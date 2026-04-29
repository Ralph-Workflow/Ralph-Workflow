from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.mcp.upstream.config import UPSTREAM_MCP_CONFIG_ENV, load_upstream_mcp_servers

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return home


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
    )

    assert "web.search" in plan.capabilities
    assert "web.visit" in plan.capabilities
    assert "upstream.tool_use" in plan.capabilities
    assert plan.server_env is not None

    upstreams = load_upstream_mcp_servers(plan.server_env[UPSTREAM_MCP_CONFIG_ENV])
    assert {server.name for server in upstreams} == {"github", "docs"}


def test_session_mcp_plan_omits_web_search_for_commit_even_when_enabled(
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
    )

    assert "web.search" not in plan.capabilities
    assert "web.visit" in plan.capabilities


def test_session_mcp_plan_grants_read_diff_and_exec_for_development_analysis(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    del isolated_home

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="development_analysis",
        workspace_path=tmp_path,
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
        )

        assert "workspace.edit" not in plan.capabilities
        assert "workspace.delete" not in plan.capabilities


class TestCommitDrainIsStrictlyReadOnly:
    """Commit drains must be strictly read-only; git.write is reserved to the orchestrator."""

    @pytest.mark.parametrize(
        "drain",
        ["development_commit", "review_commit", "commit"],
    )
    def test_commit_drain_does_not_grant_write_capabilities(
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
        )

        assert "git.write" not in plan.capabilities
        assert "workspace.write_ephemeral" not in plan.capabilities
        assert "workspace.write_tracked" not in plan.capabilities
        assert "process.exec_bounded" not in plan.capabilities

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
        )

        assert "workspace.read" in plan.capabilities
        assert "git.status_read" in plan.capabilities
        assert "git.diff_read" in plan.capabilities
        assert "artifact.submit" in plan.capabilities
        assert "workspace.metadata_read" in plan.capabilities
        assert "run.report_progress" in plan.capabilities
