"""Config-aware wrapper around the auto-integrate origin refresh.

Separated from :mod:`ralph.pipeline.auto_integrate` so that module stays
under the repo-structure ``_MAX_FILE_LINES`` cap. This is the single
place that maps configuration (``auto_integrate_fetch_enabled`` and
``auto_integrate_fetch_timeout_seconds``) onto
:func:`ralph.pipeline.auto_integrate_sync.refresh_target_from_remote`
and guarantees the fail-open contract: an unreachable remote must
degrade to local-only integration, never fail the run.

The mainline pointer is assumed to be moving continuously -- other
agents land on it while this one is rebasing, merging and (possibly)
resolving conflicts -- so the integration calls this at BOTH ends of an
attempt: once when the integration context is resolved, and again
immediately before the fast-forward observes the target SHA.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline.auto_integrate_sync import (
    REFRESH_DISABLED,
    REFRESH_LOCAL_FLEET,
    REFRESH_UNREACHABLE,
    observe_target_sha,
    refresh_target_from_remote,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig

#: Fallback fetch budget when the config does not carry the key
#: (partially-constructed configs in tests).
_DEFAULT_FETCH_TIMEOUT_SECONDS = 10.0

__all__ = ["refresh_target"]


def refresh_target(config: UnifiedConfig, root: Path, target: str) -> str:
    """Pull the freshest mainline pointer from origin before integrating.

    Returns the ``REFRESH_*`` outcome so the caller can record it on
    :class:`ralph.pipeline.rebase_state.RebaseState` and the operator can
    see whether the pointer just landed against was fresh. When
    fetching is turned off only the NETWORK fetch is skipped: the local
    target ref is still re-observed, so a fleet sharing one git common
    directory still sees a sibling-advanced pointer and the outcome is
    :data:`~ralph.pipeline.auto_integrate_sync.REFRESH_LOCAL_FLEET`.
    :data:`~ralph.pipeline.auto_integrate_sync.REFRESH_DISABLED`
    survives only for the case where no such local branch exists to
    observe either. Never raises: an unreachable remote
    must degrade to local-only integration, not fail the run, so an
    exception is reported as
    :data:`~ralph.pipeline.auto_integrate_sync.REFRESH_UNREACHABLE`
    rather than propagated.
    """
    enabled: object = getattr(config.general, "auto_integrate_fetch_enabled", True)
    if not enabled:
        return _observe_locally(root, target)
    timeout: object = getattr(
        config.general,
        "auto_integrate_fetch_timeout_seconds",
        _DEFAULT_FETCH_TIMEOUT_SECONDS,
    )
    seconds = (
        timeout
        if isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
        else _DEFAULT_FETCH_TIMEOUT_SECONDS
    )
    try:
        return refresh_target_from_remote(root, target, timeout_seconds=float(seconds))
    except Exception as exc:
        # Fail open -- but loudly. A permanently unreachable origin used
        # to degrade to local-only integration with no operator-visible
        # trace naming the branch involved, so a fleet that had been
        # integrating against a stale pointer for hours looked healthy.
        logger.warning(
            "auto_integrate: target refresh of '{}' failed: {}", target, exc
        )
        return REFRESH_UNREACHABLE


def _observe_locally(root: Path, target: str) -> str:
    """Report freshness for a run that has the origin fetch turned off.

    ``auto_integrate_fetch_enabled`` governs NETWORK access and nothing
    else. Conflating it with pointer freshness meant an operator who
    disabled fetching -- the natural setting for a fleet with no remote
    at all -- also silently disabled the re-read of the very ref sibling
    agents advance, which is a local, free, always-available operation.

    So the local observation still happens; only the fetch is skipped.
    :data:`~ralph.pipeline.auto_integrate_sync.REFRESH_DISABLED` survives
    for the case where there is no such local branch to observe either.
    """
    return (
        REFRESH_LOCAL_FLEET
        if observe_target_sha(root, target) is not None
        else REFRESH_DISABLED
    )
