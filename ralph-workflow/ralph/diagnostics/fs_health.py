"""Filesystem-health diagnostics for the workspace volume (macOS-focused).

Long multi-instance runs on an external volume can drive the macOS
``fseventsd`` daemon to a full core when (a) Spotlight indexes the
churned paths and (b) the volume's ``.fseventsd`` journal bloats.
This check surfaces both so operators apply the documented mitigations
(see docs/sphinx/diagnostics.md, "External-volume filesystem hygiene").
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

_JOURNAL_WARN_BYTES = 50 * 1024 * 1024
_VOLUME_PATH_PARTS_FOR_EXTERNAL = 3


class _SubprocessRunner(Protocol):
    """Minimal protocol capturing the ``subprocess.run`` shape used by FsHealth.

    Only ``capture_output``, ``text``, and ``timeout`` are forwarded.
    Tests may pass a fake that exposes ``returncode`` and ``stdout``.
    """

    def __call__(
        self,
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> object: ...


def _probe_spotlight(volume: Path, run_command: _SubprocessRunner) -> bool | None:
    """Run ``mdutil -s <volume>`` and report the Spotlight state."""
    try:
        result = run_command(
            ["mdutil", "-s", str(volume)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    result_obj: object = result
    rc_obj: object = getattr(result_obj, "returncode", None)
    stdout_obj: object = getattr(result_obj, "stdout", "") or ""
    rc_int: int | None = rc_obj if isinstance(rc_obj, int) else None
    if not isinstance(stdout_obj, str):
        return None
    if rc_int != 0:
        return None
    return "Indexing enabled" in stdout_obj


def _probe_journal_size(journal_dir: Path) -> int | None:
    """Sum the file sizes under ``<volume>/.fseventsd``."""
    try:
        if not journal_dir.is_dir():
            return None
    except OSError:
        return None
    total = 0
    try:
        for entry in journal_dir.iterdir():
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        return None
    return total


def _volume_root(path: Path) -> Path:
    """Return the volume root that contains the workspace.

    ``/Volumes/<vol>/proj/ws`` -> ``/Volumes/<vol>``. Anything not under
    ``/Volumes`` is treated as boot-volume ``/``.
    """
    resolved = path.resolve()
    parts = resolved.parts
    if len(parts) >= _VOLUME_PATH_PARTS_FOR_EXTERNAL and parts[1] == "Volumes":
        return Path(parts[0]) / parts[1] / parts[2]
    return Path(parts[0]) if parts else Path("/")


def _run_subprocess_mdutil(
    args: list[str],
    *,
    capture_output: bool,
    text: bool,
    timeout: int,
) -> object:
    """Default ``run_command`` adapter that delegates to ``subprocess.run``."""
    proc: subprocess.CompletedProcess[str] = subprocess.run(
        args,
        capture_output=capture_output,
        text=text,
        timeout=timeout,
        check=False,
    )
    return proc


@dataclass
class FsHealth:
    """Filesystem-environment health snapshot for the workspace volume.

    Attributes:
        volume_root: Absolute path to the volume containing the workspace.
        spotlight_indexing_enabled: When non-None, True iff Spotlight
            (``mdutil -s``) reports "Indexing enabled" on the volume.
            ``None`` when Spotlight status cannot be determined (e.g.
            non-macOS host, ``mdutil`` missing, or subprocess error).
        fsevents_journal_bytes: Total size of files under
            ``<volume>/.fseventsd``. ``None`` when the directory cannot
            be enumerated (locked volume, permissions quirk).
        warnings: Human-readable operator warnings. Each entry is one
            diagnostic the operator should act on.
    """

    volume_root: str
    spotlight_indexing_enabled: bool | None = None
    fsevents_journal_bytes: int | None = None
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def gather(
        cls,
        workspace_root: Path,
        *,
        run_command: _SubprocessRunner = _run_subprocess_mdutil,
    ) -> FsHealth:
        """Probe the workspace volume and return a populated ``FsHealth``.

        Args:
            workspace_root: Workspace directory whose containing volume
                is being probed.
            run_command: Subprocess runner; defaults to
                ``subprocess.run``. Injectable for tests.

        Returns:
            ``FsHealth`` populated with the volume root, Spotlight
            status, ``.fseventsd`` journal size, and any operator
            warnings. On non-darwin hosts only ``volume_root`` is set.
        """
        volume = _volume_root(workspace_root)
        journal_dir = Path(volume) / ".fseventsd"
        health = cls(volume_root=str(volume))

        if sys.platform == "darwin":
            health.spotlight_indexing_enabled = _probe_spotlight(volume, run_command)
        health.fsevents_journal_bytes = _probe_journal_size(journal_dir)

        if health.spotlight_indexing_enabled is True:
            health.warnings.append(
                f"Spotlight indexing is enabled on {volume}. "
                "Disable with `sudo mdutil -i off <volume>`."
            )
        if (
            health.fsevents_journal_bytes is not None
            and health.fsevents_journal_bytes > _JOURNAL_WARN_BYTES
        ):
            human_mb = health.fsevents_journal_bytes / (1024 * 1024)
            health.warnings.append(
                f"fseventsd journal on {volume} is {human_mb:.1f} MB "
                "(threshold 50 MB). Reset it with "
                "`sudo rm -rf <volume>/.fseventsd` after stopping runs."
            )

        return health


__all__ = [
    "_JOURNAL_WARN_BYTES",
    "FsHealth",
    "_probe_journal_size",
    "_probe_spotlight",
    "_volume_root",
]
