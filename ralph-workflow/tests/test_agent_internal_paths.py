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
    """Canonical ``.agent/<dir>/<file>`` paths are accepted when extension matches."""
    # Per-directory extension allowlist mirrors the engine-written file types
    # for each directory. The engine never writes a ``.json`` payload under
    # ``.agent/raw/`` (logs only) or under ``.agent/artifact-formats/`` (markdown only),
    # so the test uses the appropriate extension for each directory.
    _dir_payload_extension: dict[str, str] = {
        "raw": ".log",
        "tmp": ".json",
        "artifacts": ".json",
        "workers": ".json",
        "receipts": ".json",
        "prompt_history": ".json",
        "artifact-formats": ".md",
    }
    for directory in AGENT_INTERNAL_DIR_GLOBS:
        ext = _dir_payload_extension[directory]
        rel = f".agent/{directory}/payload{ext}"
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


def test_rejects_source_files_inside_engine_internal_dirs() -> None:
    """Source-code files inside engine-internal directories are REJECTED.

    Regression for the security gap where the predicate previously
    returned True for any path whose second segment was in
    ``AGENT_INTERNAL_DIR_GLOBS``, regardless of file extension. That
    blanket dir-prefix match would silently allow deletion of
    user-authored tracked files placed inside engine-internal dirs
    like ``.agent/raw/script.py`` or
    ``.agent/workers/<unit>/src/main.py``.

    The tightened rule: a file inside an engine-internal directory is
    deletable ONLY when its extension is in the per-directory
    allowlist (``_AGENT_INTERNAL_DIR_FILE_EXTENSIONS``).
    """
    negative_paths = (
        # .agent/raw/ only accepts .log files
        ".agent/raw/script.py",
        ".agent/raw/main.go",
        ".agent/raw/notes.md",
        # .agent/tmp/ only accepts .log, .md, .json
        ".agent/tmp/config.yaml",
        ".agent/tmp/main.py",
        # .agent/artifacts/ only accepts .json
        ".agent/artifacts/notes.md",
        # .agent/receipts/ only accepts .json
        ".agent/receipts/run-1/note.md",
        # .agent/prompt_history/ only accepts .json
        ".agent/prompt_history/notes.md",
        # .agent/artifact-formats/ only accepts .md
        ".agent/artifact-formats/data.json",
        # .agent/workers/ only accepts .log, .md, .json (recursive)
        ".agent/workers/unit-a/src/main.py",
        ".agent/workers/unit-a/src/foo.go",
        ".agent/workers/unit-a/sub/foo.rs",
    )
    for rel in negative_paths:
        assert is_agent_internal_path(rel) is False, (
            f"User-authored file inside engine-internal dir {rel!r} must NOT be agent-internal"
        )


def test_accepts_engine_extensions_inside_engine_internal_dirs() -> None:
    """Engine-owned file extensions inside engine-internal directories are ACCEPTED.

    Pairs with ``test_rejects_source_files_inside_engine_internal_dirs``.
    The per-directory allowlist covers ``.log``, ``.md``, ``.json``
    (where appropriate) at any depth inside the engine-internal dir.
    """
    positive_paths = (
        ".agent/raw/opencode.log",
        ".agent/raw/another.log",
        ".agent/tmp/mcp-server.log",
        ".agent/tmp/scratch.json",
        ".agent/tmp/prompt.md",
        ".agent/artifacts/commit_cleanup.json",
        ".agent/artifacts/x.json",
        ".agent/receipts/run-1/commit_cleanup.json",
        ".agent/receipts/run-2/smoke.json",
        ".agent/prompt_history/abc.json",
        ".agent/artifact-formats/commit_message.md",
        ".agent/workers/unit-a/output.log",
        ".agent/workers/unit-a/prompt.md",
        ".agent/workers/unit-a/artifacts/x.json",
        ".agent/workers/unit-a/tmp/checkpoint.json",
        ".agent/workers/unit-a/sub/dir/data.json",
    )
    for rel in positive_paths:
        assert is_agent_internal_path(rel) is True, f"Engine-owned file {rel!r} must be accepted"


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
