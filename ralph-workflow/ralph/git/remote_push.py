from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import branch_sha
from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from pathlib import Path


PUSH_TIMEOUT_SECONDS = 30.0

REF_QUERY_TIMEOUT_SECONDS = 10.0

UP_TO_DATE_DETAIL = "remote ref already equals the local branch tip"


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
    status: PushStatus
    remote: str
    branch: str
    detail: str = ""

    @property
    def success(self) -> bool:
        return self.status in {PushStatus.PUSHED, PushStatus.CREATED}

    @property
    def summary(self) -> str:
        if self.status is PushStatus.PUSHED:
            if self.detail == UP_TO_DATE_DETAIL:
                return f"{self.branch} already published to {self.remote}"
            return f"pushed {self.branch} to {self.remote}"
        if self.status is PushStatus.CREATED:
            return f"created {self.remote}/{self.branch}"
        if self.status is PushStatus.MISSING_REMOTE:
            return f"remote '{self.remote}' not configured"
        return f"push of {self.branch} to {self.remote} failed: {self.detail or self.status.value}"


def _list_remotes(repo_root: Path) -> list[str]:
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
    text = detail.lower()
    if "timed out" in text or "timeout" in text:
        return PushStatus.TIMEOUT
    if any(
        token in text
        for token in (
            "permission denied",
            "authentication",
            "publickey",
            "terminal prompts disabled",
        )
    ):
        return PushStatus.AUTH_FAILED
    if "non-fast-forward" in text or "fetch first" in text:
        return PushStatus.NON_FAST_FORWARD
    if "hook declined" in text or "pre-receive hook declined" in text or "hook rejected" in text:
        return PushStatus.HOOK_REJECTED
    return PushStatus.UNREACHABLE


def _remote_ref_sha(repo_root: Path, remote: str, branch: str) -> str | None:
    try:
        result = run_git(
            ("ls-remote", "--", remote, f"refs/heads/{branch}"),
            cwd=repo_root,
            label=f"git-ls-remote-{remote}",
            options=GitRunOptions(timeout=REF_QUERY_TIMEOUT_SECONDS),
        )
    except Exception as exc:
        logger.debug("auto_integrate_push: `git ls-remote` could not be launched: {}", exc)
        return None
    if result.returncode != 0:
        return None
    wanted = f"refs/heads/{branch}"
    for raw in result.stdout.splitlines():
        sha, _tab, ref = raw.partition("\t")
        if ref.strip() == wanted:
            return sha.strip() or None
    return None


def _publishable_locally(repo_root: Path, remote: str, branch: str) -> bool:
    local_sha = branch_sha(repo_root, branch)
    if local_sha is None:
        return False
    return local_sha == _remote_ref_sha(repo_root, remote, branch)


def _push_to_remote(
    repo_root: Path, remote: str, branch: str, *, timeout_seconds: float = PUSH_TIMEOUT_SECONDS
) -> tuple[bool, str]:
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
    if not isinstance(remote, str) or not remote.strip() or remote not in _list_remotes(repo_root):
        return PushResult(
            PushStatus.MISSING_REMOTE, remote if isinstance(remote, str) else "", branch
        )
    if _publishable_locally(repo_root, remote, branch):
        logger.debug(
            "auto_integrate_push: '{}' already published to '{}'; push skipped", branch, remote
        )
        return PushResult(PushStatus.PUSHED, remote, branch, UP_TO_DATE_DETAIL)
    ok, detail = _push_to_remote(repo_root, remote, branch, timeout_seconds=timeout_seconds)
    if ok:
        status = PushStatus.CREATED if "[new branch]" in detail.lower() else PushStatus.PUSHED
        return PushResult(status, remote, branch, detail)
    detail = detail or "push failed"
    logger.warning("auto_integrate_push: push of '{}' to '{}' failed: {}", branch, remote, detail)
    return PushResult(_classify_failure(detail), remote, branch, detail)


__all__ = [
    "PUSH_TIMEOUT_SECONDS",
    "REF_QUERY_TIMEOUT_SECONDS",
    "UP_TO_DATE_DETAIL",
    "PushResult",
    "PushStatus",
    "push_branch_to_single_remote",
]
