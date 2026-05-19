"""CCS configuration model definitions."""

from __future__ import annotations

from pydantic import ConfigDict

from ralph.config._ccs_alias_config import CcsAliasConfig
from ralph.pydantic_compat import RalphBaseModel


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


__all__ = ["CcsAliasConfig", "CcsConfig"]
