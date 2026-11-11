from __future__ import annotations

from ralph.agents.catalog import AgentCatalog, default_catalog
from ralph.agents.execution_state.claude_execution_strategy import ClaudeExecutionStrategy
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.registration import register_agent_support_to_catalog
from ralph.agents.registry import agy_alias_help, lookup_dynamic_alias_help
from ralph.agents.support import _DYNAMIC_ALIAS_HELP_BY_PREFIX, AgentSupport
from ralph.agents.unknown_agent_error import UnknownAgentError
from ralph.config.enums import AgentTransport, JsonParserType

_CUSTOM_HELP = "FutureAgent models: future-one, future-two."


def _register_future_agent(catalog: AgentCatalog) -> None:
    support = AgentSupport.from_registration_kwargs(
        "futureagent",
        transport=AgentTransport.GENERIC,
        parser_factory=ClaudeParser,
        strategy_factory=ClaudeExecutionStrategy,
        json_parser=JsonParserType.GENERIC,
        dynamic_alias_help=lambda: _CUSTOM_HELP,
        dynamic_alias_help_prefix=("futureagent",),
    )
    register_agent_support_to_catalog("futureagent", support, catalog)


def test_phase1_registered_name_returns_agy_help() -> None:
    support = default_catalog().get("agy")
    assert support is not None, "default-catalog seeding must register the agy support"
    assert support.dynamic_alias_help is not None
    assert lookup_dynamic_alias_help("agy") == agy_alias_help()


def test_phase2_unknown_alias_falls_back_to_registered_prefix() -> None:
    assert default_catalog().get("agy/unknown-model") is None, (
        "premise: the unknown alias must not resolve in the catalog, so the "
        "help can only come from the registered-prefix fallback"
    )
    assert lookup_dynamic_alias_help("agy/unknown-model") == agy_alias_help()


def test_miss_returns_none_for_unregistered_prefix() -> None:
    assert lookup_dynamic_alias_help("non-agy/unknown") is None


def test_unknown_agent_error_carries_agy_help_via_prefix_fallback() -> None:
    message = UnknownAgentError("agy/unknown-model").args[0]
    assert "Unknown agent: 'agy/unknown-model'" in message
    assert f" {agy_alias_help()}" in message


def test_custom_agent_gets_the_same_seam() -> None:
    catalog = AgentCatalog()
    _register_future_agent(catalog)
    try:
        assert lookup_dynamic_alias_help("futureagent", catalog=catalog) == _CUSTOM_HELP
        assert lookup_dynamic_alias_help("futureagent/unknown", catalog=catalog) == _CUSTOM_HELP
    finally:
        _DYNAMIC_ALIAS_HELP_BY_PREFIX.pop("futureagent", None)
    assert lookup_dynamic_alias_help("futureagent/unknown", catalog=AgentCatalog()) is None
