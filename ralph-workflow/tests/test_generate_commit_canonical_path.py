"""Verify that ``--generate-commit`` routes artifact submission through the
canonical path (receipt → sentinel → artifact file).

The commit plumbing uses ``run_id="commit-plumbing"``. This test exercises
the full agent-attempt-with-recovery loop end-to-end with a fake agent that
calls ``handle_submit_md_artifact`` (the real canonical entry point), then checks
that every expected side-effect is present and that the plumbing module itself
does not contain any ad-hoc writes to ``.agent/receipts/`` or
``.agent/completion_seen_*.json``.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.completion_signals import (
    _check_completion_sentinel,
)
from ralph.cli.commands._commit_attempt_context import CommitAttemptContext
from ralph.config.models import AgentConfig
from ralph.display.context import make_display_context
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact
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
    run_id = "commit-plumbing"

    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:65535/mcp"

    def shutdown(self) -> None:
        return None


class _StubPipelineDeps:
    def __getattr__(self, name: str) -> object:
        del name
        return lambda *args: object()


class _CommitSession:
    session_id = "sess-1"
    run_id = "commit-plumbing"
    drain = "commit"
    broker_secret = None

    def check_capability(self, capability: str) -> object:
        del capability
        return "approved"


def test_generate_commit_end_to_end_uses_canonical_submit(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    plumbing_module = _plumbing_module()
    backend = MemoryBackend()
    workspace = MockWorkspace(tmp_path)

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        del args, kwargs
        session = _CommitSession()
        handle_submit_md_artifact(
            session,
            workspace,
            {
                "artifact_type": "commit_message",
                "content": "---\ntype: commit\nsubject: feat: test\n---\n",
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
        tmp_path,
        plumbing_module._COMMIT_RUN_ID,
        "commit_message",
        backend=backend,
    )

    # RFC-013 P3: completion sentinel is DB-backed. Verify via the
    # completion-signal check which honors both DB and legacy file.
    assert _check_completion_sentinel(tmp_path, plumbing_module._COMMIT_RUN_ID) is True

    artifact_path = tmp_path / ".agent" / "artifacts" / "commit_message.md"
    assert backend.exists(artifact_path)
    assert backend.read_text(artifact_path) == "---\ntype: commit\nsubject: feat: test\n---\n"

    source = Path(plumbing_module.__file__).read_text()
    assert ".agent/receipts/" not in source, (
        "plumbing module must not write receipts directly — "
        "use handle_submit_md_artifact / canonical path"
    )
    assert "completion_seen_" not in source, (
        "plumbing module must not write completion sentinels directly — "
        "use handle_submit_md_artifact / canonical path"
    )
