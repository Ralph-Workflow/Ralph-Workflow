from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.git.remote_push import PushResult


__all__ = [
    "RemoteOps",
    "default_remote_ops",
]


@runtime_checkable
class RemoteOps(Protocol):
    def refresh_target(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
        timeout_seconds: float,
    ) -> str: ...

    def push_branch(
        self,
        repo_root: Path,
        branch: str,
        *,
        remote: str,
        timeout_seconds: float,
    ) -> PushResult: ...

    def observe_remote_sha(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
    ) -> str | None: ...


def default_remote_ops() -> RemoteOps:
    from ralph.pipeline._remote_default_client import _DEFAULT_REMOTE_CLIENT

    return _DEFAULT_REMOTE_CLIENT
