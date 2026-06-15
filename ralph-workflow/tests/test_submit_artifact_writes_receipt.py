"""handle_submit_artifact must stamp a run-scoped receipt on success.

This binds "artifact persisted" and "completion signal emitted" into a single
event so the completion gate can never go blind to a successfully submitted
artifact (the failure that produced "Artifact submitted" + "no artifact"
simultaneously).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.tools.artifact import ArtifactHandlerDeps, handle_submit_artifact
from tests.test_artifact_format_docs_memory_backend import MemoryBackend
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    from pathlib import Path

_GRANTED: frozenset[str] = frozenset({"artifact.submit"})


@dataclass
class _Session:
    session_id: str = "sess-1"
    run_id: str = "run-1"
    drain: str = "development_commit"
    granted_capabilities: frozenset[str] = field(default_factory=lambda: _GRANTED)

    def check_capability(self, capability: str) -> bool:
        return capability in self.granted_capabilities


def _commit_params() -> dict[str, object]:
    content = json.dumps({"type": "commit", "subject": "feat(board): add drag grip"})
    return {"artifact_type": "commit_message", "content": content}


def test_submit_artifact_writes_receipt(tmp_path: Path) -> None:
    backend = MemoryBackend()
    workspace = MockWorkspace(tmp_path)
    deps = ArtifactHandlerDeps(backend=backend)

    result = handle_submit_artifact(_Session(), workspace, _commit_params(), deps=deps)

    assert result.is_error is False
    assert artifact_receipt_present(tmp_path, "run-1", "commit_message", backend=backend) is True


def test_submit_artifact_receipt_keyed_by_type(tmp_path: Path) -> None:
    backend = MemoryBackend()
    workspace = MockWorkspace(tmp_path)
    deps = ArtifactHandlerDeps(backend=backend)

    handle_submit_artifact(_Session(), workspace, _commit_params(), deps=deps)

    # No receipt should exist for an artifact type that was never submitted.
    assert artifact_receipt_present(tmp_path, "run-1", "plan", backend=backend) is False
