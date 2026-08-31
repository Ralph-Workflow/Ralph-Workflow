"""Native OpenCode subagent evidence read from OpenCode's own session store.

``opencode run --format json`` (1.18.x) emits ``step_start`` when the parent
turn dispatches a native ``task`` subagent and then NOTHING until that call
completes -- the whole ``tool_use`` frame is buffered until the child is done.
A silent parent therefore carries no first-party evidence for the entire
subagent run, and the idle watchdog killed healthy runs at the 240 s
``NO_PROGRESS_QUIET`` ceiling (no OS descendants) or the 600 s
``CHILDREN_PERSIST_TOO_LONG`` ceiling (child shelling out), while native
subagents measured against an operator's store routinely ran 10-15 minutes.

OpenCode does persist every child session as it works. Its SQLite store
(``$XDG_DATA_HOME/opencode/opencode.db``, default ``~/.local/share``) carries
one ``session`` row per subagent with ``parent_id`` set to the dispatching
session, and upserts one ``part`` row per tool call, reasoning block, or
text block with an advancing ``time_updated``. Reading that store read-only
turns each newly updated part into demonstrated child work. This is the
OpenCode counterpart of the Claude subagent transcript tailer
(``~/.claude/projects/<key>/<session>/subagents/*.jsonl``) with a deliberately
stronger signal: besides the watchdog's subagent channel, each update resets
the idle baseline like a parent output line and keeps a ``running`` child in
the liveness registry, because OpenCode's stream is otherwise silent for the
entire native task.

The probe is deliberately observation-only and fail-quiet: a missing store,
a locked page, or an unexpected row shape yields no evidence rather than a
false liveness signal, so a genuinely wedged child still reaches the
existing ceilings.
"""

from __future__ import annotations

from ._child_part import OpenCodeChildPart, part_kind_from_data, summarize_child_part
from ._part_source import OpenCodeChildPartSource, default_opencode_db_path
from ._probe import OpenCodeSubagentSessionProbe
from ._sqlite_source import SqliteOpenCodeChildPartSource

__all__ = [
    "OpenCodeChildPart",
    "OpenCodeChildPartSource",
    "OpenCodeSubagentSessionProbe",
    "SqliteOpenCodeChildPartSource",
    "default_opencode_db_path",
    "part_kind_from_data",
    "summarize_child_part",
]
