from __future__ import annotations

import ast

from ralph.testing._single_owner_rules import SingleOwnerViolation

_PRIVATE_LEDGER_COUNTERS: frozenset[str] = frozenset(
    {
        "is_spent",
        "spent_reason",
        "cooldown_until_ms",
        "consecutive_unproductive",
        "total_unproductive",
    }
)

_PRIVATE_FAILURE_OWNER_NAMES: frozenset[str] = frozenset(
    {
        "_backoff_attempts",
        "_unavailability_tracker",
        "_mark_agent_unavailable",
    }
)

_PRIVATE_FAILURE_OWNER_SERIALISED_KEYS: frozenset[str] = frozenset(
    {
        "backoff_attempts",
        "unavailable_timeouts",
    }
)

_SERIALISED_SNAPSHOT_METHODS: frozenset[str] = frozenset(
    {
        "snapshot",
        "to_snapshot",
        "export_snapshot",
    }
)

_GETATTR_MIN_ARGS = 2


def _is_private_failure_owner_name(name: str) -> bool:
    return name in _PRIVATE_FAILURE_OWNER_NAMES


def _is_ledger_serialised_root(node: ast.AST) -> bool:
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
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr in _SERIALISED_SNAPSHOT_METHODS
    return isinstance(func, ast.Name) and func.id in _SERIALISED_SNAPSHOT_METHODS


def _serialised_counter_violation(
    node: ast.expr, counter: str, rel_path: str
) -> list[SingleOwnerViolation]:
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
