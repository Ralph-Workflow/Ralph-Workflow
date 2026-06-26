"""Synchronous git helper backed by ProcessManager."""

from __future__ import annotations

import contextlib
import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ralph.git.git_run_result import GitRunResult
from ralph.process.manager import SpawnOptions, get_process_manager
from ralph.process.manager._managed_process_output_limit_exceeded_error import (
    ManagedProcessOutputLimitExceededError,
)
from ralph.timeout_defaults import GIT_SUBPROCESS_TIMEOUT_SECONDS

#: Non-interactive git environment baseline. Ensures git never blocks on a
#: credential prompt, a pager, or an editor — so a missing credential or network
#: failure fails fast instead of hanging the process forever (a real agent-hang
#: vector for any network git op). Merged into the parent environment for every
#: ``run_git`` call; caller-supplied env still takes precedence.
_GIT_BATCH_MODE_ENV: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "Never",
    "GIT_EDITOR": ":",
    "GIT_SEQUENCE_EDITOR": ":",
    "GIT_PAGER": "cat",
}

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    class _PopenExitProtocol:
        def __exit__(
            self,
            exc_type: object,
            exc: object,
            tb: object,
        ) -> object: ...


@dataclass(frozen=True)
class GitRunOptions:
    """Options for run_git beyond the required args, cwd, and label.

    ``output_limit_bytes``: cap on stdout/stderr captured from the git
    subprocess. ``None`` (default) preserves the legacy unbounded
    behavior. The default when callers do pass a non-None value is the
    module-level ``GIT_OUTPUT_LIMIT_BYTES`` (10 MiB) — matching the
    existing ``SPILL_OUTPUT_LIMIT_BYTES`` precedent at
    ``ralph/mcp/tools/_exec_output_spill.py:33`` and well above any
    realistic single-file diff. Outputs exceeding the cap are truncated
    with a marker (the
    ``ManagedProcessOutputLimitExceededError`` semantics in
    ``_communicate_with_output_limit``).
    """

    phase: str | None = None
    timeout: float | None = None
    env: Mapping[str, str] | None = None
    check: bool = False
    capture_output: bool = True
    text: bool = True
    output_limit_bytes: int | None = None


def run_git(
    args: Sequence[str],
    *,
    cwd: Path | None,
    label: str,
    options: GitRunOptions | None = None,
) -> GitRunResult:
    """Spawn a git subprocess through ProcessManager and return the result.

    When ``options.phase`` is provided the process label becomes
    ``phase:<phase>:git:<label>`` so process_phase_scope can terminate it.

    Raises subprocess.TimeoutExpired if timeout is exceeded.
    Raises subprocess.CalledProcessError if options.check is True and returncode != 0.
    """
    effective_options = options or GitRunOptions()
    phase = effective_options.phase
    effective_label = f"phase:{phase}:git:{label}" if phase is not None else label
    cmd = ("git", *args)
    # Fail closed: bound every git call with a default timeout when the caller
    # gives none, and always run git non-interactively so it cannot hang on a
    # credential/editor/pager prompt.
    effective_timeout = (
        effective_options.timeout
        if effective_options.timeout is not None
        else GIT_SUBPROCESS_TIMEOUT_SECONDS
    )
    spawn_env = dict(os.environ)
    spawn_env.update(_GIT_BATCH_MODE_ENV)
    if effective_options.env is not None:
        spawn_env.update(effective_options.env)
    proc = get_process_manager().spawn(
        cmd,
        SpawnOptions(
            cwd=str(cwd) if cwd else None,
            env=spawn_env,
            stdout=subprocess.PIPE if effective_options.capture_output else None,
            stderr=subprocess.PIPE if effective_options.capture_output else None,
            label=effective_label,
            text=effective_options.text,
        ),
    )
    try:
        raw_stdout, raw_stderr = proc.communicate_and_cleanup(
            timeout=effective_timeout,
            cleanup_grace_period_s=0.0,
            # Bound the captured stdout/stderr when the caller opts in via
            # ``GitRunOptions.output_limit_bytes``. The default of ``None``
            # preserves the legacy unbounded path; the recommended cap is
            # ``GIT_OUTPUT_LIMIT_BYTES`` (10 MiB) in
            # ``ralph.timeout_defaults``. The bounded branch in
            # ``communicate_and_cleanup`` truncates at the cap with a
            # marker (the ``ManagedProcessOutputLimitExceededError``
            # semantics in ``_communicate_with_output_limit``).
            output_limit_bytes=effective_options.output_limit_bytes,
        )
        with contextlib.suppress(Exception):
            proc.poll()
        with contextlib.suppress(Exception):
            proc.wait(timeout=0)
    except subprocess.TimeoutExpired:
        proc.terminate(grace_period_s=0)
        raise
    except ManagedProcessOutputLimitExceededError:
        # Output-cap hit — kill the proc tree and propagate.
        proc.terminate(grace_period_s=0)
        raise
    finally:
        raw_proc_obj: object = proc._proc
        for stream in (proc.stdout, proc.stderr):
            if stream is None:
                continue
            with contextlib.suppress(Exception):
                stream.close()
        if hasattr(raw_proc_obj, "__exit__"):
            raw_proc = cast("_PopenExitProtocol", raw_proc_obj)
            with contextlib.suppress(Exception):
                raw_proc.__exit__(None, None, None)

    def _str(v: bytes | str | None) -> str:
        if v is None:
            return ""
        return v.decode() if isinstance(v, bytes) else v

    stdout = _str(raw_stdout)
    stderr = _str(raw_stderr)
    returncode = proc.returncode if proc.returncode is not None else 0

    if effective_options.check and returncode != 0:
        raise subprocess.CalledProcessError(returncode, list(cmd), stdout, stderr)

    return GitRunResult(args=cmd, returncode=returncode, stdout=stdout, stderr=stderr)


__all__ = ["GitRunOptions", "run_git"]
