"""Black-box tests for the Ralph runtime-artifact allowlist predicate.

The leaf module ``ralph.phases._agent_internal_paths`` is the single source of
truth for which paths the commit_cleanup phase is allowed to delete
unconditionally. These tests pin the canonical inventory (positive cases)
and the security boundary (negative cases).
"""

from __future__ import annotations

from ralph.phases._agent_internal_paths import (
    _AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB,
    AGENT_INTERNAL_DIR_GLOBS,
    AGENT_INTERNAL_ROOT_BASENAMES,
    AGENT_INTERNAL_TOP_LEVEL_BASENAMES,
    is_agent_internal_path,
)


def test_frozensets_non_empty() -> None:
    """The three frozensets must not be silently emptied."""
    assert len(AGENT_INTERNAL_TOP_LEVEL_BASENAMES) >= 14, (
        f"Expected at least 14 top-level basenames, got {len(AGENT_INTERNAL_TOP_LEVEL_BASENAMES)}"
    )
    assert len(AGENT_INTERNAL_DIR_GLOBS) >= 7, (
        f"Expected at least 7 dir-segment globs, got {len(AGENT_INTERNAL_DIR_GLOBS)}"
    )
    assert len(AGENT_INTERNAL_ROOT_BASENAMES) >= 1, (
        f"Expected at least 1 root basename, got {len(AGENT_INTERNAL_ROOT_BASENAMES)}"
    )
    assert _AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB == "completion_seen_*.json", (
        f"Completion-sentinel glob must be the canonical on-disk filename glob, "
        f"got {_AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB!r}"
    )


def test_root_basename_only_at_root() -> None:
    """``checkpoint.json`` at the root is allowed; nested ``subdir/checkpoint.json`` is NOT.

    Note: ``./checkpoint.json`` and ``checkpoint.json`` both produce
    ``Path('checkpoint.json').parts == ('checkpoint.json',)`` so they are
    equivalent at the path-parse level.
    """
    assert is_agent_internal_path("checkpoint.json") is True
    assert is_agent_internal_path("sub/checkpoint.json") is False
    assert is_agent_internal_path("a/b/c/checkpoint.json") is False
    assert is_agent_internal_path("src/lib/checkpoint.json") is False


def test_accepts_all_canonical_basenames() -> None:
    """Every canonical top-level basename under ``.agent/`` is accepted."""
    for name in AGENT_INTERNAL_TOP_LEVEL_BASENAMES:
        rel = f".agent/{name}"
        assert is_agent_internal_path(rel) is True, (
            f"Expected canonical top-level path {rel!r} to be accepted"
        )


def test_accepts_canonical_root_basename() -> None:
    """Bare ``checkpoint.json`` at the repo root is an engine artifact."""
    for name in AGENT_INTERNAL_ROOT_BASENAMES:
        assert is_agent_internal_path(name) is True, (
            f"Expected canonical root basename {name!r} to be accepted"
        )


def test_accepts_all_canonical_dir_segment_paths() -> None:
    """Any path under a canonical ``.agent/<dir>/`` directory is accepted."""
    for directory in AGENT_INTERNAL_DIR_GLOBS:
        rel = f".agent/{directory}/payload.json"
        assert is_agent_internal_path(rel) is True, (
            f"Expected dir-segment path {rel!r} to be accepted (dir={directory!r})"
        )


def test_accepts_completion_seen_glob() -> None:
    """The canonical completion-sentinel filename glob ``completion_seen_*.json`` is accepted.

    This is the on-disk filename pattern (per ``COMPLETION_SENTINEL_RELPATHFMT`` in
    ``ralph.mcp.tools.coordination``), NOT the Python abstraction identifier.
    """
    for stem in ("completion_seen_abc.json", "completion_seen_run-12345.json"):
        rel = f".agent/{stem}"
        assert is_agent_internal_path(rel) is True, (
            f"Expected completion sentinel {rel!r} to be accepted"
        )
    # Negative — wrong filename pattern (Python abstraction, not the canonical filename)
    for bad in (
        ".agent/completion_sentinel_abc.json",
        ".agent/completion_abc.json",
    ):
        assert is_agent_internal_path(bad) is False, (
            f"Wrong-format completion sentinel {bad!r} must NOT be accepted"
        )


def test_rejects_source_files_under_agent_dir() -> None:
    """Source-code files under ``.agent/`` that are NOT in the allowlist are REJECTED.

    Pins the security boundary: blanket path-prefix matching would silently allow
    deletion of user-authored tracked files like ``.agent/test.py``.
    """
    negative_paths = (
        ".agent/test.py",
        ".agent/utils.py",
        ".agent/CHANGELOG.md",
        ".agent/note.txt",
        ".agent/scripts/build.sh",
        ".agent/lib/foo.py",
        ".agent/hooks/pre-commit.py",
        ".agent/notes/foo.txt",
        ".agent/data/seed.json",
        ".agent/random_config.json",
        ".agent/foo/bar.py",
    )
    for rel in negative_paths:
        assert is_agent_internal_path(rel) is False, (
            f"User-authored path under .agent/ {rel!r} must NOT be agent-internal"
        )


def test_rejects_paths_outside_agent_dir() -> None:
    """Source-code files outside ``.agent/`` are never accepted regardless of basename."""
    negative_paths = (
        "app/controllers/foo.rb",
        "src/main.go",
        "lib/utils.rb",
        "scripts/build.sh",
        "checkpoint.json.bak",
        "test.py",
        "PLAN.md",
        "PROMPT.md",
    )
    for rel in negative_paths:
        assert is_agent_internal_path(rel) is False, (
            f"Path outside .agent/ {rel!r} must NOT be agent-internal"
        )
