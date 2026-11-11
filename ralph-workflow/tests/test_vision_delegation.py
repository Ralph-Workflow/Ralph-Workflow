from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.agents.catalog import AgentCatalog
from ralph.agents.delegation_capabilities import (
    DelegationCapability,
    DelegationStance,
    all_delegation_capabilities,
    delegation_for,
)
from ralph.agents.registry import AgentRegistry
from ralph.agents.vision_agent_provisioning import (
    VISION_VERDICT_AGENT_NAME,
    VisionVerdictDispatch,
    dispatch_vision_verdict,
    is_vision_verdict_agent_registered,
    provision_vision_verdict_agent,
    vision_verdict_agent_support,
)
from ralph.config.agent_transport import AgentTransport

if TYPE_CHECKING:
    from ralph.language_detector.models import ProjectStack
    from ralph.workspace.protocol import Workspace


def _design_system_in_scope_workspace() -> Workspace:
    from ralph.workspace.memory import (
        MemoryWorkspace,
    )

    workspace = MemoryWorkspace()
    workspace.write("package.json", '{"name": "demo", "dependencies": {"react": "1.0.0"}}')
    return workspace


def _design_system_in_scope_stack() -> ProjectStack:
    from ralph.language_detector.models import (
        ProjectStack,
    )

    return ProjectStack(
        primary_language="JavaScript",
        frameworks=["React"],
        secondary_languages=["CSS"],
    )


def _design_system_out_of_scope_workspace() -> Workspace:
    from ralph.workspace.memory import (
        MemoryWorkspace,
    )

    return MemoryWorkspace()


def _design_system_out_of_scope_stack() -> ProjectStack:
    from ralph.language_detector.models import (
        ProjectStack,
    )

    return ProjectStack(
        primary_language="Python",
        frameworks=[],
        secondary_languages=[],
    )


def test_vision_verdict_agent_is_provided_when_design_system_policy_applies() -> None:
    catalog = AgentCatalog()
    workspace = _design_system_in_scope_workspace()
    stack = _design_system_in_scope_stack()

    registered = provision_vision_verdict_agent(catalog, workspace=workspace, stack=stack)

    assert registered is True, (
        "vision-verdict agent must be registered when the design-system policy is in scope"
    )
    assert is_vision_verdict_agent_registered(catalog) is True, (
        "is_vision_verdict_agent_registered must return True after a successful provision"
    )

    support = catalog.get(VISION_VERDICT_AGENT_NAME)
    assert support is not None, (
        f"catalog must carry a support for {VISION_VERDICT_AGENT_NAME!r} after provisioning"
    )
    assert support.is_builtin is True, (
        "vision-verdict must be registered as a built-in so the catalog treats the name as reserved"
    )
    assert support.name == VISION_VERDICT_AGENT_NAME
    assert support.spec.transport is AgentTransport.GENERIC, (
        "vision-verdict must use the GENERIC transport (in-process vision judge)"
    )

    assert provision_vision_verdict_agent(catalog, workspace=workspace, stack=stack) is True
    assert catalog.get(VISION_VERDICT_AGENT_NAME) is support


def test_vision_verdict_agent_is_NOT_provided_when_no_design_system_policy() -> None:
    catalog = AgentCatalog()
    workspace = _design_system_out_of_scope_workspace()
    stack = _design_system_out_of_scope_stack()

    registered = provision_vision_verdict_agent(catalog, workspace=workspace, stack=stack)

    assert registered is False, (
        "vision-verdict agent must NOT be registered when the design-system policy is not in scope"
    )
    assert is_vision_verdict_agent_registered(catalog) is False, (
        "is_vision_verdict_agent_registered must return False when the design-system policy is absent"
    )
    assert catalog.get(VISION_VERDICT_AGENT_NAME) is None, (
        "catalog must NOT carry a vision-verdict entry when the design-system policy is absent"
    )

    registry = AgentRegistry(catalog=catalog)
    registry_provisioned = registry.provision_vision_verdict_agent(
        workspace=workspace,
        stack=stack,
    )
    assert registry_provisioned is False, (
        "AgentRegistry.provision_vision_verdict_agent must return False when the design-system policy is absent"
    )
    assert registry.get(VISION_VERDICT_AGENT_NAME) is None, (
        "AgentRegistry must not retain a vision-verdict entry when the design-system policy is absent"
    )

    with patch(
        "ralph.agents.vision_agent_provisioning.is_design_system_policy_in_scope",
        return_value=False,
    ) as predicate:
        catalog2 = AgentCatalog()
        registered2 = provision_vision_verdict_agent(
            catalog2,
            workspace=workspace,
            stack=stack,
        )
        assert registered2 is False
        assert predicate.called, (
            "provision_vision_verdict_agent must consult is_design_system_policy_in_scope; "
            "a hard-coded unconditional provision would be a bypass"
        )
        assert catalog2.get(VISION_VERDICT_AGENT_NAME) is None

    with patch(
        "ralph.agents.vision_agent_provisioning.is_design_system_policy_in_scope",
        return_value=True,
    ):
        catalog3 = AgentCatalog()
        registered3 = provision_vision_verdict_agent(
            catalog3,
            workspace=workspace,
            stack=stack,
        )
        assert registered3 is True
        assert catalog3.get(VISION_VERDICT_AGENT_NAME) is not None
        canonical = vision_verdict_agent_support()
        wired = catalog3.get(VISION_VERDICT_AGENT_NAME)
        assert wired is not None
        assert wired.is_builtin is canonical.is_builtin
        assert wired.spec.transport is canonical.spec.transport


