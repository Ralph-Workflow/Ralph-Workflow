from __future__ import annotations

from ralph.agents.delegation_capabilities import (
    DelegationCapability,
    DelegationStance,
    all_delegation_capabilities,
    delegation_for,
)
from ralph.config.agent_transport import AgentTransport


def test_delegation_stance_has_exactly_three_values() -> None:
    members = list(DelegationStance)
    names = {member.name for member in members}
    values = {member.value for member in members}
    assert names == {"SUPPORTED", "EXPLICIT_UNSUPPORTED", "NOT_APPLICABLE"}
    assert values == {"supported", "explicit_unsupported", "not_applicable"}
    assert len(members) == 3


def test_every_agent_transport_member_has_a_delegation_entry() -> None:
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
    for transport in AgentTransport:
        entry = delegation_for(transport)
        assert isinstance(entry, DelegationCapability)
        assert entry.transport is transport


def test_no_duplicate_mechanisms() -> None:
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
    seen: set[AgentTransport] = set()
    for entry in all_delegation_capabilities():
        assert entry.transport not in seen, (
            f"Duplicate DelegationCapability for transport {entry.transport!r}"
        )
        seen.add(entry.transport)


def test_supported_entries_carry_a_non_empty_mechanism() -> None:
    for entry in all_delegation_capabilities():
        if entry.stance is DelegationStance.SUPPORTED:
            assert entry.mechanism.strip(), (
                f"Transport {entry.transport!r} is SUPPORTED with an empty mechanism"
            )


def test_not_applicable_entries_carry_an_empty_mechanism() -> None:
    for entry in all_delegation_capabilities():
        if entry.stance is DelegationStance.NOT_APPLICABLE:
            assert entry.mechanism == "", (
                f"Transport {entry.transport!r} is NOT_APPLICABLE but carries a "
                f"non-empty mechanism: {entry.mechanism!r}"
            )


def test_every_entry_carries_a_non_empty_citation() -> None:
    for entry in all_delegation_capabilities():
        assert entry.citation.strip(), (
            f"Delegation entry for {entry.transport!r} has an empty citation"
        )


def test_all_delegation_capabilities_returns_a_tuple() -> None:
    result = all_delegation_capabilities()
    assert isinstance(result, tuple)
    assert all(isinstance(entry, DelegationCapability) for entry in result)


def test_agy_delegation_entry_is_declared_supported_from_measured_wire_evidence() -> None:
    entry = delegation_for(AgentTransport.AGY)
    assert entry.transport is AgentTransport.AGY
    assert entry.stance is DelegationStance.SUPPORTED
    assert "define_subagent" in entry.mechanism
    assert "invoke_subagent" in entry.mechanism
    assert "agy_wire_provenance" in entry.citation


def test_kimi_delegation_entry_is_declared_supported() -> None:
    entry = delegation_for(AgentTransport.KIMI)
    assert entry.transport is AgentTransport.KIMI
    assert entry.stance is DelegationStance.SUPPORTED
    assert "Agent" in entry.mechanism
    assert entry.mechanism.strip()
    assert entry.citation.strip()
