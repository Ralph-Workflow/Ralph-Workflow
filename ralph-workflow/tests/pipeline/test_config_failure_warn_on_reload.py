"""Tests for config failure warning on startup load and workspace_context reload."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from loguru import logger

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate_agent import build_agent_rebase_stop_resolver
from ralph.pipeline.conflict_resolution import RebaseStop
from ralph.pipeline.conflict_resolution.rebase_loop import (
    bind_active_rebase_resolution_session,
)
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.workspace.context import WorkspaceContextError, workspace_context
from ralph.workspace.scope import WorkspaceScope


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
