"""Regression coverage for Cursor credentials under Ralph's private HOME."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from pytest import mark, raises

from ralph.agents.invoke import (
    InvokeOptions,
    MissingCredentialsError,
    _has_cursor_file_credentials,
    invoke_agent,
)
from ralph.agents.invoke._runtime_resolvers import CursorRuntimeResolver
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV
from ralph.mcp.transport import cursor as cursor_transport

if TYPE_CHECKING:
    import pytest

    from ralph.agents.invoke._subprocess import SubprocessContext


@mark.parametrize("use_explicit_xdg", [False, True])
def test_cursor_runtime_regression_projects_xdg_auth_into_private_config_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, use_explicit_xdg: bool
) -> None:
    """Regression: Cursor auth lives at XDG_CONFIG_HOME/cursor/auth.json, not ~/.cursor."""
    operator_home = tmp_path / "operator-home"
    source_config_home = (
        tmp_path / "operator-xdg-config" if use_explicit_xdg else operator_home / ".config"
    )
    credentials = source_config_home / "cursor" / "auth.json"
    credentials.parent.mkdir(parents=True)
    credentials.write_text('{"token":"operator-token"}', encoding="utf-8")
    mutable_sibling = source_config_home / "cursor" / "state.vscdb"
    mutable_sibling.write_text("mutable operator state", encoding="utf-8")
    operator_mcp = operator_home / ".cursor" / "mcp.json"
    operator_mcp.parent.mkdir(parents=True)
    operator_mcp.write_text('{"mcpServers":{"operator":{}}}', encoding="utf-8")
    monkeypatch.setenv("HOME", str(operator_home))
    if use_explicit_xdg:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(source_config_home))
    else:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    endpoint = "http://127.0.0.1:9999/mcp"
    runtime = CursorRuntimeResolver().resolve(
        AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
        extra_env={str(MCP_ENDPOINT_ENV): endpoint},
        workspace_path=tmp_path,
    )

    try:
        assert runtime.agent_env is not None
        private_home = Path(runtime.agent_env["HOME"])
        private_config_home = Path(runtime.agent_env["XDG_CONFIG_HOME"])
        assert private_config_home != source_config_home
        assert private_config_home.parent == private_home
        for projected_auth in (
            private_home / ".cursor" / "auth.json",
            private_config_home / "cursor" / "auth.json",
        ):
            assert json.loads(projected_auth.read_text(encoding="utf-8")) == json.loads(
                credentials.read_text(encoding="utf-8")
            )
        assert not (private_config_home / "cursor" / mutable_sibling.name).exists()
        generated_mcp: dict[str, object] = json.loads(
            (private_home / ".cursor" / "mcp.json").read_text(encoding="utf-8")
        )
        mcp_servers = generated_mcp["mcpServers"]
        assert isinstance(mcp_servers, dict)
        ralph_server = mcp_servers["ralph"]
        assert isinstance(ralph_server, dict)
        ralph_url = ralph_server["url"]
        assert isinstance(ralph_url, str)
        assert ralph_url == endpoint
        assert operator_mcp.read_text(encoding="utf-8") == '{"mcpServers":{"operator":{}}}'
    finally:
        assert runtime.cleanup is not None
        runtime.cleanup()


def test_cursor_runtime_regression_defaults_to_file_store_without_api_key(
    tmp_path: Path,
) -> None:
    """S-4: Cursor runs avoid the macOS keychain unless an operator overrides the store."""
    runtime = CursorRuntimeResolver().resolve(
        AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
        extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
        workspace_path=tmp_path,
        base_env={"HOME": str(tmp_path / "operator-home")},
    )

    try:
        assert runtime.agent_env is not None
        assert runtime.agent_env["AGENT_CLI_CREDENTIAL_STORE"] == "file"
        assert "CURSOR_API_KEY" not in runtime.agent_env
    finally:
        assert runtime.cleanup is not None
        runtime.cleanup()


@mark.parametrize(
    ("extra_env", "base_env", "expected_store", "expected_api_key"),
    [
        (
            {str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
            {
                "HOME": "/operator-home",
                "AGENT_CLI_CREDENTIAL_STORE": "memory",
                "CURSOR_API_KEY": "ambient-key",
            },
            "memory",
            "ambient-key",
        ),
        (
            {
                str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp",
                "AGENT_CLI_CREDENTIAL_STORE": "default",
                "CURSOR_API_KEY": "invocation-key",
            },
            {
                "HOME": "/operator-home",
                "AGENT_CLI_CREDENTIAL_STORE": "memory",
                "CURSOR_API_KEY": "ambient-key",
            },
            "file",
            "invocation-key",
        ),
        (
            {
                str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp",
                "AGENT_CLI_CREDENTIAL_STORE": "",
                "CURSOR_API_KEY": "invocation-key",
            },
            {"HOME": "/operator-home"},
            "file",
            "invocation-key",
        ),
        (
            {
                str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp",
                "AGENT_CLI_CREDENTIAL_STORE": "keychain",
                "CURSOR_API_KEY": "invocation-key",
            },
            {"HOME": "/operator-home"},
            "file",
            "invocation-key",
        ),
    ],
)
def test_cursor_runtime_regression_preserves_credential_override_precedence(
    tmp_path: Path,
    extra_env: dict[str, str],
    base_env: dict[str, str],
    expected_store: str,
    expected_api_key: str,
) -> None:
    """S-4: invocation credentials override ambient values, which override defaults."""
    runtime = CursorRuntimeResolver().resolve(
        AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
        extra_env=extra_env,
        workspace_path=tmp_path,
        base_env=base_env,
    )

    try:
        assert runtime.agent_env is not None
        assert runtime.agent_env["AGENT_CLI_CREDENTIAL_STORE"] == expected_store
        assert runtime.agent_env["CURSOR_API_KEY"] == expected_api_key
    finally:
        assert runtime.cleanup is not None
        runtime.cleanup()


@mark.parametrize(
    ("extra_env", "credential"),
    [
        ({str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"}, "api_key"),
        ({}, "projected_auth"),
    ],
)
def test_cursor_invocation_uses_only_private_non_keychain_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra_env: dict[str, str],
    credential: str,
) -> None:
    """S-3: both Cursor invocation shapes isolate credentials before spawn."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("task", encoding="utf-8")
    operator_home = tmp_path / "operator-home"
    operator_config = operator_home / ".config"
    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(operator_config))
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    if credential == "api_key":
        extra_env["CURSOR_API_KEY"] = "test-key"
    else:
        auth_path = operator_config / "cursor" / "auth.json"
        auth_path.parent.mkdir(parents=True)
        auth_path.write_text('{"token":"non-empty-value"}', encoding="utf-8")

    captured_env: dict[str, str] = {}

    def capture_runtime(_cmd: list[str], ctx: SubprocessContext) -> object:
        captured_env.update(ctx.extra_env)
        return iter(())

    monkeypatch.setattr("ralph.agents.invoke.run_subprocess_and_read_lines", capture_runtime)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)

    list(
        invoke_agent(
            AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
            str(prompt_file),
            options=InvokeOptions(show_progress=False, workspace_path=tmp_path, extra_env=extra_env),
        )
    )

    assert Path(captured_env["HOME"]) != operator_home
    assert Path(captured_env["XDG_CONFIG_HOME"]).parent == Path(captured_env["HOME"])
    assert captured_env["AGENT_CLI_CREDENTIAL_STORE"] in {"file", "memory"}
    assert not {"SSH_CLIENT", "SSH_TTY", "SSH_CONNECTION"} & captured_env.keys()


