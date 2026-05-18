"""CCS configuration model definitions."""

from __future__ import annotations

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel


class CcsAliasConfig(RalphBaseModel):
    """Per-alias CCS configuration (table form)."""

    class CcsConfig(RalphBaseModel):
        """Headless-by-design Claude Code Switch (CCS) defaults.

        CCS aliases explicitly run Claude in non-interactive streaming mode
        (``--print --output-format=stream-json``). That is the intended explicit
        headless Claude path for users who configure ``[ccs_aliases]``. The built-in
        ``claude`` agent runs in interactive mode by default.
        """

        model_config = ConfigDict(frozen=True)

        output_flag: str = "--output-format=stream-json"
        yolo_flag: str = "--permission-mode auto"
        verbose_flag: str = "--verbose"
        print_flag: str = "--print"
        streaming_flag: str = "--include-partial-messages"
        json_parser: str = "claude"
        session_flag: str = "--resume {}"
        can_commit: bool = True


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


CcsConfig = CcsAliasConfig.CcsConfig


__all__ = ["CcsAliasConfig", "CcsConfig"]
