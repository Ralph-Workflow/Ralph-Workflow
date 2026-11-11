from __future__ import annotations

import ast

from ralph.testing._single_owner_rules import SingleOwnerViolation, _is_ledger_receiver

_RETRY_NAME_STEMS: tuple[str, ...] = ("retry", "retries", "retrying", "retried")

_RETRY_CAP_TOKENS: tuple[str, ...] = (
    "max",
    "cap",
    "limit",
    "budget",
    "allowance",
    "threshold",
    "ceiling",
)

_AGENT_FATE_ACTION_TOKENS: tuple[str, ...] = (
    "advance",
    "fall_over",
    "fallback",
    "next_agent",
    "select_agent",
    "select_next",
)

_AGENT_SCOPE_TOKENS: tuple[str, ...] = (
    "chain",
    "agent",
    "session",
    "worker",
    "candidate",
    "selection",
)

_REPOSITORY_SAFETY_TOKENS: tuple[str, ...] = (
    "merge",
    "repair",
    "rebase",
    "conflict",
    "commit",
    "integration",
    "workspace",
    "diagnostic",
    "resolution",
    "landing",
    "pending",
    "repo",
    "worktree",
    "submodule",
    "push",
    "pull",
    "fetch",
    "clone",
)


def _expression_identifiers(node: ast.AST) -> list[str]:
    attributes = [child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)]
    names = [child.id for child in ast.walk(node) if isinstance(child, ast.Name)]
    return [*attributes, *names]


def _is_retry_name(name: str) -> bool:
    lowered = name.lower()
    return any(stem in lowered for stem in _RETRY_NAME_STEMS)


def _is_retry_cap_identifier(name: str) -> bool:
    lowered = name.lower()
    return _is_retry_name(name) and any(token in lowered for token in _RETRY_CAP_TOKENS)


def _is_retry_counter_identifier(name: str) -> bool:
    return _is_retry_name(name) and not _is_retry_cap_identifier(name)


def _is_numeric_literal(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    )


def _split_retry_counter_and_cap(compare: ast.Compare) -> tuple[list[str] | None, list[str] | None]:
    counter: list[str] | None = None
    cap: list[str] | None = None
    for operand in [compare.left, *compare.comparators]:
        identifiers = _expression_identifiers(operand)
        if counter is None and any(_is_retry_counter_identifier(name) for name in identifiers):
            counter = identifiers
            continue
        if cap is None and (
            any(_is_retry_cap_identifier(name) for name in identifiers)
            or _is_numeric_literal(operand)
        ):
            cap = identifiers
    return counter, cap


def _is_ledger_owned_comparison(compare: ast.Compare) -> bool:
    return any(_is_ledger_receiver(operand) for operand in [compare.left, *compare.comparators])


def _has_repository_safety_token(identifiers: list[str]) -> bool:
    return any(token in name.lower() for name in identifiers for token in _REPOSITORY_SAFETY_TOKENS)


def _names_signal_agent_fate(names: list[str]) -> bool:
    for name in names:
        lowered = name.lower()
        if any(token in lowered for token in _AGENT_FATE_ACTION_TOKENS):
            return True
        if _is_retry_name(name) and any(token in lowered for token in ("agent", "chain", "same")):
            return True
    return False


def _is_agent_fate_action_call(call: ast.Call) -> bool:
    if isinstance(call.func, ast.Attribute):
        name = call.func.attr
        receiver = _expression_identifiers(call.func.value)
    elif isinstance(call.func, ast.Name):
        name = call.func.id
        receiver = []
    else:
        return False
    if _names_signal_agent_fate([name]):
        return True
    if not _is_retry_name(name):
        return False
    return any(
        token in candidate.lower() for candidate in receiver for token in _AGENT_SCOPE_TOKENS
    )


def _is_agent_fate_action_assignment(node: ast.Assign | ast.AnnAssign | ast.AugAssign) -> bool:
    targets: list[ast.expr] = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Attribute):
            names.append(target.attr)
    return _names_signal_agent_fate(names)


def _branch_performs_agent_fate_action(node: ast.If | ast.While | ast.IfExp) -> bool:
    branches: list[ast.AST] = []
    if isinstance(node, ast.IfExp):
        branches = [node.body, node.orelse]
    elif isinstance(node, ast.If):
        branches = [*node.body, *node.orelse]
    else:
        branches = [*node.body]
    for branch in branches:
        for child in ast.walk(branch):
            if isinstance(child, ast.Call) and _is_agent_fate_action_call(child):
                return True
            if isinstance(child, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and (
                _is_agent_fate_action_assignment(child)
            ):
                return True
    return False


def _check_production_retry_cap_rule(
    node: ast.If | ast.While | ast.IfExp, rel_path: str
) -> list[SingleOwnerViolation]:
    for compare in [child for child in ast.walk(node.test) if isinstance(child, ast.Compare)]:
        if _is_ledger_owned_comparison(compare):
            continue
        counter, cap = _split_retry_counter_and_cap(compare)
        if counter is None or cap is None:
            continue
        if _has_repository_safety_token([*counter, *cap]):
            continue
        if not _branch_performs_agent_fate_action(node):
            continue
        return [
            SingleOwnerViolation(
                path=rel_path,
                line=compare.lineno,
                rule="production_local_retry_cap_decision",
                detail=(
                    f"Comparison '{ast.unparse(compare)}' decides an agent's retry or "
                    "advance fate with a local retry cap outside AgentFailureLedger; "
                    "the ledger is the single owner of agent failure decisions."
                ),
            )
        ]
    return []
