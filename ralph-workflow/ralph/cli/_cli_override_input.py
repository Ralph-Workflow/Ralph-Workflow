from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CLIOverrideInput:
    """Input for building CLI overrides."""

    developer_agent: str | None = None
    developer_model: str | None = None
    git_user_name: str | None = None
    git_user_email: str | None = None
    developer_iters: int | None = None
    unsafe_mode: bool | None = None
