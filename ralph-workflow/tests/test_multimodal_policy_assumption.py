from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ralph.config.enums import AgentTransport
from ralph.config.mcp_models import MediaConfig
from ralph.mcp.session_plan import (
    SessionMcpPlan,
    build_session_mcp_plan,
    default_prompt_capability_identifiers,
)
from ralph.pipeline.plumbing.smoke_multimodal import (
    SMOKE_FIXTURE_RELNAME,
    build_smoke_fixture_png,
    expected_fixture_sha256,
    multimodal_prompt_requirements,
    smoke_media_config_toml,
)
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy

pytestmark = [pytest.mark.timeout_seconds(10)]


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


def test_media_config_default_enabled_is_true() -> None:
    assert MediaConfig().enabled is True


def test_media_config_explicit_false_is_coerced_to_true() -> None:
    assert MediaConfig(enabled=False).enabled is True


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
def test_default_prompt_capability_grants_media_read(isolated_home: Path, drain: str) -> None:
    del isolated_home
    from ralph.mcp.protocol.capability_mapping import SessionDrain

    identifiers = default_prompt_capability_identifiers(SessionDrain(drain))
    assert "media.read" in identifiers


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
def test_build_session_mcp_plan_grants_media_read_under_disabled_config(
    isolated_home: Path,
    tmp_path: Path,
    drain: str,
) -> None:
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
        agents_policy=_default_agents_policy(tmp_path),
    )
    assert isinstance(plan, SessionMcpPlan)
    assert "media.read" in plan.capabilities


def test_media_read_capability_present_in_every_drain() -> None:
    from ralph.mcp.protocol.capability_mapping import SessionDrain

    for drain_name in (
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
    ):
        identifiers = default_prompt_capability_identifiers(SessionDrain(drain_name))
        assert "media.read" in identifiers, drain_name


def test_shipped_agent_policy_carries_multimodal_assumption_clause() -> None:
    shipped = (
        Path(__file__).resolve().parents[2] / "docs" / "ralph-workflow-policy" / "agent-policy.md"
    )
    text = shipped.read_text(encoding="utf-8")
    assert "Ralph's multimodal MCP endpoints work" in text, (
        "shipped agent-policy.md must state the multimodal-assumption clause"
    )


def test_shipped_claude_md_documents_multimodal_flag() -> None:
    cli = Path(__file__).resolve().parents[1] / "docs" / "sphinx" / "cli.md"
    text = cli.read_text(encoding="utf-8")
    for cmd in (
        "smoke-interactive-claude --multimodal",
        "smoke-headless-claude --multimodal",
        "smoke-interactive-agy --multimodal",
        "smoke-interactive-nanocoder --multimodal",
        "smoke-interactive-cursor --multimodal",
        "smoke-interactive-opencode --multimodal",
    ):
        assert cmd in text, f"missing {cmd!r} in cli.md"


def test_disabled_config_logged_warning_naming_the_ignored_key(
    isolated_home: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    del isolated_home
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "mcp.toml").write_text(
        "[media]\nenabled = false\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="ralph.config.media"):
        build_session_mcp_plan(
            transport=AgentTransport.CLAUDE,
            drain="development",
            workspace_path=tmp_path,
            agents_policy=_default_agents_policy(tmp_path),
        )

    matching = [
        record
        for record in caplog.records
        if "enabled = false is accepted and ignored" in record.message
    ]
    assert len(matching) >= 1


def test_multimodal_prompt_requirements_carries_every_required_token() -> None:
    prompt = multimodal_prompt_requirements(SMOKE_FIXTURE_RELNAME)
    assert SMOKE_FIXTURE_RELNAME in prompt
    assert "MEDIA_RECEIPT" in prompt
    assert "DIMENSIONS" in prompt
    assert "MEDIA_SHA256" in prompt


def test_fixture_and_mcp_toml_fragments_are_stable() -> None:
    width, height = 40, 24
    sha = expected_fixture_sha256(width, height)
    assert sha == hashlib_sha256_bytes(build_smoke_fixture_png(width, height))
    assert "[media]" in smoke_media_config_toml()
    assert "max_inline_bytes" in smoke_media_config_toml()


def hashlib_sha256_bytes(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()
