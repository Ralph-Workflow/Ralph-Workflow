from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.agent_config import AgentConfig
from ralph.config.agent_transport import AgentTransport
from ralph.pipeline.attempt_runner import seamed_dead_agent_probe_runner
from ralph.recovery import dead_agent_probe
from ralph.recovery.canary_probe import (
    CANARY_MARKER_CONTENT,
    CANARY_MARKER_FILENAME,
    CANARY_PROBE_REPLY,
    CANARY_PROBE_TASK,
    CANARY_PROMPT_FILENAME,
    ProbeOutcome,
    ProbeResult,
    run_canary_probe,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.factory import InvokeAgentFn


@dataclass(frozen=True)
class _RecordedInvoke:
    config: AgentConfig
    prompt_file: str
    prompt_text: str | None


def _unified_config() -> UnifiedConfig:
    from ralph.config.models import UnifiedConfig

    return UnifiedConfig.model_validate(
        {
            "general": {"auto_integrate_enabled": False, "max_same_agent_retries": 0},
            "agents": {"codex": {"cmd": "codex"}},
        }
    )


def _make_recording_invoke(
    calls: list[_RecordedInvoke],
    *,
    write_marker: bool,
) -> InvokeAgentFn:
    def fake_invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str]:
        prompt_path = Path(prompt_file)
        calls.append(
            _RecordedInvoke(
                config=config,
                prompt_file=prompt_path.name,
                prompt_text=(
                    prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else None
                ),
            )
        )
        if write_marker:
            assert options is not None
            workspace_path = options.workspace_path
            assert workspace_path is not None
            (workspace_path / CANARY_MARKER_FILENAME).write_text(
                CANARY_MARKER_CONTENT, encoding="utf-8"
            )
        return iter((CANARY_PROBE_REPLY,))

    return fake_invoke


def test_production_seam_uses_the_unpatched_real_canary_probe() -> None:
    assert dead_agent_probe.run_canary_probe is run_canary_probe, (
        "the recovery probe seam must keep calling the real canary runner; a patched "
        "dead_agent_probe.run_canary_probe would make the injected scripted seam vacuous"
    )


def test_production_seam_runs_the_probe_through_the_injected_invoke(tmp_path: Path) -> None:
    calls: list[_RecordedInvoke] = []
    runner = seamed_dead_agent_probe_runner(
        _unified_config(), _make_recording_invoke(calls, write_marker=True)
    )

    assert runner is not None, "a config and an injected invoke must produce a probe runner"
    outcome = runner("development", "codex")

    assert outcome is ProbeOutcome.PASSED, (
        "the real canary runner must judge the marker the injected invoke wrote and pass; "
        f"observed outcome: {outcome!r}"
    )
    assert len(calls) == 1, (
        f"the canary probe must reach the injected invoke exactly once (observed calls: {calls!r})"
    )
    call = calls[0]
    assert call.config.cmd == "codex", (
        f"the probe must resolve the benched agent's config (observed: {call.config!r})"
    )
    assert call.prompt_file == CANARY_PROMPT_FILENAME, (
        f"the injected invoke must receive the canary prompt file (observed: {call.prompt_file!r})"
    )
    assert call.prompt_text == CANARY_PROBE_TASK, (
        "the canary prompt content must be the production probe task "
        f"(observed: {call.prompt_text!r})"
    )


def test_production_seam_judges_a_missing_marker_without_evidence_as_inconclusive(
    tmp_path: Path,
) -> None:
    calls: list[_RecordedInvoke] = []
    runner = seamed_dead_agent_probe_runner(
        _unified_config(), _make_recording_invoke(calls, write_marker=False)
    )

    assert runner is not None
    outcome = runner("development", "codex")

    assert outcome is ProbeOutcome.INCONCLUSIVE, (
        "a missing marker with no recorded agent activity must be inconclusive, not a "
        f"failure verdict (observed outcome: {outcome!r})"
    )
    assert len(calls) == 1, f"the injected invoke must still run (observed calls: {calls!r})"


_PIPE_STUB = Path(__file__).resolve().parent / "fixtures" / "fake_pipe_activity_cli.py"

pytestmark = pytest.mark.subprocess_e2e


def _pipe_config(mode: str) -> AgentConfig:
    return AgentConfig(
        cmd=f"{shlex.quote(sys.executable)} {shlex.quote(str(_PIPE_STUB))} {mode}",
        transport=AgentTransport.CLAUDE,
        model="fake-model",
    )


def _run_pipe_probe(tmp_path: Path, mode: str) -> ProbeResult:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return run_canary_probe(
        _pipe_config(mode),
        workspace_factory=lambda: scratch,
        timeout_s=15,
        extra_env={"ANTHROPIC_API_KEY": "stub-key"},
    )


@pytest.mark.timeout_seconds(30)
def test_pipe_cli_tool_use_without_marker_is_failed_through_recorded_facts(
    tmp_path: Path,
) -> None:
    result = _run_pipe_probe(tmp_path, "tool_use")

    assert result.outcome is ProbeOutcome.FAILED_REPRODUCING_NO_WORK, (
        "the probe attempt's own recorder facts must reach the judge: the fake CLI emitted "
        "a structured tool_use event, so a missing marker is a real failure "
        f"(observed: {result!r})"
    )
    assert result.detail == "marker_missing"
    assert result.marker_written is False


@pytest.mark.timeout_seconds(30)
def test_pipe_cli_prompt_only_text_without_marker_is_inconclusive(tmp_path: Path) -> None:
    result = _run_pipe_probe(tmp_path, "text")

    assert result.outcome is ProbeOutcome.INCONCLUSIVE, (
        "prompt-only text yields no agent-ran evidence, so the judge must stay "
        f"inconclusive (observed: {result!r})"
    )
    assert result.detail == "probe_could_not_run"
    assert result.marker_written is None