def test_delegation_capability_stance_is_complete_for_all_transports() -> None:
    declared_transports = {entry.transport for entry in all_delegation_capabilities()}
    assert declared_transports == set(AgentTransport), (
        "Delegation declaration must cover each AgentTransport exactly once; "
        f"missing={sorted(set(AgentTransport) - declared_transports, key=str)}, "
        f"extra={sorted(declared_transports - set(AgentTransport), key=str)}"
    )

    for transport in AgentTransport:
        entry = delegation_for(transport)
        assert isinstance(entry, DelegationCapability)
        assert entry.transport is transport

    stance_members = tuple(DelegationStance)
    stance_names = {member.name for member in stance_members}
    stance_values = {member.value for member in stance_members}
    assert stance_names == {"SUPPORTED", "EXPLICIT_UNSUPPORTED", "NOT_APPLICABLE"}, (
        f"DelegationStance must carry exactly the canonical tri-state labels; got {sorted(stance_names)}"
    )
    assert stance_values == {"supported", "explicit_unsupported", "not_applicable"}, (
        f"DelegationStance values must be the canonical lower-case tri-state; got {sorted(stance_values)}"
    )
    assert len(stance_members) == 3, (
        f"DelegationStance must have exactly 3 members (the canonical tri-state); got {len(stance_members)}"
    )

    for entry in all_delegation_capabilities():
        assert entry.citation.strip(), (
            f"Delegation entry for {entry.transport!r} must carry a non-empty citation"
        )
        if entry.stance is DelegationStance.SUPPORTED:
            assert entry.mechanism.strip(), (
                f"Transport {entry.transport!r} is SUPPORTED but carries an empty mechanism; "
                "the parallelization planner cannot dispatch on an empty path"
            )
        elif entry.stance is DelegationStance.NOT_APPLICABLE:
            assert entry.mechanism == "", (
                f"Transport {entry.transport!r} is NOT_APPLICABLE but carries a non-empty mechanism: "
                f"{entry.mechanism!r}; NOT_APPLICABLE has no dispatch path to describe"
            )

    seen_mechanisms: dict[str, AgentTransport] = {}
    for entry in all_delegation_capabilities():
        mechanism = entry.mechanism.strip()
        if not mechanism:
            continue
        assert mechanism not in seen_mechanisms, (
            f"Duplicate mechanism {mechanism!r} shared by transports "
            f"{seen_mechanisms[mechanism]!r} and {entry.transport!r}; "
            "duplicate mechanisms would collapse two distinct transports onto the same dispatch path"
        )
        seen_mechanisms[mechanism] = entry.transport


def test_delegated_vision_verdict_is_attributed_to_the_subagent() -> None:
    dispatched: list[VisionVerdictDispatch] = []

    def invoke(request: VisionVerdictDispatch) -> str:
        dispatched.append(request)
        return "verdict-1"

    verdict_id = dispatch_vision_verdict(
        target="fixture-ui",
        intent="Improve hierarchy",
        before_handles=("ralph://media/before",),
        after_handles=("ralph://media/after",),
        delegated_agent_id="vision-verdict-123",
        invoke=invoke,
    )

    assert verdict_id == "verdict-1"
    assert dispatched == [
        VisionVerdictDispatch(
            target="fixture-ui",
            intent="Improve hierarchy",
            before_handles=("ralph://media/before",),
            after_handles=("ralph://media/after",),
            delegated_agent_id="vision-verdict-123",
        )
    ]


_ = pytest
