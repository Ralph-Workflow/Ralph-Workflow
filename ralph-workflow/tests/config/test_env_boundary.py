from __future__ import annotations

import inspect
from pathlib import Path

import ralph.config as config_package
from ralph.config import resolve_global_config_dir
from ralph.config.agent_detection import opencode_binary_override


def test_env_boundary_is_the_only_config_environment_read() -> None:
    config_root = Path(config_package.__file__).parent
    readers = [
        path.name
        for path in config_root.glob("**/*.py")
        if "os.environ" in path.read_text(encoding="utf-8")
    ]
    assert readers == ["_env.py"]


def test_env_boundary_reads_only_the_injected_mapping() -> None:
    fake = {"VALUE": "injected", "XDG_CONFIG_HOME": "/injected/config"}
    assert resolve_global_config_dir(fake) == Path("/injected/config")
    assert opencode_binary_override({"RALPH_OPENCODE_BINARY": "injected"}.get) == "injected"
    assert opencode_binary_override({}.get) is None


def test_config_path_preserves_set_empty_and_unset_resolution() -> None:
    fallback = Path.home() / ".config"
    assert resolve_global_config_dir({"XDG_CONFIG_HOME": "/custom"}) == Path("/custom")
    assert resolve_global_config_dir({"XDG_CONFIG_HOME": ""}) == fallback
    assert resolve_global_config_dir({}) == fallback


def test_agent_override_preserves_set_empty_and_unset_values() -> None:
    assert opencode_binary_override({"RALPH_OPENCODE_BINARY": "custom"}.get) == "custom"
    assert opencode_binary_override({"RALPH_OPENCODE_BINARY": ""}.get) == ""
    assert opencode_binary_override({}.get) is None


def test_public_config_environment_api_exposes_no_process_environment_mutator() -> None:
    public_functions = {
        name: getattr(config_package, name)
        for name in config_package.__all__
        if inspect.isfunction(getattr(config_package, name))
    }
    assert "resolve_global_config_dir" in public_functions
    assert all(
        "value" not in inspect.signature(function).parameters
        for function in public_functions.values()
    )
    assert "env" in inspect.signature(resolve_global_config_dir).parameters
    assert "env_getter" in inspect.signature(opencode_binary_override).parameters
