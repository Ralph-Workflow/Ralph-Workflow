"""Fail-open, non-force push of one target branch to one configured remote."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from pathlib import Path


#: Wall-clock ceiling for each remote push attempt.
PUSH_TIMEOUT_SECONDS = 30.0


PushStatus = StrEnum(
    "PushStatus",
    {
        "PUSHED": "pushed",
        "CREATED": "created",
        "NON_FAST_FORWARD": "non_fast_forward",
        "TIMEOUT": "timeout",
        "AUTH_FAILED": "auth_failed",
        "HOOK_REJECTED": "hook_rejected",
        "UNREACHABLE": "unreachable",
        "MISSING_REMOTE": "missing_remote",
    },
)


@dataclass(frozen=True, slots=True)
class PushResult:
    """Structured, bounded result of an opt-in remote publication attempt."""

    status: PushStatus
    remote: str
    branch: str
    detail: str = ""

    @property
    def success(self) -> bool:
        """Whether the remote now has the requested target branch."""
        return self.status in {PushStatus.PUSHED, PushStatus.CREATED}

    @property
    def summary(self) -> str:
        """Human-facing compatibility text derived from facts, never parsed."""
        if self.status is PushStatus.PUSHED:
            return f"pushed {self.branch} to {self.remote}"
        if self.status is PushStatus.CREATED:
            return f"created {self.remote}/{self.branch}"
        if self.status is PushStatus.MISSING_REMOTE:
            return f"remote '{self.remote}' not configured"
        return f"push of {self.branch} to {self.remote} failed: {self.detail or self.status.value}"


def _list_remotes(repo_root: Path) -> list[str]:
    """Return configured remote names, treating query failure as no remote."""
    try:
        result = run_git(("remote",), cwd=repo_root, label="git-list-remotes")
    except (OSError, FileNotFoundError) as exc:
        logger.debug("auto_integrate_push: `git remote` could not be launched: {}", exc)
        return []
    if result.returncode != 0:
        return []
    names: list[str] = []
    for raw in result.stdout.splitlines():
        name = raw.strip()
        if name and name not in names:
            names.append(name)
    return names


def _classify_failure(detail: str) -> PushStatus:
    """Classify Git's bounded failure output once at the subprocess boundary."""
    text = detail.lower()
    if "timed out" in text or "timeout" in text:
        return PushStatus.TIMEOUT
    if any(token in text for token in ("permission denied", "authentication", "publickey", "terminal prompts disabled")):
        return PushStatus.AUTH_FAILED
    if "non-fast-forward" in text or "fetch first" in text:
        return PushStatus.NON_FAST_FORWARD
    if "hook declined" in text or "pre-receive hook declined" in text or "hook rejected" in text:
        return PushStatus.HOOK_REJECTED
    return PushStatus.UNREACHABLE


def _push_to_remote(
    repo_root: Path, remote: str, branch: str, *, timeout_seconds: float = PUSH_TIMEOUT_SECONDS
) -> tuple[bool, str]:
    """Run the one permitted refspec; retain this narrow subprocess seam for tests."""
    try:
        result = run_git(
            ("push", "--", remote, f"refs/heads/{branch}:refs/heads/{branch}"),
            cwd=repo_root,
            label=f"git-push-to-{remote}",
            options=GitRunOptions(timeout=timeout_seconds),
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return result.returncode == 0, " ".join((result.stderr or result.stdout or "").splitlines())


def push_branch_to_single_remote(
    repo_root: Path,
    branch: str,
    *,
    remote: str,
    timeout_seconds: float = PUSH_TIMEOUT_SECONDS,
) -> PushResult:
    """Push exactly ``refs/heads/<branch>`` to one remote without raising."""
    if not isinstance(remote, str) or not remote.strip() or remote not in _list_remotes(repo_root):
        return PushResult(PushStatus.MISSING_REMOTE, remote if isinstance(remote, str) else "", branch)
    ok, detail = _push_to_remote(repo_root, remote, branch, timeout_seconds=timeout_seconds)
    if ok:
        status = PushStatus.CREATED if "[new branch]" in detail.lower() else PushStatus.PUSHED
        return PushResult(status, remote, branch, detail)
    detail = detail or "push failed"
    logger.warning("auto_integrate_push: push of '{}' to '{}' failed: {}", branch, remote, detail)
    return PushResult(_classify_failure(detail), remote, branch, detail)


__all__ = ["PUSH_TIMEOUT_SECONDS", "PushResult", "PushStatus", "push_branch_to_single_remote"]
