from __future__ import annotations

import inspect
from pathlib import Path

from ralph.config import _env
from ralph.config._agent_overrides import opencode_binary_override
from ralph.config._paths import resolve_global_config_dir


def test_env_boundary_is_the_only_config_environment_read() -> None:
    config_root = Path(_env.__file__).parent
    readers = [
        path.name
        for path in config_root.glob("**/*.py")
        if "os.environ" in path.read_text(encoding="utf-8")
    ]
    assert readers == ["_env.py"]


def test_env_boundary_reads_only_the_injected_mapping() -> None:
    fake = {"VALUE": "injected", "XDG_CONFIG_HOME": "/injected/config"}
    assert _env.getenv("VALUE", fake) == "injected"
    assert _env.getenv("MISSING", fake) is None
    assert _env.resolve_config_home(fake) == Path("/injected/config")


def test_config_path_preserves_set_empty_and_unset_resolution() -> None:
    fallback = Path.home() / ".config"
    assert resolve_global_config_dir({"XDG_CONFIG_HOME": "/custom"}) == Path("/custom")
    assert resolve_global_config_dir({"XDG_CONFIG_HOME": ""}) == fallback
    assert resolve_global_config_dir({}) == fallback


def test_agent_override_preserves_set_empty_and_unset_values() -> None:
    assert opencode_binary_override({"RALPH_OPENCODE_BINARY": "custom"}.get) == "custom"
    assert opencode_binary_override({"RALPH_OPENCODE_BINARY": ""}.get) == ""
    assert opencode_binary_override({}.get) is None


def test_env_api_exposes_no_process_environment_mutator() -> None:
    public_functions = {
        name: value
        for name, value in inspect.getmembers(_env, inspect.isfunction)
        if not name.startswith("_")
    }
    assert set(public_functions) == {"getenv", "resolve_config_home"}
    assert all(
        "value" not in inspect.signature(function).parameters
        for function in public_functions.values()
    )
