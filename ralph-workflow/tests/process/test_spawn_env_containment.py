"""Containment coverage for spawn-capable process entry points."""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable
from importlib import import_module

from ralph.cli._prompt_helper_entry import main as prompt_main
from ralph.cli.main import main as cli_main
from ralph.install import main as install_main
from ralph.mcp.server.runtime import main as mcp_main
from ralph.test_suites import main as suites_main
from ralph.verify import main as verify_main
from ralph.verify_timeout import main as verify_timeout_main

_ENTRY_POINTS: tuple[Callable[..., object], ...] = (
    cli_main,
    mcp_main,
    prompt_main,
    verify_main,
    verify_timeout_main,
    suites_main,
    install_main,
)


def _subprocess_env(extra_env: dict[str, str] | None) -> dict[str, str]:
    process_reader = import_module("ralph.agents.invoke._process_reader")
    return process_reader._subprocess_env(extra_env)


def test_spawn_capable_entry_points_sanitize_before_work() -> None:
    for entry_point in _ENTRY_POINTS:
        source = inspect.getsource(entry_point)
        function = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef))
        calls = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        sanitizer_calls = [node for node in calls if node.func.id == "sanitize_process_environment"]
        assert sanitizer_calls, f"{entry_point.__module__}.{entry_point.__name__} must sanitize its base env"
        spawn_calls = [
            node
            for node in calls
            if node.func.id in {"run_standalone_server", "run_test_suites", "run_verify"}
        ]
        assert all(sanitizer_calls[0].lineno < call.lineno for call in spawn_calls)


def test_agent_env_builder_keeps_explicit_malloc_debug_configuration() -> None:
    env = _subprocess_env({"MallocStackLogging": "1"})

    assert env["MallocStackLogging"] == "1"
