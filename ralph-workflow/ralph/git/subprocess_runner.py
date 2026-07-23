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
#:
#: Per the auto-integration hardening spec (D1/D14), the baseline also pins
#: ``LC_ALL=C`` (so stderr substring classification is stable across locales),
#: ``VISUAL=`` / ``EDITOR=`` (some tools consult these directly rather than
#: through git's own ``GIT_EDITOR``), and ``GIT_NO_REPLACE_OBJECTS=1`` (H4:
#: produced history must never silently depend on ``refs/replace/*``). The
#: ``GIT_*`` family location variables (``GIT_DIR``, ``GIT_WORK_TREE``,
#: ``GIT_INDEX_FILE``, ``GIT_COMMON_DIR``) are scrubbed from inherited
#: environments inside :func:`_build_spawn_env` rather than from this dict;
#: a default-empty value would still win against the inherited one and
#: silently redirect a subprocess into the wrong repository.
_GIT_BATCH_MODE_ENV: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "Never",
    "GIT_EDITOR": ":",
    "GIT_SEQUENCE_EDITOR": ":",
    "GIT_PAGER": "cat",
    "EDITOR": ":",
    "VISUAL": ":",
    "LC_ALL": "C",
    "GIT_NO_REPLACE_OBJECTS": "1",
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


#: ``GIT_*`` family location variables scrubbed from every spawned
#: environment. A non-empty inherited ``GIT_DIR`` would silently
#: redirect a subprocess into a different repository than the one
#: the caller asked for; defaulting these to empty in
#: :data:`_GIT_BATCH_MODE_ENV` would still lose to a non-empty
#: inherited value. Removing them at every precedence level
#: (caller / baseline / inherited environ) is the only way the
#: scrub survives any caller.
_SCRUBBED_GIT_ENV_KEYS: frozenset[str] = frozenset(
    {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"}
)


def _build_spawn_env(
    caller_env: Mapping[str, str] | None,
) -> dict[str, str]:
    """Build the environment handed to the git subprocess.

    Order of precedence (highest first):

    1. The caller-supplied env (when provided through
       :class:`GitRunOptions`).
    2. The non-interactive batch baseline in
       :data:`_GIT_BATCH_MODE_ENV`.
    3. The inherited :data:`os.environ`.

    ``GIT_DIR``, ``GIT_WORK_TREE``, ``GIT_INDEX_FILE`` and
    ``GIT_COMMON_DIR`` are scrubbed at every precedence level: a
    fleet agent that inherited any of them from a parent process
    would silently redirect every later ``git`` call into a
    different repository (D13). Removing the inherited value BEFORE
    the batch baseline is applied is the only way a downstream
    caller cannot re-introduce it through
    :class:`GitRunOptions`.
    """
    merged: dict[str, str] = {}
    for source in (os.environ, _GIT_BATCH_MODE_ENV):
        for key, value in source.items():
            if key in _SCRUBBED_GIT_ENV_KEYS:
                continue
            merged[key] = value
    if caller_env is not None:
        for key, value in caller_env.items():
            if key in _SCRUBBED_GIT_ENV_KEYS:
                continue
            merged[key] = value
    return merged


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
    spawn_env = _build_spawn_env(effective_options.env)
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
