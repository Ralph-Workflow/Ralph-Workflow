from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

import ralph.api.opencode as opencode_module
import ralph.mcp.session_plan as session_plan_module
from ralph.config.enums import AgentTransport
from ralph.mcp.multimodal.capabilities import (
    MultimodalModelIdentity,
)
from ralph.mcp.session_plan import SessionModelOpts, build_session_mcp_plan, resolve_model_identity
from ralph.mcp.upstream.config import UPSTREAM_MCP_CONFIG_ENV, load_upstream_mcp_servers
from ralph.mcp.upstream.tool_catalog_cache import cache_tool_catalog, clear_tool_catalog
from ralph.mcp.upstream.upstream_tool import UpstreamTool
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return home


@pytest.fixture(autouse=True)
def _clear_tool_catalog_cache(tmp_path: Path) -> Generator[None, None, None]:
    clear_tool_catalog(tmp_path)
    yield
    clear_tool_catalog(tmp_path)


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


def test_resolve_model_identity_claude_interactive() -> None:
    identity = resolve_model_identity(AgentTransport.CLAUDE_INTERACTIVE, "--model sonnet")

    assert identity.provider == "claude"
    assert identity.model_id == "--model sonnet"
    assert identity.transport == AgentTransport.CLAUDE_INTERACTIVE.value


def test_resolve_model_identity_agy() -> None:
    identity = resolve_model_identity(AgentTransport.AGY, "--model gemini-2.5-pro")

    assert identity.provider == "gemini"
    assert identity.model_id == "--model gemini-2.5-pro"
    assert identity.transport == AgentTransport.AGY.value


def test_session_mcp_plan_derives_web_and_upstream_capabilities_from_live_config(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    (isolated_home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "npx", "args": ["-y", "github-mcp"]}}}),
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


def test_build_session_mcp_plan_filters_server_env_to_cached_upstreams(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    (isolated_home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "npx", "args": ["-y", "github-mcp"]}}}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "mcp.toml").write_text(
        """
[mcp_servers.docs]
transport = "http"
url = "http://docs.example/mcp"
""".strip(),
        encoding="utf-8",
    )
    cache_tool_catalog(
        tmp_path,
        {"github": [UpstreamTool(name="search", description="Search", input_schema={})]},
    )

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="planning",
        workspace_path=tmp_path,
        agents_policy=_default_agents_policy(tmp_path),
    )

    assert plan.server_env is not None
    upstreams = load_upstream_mcp_servers(plan.server_env[UPSTREAM_MCP_CONFIG_ENV])
    assert {server.name for server in upstreams} == {"github"}


def test_build_session_mcp_plan_claude_interactive_includes_upstreams(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    (isolated_home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "npx", "args": ["-y", "github-mcp"]}}}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "mcp.toml").write_text(
        """
[mcp_servers.docs]
transport = "http"
url = "http://docs.example/mcp"
""".strip(),
        encoding="utf-8",
    )

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        drain="planning",
        workspace_path=tmp_path,
        agents_policy=_default_agents_policy(tmp_path),
    )

    assert plan.server_env is not None
    upstreams = load_upstream_mcp_servers(plan.server_env[UPSTREAM_MCP_CONFIG_ENV])
    assert {server.name for server in upstreams} == {"github", "docs"}


