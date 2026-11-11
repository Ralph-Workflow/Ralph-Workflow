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
    config = AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE)

    _fail_for_missing_credentials(
        config,
        InvokeOptions(model_flag="-m openai/gpt-5"),
        env_getter={"OPENAI_API_KEY": "configured"}.get,
    )


def test_local_opencode_model_is_exempt_from_provider_key_preflight() -> None:
    config = AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE)

    _fail_for_missing_credentials(
        config,
        InvokeOptions(model_flag="-m ollama/qwen3"),
        env_getter=lambda _name: None,
    )


def test_claude_wrapper_requires_anthropic_key() -> None:
    config = AgentConfig(cmd="claude-wrapper -p", transport=AgentTransport.CLAUDE)

    with pytest.raises(MissingCredentialsError) as excinfo:
        _fail_for_missing_credentials(config, InvokeOptions(), env_getter=lambda _name: None)

    assert excinfo.value.env_var == "ANTHROPIC_API_KEY"


def test_official_claude_cli_is_exempt_from_anthropic_key_preflight() -> None:
    for cmd in ("claude", "claude -p"):
        for transport in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE):
            config = AgentConfig(cmd=cmd, transport=transport)
            _fail_for_missing_credentials(config, InvokeOptions(), env_getter=lambda _name: None)


def test_cursor_regression_uses_cli_default_credentials_without_preflight() -> None:
    config = AgentConfig(cmd="agent", transport=AgentTransport.CURSOR)

    _fail_for_missing_credentials(config, InvokeOptions(), env_getter=lambda _name: None)


def test_cursor_explicit_api_key_remains_an_optional_override() -> None:
    config = AgentConfig(cmd="agent", transport=AgentTransport.CURSOR)

    _fail_for_missing_credentials(
        config,
        InvokeOptions(extra_env={"CURSOR_API_KEY": "per-invocation"}),
        env_getter=lambda _name: None,
    )
    _fail_for_missing_credentials(
        config,
        InvokeOptions(),
        env_getter={"CURSOR_API_KEY": "ambient"}.get,
    )


def test_ccs_alias_is_exempt_from_anthropic_key_preflight() -> None:
    config = AgentConfig(cmd="ccs glm", transport=AgentTransport.CLAUDE)

    _fail_for_missing_credentials(config, InvokeOptions(), env_getter=lambda _name: None)
