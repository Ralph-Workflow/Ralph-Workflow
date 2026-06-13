"""Black-box tests for WorkspaceChangeClassifier and WorkspaceMonitor integration.

These tests cover the new class-aware workspace channel: source-code
changes are valued MUCH higher than log/cache/artifact/other changes
when deciding whether a workspace file change should defer the
NO_OUTPUT_DEADLINE verdict.

Rule order (fixed; see WorkspaceChangeClassifier.classify):
  1. CACHE parent walk (CACHE_PARENT_DIRS, with `.agent/tmp`/`.agent/raw`
     but NOT `.agent` top-level)
  2. CACHE filename glob (`completion_seen_*.json`)
  3. ARTIFACT parent walk (`.agent/artifacts`)
  4. LOG name/extension (`.log`, `.tmp`, `.bak`, `.swp`, `~`, `.pyc`, `.pyo`)
  5. SOURCE extension membership (default weight 1.0)
  6. OTHER (default weight 0.0)

Weight semantics are BINARY: 0.0 means the change is DROPPED; 1.0
means the change counts as full activity. Intermediate values are
rejected by the validator today.

The WorkspaceMonitor integration tests assert that a production-style
WorkspaceMonitor with a real classifier drops weight-0 events without
invoking the on_event callback, while passing through (kind, weight)
for weight-1 events. The __post_init__ arity check rejects 1-arg and
3+ arg callbacks with a clear ValueError.
"""

from __future__ import annotations

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
    _normalize_workspace_change_weights,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# (a) WorkspaceChangeKind enum and default policy
# ---------------------------------------------------------------------------


def test_workspace_change_kind_enum_members() -> None:
    """WorkspaceChangeKind has exactly 5 members: source, log, cache, artifact, other."""
    values = {k.value for k in WorkspaceChangeKind}
    assert values == {"source", "log", "cache", "artifact", "other"}


def test_default_weights_source_is_one() -> None:
    """``DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS['source']`` is 1.0 by default."""
    assert DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS["source"] == 1.0


def test_default_weights_log_is_zero() -> None:
    """``DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS['log']`` is 0.0 by default."""
    assert DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS["log"] == 0.0


def test_default_weights_cache_is_zero() -> None:
    """``DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS['cache']`` is 0.0 by default."""
    assert DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS["cache"] == 0.0


def test_default_weights_artifact_is_zero() -> None:
    """``DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS['artifact']`` is 0.0 by default."""
    assert DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS["artifact"] == 0.0


def test_default_weights_other_is_zero() -> None:
    """``DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS['other']`` is 0.0 by default."""
    assert DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS["other"] == 0.0


def test_default_weights_are_all_binary() -> None:
    """All default weights are in {0.0, 1.0}; intermediate values are absent."""
    for value in DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS.values():
        assert value in {0.0, 1.0}, f"default weight {value!r} is not binary"


# ---------------------------------------------------------------------------
# (b) Classifier construction and validation
# ---------------------------------------------------------------------------


def test_classifier_default_constructor_uses_safe_defaults() -> None:
    """WorkspaceChangeClassifier() with no args uses DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS."""
    classifier = WorkspaceChangeClassifier()
    assert classifier.weights == dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)


def test_classifier_rejects_unknown_key() -> None:
    """An unknown weights key raises ValueError naming the offending key."""
    with pytest.raises(ValueError, match="not_a_kind"):
        WorkspaceChangeClassifier(weights={"not_a_kind": 1.0})


def test_classifier_rejects_intermediate_weight() -> None:
    """An intermediate weight (0.5) raises ValueError; only {0.0, 1.0} are allowed."""
    with pytest.raises(ValueError, match="binary weight"):
        WorkspaceChangeClassifier(weights={"source": 0.5})


def test_classifier_rejects_weight_above_one() -> None:
    """A weight above 1.0 (e.g. 2.0) raises ValueError."""
    with pytest.raises(ValueError, match="binary weight"):
        WorkspaceChangeClassifier(weights={"source": 2.0})


