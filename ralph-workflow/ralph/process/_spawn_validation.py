"""Reject spawn arguments the OS exec interface cannot carry.

A NUL byte anywhere in argv, cwd, or the environment map makes CPython's
``_fork_exec`` raise ``ValueError("embedded null byte")`` from deep inside
``subprocess.Popen.__init__``. That message names neither the process nor
the offending argument, and ``ValueError`` is not an ``OSError``, so the
spawn seams' failure bookkeeping never ran and the bare exception unwound
through the entire pipeline.

Validating at the boundary instead fails closed with a message that names
exactly which argument is unusable, and lets the spawn seams record the
attempt as a FAILED process the same way a refused ``exec`` is recorded.
Values are never interpolated into the message: an environment value can
hold a credential.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_NUL = "\x00"


def _spawn_target(command: Sequence[str]) -> str:
    return repr(command[0]) if command else "<empty command>"


def validate_spawn_arguments(
    command: Sequence[str],
    *,
    cwd: str | None,
    env: Mapping[str, str] | None,
) -> None:
    """Raise when any spawn argument carries a NUL byte.

    Args:
        command: The argv the child would be executed with.
        cwd: The working directory the child would start in, if any.
        env: The environment map the child would inherit, if any.

    Raises:
        ValueError: Naming the first argument that carries a NUL byte.
            The offending value itself is never included, because an
            environment value can hold a credential.
    """
    offender: str | None = None
    for index, argument in enumerate(command):
        if _NUL in argument:
            offender = f"command[{index}]"
            break
    if offender is None and cwd is not None and _NUL in cwd:
        offender = "cwd"
    if offender is None and env is not None:
        for name, value in env.items():
            if _NUL in name or _NUL in value:
                offender = f"env[{name.replace(_NUL, '?')}]"
                break
    if offender is not None:
        raise ValueError(
            f"cannot spawn {_spawn_target(command)}: {offender} contains an "
            "embedded null byte, which the OS exec interface cannot carry"
        )
