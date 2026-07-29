"""Regression coverage for inherited macOS malloc-debug environment cleanup."""

from __future__ import annotations

import os
from importlib import import_module

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


def test_downstream_env_builders_inherit_the_sanitized_base(monkeypatch: object) -> None:
    monkeypatch.setattr(os, "environ", {"MallocStackLogging": "1", "KEEP": "1"})

    sanitize_process_environment()

    assert "MallocStackLogging" not in _build_env(None)
    assert "MallocStackLogging" not in _subprocess_env(None)
    assert _build_env(None)["KEEP"] == "1"
    assert _subprocess_env(None)["KEEP"] == "1"
