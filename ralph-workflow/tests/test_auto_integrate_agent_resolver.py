"""Unit contracts for the conflict-resolver builder.

All-mock: no test here starts git or a real agent subprocess. The
builder is a thin adapter now, so its contract is (a) it delegates to the
conflict-resolution pipeline with the seam's dependencies threaded
through, (b) a MISSING dependency declines instead of falling back to an
MCP-less invocation -- that fallback was the defect -- and (c) every
failure mode is contained as ``False`` so
:mod:`ralph.pipeline.auto_integrate_resolve` aborts the merge rather than
crashing the run.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate_agent as resolver_module
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


#: Distinct from every other timeout default so an assertion on it
#: cannot pass by coincidence.
_RESOLVE_TIMEOUT_SECONDS = 123.5


def _build_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_resolve_timeout_seconds": _RESOLVE_TIMEOUT_SECONDS,
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
    monkeypatch: pytest.MonkeyPatch, *, result: bool = True
) -> list[dict[str, object]]:
    """Replace the pipeline with a recorder; returns the recorded kwargs."""
    calls: list[dict[str, object]] = []

    def _fake_pipeline(**kwargs: object) -> bool:
        calls.append(kwargs)
        return result

    monkeypatch.setattr(
        resolver_module, "run_conflict_resolution_pipeline", _fake_pipeline
    )
    return calls


def test_resolver_delegates_to_the_conflict_resolution_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The seam's dependencies reach the pipeline that needs them."""
    calls = _install_pipeline_spy(monkeypatch)
    resolver, registry, _display = _build_resolver(agent_config=object())

    assert resolver(tmp_path, "main") is True

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["root"] == tmp_path
    assert kwargs["target"] == "main"
    # Without these two the pipeline cannot build an MCP session at all.
    assert kwargs["pipeline_deps"] == "pipeline-deps-sentinel"
    assert kwargs["workspace_scope"] == "workspace-scope-sentinel"
    assert kwargs["policy_bundle"] is _load_default_policy_bundle()
    # The resolution chain is checked against the installed agents first.
    looked_up = [call.args[0] for call in registry.get.call_args_list]
    assert looked_up[0] == "claude"


def test_resolver_reports_the_pipeline_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pipeline that could not resolve is reported as failure, not success."""
    _install_pipeline_spy(monkeypatch, result=False)
    resolver, _registry, _display = _build_resolver(agent_config=object())

    assert resolver(tmp_path, "main") is False


def test_resolver_declines_without_pipeline_deps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A seam that did not thread pipeline_deps must NOT invoke an agent.

    An invocation without pipeline_deps has no Ralph MCP session, hence
    no exec-policy git denial and no completion contract. Declining is
    the only safe answer.
    """
    calls = _install_pipeline_spy(monkeypatch)
    resolver, _registry, _display = _build_resolver(
        agent_config=object(), pipeline_deps=None
    )

    assert resolver(tmp_path, "main") is False
    assert calls == []


def test_resolver_declines_without_workspace_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same contract for the other required dependency."""
    calls = _install_pipeline_spy(monkeypatch)
    resolver, _registry, _display = _build_resolver(
        agent_config=object(), workspace_scope=None
    )

    assert resolver(tmp_path, "main") is False
    assert calls == []


def test_resolver_declines_when_no_chain_agent_is_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No usable agent: decline before paying for a session."""
    calls = _install_pipeline_spy(monkeypatch)
    resolver, _registry, _display = _build_resolver(agent_config=None)

    assert resolver(tmp_path, "main") is False
    assert calls == []


def test_resolver_contains_a_pipeline_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The resolver never raises into the integration step."""

    def _boom(**_kwargs: object) -> bool:
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(resolver_module, "run_conflict_resolution_pipeline", _boom)
    resolver, _registry, _display = _build_resolver(agent_config=object())

    try:
        resolved = resolver(tmp_path, "main")
    except Exception as exc:  # pragma: no cover - the assertion below reports it
        raise AssertionError(f"resolver must not raise: {exc}") from exc
    assert resolved is False


def test_resolver_issues_no_git_command_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Aborting and committing the merge belong to the caller, not here."""
    _install_pipeline_spy(monkeypatch, result=False)
    run_git = MagicMock()
    monkeypatch.setattr("ralph.git.subprocess_runner.run_git", run_git, raising=True)
    resolver, _registry, _display = _build_resolver(agent_config=object())

    assert resolver(tmp_path, "main") is False
    run_git.assert_not_called()
