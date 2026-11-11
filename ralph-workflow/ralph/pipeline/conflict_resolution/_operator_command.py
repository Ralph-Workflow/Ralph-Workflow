"""Operator command synthesis for conflicted paths.

This module provides the manual Git command an operator can execute to resolve
or stage conflicted paths when automated conflict resolution has exhausted its
options or cannot proceed.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["operator_command_for_paths"]


def operator_command_for_paths(paths: Sequence[str]) -> str:
    """Produce the exact manual git command naming each path for operator action.

    When ``paths`` is non-empty, produces ``git add -- <quoted_paths>`` with each
    path quoted via :func:`shlex.quote` to protect against shell injection and
    spaces. The ``--`` separator ensures paths beginning with a dash are not
    interpreted as flags by Git.

    When ``paths`` is empty, returns an empty string.

    Args:
        paths: Tuple of file paths with unresolved conflicts.

    Returns:
        The shell-safe ``git add`` command string, or an empty string if ``paths``
        is empty.
    """
    if not paths:
        return ""
    quoted_paths = " ".join(shlex.quote(path) for path in paths)
    return f"git add -- {quoted_paths}"
