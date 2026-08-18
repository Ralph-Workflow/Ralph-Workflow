"""Default-suite regressions for CCS smoke classification.

``tests/test_smoke_ccs.py`` is marked ``smoke`` and excluded from ``make test``.
These tests pin CCS-specific classification behavior that must gate the default
suite.
"""

from __future__ import annotations

from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport
from ralph.pipeline.plumbing.smoke_plumbing import _resolve_visible_output_agent_prefix


def test_ccs_mm_registry_config_uses_headless_visible_output_ceiling() -> None:
    """The registry-synthesized ``ccs/mm`` config uses the headless ceiling.

    ``ccs/mm`` resolves to ``cmd="ccs mm"`` plus ``output_flag="--output-format=stream-json"``.
    The visible-output classifier must treat this as ``claude-headless`` (250-line
    ceiling), not as a generic ``ccs`` alias (80-line ceiling), so the subagent
    smoke scenario does not spuriously fail on output volume.
    """
    registry = AgentRegistry()
    config = registry.get("ccs/mm")
    assert config is not None, "ccs/mm must resolve in the registry"
    assert config.transport is AgentTransport.CLAUDE

    prefix = _resolve_visible_output_agent_prefix(config)
    assert prefix == "claude-headless"
