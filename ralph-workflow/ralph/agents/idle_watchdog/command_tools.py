from __future__ import annotations

import re
from typing import Final

_COMMAND_TOOL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[._])(?:batch_)?exec(?:ute)?(?:_file)?$"
)


def runs_a_command(tool_name: str) -> bool:
    return bool(tool_name) and _COMMAND_TOOL_RE.search(tool_name.lower()) is not None


__all__ = ["runs_a_command"]
