
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import NamedTuple


class ConfigFileFingerprint(NamedTuple):

    path: Path
    content_hash: str | None


def compute_config_file_fingerprint(path: Path | str | None) -> str | None:
    if path is None:
        return None
    p = Path(path)
    try:
        raw_bytes = p.read_bytes()
        return hashlib.sha256(raw_bytes).hexdigest()
    except (FileNotFoundError, IsADirectoryError):
        return None
    except OSError as exc:
        return f"unreadable:{type(exc).__name__}"


def get_config_file_fingerprint(path: Path | str) -> ConfigFileFingerprint:
    resolved = Path(path).resolve()
    return ConfigFileFingerprint(
        path=resolved,
        content_hash=compute_config_file_fingerprint(resolved),
    )


def extract_config_path_from_reason(reason: str | None) -> Path | None:
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
