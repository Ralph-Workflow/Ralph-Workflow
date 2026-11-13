from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

from ralph.config.agent_config import AgentConfig
from ralph.recovery.canary_probe import (
    CANARY_MARKER_CONTENT,
    CANARY_MARKER_FILENAME,
    CANARY_PROBE_PHASE,
    CANARY_PROMPT_FILENAME,
    DEFAULT_PROBE_TIMEOUT_S,
    PROBE_INCONCLUSIVE_VERDICT,
    ProbeOutcome,
    ProbeResult,
    run_canary_probe,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.invoke import InvokeOptions


class _FakeInvoke(Protocol):
    def __call__(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str] | None: ...


@dataclass(frozen=True)
class _Call:
    config: AgentConfig
    prompt_file: str
    options: InvokeOptions | None


@dataclass(frozen=True)
class _Case:
    name: str
    behaviour: str
    expected: ProbeOutcome
    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S
    budget_exhausted: bool = False


_CASES: tuple[_Case, ...] = (
    _Case(
        name="marker_written",
        behaviour="write_marker",
        expected=ProbeOutcome.PASSED,
    ),
    _Case(
        name="marker_missing",
        behaviour="no_marker",
        expected=ProbeOutcome.FAILED_REPRODUCING_NO_WORK,
    ),
    _Case(
        name="infrastructure_error",
        behaviour="raise",
        expected=ProbeOutcome.INCONCLUSIVE,
    ),
    _Case(
        name="timeout",
        behaviour="block",
        expected=ProbeOutcome.INCONCLUSIVE,
        timeout_s=0.1,
    ),
    _Case(
        name="budget_exhausted",
        behaviour="write_marker",
        expected=ProbeOutcome.INCONCLUSIVE,
        budget_exhausted=True,
    ),
)


def _agent_config() -> AgentConfig:
    return AgentConfig(cmd="fake-agent", model="fake-model")


def _make_invoke(behaviour: str, calls: list[_Call]) -> _FakeInvoke:
    def _invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str] | None:
        calls.append(_Call(config=config, prompt_file=prompt_file, options=options))
        if behaviour == "block":
            threading.Event().wait(1.0)
            return iter(())
        if behaviour == "raise":
            raise RuntimeError("infrastructure failure")
        if behaviour == "write_marker":
            assert options is not None
            workspace_path = options.workspace_path
            assert workspace_path is not None
            (workspace_path / CANARY_MARKER_FILENAME).write_text(
                CANARY_MARKER_CONTENT, encoding="utf-8"
            )
        return iter(())

    return _invoke


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


@pytest.mark.parametrize("case", _CASES, ids=[case.name for case in _CASES])
def test_probe_reports_the_expected_outcome(case: _Case, tmp_path: Path) -> None:
    calls: list[_Call] = []
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke(case.behaviour, calls),
        workspace_factory=lambda: scratch,
        timeout_s=case.timeout_s,
        budget_exhausted=(lambda: True) if case.budget_exhausted else None,
    )
    assert isinstance(result, ProbeResult)
    assert result.outcome == case.expected
    assert result.detail
    if case.budget_exhausted:
        assert calls == []
        assert result.marker_written is None
        assert result.error is None
    elif result.outcome == ProbeOutcome.INCONCLUSIVE:
        assert result.marker_written is None
    else:
        assert result.marker_written is (result.outcome == ProbeOutcome.PASSED)


