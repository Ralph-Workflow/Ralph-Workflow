from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.agents.invoke._workspace_change_classifier import (
    ARTIFACT_PARENT_DIRS,
    CACHE_FILENAME_GLOBS,
    CACHE_PARENT_DIRS,
    DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS,
    LOG_SUFFIXES,
    SOURCE_EXTENSIONS,
    WorkspaceChangeClassifier,
    WorkspaceChangeKind,
    _is_agent_internal_state_db_path,
    _normalize_workspace_change_weights,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_workspace_change_kind_enum_members() -> None:
    values = {k.value for k in WorkspaceChangeKind}
    assert values == {"source", "log", "cache", "artifact", "other"}


def test_default_weights_source_is_one() -> None:
    assert DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS["source"] == 1.0


def test_default_weights_log_is_zero() -> None:
    assert DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS["log"] == 0.0


def test_default_weights_cache_is_zero() -> None:
    assert DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS["cache"] == 0.0


def test_default_weights_artifact_is_zero() -> None:
    assert DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS["artifact"] == 0.0


def test_default_weights_other_is_zero() -> None:
    assert DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS["other"] == 0.0


def test_default_weights_are_all_binary() -> None:
    for value in DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS.values():
        assert value in {0.0, 1.0}, f"default weight {value!r} is not binary"


def test_classifier_default_constructor_uses_safe_defaults() -> None:
    classifier = WorkspaceChangeClassifier()
    assert classifier.weights == dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)


def test_classifier_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="not_a_kind"):
        WorkspaceChangeClassifier(weights={"not_a_kind": 1.0})


def test_classifier_rejects_intermediate_weight() -> None:
    with pytest.raises(ValueError, match="binary weight"):
        WorkspaceChangeClassifier(weights={"source": 0.5})


def test_classifier_rejects_weight_above_one() -> None:
    with pytest.raises(ValueError, match="binary weight"):
        WorkspaceChangeClassifier(weights={"source": 2.0})


def test_classifier_rejects_negative_weight() -> None:
    with pytest.raises(ValueError, match="binary weight"):
        WorkspaceChangeClassifier(weights={"source": -0.1})


def test_classify_cache_parent_dot_git() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/.git/HEAD")
    assert kind is WorkspaceChangeKind.CACHE
    assert weight == 0.0


def test_classify_cache_parent_pycache() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/src/__pycache__/foo.cpython-312.pyc")
    assert kind is WorkspaceChangeKind.CACHE
    assert weight == 0.0


def test_classify_cache_parent_node_modules() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/node_modules/lodash/index.js")
    assert kind is WorkspaceChangeKind.CACHE
    assert weight == 0.0


def test_classify_cache_parent_dot_venv() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/.venv/lib/python3.12/site.py")
    assert kind is WorkspaceChangeKind.CACHE
    assert weight == 0.0


def test_classify_cache_parent_agent_tmp() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/.agent/tmp/foo.log")
    assert kind is WorkspaceChangeKind.CACHE
    assert weight == 0.0


def test_classify_cache_parent_agent_raw() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/.agent/raw/stream.bin")
    assert kind is WorkspaceChangeKind.CACHE
    assert weight == 0.0


def test_cache_parent_dirs_does_not_include_dot_agent_top_level() -> None:
    assert ".agent" not in CACHE_PARENT_DIRS
    assert ".agent/tmp" in CACHE_PARENT_DIRS
    assert ".agent/raw" in CACHE_PARENT_DIRS


def test_classify_cache_completion_seen_sentinel() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/.agent/completion_seen_abc-123.json")
    assert kind is WorkspaceChangeKind.CACHE
    assert weight == 0.0


def test_cache_filename_globs_contains_completion_seen() -> None:
    assert "completion_seen_*.json" in CACHE_FILENAME_GLOBS


def test_classify_cache_agent_internal_state_db() -> None:
    classifier = WorkspaceChangeClassifier()
    for path in (
        "/repo/.agent/state.db",
        "/repo/.agent/state.db-wal",
        "/repo/.agent/state.db-shm",
    ):
        kind, weight = classifier.classify(path)
        assert kind is WorkspaceChangeKind.CACHE, f"{path} should be CACHE"
        assert weight == 0.0


def test_classify_user_file_state_db_is_not_cache() -> None:
    classifier = WorkspaceChangeClassifier()
    for path in (
        "/repo/src/state.db",
        "/repo/docs/state.db-wal",
        "/repo/foo/state.db-shm",
    ):
        kind, _weight = classifier.classify(path)
        assert kind is not WorkspaceChangeKind.CACHE, (
            f"{path} must NOT be classified CACHE (user file, not engine-internal)"
        )


