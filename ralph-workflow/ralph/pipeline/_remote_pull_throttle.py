"""Rate-limiting for periodic pull keyed per (root, remote, target)."""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import UnifiedConfig

_MAX_REMOTE_KEYS = 128
_REMOTE_SYNC_INTERVAL_SECONDS = 300.0


class _RemotePullThrottle:
    """Rate-limits the periodic pull keyed per (root, remote, target)."""

    def __init__(self, *, max_tracked_keys: int = _MAX_REMOTE_KEYS) -> None:
        self._max_tracked_keys = max(1, max_tracked_keys)
        self._last_pull: OrderedDict[tuple[str, str, str], float] = (
            OrderedDict()
        )  # bounded-accumulator-ok: _MAX_REMOTE_KEYS FIFO cap applied in arm()

    def should_pull(
        self,
        root: Path | str,
        remote: str,
        target: str,
        *,
        interval_seconds: float,
        clock: Callable[[], float],
    ) -> bool:
        """Return True if enough time has elapsed since the last pull."""
        key = self._key(root, remote, target)
        last = self._last_pull.get(key)
        if last is None:
            return True
        if interval_seconds <= 0.0:
            return True
        return clock() - last >= interval_seconds

    def arm(self, root: Path | str, remote: str, target: str, clock: Callable[[], float]) -> None:
        """Record the current timestamp for (root, remote, target)."""
        key = self._key(root, remote, target)
        self._last_pull.pop(key, None)
        while len(self._last_pull) >= self._max_tracked_keys:
            self._last_pull.popitem(last=False)
        self._last_pull[key] = clock()

    def invalidate(self, root: Path | str, remote: str, target: str) -> None:
        """Evict timestamp for (root, remote, target) on failure."""
        self._last_pull.pop(self._key(root, remote, target), None)

    @staticmethod
    def _key(root: Path | str, remote: str, target: str) -> tuple[str, str, str]:
        return (str(root), remote, target)


_REMOTE_PULL_THROTTLE = _RemotePullThrottle()


def throttle_allows_pull(
    repo_root: Path,
    remote: str,
    target: str,
    config: UnifiedConfig | None,
    clock: Callable[[], float],
) -> bool:
    """Check if the periodic pull throttle allows a pull for target on remote."""
    from ralph.pipeline import auto_integrate_remote_sync as sync_mod

    general: object = getattr(config, "general", config)
    configured: object = getattr(general, "auto_integrate_remote_interval_seconds", 0.0)
    interval = configured if isinstance(configured, (int, float)) else _REMOTE_SYNC_INTERVAL_SECONDS
    return sync_mod._REMOTE_PULL_THROTTLE.should_pull(
        repo_root,
        remote,
        target,
        interval_seconds=interval,
        clock=clock,
    )


def arm_throttle(
    repo_root: Path,
    remote: str,
    target: str,
    clock: Callable[[], float],
) -> None:
    """Arm the throttle for target on remote with current clock timestamp."""
    from ralph.pipeline import auto_integrate_remote_sync as sync_mod

    sync_mod._REMOTE_PULL_THROTTLE.arm(repo_root, remote, target, clock)


def consume_throttle_signal_unhealthy(repo_root: Path, remote: str, target: str) -> None:
    """Invalidate the throttle so a failed fetch does NOT arm the next window."""
    from ralph.pipeline import auto_integrate_remote_sync as sync_mod

    sync_mod._REMOTE_PULL_THROTTLE.invalidate(repo_root, remote, target)
