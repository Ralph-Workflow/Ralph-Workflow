"""CcsAliasConfig model — per-alias CCS configuration."""

from __future__ import annotations

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel


class CcsAliasConfig(RalphBaseModel):
    """Per-alias CCS configuration (table form)."""

    model_config = ConfigDict(frozen=True)

    cmd: str
    output_flag: str | None = None
    yolo_flag: str | None = None
    verbose_flag: str | None = None
    print_flag: str | None = None
    streaming_flag: str | None = None
    json_parser: str | None = None
    can_commit: bool | None = None
    model_flag: str | None = None
    session_flag: str | None = None


__all__ = ["CcsAliasConfig"]
