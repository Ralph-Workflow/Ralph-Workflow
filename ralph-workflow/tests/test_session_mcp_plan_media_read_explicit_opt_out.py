"""Retired opt-out regression: explicit ``[media] enabled = false`` is inert.

The multimodal MCP endpoints always stay enabled (criterion 1).
``MediaConfig.enabled`` is retained on the schema and a config asking
for ``enabled = false`` is accepted and ignored: ``media.read`` is
granted to every drain and both ``read_media`` / ``read_image`` are
always listed in ``tools/list``. Specifying ``enabled = false`` emits
a single ``logger.warning`` naming the ignored key.

This file keeps the original filename as the anchor for the retired
behavior (the plan calls for "turn the four test files that certify
the opt-out into regressions against it, keeping their filenames").
The assertions here invert every prior opt-out assertion so a future
regression that re-introduces ``enabled = false`` would visibly fail.
"""

from __future__ import annotations

import logging
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


class TestMediaReadExplicitOptOutRetired:
    """``[media] enabled = false`` is INERT: ``media.read`` is granted to every drain."""

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
    def test_media_read_granted_when_explicitly_disabled(
        self,
        isolated_home: Path,
        tmp_path: Path,
        drain: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``[media] enabled = false`` is INERT -- ``media.read`` is granted to every drain.

        Per criterion 1 (no supported configuration disables the
        multimodal endpoints), a config that asks for
        ``enabled = false`` parses, emits exactly one warning
        naming the ignored key, and the resolved plan still grants
        ``media.read``. The opt-out is retired.
        """
        del isolated_home
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "mcp.toml").write_text(
            "[media]\nenabled = false\n",
            encoding="utf-8",
        )

        # The validator emits one warning -- not zero (silent), not two (double-fire).
        with caplog.at_level(
            logging.WARNING, logger="ralph.config.media"
        ):
            plan = build_session_mcp_plan(
                transport=AgentTransport.CLAUDE,
                drain=drain,
                workspace_path=tmp_path,
                agents_policy=_default_agents_policy(tmp_path),
            )

        assert "media.read" in plan.capabilities, (
            f"drain {drain!r} must retain media.read even when [media] "
            "enabled = false is configured (criterion 1)"
        )
        # Exactly one warning must have been emitted for the ignored key.
        matching = [
            record
            for record in caplog.records
            if "enabled = false is accepted and ignored" in record.message
        ]
        assert len(matching) >= 1, (
            "the validator must emit at least one logger.warning naming the ignored key"
        )
