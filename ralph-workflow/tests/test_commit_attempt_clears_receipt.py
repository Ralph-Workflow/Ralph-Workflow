"""Commit plumbing must start each attempt from clean completion state.

The commit path uses a fixed ``run_id="commit-plumbing"`` and reuses it
across retries *and across separate ``--generate-commit`` invocations*
(because the gate keys every receipt on the same value the bridge exposes
as ``run_id``). Two kinds of durable evidence are keyed on that run id —
the artifact submission *receipt* and the ``declare_complete``
*completion sentinel* — and either one left behind by an earlier run
leaks into the next attempt as false completion: the gate sees the
evidence, declares the phase terminal, and kills the agent process
before it emits a single line.

Both are cleared per attempt, mirroring the AGY branch's per-attempt
clear in :mod:`ralph.agents.invoke`. The attempt also declares the
``commit_message`` artifact contract on the invocation, so the gate
requires a fresh receipt and cannot be satisfied by a sentinel alone.
These are the regression tests that pin those contracts.
"""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING

from ralph.agents.completion_signals import evaluate_completion
from ralph.cli.commands._commit_attempt_context import CommitAttemptContext
from ralph.config.models import AgentConfig
from ralph.display.context import make_display_context
from ralph.mcp.artifacts.commit_message import COMMIT_MESSAGE_TYPE
from ralph.mcp.artifacts.state_db import RunStateDB
from ralph.phases.required_artifacts import RequiredArtifact
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


def _seed_completion_sentinel(workspace_root: Path, run_id: str) -> None:
    """Pre-populate the sentinel a prior run's ``declare_complete`` wrote."""
    db = RunStateDB(workspace_root)
    try:
        db.upsert_completion_sentinel(run_id, None)
    finally:
        db.close()


def _sentinel_holds(workspace_root: Path, run_id: str) -> bool:
    """Report whether the completion gate still sees a sentinel for ``run_id``."""
    signals = evaluate_completion(workspace_root, run_id=run_id)
    return signals.completion_sentinel_evidence.holds


def _run_attempt(
    plumbing_module: types.ModuleType,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    *,
    on_effect: Callable[[dict[str, object]], None] | None = None,
) -> None:
    """Drive one commit attempt with the agent invocation stubbed out."""

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        del args
        if on_effect is not None:
            on_effect(kwargs)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        plumbing_module,
        "execute_agent_effect",
        _fake_execute_agent_effect,
    )

    plumbing_module._run_commit_agent_attempt_with_recovery(
        "agent1",
        AgentConfig(cmd="claude", transport="claude", json_parser="generic"),
        prompt_file=str(tmp_path / "PROMPT.md"),
        attempt_context=CommitAttemptContext(
            repo_root=tmp_path,
            verbose=False,
            extra_env={},
            general_config=None,
            bridge=_StubBridge(),
        ),
        display_context=make_display_context(),
        max_retries=1,
        pipeline_deps=_StubPipelineDeps(),
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

    _run_attempt(plumbing_module, tmp_path, monkeypatch)

    assert not receipt_path.exists(), (
        "stale receipt must be deleted by the per-attempt clear; the gate "
        "would otherwise see 'already submitted' and skip the new attempt"
    )


def test_commit_attempt_clears_stale_completion_sentinel(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A prior run's completion sentinel MUST NOT survive into a new attempt.

    ``declare_complete`` writes a sentinel keyed on the fixed commit run id,
    so a --generate-commit run that succeeds leaves one behind for the next
    one to inherit. An inherited sentinel makes the completion gate call the
    phase terminal within milliseconds of spawn, killing every drain agent
    before it can produce a commit_message artifact.
    """
    plumbing_module = _plumbing_module()
    run_id = plumbing_module._COMMIT_RUN_ID
    _seed_completion_sentinel(tmp_path, run_id)
    assert _sentinel_holds(tmp_path, run_id), (
        "test setup: the seeded sentinel should be visible to the gate"
    )

    _run_attempt(plumbing_module, tmp_path, monkeypatch)

    assert not _sentinel_holds(tmp_path, run_id), (
        "stale completion sentinel must be cleared by the per-attempt clear; "
        "the gate would otherwise declare the phase complete before the agent runs"
    )


def test_commit_attempt_declares_commit_artifact_contract(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """The attempt MUST carry the commit_message artifact contract.

    Without it the invocation reports ``artifact_required=False`` and the
    completion gate settles for a bare sentinel, so no submitted artifact is
    ever required of the agent.
    """
    plumbing_module = _plumbing_module()
    seen: list[object] = []

    _run_attempt(
        plumbing_module,
        tmp_path,
        monkeypatch,
        on_effect=lambda kwargs: seen.append(kwargs.get("required_artifact")),
    )

    assert seen, "the attempt must execute the agent effect"
    required_artifact = seen[0]
    assert isinstance(required_artifact, RequiredArtifact), (
        "commit attempts must declare a required artifact so the completion "
        "gate demands a fresh submission receipt"
    )
    assert required_artifact.artifact_type == COMMIT_MESSAGE_TYPE
    assert required_artifact.artifact_required is True
