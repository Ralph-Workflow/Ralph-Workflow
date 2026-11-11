
from __future__ import annotations

from ralph import _BASE_VERSION

BUILD_FLAVOR: str = ""
BUILD_SOURCE_COMMIT: str = ""
BUILD_SOURCE_PATH: str = ""
BUILD_INSTALLED_AT: str = ""

_SHORT_COMMIT_LENGTH = 8


def flavored_version() -> str:
    return _BASE_VERSION + BUILD_FLAVOR


def build_provenance_line() -> str:
    if not BUILD_SOURCE_PATH and not BUILD_SOURCE_COMMIT:
        return ""
    source = BUILD_SOURCE_PATH or "(unknown source)"
    commit = BUILD_SOURCE_COMMIT[:_SHORT_COMMIT_LENGTH] or "(unknown commit)"
    return f"built from {source} @ {commit}"