@mark.parametrize("ambient_store", [None, "default", "keychain"])
def test_cursor_invocation_without_credentials_fails_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ambient_store: str | None
) -> None:
    """S-2: keychain-only Cursor runs never reach the subprocess."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("task", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "operator-home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "operator-config"))
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    if ambient_store is None:
        monkeypatch.delenv("AGENT_CLI_CREDENTIAL_STORE", raising=False)
    else:
        monkeypatch.setenv("AGENT_CLI_CREDENTIAL_STORE", ambient_store)
    monkeypatch.setattr(
        "ralph.agents.invoke.run_subprocess_and_read_lines",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not spawn")),
    )

    with raises(MissingCredentialsError) as excinfo:
        list(
            invoke_agent(
                AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
                str(prompt_file),
                options=InvokeOptions(show_progress=False, workspace_path=tmp_path),
            )
        )

    assert excinfo.value.env_var == "CURSOR_API_KEY"
    assert excinfo.value.stderr.startswith("CURSOR_API_KEY")
    assert "file-backed Cursor login" in excinfo.value.stderr


@mark.parametrize("auth_payload", ["{}", '{"invalid":true}', "not json"])
def test_cursor_invocation_with_unusable_auth_fails_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, auth_payload: str
) -> None:
    """S-3: empty and wrong-schema logins never launch Cursor."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("task", encoding="utf-8")
    operator_home = tmp_path / "operator-home"
    operator_config = operator_home / ".config"
    auth_path = operator_config / "cursor" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(auth_payload, encoding="utf-8")
    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(operator_config))
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setattr(
        "ralph.agents.invoke.run_subprocess_and_read_lines",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not spawn")),
    )

    with raises(MissingCredentialsError):
        list(
            invoke_agent(
                AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
                str(prompt_file),
                options=InvokeOptions(show_progress=False, workspace_path=tmp_path),
            )
        )


