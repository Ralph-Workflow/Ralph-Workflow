"""Pure fingerprint computation and retry-gating for configuration failure self-recovery.

This module computes `(path, content_hash)` fingerprints of failing configuration files
and provides helpers to determine if retry is appropriate after a file is repaired.
Retries occur once per distinct fingerprint, preventing endless spin when unchanged.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import NamedTuple


class ConfigFileFingerprint(NamedTuple):
    """Immutable fingerprint representation for a configuration file on disk.

    Attributes:
        path: Resolved absolute Path of the configuration file.
        content_hash: SHA-256 hexadecimal digest of the file bytes, or None if
            the file does not exist or cannot be read.
    """

    path: Path
    content_hash: str | None


def compute_config_file_fingerprint(path: Path | str | None) -> str | None:
    """Compute the SHA-256 content hash of a configuration file.

    Returns:
        Hexadecimal SHA-256 digest string if the file exists and is readable,
        or None if path is None, file does not exist, or path is a directory.
        If an unreadable file raises OSError (e.g. PermissionError), returns
        a distinct string descriptor ("unreadable:<ErrorClass>") so permission
        repairs are detected as distinct fingerprint transitions.
    """
    if path is None:
        return None
    p = Path(path)
    try:
        # filesystem-read-ok: read config file bytes for failure fingerprinting
        raw_bytes = p.read_bytes()
        return hashlib.sha256(raw_bytes).hexdigest()
    except (FileNotFoundError, IsADirectoryError):
        return None
    except OSError as exc:
        return f"unreadable:{type(exc).__name__}"


def get_config_file_fingerprint(path: Path | str) -> ConfigFileFingerprint:
    """Return a typed ConfigFileFingerprint for the given path.

    Supports tuple unpacking: ``path, content_hash = get_config_file_fingerprint(...)``.
    """
    resolved = Path(path).resolve()
    return ConfigFileFingerprint(
        path=resolved,
        content_hash=compute_config_file_fingerprint(resolved),
    )


def extract_config_path_from_reason(reason: str | None) -> Path | None:
    """Extract configuration file path from a fix message or error reason string.

    Looks for patterns like 'fix <path> then run ralph --check-config'.
    """
    if not reason:
        return None
    match = re.search(r"fix\s+(.*?)\s+then\s+run\s+ralph\s+--check-config", reason)
    if match is not None:
        raw_target: object = match.group(1)
        if isinstance(raw_target, str):
            extracted = raw_target.strip()
            if extracted:
                return Path(extracted)
    return None


def should_retry_config_failure(
    path: Path | str,
    last_fingerprint: str | None,
) -> tuple[bool, str | None]:
    """Pure functional helper to check if a failing configuration file warrants retry.

    Args:
        path: Path to the failing configuration file.
        last_fingerprint: Fingerprint observed at previous failure or attempt.

    Returns:
        (should_retry, new_fingerprint):
        should_retry is True if the freshly computed fingerprint differs from
        last_fingerprint (e.g. file repaired, created, or deleted).
        new_fingerprint is the freshly computed fingerprint to record for the next check.
    """
    current_fp = compute_config_file_fingerprint(path)
    if current_fp != last_fingerprint:
        return True, current_fp
    return False, last_fingerprint


class ConfigFileFingerprintWatcher:
    """Stateful watcher tracking config file fingerprints for self-recovery retries.

    Ensures that after a failing configuration file is repaired (fingerprint changes),
    retry occurs once per distinct fingerprint, preventing endless spin when unchanged.
    """

    def __init__(self, default_path: Path | str | None = None) -> None:
        self._default_path: Path | None = (
            Path(default_path).resolve() if default_path is not None else None
        )
        self._last_fingerprints: dict[Path, str | None] = {}
        self._attempted_fingerprints: dict[Path, set[str | None]] = {}
        if self._default_path is not None:
            self.record_failure(self._default_path)

    def _resolve_path(self, path: Path | str | None) -> Path:
        target = path if path is not None else self._default_path
        if target is None:
            raise ValueError("No path provided and no default path configured")
        return Path(target).resolve()

    def record_failure(
        self,
        path: Path | str | None = None,
        fingerprint: str | None = None,
    ) -> str | None:
        """Record the initial failure state for a configuration file.

        Args:
            path: Target file path. Uses default_path if None.
            fingerprint: Pre-computed fingerprint, or None to compute from disk.

        Returns:
            The recorded failure fingerprint.
        """
        resolved = self._resolve_path(path)
        fp = fingerprint if fingerprint is not None else compute_config_file_fingerprint(resolved)
        self._last_fingerprints[resolved] = fp
        attempted = self._attempted_fingerprints.setdefault(resolved, set())
        attempted.add(fp)
        return fp

    def get_last_fingerprint(self, path: Path | str | None = None) -> str | None:
        """Return the last recorded or attempted fingerprint for the given path."""
        resolved = self._resolve_path(path)
        return self._last_fingerprints.get(resolved)

    def get_current_fingerprint(self, path: Path | str | None = None) -> str | None:
        """Compute the current fingerprint on disk for the given path."""
        resolved = self._resolve_path(path)
        return compute_config_file_fingerprint(resolved)

    def should_retry(
        self,
        path: Path | str | None = None,
        *,
        consume: bool = True,
        allow_revisit: bool = False,
    ) -> bool:
        """Determine whether retry is appropriate for the file.

        Returns True if the file's fingerprint has changed since the last failure
        or attempt and has not already been retried (unless allow_revisit=True).

        If consume=True (the default), marks the new fingerprint as attempted
        so subsequent checks with unchanged content return False, preventing endless spin.
        """
        resolved = self._resolve_path(path)
        current = compute_config_file_fingerprint(resolved)

        if resolved not in self._last_fingerprints:
            # First observation of this path: record baseline failure state and wait for repair
            self.record_failure(resolved, fingerprint=current)
            return False

        last = self._last_fingerprints[resolved]
        if current == last:
            # Unchanged: do not retry
            return False

        attempted = self._attempted_fingerprints.setdefault(resolved, set())
        if not allow_revisit and current in attempted:
            # Already retried for this distinct fingerprint: do not retry again
            return False

        if consume:
            self._last_fingerprints[resolved] = current
            attempted.add(current)

        return True

    def mark_attempt(
        self,
        path: Path | str | None = None,
        fingerprint: str | None = None,
    ) -> None:
        """Mark a fingerprint as attempted without checking should_retry."""
        resolved = self._resolve_path(path)
        fp = fingerprint if fingerprint is not None else compute_config_file_fingerprint(resolved)
        self._last_fingerprints[resolved] = fp
        self._attempted_fingerprints.setdefault(resolved, set()).add(fp)

    def reset(self, path: Path | str | None = None) -> None:
        """Reset state for a specific path, or all paths if path is None."""
        if path is not None:
            resolved = Path(path).resolve()
            self._last_fingerprints.pop(resolved, None)
            self._attempted_fingerprints.pop(resolved, None)
        else:
            self._last_fingerprints.clear()
            self._attempted_fingerprints.clear()
            if self._default_path is not None:
                self.record_failure(self._default_path)


__all__ = [
    "ConfigFileFingerprint",
    "ConfigFileFingerprintWatcher",
    "compute_config_file_fingerprint",
    "extract_config_path_from_reason",
    "get_config_file_fingerprint",
    "should_retry_config_failure",
]
