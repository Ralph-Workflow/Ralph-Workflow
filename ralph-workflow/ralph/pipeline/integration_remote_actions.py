"""Remote-tier operation protocol for the integration pipeline.

Declares the narrow :class:`RemoteOps` protocol the integration
pipeline uses to observe and mutate ONE remote, plus the accessor for
the production adapter that binds that protocol to live Git operations.

The protocol IS the injection seam of the remote tier. A
fault-injecting double can therefore simulate an unreachable remote, a
non-fast-forward rejection, or a remote that advanced underneath the
caller -- none of which needs a network, a subprocess, or a clock.

The UNKNOWN rule is part of the contract. A remote outage is an
environmental reading, never a proven negative: an unreadable remote
is reported as :data:`~ralph.pipeline.auto_integrate_sync.REFRESH_UNREACHABLE`
and an unfinished push as an ``UNREACHABLE``/``TIMEOUT``
:class:`~ralph.git.remote_push.PushResult`. The caller therefore DEFERS
the publication obligation and re-observes later; it never blocks the
run terminally and never blames the remote for a local failure.

The module deliberately imports nothing from the pipeline at import
time: the production adapter is resolved lazily inside
:func:`default_remote_ops` so this module stays free of an import
cycle with the client module that implements it.
"""

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
    """Protocol for the remote tier of the integration pipeline.

    The three members are the ONLY remote entry points the integration
    pipeline needs: observe the remote's freshness, push one branch, and
    read the remote-tracking ref. Every member is fail-open -- an
    unreachable, unauthenticated, or unreadable remote is reported as
    the matching UNKNOWN outcome and never as a proven negative.
    """

    def refresh_target(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
        timeout_seconds: float,
    ) -> str:
        """Observe the remote's freshness for ``target``.

        Returns one of the ``REFRESH_*`` outcomes. An unreachable,
        timed-out, or otherwise unreadable remote MUST be reported as
        ``REFRESH_UNREACHABLE`` -- the UNKNOWN reading -- and never as a
        proven negative such as "the remote does not hold the target".

        A client that reports a THROTTLED probe (a "refresh suppressed"
        outcome) tells the caller that no fetch happened, so the
        remote-tracking ref behind the reading is stale. The caller
        then forces a real fetch to repair that cache; that forced
        fetch is the module-level Git primitive, NOT this protocol, so
        a client that must stay off the network reports a fresh
        outcome and never a throttled one.
        """
        ...

    def push_branch(
        self,
        repo_root: Path,
        branch: str,
        *,
        remote: str,
        timeout_seconds: float,
    ) -> PushResult:
        """Push ``branch`` to ``remote`` and report the typed outcome.

        Returns a :class:`~ralph.git.remote_push.PushResult`. An
        unreachable remote, an auth failure, and a timeout are reported
        as the matching status; a non-fast-forward rejection is
        reported as ``NON_FAST_FORWARD`` so the caller reconciles
        instead of giving up.
        """
        ...

    def observe_remote_sha(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
    ) -> str | None:
        """Read ``refs/remotes/<remote>/<target>`` without mutating anything.

        Returns the SHA, or ``None`` when the ref cannot be read.
        ``None`` means UNKNOWN: it never proves the remote matches the
        local ref, so the caller must not skip a publication because of
        it.
        """
        ...


def default_remote_ops() -> RemoteOps:
    """Return the production remote-ops adapter (live Git operations).

    The default implementation is
    :class:`ralph.pipeline._remote_default_client._DefaultRemoteClient`
    -- the same adapter the runtime uses when no remote-ops double is
    injected. The import is resolved lazily so this module never
    depends on the client module at import time.
    """
    from ralph.pipeline._remote_default_client import _DEFAULT_REMOTE_CLIENT

    return _DEFAULT_REMOTE_CLIENT