def test_classifier_rejects_negative_weight() -> None:
    """A negative weight raises ValueError."""
    with pytest.raises(ValueError, match="binary weight"):
        WorkspaceChangeClassifier(weights={"source": -0.1})


# ---------------------------------------------------------------------------
# (c) Rule order: CACHE parent walk
# ---------------------------------------------------------------------------


def test_classify_cache_parent_dot_git() -> None:
    """A path under ``.git`` is classified as CACHE with weight 0.0."""
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/.git/HEAD")
    assert kind is WorkspaceChangeKind.CACHE
    assert weight == 0.0


def test_classify_cache_parent_pycache() -> None:
    """A path under ``__pycache__`` is CACHE with weight 0.0."""
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/src/__pycache__/foo.cpython-312.pyc")
    assert kind is WorkspaceChangeKind.CACHE
    assert weight == 0.0


def test_classify_cache_parent_node_modules() -> None:
    """A path under ``node_modules`` is CACHE with weight 0.0."""
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/node_modules/lodash/index.js")
    assert kind is WorkspaceChangeKind.CACHE
    assert weight == 0.0


def test_classify_cache_parent_dot_venv() -> None:
    """A path under ``.venv`` is CACHE with weight 0.0."""
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/.venv/lib/python3.12/site.py")
    assert kind is WorkspaceChangeKind.CACHE
    assert weight == 0.0


def test_classify_cache_parent_agent_tmp() -> None:
    """A path under ``.agent/tmp`` is CACHE with weight 0.0."""
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/.agent/tmp/foo.log")
    assert kind is WorkspaceChangeKind.CACHE
    assert weight == 0.0


def test_classify_cache_parent_agent_raw() -> None:
    """A path under ``.agent/raw`` is CACHE with weight 0.0."""
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/.agent/raw/stream.bin")
    assert kind is WorkspaceChangeKind.CACHE
    assert weight == 0.0


def test_cache_parent_dirs_does_not_include_dot_agent_top_level() -> None:
    """The ``.agent`` top-level is NOT in CACHE_PARENT_DIRS so ``.agent/artifacts``
    can be classified as ARTIFACT (closes PA-001 from the prior plan)."""
    assert ".agent" not in CACHE_PARENT_DIRS
    # But .agent/tmp and .agent/raw are explicit CACHE parents.
    assert ".agent/tmp" in CACHE_PARENT_DIRS
    assert ".agent/raw" in CACHE_PARENT_DIRS


# ---------------------------------------------------------------------------
# (d) Rule order: CACHE filename glob
# ---------------------------------------------------------------------------


def test_classify_cache_completion_seen_sentinel() -> None:
    """A ``completion_seen_<id>.json`` sentinel is CACHE with weight 0.0.

    This is the WorkspaceMonitor-internal sentinel written by AGY; it
    must NOT count as workspace activity because the agent's "I'm done"
    signal is not a code change.
    """
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/.agent/completion_seen_abc-123.json")
    assert kind is WorkspaceChangeKind.CACHE
    assert weight == 0.0


def test_cache_filename_globs_contains_completion_seen() -> None:
    """``CACHE_FILENAME_GLOBS`` includes the ``completion_seen_*.json`` glob."""
    assert "completion_seen_*.json" in CACHE_FILENAME_GLOBS


# ---------------------------------------------------------------------------
# (e) Rule order: ARTIFACT parent walk
# ---------------------------------------------------------------------------


def test_classify_artifact_agent_artifacts() -> None:
    """A path under ``.agent/artifacts`` is ARTIFACT with weight 0.0.

    This is the PA-001 closure: pre-fix, the ``.agent`` top-level was
    in CACHE_PARENT_DIRS, so ``.agent/artifacts/plan.json`` was
    classified CACHE not ARTIFACT. The fixed rule order checks
    ``.agent/tmp``/``.agent/raw`` explicitly and reserves
    ``.agent/artifacts`` for ARTIFACT.
    """
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/.agent/artifacts/plan.json")
    assert kind is WorkspaceChangeKind.ARTIFACT
    assert weight == 0.0