def test_cursor_parallel_worker_environment_cannot_restore_keychain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-4: a worker inherits hostile ambient env but its Cursor child cannot."""
    monkeypatch.setenv("HOME", str(tmp_path / "operator-home"))
    monkeypatch.setenv("AGENT_CLI_CREDENTIAL_STORE", "keychain")
    worker_extra_env = {str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"}
    worker_env = {**os.environ, **worker_extra_env}
    runtime = CursorRuntimeResolver().resolve(
        AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
        extra_env=worker_extra_env,
        workspace_path=tmp_path,
        base_env=worker_env,
    )

    try:
        assert runtime.agent_env is not None
        assert runtime.agent_env["AGENT_CLI_CREDENTIAL_STORE"] in {"file", "memory"}
        assert Path(runtime.agent_env["HOME"]) != Path(worker_env["HOME"])
        assert Path(runtime.agent_env["XDG_CONFIG_HOME"]).parent == Path(runtime.agent_env["HOME"])
    finally:
        assert runtime.cleanup is not None
        runtime.cleanup()


def test_cursor_invocation_accepts_dot_cursor_auth_json_when_xdg_auth_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-2: a standard ~/.cursor file login is sufficient for unattended Cursor."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("task", encoding="utf-8")
    operator_home = tmp_path / "operator-home"
    operator_config = operator_home / ".config"
    auth_path = operator_home / ".cursor" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text('{"token":"non-empty-value"}', encoding="utf-8")
    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(operator_config))
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    captured_env: dict[str, str] = {}
    captured_projected_auth: list[str] = []

    def capture_runtime(_cmd: list[str], ctx: SubprocessContext) -> object:
        captured_env.update(ctx.extra_env)
        captured_projected_auth.extend(
            path.read_text(encoding="utf-8")
            for path in (
                Path(ctx.extra_env["HOME"]) / ".cursor" / "auth.json",
                Path(ctx.extra_env["XDG_CONFIG_HOME"]) / "cursor" / "auth.json",
            )
        )
        return iter(())

    monkeypatch.setattr("ralph.agents.invoke.run_subprocess_and_read_lines", capture_runtime)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)

    list(
        invoke_agent(
            AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
            str(prompt_file),
            options=InvokeOptions(show_progress=False, workspace_path=tmp_path),
        )
    )

    assert captured_env["AGENT_CLI_CREDENTIAL_STORE"] == "file"
    assert [json.loads(payload) for payload in captured_projected_auth] == [
        json.loads(auth_path.read_text(encoding="utf-8"))
    ] * 2


@mark.parametrize("auth_payload", ["{}", '{"invalid":true}', "not json"])
def test_cursor_invocation_with_unusable_dot_cursor_auth_fails_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, auth_payload: str
) -> None:
    """S-2: unusable ~/.cursor auth cannot fall through to a keychain prompt."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("task", encoding="utf-8")
    operator_home = tmp_path / "operator-home"
    auth_path = operator_home / ".cursor" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(auth_payload, encoding="utf-8")
    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(operator_home / ".config"))
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setattr(
        "ralph.agents.invoke.run_subprocess_and_read_lines",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not spawn")),
    )

    with raises(MissingCredentialsError):
        list(
            invoke_agent(
                AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
                str(prompt_file),
                options=InvokeOptions(show_progress=False, workspace_path=tmp_path),
            )
        )


