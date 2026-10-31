"""Test-side rule helpers for the agent failure ledger audit.

This private module holds the policy data and violation builders that keep
tests on the public seam of the failure owners: the ledger counters only
:class:`ralph.recovery.agent_failure_ledger.AgentFailureLedger` may own or
serialise, the recovery controller's private failure-owner state (its backoff
counter, unavailability store handle, and agent-unavailable marker), and the
same private state read through the controller's SERIALISED snapshot
(``snapshot()["backoff_attempts"]`` and friends). The audit entry point
(:mod:`ralph.testing.audit_agent_failure_single_owner`) applies these helpers
in its test-side rules.
"""

from __future__ import annotations

import ast

from ralph.testing._single_owner_rules import SingleOwnerViolation

#: Counter names that only the AgentFailureLedger may own, expose, or serialise.
_PRIVATE_LEDGER_COUNTERS: frozenset[str] = frozenset(
    {
        "is_spent",
        "spent_reason",
        "cooldown_until_ms",
        "consecutive_unproductive",
        "total_unproductive",
    }
)

#: Private attributes that own the recovery controller's failure-decision
#: state (the per-agent backoff counter, the unavailability store handle, and
#: the helper that marks an agent unavailable). These names are declared on no
#: production class other than :class:`ralph.recovery.controller.RecoveryController`
#: and :class:`ralph.recovery.agent_unavailability_tracker.AgentUnavailabilityTracker`,
#: so a NAME-based rule catches a test that couples to the private failure-owner
#: state without a receiver heuristic and without collateral on unrelated
#: private attributes.
_PRIVATE_FAILURE_OWNER_NAMES: frozenset[str] = frozenset(
    {
        "_backoff_attempts",
        "_unavailability_tracker",
        "_mark_agent_unavailable",
    }
)

#: Snapshot keys that publish the recovery controller's private failure-owner
#: state under a public key name: ``backoff_attempts`` serialises
#: ``_backoff_attempts`` and ``unavailable_timeouts`` serialises the
#: unavailability store's timeout history (``_unavailability_tracker``).
#: Indexing one of these keys out of a serialised snapshot is the same coupling
#: as reading the private attribute directly, so the audit rejects it too.
_PRIVATE_FAILURE_OWNER_SERIALISED_KEYS: frozenset[str] = frozenset(
    {
        "backoff_attempts",
        "unavailable_timeouts",
    }
)

#: Method names that publish a serialised snapshot of owner state. Reading a
#: private failure-owner key out of one of these calls is the serialised form
#: of the direct-access rule.
_SERIALISED_SNAPSHOT_METHODS: frozenset[str] = frozenset(
    {
        "snapshot",
        "to_snapshot",
        "export_snapshot",
    }
)

_GETATTR_MIN_ARGS = 2


def _is_private_failure_owner_name(name: str) -> bool:
    """Return True when a name is private failure-owner state.

    The recovery controller owns the agent-failure decision state. A test that
    reads these private names couples to that state instead of observing the
    controller's public answers, so the name-based rule rejects the read.
    """
    return name in _PRIVATE_FAILURE_OWNER_NAMES


def _is_ledger_serialised_root(node: ast.AST) -> bool:
    """Check if an expression reads the ledger's serialised state.

    A serialised root is the persisted ledger snapshot itself, the ledger
    object that owns it, or a read chained off either of them. The owner's
    public answers (``status()``, ``is_available()``, ``record()``) are not
    serialised roots: they are the answers a test is supposed to observe.
    """
    if isinstance(node, ast.Attribute):
        return node.attr in ("ledger", "ledger_snapshot") or _is_ledger_serialised_root(node.value)
    if isinstance(node, ast.Name):
        return "ledger" in node.id.lower()
    if isinstance(node, ast.Subscript):
        return _is_ledger_serialised_root(node.value)
    if isinstance(node, ast.Call):
        func = node.func
        return (
            isinstance(func, ast.Attribute)
            and func.attr in ("snapshot", "get", "items", "values")
            and _is_ledger_serialised_root(func.value)
        )
    return False


def _is_serialised_snapshot_call(node: ast.AST) -> bool:
    """Return True when an expression is a call that publishes a serialised snapshot.

    A serialised snapshot is the owner's own published view of its state
    (``controller.snapshot()`` and friends, including the bare ``snapshot()``
    spelling). The private failure-owner keys are the discriminator for the
    serialised-access rule, so the receiver shape is not part of the decision:
    no other published snapshot in the tree carries the backoff-counter or
    unavailability-timeout keys.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr in _SERIALISED_SNAPSHOT_METHODS
    return isinstance(func, ast.Name) and func.id in _SERIALISED_SNAPSHOT_METHODS


def _serialised_counter_violation(
    node: ast.expr, counter: str, rel_path: str
) -> list[SingleOwnerViolation]:
    """Build the violation for a test reading a serialised ledger counter."""
    return [
        SingleOwnerViolation(
            path=rel_path,
            line=node.lineno,
            rule="test_serialised_ledger_access",
            detail=(
                f"Test reads the serialised ledger counter '{counter}'; "
                "tests must assert through the owner's public answers "
                "(status, is_available, record) and hand a snapshot back to the "
                "owner's public restore API instead of indexing into it."
            ),
        )
    ]


def private_failure_owner_violation(
    node: ast.expr, name: str, rel_path: str, *, via_getattr: bool = False
) -> list[SingleOwnerViolation]:
    """Build the violation for a test reading private failure-owner state.

    The recovery controller owns the agent-failure decision state; a test must
    observe that state through the controller's public seam (``snapshot()``,
    ``unavailability_store``) instead of reaching into its private attributes.
    """
    access = "via getattr" if via_getattr else "directly"
    return [
        SingleOwnerViolation(
            path=rel_path,
            line=node.lineno,
            rule="test_private_failure_owner_access",
            detail=(
                f"Test accesses private failure-owner attribute '{name}' {access}; "
                "tests must use the public seam only."
            ),
        )
    ]


def serialised_failure_owner_violation(
    node: ast.expr, key: str, rel_path: str
) -> list[SingleOwnerViolation]:
    """Build the violation for a test reading serialised failure-owner state.

    The recovery controller publishes its private failure-owner state inside its
    snapshot under public key names. Indexing one of those keys out of the
    snapshot couples the test to the same private state that the direct-access
    rule already rejects, so the serialised form is rejected too.
    """
    return [
        SingleOwnerViolation(
            path=rel_path,
            line=node.lineno,
            rule="test_serialised_failure_owner_access",
            detail=(
                f"Test reads the serialised failure-owner state '{key}' from a "
                "controller snapshot; tests must observe the controller's public "
                "answers instead of indexing into its snapshot."
            ),
        )
    ]