def test_is_agent_internal_state_db_path_matches_dot_agent_only() -> None:
    for path in ("/repo/.agent/state.db", "/repo/.agent/state.db-wal", "/repo/.agent/state.db-shm"):
        posix = PurePosixPath(path)
        assert _is_agent_internal_state_db_path(posix.parts, posix.name) is True

    for path in ("/repo/src/state.db", "/repo/docs/state.db-wal", "/repo/foo/state.db-shm"):
        posix = PurePosixPath(path)
        assert _is_agent_internal_state_db_path(posix.parts, posix.name) is False

    posix = PurePosixPath("/repo/.agent/state.db-journal")
    assert _is_agent_internal_state_db_path(posix.parts, posix.name) is False

    assert _is_agent_internal_state_db_path(("state.db",), "state.db") is False


def test_cache_filename_globs_does_not_contain_state_db() -> None:
    for forbidden in ("state.db", "state.db-wal", "state.db-shm"):
        assert forbidden not in CACHE_FILENAME_GLOBS, (
            f"CACHE_FILENAME_GLOBS must not contain {forbidden!r};"
            f" use _is_agent_internal_state_db_path for path-scoped matching"
        )


def test_classify_artifact_agent_artifacts() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/.agent/artifacts/plan.md")
    assert kind is WorkspaceChangeKind.ARTIFACT
    assert weight == 0.0


def test_artifact_parent_dirs_contains_dot_agent_artifacts() -> None:
    assert frozenset({".agent/artifacts"}) == ARTIFACT_PARENT_DIRS


def test_classify_log_extension() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/agent.log")
    assert kind is WorkspaceChangeKind.LOG
    assert weight == 0.0


def test_classify_log_tmp_extension() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/scratch.tmp")
    assert kind is WorkspaceChangeKind.LOG
    assert weight == 0.0


def test_classify_log_pyc_extension() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/foo.pyc")
    assert kind is WorkspaceChangeKind.LOG
    assert weight == 0.0


def test_log_suffixes_includes_common_log_types() -> None:
    assert ".log" in LOG_SUFFIXES
    assert ".tmp" in LOG_SUFFIXES
    assert ".bak" in LOG_SUFFIXES
    assert ".swp" in LOG_SUFFIXES
    assert "~" in LOG_SUFFIXES
    assert ".pyc" in LOG_SUFFIXES
    assert ".pyo" in LOG_SUFFIXES


def test_classify_source_python() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/src/foo.py")
    assert kind is WorkspaceChangeKind.SOURCE
    assert weight == 1.0


def test_classify_source_rust() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/src/main.rs")
    assert kind is WorkspaceChangeKind.SOURCE
    assert weight == 1.0


def test_classify_source_typescript() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/src/app.ts")
    assert kind is WorkspaceChangeKind.SOURCE
    assert weight == 1.0


def test_classify_source_markdown() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/docs/README.md")
    assert kind is WorkspaceChangeKind.SOURCE
    assert weight == 1.0


def test_source_extensions_includes_common_languages() -> None:
    assert ".py" in SOURCE_EXTENSIONS
    assert ".rs" in SOURCE_EXTENSIONS
    assert ".go" in SOURCE_EXTENSIONS
    assert ".ts" in SOURCE_EXTENSIONS
    assert ".js" in SOURCE_EXTENSIONS
    assert ".java" in SOURCE_EXTENSIONS


def test_classify_other_unknown_extension() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/blob.xyz123")
    assert kind is WorkspaceChangeKind.OTHER
    assert weight == 0.0


def test_classify_other_binary_blob() -> None:
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/bin/exec")
    assert kind is WorkspaceChangeKind.OTHER
    assert weight == 0.0


def test_custom_weights_log_can_be_activated() -> None:
    classifier = WorkspaceChangeClassifier(weights={"source": 1.0, "log": 1.0})
    kind, weight = classifier.classify("/repo/agent.log")
    assert kind is WorkspaceChangeKind.LOG
    assert weight == 1.0


def test_custom_weights_source_can_be_disabled() -> None:
    classifier = WorkspaceChangeClassifier(weights={"source": 0.0})
    kind, weight = classifier.classify("/repo/src/foo.py")
    assert kind is WorkspaceChangeKind.SOURCE
    assert weight == 0.0


