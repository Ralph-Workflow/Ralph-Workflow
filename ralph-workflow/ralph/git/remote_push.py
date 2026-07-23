"""Fail-open, best-effort push of one local branch to EVERY configured remote.

The auto-integration landing (``ralph.pipeline.auto_integrate``) advances
the local ``refs/heads/<target>`` ref through a strictly local
rebase/merge/fast-forward sequence; remote state never affects that
landing, and a remote failure never affects it either. This module is
the ONLY place that ever pushes the shared mainline to a remote, and it
does so on OPT-IN only (see
:attr:`ralph.config.general_config.GeneralConfig.auto_integrate_push_enabled`).

Why a dedicated helper rather than reusing
:func:`ralph.git.operations.push`?

* ``operations.push`` is a single-remote helper; the auto-integrate
  push must enumerate remotes, push to each, and aggregate outcomes.
* The auto-integrate contract is **fail-open and best-effort**:

  - No exception escapes. Each remote is wrapped in its own
    try/except; a failure (timeout, unreachable host, non-fast-forward
    ref) is recorded, the next remote is tried, and the caller gets a
    summary it can render to the operator.
  - The push never force-pushes. Only the
    ``refs/heads/<branch>:refs/heads/<branch>`` refspec is used, so
    :program:`git push` rejects any push that would require replacing
    remote history. A divergent remote is left alone and the local
    landing stays valid.
  - ``run_git`` already forces
    ``GIT_TERMINAL_PROMPT=0``/``GCM_INTERACTIVE=Never`` so a missing
    credential fails fast instead of hanging the agent (the real
    agent-hang vector a network push opens).
  - The push runs ONLY after the local landing already succeeded;
    a remote failure cannot undo a local fast-forward.

The helper is the SINGLE source of the auto-integrate push path.
``auto_integrate._integrate_once`` calls it from the single successful
landing site, the renderer routes the summary through
:func:`ralph.display.auto_integrate_message.format_auto_integrate_message`,
and the timeout comes from
:attr:`ralph.config.general_config.GeneralConfig.auto_integrate_push_timeout_seconds`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def _list_remotes(repo_root: Path) -> list[str]:
    """Enumerate configured remotes by running ``git remote`` locally.

    Uses :func:`ralph.git.subprocess_runner.run_git` (not GitPython) so
    the read goes through the same non-interactive env every other
    auto-integrate git call uses, and so a corrupt repository does not
    surface as a GitPython exception. Remote names are returned in
    output order; duplicates are de-duplicated while preserving first
    occurrence (an operator adding the same remote twice should not get
    a duplicate push that errors on the second iteration).

    The call is wrapped in a defensive try/except because
    ``run_git`` is fail-fast on a missing cwd (the spawn raises
    ``FileNotFoundError``). The auto-integrate push is opt-in and
    fail-open, so a repo-root that has been removed or moved
    between the local landing and the push hook must not propagate
    that failure; the helper returns ``[]`` and the caller's
    ``no remotes configured`` summary covers it.
    """
    try:
        result = run_git(
            ("remote",),
            cwd=repo_root,
            label="git-list-remotes",
        )
    except (OSError, FileNotFoundError) as exc:
        logger.debug(
            "auto_integrate_push: `git remote` could not be launched: {}",
            exc,
        )
        return []
    if result.returncode != 0:
        logger.debug(
            "auto_integrate_push: `git remote` failed: {}",
            (result.stderr or "").strip(),
        )
        return []
    seen: set[str] = set()
    names: list[str] = []
    for raw in result.stdout.splitlines():
        name = raw.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _push_to_remote(
    repo_root: Path,
    remote: str,
    branch: str,
    *,
    timeout_seconds: float,
) -> tuple[bool, str]:
    """Push ``refs/heads/<branch>`` to one remote; never raises.

    Returns ``(ok, detail)`` where ``detail`` is the trimmed stderr
    on failure (or an empty string on success). The refspec
    ``refs/heads/<branch>:refs/heads/<branch>`` is explicit so the
    push can never accidentally push a different ref or, on a tag
    collision, create a remote tag the operator did not ask for.
    """
    args: Sequence[str] = (
        "push",
        "--",
        remote,
        f"refs/heads/{branch}:refs/heads/{branch}",
    )
    try:
        result = run_git(
            args,
            cwd=repo_root,
            label=f"git-push-to-{remote}",
            options=GitRunOptions(timeout=timeout_seconds),
        )
    except Exception as exc:
        # ``run_git`` only raises on timeout or on a process-management
        # failure; both must be reported as "this remote failed" so the
        # caller's per-remote try/except aggregates them with the
        # non-zero-returncode cases.
        return False, f"{type(exc).__name__}: {exc}"
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "").strip()
    return True, ""


def push_branch_to_all_remotes(
    repo_root: Path,
    branch: str,
    *,
    timeout_seconds: float,
) -> str:
    """Push ``branch`` to EVERY configured remote; never raises.

    Returns a short human-readable summary suitable for direct
    rendering in the ``auto-integrate:`` line. The four outcomes:

    * **No remotes configured** -- returns ``"no remotes configured"``
      without contacting the network. The push hook is opt-in and the
      common local-fleet configuration has no remote at all; the
      summary acknowledges that cleanly so the operator can tell "I
      have no remotes" from "all of my remotes are down".
    * **All remotes succeeded** -- returns
      ``"pushed <branch> to N/N remotes"``.
    * **Some remotes failed** -- returns
      ``"pushed <branch> to K/N remotes (<failed> failed)"`` so a
      partial push is operator-visible.
    * **All remotes failed** -- returns
      ``"pushed <branch> to 0/N remotes"``.

    The summary is the ONLY signal the auto-integrate line carries
    about remote state, so a partial push must read as a partial
    push, not as a no-op and not as a failure of the run.
    """
    remotes = _list_remotes(repo_root)
    if not remotes:
        return "no remotes configured"

    successes: list[str] = []
    failures: list[str] = []
    for remote in remotes:
        ok, detail = _push_to_remote(
            repo_root, remote, branch, timeout_seconds=timeout_seconds
        )
        if ok:
            successes.append(remote)
            continue
        one_line = " ".join(detail.splitlines()) or "push failed"
        logger.warning(
            "auto_integrate_push: push of '{}' to '{}' failed: {}",
            branch,
            remote,
            one_line,
        )
        failures.append(remote)

    total = len(remotes)
    succeeded = len(successes)
    if not failures:
        return f"pushed {branch} to {succeeded}/{total} remotes"
    if succeeded == 0:
        return f"pushed {branch} to 0/{total} remotes"
    failed_label = ",".join(failures)
    return f"pushed {branch} to {succeeded}/{total} remotes ({failed_label} failed)"


__all__ = ["push_branch_to_all_remotes"]
