"""Runtime-only build-flavor metadata.

Published package metadata continues to use ``ralph.__version__`` unchanged.
"""

from __future__ import annotations

from ralph import _BASE_VERSION

BUILD_FLAVOR: str = ""
BUILD_SOURCE_COMMIT: str = ""
BUILD_SOURCE_PATH: str = ""
BUILD_INSTALLED_AT: str = ""

_SHORT_COMMIT_LENGTH = 8


def flavored_version() -> str:
    """Return the public version including an installer-written flavor suffix."""
    return _BASE_VERSION + BUILD_FLAVOR


def build_provenance_line() -> str:
    """Return the checkout this build was installed from, or ``""`` when unknown.

    Every checkout and git worktree installs into the same ``rdev`` snapshot, so
    the running dev build is not necessarily the one the reader is standing in.
    Naming the source checkout in ``--version`` output makes that visible without
    having to dig through the snapshot's metadata.  Published installs carry no
    checkout provenance and get an empty line, which callers skip.
    """
    if not BUILD_SOURCE_PATH and not BUILD_SOURCE_COMMIT:
        return ""
    source = BUILD_SOURCE_PATH or "(unknown source)"
    commit = BUILD_SOURCE_COMMIT[:_SHORT_COMMIT_LENGTH] or "(unknown commit)"
    return f"built from {source} @ {commit}"
