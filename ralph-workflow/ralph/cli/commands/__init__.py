"""Ralph CLI commands package.

Re-exports the top-level entry points for each CLI sub-command so callers can
import them from ``ralph.cli.commands`` without knowing the submodule layout.
The main CLI wiring lives in ``ralph.cli.main``; each sub-command is
implemented in its own submodule under this package.

Public exports:

- ``commit_plumbing`` - drives ``ralph --generate-commit``
- ``diagnose_command`` - drives ``ralph diagnose``
- ``init_command`` - drives ``ralph init``
- ``run_pipeline`` - drives ``ralph run`` (the primary workflow entry point)
- ``smoke_interactive_claude_command`` - drives the manual PTY parity smoke test
"""

from __future__ import annotations

__all__ = [
    "commit_plumbing",
    "diagnose_command",
    "init_command",
    "run_pipeline",
    "smoke_interactive_claude_command",
]


def __getattr__(name: str) -> object:
    if name == "commit_plumbing":
        from ralph.cli.commands.commit import commit_plumbing as _commit_plumbing

        return _commit_plumbing
    if name == "diagnose_command":
        from ralph.cli.commands.diagnose import diagnose_command as _diagnose_command

        return _diagnose_command
    if name == "init_command":
        from ralph.cli.commands.init import init_command as _init_command

        return _init_command
    if name == "run_pipeline":
        from ralph.cli.commands.run import run_pipeline as _run_pipeline

        return _run_pipeline
    if name == "smoke_interactive_claude_command":
        from ralph.cli.commands.smoke import (
            smoke_interactive_claude_command as _smoke_interactive_claude_command,
        )

        return _smoke_interactive_claude_command
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
