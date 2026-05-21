"""Tests for ralph/config/prompt_helper_config.py — PromptHelperConfig."""

from __future__ import annotations

import pytest

from ralph.config.prompt_helper_config import PromptHelperConfig


class TestPromptHelperConfig:
    """Tests for PromptHelperConfig."""

    def test_default_agent_is_opencode(self) -> None:
        """Default agent value is 'opencode'."""
        config = PromptHelperConfig()
        assert config.agent == "opencode"

    def test_rejects_extra_fields(self) -> None:
        """Config rejects extra fields."""
        with pytest.raises(ValueError):
            PromptHelperConfig(agent="custom", unknown_field="invalid")
