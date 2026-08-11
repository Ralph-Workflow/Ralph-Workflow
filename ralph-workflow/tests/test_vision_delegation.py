"""Tests for S-17 vision-verdict agent provisioning and delegation capability coverage.

This file pins the S-17 contract:

1. The vision-verdict agent is provisioned in the catalog when the
   design-system policy is in scope for the active workspace.
2. The vision-verdict agent is NOT provisioned when the design-system
   policy is NOT in scope, even when the catalog is fresh.
3. Every :class:`ralph.config.agent_transport.AgentTransport` member
   has a matching :class:`ralph.agents.delegation_capabilities.DelegationCapability`
   entry — the per-transport delegation contract S-2 introduced
   and S-17 extends is complete (one entry per transport, no
   duplicates, no orphan declarations).

The three tests are scoped to the S-17 seam and do NOT exercise the
criterion 8 verdict logic itself (that is owned by
:mod:`ralph.visual.design_verdict` and tested in
:mod:`tests.test_visual_verdict_policy_alignment`).
"""

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
    is_vision_verdict_agent_registered,
    provision_vision_verdict_agent,
    vision_verdict_agent_support,
)
from ralph.config.agent_transport import AgentTransport

if TYPE_CHECKING:
    from ralph.language_detector.models import ProjectStack
    from ralph.workspace.protocol import Workspace


def _design_system_in_scope_workspace() -> Workspace:
    """Return a workspace where the design-system policy is in scope.

    The deterministic signal set inside
    :func:`ralph.project_policy.evidence.design_system_required`
    looks at ``stack.frameworks`` (a UI framework triggers it) and
    ``stack.secondary_languages`` (a CSS-family language triggers
    it). A :class:`MemoryWorkspace` with a ``package.json`` plus a
    :class:`ProjectStack` that reports ``React`` is sufficient for
    the detector to return ``True``.

    The test does NOT depend on the detector's exact signal list:
    if a future refactor adds new signal sources, the
    ``_design_system_in_scope_stack`` helper still produces a stack
    that satisfies the canonical ``UI_FRAMEWORK_SIGNALS`` set the
    detector consults.
    """
    from ralph.workspace.memory import (
        MemoryWorkspace,  # reason: lazy import keeps the no-arg path free of MemoryWorkspace
    )

    workspace = MemoryWorkspace()
    workspace.write("package.json", '{"name": "demo", "dependencies": {"react": "1.0.0"}}')
    return workspace


def _design_system_in_scope_stack() -> ProjectStack:
    """Return a :class:`ProjectStack` that reports React as a framework."""
    from ralph.language_detector.models import (  # reason: lazy import keeps the no-arg path free of the language detector
        ProjectStack,
    )

    return ProjectStack(
        primary_language="JavaScript",
        frameworks=["React"],
        secondary_languages=["CSS"],
    )


def _design_system_out_of_scope_workspace() -> Workspace:
    """Return a workspace with NO design-system triggers."""
    from ralph.workspace.memory import (
        MemoryWorkspace,  # reason: lazy import keeps the no-arg path free of MemoryWorkspace
    )

    return MemoryWorkspace()


def _design_system_out_of_scope_stack() -> ProjectStack:
    """Return a :class:`ProjectStack` with no UI or CSS signals."""
    from ralph.language_detector.models import (  # reason: lazy import keeps the no-arg path free of the language detector
        ProjectStack,
    )

    return ProjectStack(
        primary_language="Python",
        frameworks=[],
        secondary_languages=[],
    )


def test_vision_verdict_agent_is_provided_when_design_system_policy_applies() -> None:
    """provision_vision_verdict_agent wires the agent when design-system is in scope.

    The function must return ``True`` and the catalog must carry
    the ``vision-verdict`` support with ``is_builtin=True`` so the
    catalog treats the name as reserved. The agent's transport is
    :attr:`AgentTransport.GENERIC` because vision judgement runs
    in-process against the wire-ledger artifacts; shelling out to
    an external vision model would break the criterion 8 audit
    chain.
    """
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

    # Idempotent: a second call when the agent is already wired in
    # is a no-op that still returns True. The S-17 contract pins
    # this so a repeated bootstrap path never raises a duplicate
    # registration error.
    assert provision_vision_verdict_agent(catalog, workspace=workspace, stack=stack) is True
    assert catalog.get(VISION_VERDICT_AGENT_NAME) is support


