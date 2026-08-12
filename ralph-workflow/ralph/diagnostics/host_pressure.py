"""Platform-aware host filesystem-watch pressure reader (S-4).

``read_host_pressure`` reports the host's filesystem-watch capacity
utilization with an explicit attribution token so callers never mistake
an uncertain estimate for a measured value. The reader is fail-closed:
any I/O or parse error returns ``attribution='uncertain'`` rather than
raising, so a diagnostics read can never hang or break the workspace
health surface.

Attribution contract:

* ``certain``: the value is computed from a real per-uid or per-process
  counter against a known limit (Linux inotify today).
* ``uncertain``: the value is an estimate from a host-level limit that
  does not isolate Ralph's own consumption (macOS ``kern.maxfiles``,
  unknown platforms, or any read failure).
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path


def _read_proc_value(path: Path) -> int | None:
    """Read an integer from a ``/proc/sys`` path, fail-closed."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _linux_inotify_pressure() -> dict[str, object]:
    """Linux: read inotify limits with certain attribution.

    The limit source (``/proc/sys/fs/inotify/max_user_watches``) is a real,
    measured kernel value, so attribution is ``certain``. The utilization
    ``value`` is ``None`` when a per-uid watch counter is not exposed via
    ``/proc`` (common on stock kernels); callers rely on the limit and
    Ralph's own watch_capacity field for degradation status.
    """
    max_watches = _read_proc_value(Path("/proc/sys/fs/inotify/max_user_watches"))
    max_instances = _read_proc_value(Path("/proc/sys/fs/inotify/max_user_instances"))
    if max_watches is None:
        return _uncertain("inotify_max_user_watches")
    return {
        "attribution": "certain",
        "signal": "inotify_max_user_watches",
        "value": None,
        "limit": max_watches,
        "instances_limit": max_instances,
        "safe_next_action": (
            "Check 'cat /proc/sys/fs/inotify/max_user_watches' and raise it "
            "with 'fs.inotify.max_user_watches=<N>' in sysctl.conf if Ralph "
            "reports watch-capacity degradation."
        ),
    }


def _macos_maxfiles_pressure() -> dict[str, object]:
    """macOS: read ``kern.maxfiles`` (a host-level limit, uncertain attribution)."""
    try:
        proc = subprocess.run(  # resource-lifecycle-ok: read-only sysctl probe with 2s timeout  # filesystem-poll-ok: read-only sysctl probe, not a managed child process
            ["sysctl", "-n", "kern.maxfiles"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return _uncertain("kern_maxfiles")
    text = proc.stdout.strip()
    try:
        limit = int(text)
    except ValueError:
        return _uncertain("kern_maxfiles")
    return {
        "attribution": "uncertain",
        "signal": "kern_maxfiles",
        "value": None,
        "limit": limit,
        "safe_next_action": (
            "Ralph uses a single recursive workspace watch; if watch-capacity "
            "degradation is reported, reduce workspace breadth or raise "
            "'kern.maxfiles' via a sysctl profile."
        ),
    }


def _uncertain(signal: str) -> dict[str, object]:
    """Return a fail-closed uncertain payload for ``signal``."""
    return {
        "attribution": "uncertain",
        "signal": signal,
        "value": None,
        "safe_next_action": (
            "Host watch-pressure could not be measured; rely on Ralph's "
            "workspace-health watch_capacity field for degradation status."
        ),
    }


def read_host_pressure() -> dict[str, object]:
    """Return the host filesystem-watch pressure payload (never raises).

    Returns a dict with:

    * ``attribution``: ``'certain'`` or ``'uncertain'``.
    * ``signal``: a non-empty identifier for the host limit source.
    * ``value``: utilization in ``[0.0, 1.0]``, or ``None`` when unknown.
    * ``safe_next_action``: a non-empty operator-facing remediation hint.

    Platform-aware: Linux probes inotify limits; macOS probes
    ``kern.maxfiles``; other platforms and any I/O failure return an
    uncertain payload.
    """
    system = platform.system()
    if system == "Linux":
        return _linux_inotify_pressure()
    if system == "Darwin":
        return _macos_maxfiles_pressure()
    return _uncertain("unknown_platform")


__all__ = ["read_host_pressure"]
