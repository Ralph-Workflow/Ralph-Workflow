"""Commit plumbing must route artifact submission through the canonical path.

The commit plumbing uses a fixed ``run_id="commit-plumbing"``. This test pins
that a successful commit-agent attempt produces a run-scoped receipt and a
completion sentinel via the canonical submit entry point, so a future refactor
cannot silently bypass it.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.cli.commands._commit_attempt_context import CommitAttemptContext
from ralph.config.models import AgentConfig
from ralph.display.context import make_display_context
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.tools.artifact import ArtifactHandlerDeps, handle_submit_artifact
from ralph.pipeline.events import PipelineEvent
from tests.test_artifact_format_docs_memory_backend import MemoryBackend
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    import types

    from _pytest.monkeypatch import MonkeyPatch


def _plumbing_module() -> types.ModuleType:
    """Resolve ``commit_plumbing`` lazily so the cycle resolves cleanly."""
    importlib.import_module("ralph.cli.commands.commit")
    return importlib.import_module("ralph.pipeline.plumbing.commit_plumbing")


class _StubBridge:
    """Minimal typed stand-in for the session bridge in the attempt context."""

    run_id = "commit-plumbing"

    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:65535/mcp"

    def shutdown(self) -> None:
        return None


class _StubPipelineDeps:
    """PipelineDeps consumed opaquely by the attempt under test."""

    def __getattr__(self, name: str) -> object:
        del name
        return lambda *args: object()


def test_commit_plumbing_attempt_stamps_receipt_and_sentinel(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A successful commit attempt leaves a receipt and sentinel under the canonical run_id."""
    plumbing_module = _plumbing_module()
    backend = MemoryBackend()
    workspace = MockWorkspace(tmp_path)

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        del args, kwargs
        session = _CommitSession()
        handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "commit_message",
                "content": '{"type": "commit", "subject": "feat: test"}',
            },
            deps=ArtifactHandlerDeps(backend=backend),
        )
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        plumbing_module,
        "execute_agent_effect",
        _fake_execute_agent_effect,
    )

    agent_cfg = AgentConfig(cmd="claude", transport="claude", json_parser="generic")
    attempt_ctx = CommitAttemptContext(
        repo_root=tmp_path,
        verbose=False,
        extra_env={},
        general_config=None,
        bridge=_StubBridge(),
    )

    plumbing_module._run_commit_agent_attempt_with_recovery(
        "agent1",
        agent_cfg,
        prompt_file=str(tmp_path / "PROMPT.md"),
        attempt_context=attempt_ctx,
        display_context=make_display_context(),
        max_retries=1,
        pipeline_deps=_StubPipelineDeps(),
    )

    assert artifact_receipt_present(
        tmp_path, plumbing_module._COMMIT_RUN_ID, "commit_message", backend=backend
    )
    sentinel_path = tmp_path / ".agent" / f"completion_seen_{plumbing_module._COMMIT_RUN_ID}.json"
    assert backend.exists(sentinel_path)


class _CommitSession:
    session_id = "sess-1"
    run_id = "commit-plumbing"
    drain = "commit"

    def check_capability(self, capability: str) -> object:
        del capability
        return "approved"


def test_commit_plumbing_attempt_uses_default_backend_when_not_injected(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """When no deps are injected, the canonical path still writes real on-disk files."""
    plumbing_module = _plumbing_module()

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        del args, kwargs
        session = _CommitSession()
        handle_submit_artifact(
            session,
            MockWorkspace(tmp_path),
            {
                "artifact_type": "commit_message",
                "content": '{"type": "commit", "subject": "feat: test"}',
            },
        )
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        plumbing_module,
        "execute_agent_effect",
        _fake_execute_agent_effect,
    )

    agent_cfg = AgentConfig(cmd="claude", transport="claude", json_parser="generic")
    attempt_ctx = CommitAttemptContext(
        repo_root=tmp_path,
        verbose=False,
        extra_env={},
        general_config=None,
        bridge=_StubBridge(),
    )

    plumbing_module._run_commit_agent_attempt_with_recovery(
        "agent1",
        agent_cfg,
        prompt_file=str(tmp_path / "PROMPT.md"),
        attempt_context=attempt_ctx,
        display_context=make_display_context(),
        max_retries=1,
        pipeline_deps=_StubPipelineDeps(),
    )

    assert artifact_receipt_present(
        tmp_path,
        plumbing_module._COMMIT_RUN_ID,
        "commit_message",
        backend=DEFAULT_FILE_BACKEND,
    )
    sentinel_path = tmp_path / ".agent" / f"completion_seen_{plumbing_module._COMMIT_RUN_ID}.json"
    assert DEFAULT_FILE_BACKEND.exists(sentinel_path)


def test_commit_plumbing_never_directly_writes_receipt_or_sentinel() -> None:
    """No direct file write to .agent/receipts/ or .agent/completion_seen_*.json."""
    source = Path(__file__).parent.parent / "ralph" / "pipeline" / "plumbing" / "commit_plumbing.py"
    text = source.read_text(encoding="utf-8")
    findings: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if ".agent/receipts" in stripped or ".agent/completion_seen_" in stripped:
            findings.append(f"  line {i}: {stripped}")
    assert not findings, (
        "Found direct receipt/sentinel references (expected 0):\n" + "\n".join(findings)
    )


def test_commit_plumbing_receipt_cleared_before_each_attempt() -> None:
    """clear_run_receipts must appear before execute_agent_effect in the recovery function."""
    source = Path(__file__).parent.parent / "ralph" / "pipeline" / "plumbing" / "commit_plumbing.py"
    body = source.read_text(encoding="utf-8")
    func_start = body.find("def _run_commit_agent_attempt_with_recovery(")
    assert func_start != -1, "function not found"
    func_lines = body[func_start:].splitlines()
    clear_line = -1
    execute_line = -1
    for i, line in enumerate(func_lines):
        stripped = line.strip()
        if "clear_run_receipts" in stripped and clear_line == -1:
            clear_line = i
        if "execute_agent_effect" in stripped and execute_line == -1:
            execute_line = i
    assert clear_line != -1, "clear_run_receipts not found in function"
    assert execute_line != -1, "execute_agent_effect not found in function"
    assert clear_line < execute_line, (
        f"clear_run_receipts (offset {clear_line}) must appear "
        f"before execute_agent_effect (offset {execute_line})"
    )


def test_commit_plumbing_run_id_binding_is_stable() -> None:
    """_COMMIT_RUN_ID must be the constant string "commit-plumbing"."""
    plumbing = _plumbing_module()
    assert plumbing._COMMIT_RUN_ID == "commit-plumbing"


def test_commit_plumbing_uses_only_allowlisted_delete() -> None:
    """No ad-hoc unlink/remove of .agent/receipts/* files outside clear_run_receipts."""
    source = Path(__file__).parent.parent / "ralph" / "pipeline" / "plumbing" / "commit_plumbing.py"
    text = source.read_text(encoding="utf-8")
    findings: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if "unlink" in stripped and ".agent/receipts" in stripped:
            findings.append(f"  line {i}: {stripped}")
        if ".remove" in stripped and ".agent/receipts" in stripped:
            findings.append(f"  line {i}: {stripped}")
        if "os.remove" in stripped and ".agent/receipts" in stripped:
            findings.append(f"  line {i}: {stripped}")
    assert not findings, (
        "Found disallowed direct delete calls (expected 0):\n" + "\n".join(findings)
    )
