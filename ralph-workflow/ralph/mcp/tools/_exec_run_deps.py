"""Injectable dependencies and type aliases for exec tool command execution."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.tools._exec_completed_process import _CompletedProcessAdapter
    from ralph.process.manager import ProcessManager

type CwdProvider = Callable[[], Path]
type CommandRunner = Callable[[list[str], Path, float | None], _CompletedProcessAdapter]
type OverlayFactory = Callable[[Path], AbstractContextManager[Path]]
type OutputChunkCallback = Callable[[str], None]


@dataclass(frozen=True)
class ExecRunDeps:
    """Injectable dependencies for exec tool command execution."""

    runner: CommandRunner | None = None
    cwd_provider: CwdProvider | None = None
    overlay_factory: OverlayFactory | None = None
    process_manager: ProcessManager | None = None
    on_output_chunk: OutputChunkCallback | None = None
