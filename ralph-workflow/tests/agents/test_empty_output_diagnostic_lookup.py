from __future__ import annotations

import types
from pathlib import Path

import pytest

from ralph.agents._agy_upstream_diagnostic import agy_empty_output_reason
from ralph.agents.catalog import AgentCatalog, default_catalog
from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.execution_state.generic_execution_strategy import GenericExecutionStrategy
from ralph.agents.invoke import AgentInvocationError, CompletionCheckOptions, check_process_result
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.registration import register_agent_support_to_catalog
from ralph.agents.registry import lookup_empty_output_diagnostic_factory
from ralph.agents.support import _EMPTY_OUTPUT_DIAGNOSTIC_FACTORY_BY_PREFIX, AgentSupport
from ralph.config.enums import AgentTransport


def _custom_diagnostic(output: list[str], cli_log_path: Path | None) -> str | None:
    return "futureagent upstream stalled" if cli_log_path is not None else None


def _register_future_agent(catalog: AgentCatalog) -> None:
    support = AgentSupport.from_registration_kwargs(
        "futureagent",
        transport=AgentTransport.GENERIC,
        parser_factory=ClaudeParser,
        strategy_factory=GenericExecutionStrategy,
        empty_output_diagnostic_factory=_custom_diagnostic,
        empty_output_diagnostic_prefix=("futureagent",),
    )
    register_agent_support_to_catalog("futureagent", support, catalog)


def test_phase1_registered_name_returns_agy_factory() -> None:
    support = default_catalog().get("agy")
    assert support is not None, "default-catalog seeding must register the agy support"
    assert support.empty_output_diagnostic_factory is not None
    assert lookup_empty_output_diagnostic_factory("agy") is agy_empty_output_reason


def test_phase2_unknown_alias_falls_back_to_registered_prefix() -> None:
    assert default_catalog().get("agy/unknown-model") is None, (
        "premise: the unknown alias must not resolve in the catalog, so the "
        "factory can only come from the registered-prefix fallback"
    )
    assert lookup_empty_output_diagnostic_factory("agy/unknown-model") is agy_empty_output_reason


def test_miss_returns_none_for_unregistered_prefix() -> None:
    assert lookup_empty_output_diagnostic_factory("non-agy/unknown") is None


def test_custom_agent_gets_the_same_seam() -> None:
    catalog = AgentCatalog()
    _register_future_agent(catalog)
    try:
        assert (
            lookup_empty_output_diagnostic_factory("futureagent", catalog=catalog)
            is _custom_diagnostic
        )
        assert (
            lookup_empty_output_diagnostic_factory("futureagent/unknown", catalog=catalog)
            is _custom_diagnostic
        )
    finally:
        _EMPTY_OUTPUT_DIAGNOSTIC_FACTORY_BY_PREFIX.pop("futureagent", None)
    assert (
        lookup_empty_output_diagnostic_factory("futureagent/unknown", catalog=AgentCatalog())
        is None
    )


def test_completion_path_routes_unknown_agy_alias_through_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_log = tmp_path / "cli.log"
    cli_log.write_text("RESOURCE_EXHAUSTED (code 429)", encoding="utf-8")

    seen: list[str] = []

    def spy(agent_name: str) -> object:
        seen.append(agent_name)
        return lookup_empty_output_diagnostic_factory(agent_name)

    monkeypatch.setattr(
        "ralph.agents.invoke._completion.lookup_empty_output_diagnostic_factory", spy
    )

    with pytest.raises(AgentInvocationError, match="quota is exhausted"):
        check_process_result(
            types.SimpleNamespace(returncode=0),
            "agy/unknown-model",
            [],
            CompletionCheckOptions(
                execution_strategy=strategy_for_transport(AgentTransport.AGY),
                workspace_path=tmp_path,
                agy_cli_log_path=cli_log,
            ),
        )
    assert seen == ["agy/unknown-model"]
