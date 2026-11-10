"""Tests for config failure warning on startup load and workspace_context reload."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from loguru import logger

from ralph.config import (
    describe_config_failure,
    emit_config_failure_warning,
    reset_fallback_warning_registry,
)
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate_agent import build_agent_rebase_stop_resolver
from ralph.pipeline.conflict_resolution import RebaseStop, RebaseStopResolver
from ralph.pipeline.conflict_resolution.rebase_loop import (
    bind_active_rebase_resolution_session,
)
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.workspace.context import WorkspaceContextError, workspace_context
from ralph.workspace.scope import WorkspaceScope


@pytest.fixture(autouse=True)
def _clear_fallback_warning_registry() -> None:
    """Start every test with an empty warning-deduplication registry."""
    reset_fallback_warning_registry()


def _write_broken_config(root: Path, content: str = "syntax error = [") -> Path:
    """Write a broken ``ralph-workflow.toml`` under ``root`` and return its path."""
    agent_dir = root / ".agent"
    agent_dir.mkdir(parents=True)
    broken_toml = agent_dir / "ralph-workflow.toml"
    broken_toml.write_text(content, encoding="utf-8")
    return broken_toml


def _build_resolver(root: Path) -> RebaseStopResolver:
    """Build the rebase-stop resolver with every collaborator mocked out."""
    mock_display = MagicMock()
    return build_agent_rebase_stop_resolver(
        policy_bundle=MagicMock(),
        registry=MagicMock(),
        display=mock_display,
        config=MagicMock(),
        pipeline_deps=MagicMock(),
        workspace_scope=WorkspaceScope(root=root),
    )


def _reload_once(resolver: RebaseStopResolver, root: Path) -> ResolutionOutcome:
    """Drive one conflict through the resolver and return its outcome."""
    stop = RebaseStop(
        sha="1234567",
        subject="conflict stop",
        conflicted_files=("test.py",),
        stop_index=1,
        stop_cap=10,
    )
    session = ResolutionSession()
    with bind_active_rebase_resolution_session(session):
        outcome = resolver(root, "main", stop)
    assert isinstance(outcome, ResolutionOutcome)
    return outcome


def _config_failure_warnings(records: list[dict[str, Any]], broken_toml: Path) -> list[str]:
    """Return the WARN records that name the broken configuration file."""
    return [
        str(record["message"])
        for record in records
        if f"fix {broken_toml} then run ralph --check-config" in str(record["message"])
    ]


def test_workspace_context_reload_logs_warning_on_config_failure(tmp_path: Path) -> None:
    """Entering workspace_context on reload with broken config raises WorkspaceContextError."""
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True)
    broken_toml = agent_dir / "ralph-workflow.toml"
    broken_toml.write_text("invalid_toml = [unclosed", encoding="utf-8")

    try:
        with workspace_context(tmp_path):
            pass
    except WorkspaceContextError as exc:
        assert exc.file == broken_toml
        assert f"fix {broken_toml} then run ralph --check-config" in exc.fix
    else:
        raise AssertionError("Expected WorkspaceContextError")


def test_resolver_reload_emits_warn_line_on_config_failure(tmp_path: Path) -> None:
    """Per-conflict reload in auto_integrate_agent emits a warning line to the display."""
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True)
    broken_toml = agent_dir / "ralph-workflow.toml"
    broken_toml.write_text("syntax error = [", encoding="utf-8")

    records: list[dict[str, Any]] = []
    sink_id = logger.add(lambda msg: records.append(dict(msg.record)), level="WARNING")

    try:
        mock_display = MagicMock()
        mock_display.emit_warn_line = MagicMock()
        mock_policy = MagicMock()
        mock_registry = MagicMock()
        mock_config = MagicMock()
        mock_deps = MagicMock()
        mock_scope = WorkspaceScope(root=tmp_path)

        resolver = build_agent_rebase_stop_resolver(
            policy_bundle=mock_policy,
            registry=mock_registry,
            display=mock_display,
            config=mock_config,
            pipeline_deps=mock_deps,
            workspace_scope=mock_scope,
        )

        stop = RebaseStop(
            sha="1234567",
            subject="conflict stop",
            conflicted_files=("test.py",),
            stop_index=1,
            stop_cap=10,
        )

        session = ResolutionSession()
        with bind_active_rebase_resolution_session(session):
            outcome = resolver(tmp_path, "main", stop)

        assert isinstance(outcome, ResolutionOutcome)
        assert outcome.succeeded is False
        assert outcome.failure_cause == ResolutionFailureCause.CONFIG_FAILURE

        # Verify logger emitted warning containing fix instructions
        matching_logs = [
            r["message"]
            for r in records
            if f"fix {broken_toml} then run ralph --check-config" in r["message"]
        ]
        assert len(matching_logs) >= 1

        # Verify display received warning line
        warn_calls = [
            call.args[2]
            for call in mock_display.emit_warn_line.call_args_list
            if len(call.args) >= 3
            and f"fix {broken_toml} then run ralph --check-config" in str(call.args[2])
        ]
        assert len(warn_calls) >= 1
    finally:
        logger.remove(sink_id)


def test_unchanged_broken_config_warns_once_across_reloads(tmp_path: Path) -> None:
    """Two reloads of the SAME unchanged broken config produce exactly one WARN."""
    broken_toml = _write_broken_config(tmp_path)

    records: list[dict[str, Any]] = []
    sink_id = logger.add(lambda msg: records.append(dict(msg.record)), level="WARNING")

    try:
        resolver = _build_resolver(tmp_path)

        first = _reload_once(resolver, tmp_path)
        assert first.failure_cause == ResolutionFailureCause.CONFIG_FAILURE
        assert len(_config_failure_warnings(records, broken_toml)) == 1
        record_count_after_first = len(records)

        # Second reload of the SAME unchanged file: no new log line at all.
        second = _reload_once(resolver, tmp_path)
        assert second.failure_cause == ResolutionFailureCause.CONFIG_FAILURE
        assert len(records) == record_count_after_first
        assert len(_config_failure_warnings(records, broken_toml)) == 1
    finally:
        logger.remove(sink_id)


def test_changed_broken_config_warns_again(tmp_path: Path) -> None:
    """A broken config whose CONTENT changed warns again."""
    broken_toml = _write_broken_config(tmp_path)

    records: list[dict[str, Any]] = []
    sink_id = logger.add(lambda msg: records.append(dict(msg.record)), level="WARNING")

    try:
        resolver = _build_resolver(tmp_path)

        first = _reload_once(resolver, tmp_path)
        assert first.failure_cause == ResolutionFailureCause.CONFIG_FAILURE
        assert len(_config_failure_warnings(records, broken_toml)) == 1

        # The operator repairs nothing and edits the file: the content
        # changed, so the failure is a DIFFERENT one and must warn again.
        broken_toml.write_text("another syntax error = {", encoding="utf-8")

        second = _reload_once(resolver, tmp_path)
        assert second.failure_cause == ResolutionFailureCause.CONFIG_FAILURE
        assert len(_config_failure_warnings(records, broken_toml)) == 2
    finally:
        logger.remove(sink_id)


def test_startup_load_reports_failure_through_shared_recorder(tmp_path: Path) -> None:
    """The startup load of a broken config warns once through the shared recorder.

    The startup load itself raises :class:`WorkspaceContextError`; the
    operator-facing WARN is the shared, ``(file, content)``-deduplicated
    recorder's job, so the same broken file cannot produce one warning per
    reload. The recorder is driven here directly because the raise site
    (``ralph/workspace/context.py``) is owned by another lane; once that
    lane calls the same recorder, this test still holds -- the second
    report of the same (file, content) pair is deduplicated.
    """
    broken_toml = _write_broken_config(tmp_path, content="invalid_toml = [unclosed")

    records: list[dict[str, Any]] = []
    sink_id = logger.add(lambda msg: records.append(dict(msg.record)), level="WARNING")

    try:
        with (
            pytest.raises(WorkspaceContextError) as excinfo,
            workspace_context(tmp_path),
        ):
            pass
        startup_error = excinfo.value
        assert startup_error.file == broken_toml

        # Report the failure through the shared recorder. Whether the raise
        # site already reported it (once the pending wiring lands) or not,
        # the same (file, content) pair must end up as ONE warning.
        _emitted = emit_config_failure_warning(
            startup_error.file,
            kind="ralph-workflow",
            error_detail=describe_config_failure(startup_error),
            behaviour=(
                "strict configuration behaviour in effect "
                "(this file has no built-in default fallback)"
            ),
            fix_hint=f"To continue: fix {startup_error.file} then run ralph --check-config",
            exc=startup_error,
        )

        # The same (file, content) pair reported again is deduplicated.
        assert (
            emit_config_failure_warning(
                startup_error.file,
                kind="ralph-workflow",
                error_detail=describe_config_failure(startup_error),
                behaviour=(
                    "strict configuration behaviour in effect "
                    "(this file has no built-in default fallback)"
                ),
                fix_hint=(
                    f"To continue: fix {startup_error.file} then run ralph --check-config"
                ),
                exc=startup_error,
            )
            is False
        )
    finally:
        logger.remove(sink_id)

    matching = _config_failure_warnings(records, broken_toml)
    assert len(matching) == 1
    # The warning names the file, the parse error with its position, the
    # behaviour in effect, and the fix.
    assert str(broken_toml) in matching[0]
    assert "line" in matching[0].lower()
    assert "strict configuration behaviour in effect" in matching[0]
    assert "ralph --check-config" in matching[0]