def test_probe_uses_a_scratch_workspace_and_leaves_the_phase_workspace_untouched(
    tmp_path: Path,
) -> None:
    phase_workspace = tmp_path / "phase"
    phase_workspace.mkdir()
    (phase_workspace / "keep.txt").write_text("keep", encoding="utf-8")
    before = sorted(entry.name for entry in phase_workspace.iterdir())
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    calls: list[_Call] = []
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke("write_marker", calls),
        workspace_factory=lambda: scratch,
        phase_workspace=phase_workspace,
    )
    assert result.outcome == ProbeOutcome.PASSED
    assert len(calls) == 1
    options = calls[0].options
    assert options is not None
    workspace_path = options.workspace_path
    assert workspace_path is not None
    assert workspace_path == scratch
    assert workspace_path != phase_workspace
    assert not _is_within(workspace_path, phase_workspace)
    assert sorted(entry.name for entry in phase_workspace.iterdir()) == before
    assert (phase_workspace / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert (scratch / CANARY_MARKER_FILENAME).read_text(encoding="utf-8") == CANARY_MARKER_CONTENT
    assert (scratch / CANARY_PROMPT_FILENAME).exists()


def test_probe_hands_the_same_config_and_environment_to_the_invoke_fn(tmp_path: Path) -> None:
    config = AgentConfig(cmd="fake-agent", model="fake-model")
    calls: list[_Call] = []
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_canary_probe(
        config,
        invoke_fn=_make_invoke("write_marker", calls),
        workspace_factory=lambda: scratch,
        extra_env={"RALPH_CANARY_ENV": "kept"},
    )
    assert result.outcome == ProbeOutcome.PASSED
    assert len(calls) == 1
    received = calls[0]
    assert received.config is config
    assert received.config.cmd == "fake-agent"
    assert received.config.model == "fake-model"
    received_options = received.options
    assert received_options is not None
    assert received_options.extra_env == {"RALPH_CANARY_ENV": "kept"}
    assert received_options.phase == CANARY_PROBE_PHASE
    assert Path(received.prompt_file).parent == scratch


def test_inconclusive_maps_to_the_unknown_verdict_hint(tmp_path: Path) -> None:
    assert PROBE_INCONCLUSIVE_VERDICT == "unknown"
    calls: list[_Call] = []
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke("raise", calls),
        workspace_factory=lambda: scratch,
    )
    assert result.outcome == ProbeOutcome.INCONCLUSIVE
    assert PROBE_INCONCLUSIVE_VERDICT == "unknown"


def _uninitialized_agent_config() -> AgentConfig:
    return object.__new__(AgentConfig)


def test_probe_never_raises_for_unusable_input(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_canary_probe(
        _uninitialized_agent_config(),
        invoke_fn=_make_invoke("no_marker", []),
        workspace_factory=lambda: scratch,
    )
    assert isinstance(result, ProbeResult)
    assert result.outcome == ProbeOutcome.INCONCLUSIVE


def test_probe_reports_inconclusive_when_the_workspace_cannot_be_created(tmp_path: Path) -> None:
    def _failing_factory() -> Path:
        raise OSError("no scratch space")

    result = run_canary_probe(
        _agent_config(),
        invoke_fn=_make_invoke("no_marker", []),
        workspace_factory=_failing_factory,
    )
    assert result.outcome == ProbeOutcome.INCONCLUSIVE
    assert result.error is not None


def test_default_invocation_uses_the_raw_agent_invoke_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _agent_config()
    calls: list[_Call] = []
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def _fake_invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str] | None:
        calls.append(_Call(config=config, prompt_file=prompt_file, options=options))
        assert options is not None
        workspace_path = options.workspace_path
        assert workspace_path is not None
        (workspace_path / CANARY_MARKER_FILENAME).write_text(
            CANARY_MARKER_CONTENT, encoding="utf-8"
        )
        return None

    monkeypatch.setattr("ralph.agents.invoke.invoke_agent", _fake_invoke)
    result = run_canary_probe(
        config,
        workspace_factory=lambda: scratch,
    )
    assert result.outcome is ProbeOutcome.PASSED
    assert result.detail == "marker_written"
    assert result.marker_written is True
    assert len(calls) == 1
    received = calls[0]
    assert received.config is config
    assert received.prompt_file.endswith(CANARY_PROMPT_FILENAME)
    received_options = received.options
    assert received_options is not None
    assert received_options.workspace_path == scratch


def test_probe_outcome_exposes_exactly_the_three_contract_members() -> None:
    assert [member.value for member in ProbeOutcome] == [
        "passed",
        "failed_reproducing_no_work",
        "inconclusive",
    ]
    assert DEFAULT_PROBE_TIMEOUT_S == 30
