"""Reject spawn arguments the OS exec interface cannot carry.

A NUL byte anywhere in argv, cwd, or the environment map makes CPython's
``_fork_exec`` raise ``ValueError("embedded null byte")`` from deep inside
``subprocess.Popen.__init__``; an empty argv raises ``IndexError`` from the
same place. Neither message names the process nor the offending argument.

Worse, neither is an ``OSError``. The spawn seams' failure bookkeeping ran
only for ``OSError``, so no FAILED ``ProcessRecord`` was built for these
inputs -- and every layer above the seams that converts a spawn failure into
a structured outcome (``ExecutorError`` in the agent executor,
``ExecutionError`` in the agent-facing ``exec`` MCP tool,
``ProcessExecutionError`` in ``ralph.executor.process``, the GitPython
fallbacks around ``run_git``) keys on ``OSError`` too, so all of them were
bypassed as well. The pipeline's blanket ``except Exception`` in
``ralph.pipeline.effect_executor`` still absorbed the escape into
``PipelineEvent.AGENT_FAILURE``, so the run did not hard-crash; what was lost
was the diagnosis and every structured outcome on the way up.

Validating at the boundary instead fails closed with a message that names
exactly which argument is unusable, and raises
:class:`InvalidSpawnArgumentError`, which is both an ``OSError`` (the OS
cannot perform this spawn, so every existing spawn-failure handler sees it)
and a ``ValueError`` (the class ``Popen`` raises for the same input today, so
no existing caller regresses). Values are never interpolated into the
message: an environment value can hold a credential.

The accepted argument forms mirror what ``Popen`` itself accepts -- ``str``,
``bytes``-like, and ``os.PathLike`` -- so validation never rejects, or raises
``TypeError`` over, an input the OS would have carried happily.
"""

from __future__ import annotations

import os
from collections.abc import Buffer
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: Every form ``subprocess.Popen`` accepts for an argv element or a cwd.
type SpawnArgument = str | bytes | os.PathLike[str] | os.PathLike[bytes]

_NUL_TEXT = "\x00"
_NUL_BYTES = b"\x00"


@runtime_checkable
class _FsPathLike(Protocol):
    """Structural stand-in for ``os.PathLike`` with a non-generic path type.

    ``os.PathLike`` is generic in the string type it yields, so narrowing to it
    yields ``PathLike[Any]`` and ``os.fspath`` then returns ``Any``. Only the
    two concrete forms exist, and both are handled below.
    """

    def __fspath__(self) -> str | bytes:
        """Return the filesystem path this object stands for."""
        ...


class InvalidSpawnArgumentError(OSError, ValueError):
    """A spawn argument the OS exec interface cannot carry.

    Subclasses both builtin classes on purpose. ``OSError`` is the class
    every spawn-failure handler in the codebase already keys on -- being
    unable to start the process is exactly what happened -- and ``ValueError``
    is what ``subprocess.Popen`` raises for the same input, so callers written
    against the pre-validation behaviour keep working.
    """


def _as_text(value: object) -> str:
    """Render a spawn argument for a message without ever raising."""
    if isinstance(value, str):
        return value
    if isinstance(value, Buffer):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, _FsPathLike):
        return _as_text(value.__fspath__())
    return repr(value)


def _carries_nul(value: object) -> bool:
    """Return whether ``value``, in any form ``Popen`` accepts, holds a NUL byte."""
    if isinstance(value, str):
        return _NUL_TEXT in value
    if isinstance(value, Buffer):
        return _NUL_BYTES in bytes(value)
    if isinstance(value, _FsPathLike):
        return _carries_nul(value.__fspath__())
    # Any other type is one ``Popen`` itself rejects; leave that verdict to it
    # rather than inventing a second, differently-worded rejection here.
    return False


def _spawn_target(command: Sequence[SpawnArgument]) -> str:
    return repr(_as_text(command[0]))


def _env_label(name: object) -> str:
    """Name an environment variable in a message, with any NUL made visible."""
    return _as_text(name).replace(_NUL_TEXT, "?")


def validate_spawn_arguments(
    command: Sequence[SpawnArgument],
    *,
    cwd: SpawnArgument | None,
    env: Mapping[str, str] | None,
) -> None:
    """Raise when a spawn argument is one the OS exec interface cannot carry.

    Args:
        command: The argv the child would be executed with.
        cwd: The working directory the child would start in, if any.
        env: The environment map the child would actually inherit, if any.
            Callers pass the map AFTER scrubbing, because a variable removed
            before the child exists can never reach it.

    Raises:
        InvalidSpawnArgumentError: When the command is empty, or naming the
            first argument that carries a NUL byte. The offending value itself
            is never included, because an environment value can hold a
            credential.
    """
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
