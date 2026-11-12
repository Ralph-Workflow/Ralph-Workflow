from __future__ import annotations

import functools
import os
import shlex
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents import invoke as invoke_module
from ralph.agents.registry import AgentRegistry
from ralph.cli.commands.smoke_binary_override import (
    apply_smoke_binary_override,
    apply_smoke_binary_overrides_to_config,
    smoke_binary_override_env_var,
)
from ralph.config.enums import AgentTransport
from ralph.config.loader import load_config
from ralph.display.context import make_display_context
from ralph.pipeline.factory import DefaultPipelineFactory
from ralph.pipeline.plumbing.smoke_plumbing import (
    SmokeRunResult,
    resolve_smoke_harness_spec,
    run_smoke_plumbing,
)
from ralph.workspace.scope import WorkspaceScope

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(180),
]

if TYPE_CHECKING:
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.pipeline.factory import PipelineDeps


_TRANSPORTS: tuple[tuple[str, str, str, str], ...] = (
    ("claude", "smoke-interactive-claude", "claude/haiku", "cmd_override"),
    ("claude-headless", "smoke-headless-claude", "claude-headless/haiku", "cmd_override"),
    ("agy", "smoke-interactive-agy", "agy/gemini-3.6-flash-low", "agy_env"),
    ("nanocoder", "smoke-interactive-nanocoder", "nanocoder", "cmd_override"),
    ("cursor", "smoke-interactive-cursor", "cursor/auto", "cursor_env"),
    ("opencode", "smoke-interactive-opencode", "opencode/minimax/MiniMax-M3", "opencode_env"),
    ("codex", "smoke-interactive-codex", "codex/gpt-5-flash", "cmd_override"),
    ("pi", "smoke-interactive-pi", "pi", "cmd_override"),
    ("kimi", "smoke-interactive-kimi", "kimi/kimi-code/kimi-for-coding", "kimi_env"),
)

_TRANSPORT_IDS: tuple[str, ...] = tuple(t[0] for t in _TRANSPORTS)

_IGNORE_RESPONSE_TRANSPORTS: tuple[str, ...] = ("agy", "claude", "cursor")

_INLINE_IMAGE_RESTRICTED_TRANSPORTS: tuple[str, ...] = ("codex",)

_WIRE_POSITIVE_TRANSPORT_IDS: tuple[str, ...] = tuple(
    t for t in _TRANSPORT_IDS if t not in _INLINE_IMAGE_RESTRICTED_TRANSPORTS
)


def test_smoke_transport_table_covers_every_non_generic_transport() -> None:
    assert set(_TRANSPORT_IDS) == {
        "claude",
        "claude-headless",
        "agy",
        "nanocoder",
        "cursor",
        "opencode",
        "codex",
        "pi",
        "kimi",
    }


def _resolve_transport_entry(transport: str) -> tuple[str, str, str, str]:
    for entry in _TRANSPORTS:
        if entry[0] == transport:
            return entry
    raise AssertionError(f"unknown transport {transport!r}")


def _stub_script_path() -> Path:
    from pathlib import Path

    return Path(__file__).resolve().parent / "_support" / "mock_multimodal_agent.py"


def _stub_is_executable() -> bool:
    import stat

    path = _stub_script_path()
    if not path.exists():
        return False
    mode = path.stat().st_mode
    return bool(mode & stat.S_IXUSR or mode & stat.S_IXGRP or mode & stat.S_IXOTH)


def _apply_redirect(
    *,
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    redirect_method: str,
    stub_path: Path,
    agent_name: str,
    agent_config: AgentConfig,
    config: UnifiedConfig,
) -> tuple[AgentConfig, UnifiedConfig]:
    if redirect_method.endswith("_env"):
        env_var = smoke_binary_override_env_var(AgentTransport(redirect_method[: -len("_env")]))
        assert env_var is not None, f"transport {transport!r}: no binary override entry"
        monkeypatch.setenv(env_var, str(stub_path))
        agent_config = apply_smoke_binary_override(agent_config)
        config = apply_smoke_binary_overrides_to_config(config)
    elif redirect_method == "cmd_override":
        quoted = shlex.quote(str(stub_path))
        new_cmd = f"{quoted} {agent_config.cmd or ''}".strip()
        agent_config = agent_config.model_copy(update={"cmd": new_cmd})
    else:
        raise AssertionError(
            f"transport {transport!r}: unknown redirect method {redirect_method!r}"
        )
    overridden_agents = dict(config.agents)
    overridden_agents[agent_name] = agent_config
    config = config.model_copy(update={"agents": overridden_agents})
    return agent_config, config


