from __future__ import annotations

from pathlib import Path

import ralph.config as config_package
from ralph.config import resolve_global_config_dir
from ralph.config._env import (
    copy_environ,
    del_environ,
    environ,
    getenv,
    pop_environ,
    resolve_environ,
    set_environ,
    setdefault_environ,
)
from ralph.config.agent_detection import opencode_binary_override

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "configuration environment boundary": "the same environment resolution contracts are asserted through public configuration exports instead of private modules",
}


def test_env_boundary_is_the_only_config_environment_read() -> None:
    config_root = Path(config_package.__file__).parent
    readers = [
        path.name
        for path in config_root.glob("**/*.py")
        if "os.environ" in path.read_text(encoding="utf-8")
    ]
    assert readers == ["_env.py"]


def test_env_boundary_reads_only_the_injected_mapping() -> None:
    requested: list[str] = []
    fake = {"RALPH_OPENCODE_BINARY": "injected"}

    def injected_getter(name: str) -> str | None:
        requested.append(name)
        return fake.get(name)

    assert opencode_binary_override(injected_getter) == "injected"
    assert requested == ["RALPH_OPENCODE_BINARY"]


def test_config_path_preserves_set_empty_and_unset_resolution() -> None:
    fallback = Path.home() / ".config"
    assert resolve_global_config_dir({"XDG_CONFIG_HOME": "/custom"}) == Path("/custom")
    assert resolve_global_config_dir({"XDG_CONFIG_HOME": ""}) == fallback
    assert resolve_global_config_dir({}) == fallback


def test_agent_override_preserves_set_empty_and_unset_values() -> None:
    assert opencode_binary_override({"RALPH_OPENCODE_BINARY": "custom"}.get) == "custom"
    assert opencode_binary_override({"RALPH_OPENCODE_BINARY": ""}.get) == ""
    assert opencode_binary_override({}.get) is None


def test_public_config_resolution_does_not_mutate_the_injected_environment() -> None:
    env = {"XDG_CONFIG_HOME": "/injected/config"}
    original = dict(env)

    assert resolve_global_config_dir(env) == Path("/injected/config")
    assert env == original


def test_env_mapping_seam_resolves_injected_and_process_mapping() -> None:
    injected = {"RALPH_L18_SEAM": "injected"}
    assert resolve_environ(injected) is injected
    assert environ() is resolve_environ(None)
    assert getenv("RALPH_L18_SEAM", injected) == "injected"
    assert isinstance(copy_environ(), dict)


def test_env_mutate_seam_set_setdefault_pop_and_delete(monkeypatch) -> None:
    key = "RALPH_L18_MUTATE_SEAM"
    monkeypatch.delenv(key, raising=False)
    set_environ(key, "one")
    assert getenv(key) == "one"
    assert setdefault_environ(key, "two") == "one"
    assert pop_environ(key) == "one"
    set_environ(key, "three")
    del_environ(key)
    assert getenv(key) is None
