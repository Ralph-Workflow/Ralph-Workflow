"""Tests for UnifiedConfig integration with PromptHelperConfig."""

from __future__ import annotations

from ralph.config.models import UnifiedConfig
from ralph.config.prompt_helper_config import PromptHelperConfig


class TestUnifiedConfigPromptHelper:
    """Tests for UnifiedConfig integration with PromptHelperConfig."""

    def test_unified_config_has_prompt_helper_attribute(self) -> None:
        """UnifiedConfig has a prompt_helper attribute with PromptHelperConfig default."""
        config = UnifiedConfig()
        assert hasattr(config, "prompt_helper")
        assert isinstance(config.prompt_helper, PromptHelperConfig)

    def test_unified_config_prompt_helper_agent_defaults_to_prompt_helper_agent(
        self,
    ) -> None:
        """UnifiedConfig.prompt_helper.agent defaults to 'prompt-helper-agent'."""
        config = UnifiedConfig()
        assert config.prompt_helper.agent == "prompt-helper-agent"