def test_normalize_workspace_change_weights_fills_defaults() -> None:
    normalized = _normalize_workspace_change_weights({"log": 1.0})
    assert normalized == {
        "source": 1.0,
        "log": 1.0,
        "cache": 0.0,
        "artifact": 0.0,
        "other": 0.0,
    }


def test_normalize_workspace_change_weights_handles_none() -> None:
    normalized = _normalize_workspace_change_weights(None)
    assert normalized == dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)


def test_workspace_monitor_drops_log_events() -> None:
    callback_invocations: list[tuple[WorkspaceChangeKind, float]] = []

    def on_event(kind: WorkspaceChangeKind, weight: float) -> None:
        callback_invocations.append((kind, weight))

    monitor = WorkspaceMonitor(
        "/tmp",
        on_event=on_event,
        classifier=WorkspaceChangeClassifier(),
    )
    monitor.record_event("/repo/agent.log")
    assert callback_invocations == []
    assert monitor.last_event_at is None
    assert monitor.event_count == 0


def test_workspace_monitor_drops_cache_events(tmp_path: Path) -> None:
    callback_invocations: list[tuple[WorkspaceChangeKind, float]] = []

    def on_event(kind: WorkspaceChangeKind, weight: float) -> None:
        callback_invocations.append((kind, weight))

    monitor = WorkspaceMonitor(tmp_path, on_event=on_event, classifier=WorkspaceChangeClassifier())
    monitor.record_event("/repo/__pycache__/foo.pyc")
    assert callback_invocations == []
    assert monitor.last_event_at is None


def test_workspace_monitor_drops_artifact_events(tmp_path: Path) -> None:
    callback_invocations: list[tuple[WorkspaceChangeKind, float]] = []

    def on_event(kind: WorkspaceChangeKind, weight: float) -> None:
        callback_invocations.append((kind, weight))

    monitor = WorkspaceMonitor(tmp_path, on_event=on_event, classifier=WorkspaceChangeClassifier())
    monitor.record_event("/repo/.agent/artifacts/plan.md")
    assert callback_invocations == []
    assert monitor.last_event_at is None


def test_workspace_monitor_passes_source_events_with_real_kind(tmp_path: Path) -> None:
    callback_invocations: list[tuple[WorkspaceChangeKind, float]] = []

    def on_event(kind: WorkspaceChangeKind, weight: float) -> None:
        callback_invocations.append((kind, weight))

    monitor = WorkspaceMonitor(tmp_path, on_event=on_event, classifier=WorkspaceChangeClassifier())
    monitor.record_event("/repo/src/foo.py")
    assert callback_invocations == [
        (WorkspaceChangeKind.SOURCE, 1.0),
    ]
    assert monitor.last_event_at is not None
    assert monitor.event_count == 1


def test_workspace_monitor_accepts_0_arg_callback(tmp_path: Path) -> None:
    callback_invocations: list[None] = []

    def on_event() -> None:
        callback_invocations.append(None)

    monitor = WorkspaceMonitor(tmp_path, on_event=on_event, classifier=WorkspaceChangeClassifier())
    monitor.record_event("/repo/src/foo.py")
    assert callback_invocations == [None]
    assert monitor.event_count == 1


def test_workspace_monitor_rejects_one_arg_callback(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="arity"):

        def on_event(_x: int) -> None:
            pass

        WorkspaceMonitor(tmp_path, on_event=on_event, classifier=WorkspaceChangeClassifier())


def test_workspace_monitor_rejects_three_arg_callback(tmp_path: Path) -> None:

    with pytest.raises(ValueError, match="arity"):

        def on_event(_a: object, _b: object, _c: object) -> None:
            pass

        WorkspaceMonitor(tmp_path, on_event=on_event, classifier=WorkspaceChangeClassifier())


def test_workspace_monitor_classifier_none_uses_default_source_only(tmp_path: Path) -> None:
    callback_invocations: list[tuple[WorkspaceChangeKind, float]] = []

    def on_event(kind: WorkspaceChangeKind, weight: float) -> None:
        callback_invocations.append((kind, weight))

    monitor = WorkspaceMonitor(tmp_path, on_event=on_event)
    monitor.record_event("/repo/agent.log")
    assert callback_invocations == []
    monitor.record_event(str(tmp_path / "src" / "app.py"))
    assert callback_invocations == [(WorkspaceChangeKind.SOURCE, 1.0)]


