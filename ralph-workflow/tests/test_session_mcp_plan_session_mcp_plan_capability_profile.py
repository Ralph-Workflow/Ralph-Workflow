from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport
from ralph.mcp.multimodal.artifacts import (
    MODALITY_AUDIO,
    MODALITY_IMAGE,
    MODALITY_PDF,
    SUPPORTED_MODALITIES,
)
from ralph.mcp.multimodal.capabilities import (
    DeliveryMode,
    ResolvedCapabilityProfile,
)
from ralph.mcp.session_plan import SessionModelOpts, build_session_mcp_plan
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


class TestSessionMcpPlanCapabilityProfile:
    """SessionMcpPlan includes a resolved capability profile keyed by provider/model identity."""

    def test_plan_includes_capability_profile_for_claude_transport(
        self, isolated_home: Path, tmp_path: Path
    ) -> None:

        del isolated_home
        plan = build_session_mcp_plan(
            transport=AgentTransport.CLAUDE,
            drain="development",
            workspace_path=tmp_path,
            agents_policy=_DEFAULT_AGENTS_POLICY,
            model_opts=SessionModelOpts(model_flag="claude-opus-4-7"),
        )

        assert plan.capability_profile is not None
        assert isinstance(plan.capability_profile, ResolvedCapabilityProfile)
        image_delivery = plan.capability_profile.verdict_for(MODALITY_IMAGE).delivery
        assert image_delivery == DeliveryMode.INLINE_IMAGE
        audio_delivery = plan.capability_profile.verdict_for(MODALITY_AUDIO).delivery
        assert audio_delivery == DeliveryMode.UNSUPPORTED

    def test_plan_includes_capability_profile_for_openai_codex_transport(
        self, isolated_home: Path, tmp_path: Path
    ) -> None:
        """Codex resolves to the OpenAI provider but must NOT inline images.

        The Codex CLI cannot round-trip an inline MCP image block back
        into its own Responses API request -- it re-serialises the part
        as ``output_text``, which the API rejects with a 400 that kills
        the whole turn. Image delivery therefore degrades to
        ``RESOURCE_REFERENCE_REPLAY`` for this transport even though the
        resolved provider (``openai``) is image-capable. See
        ``tests/test_codex_inline_image_roundtrip.py``.
        """
        del isolated_home
        plan = build_session_mcp_plan(
            transport=AgentTransport.CODEX,
            drain="development",
            workspace_path=tmp_path,
            agents_policy=_DEFAULT_AGENTS_POLICY,
            model_opts=SessionModelOpts(model_flag="gpt-4o"),
        )

        assert plan.capability_profile is not None
        assert isinstance(plan.capability_profile, ResolvedCapabilityProfile)
        assert plan.capability_profile.identity.provider == "openai"
        assert plan.capability_profile.identity.transport == "codex"
        image_delivery = plan.capability_profile.verdict_for(MODALITY_IMAGE).delivery
        assert image_delivery == DeliveryMode.RESOURCE_REFERENCE_REPLAY
        pdf_delivery = plan.capability_profile.verdict_for(MODALITY_PDF).delivery
        assert pdf_delivery == DeliveryMode.UNSUPPORTED

    def test_plan_capability_profile_for_unknown_provider_inlines_images(
        self, isolated_home: Path, tmp_path: Path
    ) -> None:

        del isolated_home
        plan = build_session_mcp_plan(
            transport=AgentTransport.CLAUDE,
            drain="development",
            workspace_path=tmp_path,
            agents_policy=_DEFAULT_AGENTS_POLICY,
        )

        assert plan.capability_profile is not None
        assert isinstance(plan.capability_profile, ResolvedCapabilityProfile)
        image_verdict = plan.capability_profile.verdict_for(MODALITY_IMAGE)
        assert image_verdict.delivery == DeliveryMode.INLINE_IMAGE
        for modality in SUPPORTED_MODALITIES - {MODALITY_IMAGE}:
            verdict = plan.capability_profile.verdict_for(modality)
            assert verdict.delivery == DeliveryMode.RESOURCE_REFERENCE_REPLAY, (
                f"unknown provider modality={modality!r}: expected RESOURCE_REFERENCE_REPLAY, "
                f"got {verdict.delivery!r}"
            )

    def test_plan_capability_profile_identity_matches_model_identity(
        self, isolated_home: Path, tmp_path: Path
    ) -> None:
        del isolated_home
        plan = build_session_mcp_plan(
            transport=AgentTransport.CLAUDE,
            drain="development",
            workspace_path=tmp_path,
            agents_policy=_DEFAULT_AGENTS_POLICY,
            model_opts=SessionModelOpts(model_flag="claude-opus-4-7"),
        )

        assert plan.capability_profile is not None
        assert plan.capability_profile.identity.provider == plan.model_identity.provider
        assert plan.capability_profile.identity.model_id == plan.model_identity.model_id
