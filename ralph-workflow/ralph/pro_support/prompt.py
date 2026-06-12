"""Operator-visible source-prompt path resolution for Pro mode.

Pro sets the engine's source-prompt file via the ``PROMPT_PATH`` env
var. When that variable is unset or empty, the engine falls back to
``<workspace>/PROMPT.md``. This module is the **single source of
truth** for the operator-visible source prompt path; every call site in
the engine that reads ``PROMPT.md`` (rather than the materialised
``.agent/CURRENT_PROMPT.md``) must go through
:func:`resolve_effective_prompt_path`.

Callers operating on the engine-owned materialised
``.agent/CURRENT_PROMPT.md` MUST NOT use this resolver — that path is
engine-owned and is never overridden by ``PROMPT_PATH``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pro_support.env import PROMPT_PATH, _get_env

if TYPE_CHECKING:
    from collections.abc import Mapping


DEFAULT_SOURCE_PROMPT_NAME = "PROMPT.md"


def resolve_effective_prompt_path(
    workspace_root: Path | str,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return the effective source-prompt path, honouring ``PROMPT_PATH``.

    Resolution order:

    1. If ``PROMPT_PATH`` is set and non-empty in the supplied ``env``:
       - when absolute, return it resolved through :meth:`Path.resolve`;
       - when relative, resolve it relative to ``workspace_root`` and
         return the result through :meth:`Path.resolve`.
    2. Otherwise return ``<workspace_root>/PROMPT.md`` resolved through
       :meth:`Path.resolve`.

    Args:
        workspace_root: Workspace root directory. The returned path is
            always relative to this root when ``PROMPT_PATH`` is unset.
        env: Optional env mapping. When ``None`` (default), ``os.environ``
            is read at call time.
    """
    root = Path(workspace_root).expanduser().resolve()
    env_map = _get_env(env)
    raw = env_map.get(PROMPT_PATH, "")
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (root / candidate).resolve()
    return (root / DEFAULT_SOURCE_PROMPT_NAME).resolve()


__all__ = ["DEFAULT_SOURCE_PROMPT_NAME", "resolve_effective_prompt_path"]
