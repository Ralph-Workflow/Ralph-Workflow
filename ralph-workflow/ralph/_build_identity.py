from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ralph import __version__
from ralph._build_meta import BUILD_SOURCE_COMMIT
from ralph.git.operations import get_head_sha

_COMMIT_LENGTH = 8
_UNKNOWN_COMMIT = "unknown"


def _source_commit_uncached() -> str:
    if BUILD_SOURCE_COMMIT:
        return BUILD_SOURCE_COMMIT[:_COMMIT_LENGTH]
    try:
        sha = get_head_sha(Path(__file__).resolve().parents[2])
    except Exception:
        return _UNKNOWN_COMMIT
    if not sha:
        return _UNKNOWN_COMMIT
    return sha[:_COMMIT_LENGTH]


_source_commit = lru_cache(maxsize=1)(_source_commit_uncached)


def source_commit() -> str:
    return _source_commit()


def build_identity() -> str:
    return f"v{__version__}+{source_commit()}"