def test_cursor_file_credentials_helper_recognizes_private_dot_cursor(tmp_path: Path) -> None:
    """S-2: the preflight recognizes valid auth in either Cursor file location."""
    config_home = tmp_path / "config"
    home = tmp_path / "home"
    config_auth = config_home / "cursor" / "auth.json"
    dot_cursor_auth = home / ".cursor" / "auth.json"

    assert not _has_cursor_file_credentials(config_home, home)
    config_auth.parent.mkdir(parents=True)
    config_auth.write_text('{"token":"config-token"}', encoding="utf-8")
    assert _has_cursor_file_credentials(config_home, home)
    config_auth.unlink()
    dot_cursor_auth.parent.mkdir(parents=True)
    dot_cursor_auth.write_text('{"token":"home-token"}', encoding="utf-8")
    assert _has_cursor_file_credentials(config_home, home)
    dot_cursor_auth.write_text("{}", encoding="utf-8")
    assert not _has_cursor_file_credentials(config_home, home)
    dot_cursor_auth.write_text('{"unrelated":"value"}', encoding="utf-8")
    assert not _has_cursor_file_credentials(config_home, home)


def test_cursor_home_mirror_skips_entry_that_vanishes_before_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    volatile = source / "volatile.json"
    volatile.write_text("credential", encoding="utf-8")
    original_is_dir = Path.is_dir

    def remove_before_stat(path: Path) -> bool:
        if path == volatile:
            path.unlink()
        return original_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", remove_before_stat)

    cursor_transport._mirror_cursor_home(source, destination)

    assert not (destination / volatile.name).exists()


def test_cursor_home_mirror_excludes_mcp_config_and_ralph_sidecar_locks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "mcp.json").write_text("{}", encoding="utf-8")
    (source / "mcp.json.ralph.lock").write_text("lock", encoding="utf-8")
    (source / "auth.json").write_text("credential", encoding="utf-8")

    cursor_transport._mirror_cursor_home(source, destination)

    assert not (destination / "mcp.json").exists()
    assert not (destination / "mcp.json.ralph.lock").exists()
    assert (destination / "auth.json").is_symlink()


