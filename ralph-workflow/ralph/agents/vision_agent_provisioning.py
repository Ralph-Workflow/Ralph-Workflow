"""Provision the vision-verdict agent when the design-system policy applies.

The vision-verdict agent (see
:file:`ralph/agents/content/vision-verdict-agent.md`) is the
vision-capable subagent that the parent dispatches to compare the
retained pre-change visual capture set against a fresh post-change
capture set and submit the criterion 8 ``design_verdict`` artifact.

Per ADR-0002 and the S-17 plan item, the agent is **conditional**: it
is provisioned only when the design-system policy is in scope for the
active workspace. On any other workspace the parent is fail-closed
against criteria 13/15 and a non-functional vision-verdict agent
would only mask the failure mode the contract exists to expose.

This module is the single source of truth for the provisioning
predicate and the catalog registration call. It exposes:

* :func:`is_design_system_policy_in_scope` — the boolean predicate
  that decides whether the agent should be provisioned.
* :func:`vision_verdict_agent_support` — the factory that returns
  the :class:`ralph.agents.support.AgentSupport` instance for the
  vision-verdict agent. Idempotent: every call returns a fresh
  support built from the same frozen kwargs.
* :func:`provision_vision_verdict_agent` — the registry helper
  that wires the support into the caller-owned
  :class:`ralph.agents.catalog.AgentCatalog`. Returns ``True`` when
  the support was added, ``False`` when the design-system policy
  is not in scope (the call is a no-op, not an error).
* :func:`is_vision_verdict_agent_registered` — the read-side
  helper that downstream callers (the parent dispatcher, the
  criterion 13/15 gate) use to ask the catalog whether the
  vision-verdict agent is currently wired in.

The agent's transport is :attr:`AgentTransport.GENERIC` because
vision judgement runs in-process against the wire-ledger
artifacts; the agent does not shell out to an external vision
model. That is a deliberate choice to keep the criterion 8
evidence chain inside the trust boundary rather than delegating
it to a third-party service the criterion 8 contract has no way
to audit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.builtin_spec import vision_verdict_agent_spec
from ralph.agents.registration import register_agent_support_to_catalog

if TYPE_CHECKING:
    from ralph.agents.catalog import AgentCatalog
    from ralph.agents.support import AgentSupport
    from ralph.language_detector.models import ProjectStack
    from ralph.workspace.protocol import Workspace

#: Canonical name under which the vision-verdict agent is registered
#: in the catalog. The naming convention is hyphenated, matching the
#: ``ralph://media/{artifact_id}`` and ``.agent/tmp/visual-baseline``
#: surface area; it is the lookup key callers use when they ask the
#: catalog for the vision-verdict support.
VISION_VERDICT_AGENT_NAME: str = "vision-verdict"

#: Citation string for the design-system policy. The provisioning
#: predicate consults :func:`ralph.project_policy.evidence.design_system_required`
#: which is the canonical S-17 plan-item reference; the citation is
#: repeated here so the agent's documentation in this module stays
#: greppable without a round-trip into ``project_policy.evidence``.
_DESIGN_SYSTEM_POLICY_CITATION: str = (
    "ralph-workflow/ralph/project_policy/evidence.py:132 "
    "(design_system_required)"
)


def is_design_system_policy_in_scope(
    workspace: Workspace | None = None,
    stack: ProjectStack | None = None,
) -> bool:
    """Return True when the design-system policy applies to the workspace.

    A ``None`` workspace or stack is treated as "no design-system
    policy in scope" — the predicate is fail-closed by default so a
    caller that has not consulted the project-policy evidence layer
    cannot accidentally provision the agent. The canonical call site
    supplies both arguments after running
    :func:`ralph.project_policy.evidence.design_system_required`.

    Args:
        workspace: The active workspace protocol object. Optional
            for testability, but the production caller MUST pass the
            real workspace so the deterministic signal set is
            available.
        stack: The detected project stack. Optional for
            testability, but the production caller MUST pass the
            real :class:`ProjectStack` so the framework and CSS
            family signals are available.

    Returns:
        ``True`` when the design-system policy is in scope,
        ``False`` otherwise.
    """
    if workspace is None or stack is None:
        return False
    from ralph.project_policy.evidence import (  # noqa: PLC0415  # reason: lazy import keeps the no-arg test path free of evidence.py
        design_system_required,
    )

    required, _consulted = design_system_required(workspace, stack)
    return bool(required)


def vision_verdict_agent_support() -> AgentSupport:
    """Build the :class:`AgentSupport` for the vision-verdict agent.

    The factory is idempotent: every invocation returns a fresh
    :class:`AgentSupport` carrying the same frozen kwargs. The
    transport is :attr:`AgentTransport.GENERIC` because the agent
    reads from the wire ledger rather than spawning a subprocess;
    the parser and strategy are the canonical
    :class:`GenericParser` and :class:`GenericExecutionStrategy`
    pair.

    The agent is registered with ``is_builtin=True`` so the catalog
    treats it as a reserved built-in name and the existing
    built-in audit (see
    :mod:`tests.agents.test_builtin_spec_consolidation`) can be
    extended to include it. The agent is non-interactive, does
    not commit, and does not auto-apply a session template; the
    four ``is_builtin=True`` display capabilities are declared
    honestly — all three display surfaces are NOT_APPLICABLE for
    an in-process vision judge (it does not produce a file
    preview, syntax highlighting, or edit diff surface; the
    surfaces it produces are pixel-level observations that flow
    into the verdict artifact rather than the agent TUI).

    Returns:
        A fresh :class:`AgentSupport` carrying
        ``is_builtin=True`` and the vision-verdict kwargs.
    """
    return vision_verdict_agent_spec().to_support(VISION_VERDICT_AGENT_NAME)


def provision_vision_verdict_agent(
    catalog: AgentCatalog,
    *,
    workspace: Workspace | None = None,
    stack: ProjectStack | None = None,
) -> bool:
    """Register the vision-verdict agent in ``catalog`` if the design-system policy applies.

    The function is the single registry entry point for the
    vision-verdict agent. It is the call site the agent bootstrap
    path uses (see
    :mod:`ralph.agents.vision_agent_provisioning`) and the
    call site the project-policy preflight uses to surface
    "the design-system policy is in scope and the vision-verdict
    agent is wired" in the readiness report.

    Idempotent: a second call when the agent is already
    registered is a no-op (the catalog's ``add`` raises on a
    duplicate, and we catch that case explicitly so a repeated
    bootstrap does not turn into a hard error).

    Args:
        catalog: The caller-owned
            :class:`ralph.agents.catalog.AgentCatalog`. The
            catalog is mutated in place; the caller owns the
            write through.
        workspace: The active workspace protocol object. Passed
            through to :func:`is_design_system_policy_in_scope`.
        stack: The detected project stack. Passed through to
            :func:`is_design_system_policy_in_scope`.

    Returns:
        ``True`` when the vision-verdict agent was registered
        in this call, ``False`` when the design-system policy is
        not in scope (and the catalog is left untouched).
    """
    if not is_design_system_policy_in_scope(workspace=workspace, stack=stack):
        return False
    if catalog.get(VISION_VERDICT_AGENT_NAME) is not None:
        # Idempotent: a second call is a no-op, not a hard error.
        return True
    support = vision_verdict_agent_support()
    register_agent_support_to_catalog(VISION_VERDICT_AGENT_NAME, support, catalog)
    return True


def is_vision_verdict_agent_registered(catalog: AgentCatalog) -> bool:
    """Return True when the vision-verdict agent is wired into ``catalog``.

    Read-side helper the parent dispatcher and the criterion 13/15
    gate use to ask the catalog whether the agent is available. A
    False return is a fail-closed signal: the gate MUST treat the
    perception/visual-completion lane as failed and the parent
    MUST NOT attempt to dispatch the agent by name.

    Args:
        catalog: The catalog to consult.

    Returns:
        ``True`` when the agent is wired in, ``False`` otherwise.
    """
    return catalog.get(VISION_VERDICT_AGENT_NAME) is not None


__all__ = [
    "VISION_VERDICT_AGENT_NAME",
    "is_design_system_policy_in_scope",
    "is_vision_verdict_agent_registered",
    "provision_vision_verdict_agent",
    "vision_verdict_agent_support",
]