def test_artifact_parent_dirs_contains_dot_agent_artifacts() -> None:
    """``ARTIFACT_PARENT_DIRS`` contains exactly ``.agent/artifacts``."""
    assert frozenset({".agent/artifacts"}) == ARTIFACT_PARENT_DIRS


# ---------------------------------------------------------------------------
# (f) Rule order: LOG name/extension
# ---------------------------------------------------------------------------


def test_classify_log_extension() -> None:
    """A ``*.log`` file is LOG with weight 0.0."""
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/agent.log")
    assert kind is WorkspaceChangeKind.LOG
    assert weight == 0.0


def test_classify_log_tmp_extension() -> None:
    """A ``*.tmp`` file is LOG with weight 0.0."""
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/scratch.tmp")
    assert kind is WorkspaceChangeKind.LOG
    assert weight == 0.0


def test_classify_log_pyc_extension() -> None:
    """A ``*.pyc`` file is LOG with weight 0.0 (compiled bytecode is treated as log)."""
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/foo.pyc")
    assert kind is WorkspaceChangeKind.LOG
    assert weight == 0.0


def test_log_suffixes_includes_common_log_types() -> None:
    """``LOG_SUFFIXES`` includes all the common log/temp extensions."""
    assert ".log" in LOG_SUFFIXES
    assert ".tmp" in LOG_SUFFIXES
    assert ".bak" in LOG_SUFFIXES
    assert ".swp" in LOG_SUFFIXES
    assert "~" in LOG_SUFFIXES
    assert ".pyc" in LOG_SUFFIXES
    assert ".pyo" in LOG_SUFFIXES


# ---------------------------------------------------------------------------
# (g) Rule order: SOURCE extension
# ---------------------------------------------------------------------------


def test_classify_source_python() -> None:
    """A ``*.py`` file is SOURCE with weight 1.0 by default."""
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/src/foo.py")
    assert kind is WorkspaceChangeKind.SOURCE
    assert weight == 1.0


def test_classify_source_rust() -> None:
    """A ``*.rs`` file is SOURCE with weight 1.0 by default."""
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/src/main.rs")
    assert kind is WorkspaceChangeKind.SOURCE
    assert weight == 1.0


def test_classify_source_typescript() -> None:
    """A ``*.ts`` file is SOURCE with weight 1.0 by default."""
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/src/app.ts")
    assert kind is WorkspaceChangeKind.SOURCE
    assert weight == 1.0


def test_classify_source_markdown() -> None:
    """A ``*.md`` file is SOURCE with weight 1.0 by default.

    The conservative default policy treats documentation files as
    source (they are real content, not transient log/cache/artifact
    output).
    """
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/docs/README.md")
    assert kind is WorkspaceChangeKind.SOURCE
    assert weight == 1.0


def test_source_extensions_includes_common_languages() -> None:
    """``SOURCE_EXTENSIONS`` covers Python, Rust, Go, JS/TS, Java, and more."""
    assert ".py" in SOURCE_EXTENSIONS
    assert ".rs" in SOURCE_EXTENSIONS
    assert ".go" in SOURCE_EXTENSIONS
    assert ".ts" in SOURCE_EXTENSIONS
    assert ".js" in SOURCE_EXTENSIONS
    assert ".java" in SOURCE_EXTENSIONS


# ---------------------------------------------------------------------------
# (h) Rule order: OTHER
# ---------------------------------------------------------------------------


def test_classify_other_unknown_extension() -> None:
    """A file with an unknown extension is OTHER with weight 0.0 by default."""
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/blob.xyz123")
    assert kind is WorkspaceChangeKind.OTHER
    assert weight == 0.0


def test_classify_other_binary_blob() -> None:
    """A file with no extension is OTHER with weight 0.0 by default."""
    classifier = WorkspaceChangeClassifier()
    kind, weight = classifier.classify("/repo/bin/exec")
    assert kind is WorkspaceChangeKind.OTHER
    assert weight == 0.0