def test_workspace_monitor_classify_path_helper(tmp_path: Path) -> None:
    monitor = WorkspaceMonitor(tmp_path, classifier=WorkspaceChangeClassifier())
    kind, weight = monitor.classify_path("/repo/src/foo.py")
    assert kind is WorkspaceChangeKind.SOURCE
    assert weight == 1.0


def test_classify_cache_agent_top_level_engine_files() -> None:
    classifier = WorkspaceChangeClassifier()
    for path in (
        "/repo/.agent/.workspace-awareness.json",
        "/repo/.agent/.workspace-awareness.json.tmp",
        "/repo/.agent/.watchdog.lock",
        "/repo/.agent/active_runs.json",
        "/repo/.agent/auto_integrate_in_progress.json",
        "/repo/.agent/rebase_checkpoint.json",
        "/repo/.agent/CURRENT_PROMPT.md",
        "/repo/.agent/PLAN.md",
        "/repo/.agent/PRODUCT_CRITERIA.md",
        "/repo/.agent/mcp.toml",
    ):
        kind, weight = classifier.classify(path)
        assert kind is WorkspaceChangeKind.CACHE, f"{path} should be CACHE"
        assert weight == 0.0


def test_classify_cache_engine_written_agent_trees() -> None:
    classifier = WorkspaceChangeClassifier()
    for path in (
        "/repo/.agent/receipts/run-1/plan.json",
        "/repo/.agent/prompt_history/run-1/prompt.md",
        "/repo/.agent/artifact-formats/plan.md",
    ):
        kind, weight = classifier.classify(path)
        assert kind is WorkspaceChangeKind.CACHE, f"{path} should be CACHE"
        assert weight == 0.0


def test_classify_agent_scoping_keeps_user_files_artifacts_and_worker_trees() -> None:
    classifier = WorkspaceChangeClassifier()
    assert classifier.classify("/repo/src/.workspace-awareness.json") == (
        WorkspaceChangeKind.SOURCE,
        1.0,
    )
    assert classifier.classify("/repo/docs/PLAN.md") == (WorkspaceChangeKind.SOURCE, 1.0)
    assert classifier.classify("/repo/.agent/artifacts/plan.md") == (
        WorkspaceChangeKind.ARTIFACT,
        0.0,
    )
    assert classifier.classify("/repo/.agent/workers/unit-1/src/main.py") == (
        WorkspaceChangeKind.SOURCE,
        1.0,
    )


def test_classify_cache_engine_root_relative_paths_only_under_the_workspace_root() -> None:
    classifier = WorkspaceChangeClassifier()
    root = PurePosixPath("/repo")
    for relative in (
        "checkpoint.json",
        ".agents/mcp_config.json",
        ".cursor/mcp.json",
        ".kimi-code/mcp.json",
    ):
        assert classifier.classify(f"/repo/{relative}", workspace_root=root) == (
            WorkspaceChangeKind.CACHE,
            0.0,
        ), relative
    assert classifier.classify("/repo/checkpoint.json") == (WorkspaceChangeKind.SOURCE, 1.0)
    assert classifier.classify("/repo/src/checkpoint.json", workspace_root=root) == (
        WorkspaceChangeKind.SOURCE,
        1.0,
    )
    assert classifier.classify("/elsewhere/checkpoint.json", workspace_root=root) == (
        WorkspaceChangeKind.SOURCE,
        1.0,
    )


def test_classify_fan_out_worker_unit_engine_paths() -> None:
    classifier = WorkspaceChangeClassifier()
    for path in (
        "/repo/.agent/workers/unit-1/tmp/checkpoint.json",
        "/repo/.agent/workers/unit-1/tmp/plan_prompt.md",
        "/repo/.agent/workers/unit-1/logs/unit-1.jsonl",
        "/repo/.agent/workers/unit-1/handoffs/plan.md",
        "/repo/.agent/workers/unit-1/worker-manifest.json",
        "/repo/.agent/logs/run-1/workers/unit-1.jsonl",
    ):
        kind, weight = classifier.classify(path)
        assert kind is WorkspaceChangeKind.CACHE, f"{path} should be CACHE"
        assert weight == 0.0
    assert classifier.classify("/repo/.agent/workers/unit-1/artifacts/plan.md") == (
        WorkspaceChangeKind.ARTIFACT,
        0.0,
    )
    assert classifier.classify("/repo/.agent/workers/unit-1/src/main.py") == (
        WorkspaceChangeKind.SOURCE,
        1.0,
    )
    assert classifier.classify("/repo/.agent/workers/unit-1/worker-manifest.json.bak") == (
        WorkspaceChangeKind.LOG,
        0.0,
    )
