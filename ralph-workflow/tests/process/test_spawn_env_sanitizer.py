"""Regression coverage for inherited macOS malloc-debug environment cleanup."""

from __future__ import annotations

import os
from importlib import import_module

import pytest

from ralph.process._spawn_env import (
    MALLOC_DEBUG_NOISE_VARS,
    sanitize_process_environment,
    strip_malloc_debug_noise,
)


def _build_env(extra_env: dict[str, str] | None) -> dict[str, str]:
    executor_process = import_module("ralph.executor.process")
    return executor_process._build_env(extra_env)


def _subprocess_env(extra_env: dict[str, str] | None) -> dict[str, str]:
    process_reader = import_module("ralph.agents.invoke._process_reader")
    return process_reader._subprocess_env(extra_env)


def test_strip_malloc_debug_noise_removes_only_known_vars() -> None:
    env = {
        "MallocStackLogging": "1",
        "MallocStackLoggingNoCompact": "1",
        "UNRELATED": "preserved",
    }

    removed = strip_malloc_debug_noise(env)

    assert removed == MALLOC_DEBUG_NOISE_VARS
    assert env == {"UNRELATED": "preserved"}


def test_strip_malloc_debug_noise_is_noop_when_vars_are_absent() -> None:
    env = {"UNRELATED": "preserved"}

    removed = strip_malloc_debug_noise(env)

    assert removed == ()
    assert env == {"UNRELATED": "preserved"}


def test_sanitize_process_environment_cleans_the_inherited_base(
    monkeypatch: object,
) -> None:
    monkeypatch.setattr(
        os,
        "environ",
        {"MallocStackLogging": "1", "MallocStackLoggingNoCompact": "1", "KEEP": "1"},
    )

    removed = sanitize_process_environment()

    assert removed == MALLOC_DEBUG_NOISE_VARS
    assert os.environ == {"KEEP": "1"}


def test_cli_logs_stripped_malloc_debug_vars(monkeypatch: object) -> None:
    """DA-003: the CLI reports inherited malloc-debug cleanup at DEBUG."""
    import ralph.cli.main as cli_module

    class _Logger:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def debug(self, message: str, *args: str) -> None:
            self.messages.append(message.format(*args))

    test_logger = _Logger()
    monkeypatch.setattr(os, "environ", {"MallocStackLogging": "1"})

    def _exit_early(**_kwargs: object) -> None:
        raise SystemExit()

    monkeypatch.setattr(cli_module, "_handle_early_exit_flags", _exit_early)
    monkeypatch.setattr(cli_module, "_validate_mode_flags", lambda **_kwargs: None)
    monkeypatch.setattr(cli_module, "_resolve_policy_mode", lambda **_kwargs: None)
    monkeypatch.setattr(cli_module, "resolve_effective_verbosity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_module, "_get_cli_context", lambda: None)
    monkeypatch.setattr(cli_module, "logger", test_logger)

    with pytest.raises(SystemExit):
        cli_module.main(None, version=True)

    assert test_logger.messages == [
        "Stripped inherited malloc-debug environment variables: MallocStackLogging"
    ]


def test_agent_env_builder_keeps_explicit_malloc_debug_configuration() -> None:
    env = _subprocess_env({"MallocStackLogging": "1"})

    assert env["MallocStackLogging"] == "1"


def test_downstream_env_builders_inherit_the_sanitized_base(monkeypatch: object) -> None:
    monkeypatch.setattr(os, "environ", {"MallocStackLogging": "1", "KEEP": "1"})

    sanitize_process_environment()

    assert "MallocStackLogging" not in _build_env(None)
    assert "MallocStackLogging" not in _subprocess_env(None)
    assert _build_env(None)["KEEP"] == "1"
    assert _subprocess_env(None)["KEEP"] == "1"
