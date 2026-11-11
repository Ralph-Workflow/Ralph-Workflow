
from __future__ import annotations

from pydantic import ConfigDict

from ralph.config._ccs_alias_config import CcsAliasConfig
from ralph.pydantic_compat import RalphBaseModel


class CcsConfig(RalphBaseModel):

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_flag: str = "--output-format=stream-json"
    yolo_flag: str = "--permission-mode bypassPermissions"
    verbose_flag: str = "--verbose"
    print_flag: str = "--print"
    streaming_flag: str = "--include-partial-messages"
    json_parser: str = "claude"
    session_flag: str = "--resume {}"


__all__ = ["CcsAliasConfig", "CcsConfig"]
