"""Keep execve-illegal NUL characters out of every child-process spawn.

``execve`` carries argv tokens, environment entries, and the child's
working directory as NUL-terminated C strings, so a NUL *inside* any of
them cannot cross the boundary at all: CPython raises ``ValueError:
embedded null byte`` out of ``subprocess`` before the fork even happens.

The two halves of that boundary are not the same kind of value, so they
get opposite treatment:

``argv[1:]`` carries content Ralph does not author -- a positional agent
prompt holding a git diff (Pi, Cursor, Kimi), an agent-authored ``git
commit -m`` subject. One source file with a literal NUL in a string
literal is enough to put one in a diff, and from there into the prompt.
A NUL can never be transmitted, so the choice is to drop the spawn or to
drop the NULs; dropping the spawn aborts a phase over content the child
would have read happily. ``argv[1:]`` is therefore STRIPPED, and every strip is logged with its
argv index. ``argv[0]`` is left alone: it names the program, and
rewriting it would run a different binary than the caller asked for, so
it is rejected -- like ``cwd`` and ``env`` -- by
:func:`ralph.process._spawn_validation.validate_spawn_arguments`, which
every spawn seam calls immediately after this one.

``env`` and ``cwd`` carry no authored content -- they are Ralph's own
control values, and a NUL in one is a defect, not data. Silently
rewriting a path could also turn a validated path into a different one,
so those are REJECTED by the same validator.

SECURITY CONTRACT for the strip: a validator that makes a security
decision about an argv token (a denylisted flag, a blacklisted command)
MUST reject NUL itself, at its own boundary. Otherwise a caller could
hide a denied token from the validator as ``--ext-di\\x00ff`` and have
this module hand the stripped, denied token to execve. The three argv
validators in this repo do exactly that:
``ralph.mcp.tools.git_read._validate_diff_args``,
``ralph.mcp.tools.exec.parse_exec_params``, and
``ralph.mcp.tools.unsafe_exec.handle_unsafe_exec`` (which guards
``_enforce_vcs_blacklist``).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Sequence

#: The one character ``execve`` cannot carry inside an argument.
NUL_CHARACTER = "\x00"


def _strip_nul(value: str) -> tuple[str, int]:
    """Return ``value`` without NUL characters plus the number removed."""
    removed = value.count(NUL_CHARACTER)
    if removed == 0:
        return value, 0
    return value.replace(NUL_CHARACTER, ""), removed


def _scope(label: str | None) -> str:
    return f" for {label}" if label else ""


def sanitize_spawn_command(
    command: Sequence[str],
    *,
    label: str | None = None,
) -> tuple[str, ...]:
    """Return ``command`` as a tuple with every embedded NUL removed.

    A ``str`` or ``PathLike[str]`` token is sanitized; every other form
    ``subprocess`` accepts (``bytes``, ``PathLike[bytes]``) is forwarded
    untouched to :func:`ralph.process._spawn_validation.validate_spawn_arguments`,
    which rejects a NUL in it by name.
    """
    cleaned: list[str] = []
    sites: list[str] = []
    for index, token in enumerate(command):
        if index == 0:
            # argv[0] names the program, so it is never stripped: running a
            # DIFFERENT binary than the caller asked for is the same hazard
            # that makes a rewritten cwd unacceptable. It is left intact for
            # ``validate_spawn_arguments`` to reject by name, with the
            # ``InvalidSpawnArgumentError`` every layer above the spawn seams
            # already converts into its own structured failure.
            cleaned.append(token)
            continue
        try:
            # ``os.fspath`` keeps a ``PathLike`` token working, as it did when
            # this was a bare ``tuple(command)``; a ``str`` passes through
            # untouched. A ``bytes`` / ``PathLike[bytes]`` token raises here and
            # is forwarded as-is: it carries no authored prose, and
            # ``validate_spawn_arguments`` rejects a NUL in it by name.
            value, removed = _strip_nul(os.fspath(token))
        except TypeError:
            cleaned.append(token)
            continue
        cleaned.append(value)
        if removed:
            sites.append(f"argv[{index}]: {removed}")
    if sites:
        logger.warning(
            f"Spawn{_scope(label)}: stripped NUL characters from the command "
            f"({', '.join(sites)}) — execve cannot carry an embedded NUL"
        )
    return tuple(cleaned)
