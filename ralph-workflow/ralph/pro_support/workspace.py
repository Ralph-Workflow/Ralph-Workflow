"""Workspace-root resolution for Pro mode.

Pro sets the engine's workspace root via the ``RALPH_WORKSPACE`` env
var. When that variable is unset or empty, the engine falls back to
:func:`pathlib.Path.cwd`, matching the existing single-checkout
behaviour. The result is always resolved through :meth:`Path.resolve`
so the value is a canonical, absolute path regardless of the input
form (relative, contains ``..``, symlink, etc.).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pro_support.env import RALPH_WORKSPACE, _get_env

if TYPE_CHECKING:
    from collections.abc import Mapping


def resolve_pro_workspace(
    env: Mapping[str, str] | None = None,
    fallback: Path | str | None = None,
) -> Path:
    """Return the Pro-mode workspace root, falling back when RALPH_WORKSPACE is unset.

    Args:
        env: Optional env mapping. When ``None`` (default), ``os.environ``
            is read at call time.
        fallback: Path to use when ``RALPH_WORKSPACE`` is unset or empty.
            Defaults to :func:`Path.cwd`. The fallback is also resolved
            via :meth:`Path.resolve` so symlinks are normalised.

    Returns:
        Canonical, absolute :class:`pathlib.Path` for the workspace.
    """
    env_map = _get_env(env)
    candidate = env_map.get(RALPH_WORKSPACE, "")
    if candidate:
        return Path(candidate).expanduser().resolve()
    base = Path.cwd() if fallback is None else Path(fallback)
    return base.expanduser().resolve()


def resolve_pro_workspace_from_environ(
    fallback: Path | str | None = None,
) -> Path:
    """Convenience wrapper that always reads ``os.environ``.

    Equivalent to ``resolve_pro_workspace(env=os.environ, fallback=...)``.
    Kept separate from the pure resolver so call sites that genuinely
    want a fresh os.environ read can document that intent without
    importing the ``os`` module at every site.
    """
    return resolve_pro_workspace(os.environ, fallback=fallback)


__all__ = ["resolve_pro_workspace", "resolve_pro_workspace_from_environ"]
