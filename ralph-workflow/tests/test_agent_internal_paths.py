from __future__ import annotations

from ralph.phases._agent_internal_paths import (
    _AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB,
    AGENT_INTERNAL_DIR_GLOBS,
    AGENT_INTERNAL_ROOT_BASENAMES,
    AGENT_INTERNAL_TOP_LEVEL_BASENAMES,
    is_agent_internal_path,
)


def test_frozensets_non_empty() -> None:
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
    assert is_agent_internal_path("checkpoint.json") is True
    assert is_agent_internal_path("sub/checkpoint.json") is False
    assert is_agent_internal_path("a/b/c/checkpoint.json") is False
    assert is_agent_internal_path("src/lib/checkpoint.json") is False


def test_accepts_all_canonical_basenames() -> None:
    for name in AGENT_INTERNAL_TOP_LEVEL_BASENAMES:
        rel = f".agent/{name}"
        assert is_agent_internal_path(rel) is True, (
            f"Expected canonical top-level path {rel!r} to be accepted"
        )


def test_accepts_canonical_root_basename() -> None:
    for name in AGENT_INTERNAL_ROOT_BASENAMES:
        assert is_agent_internal_path(name) is True, (
            f"Expected canonical root basename {name!r} to be accepted"
        )


def test_accepts_all_canonical_dir_segment_paths() -> None:
    _dir_payload_extension: dict[str, str] = {
        "raw": ".log",
        "tmp": ".json",
        "artifacts": ".md",
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
    for stem in ("completion_seen_abc.json", "completion_seen_run-12345.json"):
        rel = f".agent/{stem}"
        assert is_agent_internal_path(rel) is True, (
            f"Expected completion sentinel {rel!r} to be accepted"
        )
    for bad in (
        ".agent/completion_sentinel_abc.json",
        ".agent/completion_abc.json",
    ):
        assert is_agent_internal_path(bad) is False, (
            f"Wrong-format completion sentinel {bad!r} must NOT be accepted"
        )


def test_rejects_source_files_under_agent_dir() -> None:
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
    negative_paths = (
        ".agent/raw/script.py",
        ".agent/raw/main.go",
        ".agent/raw/notes.md",
        ".agent/tmp/config.yaml",
        ".agent/tmp/main.py",
        ".agent/artifacts/plan.json",
        ".agent/receipts/run-1/note.md",
        ".agent/prompt_history/notes.md",
        ".agent/artifact-formats/data.json",
        ".agent/workers/unit-a/src/main.py",
        ".agent/workers/unit-a/src/foo.go",
        ".agent/workers/unit-a/sub/foo.rs",
    )
    for rel in negative_paths:
        assert is_agent_internal_path(rel) is False, (
            f"User-authored file inside engine-internal dir {rel!r} must NOT be agent-internal"
        )


def test_accepts_engine_extensions_inside_engine_internal_dirs() -> None:
    positive_paths = (
        ".agent/raw/opencode.log",
        ".agent/raw/another.log",
        ".agent/tmp/mcp-server.log",
        ".agent/tmp/scratch.json",
        ".agent/tmp/prompt.md",
        ".agent/artifacts/commit_review.md",
        ".agent/artifacts/plan.md",
        ".agent/receipts/run-1/commit_review.json",
        ".agent/receipts/run-2/smoke.json",
        ".agent/prompt_history/abc.json",
        ".agent/artifact-formats/commit_message.md",
        ".agent/workers/unit-a/output.log",
        ".agent/workers/unit-a/prompt.md",
        ".agent/workers/unit-a/artifacts/development_result.md",
        ".agent/workers/unit-a/tmp/checkpoint.json",
        ".agent/workers/unit-a/sub/dir/data.json",
    )
    for rel in positive_paths:
        assert is_agent_internal_path(rel) is True, f"Engine-owned file {rel!r} must be accepted"


def test_rejects_paths_outside_agent_dir() -> None:
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


def test_accepts_auto_integrate_crash_record() -> None:
    assert is_agent_internal_path(".agent/auto_integrate_in_progress.json") is True


def test_auto_integrate_record_filename_is_registered() -> None:
    from ralph.pipeline.auto_integrate_record import AUTO_INTEGRATE_RECORD_FILENAME

    assert AUTO_INTEGRATE_RECORD_FILENAME in AGENT_INTERNAL_TOP_LEVEL_BASENAMES, (
        f"{AUTO_INTEGRATE_RECORD_FILENAME!r} must be registered in "
        "AGENT_INTERNAL_TOP_LEVEL_BASENAMES or the commit phase's "
        "`git add -A` will land the crash record on the operator's mainline"
    )
