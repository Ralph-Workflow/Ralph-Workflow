"""Pro contract env var readers.

The Pro↔Ralph contract (see
``Ralph-Workflow-Pro/docs/product-spec/CONTRACT_RALPH_INTEGRATION.md``
§3) limits Ralph-Workflow-Pro to setting exactly three engine-facing
environment variables on the subprocess:

- ``RALPH_WORKFLOW_PRO`` — non-empty truthy marker that the engine uses to
  detect "we are a Pro subprocess."
- ``RALPH_WORKSPACE`` — absolute or relative path to the workspace root.
  When set, the engine must prefer this over the current working
  directory when resolving the workspace scope.
- ``PROMPT_PATH`` — absolute or relative path to the operator-visible
  source prompt file. When set, the engine must prefer this over
  ``<workspace>/PROMPT.md``.

The contract explicitly states the engine MUST NOT require any
additional variables. Run identifiers, heartbeat tokens, ports, and
other Pro-owned metadata are delivered through a Pro-owned marker file
at ``<workspace>/.ralph/run.json`` (see :mod:`ralph.pro_support.marker`).

Each helper in this module is a *pure* function. None of them perform
I/O. Each accepts an optional ``env`` mapping so tests can inject values
without monkeypatching ``os.environ``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


RALPH_WORKFLOW_PRO = "RALPH_WORKFLOW_PRO"
RALPH_WORKSPACE = "RALPH_WORKSPACE"
PROMPT_PATH = "PROMPT_PATH"


def _get_env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    """Return the supplied ``env`` mapping or ``os.environ`` when omitted.

    Centralises the "default to os.environ" pattern so every helper has
    the same behaviour and tests can inject without monkeypatching.
    """
    return os.environ if env is None else env


def is_pro_mode(env: Mapping[str, str] | None = None) -> bool:
    """Return True when ``RALPH_WORKFLOW_PRO`` is set to a truthy value.

    "Truthy" here is a non-empty string. The contract does not specify
    a particular value; any non-empty value indicates Pro-mode.

    Args:
        env: Optional env mapping. When ``None`` (default), ``os.environ``
            is read at call time.
    """
    return bool(_get_env(env).get(RALPH_WORKFLOW_PRO))


def get_ralph_workspace(env: Mapping[str, str] | None = None) -> str | None:
    """Return the raw ``RALPH_WORKSPACE`` value, or ``None`` if unset/empty.

    This is a thin accessor; the canonical Path-aware resolver is
    :func:`ralph.pro_support.workspace.resolve_pro_workspace`.

    Args:
        env: Optional env mapping. When ``None`` (default), ``os.environ``
            is read at call time.
    """
    value = _get_env(env).get(RALPH_WORKSPACE, "")
    if not value:
        return None
    return value


def get_prompt_path(env: Mapping[str, str] | None = None) -> str | None:
    """Return the raw ``PROMPT_PATH`` value, or ``None`` if unset/empty.

    This is a thin accessor; the canonical Path-aware resolver is
    :func:`ralph.pro_support.prompt.resolve_effective_prompt_path`.

    Args:
        env: Optional env mapping. When ``None`` (default), ``os.environ``
            is read at call time.
    """
    value = _get_env(env).get(PROMPT_PATH, "")
    if not value:
        return None
    return value


__all__ = [
    "PROMPT_PATH",
    "RALPH_WORKFLOW_PRO",
    "RALPH_WORKSPACE",
    "get_prompt_path",
    "get_ralph_workspace",
    "is_pro_mode",
]
