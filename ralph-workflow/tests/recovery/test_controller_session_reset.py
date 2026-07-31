"""Black-box tests: controller clears stale session state on reset_session failure."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.classifier import ClassifiedFailure, FailureCategory
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions

if TYPE_CHECKING:
    import pytest


class _RecordingFileBackend(FileBackend):
    """In-memory file backend that records physical writes."""

    def __init__(self) -> None:
        self.files: dict[Path, str] = {}
        self.write_text_calls = 0

    def exists(self, path: Path) -> bool:
        return path in self.files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del path, parents, exist_ok

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        return self.files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self.write_text_calls += 1
        self.files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self.files[destination] = self.files.pop(source)

    def sync_directory(self, path: Path) -> None:
        del path

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self.files.pop(path, None)
            return
        del self.files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


def _session_reset_failure(session_id: str) -> ClassifiedFailure:
    return ClassifiedFailure(
        category=FailureCategory.AGENT,
        reason="stale session",
        attributed_agent="claude",
        attributed_phase="development",
        counts_against_budget=True,
        original_exception=None,
        raw_message=f"No conversation found with session ID: {session_id}",
        reset_session=True,
    )


class _AgentInvocationError(Exception):
    """Simulates AgentInvocationError via class name."""


_AgentInvocationError.__name__ = "AgentInvocationError"


def _make_state(
    agents: list[str],
    last_session_id: str | None = None,
) -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=agents, current_index=0, retries=0)},
        last_agent_session_id=last_session_id,
    )


def test_stale_session_clears_last_agent_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After stale-session failure, last_agent_session_id is cleared."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent" / "tmp").mkdir(parents=True)

    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, budget_registry=registry)
    )
    state = _make_state(["claude"], last_session_id="deadbeef-1234")

    exc = _AgentInvocationError(
        "Agent 'claude' failed with code 1: No conversation found with session ID: deadbeef-1234"
    )
    new_state, _, _ = controller.handle(
        state, exc, FailureContext(phase="development", agent="claude")
    )

    assert new_state.last_agent_session_id is None


def test_stale_session_clears_agent_retry_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After stale-session failure, the next-attempt retry intent is cleared."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent" / "tmp").mkdir(parents=True)

    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, budget_registry=registry)
    )
    state = _make_state(["claude"], last_session_id="deadbeef-1234")

    exc = _AgentInvocationError(
        "Agent 'claude' failed with code 1: No conversation found with session ID: deadbeef-1234"
    )
    new_state, _, _ = controller.handle(
        state, exc, FailureContext(phase="development", agent="claude")
    )

    assert new_state.agent_retry_intent.action is None


def test_stale_session_writes_retry_hint_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After stale-session failure, a retry hint file exists with relevant content."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent" / "tmp").mkdir(parents=True)

    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, budget_registry=registry)
    )
    state = _make_state(["claude"], last_session_id="abc-session")

    exc = _AgentInvocationError(
        "Agent 'claude' failed with code 1: No conversation found with session ID: abc-session"
    )
    controller.handle(state, exc, FailureContext(phase="development", agent="claude"))

    hint_file = tmp_path / ".agent" / "tmp" / f"last_retry_error_{'development'}.txt"
    assert hint_file.exists(), "Retry hint file should be written on stale-session failure"
    content = hint_file.read_text(encoding="utf-8")
    assert "session" in content.lower()
    assert "No conversation found with session ID" in content


def test_session_reset_hint_regression_skips_byte_identical_rewrite() -> None:
    """S-3: repeated stale-session hints physically write once through the backend seam."""
    backend = _RecordingFileBackend()
    controller = RecoveryController()
    failure = _session_reset_failure("same-session")

    controller._write_session_reset_hint("development", failure, backend=backend)
    controller._write_session_reset_hint("development", failure, backend=backend)

    hint_path = Path(".agent/tmp/last_retry_error_development.txt")
    assert backend.write_text_calls == 1
    assert "same-session" in backend.files[hint_path]


def test_session_reset_hint_regression_writes_changed_failure_detail() -> None:
    """S-3: changed stale-session detail replaces the retry hint through the backend seam."""
    backend = _RecordingFileBackend()
    controller = RecoveryController()

    controller._write_session_reset_hint(
        "development", _session_reset_failure("first"), backend=backend
    )
    controller._write_session_reset_hint(
        "development", _session_reset_failure("second"), backend=backend
    )

    hint_path = Path(".agent/tmp/last_retry_error_development.txt")
    assert backend.write_text_calls == 2
    assert "second" in backend.files[hint_path]


def test_stale_session_debits_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stale-session failure still decrements the agent retry budget."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent" / "tmp").mkdir(parents=True)

    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, budget_registry=registry)
    )
    state = _make_state(["claude"], last_session_id="stale-id")

    exc = _AgentInvocationError(
        "Agent 'claude' failed with code 1: No conversation found with session ID: stale-id"
    )
    _, _, evt = controller.handle(state, exc, FailureContext(phase="development", agent="claude"))

    assert evt.counted_against_budget is True
    budget = controller.budget_registry.get("development", "claude")
    assert budget is not None
    assert budget.consumed == 1


def test_stale_session_allows_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After stale-session failure, the pipeline remains in the current phase for retry."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent" / "tmp").mkdir(parents=True)

    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, budget_registry=registry)
    )
    state = _make_state(["claude"], last_session_id="stale-id")

    exc = _AgentInvocationError(
        "Agent 'claude' failed with code 1: No conversation found with session ID: stale-id"
    )
    new_state, effects, _ = controller.handle(
        state, exc, FailureContext(phase="development", agent="claude")
    )

    assert new_state.phase == "development"
    assert effects == []
