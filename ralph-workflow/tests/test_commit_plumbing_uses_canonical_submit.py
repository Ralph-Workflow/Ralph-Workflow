"""Commit plumbing must use canonical artifact submission.

The commit CLI has a single canonical path for artifact submission
through the MCP tool handle_submit_artifact. No direct writes to
.agent/receipts/ or .agent/completion_seen_*.json are permitted.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.completion_signals import is_artifact_submitted
from ralph.cli.commands._commit_attempt_context import CommitAttemptContext
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.artifacts.commit_message import COMMIT_MESSAGE_TYPE
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.tools.artifact import handle_submit_artifact
from ralph.pipeline.events import PipelineEvent
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    import types

    from _pytest.monkeypatch import MonkeyPatch


def _plumbing_module() -> types.ModuleType:
    """Lazily resolve commit_plumbing to avoid circular import.

    The cycle is: commit_plumbing imports ralph.cli.commands.commit,
    which imports commit_plumbing. Resolving lazily (after the first
    import_module call) unwinds the cycle cleanly.
    """
    importlib.import_module("ralph.cli.commands.commit")
    return importlib.import_module("ralph.pipeline.plumbing.commit_plumbing")


def test_commit_plumbing_never_directly_writes_receipt_or_sentinel() -> None:
    """Commit plumbing never directly writes to .agent/receipts/ or .agent/completion_seen_*.json.

    This test reads the commit_plumbing.py source and scans for patterns that
    would indicate direct writes to protected paths outside the canonical
    submission block markers.
    """
    plumbing_path = (
        Path(__file__).parent.parent / "ralph" / "pipeline" / "plumbing" / "commit_plumbing.py"
    )
    source_text = plumbing_path.read_text(encoding="utf-8")

    # Patterns that would indicate direct writes to protected paths
    protected_write_patterns = [
        # Direct Path.write_text / write_bytes to .agent/receipts/
        r"\.agent/receipts/.*\.write_(text|bytes)",
        # Direct Path.open for writing to .agent/receipts/
        r"\.agent/receipts/.*\.open.*\w",
        # Direct writes to .agent/completion_seen_*.json
        r"\.agent/completion_seen_.*\.write_(text|bytes)",
        r"\.agent/completion_seen_.*\.open.*\w",
        # Direct json.dump to protected paths
        r"json\.dump.*\.agent/(receipts|completion_seen_)",
    ]

    for pattern in protected_write_patterns:
        matches = re.findall(pattern, source_text)
        assert not matches, f"Found direct write to protected path matching {pattern}: {matches}"


@pytest.mark.timeout_seconds(5)
def test_commit_plumbing_receipt_cleared_before_each_attempt() -> None:
    """Clear run receipts is called at the start of _run_commit_agent_attempt_with_recovery.

    This protects against stale-receipt contamination between attempts.
    """
    # Read the source of _run_commit_agent_attempt_with_recovery
    plumbing_path = (
        Path(__file__).parent.parent / "ralph" / "pipeline" / "plumbing" / "commit_plumbing.py"
    )
    source_text = plumbing_path.read_text(encoding="utf-8")

    # Parse the source to find the function definition
    tree = ast.parse(source_text)

    # Find _run_commit_agent_attempt_with_recovery function
    target_func = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "_run_commit_agent_attempt_with_recovery"
        ):
            target_func = node
            break

    assert target_func is not None, "Function _run_commit_agent_attempt_with_recovery not found"

    # Check if clear_run_receipts is called in the function body
    calls_clear_run_receipts = False
    for node in ast.walk(target_func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "clear_run_receipts"
        ):
            calls_clear_run_receipts = True
            break

    assert calls_clear_run_receipts, (
        "_run_commit_agent_attempt_with_recovery does not call clear_run_receipts"
    )


def test_commit_plumbing_run_id_binding_is_stable() -> None:
    """Receipt is stamped under _COMMIT_RUN_ID and never any other value.

    This protects against the artifact-handoff drift bug.
    """
    plumbing_path = (
        Path(__file__).parent.parent / "ralph" / "pipeline" / "plumbing" / "commit_plumbing.py"
    )
    source_text = plumbing_path.read_text(encoding="utf-8")

    # Check that _COMMIT_RUN_ID is used consistently
    # The constant should be defined and used
    assert "_COMMIT_RUN_ID" in source_text, "_COMMIT_RUN_ID constant not defined"

    # Check that the constant value is used (not a different run_id literal)
    # Look for patterns like run_id="commit-plumbing" or run_id='commit-plumbing'
    # and ensure they don't appear (only the constant should be used)
    non_constant_run_id_patterns = [
        r'run_id\s*=\s*["\']commit-plumbing["\']',
        r'run_id\s*=\s*["\'][^"\']*["\']',  # Any run_id literal
    ]

    # Only check for literals, not the specific value
    for pattern in non_constant_run_id_patterns[1:2]:
        matches = re.findall(pattern, source_text)
        # Filter out the constant definition itself
        filtered_matches = [m for m in matches if "_COMMIT_RUN_ID" not in m]
        assert not filtered_matches, (
            f"Found run_id literal instead of _COMMIT_RUN_ID constant: {filtered_matches}"
        )


def test_commit_plumbing_uses_only_allowlisted_delete() -> None:
    """Only clear_run_receipts is used for deletion in protected paths.

    No ad-hoc unlink of .agent/receipts/* or .agent/completion_seen_* files.
    Other cleanup operations (e.g., .agent/tmp/*) are allowed.
    """
    plumbing_path = (
        Path(__file__).parent.parent / "ralph" / "pipeline" / "plumbing" / "commit_plumbing.py"
    )
    source_text = plumbing_path.read_text(encoding="utf-8")

    # Patterns that would indicate direct deletes to protected paths
    protected_delete_patterns = [
        # Direct Path.unlink/rmdir/remove to .agent/receipts/
        r"\.agent/receipts/.*\.(unlink|rmdir|remove)\(",
        # Direct Path.unlink/rmdir/remove to .agent/completion_seen_
        r"\.agent/completion_seen_.*\.(unlink|rmdir|remove)\(",
        # Direct os.remove/os.unlink/os.rmdir to protected paths
        r"os\.(remove|unlink|rmdir).*\.agent/(receipts|completion_seen_)",
    ]

    for pattern in protected_delete_patterns:
        matches = re.findall(pattern, source_text)
        assert not matches, f"Found direct delete to protected path matching {pattern}: {matches}"


class _CommitSession:
    session_id = "sess-commit"
    run_id = "commit-plumbing"
    drain = "commit"
    broker_secret = None

    def check_capability(self, capability: str) -> object:
        del capability
        return "approved"


def _make_attempt_context(
    tmp_path: Path,
) -> CommitAttemptContext:
    return CommitAttemptContext(
        repo_root=tmp_path,
        verbose=False,
        extra_env={},
        general_config=None,
        bridge=None,
    )


def _make_agent() -> AgentConfig:
    return AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        can_commit=False,
        json_parser="claude",
        transport=AgentTransport.CLAUDE,
    )


def test_commit_plumbing_attempt_stamps_receipt_and_sentinel(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A commit attempt that calls handle_submit_artifact stamps receipt and sentinel."""

    def _fake_execute_agent_effect(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: object,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        raw_sink = kwargs.get("raw_output_sink")
        if isinstance(raw_sink, deque):
            raw_sink.append('{"type":"session","session_id":"sess-commit"}')
            raw_sink.append('{"type":"tool_use","tool":"submit_artifact"}')
            raw_sink.append('{"type":"tool_result","tool":"submit_artifact"}')
            raw_sink.append("Task declared complete: commit done")
        rendered_sink = kwargs.get("rendered_output_sink")
        if isinstance(rendered_sink, deque):
            rendered_sink.append("tool_use: submit_artifact")
            rendered_sink.append("tool_result: submit_artifact")
        handle_submit_artifact(
            _CommitSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": COMMIT_MESSAGE_TYPE,
                "content": json.dumps(
                    {
                        "type": "commit",
                        "subject": "fix(plumbing): test commit",
                        "body": "test",
                    }
                ),
            },
        )
        return PipelineEvent.AGENT_SUCCESS

    plumbing = _plumbing_module()
    monkeypatch.setattr(
        plumbing,
        "execute_agent_effect",
        _fake_execute_agent_effect,
    )

    attempt_ctx = _make_attempt_context(tmp_path)
    agent = _make_agent()
    display_ctx = make_display_context()

    _, session_id, error = plumbing._run_commit_agent_attempt_with_recovery(
        agent_name="test-agent",
        agent=agent,
        prompt_file=str(tmp_path / "PROMPT.md"),
        attempt_context=attempt_ctx,
        display_context=display_ctx,
        max_retries=1,
        pipeline_deps=None,
    )

    assert error is None, f"Commit attempt raised: {error}"
    assert session_id is not None
    assert artifact_receipt_present(tmp_path, plumbing._COMMIT_RUN_ID, COMMIT_MESSAGE_TYPE), (
        "Canonical receipt not found after commit attempt"
    )
    assert is_artifact_submitted(tmp_path, plumbing._COMMIT_RUN_ID, COMMIT_MESSAGE_TYPE), (
        "is_artifact_submitted returned False after canonical submission"
    )


def test_commit_plumbing_attempt_uses_default_backend_when_not_injected(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A commit attempt without a custom backend injection still succeeds.

    This verifies that the plumbing works with the default ArtifactHandlerDeps
    (DEFAULT_ARTIFACT_HANDLER_DEPS) rather than requiring an injected backend.
    """

    def _fake_execute_agent_effect(
        effect: object,
        config: UnifiedConfig,
        pipeline_deps: object,
        workspace_scope: object,
        **kwargs: object,
    ) -> PipelineEvent:
        raw_sink = kwargs.get("raw_output_sink")
        if isinstance(raw_sink, deque):
            raw_sink.append('{"type":"session","session_id":"sess-commit"}')
            raw_sink.append('{"type":"tool_use","tool":"submit_artifact"}')
            raw_sink.append('{"type":"tool_result","tool":"submit_artifact"}')
            raw_sink.append("Task declared complete: commit done")
        rendered_sink = kwargs.get("rendered_output_sink")
        if isinstance(rendered_sink, deque):
            rendered_sink.append("tool_use: submit_artifact")
            rendered_sink.append("tool_result: submit_artifact")
        handle_submit_artifact(
            _CommitSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": COMMIT_MESSAGE_TYPE,
                "content": json.dumps(
                    {
                        "type": "commit",
                        "subject": "fix(plumbing): test commit",
                        "body": "test body",
                    }
                ),
            },
        )
        return PipelineEvent.AGENT_SUCCESS

    plumbing = _plumbing_module()
    monkeypatch.setattr(
        plumbing,
        "execute_agent_effect",
        _fake_execute_agent_effect,
    )

    attempt_ctx = _make_attempt_context(tmp_path)
    agent = _make_agent()
    display_ctx = make_display_context()

    _, _, error = plumbing._run_commit_agent_attempt_with_recovery(
        agent_name="test-agent",
        agent=agent,
        prompt_file=str(tmp_path / "PROMPT.md"),
        attempt_context=attempt_ctx,
        display_context=display_ctx,
        max_retries=1,
        pipeline_deps=None,
    )

    assert error is None
    assert artifact_receipt_present(tmp_path, plumbing._COMMIT_RUN_ID, COMMIT_MESSAGE_TYPE)
    assert is_artifact_submitted(tmp_path, plumbing._COMMIT_RUN_ID, COMMIT_MESSAGE_TYPE)