@functools.cache
def _base_smoke_config() -> UnifiedConfig:
    dummy_scope = WorkspaceScope(Path(tempfile.gettempdir()))
    cfg = load_config(None, {}, workspace_scope=dummy_scope)
    return cfg.model_copy(
        update={
            "general": cfg.general.model_copy(
                update={
                    "agent_idle_drain_window_seconds": 0.02,
                    "agent_parent_exit_grace_seconds": 0.05,
                    "agent_descendant_wait_timeout_seconds": 0.05,
                    "agent_descendant_wait_poll_seconds": 0.01,
                    "agent_process_exit_wait_seconds": 0.05,
                }
            )
        }
    )


@functools.cache
def _base_agent_config(agent_name: str) -> AgentConfig:
    agent_config = AgentRegistry.from_config(_base_smoke_config()).get(agent_name)
    if agent_config is None:
        raise AssertionError(f"agent {agent_name!r} is not in the registry")
    return agent_config


@functools.cache
def _pipeline_runtime() -> tuple[DisplayContext, PipelineDeps]:
    display_context = make_display_context()
    return display_context, DefaultPipelineFactory().build(_base_smoke_config(), display_context)


def _end_to_end_test_for_harness(
    workspace: Path,
    transport: str,
    *,
    positive: bool,
    monkeypatch: pytest.MonkeyPatch,
    cursor_credential: str = "api_key",
    skip_media: bool = False,
) -> SmokeRunResult:
    transport_prefix, _cli_cmd, agent_name, redirect_method = _resolve_transport_entry(transport)
    stub_path = _stub_script_path()
    if not stub_path.is_file():
        raise AssertionError(
            f"multimodal smoke stub missing at {stub_path}; "
            "the S-9 / S-12 harness-level proof requires the deterministic "
            "stub to be present"
        )
    if not os.access(stub_path, os.X_OK):
        raise AssertionError(
            f"multimodal smoke stub at {stub_path} is not executable; "
            "run `chmod +x tests/_support/mock_multimodal_agent.py` "
            "before invoking the S-9 / S-12 harness-level proof"
        )

    broker_secret = "multimodal-broker-secret-for-e2e-test"
    monkeypatch.setenv("RALPH_BROKER_SECRET", broker_secret)
    monkeypatch.setenv("MOCK_MULTIMODAL_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("MOCK_MULTIMODAL_TRANSPORT", transport_prefix)
    if transport == "cursor":
        if cursor_credential == "api_key":
            monkeypatch.setenv("CURSOR_API_KEY", "multimodal-smoke-test-credential")
        elif cursor_credential == "login":
            monkeypatch.delenv("CURSOR_API_KEY", raising=False)
            cursor_home = workspace / ".home"
            cursor_state_path = cursor_home / ".cursor" / "state.vscdb"
            cursor_state_path.parent.mkdir(parents=True)
            cursor_state_path.write_text("state", encoding="utf-8")
            cursor_config_home = workspace / ".xdg-config"
            cursor_auth_path = cursor_config_home / "cursor" / "auth.json"
            cursor_auth_path.parent.mkdir(parents=True)
            cursor_auth_path.write_text('{"token":"credential"}', encoding="utf-8")
            monkeypatch.setenv("HOME", str(cursor_home))
            monkeypatch.setenv("XDG_CONFIG_HOME", str(cursor_config_home))
        elif cursor_credential == "missing":
            monkeypatch.delenv("CURSOR_API_KEY", raising=False)
            monkeypatch.setenv("HOME", str(workspace))
        else:
            raise AssertionError(f"unknown cursor credential mode {cursor_credential!r}")
    if skip_media:
        monkeypatch.delenv("MOCK_MULTIMODAL_IGNORE_RESPONSE", raising=False)
        monkeypatch.setenv("MOCK_MULTIMODAL_SKIP_MEDIA", "1")
    elif positive:
        monkeypatch.delenv("MOCK_MULTIMODAL_IGNORE_RESPONSE", raising=False)
        monkeypatch.delenv("MOCK_MULTIMODAL_SKIP_MEDIA", raising=False)
    else:
        monkeypatch.setenv("MOCK_MULTIMODAL_IGNORE_RESPONSE", "1")
        monkeypatch.delenv("MOCK_MULTIMODAL_SKIP_MEDIA", raising=False)

    config = _base_smoke_config()

    agent_config = _base_agent_config(agent_name)

    agent_config, config = _apply_redirect(
        monkeypatch=monkeypatch,
        transport=transport,
        redirect_method=redirect_method,
        stub_path=stub_path,
        agent_name=agent_name,
        agent_config=agent_config,
        config=config,
    )

    display_context, deps = _pipeline_runtime()

    spec = resolve_smoke_harness_spec(agent_name)
    prompt_file = workspace / spec.relative_dir / "PROMPT.md"
    output_file = workspace / spec.output_file
    monkeypatch.setenv("MOCK_MULTIMODAL_OUTPUT_FILE", str(output_file))
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(
        "# Multimodal smoke stub prompt\n"
        "Read smoke-fixture.png via the multimodal MCP endpoint, replay the "
        "server-minted handle, write receipts, and complete.\n",
        encoding="utf-8",
    )

    return run_smoke_plumbing(
        config=config,
        workspace_root=workspace,
        agent_name=agent_name,
        prompt_file=prompt_file,
        output_file=output_file,
        display_context=display_context,
        pipeline_deps=deps,
        multimodal=True,
    )


@pytest.mark.parametrize(
    "transport",
    _WIRE_POSITIVE_TRANSPORT_IDS,
)
def test_positive_multimodal_run_grades_wire(
    transport: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor_factories: list[object] = []
    original_start_workspace_monitor = invoke_module._start_workspace_monitor

    def record_start_workspace_monitor(*args: object, **kwargs: object) -> object:
        monitor_factories.append(kwargs["factory"])
        return original_start_workspace_monitor(*args, **kwargs)

    monkeypatch.setattr(invoke_module, "_start_workspace_monitor", record_start_workspace_monitor)
    result = _end_to_end_test_for_harness(
        tmp_path, transport, positive=True, monkeypatch=monkeypatch
    )
    assert len(monitor_factories) == 1
    assert callable(monitor_factories[0])
    assert result.multimodal_tool_used is not None
    assert result.multimodal_tool_used.provenance is result.multimodal_tool_used.provenance.WIRE, (
        f"transport {transport!r}: multimodal fact graded "
        f"{result.multimodal_tool_used.provenance.name!r} (expected WIRE) "
        f"-- detail: {result.multimodal_tool_used.detail}"
    )


def test_cursor_smoke_accepts_operator_login_without_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _end_to_end_test_for_harness(
        tmp_path,
        "cursor",
        positive=True,
        monkeypatch=monkeypatch,
        cursor_credential="login",
    )

    assert result.multimodal_tool_used is not None
    assert result.multimodal_tool_used.provenance is result.multimodal_tool_used.provenance.WIRE


@pytest.mark.parametrize(
    "transport",
    _INLINE_IMAGE_RESTRICTED_TRANSPORTS,
)
def test_restricted_transport_cannot_grade_wire(
    transport: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _end_to_end_test_for_harness(
        tmp_path, transport, positive=True, monkeypatch=monkeypatch
    )

    assert result.multimodal_tool_used is not None
    provenance = result.multimodal_tool_used.provenance
    assert provenance is not provenance.WIRE, (
        f"transport {transport!r} is denied inline image delivery, so it "
        f"cannot grade WIRE; got {provenance.name!r}"
    )
    assert "resource_reference_replay" in result.multimodal_tool_used.detail


@pytest.mark.parametrize(
    "transport",
    _IGNORE_RESPONSE_TRANSPORTS,
)
def test_ignore_response_multimodal_run_exits_nonzero(
    transport: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _end_to_end_test_for_harness(
        tmp_path, transport, positive=False, monkeypatch=monkeypatch
    )
    assert result.multimodal_tool_used is not None
    assert (
        result.multimodal_tool_used.provenance is not result.multimodal_tool_used.provenance.WIRE
    ), (
        f"transport {transport!r}: ignore-response stub dials the endpoint once "
        f"and discards the response; the multimodal fact must NOT grade WIRE "
        f"(got {result.multimodal_tool_used.provenance.name!r}; "
        f"detail: {result.multimodal_tool_used.detail})"
    )
    assert any("multimodal break" in err.lower() for err in result.errors), (
        f"transport {transport!r}: expected a multimodal break in errors, got: {result.errors!r}"
    )


def test_skip_media_multimodal_run_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _end_to_end_test_for_harness(
        tmp_path,
        "agy",
        positive=True,
        monkeypatch=monkeypatch,
        skip_media=True,
    )
    assert result.multimodal_tool_used is not None
    assert (
        result.multimodal_tool_used.provenance is not result.multimodal_tool_used.provenance.WIRE
    ), (
        f"skip-media stub never makes the media call; the multimodal fact "
        f"must NOT grade WIRE (got {result.multimodal_tool_used.provenance.name!r}; "
        f"detail: {result.multimodal_tool_used.detail})"
    )
    assert any("multimodal break" in err.lower() for err in result.errors), (
        f"expected a multimodal break in errors, got: {result.errors!r}"
    )


def test_stub_agent_module_imports_cleanly() -> None:
    spec_path = _stub_script_path()
    if not spec_path.exists():
        pytest.skip("stub agent not on disk")
    import importlib.util

    spec = importlib.util.spec_from_file_location("mock_multimodal_agent", spec_path)
    if spec is None or spec.loader is None:
        pytest.skip("stub agent spec not loadable")
    module = importlib.util.module_from_spec(spec)
    assert module is not None
    spec.loader.exec_module(module)
    assert hasattr(module, "main")
    assert hasattr(module, "_make_emit_functions")
    assert hasattr(module, "_resolve_transport")
    assert hasattr(module, "_dispatch")
