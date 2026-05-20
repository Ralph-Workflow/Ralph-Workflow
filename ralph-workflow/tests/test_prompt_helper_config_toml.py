"""Tests for TOML round-trip with PromptHelperConfig."""

from __future__ import annotations

import tomllib

from ralph.config.models import UnifiedConfig


class TestPromptHelperConfigTomlRoundTrip:
    """Tests for TOML round-trip with PromptHelperConfig."""

    def test_toml_override_preserves_custom_agent_name(self) -> None:
        """TOML with custom agent name parses correctly."""
        toml_content = b"""
[prompt_helper]
agent = "custom-agent"
"""
        data = tomllib.loads(toml_content.decode("utf-8"))
        config = UnifiedConfig.model_validate(data)
        assert config.prompt_helper.agent == "custom-agent"

    def test_toml_without_prompt_helper_section_uses_defaults(self) -> None:
        """TOML without [prompt_helper] section uses default values."""
        toml_content = b"""
[general]
"""
        data = tomllib.loads(toml_content.decode("utf-8"))
        config = UnifiedConfig.model_validate(data)
        assert config.prompt_helper.agent == "prompt-helper-agent"
