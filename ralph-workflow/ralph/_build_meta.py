"""Runtime-only build-flavor metadata.

Published package metadata continues to use ``ralph.__version__`` unchanged.
"""

from __future__ import annotations

from ralph import _BASE_VERSION

BUILD_FLAVOR: str = ""


def flavored_version() -> str:
    """Return the public version including an installer-written flavor suffix."""
    return _BASE_VERSION + BUILD_FLAVOR
