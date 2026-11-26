from __future__ import annotations

import re
from typing import Final

from ralph.timeout_defaults import EXEC_MAX_TIMEOUT_MS

OPEN_COMMAND_GRACE_SECONDS: Final[float] = EXEC_MAX_TIMEOUT_MS / 1000.0

_COMMAND_TOOL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[._])(?:batch_)?exec(?:ute)?(?:_file)?$"
)


def runs_a_command(tool_name: str) -> bool:
    return bool(tool_name) and _COMMAND_TOOL_RE.search(tool_name.lower()) is not None


__all__ = ["OPEN_COMMAND_GRACE_SECONDS", "runs_a_command"]
