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
    REFRESH_UNREACHABLE,
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
    see whether the pointer just landed against was fresh. A no-op
    returning :data:`~ralph.pipeline.auto_integrate_sync.REFRESH_DISABLED`
    when fetching is turned off. Never raises: an unreachable remote
    must degrade to local-only integration, not fail the run, so an
    exception is reported as
    :data:`~ralph.pipeline.auto_integrate_sync.REFRESH_UNREACHABLE`
    rather than propagated.
    """
    enabled: object = getattr(config.general, "auto_integrate_fetch_enabled", True)
    if not enabled:
        return REFRESH_DISABLED
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
        logger.warning("auto_integrate: target refresh failed: {}", exc)
        return REFRESH_UNREACHABLE
