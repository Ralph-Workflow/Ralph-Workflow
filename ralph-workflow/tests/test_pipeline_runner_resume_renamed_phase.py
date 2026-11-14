from __future__ import annotations

import io
import json
import re
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

import ralph.agents.invoke as agents_invoke
from ralph.cli.commands import run as run_module
from ralph.cli.commands.run import RunPipelineRequest
from ralph.display.context import make_display_context
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import cached_default_policy_bundle
from ralph.recovery.agent_failure_ledger import AgentFailureLedger

if TYPE_CHECKING:
    from pathlib import Path

_RETIRED_PHASE = "development_commit_" + "cleanup"
_EXIT_PREFLIGHT = 2


def test_resume_preflight_refuses_retired_phase_before_any_agent_invocation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    agent_dir = workspace / ".agent"
    agent_dir.mkdir(parents=True)
    (workspace / "PROMPT.md").write_text("Test prompt\n", encoding="utf-8")

    monkeypatch.delenv("RALPH_WORKSPACE", raising=False)
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")
    monkeypatch.chdir(workspace)

    default_bundle = cached_default_policy_bundle()
    assert _RETIRED_PHASE not in default_bundle.pipeline.phases, (
        "the retired phase must be absent from the current default policy bundle"
    )

    stale_state = PipelineState.from_policy(default_bundle.pipeline).copy_with(phase=_RETIRED_PHASE)
    checkpoint = ckpt.Checkpoint()
    checkpoint.save(stale_state)

    checkpoint_path = agent_dir / "checkpoint.json"
    assert checkpoint_path.is_file()
    persisted_payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert persisted_payload["phase"] == _RETIRED_PHASE

    reloaded_state = checkpoint.load()
    assert reloaded_state is not None
    assert reloaded_state.phase == _RETIRED_PHASE

    persisted_checkpoint = checkpoint_path.read_bytes()

    runner_calls: list[str] = []
    agent_invocations: list[str] = []
    ledger_charges: list[str] = []

    def _record_runner_call(*_args: object, **_kwargs: object) -> int:
        runner_calls.append("pipeline-runner")
        return 0

    def _record_agent_invocation(*_args: object, **_kwargs: object) -> object:
        agent_invocations.append("invoke_agent")
        raise AssertionError("no agent may be invoked when the resume preflight refuses")

    def _record_ledger_charge(_ledger: object, *_args: object, **_kwargs: object) -> object:
        ledger_charges.append("ledger-record")
        raise AssertionError("agent failure accounting must not be charged before an invocation")

    monkeypatch.setattr(run_module.state, "run_func", _record_runner_call)
    monkeypatch.setattr(agents_invoke, "invoke_agent", _record_agent_invocation)
    monkeypatch.setattr(effect_executor_module, "invoke_agent", _record_agent_invocation)
    monkeypatch.setattr(AgentFailureLedger, "record", _record_ledger_charge)

    output = io.StringIO()
    display_context = make_display_context(console=Console(file=output, width=200), env={})

    exit_code = run_module.run_pipeline(
        request=RunPipelineRequest(resume=True),
        display_context=display_context,
    )
    rendered = output.getvalue()
    flattened = "".join(rendered.split())

    assert runner_calls == [], "resume preflight must refuse before the pipeline runner starts"
    assert agent_invocations == [], "resume preflight must refuse before any agent invocation"
    assert ledger_charges == [], "resume preflight must not charge agent failure accounting"
    assert exit_code == _EXIT_PREFLIGHT, rendered
    assert "Checkpoint mismatch" in rendered, rendered
    assert _RETIRED_PHASE in flattened, rendered
    named_phases = set(re.findall(r"'([^']+)'", flattened))
    assert named_phases == set(default_bundle.pipeline.phases) | {_RETIRED_PHASE}, rendered
    assert checkpoint_path.read_bytes() == persisted_checkpoint
    final_state = checkpoint.load()
    assert final_state is not None
    assert final_state.phase == _RETIRED_PHASE
