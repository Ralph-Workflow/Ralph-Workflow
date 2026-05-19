from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

EXPECTED_MAJOR = 3
EXPECTED_MINOR = 12
EXPECTED_MICRO = 7


@pytest.fixture
def runtime_environment_module() -> object:
    try:
        return importlib.import_module("ralph.runtime.environment")
    except ModuleNotFoundError as exc:  # pragma: no cover - red phase only
        pytest.fail(f"runtime environment module missing: {exc}")


@pytest.fixture
def runtime_module() -> object:
    try:
        return importlib.import_module("ralph.runtime")
    except ModuleNotFoundError as exc:  # pragma: no cover - red phase only
        pytest.fail(f"runtime package missing: {exc}")


def make_sys(*, prefix: str, base_prefix: str, executable: str = "/usr/bin/python3") -> object:
    return SimpleNamespace(
        version_info=SimpleNamespace(
            major=EXPECTED_MAJOR,
            minor=EXPECTED_MINOR,
            micro=EXPECTED_MICRO,
            releaselevel="final",
            serial=0,
        ),
        version="3.12.7 (main, Jan 01 2026, 00:00:00) [Clang]",
        executable=executable,
        prefix=prefix,
        base_prefix=base_prefix,
        exec_prefix=prefix,
        base_exec_prefix=base_prefix,
        implementation=SimpleNamespace(name="cpython"),
    )


def test_detect_runtime_environment_captures_python_metadata(
    runtime_environment_module: object,
) -> None:
    runtime = runtime_environment_module.detect_runtime_environment(
        env={"HOME": "/tmp/home"},
        sys_module=make_sys(prefix="/usr", base_prefix="/usr"),
    )

    assert runtime.python.major == EXPECTED_MAJOR
    assert runtime.python.minor == EXPECTED_MINOR
    assert runtime.python.micro == EXPECTED_MICRO
    assert runtime.python.implementation == "cpython"
    assert runtime.python.executable == Path("/usr/bin/python3")


def test_detect_runtime_environment_marks_virtualenv_from_prefixes(
    runtime_environment_module: object,
) -> None:
    runtime = runtime_environment_module.detect_runtime_environment(
        env={},
        sys_module=make_sys(prefix="/tmp/.venv", base_prefix="/usr"),
    )

    assert runtime.in_virtualenv is True
    assert runtime.virtualenv_path == Path("/tmp/.venv")


def test_detect_runtime_environment_prefers_virtual_env_variable(
    runtime_environment_module: object,
) -> None:
    runtime = runtime_environment_module.detect_runtime_environment(
        env={"VIRTUAL_ENV": "/workspace/.venv"},
        sys_module=make_sys(prefix="/usr", base_prefix="/usr"),
    )

    assert runtime.in_virtualenv is True
    assert runtime.virtualenv_path == Path("/workspace/.venv")


def test_detect_runtime_environment_reports_non_virtualenv(
    runtime_environment_module: object,
) -> None:
    runtime = runtime_environment_module.detect_runtime_environment(
        env={},
        sys_module=make_sys(prefix="/usr", base_prefix="/usr"),
    )

    assert runtime.in_virtualenv is False
    assert runtime.virtualenv_path is None


def test_runtime_package_re_exports_environment_api(runtime_module: object) -> None:
    assert "RuntimeEnvironment" in runtime_module.__all__
    assert "PythonVersionInfo" in runtime_module.__all__
    assert hasattr(runtime_module, "detect_runtime_environment")
    assert hasattr(runtime_module, "is_virtualenv")
