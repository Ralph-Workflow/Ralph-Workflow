"""Injectable dependencies and type aliases for exec tool command execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ralph.mcp.tools._exec_completed_process import _CompletedProcessAdapter
    from ralph.mcp.tools.coordination import CoordinationSessionLike
    from ralph.process.manager import ProcessManager

type CwdProvider = Callable[[], Path]
type CommandRunner = Callable[[list[str], Path, float | None], _CompletedProcessAdapter]
type OutputChunkCallback = Callable[[str], None]


@dataclass(frozen=True)
class ExecRunDeps:
    """Injectable dependencies for exec tool command execution."""

    runner: CommandRunner | None = None
    cwd_provider: CwdProvider | None = None
    process_manager: ProcessManager | None = None
    on_output_chunk: OutputChunkCallback | None = None
    #: Directory for oversized-output spill files. Defaults to the OS temp dir
    #: (``tempfile.gettempdir()``) so the OS reclaims them; injectable for tests.
    spill_dir: Path | None = None


@runtime_checkable
class _SessionWithStreaming(Protocol):
    """Subset of AgentSession that supports thread-owned tool output streaming."""

    def current_thread_tool_output_sink(
        self,
    ) -> Callable[[dict[str, object]], None] | None:
        """Return the active sink when the calling thread owns it."""
        ...


def build_effective_exec_deps(
    session: CoordinationSessionLike,
    deps: ExecRunDeps | None,
) -> ExecRunDeps | None:
    """Compose the session's thread-owned output sink into exec dependencies."""
    if not isinstance(session, _SessionWithStreaming):
        return deps
    sink = session.current_thread_tool_output_sink()
    if sink is None:
        return deps

    def session_chunk(chunk: str) -> None:
        sink({"tool": "exec", "stream": "combined", "text": chunk})

    if deps is None:
        return ExecRunDeps(on_output_chunk=session_chunk)
    existing_callback = deps.on_output_chunk
    if existing_callback is None:
        composed_callback: OutputChunkCallback = session_chunk
    else:

        def composed_callback(chunk: str) -> None:
            existing_callback(chunk)
            session_chunk(chunk)

    return ExecRunDeps(
        runner=deps.runner,
        cwd_provider=deps.cwd_provider,
        process_manager=deps.process_manager,
        on_output_chunk=composed_callback,
        spill_dir=deps.spill_dir,
    )
