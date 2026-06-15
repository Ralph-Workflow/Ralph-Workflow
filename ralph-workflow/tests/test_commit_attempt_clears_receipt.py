"""Commit plumbing must clear stale receipts at the start of each attempt.

The commit path uses a fixed ``run_id="commit-plumbing"`` and reuses it
across retries (because the gate keys every receipt on the same value the
bridge exposes as ``run_id``). A prior attempt that left a successful
receipt on disk would otherwise leak into a new attempt and cause false
completion: the gate would see "receipt present" and declare done, even
when the new attempt's agent never submitted an artifact.

The fix mirrors the AGY branch's per-attempt receipt clear in
:mod:`ralph.agents.invoke` so a retry that reuses ``run_id`` cannot
inherit the prior attempt's success signal. This is the regression
test that pins the contract.
"""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING

from ralph.cli.commands._commit_attempt_context import CommitAttemptContext
from ralph.config.models import AgentConfig
from ralph.display.context import make_display_context
from ralph.mcp.artifacts.completion_receipts import clear_run_receipts
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.pipeline.events import PipelineEvent

if TYPE_CHECKING:
    import types
    from collections.abc import Callable
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch


def _plumbing_module() -> types.ModuleType:
    """Resolve ``commit_plumbing`` lazily so the cycle resolves cleanly.

    See ``tests/test_commit_bridge_run_id_binding.py`` for the rationale.
    The cycle is benign once ``ralph.cli.commands.commit`` has finished
    loading, so we trigger that first via ``importlib`` and then resolve
    ``commit_plumbing`` once the cycle is unwound.
    """
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
    """PipelineDeps is consumed opaquely by the attempt under test; the
    typed stub keeps the test fully typed while leaving the production
    call site unexercised (we mock ``execute_agent_effect`` anyway)."""

    def __getattr__(self, name: str) -> Callable[..., object]:
        return lambda *args: object()


def _seed_receipt(workspace_root: Path, run_id: str, artifact_type: str) -> None:
    """Pre-populate a receipt as if a prior --generate-commit run had succeeded."""
    receipt_path = workspace_root / ".agent" / "receipts" / run_id / f"{artifact_type}.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps({"run_id": run_id, "artifact_type": artifact_type}),
        encoding="utf-8",
    )


def test_commit_attempt_clears_stale_receipt(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A prior run's receipt MUST be cleared at the start of a new commit attempt.

    The pre-existing receipt simulates the case where a prior --generate-commit
    run succeeded and the user re-runs the command (e.g. a retry of a
    second attempt after a failure that did not delete the receipt). The
    new attempt must start with a clean slate for ``run_id="commit-plumbing"``
    so the gate's "receipt present → done" check cannot be satisfied by
    leftover state.
    """
    plumbing_module = _plumbing_module()
    _seed_receipt(tmp_path, plumbing_module._COMMIT_RUN_ID, "commit_message")
    receipt_path = (
        tmp_path / ".agent" / "receipts" / plumbing_module._COMMIT_RUN_ID / "commit_message.json"
    )
    assert receipt_path.exists(), "test setup: receipt should be on disk before the attempt"

    clear_calls: list[tuple[Path, str]] = []

    def _fake_clear_run_receipts(
        workspace_root: Path,
        run_id: str,
        *,
        backend: object = None,
    ) -> None:
        clear_calls.append((workspace_root, run_id))
        clear_run_receipts(workspace_root, run_id, backend=DEFAULT_FILE_BACKEND)

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        return PipelineEvent.AGENT_SUCCESS

    agent_cfg = AgentConfig(cmd="claude", transport="claude", json_parser="generic")
    attempt_ctx = CommitAttemptContext(
        repo_root=tmp_path,
        verbose=False,
        extra_env={},
        general_config=None,
        bridge=_StubBridge(),
    )

    monkeypatch.setattr(
        plumbing_module,
        "clear_run_receipts",
        _fake_clear_run_receipts,
    )
    monkeypatch.setattr(
        plumbing_module,
        "execute_agent_effect",
        _fake_execute_agent_effect,
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

    assert clear_calls, "commit attempt must call clear_run_receipts at the start"
    assert clear_calls[0][0] == tmp_path
    assert clear_calls[0][1] == plumbing_module._COMMIT_RUN_ID
    assert not receipt_path.exists(), (
        "stale receipt must be deleted by the per-attempt clear; the gate "
        "would otherwise see 'already submitted' and skip the new attempt"
    )
