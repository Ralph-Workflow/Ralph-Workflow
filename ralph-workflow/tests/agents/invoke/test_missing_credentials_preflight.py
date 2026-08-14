"""Regression coverage for credential preflight detection (S-3)."""

from __future__ import annotations

import pytest

from ralph.agents.invoke import (
    InvokeOptions,
    MissingCredentialsError,
    _fail_for_missing_credentials,
)
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport


def test_missing_openai_key_raises_fast_fallover_error() -> None:
    """S-3: OpenCode OpenAI models fail before an empty subprocess launch."""
    config = AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE)

    with pytest.raises(MissingCredentialsError) as excinfo:
        _fail_for_missing_credentials(
            config,
            InvokeOptions(model_flag="-m openai/gpt-5"),
            env_getter=lambda _name: None,
        )

    assert excinfo.value.agent_name == "opencode"
    assert excinfo.value.env_var == "OPENAI_API_KEY"
    assert excinfo.value.skip_same_agent_retries is True


def test_present_openai_key_allows_opencode_invocation() -> None:
    """S-3: injected credentials permit the configured provider."""
    config = AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE)

    _fail_for_missing_credentials(
        config,
        InvokeOptions(model_flag="-m openai/gpt-5"),
        env_getter={"OPENAI_API_KEY": "configured"}.get,
    )


def test_local_opencode_model_is_exempt_from_provider_key_preflight() -> None:
    """S-3: local model ids are not assumed to use a hosted provider credential."""
    config = AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE)

    _fail_for_missing_credentials(
        config,
        InvokeOptions(model_flag="-m ollama/qwen3"),
        env_getter=lambda _name: None,
    )


def test_claude_transport_requires_anthropic_key() -> None:
    """S-3: Claude transport uses the Anthropic credential regardless of model flag."""
    config = AgentConfig(cmd="claude -p", transport=AgentTransport.CLAUDE)

    with pytest.raises(MissingCredentialsError) as excinfo:
        _fail_for_missing_credentials(config, InvokeOptions(), env_getter=lambda _name: None)

    assert excinfo.value.env_var == "ANTHROPIC_API_KEY"


def test_ccs_alias_is_exempt_from_anthropic_key_preflight() -> None:
    """A ``ccs/<alias>`` agent resolves its own credential per-profile.

    Before this exemption, every real ``ccs/<alias>`` invocation (e.g.
    ``ccs/glm``, a GLM-backed profile that injects
    ``ANTHROPIC_AUTH_TOKEN`` / ``ANTHROPIC_BASE_URL`` itself via the
    ``ccs`` wrapper) was rejected here with
    ``MissingCredentialsError: ANTHROPIC_API_KEY not set`` before the
    ``ccs`` binary was ever spawned -- even though the CCS profile
    needed no such variable in Ralph's own environment.
    """
    config = AgentConfig(cmd="ccs glm", transport=AgentTransport.CLAUDE)

    _fail_for_missing_credentials(config, InvokeOptions(), env_getter=lambda _name: None)