# ---------------------------------------------------------------------------
# (i) Custom weight overrides
# ---------------------------------------------------------------------------


def test_custom_weights_log_can_be_activated() -> None:
    """An operator can opt log files in by setting ``weights['log'] = 1.0``.

    This is the migration path for operators who relied on log-file
    activity to defer the NO_OUTPUT_DEADLINE verdict.
    """
    classifier = WorkspaceChangeClassifier(weights={"source": 1.0, "log": 1.0})
    kind, weight = classifier.classify("/repo/agent.log")
    assert kind is WorkspaceChangeKind.LOG
    assert weight == 1.0


def test_custom_weights_source_can_be_disabled() -> None:
    """An operator can opt source OUT by setting ``weights['source'] = 0.0``."""
    classifier = WorkspaceChangeClassifier(weights={"source": 0.0})
    kind, weight = classifier.classify("/repo/src/foo.py")
    assert kind is WorkspaceChangeKind.SOURCE
    assert weight == 0.0


def test_normalize_workspace_change_weights_fills_defaults() -> None:
    """``_normalize_workspace_change_weights`` fills missing keys from defaults."""
    normalized = _normalize_workspace_change_weights({"log": 1.0})
    assert normalized == {
        "source": 1.0,
        "log": 1.0,
        "cache": 0.0,
        "artifact": 0.0,
        "other": 0.0,
    }


def test_normalize_workspace_change_weights_handles_none() -> None:
    """``_normalize_workspace_change_weights(None)`` returns a copy of the defaults."""
    normalized = _normalize_workspace_change_weights(None)
    assert normalized == dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)


# ---------------------------------------------------------------------------
# (j) WorkspaceMonitor integration: dropped weight-0 events
# ---------------------------------------------------------------------------


def test_workspace_monitor_drops_log_events() -> None:
    """A log file change is classified LOG and DROPPED; the on_event callback
    is not invoked and the monitor's ``last_event_at`` stays None."""
    callback_invocations: list[tuple[WorkspaceChangeKind, float]] = []

    def on_event(kind: WorkspaceChangeKind, weight: float) -> None:
        callback_invocations.append((kind, weight))

    monitor = WorkspaceMonitor(
        "/tmp",  # workspace_path (unused; the classifier doesn't consult it)
        on_event=on_event,
        classifier=WorkspaceChangeClassifier(),
    )
    monitor.record_event("/repo/agent.log")
    assert callback_invocations == []
    assert monitor.last_event_at is None
    assert monitor.event_count == 0


def test_workspace_monitor_drops_cache_events(tmp_path: Path) -> None:
    """A ``__pycache__`` file change is CACHE and DROPPED."""
    callback_invocations: list[tuple[WorkspaceChangeKind, float]] = []

    def on_event(kind: WorkspaceChangeKind, weight: float) -> None:
        callback_invocations.append((kind, weight))

    monitor = WorkspaceMonitor(tmp_path, on_event=on_event, classifier=WorkspaceChangeClassifier())
    monitor.record_event("/repo/__pycache__/foo.pyc")
    assert callback_invocations == []
    assert monitor.last_event_at is None


def test_workspace_monitor_drops_artifact_events(tmp_path: Path) -> None:
    """A ``.agent/artifacts/plan.json`` change is ARTIFACT and DROPPED.

    This is the PA-001 closure: pre-fix the .agent top-level was in
    CACHE_PARENT_DIRS and the test for the artifact path failed.
    """
    callback_invocations: list[tuple[WorkspaceChangeKind, float]] = []

    def on_event(kind: WorkspaceChangeKind, weight: float) -> None:
        callback_invocations.append((kind, weight))

    monitor = WorkspaceMonitor(tmp_path, on_event=on_event, classifier=WorkspaceChangeClassifier())
    monitor.record_event("/repo/.agent/artifacts/plan.json")
    assert callback_invocations == []
    assert monitor.last_event_at is None


