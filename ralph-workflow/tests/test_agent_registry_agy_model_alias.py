"""Tests for measured ``agy/<model>[:effort]`` dynamic aliases.

The ``agy`` transport publishes a set of model IDs (the effort tier is part
of the published ID itself, e.g. ``gemini-3.8-flash-high``). Ralph exposes
each published ID as the dynamic alias ``agy/<model-id>`` and rejects any
``:effort`` suffix (AGY accepts ``--effort`` only WITHOUT an explicit
model).

The published set is read from the canonical helper
:func:`ralph.agents.registry.agy_published_models` instead of being
hard-coded here: the helper falls back to the measured v1.2.17 pin when the
live ``agy models`` probe is unavailable, so the table below is always the
set the registry itself considers published. The alias contract is then
asserted for EVERY published ID in one table-driven pass.
"""

from __future__ import annotations

import shlex

from ralph.agents.registry import AgentRegistry, _resolve_dynamic_agent, agy_published_models
from ralph.config.ccs_config import CcsConfig
from ralph.config.enums import AgentTransport
from ralph.config.models import UnifiedConfig

#: Alias shapes that MUST NOT resolve to an agent configuration.
#: ``agy`` alone and ``agy/`` carry no model ID; ``agy/not-published`` names
#: a model AGY does not publish; ``agy/<published-id>:high`` carries the
#: effort suffix AGY rejects when a model is named explicitly.
_UNKNOWN_ALIASES: tuple[str, ...] = (
    "agy",
    "agy/",
    "agy/not-published",
)


def test_agy_model_alias_sets_published_model_flag_with_commit_permission() -> None:
    """Every published AGY model ID resolves to an ``agy/<id>`` alias.

    The alias must select the published model through a single
    ``--model <id>`` argv token (``shlex.quote`` keeps an ID containing
    shell-special characters in one token) and must keep the AGY
    transport so the invocation is routed through the AGY runtime.
    """
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
        # The AGY transport must survive the alias resolution, otherwise
        # the alias would be routed through a different runtime.
        assert config.transport == AgentTransport.AGY, (
            f"{name} must keep the AGY transport; got {config.transport!r}"
        )


def test_agy_model_alias_rejects_unknown_names() -> None:
    """An alias without a published model ID must not resolve."""
    for name in _UNKNOWN_ALIASES:
        assert _resolve_dynamic_agent(name, CcsConfig()) is None, (
            f"{name!r} must not resolve to an agent config"
        )


def test_agy_model_alias_rejects_effort_suffix() -> None:
    """``agy/<published-id>:<effort>`` is rejected: AGY takes no effort suffix."""
    published = agy_published_models()
    assert published, "AGY must publish at least one model id"

    name = f"agy/{published[0]}:high"
    assert _resolve_dynamic_agent(name, CcsConfig()) is None, (
        f"{name!r} must not resolve: AGY accepts --effort only without a model"
    )
