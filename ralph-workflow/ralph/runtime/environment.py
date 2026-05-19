"""Runtime environment discovery helpers."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from ralph.runtime._version_info import PythonVersionInfo

if TYPE_CHECKING:
    from collections.abc import Mapping
if TYPE_CHECKING:

    class _VersionInfoProtocol(Protocol):
        @property
        def major(self) -> int: ...

        @property
        def minor(self) -> int: ...

        @property
        def micro(self) -> int: ...

        @property
        def releaselevel(self) -> str: ...

        @property
        def serial(self) -> int: ...

    class _ImplementationProtocol(Protocol):
        @property
        def name(self) -> str: ...

    class SysModuleProtocol(Protocol):
        """Subset of the sys module interface required for runtime environment detection."""

        @property
        def version_info(self) -> _VersionInfoProtocol: ...

        @property
        def version(self) -> str: ...

        @property
        def executable(self) -> str: ...

        @property
        def prefix(self) -> str: ...

        @property
        def base_prefix(self) -> str: ...

        @property
        def exec_prefix(self) -> str: ...

        @property
        def base_exec_prefix(self) -> str: ...

        @property
        def implementation(self) -> _ImplementationProtocol: ...


@dataclass(frozen=True)
class RuntimeEnvironment:
    """Snapshot of the active Python runtime environment."""

    python: PythonVersionInfo
    executable: Path
    prefix: Path
    base_prefix: Path
    exec_prefix: Path
    base_exec_prefix: Path
    in_virtualenv: bool
    virtualenv_path: Path | None
    env: Mapping[str, str]

    def get(self, name: str, default: str | None = None) -> str | None:
        """Return an environment variable from the captured snapshot."""

        return self.env.get(name, default)


_VIRTUAL_ENV_KEYS = ("VIRTUAL_ENV", "CONDA_PREFIX")


def _normalize_env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    captured = dict(os.environ if env is None else env)
    return MappingProxyType(captured)


def detect_virtualenv_path(
    env: Mapping[str, str] | None = None,
    *,
    sys_module: SysModuleProtocol = sys,
) -> Path | None:
    """Return the detected virtual environment path, if any."""

    captured_env = _normalize_env(env)
    explicit_path = next(
        (captured_env[key] for key in _VIRTUAL_ENV_KEYS if captured_env.get(key)),
        None,
    )
    if explicit_path:
        return Path(explicit_path)

    if sys_module.prefix != sys_module.base_prefix:
        return Path(sys_module.prefix)

    return None


def is_virtualenv(
    env: Mapping[str, str] | None = None,
    *,
    sys_module: SysModuleProtocol = sys,
) -> bool:
    """Return whether the current interpreter is running inside a virtual environment."""

    return detect_virtualenv_path(env, sys_module=sys_module) is not None


def detect_runtime_environment(
    env: Mapping[str, str] | None = None,
    *,
    sys_module: SysModuleProtocol = sys,
) -> RuntimeEnvironment:
    """Capture a structured snapshot of the active Python runtime."""

    captured_env = _normalize_env(env)
    virtualenv_path = detect_virtualenv_path(captured_env, sys_module=sys_module)

    return RuntimeEnvironment(
        python=PythonVersionInfo.from_sys(sys_module),
        executable=Path(sys_module.executable),
        prefix=Path(sys_module.prefix),
        base_prefix=Path(sys_module.base_prefix),
        exec_prefix=Path(sys_module.exec_prefix),
        base_exec_prefix=Path(sys_module.base_exec_prefix),
        in_virtualenv=virtualenv_path is not None,
        virtualenv_path=virtualenv_path,
        env=captured_env,
    )


__all__ = [
    "RuntimeEnvironment",
    "SysModuleProtocol",
    "detect_runtime_environment",
    "detect_virtualenv_path",
    "is_virtualenv",
]
