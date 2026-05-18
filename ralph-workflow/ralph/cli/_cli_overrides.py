from __future__ import annotations

from typing import TypedDict

from ralph.cli._general_overrides import GeneralOverrides


class CLIOverrides(TypedDict):
    """CLI configuration overrides."""

    general: GeneralOverrides
    developer_agent: str | None
    developer_model: str | None
