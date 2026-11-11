
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


RALPH_WORKFLOW_PRO = "RALPH_WORKFLOW_PRO"
RALPH_WORKSPACE = "RALPH_WORKSPACE"
PROMPT_PATH = "PROMPT_PATH"


def _get_env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def is_pro_mode(env: Mapping[str, str] | None = None) -> bool:
    return bool(_get_env(env).get(RALPH_WORKFLOW_PRO))


def get_ralph_workspace(env: Mapping[str, str] | None = None) -> str | None:
    value = _get_env(env).get(RALPH_WORKSPACE, "")
    if not value:
        return None
    return value


def get_prompt_path(env: Mapping[str, str] | None = None) -> str | None:
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
