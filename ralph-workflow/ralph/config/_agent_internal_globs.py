from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def _load_agent_internal_paths_module() -> ModuleType:
    module_path = Path(__file__).resolve().parent.parent / "phases" / "_agent_internal_paths.py"
    spec = importlib.util.spec_from_file_location(
        "_ralph_agent_internal_paths_bootstrap_target", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot build import spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_agent_internal_paths_module = _load_agent_internal_paths_module()
_AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB: str = (
    _agent_internal_paths_module._AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB
)
AGENT_INTERNAL_DIR_GLOBS: frozenset[str] = _agent_internal_paths_module.AGENT_INTERNAL_DIR_GLOBS
AGENT_INTERNAL_ROOT_BASENAMES: frozenset[str] = (
    _agent_internal_paths_module.AGENT_INTERNAL_ROOT_BASENAMES
)
AGENT_INTERNAL_TOP_LEVEL_BASENAMES: frozenset[str] = (
    _agent_internal_paths_module.AGENT_INTERNAL_TOP_LEVEL_BASENAMES
)

__all__ = [
    "AGENT_INTERNAL_DIR_GLOBS",
    "AGENT_INTERNAL_ROOT_BASENAMES",
    "AGENT_INTERNAL_TOP_LEVEL_BASENAMES",
]
