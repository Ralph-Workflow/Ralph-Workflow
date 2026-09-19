"""Regression coverage for credential preflight detection (S-3)."""

from __future__ import annotations

from pathlib import Path
from subprocess import TimeoutExpired

import pytest

import ralph.agents.invoke as invoke_module
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


def test_claude_wrapper_requires_anthropic_key() -> None:
    """S-3: a non-official Claude wrapper still requires the Anthropic credential."""
    config = AgentConfig(cmd="claude-wrapper -p", transport=AgentTransport.CLAUDE)

    with pytest.raises(MissingCredentialsError) as excinfo:
        _fail_for_missing_credentials(config, InvokeOptions(), env_getter=lambda _name: None)

    assert excinfo.value.env_var == "ANTHROPIC_API_KEY"


def test_official_claude_cli_is_exempt_from_anthropic_key_preflight() -> None:
    """The official Claude Code CLI manages its own credentials.

    Both interactive ``claude`` and headless ``claude -p`` store credentials
    in ``~/.claude/.credentials.json`` after login, so they must not be
    blocked by ``ANTHROPIC_API_KEY`` in Ralph's environment.
    """
    for cmd in ("claude", "claude -p"):
        for transport in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE):
            config = AgentConfig(cmd=cmd, transport=transport)
            _fail_for_missing_credentials(config, InvokeOptions(), env_getter=lambda _name: None)


def test_cursor_missing_env_and_operator_credentials_raises() -> None:
    """S-4: Cursor requires login material when no API key is injected."""
    config = AgentConfig(cmd="agent", transport=AgentTransport.CURSOR)
    home = Path("/nonexistent-cursor-home")

    with pytest.raises(MissingCredentialsError) as excinfo:
        _fail_for_missing_credentials(
            config,
            InvokeOptions(),
            env_getter={"HOME": str(home)}.get,
            keychain_login_probe=lambda: False,
        )

    assert excinfo.value.agent_name == "agent"
    assert excinfo.value.env_var == "CURSOR_API_KEY"
    assert "agent login" in excinfo.value.stderr
    assert "CURSOR_API_KEY" in excinfo.value.stderr


def test_cursor_mcp_json_alone_is_not_credential_material(tmp_path: Path) -> None:
    """S-4: Cursor MCP configuration does not count as login material."""
    cursor_home = tmp_path / ".cursor"
    cursor_home.mkdir()
    (cursor_home / "mcp.json").write_text("{}", encoding="utf-8")
    config = AgentConfig(cmd="agent", transport=AgentTransport.CURSOR)

    with pytest.raises(MissingCredentialsError):
        _fail_for_missing_credentials(
            config,
            InvokeOptions(),
            env_getter={"HOME": str(tmp_path)}.get,
            keychain_login_probe=lambda: False,
        )


def test_cursor_keychain_login_allows_launch_on_macos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A macOS ``agent login`` keychain entry is valid Cursor credential material."""
    monkeypatch.setattr(invoke_module.sys, "platform", "darwin")
    config = AgentConfig(cmd="agent", transport=AgentTransport.CURSOR)

    _fail_for_missing_credentials(
        config,
        InvokeOptions(),
        env_getter={"HOME": str(tmp_path)}.get,
        keychain_login_probe=lambda: True,
    )


def test_cursor_absent_keychain_login_still_fails_on_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A logged-out macOS Cursor installation remains fail-fast."""
    monkeypatch.setattr(invoke_module.sys, "platform", "darwin")
    config = AgentConfig(cmd="agent", transport=AgentTransport.CURSOR)

    with pytest.raises(MissingCredentialsError):
        _fail_for_missing_credentials(
            config,
            InvokeOptions(),
            env_getter={"HOME": str(tmp_path)}.get,
            keychain_login_probe=lambda: False,
        )


@pytest.mark.parametrize(
    "failure",
    [FileNotFoundError(), PermissionError(), TimeoutExpired(["security"], 5)],
)
def test_cursor_unavailable_keychain_probe_still_fails_on_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: OSError | TimeoutExpired
) -> None:
    """Probe failures are unsuccessful credential evidence, not preflight errors."""
    monkeypatch.setattr(invoke_module.sys, "platform", "darwin")
    config = AgentConfig(cmd="agent", transport=AgentTransport.CURSOR)

    def unavailable_probe() -> bool:
        raise failure

    with pytest.raises(MissingCredentialsError):
        _fail_for_missing_credentials(
            config,
            InvokeOptions(),
            env_getter={"HOME": str(tmp_path)}.get,
            keychain_login_probe=unavailable_probe,
        )


def test_cursor_keychain_probe_does_not_run_off_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-macOS credential behavior remains unchanged."""
    monkeypatch.setattr(invoke_module.sys, "platform", "linux")
    config = AgentConfig(cmd="agent", transport=AgentTransport.CURSOR)

    with pytest.raises(MissingCredentialsError):
        _fail_for_missing_credentials(
            config,
            InvokeOptions(),
            env_getter={"HOME": str(tmp_path)}.get,
            keychain_login_probe=lambda: (_ for _ in ()).throw(AssertionError("unexpected probe")),
        )


def test_cursor_operator_material_allows_launch(tmp_path: Path) -> None:
    """S-4: any non-MCP file under ~/.cursor represents logged-in material."""
    cursor_home = tmp_path / ".cursor"
    cursor_home.mkdir()
    (cursor_home / "auth.json").write_text("{}", encoding="utf-8")
    config = AgentConfig(cmd="agent", transport=AgentTransport.CURSOR)

    _fail_for_missing_credentials(
        config,
        InvokeOptions(),
        env_getter={"HOME": str(tmp_path)}.get,
    )


def test_cursor_env_key_and_extra_env_override_allow_launch(tmp_path: Path) -> None:
    """S-4: injected env values allow Cursor with invocation precedence."""
    config = AgentConfig(cmd="agent", transport=AgentTransport.CURSOR)

    _fail_for_missing_credentials(
        config,
        InvokeOptions(extra_env={"CURSOR_API_KEY": "per-invocation"}),
        env_getter={"CURSOR_API_KEY": "ambient", "HOME": str(tmp_path)}.get,
    )
    _fail_for_missing_credentials(
        config,
        InvokeOptions(),
        env_getter={"CURSOR_API_KEY": "ambient", "HOME": str(tmp_path)}.get,
    )


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
