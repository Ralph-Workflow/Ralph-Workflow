from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate_agent as resolver_module
from ralph.pipeline.resolution_outcome import (
    NextStrategyOutcome,
    ResolvedOutcome,
    StopResolutionOutcome,
    UnknownOutcome,
)
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


_RESOLVE_TIMEOUT_SECONDS = 123.5


def _build_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "RESOLVE_TIMEOUT_SECONDS": _RESOLVE_TIMEOUT_SECONDS,
            }
        }
    )


def _build_resolver(
    *,
    agent_config: object,
    pipeline_deps: object = "pipeline-deps-sentinel",
    workspace_scope: object = "workspace-scope-sentinel",
) -> tuple[resolver_module.ConflictResolver, MagicMock, MagicMock]:
    registry = MagicMock()
    registry.get.return_value = agent_config
    display = MagicMock()
    resolver = resolver_module.build_agent_conflict_resolver(
        policy_bundle=_load_default_policy_bundle(),
        registry=registry,
        display=display,
        config=_build_config(),
        pipeline_deps=pipeline_deps,
        workspace_scope=workspace_scope,
    )
    return resolver, registry, display


def _install_pipeline_spy(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: StopResolutionOutcome | None = None,
) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    effective_result = result if result is not None else ResolvedOutcome()

    def _fake_pipeline(**kwargs: object) -> StopResolutionOutcome:
        calls.append(kwargs)
        return effective_result

    monkeypatch.setattr(resolver_module, "run_conflict_resolution_pipeline", _fake_pipeline)
    return calls


def test_resolver_delegates_to_the_conflict_resolution_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install_pipeline_spy(monkeypatch)
    resolver, registry, _display = _build_resolver(agent_config=object())

    assert isinstance(resolver(tmp_path, "main"), ResolvedOutcome)

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["root"] == tmp_path
    assert kwargs["target"] == "main"
    assert kwargs["pipeline_deps"] == "pipeline-deps-sentinel"
    assert kwargs["workspace_scope"] == "workspace-scope-sentinel"
    assert kwargs["policy_bundle"] is _load_default_policy_bundle()
    looked_up = [call.args[0] for call in registry.get.call_args_list]
    assert looked_up[0] == "claude"


def test_resolver_reports_the_pipeline_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_pipeline_spy(monkeypatch, result=NextStrategyOutcome("completion_in_place"))
    resolver, _registry, _display = _build_resolver(agent_config=object())

    assert isinstance(resolver(tmp_path, "main"), NextStrategyOutcome)


def test_resolver_declines_without_pipeline_deps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install_pipeline_spy(monkeypatch)
    resolver, _registry, _display = _build_resolver(agent_config=object(), pipeline_deps=None)

    assert isinstance(resolver(tmp_path, "main"), NextStrategyOutcome)
    assert calls == []


def test_resolver_declines_without_workspace_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install_pipeline_spy(monkeypatch)
    resolver, _registry, _display = _build_resolver(agent_config=object(), workspace_scope=None)

    assert isinstance(resolver(tmp_path, "main"), NextStrategyOutcome)
    assert calls == []


@pytest.mark.timeout_seconds(5)
def test_resolver_declines_when_no_chain_agent_is_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install_pipeline_spy(monkeypatch)
    resolver, _registry, _display = _build_resolver(agent_config=None)

    assert isinstance(resolver(tmp_path, "main"), NextStrategyOutcome)
    assert calls == []


def test_resolver_contains_a_pipeline_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    def _boom(**_kwargs: object) -> StopResolutionOutcome:
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(resolver_module, "run_conflict_resolution_pipeline", _boom)
    resolver, _registry, _display = _build_resolver(agent_config=object())

    try:
        resolved = resolver(tmp_path, "main")
    except Exception as exc:
        raise AssertionError(f"resolver must not raise: {exc}") from exc
    assert isinstance(resolved, UnknownOutcome)


def test_resolver_issues_no_git_command_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_pipeline_spy(monkeypatch, result=NextStrategyOutcome("completion_in_place"))
    run_git = MagicMock()
    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", run_git, raising=True)
    resolver, _registry, _display = _build_resolver(agent_config=object())

    assert isinstance(resolver(tmp_path, "main"), NextStrategyOutcome)
    run_git.assert_not_called()
