from __future__ import annotations

import platform
import subprocess
from pathlib import Path


def _read_proc_value(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _linux_inotify_pressure() -> dict[str, object]:
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
    try:
        proc = subprocess.run(
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
    system = platform.system()
    if system == "Linux":
        return _linux_inotify_pressure()
    if system == "Darwin":
        return _macos_maxfiles_pressure()
    return _uncertain("unknown_platform")


__all__ = ["read_host_pressure"]
