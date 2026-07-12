"""Unit tests for the project-policy preflight CLI helpers.

Covers the seams that the run-integration tests mock away:

* ``_resolve_remediation_agent_name`` resolves through the
  ``policy_remediation`` drain binding (so a reviewer-drain alias works).
* ``_resolve_max_attempts`` uses the remediation driver's own small budget,
  never the global recovery ``cycle_cap``.
* The production invocation closure forwards ``display_context`` to
  ``execute_agent_effect`` and converts launch crashes into
  ``RemediationInvocationError`` so the driver aborts instead of spinning.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from ralph.cli.commands._load_result import _LoadResult
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import default_dir, load_policy
from ralph.policy.models._agent_chain_config import AgentChainConfig
from ralph.policy.models._agent_drain_config import AgentDrainConfig
from ralph.project_policy import cli_integration, remediation
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


def _bundle_with_review_bound_remediation() -> PolicyBundle:
    """Default bundle rewired: no policy_remediation chain; the drain binds
    to a review chain, mirroring the loader's reviewer-drain backfill."""
    bundle = load_policy(default_dir())
    chains = dict(bundle.agents.agent_chains)
    del chains["policy_remediation"]
    chains["review"] = AgentChainConfig(
        agents=["reviewer-agent", "codex"], max_retries=2, retry_delay_ms=1000
    )
    drains = dict(bundle.agents.agent_drains)
    drains["policy_remediation"] = AgentDrainConfig(
        chain="review", drain_class="development"
    )
    agents = bundle.agents.model_copy(
        update={"agent_chains": chains, "agent_drains": drains}
    )
    return bundle.model_copy(update={"agents": agents})


def _load_result(bundle: PolicyBundle | None) -> _LoadResult:
    return _LoadResult(
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(
            root="/test/project", allowed_roots=["/test/project"]
        ),
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=bundle,
        run_id="test-run-id",
    )


def test_agent_name_resolves_through_drain_binding() -> None:
    load_result = _load_result(_bundle_with_review_bound_remediation())
    assert (
        cli_integration._resolve_remediation_agent_name(load_result)
        == "reviewer-agent"
    )


def test_max_attempts_ignores_global_recovery_cycle_cap() -> None:
    bundle = load_policy(default_dir())
    assert bundle.pipeline.recovery.cycle_cap == 200
    load_result = _load_result(bundle)
    assert (
        cli_integration._resolve_max_attempts(load_result)
        == remediation.DEFAULT_MAX_ATTEMPTS
    )


def test_production_closure_forwards_display_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_policy(default_dir())
    load_result = _load_result(bundle)
    display_context = make_display_context()
    observed_opts: list[dict[str, object]] = []

    def fake_execute_agent_effect(
        effect: object,
        config: object,
        pipeline_deps: object,
        workspace_scope: object,
        **opts: object,
    ) -> object:
        observed_opts.append(dict(opts))
        from ralph.pipeline.events import PipelineEvent

        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        effect_executor_module, "execute_agent_effect", fake_execute_agent_effect
    )
    invoke = cli_integration._make_production_invoke_remediation_agent(
        load_result,
        cast("object", object()),  # non-None pipeline deps sentinel
        load_result.workspace_scope,
        "claude",
        display_context,
    )
    assert invoke("prompt.md") is True
    assert observed_opts, "execute_agent_effect must be invoked"
    assert observed_opts[0].get("display_context") is display_context


def test_production_closure_raises_on_launch_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_policy(default_dir())
    load_result = _load_result(bundle)

    def crashing_execute_agent_effect(*args: object, **opts: object) -> object:
        raise TypeError("display_context is required when display is None")

    monkeypatch.setattr(
        effect_executor_module,
        "execute_agent_effect",
        crashing_execute_agent_effect,
    )
    invoke = cli_integration._make_production_invoke_remediation_agent(
        load_result,
        cast("object", object()),
        load_result.workspace_scope,
        "claude",
        make_display_context(),
    )
    with pytest.raises(remediation.RemediationInvocationError):
        invoke("prompt.md")
