"""Tests for rare entrypoints and MCP lazy exports."""

import builtins
import importlib
import runpy
import sys

import pytest


def _run_entrypoint(module_name: str) -> None:
    # Ensure we import a fresh module to guarantee __main__ logic runs.
    sys.modules.pop(module_name, None)
    runpy.run_module(module_name, run_name="__main__")


def test_ralph_main_entrypoint_calls_app(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []

    def fake_app() -> None:
        called.append("called")

    monkeypatch.setattr("ralph.cli.main.app", fake_app)

    _run_entrypoint("ralph.main")

    assert called == ["called"]


def test_ralph_dunder_main_entrypoint_calls_app(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []

    def fake_app() -> None:
        called.append("called")

    monkeypatch.setattr("ralph.cli.main.app", fake_app)

    _run_entrypoint("ralph.__main__")

    assert called == ["called"]


def test_ralph_mcp_server_entrypoint_calls_main(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []

    def fake_main() -> None:
        called.append("called")

    monkeypatch.setattr("ralph.mcp.server.runtime.main", fake_main)

    _run_entrypoint("ralph.mcp.server.__main__")

    assert called == ["called"]


def test_mcp_tool_bridge_lazy_exports_and_error() -> None:
    module = importlib.reload(importlib.import_module("ralph.mcp"))

    assert "ToolBridge" not in module.__dict__

    tool_bridge_module = importlib.import_module("ralph.mcp.tools.bridge")
    assert module.ToolBridge is tool_bridge_module.ToolBridge
    assert module.ToolBridge is module.__dict__["ToolBridge"]

    required_symbols = {"ToolBridge", "ToolBridgeError", "ToolDefinition", "ToolMetadata"}
    assert required_symbols.issubset(set(module.__all__))

    with pytest.raises(AttributeError):
        _ = module.__not_a_symbol__


def test_mcp_server_package_import_is_lazy_when_mcp_dependency_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("mcp"):
            raise ModuleNotFoundError("No module named 'mcp'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    for module_name in (
        "ralph.mcp.server",
        "ralph.mcp.server.runtime",
        "ralph.mcp.server.lifecycle",
    ):
        sys.modules.pop(module_name, None)

    module = importlib.import_module("ralph.mcp.server")

    assert module.start_mcp_server is not None