def test_workspace_monitor_passes_source_events_with_real_kind(tmp_path: Path) -> None:
    """A source-code change is classified SOURCE and the on_event callback
    receives the real (SOURCE, 1.0) pair. This is the production binding
    contract: the watchdog's per-kind counter receives the real kind."""
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


# ---------------------------------------------------------------------------
# (k) WorkspaceMonitor integration: 0-arg callback still works (legacy)
# ---------------------------------------------------------------------------


def test_workspace_monitor_accepts_0_arg_callback(tmp_path: Path) -> None:
    """A 0-arg callback (the legacy production binding) is still accepted
    when a classifier drops every event. The classifier does not need
    to know the callback arity; the monitor's ``__post_init__`` does.

    The on_event callback is invoked with no args in this case, mirroring
    the pre-fix 0-arg binding at line 307 of ``_process_reader.py``.
    """
    callback_invocations: list[None] = []

    def on_event() -> None:
        callback_invocations.append(None)

    monitor = WorkspaceMonitor(tmp_path, on_event=on_event, classifier=WorkspaceChangeClassifier())
    # A source event passes through; the 0-arg callback is invoked
    # with no positional args (the production 0-arg binding ignores
    # both kind and weight).
    monitor.record_event("/repo/src/foo.py")
    assert callback_invocations == [None]
    assert monitor.event_count == 1


# ---------------------------------------------------------------------------
# (l) WorkspaceMonitor integration: arity check rejects bad callbacks
# ---------------------------------------------------------------------------


def test_workspace_monitor_rejects_one_arg_callback(tmp_path: Path) -> None:
    """A 1-arg callback is rejected at construction time with a clear
    ValueError naming the offending arity (1). Only 0-arg (legacy) and
    2-arg (production-style) are accepted."""
    with pytest.raises(ValueError, match="arity"):

        def on_event(_x: int) -> None:
            pass

        WorkspaceMonitor(tmp_path, on_event=on_event, classifier=WorkspaceChangeClassifier())


def test_workspace_monitor_rejects_three_arg_callback(tmp_path: Path) -> None:
    """A 3-arg callback is rejected at construction time."""

    with pytest.raises(ValueError, match="arity"):

        def on_event(_a: object, _b: object, _c: object) -> None:
            pass

        WorkspaceMonitor(tmp_path, on_event=on_event, classifier=WorkspaceChangeClassifier())


# ---------------------------------------------------------------------------
# (m) WorkspaceMonitor integration: classifier=None preserves legacy behavior
# ---------------------------------------------------------------------------


def test_workspace_monitor_classifier_none_legacy_behavior(tmp_path: Path) -> None:
    """When ``classifier=None`` is passed (or omitted), every event
    is classified OTHER with weight 1.0 and the on_event callback is
    invoked with ``(OTHER, 1.0)``. This is the legacy behavior for
    callers that do not opt into the new class-aware verdict.
    """
    callback_invocations: list[tuple[WorkspaceChangeKind, float]] = []

    def on_event(kind: WorkspaceChangeKind, weight: float) -> None:
        callback_invocations.append((kind, weight))

    monitor = WorkspaceMonitor(tmp_path, on_event=on_event)  # classifier omitted
    monitor.record_event("/repo/agent.log")
    assert callback_invocations == [(WorkspaceChangeKind.OTHER, 1.0)]


# ---------------------------------------------------------------------------
# (n) WorkspaceMonitor integration: classify_path helper
# ---------------------------------------------------------------------------


def test_workspace_monitor_classify_path_helper(tmp_path: Path) -> None:
    """``WorkspaceMonitor.classify_path`` exposes the classifier for
    direct callers (e.g. tests, dry-run checks)."""
    monitor = WorkspaceMonitor(tmp_path, classifier=WorkspaceChangeClassifier())
    kind, weight = monitor.classify_path("/repo/src/foo.py")
    assert kind is WorkspaceChangeKind.SOURCE
    assert weight == 1.0