def test_vision_verdict_agent_is_NOT_provided_when_no_design_system_policy() -> None:
    """provision_vision_verdict_agent is a no-op when the design-system policy is absent.

    The parent dispatcher is fail-closed against criteria 13/15 in
    this case; provisioning a non-functional agent would mask the
    failure mode the contract exists to expose. The function must
    return ``False`` and the catalog must remain empty of the
    vision-verdict entry.
    """
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

    # A workspace with no policy but a stacked AgentRegistry must
    # also stay empty: the registry's provision_vision_verdict_agent
    # must thread the policy through the same predicate the
    # direct call site uses, and must NOT silently fall through to
    # the unconditional registration path.
    registry = AgentRegistry(catalog=catalog)
    registry_provisioned = registry.provision_vision_verdict_agent(
        workspace=workspace, stack=stack,
    )
    assert registry_provisioned is False, (
        "AgentRegistry.provision_vision_verdict_agent must return False when the design-system policy is absent"
    )
    assert registry.get(VISION_VERDICT_AGENT_NAME) is None, (
        "AgentRegistry must not retain a vision-verdict entry when the design-system policy is absent"
    )

    # Negative path through the direct registry path: patch the
    # predicate to assert the registry consults it (and does not
    # bypass it on the way to a hard-coded provisioning call).
    with patch(
        "ralph.agents.vision_agent_provisioning.is_design_system_policy_in_scope",
        return_value=False,
    ) as predicate:
        catalog2 = AgentCatalog()
        registered2 = provision_vision_verdict_agent(
            catalog2, workspace=workspace, stack=stack,
        )
        assert registered2 is False
        assert predicate.called, (
            "provision_vision_verdict_agent must consult is_design_system_policy_in_scope; "
            "a hard-coded unconditional provision would be a bypass"
        )
        assert catalog2.get(VISION_VERDICT_AGENT_NAME) is None

    # Positive path through the same patched predicate: a True
    # result MUST wire the agent in even when the real detector
    # would return False. This pins the registry's behavior
    # against the same predicate (a future refactor that adds a
    # second predicate must keep the wire-in path consistent).
    with patch(
        "ralph.agents.vision_agent_provisioning.is_design_system_policy_in_scope",
        return_value=True,
    ):
        catalog3 = AgentCatalog()
        registered3 = provision_vision_verdict_agent(
            catalog3, workspace=workspace, stack=stack,
        )
        assert registered3 is True
        assert catalog3.get(VISION_VERDICT_AGENT_NAME) is not None
        # Re-confirm the support shape is the canonical factory output.
        canonical = vision_verdict_agent_support()
        wired = catalog3.get(VISION_VERDICT_AGENT_NAME)
        assert wired is not None
        assert wired.is_builtin is canonical.is_builtin
        assert wired.spec.transport is canonical.spec.transport


def test_delegation_capability_stance_is_complete_for_all_transports() -> None:
    """Every :class:`AgentTransport` member has a complete delegation declaration.

    The S-2 contract pinned one :class:`DelegationCapability` per
    transport; S-17 extends the contract by adding the
    ``vision-verdict`` agent to the catalog and routing the
    criterion 8 evidence through the wire ledger. The "complete"
    property in this test has three parts:

    1. **Coverage**: every transport is declared (no orphan
       transports, no orphan declarations).
    2. **Vocabulary**: the stance vocabulary is the canonical
       three-valued tri-state. Adding a fourth stance is a
       breaking change to the delegation contract; the test pins
       the count and the labels.
    3. **Per-stance shape**: every entry carries a non-empty
       citation (evidence provenance), every SUPPORTED entry
       carries a non-empty mechanism (the dispatch path the
       parallelization planner branches on), and every
       NOT_APPLICABLE entry carries an empty mechanism (the
       question is structurally undefined for that transport).

    A future enum addition or stance addition will fail one of
    these three properties at the import-time or at this test
    boundary, surfacing the regression in CI rather than at the
    first parallelization planner invocation.
    """
    # (1) Coverage: every transport declared exactly once.
    declared_transports = {entry.transport for entry in all_delegation_capabilities()}
    assert declared_transports == set(AgentTransport), (
        "Delegation declaration must cover each AgentTransport exactly once; "
        f"missing={sorted(set(AgentTransport) - declared_transports, key=str)}, "
        f"extra={sorted(declared_transports - set(AgentTransport), key=str)}"
    )

    # (1) Lookup: delegation_for resolves every transport without KeyError.
    for transport in AgentTransport:
        entry = delegation_for(transport)
        assert isinstance(entry, DelegationCapability)
        assert entry.transport is transport

    # (2) Vocabulary: the tri-state stance is the canonical three.
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

    # (3) Per-stance shape: citation is always present; mechanism
    #     is present when stance is SUPPORTED, empty when stance
    #     is NOT_APPLICABLE. EXPLICIT_UNSUPPORTED is permissive
    #     on the mechanism (some transports carry a reason, some
    #     do not) but MUST still carry a citation.
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
        # EXPLICIT_UNSUPPORTED: mechanism may be present or empty,
        # but the entry MUST remain self-describing via the
        # citation. The check above already enforces that.

    # (1) Stanza: no duplicate mechanism across non-empty
    #     declarations. The check is restricted to non-empty
    #     mechanisms because NOT_APPLICABLE entries legitimately
    #     carry an empty string.
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


# Marker so the file is recognized as a test module by ``pytest
# --collect-only`` and the budget tracker.
_ = pytest
