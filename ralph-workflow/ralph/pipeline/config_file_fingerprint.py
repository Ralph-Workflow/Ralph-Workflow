"""Pure fingerprint computation for configuration-failure self-recovery.

This module computes ``(path, content_hash)`` fingerprints of failing
configuration files and provides the pure helpers that decide whether a
retry is appropriate after a file is repaired. Retries occur once per
distinct fingerprint, preventing endless spin when the file is unchanged.

The stateful watcher that consumes these fingerprints lives in
:mod:`ralph.pipeline.config_file_fingerprint_watcher`.
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


__all__ = [
    "ConfigFileFingerprint",
    "compute_config_file_fingerprint",
    "extract_config_path_from_reason",
    "get_config_file_fingerprint",
    "should_retry_config_failure",
]