def test_cursor_runtime_uses_valid_home_auth_when_xdg_auth_is_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed XDG file cannot suppress a usable ~/.cursor login."""
    operator_home = tmp_path / "operator-home"
    source_config_home = tmp_path / "operator-config"
    xdg_auth = source_config_home / "cursor" / "auth.json"
    home_auth = operator_home / ".cursor" / "auth.json"
    xdg_auth.parent.mkdir(parents=True)
    home_auth.parent.mkdir(parents=True)
    xdg_auth.write_text("not json", encoding="utf-8")
    home_auth.write_text('{"token":"home-token"}', encoding="utf-8")
    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(source_config_home))

    runtime = CursorRuntimeResolver().resolve(
        AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
        extra_env={},
        workspace_path=tmp_path,
    )

    try:
        assert runtime.agent_env is not None
        assert all(
            json.loads(path.read_text(encoding="utf-8")) == {"token": "home-token"}
            for path in (
                Path(runtime.agent_env["HOME"]) / ".cursor" / "auth.json",
                Path(runtime.agent_env["XDG_CONFIG_HOME"]) / "cursor" / "auth.json",
            )
        )
    finally:
        assert runtime.cleanup is not None
        runtime.cleanup()


@mark.parametrize("platform", ["darwin", "linux"])
def test_cursor_runtime_extracts_ide_auth_when_file_auth_is_unusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    """Cursor IDE SQLite login supplies both private file-store locations."""
    import sqlite3

    operator_home = tmp_path / "operator-home"
    source_config_home = tmp_path / "operator-config"
    invalid_auth = source_config_home / "cursor" / "auth.json"
    invalid_auth.parent.mkdir(parents=True)
    invalid_auth.write_text("{}", encoding="utf-8")
    state_db = (
        operator_home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
        if platform == "darwin"
        else source_config_home / "Cursor/User/globalStorage/state.vscdb"
    )
    state_db.parent.mkdir(parents=True)
    with sqlite3.connect(state_db) as connection:
        connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute(
            "INSERT INTO ItemTable VALUES (?, ?)", ("cursorAuth/accessToken", "ide-token")
        )
        connection.execute(
            "INSERT INTO ItemTable VALUES (?, ?)", ("cursorAuth/refreshToken", "refresh-token")
        )
    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(source_config_home))
    monkeypatch.setattr("ralph.agents.invoke._runtime_resolvers.sys.platform", platform)

    runtime = CursorRuntimeResolver().resolve(
        AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
        extra_env={},
        workspace_path=tmp_path,
    )

    try:
        assert runtime.agent_env is not None
        assert all(
            json.loads(path.read_text(encoding="utf-8"))
            == {"accessToken": "ide-token", "refreshToken": "refresh-token"}
            for path in (
                Path(runtime.agent_env["HOME"]) / ".cursor" / "auth.json",
                Path(runtime.agent_env["XDG_CONFIG_HOME"]) / "cursor" / "auth.json",
            )
        )
    finally:
        assert runtime.cleanup is not None
        runtime.cleanup()


@mark.parametrize("platform", ["darwin", "linux"])
@mark.parametrize("credential_store", ["file", "memory"])
def test_cursor_invocation_projects_ide_credentials_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    credential_store: str,
) -> None:
    """Unattended macOS/Linux launches pass projected IDE auth without Keychain."""
    import sqlite3

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("task", encoding="utf-8")
    operator_home = tmp_path / "operator-home"
    source_config_home = tmp_path / "operator-config"
    state_db = (
        operator_home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
        if platform == "darwin"
        else source_config_home / "Cursor/User/globalStorage/state.vscdb"
    )
    state_db.parent.mkdir(parents=True)
    with sqlite3.connect(state_db) as connection:
        connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        connection.executemany(
            "INSERT INTO ItemTable VALUES (?, ?)",
            (
                ("cursorAuth/accessToken", "ide-token"),
                ("cursorAuth/refreshToken", "refresh-token"),
            ),
        )
    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(source_config_home))
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setattr("ralph.agents.invoke._runtime_resolvers.sys.platform", platform)
    monkeypatch.setattr("ralph.agents.invoke._start_workspace_monitor", lambda *_a, **_k: None)
    captured_env: dict[str, str] = {}
    projected_payloads: list[dict[str, object]] = []

    def capture_runtime(_cmd: list[str], ctx: SubprocessContext) -> object:
        captured_env.update(ctx.extra_env)
        projected_payloads.extend(
            json.loads(path.read_text(encoding="utf-8"))
            for path in (
                Path(ctx.extra_env["HOME"]) / ".cursor" / "auth.json",
                Path(ctx.extra_env["XDG_CONFIG_HOME"]) / "cursor" / "auth.json",
            )
        )
        return iter(())

    monkeypatch.setattr("ralph.agents.invoke.run_subprocess_and_read_lines", capture_runtime)

    list(
        invoke_agent(
            AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
            str(prompt_file),
            options=InvokeOptions(
                show_progress=False,
                workspace_path=tmp_path,
                extra_env={"AGENT_CLI_CREDENTIAL_STORE": credential_store},
            ),
        )
    )

    assert captured_env["AGENT_CLI_CREDENTIAL_STORE"] == credential_store
    assert Path(captured_env["HOME"]) != operator_home
    assert projected_payloads == [
        {"accessToken": "ide-token", "refreshToken": "refresh-token"}
    ] * 2


def test_cursor_runtime_replaces_mirrored_auth_without_touching_operator_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SQLite fallback replaces a mirrored auth symlink only in the private home."""
    import sqlite3

    operator_home = tmp_path / "operator-home"
    source_config_home = tmp_path / "operator-config"
    operator_auth = operator_home / ".cursor" / "auth.json"
    operator_auth.parent.mkdir(parents=True)
    operator_auth.write_text("{}", encoding="utf-8")
    state_db = source_config_home / "Cursor/User/globalStorage/state.vscdb"
    state_db.parent.mkdir(parents=True)
    with sqlite3.connect(state_db) as connection:
        connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute(
            "INSERT INTO ItemTable VALUES (?, ?)", ("cursorAuth/refreshToken", "refresh-token")
        )
    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(source_config_home))
    monkeypatch.setattr("ralph.agents.invoke._runtime_resolvers.sys.platform", "linux")

    runtime = CursorRuntimeResolver().resolve(
        AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
        extra_env={},
        workspace_path=tmp_path,
    )

    try:
        assert runtime.agent_env is not None
        private_auth = Path(runtime.agent_env["HOME"]) / ".cursor" / "auth.json"
        assert not private_auth.is_symlink()
        assert json.loads(private_auth.read_text(encoding="utf-8")) == {
            "refreshToken": "refresh-token"
        }
        assert operator_auth.read_text(encoding="utf-8") == "{}"
    finally:
        assert runtime.cleanup is not None
        runtime.cleanup()