def test_build_session_mcp_plan_agy_includes_upstreams(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    (isolated_home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    (isolated_home / ".gemini" / "antigravity-cli" / "mcp_config.json").write_text(
        json.dumps(
            {"mcpServers": {"agy-upstream": {"serverUrl": "http://agy-upstream.example/mcp"}}}
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
""".strip(),
        encoding="utf-8",
    )

    plan = build_session_mcp_plan(
        transport=AgentTransport.AGY,
        drain="development",
        workspace_path=tmp_path,
        agents_policy=_default_agents_policy(tmp_path),
    )

    assert "upstream.tool_use" in plan.capabilities
    assert plan.server_env is not None

    upstreams = load_upstream_mcp_servers(plan.server_env[UPSTREAM_MCP_CONFIG_ENV])
    assert {"agy-upstream", "docs"}.issubset({server.name for server in upstreams})


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


class TestModelFlagResolutionInBuildSessionMcpPlan:
    """build_session_mcp_plan owns model identity resolution via model_flag."""

    def test_model_flag_resolves_claude_identity(self, isolated_home: Path, tmp_path: Path) -> None:
        del isolated_home
        plan = build_session_mcp_plan(
            transport=AgentTransport.CLAUDE,
            drain="development",
            workspace_path=tmp_path,
            agents_policy=_DEFAULT_AGENTS_POLICY,
            model_opts=SessionModelOpts(model_flag="claude-opus-4-7"),
        )

        assert plan.model_identity.provider == "claude"
        assert plan.model_identity.model_id == "claude-opus-4-7"
        assert plan.model_identity.transport == "claude"

    def test_model_flag_resolves_codex_identity(self, isolated_home: Path, tmp_path: Path) -> None:
        del isolated_home
        plan = build_session_mcp_plan(
            transport=AgentTransport.CODEX,
            drain="development",
            workspace_path=tmp_path,
            agents_policy=_DEFAULT_AGENTS_POLICY,
            model_opts=SessionModelOpts(model_flag="gpt-4o"),
        )

        assert plan.model_identity.provider == "openai"
        assert plan.model_identity.model_id == "gpt-4o"

    def test_codex_config_override_does_not_pollute_model_identity(
        self, isolated_home: Path, tmp_path: Path
    ) -> None:
        del isolated_home
        plan = build_session_mcp_plan(
            transport=AgentTransport.CODEX,
            drain="development",
            workspace_path=tmp_path,
            agents_policy=_DEFAULT_AGENTS_POLICY,
            model_opts=SessionModelOpts(
                model_flag="--model gpt-5.3-codex -c 'model_reasoning_effort = \"high\"'"
            ),
        )

        assert plan.model_identity.provider == "openai"
        assert plan.model_identity.model_id == "gpt-5.3-codex"

    def test_model_identity_takes_precedence_over_model_flag(
        self, isolated_home: Path, tmp_path: Path
    ) -> None:
        """Explicit model_identity overrides model_flag."""
        del isolated_home
        explicit_identity = MultimodalModelIdentity(
            provider="custom-provider", model_id="custom-model", transport="claude"
        )
        plan = build_session_mcp_plan(
            transport=AgentTransport.CLAUDE,
            drain="development",
            workspace_path=tmp_path,
            agents_policy=_DEFAULT_AGENTS_POLICY,
            model_opts=SessionModelOpts(
                model_identity=explicit_identity,
                model_flag="claude-opus-4-7",
            ),
        )

        assert plan.model_identity.provider == "custom-provider"
        assert plan.model_identity.model_id == "custom-model"

    def test_no_model_flag_yields_unknown_identity(
        self, isolated_home: Path, tmp_path: Path
    ) -> None:
        del isolated_home
        plan = build_session_mcp_plan(
            transport=AgentTransport.CLAUDE,
            drain="development",
            workspace_path=tmp_path,
            agents_policy=_DEFAULT_AGENTS_POLICY,
        )

        assert plan.model_identity.provider == "unknown"

    def test_opencode_model_flag_catalog_failure_returns_unknown_provider(
        self, isolated_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When OpenCode catalog lookup fails, identity has 'unknown' provider."""
        del isolated_home

        def _fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError("catalog unreachable")

        monkeypatch.setattr(opencode_module, "fetch_catalog", _fail)

        plan = build_session_mcp_plan(
            transport=AgentTransport.OPENCODE,
            drain="development",
            workspace_path=tmp_path,
            agents_policy=_DEFAULT_AGENTS_POLICY,
            model_opts=SessionModelOpts(model_flag="some-opencode-model"),
        )

        assert plan.model_identity.provider == "unknown"
        assert plan.model_identity.model_id == "some-opencode-model"
        assert plan.model_identity.transport == "opencode"

    def test_opencode_model_flag_catalog_success_uses_catalog_provider(
        self, isolated_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When OpenCode catalog lookup succeeds, provider comes from catalog."""
        del isolated_home
        monkeypatch.setattr(
            session_plan_module,
            "resolve_model_identity",
            lambda t, m: MultimodalModelIdentity(
                provider="anthropic",
                model_id=m,
                transport=str(t.value if t else ""),
            ),
        )

        plan = build_session_mcp_plan(
            transport=AgentTransport.OPENCODE,
            drain="development",
            workspace_path=tmp_path,
            agents_policy=_DEFAULT_AGENTS_POLICY,
            model_opts=SessionModelOpts(model_flag="anthropic/claude-3-5-sonnet"),
        )

        assert plan.model_identity.provider == "anthropic"
        assert plan.model_identity.model_id == "anthropic/claude-3-5-sonnet"
