"""Tests for ralph/config/prompt_helper_config.py — PromptHelperConfig."""

from __future__ import annotations

import pytest

from ralph.config.prompt_helper_config import PromptHelperConfig


class TestPromptHelperConfig:
    """Tests for PromptHelperConfig."""

    def test_default_agent_is_none(self) -> None:
        """Default agent value is None (triggers fallback to first configured agent)."""
        config = PromptHelperConfig()
        assert config.agent is None

    def test_rejects_extra_fields(self) -> None:
        """Config rejects extra fields."""
        with pytest.raises(ValueError):
            PromptHelperConfig(agent="custom", unknown_field="invalid")
