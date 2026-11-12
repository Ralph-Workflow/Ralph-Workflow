from __future__ import annotations

from typing import TypedDict


class GeneralOverrides(TypedDict, total=False):
    git_user_name: str | None
    git_user_email: str | None
    execution: dict[str, bool]
    developer_iters: int
    workflow: dict[str, bool]
