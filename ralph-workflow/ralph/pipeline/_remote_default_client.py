"""Default remote client delegating to live Git operations.

The client is the PRODUCTION implementation of the remote tier: it
implements the :class:`~ralph.pipeline.integration_remote_actions.RemoteOps`
protocol the integration pipeline injects, so the same adapter serves
the runtime and any test that substitutes a fault-injecting double.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git import remote_push as _remote_push_module
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_UNREACHABLE,
)
from ralph.pipeline.integration_remote_actions import RemoteOps

if TYPE_CHECKING:
    from pathlib import Path


def _remote_tracking_sha(repo_root: Path, target: str, remote: str) -> str | None:
    """Return ``refs/remotes/<remote>/<target>`` SHA, or ``None`` when absent."""
    result = run_git(
        ("rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{target}"),
        cwd=repo_root,
        label=f"git-remote-tracking-sha-pull-{remote}",
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


class _DefaultRemoteClient(RemoteOps):
    """Default production implementation delegating to live Git operations.

    Implements the :class:`~ralph.pipeline.integration_remote_actions.RemoteOps`
    protocol explicitly: the client is ONE implementation of the remote
    tier, and the protocol is the narrow seam the integration pipeline
    injects. Every failure stays fail-open -- an unreadable remote is
    reported as ``REFRESH_UNREACHABLE`` / an ``UNREACHABLE`` push
    result, never as a proven negative.
    """

    def refresh_target(
        self,
        repo_root: Path,
        target: str,
        *,
        timeout_seconds: float,
        remote: str,
    ) -> str:
        from ralph.pipeline import auto_integrate_remote_sync as sync_mod

        try:
            return sync_mod.refresh_target_from_remote(
                repo_root,
                target,
                timeout_seconds=timeout_seconds,
                remote=remote,
            )
        except Exception as exc:
            logger.debug("auto_integrate: remote refresh query failed: {}", exc)
            return REFRESH_UNREACHABLE

    def push_branch(
        self,
        repo_root: Path,
        branch: str,
        *,
        remote: str,
        timeout_seconds: float,
    ) -> _remote_push_module.PushResult:
        try:
            return _remote_push_module.push_branch_to_single_remote(
                repo_root,
                branch,
                remote=remote,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            logger.debug("auto_integrate: remote push failed: {}", exc)
            return _remote_push_module.PushResult(
                _remote_push_module.PushStatus.UNREACHABLE,
                remote,
                branch,
                detail=str(exc),
            )

    def observe_remote_sha(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
    ) -> str | None:
        return _remote_tracking_sha(repo_root, target, remote)


_DEFAULT_REMOTE_CLIENT = _DefaultRemoteClient()
