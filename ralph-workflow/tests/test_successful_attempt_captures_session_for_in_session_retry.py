"""A successful agent attempt must record its session id for in-session retry.

Artifact-validation failures are raised AFTER the agent exits successfully:
the phase handler reads the submitted artifact, finds it invalid, and emits a
recoverable ``PhaseFailureEvent`` with ``retry_in_session=True`` so the agent
resumes its own session and repairs the artifact instead of re-running the
whole phase from a blank prompt.

That resume is conditional on ``state.last_agent_session_id`` being populated,
which only happens when the successful attempt records the transport session
id. When the recording is lost, every artifact-validation retry silently
degrades into a fresh session and the agent redoes the entire phase.

The reducer's ``retry_in_session`` -> resume branch is pinned separately by
``tests/test_phases_retry_in_session_reducer_session_preserving_retry.py``;
this module pins the producer that branch depends on.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke import AgentInvocationError, InvokeOptions
from ralph.config.general_config import GeneralConfig
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline._runner_session import apply_session_capture
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._session_fake_mcp_bridge import _FakeMcpBridge
from tests._session_registry_factory import _RegistryFactory

if TYPE_CHECKING:
    from collections.abc import Iterator

_SESSION_ID = "sess-artifact-repair"


def _run_successful_attempt(tmp_path: Path) -> PipelineEvent:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("write the plan", encoding="utf-8")

    _RegistryFactory._agent_config = AgentConfig(cmd="claude", output_flag="--json-stream")
    display_context = make_display_context()
    pipeline_deps = make_test_pipeline_deps(
        display_context,
        bridge=_FakeMcpBridge(),
        registry_factory=_RegistryFactory.from_config,
    )

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[object]:
        del config, prompt_file, options
        return iter([f"Session ID: {_SESSION_ID}", '{"type":"result"}'])

    return effect_executor_module.execute_agent_effect(
        InvokeAgentEffect(
            agent_name="claude",
            phase="planning",
            prompt_file=str(prompt_file),
        ),
        UnifiedConfig(general=GeneralConfig(max_retries=0)),
        pipeline_deps,
        WorkspaceScope(tmp_path),
        display_context=display_context,
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=AgentInvocationError,
    )


def test_successful_attempt_records_session_id_in_state(tmp_path: Path) -> None:
    """Without this, an in-session retry has no session to resume."""
    assert _run_successful_attempt(tmp_path) == PipelineEvent.AGENT_SUCCESS

    state = apply_session_capture(PipelineState(phase="planning"))

    assert state.last_agent_session_id == _SESSION_ID
