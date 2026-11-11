
from __future__ import annotations

import os
from collections.abc import Buffer
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.process._agent_launch_error import AgentLaunchError
from ralph.runtime_events import record_runtime_event

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

type SpawnArgument = str | bytes | os.PathLike[str] | os.PathLike[bytes]

_NUL_TEXT = "\x00"
_NUL_BYTES = b"\x00"


@runtime_checkable
class _PromptMaterializerModule(Protocol):

    def materialize_argv_prompt(self, prompt: str, cwd: str | None) -> str:
        pass


@runtime_checkable
class _FsPathLike(Protocol):

    def __fspath__(self) -> str | bytes:
        ...


class InvalidSpawnArgumentError(OSError, ValueError):
    pass


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Buffer):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, _FsPathLike):
        return _as_text(value.__fspath__())
    return repr(value)


def _carries_nul(value: object) -> bool:
    if isinstance(value, str):
        return _NUL_TEXT in value
    if isinstance(value, Buffer):
        return _NUL_BYTES in bytes(value)
    if isinstance(value, _FsPathLike):
        return _carries_nul(value.__fspath__())
    return False


def _spawn_target(command: Sequence[SpawnArgument]) -> str:
    return repr(_as_text(command[0]))


def _env_label(name: object) -> str:
    return _as_text(name).replace(_NUL_TEXT, "?")


def spawn_payload_bytes(command: Sequence[SpawnArgument], env: Mapping[str, str] | None) -> int:
    values = [os.fsencode(item) for item in command]
    if env is not None:
        values.extend(os.fsencode(f"{key}={value}") for key, value in env.items())
    return sum(len(value) + 1 for value in values)


def arg_max_limit(sysconf: Callable[[str], int] = os.sysconf) -> int:
    try:
        limit: int = sysconf("SC_ARG_MAX")
    except (AttributeError, OSError, ValueError):
        return 1024 * 1024
    return limit if limit > 0 else 1024 * 1024


def reduce_oversized_inline_prompt(
    command: Sequence[str],
    *,
    cwd: str | None,
) -> tuple[str, ...]:
    if "--" not in command:
        return tuple(command)
    prompt_index = command.index("--") + 1
    if prompt_index >= len(command) or not isinstance(command[prompt_index], str):
        return tuple(command)
    module: object = import_module("ralph.agents.invoke._command_builders")
    if not isinstance(module, _PromptMaterializerModule):
        raise RuntimeError("prompt materializer is unavailable")
    prompt = command[prompt_index]
    replacement = module.materialize_argv_prompt(prompt, cwd)
    reduced = list(command)
    reduced[prompt_index] = replacement
    return tuple(reduced)


def prepare_spawn_command(
    command: Sequence[str],
    *,
    cwd: str | None,
    env: Mapping[str, str] | None,
    payload_limit: int | None = None,
) -> tuple[str, ...]:
    limit = arg_max_limit() if payload_limit is None else payload_limit
    prepared: tuple[str, ...] = tuple(command)
    if spawn_payload_bytes(prepared, env) > limit:
        prepared = reduce_oversized_inline_prompt(prepared, cwd=cwd)
    validate_spawn_arguments(prepared, cwd=cwd, env=env, payload_limit=limit)
    return prepared


def validate_spawn_arguments(
    command: Sequence[SpawnArgument],
    *,
    cwd: SpawnArgument | None,
    env: Mapping[str, str] | None,
    payload_limit: int | None = None,
) -> None:
    if not command:
        raise InvalidSpawnArgumentError(
            "cannot spawn: the command is empty, so there is no executable to run; "
            "argv[0] must name the program"
        )
    offender: str | None = None
    for index, argument in enumerate(command):
        if _carries_nul(argument):
            offender = f"command[{index}]"
            break
    if offender is None and cwd is not None and _carries_nul(cwd):
        offender = "cwd"
    if offender is None and env is not None:
        for name, value in env.items():
            if _carries_nul(name) or _carries_nul(value):
                offender = f"env[{_env_label(name)}]"
                break
    if offender is not None:
        raise InvalidSpawnArgumentError(
            f"cannot spawn {_spawn_target(command)}: {offender} contains an "
            "embedded null byte, which the OS exec interface cannot carry"
        )
    payload_bytes = spawn_payload_bytes(command, env)
    limit = arg_max_limit() if payload_limit is None else payload_limit
    if payload_bytes > limit:
        detail = f"argv+env = {payload_bytes} bytes exceeds limit {limit}"
        record_runtime_event("runtime_launch", detail)
        exc = OSError(7, detail)
        raise AgentLaunchError(_as_text(command[0]), exc, payload_bytes) from exc
