from __future__ import annotations

import shlex

from ralph.agents.registry import AgentRegistry, _resolve_dynamic_agent, agy_published_models
from ralph.config.ccs_config import CcsConfig
from ralph.config.enums import AgentTransport
from ralph.config.models import UnifiedConfig

_UNKNOWN_ALIASES: tuple[str, ...] = (
    "agy",
    "agy/",
    "agy/not-published",
)


def test_agy_model_alias_sets_published_model_flag_with_commit_permission() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())
    published = agy_published_models()
    assert published, "AGY must publish at least one model id"

    for model_id in published:
        name = f"agy/{model_id}"
        config = registry.get(name)

        assert config is not None, f"{name} must resolve to an agent config"
        assert config.model_flag == f"--model {shlex.quote(model_id)}", (
            f"{name} must select the published model through --model; got {config.model_flag!r}"
        )
        assert config.transport == AgentTransport.AGY, (
            f"{name} must keep the AGY transport; got {config.transport!r}"
        )


def test_agy_model_alias_rejects_unknown_names() -> None:
    for name in _UNKNOWN_ALIASES:
        assert _resolve_dynamic_agent(name, CcsConfig()) is None, (
            f"{name!r} must not resolve to an agent config"
        )


def test_agy_model_alias_rejects_effort_suffix() -> None:
    published = agy_published_models()
    assert published, "AGY must publish at least one model id"

    name = f"agy/{published[0]}:high"
    assert _resolve_dynamic_agent(name, CcsConfig()) is None, (
        f"{name!r} must not resolve: AGY accepts --effort only without a model"
    )