@mark.parametrize("credential_source", ["file", "ide"])
def test_cursor_runtime_regression_strips_ssh_markers_for_each_credential_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, credential_source: str
) -> None:
    """S-1: Cursor never receives SSH markers that trigger Keychain mode."""
    import sqlite3

    operator_home = tmp_path / "operator-home"
    source_config_home = tmp_path / "operator-config"
    if credential_source == "file":
        auth_path = source_config_home / "cursor" / "auth.json"
        auth_path.parent.mkdir(parents=True)
        auth_path.write_text('{"token":"file-token"}', encoding="utf-8")
    else:
        state_db = source_config_home / "Cursor/User/globalStorage/state.vscdb"
        state_db.parent.mkdir(parents=True)
        with sqlite3.connect(state_db) as connection:
            connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
            connection.execute(
                "INSERT INTO ItemTable VALUES (?, ?)", ("cursorAuth/accessToken", "ide-token")
            )
        monkeypatch.setattr("ralph.agents.invoke._runtime_resolvers.sys.platform", "linux")

    runtime = CursorRuntimeResolver().resolve(
        AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
        extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
        workspace_path=tmp_path,
        base_env={
            "HOME": str(operator_home),
            "XDG_CONFIG_HOME": str(source_config_home),
            "SSH_CLIENT": "192.0.2.1 12345 22",
            "SSH_TTY": "/dev/ttys001",
            "SSH_CONNECTION": "192.0.2.1 12345 198.51.100.1 22",
        },
    )

    try:
        assert runtime.agent_env is not None
        assert not {"SSH_CLIENT", "SSH_TTY", "SSH_CONNECTION"} & runtime.agent_env.keys()
        assert _has_cursor_file_credentials(
            Path(runtime.agent_env["XDG_CONFIG_HOME"]), Path(runtime.agent_env["HOME"])
        )
    finally:
        assert runtime.cleanup is not None
        runtime.cleanup()

