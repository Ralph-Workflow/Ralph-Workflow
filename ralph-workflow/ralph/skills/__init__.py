"""Baseline capability management for Ralph Workflow.

``ralph.skills.state_store`` is the PUBLIC name of the capability state
store. It resolves lazily, on first attribute access, so importing this
package does not eagerly pull the state store (and through it the artifact
backend) into every importing process.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

from ralph.skills._content import get_skill_content

if TYPE_CHECKING:
    import ralph.skills._state_store as state_store

__all__ = ["get_skill_content", "state_store"]


def __getattr__(name: str) -> object:
    """Resolve the lazily exported state-store module on first access.

    The public export is the module object itself, so callers read and
    write the LIVE module globals (for example ``DEFAULT_STATE_PATH``)
    exactly as they would through a direct import of the defining module.
    """
    if name != "state_store":
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)

    module = import_module("ralph.skills._state_store")
    cast("dict[str, object]", globals())["state_store"] = module
    return module
