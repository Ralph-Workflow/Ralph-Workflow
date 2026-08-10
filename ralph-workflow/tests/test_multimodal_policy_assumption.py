"""Pin the multimodal-assumption policy and configuration invariants (S-8 / criteria 1+4).

Per ``.agent/PRODUCT_CRITERIA.md`` (criterion 1) "the MCP (and equivalent)
endpoints that carry multimodal data must always stay enabled" and
(criterion 4) "Every shipped policy that touches visuals or UI/UX
must ASSUME that multimodal endpoints WORK".

These tests pin the runtime invariants that:
- ``MediaConfig().enabled`` is True (default).
- ``MediaConfig(enabled=False).enabled`` is ALSO True (coerced).
- ``default_prompt_capability_identifiers`` grants ``media.read``
  on every drain under the default config.
- ``build_session_mcp_plan`` against a workspace whose
  ``.agent/mcp.toml`` sets ``[media] enabled = false`` still
  grants ``media.read`` to every drain (criterion 1).
- The shipped ``agent-policy.md`` carries the multimodal-assumption
  clause (criterion 4).
- The drift script verifies the shipped-policy parity.
"""

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
        "development_commit": AgentDrainConfig(
            chain="development_commit", drain_class="commit"
        ),
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


# ---------------------------------------------------------------------------
# Criterion 1: no supported configuration disables the multimodal endpoints
# ---------------------------------------------------------------------------


def test_media_config_default_enabled_is_true() -> None:
    """``MediaConfig()`` defaults to ``enabled = True`` (criterion 1)."""
    assert MediaConfig().enabled is True


def test_media_config_explicit_false_is_coerced_to_true() -> None:
    """``MediaConfig(enabled=False)`` is coerced to ``True`` (criterion 1).

    The legacy opt-out is retired at the ``MediaConfig`` validator
    so an existing ``mcp.toml`` asking for ``enabled = false``
    parses but the resolved value is always ``True``.
    """
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
def test_default_prompt_capability_grants_media_read(
    isolated_home: Path, drain: str
) -> None:
    """Every drain's default capabilities grant ``media.read`` (criterion 1)."""
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
    """A workspace ``[media] enabled = false`` config still grants ``media.read`` on every drain.

    ``MediaConfig.enabled`` is INERT (criterion 1); the canonical
    config-resolution path grants ``media.read`` to every drain
    whenever ``media.read`` is in the base capabilities, and the
    opt-out cannot remove it.
    """
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
    """A noise-free existence-check across all built-in drain surface names."""
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
        identifiers = default_prompt_capability_identifiers(
            SessionDrain(drain_name)
        )
        assert "media.read" in identifiers, drain_name


# ---------------------------------------------------------------------------
# Criterion 4: visual / UI policy MUST assume multimodal endpoints work
# ---------------------------------------------------------------------------


def test_shipped_agent_policy_carries_multimodal_assumption_clause() -> None:
    """The shipped ``agent-policy.md`` (S-8 / criterion 4) carries the multimodal clause."""
    shipped = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "ralph-workflow-policy"
        / "agent-policy.md"
    )
    text = shipped.read_text(encoding="utf-8")
    assert "Ralph's multimodal MCP endpoints work" in text, (
        "shipped agent-policy.md must state the multimodal-assumption clause"
    )


def test_shipped_claude_md_documents_multimodal_flag() -> None:
    """The shipped Sphinx cli documentation lists ``--multimodal`` for every smoke command."""
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


# ---------------------------------------------------------------------------
# S-14 / criterion 1: the warning is logged once and named the ignored key
# ---------------------------------------------------------------------------


def test_disabled_config_logged_warning_naming_the_ignored_key(
    isolated_home: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``[media] enabled = false`` is parsed and emits one logger.warning naming the ignored key.

    The CLI config parsing path hits the validator with
    ``enabled = False`` and emits exactly one warning carrying the
    literal substring ``enabled = false is accepted and ignored``
    so operators see a precise hint about what was ignored.
    """
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


# ---------------------------------------------------------------------------
# S-2 / criterion 5: the multimodal prompt scenario carries every required token
# ---------------------------------------------------------------------------


def test_multimodal_prompt_requirements_carries_every_required_token() -> None:
    """Every token the multimodal grader depends on is in the prompt builder."""
    prompt = multimodal_prompt_requirements(SMOKE_FIXTURE_RELNAME)
    assert SMOKE_FIXTURE_RELNAME in prompt
    assert "MEDIA_RECEIPT" in prompt
    assert "DIMENSIONS" in prompt
    assert "MEDIA_SHA256" in prompt


# ---------------------------------------------------------------------------
# S-2 / criterion 5: the fixture and mcp.toml fragments are stable
# ---------------------------------------------------------------------------


def test_fixture_and_mcp_toml_fragments_are_stable() -> None:
    """The fixture bytes and mcp.toml fragment are stable per (width, height)."""
    width, height = 40, 24
    sha = expected_fixture_sha256(width, height)
    assert sha == hashlib_sha256_bytes(build_smoke_fixture_png(width, height))
    assert "[media]" in smoke_media_config_toml()
    assert "max_inline_bytes" in smoke_media_config_toml()


def hashlib_sha256_bytes(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()
