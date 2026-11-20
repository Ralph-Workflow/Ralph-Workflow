from __future__ import annotations

import stat
from dataclasses import replace
from pathlib import Path

import pytest

from ralph.agents.invoke import invoke_agent
from ralph.agents.registry import AgentRegistry
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.recovery.agent_failure_ledger import Cooldown, Eligible
from tests.recovery.test_cooldown_real_invocation_main_phases import _MainPhaseHarness
from tests.recovery.test_development_analysis_consecutive_limit import _run_step

pytestmark = pytest.mark.subprocess_e2e

_PHASE = "planning_analysis"
_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "opencode_transcripts"
    / "minimax_token_plan_limit.jsonl"
)
_PROMPT = "## Submit a `planning_analysis_decision` artifact\n"


def _fake_opencode(root: Path, transcript: Path | None) -> Path:
    script = root.parent / "fake-opencode"
    replay = f"cat '{transcript}'\n" if transcript is not None else ""
    script.write_text(
        f"#!/bin/sh\ncat README.md >/dev/null\nls >/dev/null\n{replay}exit 0\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _harness(root: Path, transcript: Path | None) -> _MainPhaseHarness:
    harness = _MainPhaseHarness(_PHASE, root)
    script = _fake_opencode(root, transcript)
    harness.config = UnifiedConfig(
        agents={
            name: AgentConfig(
                cmd=f"{script} run",
                transport="opencode",
                json_parser="opencode",
                output_flag="--format json",
            )
            for name in harness.agents
        }
    )
    harness.registry = AgentRegistry.from_config(harness.config)
    harness.deps = replace(
        harness.deps,
        invoke_agent=invoke_agent,
        artifact_requirements_resolver=resolve_phase_required_artifact,
    )
    return harness


def _attempt(harness: _MainPhaseHarness, prompt: Path) -> None:
    prompt.write_text(_PROMPT, encoding="utf-8")
    _run_step(harness)


@pytest.mark.timeout_seconds(90)
def test_a_silent_opencode_exit_that_only_reads_files_cools_down_at_the_consecutive_limit(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(tmp_git_repo)
    monkeypatch.chdir(root)
    harness = _harness(root, None)
    prompt = root / ".agent" / "tmp" / f"{_PHASE}_prompt.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    limit = harness.controller.ledger.consecutive_limit

    for _ in range(limit - 1):
        _attempt(harness, prompt)
        assert isinstance(harness.controller.agent_eligibility(_PHASE, harness.primary), Eligible)
    _attempt(harness, prompt)

    status = harness.controller.agent_eligibility(_PHASE, harness.primary)
    assert isinstance(status, Cooldown), harness.controller.ledger.snapshot()


@pytest.mark.timeout_seconds(60)
def test_the_real_token_plan_transcript_starts_an_out_of_credits_cooldown_at_once(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(tmp_git_repo)
    monkeypatch.chdir(root)
    harness = _harness(root, _FIXTURE)
    prompt = root / ".agent" / "tmp" / f"{_PHASE}_prompt.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)

    _attempt(harness, prompt)

    status = harness.controller.agent_eligibility(_PHASE, harness.primary)
    assert isinstance(status, Cooldown)
    assert status.reason == "out_of_credits"
