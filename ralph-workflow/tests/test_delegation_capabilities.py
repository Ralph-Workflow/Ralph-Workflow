"""Tests for the per-transport delegation-capability declaration contract.

The declaration is the canonical source of truth for which
:class:`AgentTransport` values can spawn a sub-agent / task during a
Ralph Workflow run. The tests in this file pin the three properties the
delegation contract depends on:

  1. Every :class:`AgentTransport` member has a
     :class:`DelegationCapability` entry. A future enum addition without
     a matching entry fails the import-time completeness check; this
     test pins the post-condition that the map is full from the
     outside.
  2. No two transports share a ``mechanism`` string. The free-form
     mechanism is the per-transport identifier an operator sees in
     documentation and the parallelization planner reads to choose a
     dispatch path; duplicate mechanisms would silently collapse two
     distinct transports onto the same path.
  3. :class:`DelegationStance` has exactly three values
     (``SUPPORTED``, ``EXPLICIT_UNSUPPORTED``, ``NOT_APPLICABLE``).
     Adding a fourth value is a breaking change to the tri-state
     vocabulary every downstream caller branches on, so the test pins
     the count and the labels.
"""

from __future__ import annotations

from ralph.agents.delegation_capabilities import (
    DelegationCapability,
    DelegationStance,
    all_delegation_capabilities,
    delegation_for,
)
from ralph.config.agent_transport import AgentTransport


def test_delegation_stance_has_exactly_three_values() -> None:
    """The stance vocabulary must be exactly SUPPORTED / EXPLICIT_UNSUPPORTED / NOT_APPLICABLE."""
    members = list(DelegationStance)
    names = {member.name for member in members}
    values = {member.value for member in members}
    assert names == {"SUPPORTED", "EXPLICIT_UNSUPPORTED", "NOT_APPLICABLE"}
    assert values == {"supported", "explicit_unsupported", "not_applicable"}
    assert len(members) == 3


def test_every_agent_transport_member_has_a_delegation_entry() -> None:
    """Every :class:`AgentTransport` member must be declared in the capability map."""
    declared = {entry.transport for entry in all_delegation_capabilities()}
    for transport in AgentTransport:
        assert transport in declared, (
            f"Transport {transport!r} is missing from the delegation declaration; "
            f"add a DelegationCapability for it in delegation_capabilities.py"
        )
    assert len(declared) == len(set(AgentTransport)), (
        "Delegation declaration must cover each AgentTransport exactly once; "
        f"declared={sorted(declared, key=str)}, "
        f"transports={sorted(set(AgentTransport), key=str)}"
    )


def test_delegation_for_resolves_every_agent_transport() -> None:
    """The :func:`delegation_for` lookup must resolve every transport without KeyError."""
    for transport in AgentTransport:
        entry = delegation_for(transport)
        assert isinstance(entry, DelegationCapability)
        assert entry.transport is transport


def test_no_duplicate_mechanisms() -> None:
    """No two declarations may carry the same ``mechanism`` string.

    The free-form ``mechanism`` is the per-transport identifier an
    operator reads in documentation and the parallelization planner
    branches on; duplicate mechanisms would silently collapse two
    distinct transports onto the same dispatch path. The check is
    restricted to non-empty mechanisms because
    :attr:`DelegationStance.NOT_APPLICABLE` entries legitimately carry
    an empty string.
    """
    seen: dict[str, AgentTransport] = {}
    for entry in all_delegation_capabilities():
        mechanism = entry.mechanism.strip()
        if not mechanism:
            continue
        if mechanism in seen:
            msg = (
                f"Duplicate mechanism {mechanism!r} shared by transports "
                f"{seen[mechanism]!r} and {entry.transport!r}"
            )
            raise AssertionError(msg)
        seen[mechanism] = entry.transport


def test_no_duplicate_declarations_for_the_same_transport() -> None:
    """The canonical tuple must carry exactly one entry per transport."""
    seen: set[AgentTransport] = set()
    for entry in all_delegation_capabilities():
        assert entry.transport not in seen, (
            f"Duplicate DelegationCapability for transport {entry.transport!r}"
        )
        seen.add(entry.transport)


def test_supported_entries_carry_a_non_empty_mechanism() -> None:
    """A SUPPORTED stance with an empty mechanism is silent about how it works."""
    for entry in all_delegation_capabilities():
        if entry.stance is DelegationStance.SUPPORTED:
            assert entry.mechanism.strip(), (
                f"Transport {entry.transport!r} is SUPPORTED with an empty mechanism"
            )


def test_not_applicable_entries_carry_an_empty_mechanism() -> None:
    """NOT_APPLICABLE stances carry no mechanism because the question does not apply."""
    for entry in all_delegation_capabilities():
        if entry.stance is DelegationStance.NOT_APPLICABLE:
            assert entry.mechanism == "", (
                f"Transport {entry.transport!r} is NOT_APPLICABLE but carries a "
                f"non-empty mechanism: {entry.mechanism!r}"
            )


def test_every_entry_carries_a_non_empty_citation() -> None:
    """No delegation declaration is silent about its evidence source."""
    for entry in all_delegation_capabilities():
        assert entry.citation.strip(), (
            f"Delegation entry for {entry.transport!r} has an empty citation"
        )


def test_all_delegation_capabilities_returns_a_tuple() -> None:
    """The public accessor must return an immutable tuple, not a list."""
    result = all_delegation_capabilities()
    assert isinstance(result, tuple)
    assert all(isinstance(entry, DelegationCapability) for entry in result)


def test_kimi_delegation_entry_is_declared_unsupported() -> None:
    """Kimi carries an evidence-grounded EXPLICIT_UNSUPPORTED delegation entry.

    The measured kimi-code model capabilities (thinking, always_thinking,
    image_in, tool_use) expose no sub-agent tool, so the headless transport
    declares delegation explicitly unsupported rather than silently
    defaulting to the generic stance.
    """
    entry = delegation_for(AgentTransport.KIMI)
    assert entry.transport is AgentTransport.KIMI
    assert entry.stance is DelegationStance.EXPLICIT_UNSUPPORTED
    assert entry.mechanism.strip()
    assert entry.citation.strip()
