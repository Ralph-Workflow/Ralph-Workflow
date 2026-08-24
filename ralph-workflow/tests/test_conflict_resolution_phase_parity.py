"""B1-B6: conflict resolution must follow the pipeline chain, not a local loop."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    invoke_resolution_agent,
)
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle

_CONFLICTED = ["src/alpha.py"]


def _policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {}})


def _install_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    unmerged: Sequence[str] = _CONFLICTED,
    surviving_per_round: Sequence[Sequence[str]] | None = None,
) -> None:
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda root: list(unmerged))
    remaining = list(surviving_per_round) if surviving_per_round is not None else [list(unmerged)]

    def _fake_markers(root: Path, paths: Sequence[str]) -> list[str]:
        if remaining:
            return list(remaining.pop(0))
        return list(unmerged)

    monkeypatch.setattr(driver_module, "paths_with_conflict_markers", _fake_markers)


def test_four_agent_chain_tries_every_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two", "three", "four")
    )
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        return False

    assert (
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke,
        )
        is False
    )
    assert called == ["one", "two", "three", "four"]


def test_transient_failure_retries_the_same_agent_using_recovery_controller_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A transient first-agent failure retries that agent before failover."""
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two")
    )
    monkeypatch.setattr("ralph.pipeline.conflict_resolution.driver.time.sleep", lambda _seconds: None)
    _install_seams(
        monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED]
    )
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        if agent_name == "one":
            raise ConnectionError("provider unavailable")
        return False

    assert (
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke,
        )
        is False
    )
    assert called[0] == "one"
    assert called.count("one") > 1
    assert "two" in called
    one_indexes = [index for index, name in enumerate(called) if name == "one"]
    assert one_indexes[1] == one_indexes[0] + 1


def test_conflict_retry_honors_chain_retry_delay_ms_from_recovery_controller(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Retry backoff is the chain's retry_delay_ms decided by RecoveryController.handle."""
    sleeps: list[float] = []
    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.driver.time.sleep", sleeps.append
    )
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two")
    )
    _install_seams(
        monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED]
    )

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        if agent_name == "one":
            raise ConnectionError("provider unavailable")
        return False

    assert (
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke,
        )
        is False
    )
    chain = _policy_bundle().agents.agent_chains["rebase_conflict_resolution"]
    expected_seconds = chain.retry_delay_ms / 1000.0
    assert sleeps
    assert sleeps[0] == expected_seconds


def test_conflict_failures_call_recovery_controller_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R1: candidate failures use RecoveryController.handle, not a private retry table."""
    from ralph.recovery.controller import RecoveryController

    handled: list[str] = []
    original = RecoveryController.handle

    def _spy(
        self: RecoveryController,
        state: object,
        raw_failure: BaseException | str,
        context: object,
    ) -> object:
        handled.append(str(raw_failure))
        return original(self, state, raw_failure, context)

    monkeypatch.setattr(RecoveryController, "handle", _spy)
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two")
    )
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        raise RuntimeError(f"launch failed for {agent_name}")

    assert (
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke,
        )
        is False
    )
    assert handled
    assert any("launch failed for one" in item for item in handled)


def test_next_round_starts_at_an_untried_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two", "three", "four")
    )
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, []])
    called: list[tuple[str, int]] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append((agent_name, round_index))
        return True

    assert (
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke,
        )
        is True
    )
    assert called[0] == ("one", 1)
    assert called[1] == ("two", 2)


def test_invoke_resolution_agent_keeps_chain_retry_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[int] = []

    def _capture_execute(*args: object, **kwargs: object) -> object:
        config = args[1] if len(args) > 1 else kwargs.get("config")
        assert config is not None
        captured.append(config.general.max_same_agent_retries)
        raise RuntimeError("launch skipped")

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session._effect_executor_module.execute_agent_effect",
        _capture_execute,
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve", encoding="utf-8")
    config = UnifiedConfig.model_validate({"general": {"max_same_agent_retries": 4}})
    assert (
        invoke_resolution_agent(
            agent_name="claude",
            prompt_path=prompt,
            config=config,
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
        )
        is False
    )
    assert captured == [4]


def test_launch_failure_is_not_collapsed_to_candidate_declined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one",))
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    session = ResolutionSession()

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        raise RuntimeError("provider unreachable")

    assert (
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke,
            session=session,
        )
        is False
    )
    assert session.terminal_reason is ResolutionTerminationReason.EXCEPTION


def test_failed_invoke_routes_launch_provider_and_decline_through_recovery_controller(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After a failed invoke, RecoveryController sees launch vs provider vs decline."""
    from ralph.recovery.controller import RecoveryController
    from ralph.recovery.failure_classifier import FailureClassifier

    seen: list[str] = []

    def _spy(
        self: RecoveryController,
        raw_failure: BaseException | str,
        *,
        agent: str,
        phase: str = "rebase_conflict_resolution",
    ) -> object:
        text = str(raw_failure).lower()
        if "launch" in text:
            seen.append("launch")
        elif "provider" in text:
            seen.append("provider")
        else:
            seen.append("decline")
        return FailureClassifier().classify(raw_failure, phase=phase, agent=agent)

    monkeypatch.setattr(
        RecoveryController,
        "classify_conflict_attempt",
        _spy,
        raising=False,
    )
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two", "three")
    )
    monkeypatch.setattr("ralph.pipeline.conflict_resolution.driver.time.sleep", lambda _seconds: None)
    _install_seams(
        monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED, _CONFLICTED]
    )
    outcomes: list[BaseException | bool] = [
        RuntimeError("launch failed: cannot spawn agent"),
        ConnectionError("provider unavailable"),
        ConnectionError("provider unavailable"),
        False,
    ]

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        item = outcomes.pop(0) if outcomes else False
        if isinstance(item, BaseException):
            raise item
        return item

    assert (
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke,
        )
        is False
    )
    assert seen[0] == "launch"
    assert "provider" in seen
    assert "decline" in seen


def test_max_fallback_agents_does_not_truncate_a_four_agent_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """max_fallback_agents remains a non-truncating compatibility field."""
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two", "three", "four")
    )
    _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED, _CONFLICTED, _CONFLICTED])
    called: list[str] = []

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        return False

    session = ResolutionSession(max_fallback_agents=2)
    assert (
        run_conflict_resolution_pipeline(
            root=tmp_path,
            target="main",
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=_invoke,
            session=session,
        )
        is False
    )
    assert called == ["one", "two", "three", "four"]
