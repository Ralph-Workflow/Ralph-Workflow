"""Resolved environment settings for display configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _ResolvedEnv:
    """Resolved environment settings for display configuration.

    Attributes:
        no_color: True when NO_COLOR is present in environment.
        force_color: True when FORCE_COLOR is present in environment.
        columns: Terminal width override from COLUMNS env, or None.
        force_ascii: True when RALPH_FORCE_ASCII is set to a truthy value.
        streaming_dedup_enabled: True when RALPH_STREAMING_DEDUP is not disabled.
        streaming_checkpoints_enabled: True when RALPH_STREAMING_CHECKPOINTS is not disabled.
    """

    no_color: bool
    force_color: bool
    columns: int | None
    force_ascii: bool
    streaming_dedup_enabled: bool
    streaming_checkpoints_enabled: bool
