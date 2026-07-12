"""ExecParams dataclass for exec tool parameter parsing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecParams:
    """Parsed parameters for the MCP exec tool.

    ``command`` / ``args`` are the argv of the (first) command. When the caller
    passed a compound shell string (an unquoted ``| & ; < >`` operator), the raw
    string is preserved in ``shell_command`` so the handler runs it through
    ``sh -c`` after the blacklist has been enforced against every command in the
    pipeline; ``command`` / ``args`` then hold the FIRST pipeline segment (for a
    readable result header). ``shell_command`` is ``None`` for a plain,
    single-command invocation, which runs argv-direct with no shell.
    """

    command: str
    args: list[str]
    timeout_ms: int
    shell_command: str | None = None
