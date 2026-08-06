"""Tests for the S-4 tri-state display-capability declaration contract.

The capability declaration is the durable, fail-closed mechanism that
prevents the original OpenCode defect class from recurring: a parser
silently dropping the structured tool metadata the display layer needs
without anything saying so. The tests in this file pin the total
catalog-derived vocabulary, the per-builtin mandatory coverage, and
the four mutation paths (remove one stance, duplicate one, add a new
agent, extend the enum) that the plan requires the gate to bite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.builtin import builtin_supports
from ralph.agents.builtin_spec import BuiltinAgentSpec
from ralph.agents.display_capabilities import (
    DisplayCapability,
    all_display_capabilities,
    surface_to_capability,
)
from ralph.agents.display_capability_stance import DisplayCapabilityStance
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.execution_state.claude_execution_strategy import ClaudeExecutionStrategy
from ralph.agents.execution_state.claude_interactive_execution_strategy import (
    ClaudeInteractiveExecutionStrategy,
)
from ralph.agents.execution_state.opencode_execution_strategy import OpenCodeExecutionStrategy
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.parsers.claude_interactive import ClaudeInteractiveParser
from ralph.agents.parsers.opencode import OpenCodeParser
from ralph.agents.spec import AgentSpec
from ralph.agents.support import AgentSupport
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.display.surface_catalog import SURFACE_CATALOG

if TYPE_CHECKING:
    from collections.abc import Iterator


# ---------------------------------------------------------------------------
# Capability vocabulary is derived from SURFACE_CATALOG and frozen.
# ---------------------------------------------------------------------------


def test_display_capabilities_vocabulary_is_non_empty() -> None:
    """The catalog-derived capability vocabulary must have at least one entry."""
    capabilities = all_display_capabilities()
    assert len(capabilities) >= 1
    assert all(isinstance(c, DisplayCapability) for c in capabilities)


def test_display_capabilities_vocabulary_matches_catalog_surfaces() -> None:
    """Every capability enum value must correspond to a SurfaceSpec in SURFACE_CATALOG."""
    catalog_names = {surface.name for surface in SURFACE_CATALOG}
    for capability in all_display_capabilities():
        assert capability.value in catalog_names, (
            f"Capability {capability.name!r} maps to surface "
            f"{capability.value!r}, which is missing from SURFACE_CATALOG"
        )


def test_surface_to_capability_resolves_known_surfaces() -> None:
    for capability in all_display_capabilities():
        assert surface_to_capability(capability.value) is capability


def test_surface_to_capability_returns_none_for_unknown_surface() -> None:
    assert surface_to_capability("definitely_not_a_capability") is None


# ---------------------------------------------------------------------------
# Tri-state stance: SUPPORTED needs no reason; the other two do.
# ---------------------------------------------------------------------------


def test_supported_stance_does_not_require_a_reason() -> None:
    """``SUPPORTED`` accepts an empty reason (the supporting evidence lives
    in fixtures/tests, not in the stance text)."""
    stance = DisplayCapabilityStance.supported(DisplayCapability.SYNTAX_HIGHLIGHTING)
    assert stance.is_supported
    assert stance.reason == ""


def test_supported_stance_accepts_optional_detail_for_diagnostics() -> None:
    stance = DisplayCapabilityStance.supported(
        DisplayCapability.SYNTAX_HIGHLIGHTING, detail="fixture:agy_wire.jsonl"
    )
    assert stance.is_supported
    assert stance.reason == "fixture:agy_wire.jsonl"


def test_not_applicable_stance_requires_a_non_empty_reason() -> None:
    stance = DisplayCapabilityStance.not_applicable(
        DisplayCapability.EDIT_DIFF, "agent never edits files"
    )
    assert not stance.is_supported
    assert stance.reason == "agent never edits files"


def test_unimplemented_stance_requires_a_non_empty_reason() -> None:
    stance = DisplayCapabilityStance.unimplemented(
        DisplayCapability.FILE_PREVIEW, "parser does not yet route to payload_from_tool_event"
    )
    assert not stance.is_supported


def test_not_applicable_rejects_blank_reason() -> None:
    with pytest.raises(ValueError, match="NOT_APPLICABLE reason must be a non-empty string"):
        DisplayCapabilityStance.not_applicable(DisplayCapability.EDIT_DIFF, "")


def test_not_applicable_rejects_whitespace_only_reason() -> None:
    with pytest.raises(ValueError, match="NOT_APPLICABLE reason must be a non-empty string"):
        DisplayCapabilityStance.not_applicable(DisplayCapability.EDIT_DIFF, "   \t  ")


def test_unimplemented_rejects_blank_reason() -> None:
    with pytest.raises(ValueError, match="UNIMPLEMENTED reason must be a non-empty string"):
        DisplayCapabilityStance.unimplemented(DisplayCapability.FILE_PREVIEW, "")


def test_stance_rejects_unknown_capability() -> None:
    """Unknown capability values are not silently accepted."""

    # The validator rejects any capability whose ``value`` is not in
    # the catalog. The check is ``self.capability not in _ALL_DISPLAY_CAPABILITIES``
    # so a plain duck-typed object with a ``value`` attribute that
    # does not collide with any catalog value is sufficient.
    class _FakeCapability:
        value = "not_in_vocabulary"

    with pytest.raises(ValueError, match="Unknown display capability"):
        DisplayCapabilityStance.supported(_FakeCapability())


def test_stance_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="must be one of"):
        DisplayCapabilityStance(
            capability=DisplayCapability.SYNTAX_HIGHLIGHTING, kind="maybe"
        )


def test_stance_label_inlines_reason_for_unsupported_kinds() -> None:
    supported = DisplayCapabilityStance.supported(DisplayCapability.SYNTAX_HIGHLIGHTING)
    assert supported.label() == "SUPPORTED"
    not_app = DisplayCapabilityStance.not_applicable(
        DisplayCapability.EDIT_DIFF, "structural impossibility"
    )
    assert not_app.label() == "NOT_APPLICABLE (structural impossibility)"
    unimplemented = DisplayCapabilityStance.unimplemented(
        DisplayCapability.FILE_PREVIEW, "parser gap"
    )
    assert unimplemented.label() == "UNIMPLEMENTED (parser gap)"


# ---------------------------------------------------------------------------
# AgentSupport validation: built-ins MUST cover the full vocabulary.
# ---------------------------------------------------------------------------


def _support_with_stances(
    stances: tuple[DisplayCapabilityStance, ...],
    *,
    is_builtin: bool,
) -> AgentSupport:
    config = AgentConfig(cmd="dummy", json_parser=JsonParserType.GENERIC)
    return AgentSupport(
        name="dummy",
        spec=AgentSpec(name="dummy", transport=AgentTransport.GENERIC),
        parser_factory=_FakeParser,
        strategy_factory=_FakeStrategy,
        config=config,
        is_builtin=is_builtin,
        display_capabilities=stances,
    )


class _FakeParser(ParserTemplateBase):
    """Minimal parser used to construct a valid AgentSupport without typing suppression."""

    _STOP_EVENT_TYPES: frozenset[str] = frozenset()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        stripped = line.strip()
        yield AgentOutputLine(type="raw", content=stripped, raw=stripped)


class _FakeStrategy(BaseExecutionStrategy):
    """Minimal strategy used to construct a valid AgentSupport without typing suppression."""


def test_builtin_support_with_complete_declaration_is_accepted() -> None:
    full = tuple(DisplayCapabilityStance.supported(c) for c in all_display_capabilities())
    support = _support_with_stances(full, is_builtin=True)
    assert len(support.display_capabilities) == len(all_display_capabilities())


def test_builtin_support_with_empty_declaration_is_rejected() -> None:
    with pytest.raises(ValueError, match="must declare display_capabilities"):
        _support_with_stances((), is_builtin=True)


def test_builtin_support_missing_one_capability_is_rejected() -> None:
    """Removing one stance from a built-in must fail the constructor."""
    full = [DisplayCapabilityStance.supported(c) for c in all_display_capabilities()]
    full.pop()  # drop the last one
    with pytest.raises(ValueError, match="missing display_capabilities"):
        _support_with_stances(tuple(full), is_builtin=True)


def test_builtin_support_with_duplicate_stances_is_rejected() -> None:
    """A built-in carrying two stances for the same capability must fail."""
    first = DisplayCapabilityStance.supported(DisplayCapability.SYNTAX_HIGHLIGHTING)
    second = DisplayCapabilityStance.not_applicable(
        DisplayCapability.SYNTAX_HIGHLIGHTING, "double entry"
    )
    with pytest.raises(ValueError, match="Duplicate"):
        _support_with_stances((first, second), is_builtin=True)


def test_builtin_support_with_unknown_capability_entry_is_rejected() -> None:
    """A built-in carrying a stance whose capability is not in the catalog vocabulary must fail."""

    # The validator checks ``self.capability not in _ALL_DISPLAY_CAPABILITIES``;
    # a duck-typed object with a non-catalog ``value`` is sufficient.
    class _FakeCapability:
        value = "rogue_capability"

    with pytest.raises(ValueError, match="Unknown display capability"):
        _support_with_stances(
            (DisplayCapabilityStance.supported(_FakeCapability()),),
            is_builtin=True,
        )


def test_custom_support_with_empty_declaration_is_accepted() -> None:
    """Custom agents (is_builtin=False) may omit the declaration entirely."""
    support = _support_with_stances((), is_builtin=False)
    assert support.display_capabilities == ()


def test_custom_support_with_partial_declaration_is_accepted() -> None:
    """Custom agents may declare a subset of capabilities."""
    partial = (
        DisplayCapabilityStance.supported(DisplayCapability.SYNTAX_HIGHLIGHTING),
    )
    support = _support_with_stances(partial, is_builtin=False)
    assert len(support.display_capabilities) == 1


def test_custom_support_with_duplicate_stances_is_rejected() -> None:
    double = (
        DisplayCapabilityStance.supported(DisplayCapability.SYNTAX_HIGHLIGHTING),
        DisplayCapabilityStance.not_applicable(DisplayCapability.SYNTAX_HIGHLIGHTING, "dup"),
    )
    with pytest.raises(ValueError, match="Duplicate"):
        _support_with_stances(double, is_builtin=False)


def test_custom_support_with_unknown_capability_entry_is_rejected() -> None:
    # The validator checks ``self.capability not in _ALL_DISPLAY_CAPABILITIES``;
    # a duck-typed object with a non-catalog ``value`` is sufficient.
    class _FakeCapability:
        value = "rogue_capability"

    with pytest.raises(ValueError, match="Unknown display capability"):
        _support_with_stances(
            (DisplayCapabilityStance.supported(_FakeCapability()),),
            is_builtin=False,
        )


# ---------------------------------------------------------------------------
# AgentSupport.capability() lookup
# ---------------------------------------------------------------------------


def test_support_capability_lookup_returns_matching_stance() -> None:
    full = tuple(
        DisplayCapabilityStance.not_applicable(c, f"reason for {c.name}")
        for c in all_display_capabilities()
    )
    support = _support_with_stances(full, is_builtin=True)
    for capability in all_display_capabilities():
        stance = support.capability(capability)
        assert stance is not None
        assert stance.capability is capability
        assert stance.kind == "not_applicable"


def test_support_capability_lookup_accepts_surface_name_string() -> None:
    full = tuple(DisplayCapabilityStance.supported(c) for c in all_display_capabilities())
    support = _support_with_stances(full, is_builtin=True)
    for capability in all_display_capabilities():
        stance = support.capability(capability.value)
        assert stance is not None
        assert stance.capability is capability


def test_support_capability_lookup_returns_none_when_missing() -> None:
    support = _support_with_stances((), is_builtin=False)
    assert support.capability(DisplayCapability.SYNTAX_HIGHLIGHTING) is None
    assert support.capability("syntax_preview") is None


# ---------------------------------------------------------------------------
# BuiltinAgentSpec / builtin.py: the 8 built-ins must carry a complete
# declaration; the four mutation paths the plan calls out must bite.
# ---------------------------------------------------------------------------


def test_every_builtin_agent_declares_a_complete_capability_set() -> None:
    expected = set(all_display_capabilities())
    for support in builtin_supports():
        declared = {stance.capability for stance in support.display_capabilities}
        assert declared == expected, (
            f"Built-in {support.name!r} declared {declared}, expected {expected}"
        )


def test_every_builtin_agent_has_a_stance_for_each_capability() -> None:
    for support in builtin_supports():
        for capability in all_display_capabilities():
            stance = support.capability(capability)
            assert stance is not None, (
                f"Built-in {support.name!r} missing stance for {capability.name!r}"
            )


def test_no_builtin_declaration_carries_a_reasonless_unsupported_stance() -> None:
    """Reasons for ``NOT_APPLICABLE`` / ``UNIMPLEMENTED`` must be non-empty.

    This pins the rule at the built-in level: the validator catches a
    blank reason at construction time, but a regression that bypassed
    that validator (e.g. by passing a populated ``DisplayCapabilityStance``
    with a whitespace-only reason that survived stripping) is locked in
    here.
    """
    for support in builtin_supports():
        for stance in support.display_capabilities:
            if stance.kind == "supported":
                continue
            stripped = stance.reason.strip()
            assert stripped, (
                f"Built-in {support.name!r} carries a blank reason for "
                f"{stance.capability.name!r} ({stance.kind!r})"
            )


def test_mutating_builtin_supports_to_remove_a_stance_fails_construction() -> None:
    """Removing one stance from the contract breaks every built-in."""
    # Capture a complete declaration, then drop one capability to simulate
    # a future maintainer forgetting a required stance. The complete
    # BuiltinAgentSpec flow must fail closed.
    base = builtin_supports()[0]
    broken = tuple(
        stance
        for stance in base.display_capabilities
        if stance.capability is not DisplayCapability.EDIT_DIFF
    )
    with pytest.raises(ValueError, match="missing display_capabilities"):
        AgentSupport(
            name=base.name,
            spec=base.spec,
            parser_factory=base.parser_factory,
            strategy_factory=base.strategy_factory,
            config=base.config,
            is_builtin=True,
            display_capabilities=broken,
        )


def test_mutating_builtin_supports_to_duplicate_a_stance_fails_construction() -> None:
    """Duplicating one stance breaks the built-in (no two stances for the same capability)."""
    base = builtin_supports()[0]
    double = base.display_capabilities + base.display_capabilities[:1]
    with pytest.raises(ValueError, match="Duplicate"):
        AgentSupport(
            name=base.name,
            spec=base.spec,
            parser_factory=base.parser_factory,
            strategy_factory=base.strategy_factory,
            config=base.config,
            is_builtin=True,
            display_capabilities=double,
        )


def test_mutating_builtin_supports_to_add_a_new_agent_without_complete_declaration_fails() -> None:
    """Adding a ninth agent without a complete declaration must fail closed."""
    # A BuiltinAgentSpec with display_capabilities=() should fail at
    # AgentSupport construction time.
    config = AgentConfig(
        cmd="newagent",
        json_parser=JsonParserType.GENERIC,
        transport=AgentTransport.GENERIC,
    )
    with pytest.raises(ValueError, match="must declare display_capabilities"):
        AgentSupport(
            name="newagent",
            spec=AgentSpec(name="newagent", transport=AgentTransport.GENERIC),
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            config=config,
            is_builtin=True,
            display_capabilities=(),
        )


# ---------------------------------------------------------------------------
# BuiltinAgentSpec.to_support wires display_capabilities through end to end.
# ---------------------------------------------------------------------------


def test_builtin_agent_spec_to_support_forwards_capabilities_unchanged() -> None:
    """The dataclass -> AgentSupport path must NOT lose DisplayCapabilityStance identity."""
    full = tuple(DisplayCapabilityStance.supported(c) for c in all_display_capabilities())
    spec = BuiltinAgentSpec(
        transport=AgentTransport.OPENCODE,
        parser_factory=OpenCodeParser,
        strategy_factory=OpenCodeExecutionStrategy,
        json_parser=JsonParserType.OPENCODE,
        cmd="opencode-test",
        display_capabilities=full,
    )
    support = spec.to_support("opencode-test")
    assert support.is_builtin
    assert tuple(stance.capability for stance in support.display_capabilities) == all_display_capabilities()
    for stance in support.display_capabilities:
        assert isinstance(stance, DisplayCapabilityStance)


def test_builtin_agent_spec_without_capabilities_fails_at_to_support() -> None:
    """A built-in declared without capabilities must fail closed at the to_support boundary."""
    spec = BuiltinAgentSpec(
        transport=AgentTransport.CLAUDE,
        parser_factory=ClaudeParser,
        strategy_factory=ClaudeExecutionStrategy,
        json_parser=JsonParserType.CLAUDE,
        cmd="claude -p",
    )
    with pytest.raises(ValueError, match="must declare display_capabilities"):
        spec.to_support("claude-no-caps")


def test_builtin_agent_spec_with_incomplete_capabilities_fails_at_to_support() -> None:
    """Built-in with missing capability fails closed."""
    incomplete = (
        DisplayCapabilityStance.supported(DisplayCapability.SYNTAX_HIGHLIGHTING),
    )
    spec = BuiltinAgentSpec(
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        parser_factory=ClaudeInteractiveParser,
        strategy_factory=ClaudeInteractiveExecutionStrategy,
        json_parser=JsonParserType.CLAUDE,
        cmd="claude",
        interactive=True,
        display_capabilities=incomplete,
    )
    with pytest.raises(ValueError, match="missing display_capabilities"):
        spec.to_support("claude-incomplete")
