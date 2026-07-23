"""Default SubprocessExecutor powered by run_git.

The rebase engine (``rebase.py``) calls through this executor for
every git operation it makes: ``rebase --abort``, ``rebase --continue``,
``status``, ``merge-base``, etc. The executor must therefore inherit
the same per-invocation hardening every other auto-integration call
sees — the ``-c`` pins in
:data:`ralph.git.hardening.PINNED_CONFIG_ARGS` (``rerere.enabled=false``,
``rebase.backend=merge``, ``commit.gpgsign=false``,
``tag.gpgsign=false``, ``core.fsmonitor=false``) — so a hostile user
config (``rerere.autoUpdate`` carrying a wrong recorded resolution,
``commit.gpgsign`` set with no key, ``rebase.autoSquash``, ``rebase.updateRefs``,
an autostash-friendly tree, an interactive sequence editor) cannot
break, hang or silently mutate a rebase the agent is watching.

The public signature is unchanged. The new behavior is the
``subprocess_argv`` transformation applied right before the call to
:func:`run_git`, which already supplies the ``git`` itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git.hardening import PINNED_CONFIG_ARGS, scrub_git_env
from ralph.git.rebase.process_result import ProcessResult
from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


@dataclass(frozen=True)
class SubprocessExecutor:
    """Default executor powered by run_git, hardened per-invocation."""

    def execute(
        self,
        command: str,
        args: Sequence[str],
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> ProcessResult:
        subcommand = args[0] if args else "unknown"
        # Splice the per-invocation -c pins immediately after ``git``,
        # matching :func:`run_git`'s convention that the caller's
        # argv starts with the sub-command. The list-shaped form (vs.
        # tuple) lets us prepend without reallocating.
        pinned_args: list[str] = [*PINNED_CONFIG_ARGS, *args]
        result = run_git(
            pinned_args,
            cwd=cwd,
            label=f"git-rebase:{subcommand}",
            options=GitRunOptions(env=scrub_git_env(dict(env) if env else None)),
        )
        return ProcessResult(
            returncode=result.returncode,
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
        )
