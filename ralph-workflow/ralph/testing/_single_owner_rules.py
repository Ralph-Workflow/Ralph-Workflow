"""Single-owner rule engine for the agent failure ledger audit.

This private module holds the single-owner policy data (the forbidden
tracker names, the temporary allowlists) and the production rule
implementations that decide whether a module under ``ralph/recovery/``,
``ralph/pipeline/``, or ``ralph/agents/invoke/`` breaks the single-owner
invariant: exactly one decision owner
(:class:`ralph.recovery.agent_failure_ledger.AgentFailureLedger`) owns the
state and the decision logic for whether an agent is failing, unproductive,
or broken, and whether to retry it or stop using it.

The rules implemented here are:

1. No module declares streak/counter state or logic used for agent
   stop/retry decisions (duplicate retry trackers, retry progress guards,
   in-session retry counters, competing health counters).
2. No module decides an agent's fate with a LOCAL retry cap: a retry counter
   compared against a locally declared retry cap that then retries or advances
   an agent is a second decision owner. Genuine repository-safety bounds (a
   merge-repair attempt cap, for example) bound repository state rather than
   agent health and stay accepted.
3. No module imports a deleted tracker module or symbol.

The audit entry point (:mod:`ralph.testing.audit_agent_failure_single_owner`)
scans the tree, applies these rules, and adds the test-side rules that keep
tests on the owner's public seam.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class SingleOwnerViolation:
    """A violation of the single-owner failure ledger policy."""

    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.detail}"


# Temporary production allowlist during single-owner consolidation (U4 removed the deleted trackers, controller, options and run_loop; the remaining error modules and __init__ re-export stay until U5 removes the tests).
TEMPORARY_PRODUCTION_ALLOWLIST: frozenset[str] = frozenset()

TEMPORARY_TEST_ALLOWLIST: frozenset[str] = frozenset()

# Transitional allowlist for surviving competing shapes in conflict resolution until future
# work refactors them into AgentFailureLedger; only surviving production references remain.
TEMPORARY_TRANSITIONAL_RULES: dict[str, frozenset[str]] = {
    # session.py: dead_tool_surfaces tracks candidate tool-surface fault state across
    # rebase stops; RALPH_FAULT_ESCALATION_HITS is a diagnostic constant.
    "ralph/pipeline/conflict_resolution/session.py": frozenset(
        {
            "dead_tool_surfaces",
            "RALPH_FAULT_ESCALATION_HITS",
        }
    ),
    # chain_candidates.py: dead_tool_surfaces filters candidate chains for tool-faulted agents.
    "ralph/pipeline/conflict_resolution/chain_candidates.py": frozenset(
        {
            "dead_tool_surfaces",
        }
    ),
}


def is_transitional_allowed(rel_path: str, name: str) -> bool:
    """Return True if the symbol is temporarily allowed in rel_path."""
    allowed = TEMPORARY_TRANSITIONAL_RULES.get(rel_path)
    return bool(allowed and name in allowed)


_FORBIDDEN_TRACKER_MODULES: frozenset[str] = frozenset(
    {
        "_same_shape_retry_tracker",
        "_broken_agent_same_shape_tracker",
        "_same_shape_retry_error",
        "_broken_agent_same_shape_error",
        "_retry_progress_guard",
    }
)

_FORBIDDEN_TRACKER_SYMBOLS: frozenset[str] = frozenset(
    {
        "SameShapeRetryTracker",
        "BrokenAgentSameShapeTracker",
        "SameShapeRetryLoopError",
        "BrokenAgentSameShapeLimitError",
        "SameShapeRetryTrackerOptions",
        # Permanent bans: Rival retry progress guard and signature tracking (eliminated across U3c/U4b)
        "RetryProgressGuard",
        "progress_guard",
        "retry_failure_signature",
        "MAX_IDENTICAL_RETRY_ATTEMPTS",
    }
)

_FORBIDDEN_TRACKER_NAMES: frozenset[str] = frozenset(
    {
        "same_shape_retry_tracker",
        "broken_agent_same_shape_tracker",
        "same_shape_tracker",
        "broken_agent_tracker",
        "same_shape_state",
        "broken_agent_same_shape_state",
        "in_session_retry_counts",
        "in_session_retry_escalation_limit",
        "same_shape_retry_limit",
        "broken_agent_same_shape_limit",
        "_same_shape_retry_tracker",
        "_broken_agent_same_shape_tracker",
        "_same_shape_tracker",
        "_broken_agent_tracker",
        "_same_shape_state",
        "_broken_agent_same_shape_state",
        "_in_session_retry_counts",
        "_in_session_retry_escalation_limit",
        "_check_same_shape_bound",
        "_check_broken_agent_same_shape_bound",
        # D-7: Competing retry caps and resolver counters
        "technical_retry_cap",
        "_technical_retry_cap",
        "resolver_retries",
        "resolver_retry_count",
        "resolver_retry_cap",
        "resolver_health_counter",
        "resolver_health_state",
        "_resolver_retries",
        "_resolver_retry_count",
        "_resolver_retry_cap",
        "_resolver_health_counter",
        # RC-1: Competing fault-hit counters and ad-hoc agent exclusion lists
        "ralph_fault_hits",
        "_ralph_fault_hits",
        "fault_hits",
        "_fault_hits",
        "fault_hit_count",
        "_fault_hit_count",
        "ralph_fault_escalation_hits",
        "_ralph_fault_escalation_hits",
        "RALPH_FAULT_ESCALATION_HITS",
        "fault_escalation_hits",
        "stop_dead_surfaces",
        "_stop_dead_surfaces",
        "dead_tool_surfaces",
        "_dead_tool_surfaces",
        "dead_surfaces",
        "_dead_surfaces",
        "barred_agents",
        "_barred_agents",
        "excluded_agents",
        "_excluded_agents",
        # Permanent bans: Rival retry progress guard and signature tracking (eliminated across U3c/U4b)
        "RetryProgressGuard",
        "_RetryProgressGuard",
        "progress_guard",
        "_progress_guard",
        "retry_failure_signature",
        "_retry_failure_signature",
        "MAX_IDENTICAL_RETRY_ATTEMPTS",
        "_MAX_IDENTICAL_RETRY_ATTEMPTS",
    }
)

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

#: Stems that mark a name as a retry counter or a retry cap.
_RETRY_NAME_STEMS: tuple[str, ...] = ("retry", "retries", "retrying", "retried")

#: Tokens that mark a name as a retry cap or retry budget rather than a retry counter.
_RETRY_CAP_TOKENS: tuple[str, ...] = (
    "max",
    "cap",
    "limit",
    "budget",
    "allowance",
    "threshold",
    "ceiling",
)

#: Tokens that mark a call or assignment as an agent retry/advance action.
_AGENT_FATE_ACTION_TOKENS: tuple[str, ...] = (
    "advance",
    "fall_over",
    "fallback",
    "next_agent",
    "select_agent",
    "select_next",
)

#: Tokens that mark the receiver of an action as agent-scoped (chain, agent, session).
_AGENT_SCOPE_TOKENS: tuple[str, ...] = (
    "chain",
    "agent",
    "session",
    "worker",
    "candidate",
    "selection",
)

#: Tokens that mark a counter as a repository-safety bound rather than agent health.
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

_GETATTR_MIN_ARGS = 2


def _is_forbidden_target_name(name: str) -> bool:
    """Check if variable/attribute name matches forbidden duplicate tracker or competing counter patterns."""
    if name in _FORBIDDEN_TRACKER_NAMES:
        return True
    lower = name.lower()
    return (
        "same_shape" in lower
        or "broken_agent_same_shape" in lower
        or "in_session_retry" in lower
        or "technical_retry_cap" in lower
        or "resolver_retry" in lower
        or "resolver_health" in lower
        or "fault_hits" in lower
        or "fault_hit" in lower
        or "dead_surfaces" in lower
        or "dead_tool_surfaces" in lower
        or "stop_dead_surfaces" in lower
        or "barred_agents" in lower
        or "excluded_agents" in lower
        or "fault_escalation" in lower
        or "progress_guard" in lower
        or "retry_failure_signature" in lower
        or "max_identical_retry_attempts" in lower
    )


def _check_production_class(node: ast.ClassDef, rel_path: str) -> list[SingleOwnerViolation]:
    if not is_transitional_allowed(rel_path, node.name) and (
        node.name in _FORBIDDEN_TRACKER_SYMBOLS
        or "SameShape" in node.name
        or "BrokenAgentSameShape" in node.name
        or (node.name.endswith("RetryTracker") and node.name != "AgentFailureLedger")
    ):
        return [
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="production_streak_counter_state",
                detail=(
                    f"Class '{node.name}' declares a duplicate failure/retry tracker. "
                    "AgentFailureLedger is the single owner."
                ),
            )
        ]
    return []


def _check_production_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef, rel_path: str
) -> list[SingleOwnerViolation]:
    violations: list[SingleOwnerViolation] = []
    if _is_forbidden_target_name(node.name) and not is_transitional_allowed(rel_path, node.name):
        violations.append(
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="production_streak_counter_state",
                detail=(
                    f"Function/method '{node.name}' implements duplicate agent "
                    "retry/stop decision logic outside AgentFailureLedger."
                ),
            )
        )
    violations.extend(
        SingleOwnerViolation(
            path=rel_path,
            line=arg.lineno,
            rule="production_streak_counter_state",
            detail=(
                f"Parameter '{arg.arg}' in function '{node.name}' declares competing "
                "retry cap or health counter outside AgentFailureLedger."
            ),
        )
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        if _is_forbidden_target_name(arg.arg) and not is_transitional_allowed(rel_path, arg.arg)
    )
    return violations


def _check_production_assignment(
    node: ast.Assign | ast.AnnAssign | ast.AugAssign, rel_path: str
) -> list[SingleOwnerViolation]:
    targets: list[ast.expr] = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
    violations: list[SingleOwnerViolation] = []
    for target in targets:
        target_name: str | None = None
        if isinstance(target, ast.Name):
            target_name = target.id
        elif isinstance(target, ast.Attribute):
            target_name = target.attr
        if (
            target_name is not None
            and not is_transitional_allowed(rel_path, target_name)
            and (
                target_name
                in (
                    "consecutive_unproductive",
                    "total_unproductive",
                    "_consecutive_unproductive",
                    "_total_unproductive",
                )
                or _is_forbidden_target_name(target_name)
            )
        ):
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=node.lineno,
                    rule="production_streak_counter_state",
                    detail=(
                        f"Declaration/assignment to '{target_name}' manages duplicate "
                        "retry/streak counter state outside AgentFailureLedger."
                    ),
                )
            )
    return violations


def _check_production_attribute(node: ast.Attribute, rel_path: str) -> list[SingleOwnerViolation]:
    if not is_transitional_allowed(rel_path, node.attr):
        if node.attr in (
            "consecutive_unproductive",
            "total_unproductive",
            "_consecutive_unproductive",
            "_total_unproductive",
        ) and _is_ledger_receiver(node.value):
            return [
                SingleOwnerViolation(
                    path=rel_path,
                    line=node.lineno,
                    rule="production_streak_counter_state",
                    detail=(
                        f"Access to ledger counter attribute '{node.attr}' "
                        "outside AgentFailureLedger."
                    ),
                )
            ]
        if _is_forbidden_target_name(node.attr):
            return [
                SingleOwnerViolation(
                    path=rel_path,
                    line=node.lineno,
                    rule="production_streak_counter_state",
                    detail=(
                        f"Access to competing retry cap or health counter attribute '{node.attr}' "
                        "outside AgentFailureLedger."
                    ),
                )
            ]
    return []


def _check_production_call(node: ast.Call, rel_path: str) -> list[SingleOwnerViolation]:
    violations: list[SingleOwnerViolation] = [
        SingleOwnerViolation(
            path=rel_path,
            line=node.lineno,
            rule="production_streak_counter_state",
            detail=(
                f"Passing competing retry cap or health counter '{kw.arg}' "
                "outside AgentFailureLedger."
            ),
        )
        for kw in node.keywords
        if kw.arg
        and _is_forbidden_target_name(kw.arg)
        and not is_transitional_allowed(rel_path, kw.arg)
    ]
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and node.args[0].value in ("consecutive_unproductive", "total_unproductive")
    ):
        violations.append(
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="production_streak_counter_state",
                detail=(
                    f"Production code accesses ledger counter '{node.args[0].value}' via .get(); "
                    "all failure and retry decisions must use AgentFailureLedger public status."
                ),
            )
        )
    if isinstance(node.func, ast.Attribute) and node.func.attr in ("append", "add", "extend"):
        recv = node.func.value
        recv_name: str | None = None
        if isinstance(recv, ast.Name):
            recv_name = recv.id
        elif isinstance(recv, ast.Attribute):
            recv_name = recv.attr
        if (
            recv_name is not None
            and _is_forbidden_target_name(recv_name)
            and not is_transitional_allowed(rel_path, recv_name)
        ):
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=node.lineno,
                    rule="production_streak_counter_state",
                    detail=(
                        f"Mutation '{ast.unparse(node)}' modifies competing agent "
                        "exclusion collection outside AgentFailureLedger."
                    ),
                )
            )
    return violations


def _check_production_import(
    node: ast.Import | ast.ImportFrom, rel_path: str
) -> list[SingleOwnerViolation]:
    violations: list[SingleOwnerViolation] = []
    if isinstance(node, ast.Import):
        violations.extend(
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="production_legacy_tracker_import",
                detail=f"Import of forbidden tracker '{alias.name}'.",
            )
            for alias in node.names
            if not is_transitional_allowed(rel_path, alias.name)
            and (
                any(m in alias.name for m in _FORBIDDEN_TRACKER_MODULES)
                or alias.name in _FORBIDDEN_TRACKER_SYMBOLS
            )
        )
    elif isinstance(node, ast.ImportFrom):
        if (
            node.module
            and any(m in node.module for m in _FORBIDDEN_TRACKER_MODULES)
            and not is_transitional_allowed(rel_path, node.module)
        ):
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=node.lineno,
                    rule="production_legacy_tracker_import",
                    detail=f"Import from forbidden tracker module '{node.module}'.",
                )
            )
        violations.extend(
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="production_legacy_tracker_import",
                detail=f"Import of forbidden tracker symbol '{alias.name}'.",
            )
            for alias in node.names
            if not is_transitional_allowed(rel_path, alias.name)
            and (
                alias.name in _FORBIDDEN_TRACKER_SYMBOLS
                or any(m in alias.name for m in _FORBIDDEN_TRACKER_MODULES)
            )
        )
    return violations


def _check_production_compare(node: ast.Compare, rel_path: str) -> list[SingleOwnerViolation]:
    """Flag comparisons of retry counters against caps/limits outside AgentFailureLedger."""
    for operand in [node.left, *node.comparators]:
        attrs = [n.attr for n in ast.walk(operand) if isinstance(n, ast.Attribute)]
        names = [n.id for n in ast.walk(operand) if isinstance(n, ast.Name)]
        all_idents = attrs + names
        is_counter = any(
            ident in ("retries", "_retries") or _is_forbidden_target_name(ident)
            for ident in all_idents
        )
        if not is_counter:
            continue
        if any(is_transitional_allowed(rel_path, ident) for ident in all_idents):
            continue
        has_forbidden = any(_is_forbidden_target_name(ident) for ident in all_idents)
        if not has_forbidden and not any(isinstance(op, (ast.GtE, ast.Gt)) for op in node.ops):
            continue
        for other in [node.left, *node.comparators]:
            if other is operand:
                continue
            other_attrs = [n.attr for n in ast.walk(other) if isinstance(n, ast.Attribute)]
            other_names = [n.id for n in ast.walk(other) if isinstance(n, ast.Name)]
            other_idents = [k.lower() for k in (other_attrs + other_names)]
            is_cap = (
                any(k in other_idents for k in ("max_retries", "cap", "limit", "retries", "hits"))
                or any(
                    "escalation" in k or "cap" in k or "limit" in k or "hits" in k
                    for k in other_idents
                )
                or any(_is_forbidden_target_name(k) for k in (other_attrs + other_names))
                or (isinstance(other, ast.Constant) and isinstance(other.value, (int, float)))
            )
            if is_cap:
                return [
                    SingleOwnerViolation(
                        path=rel_path,
                        line=node.lineno,
                        rule="production_streak_counter_state",
                        detail=(
                            f"Comparison '{ast.unparse(node)}' implements an independent "
                            "retry cap or health cutoff outside AgentFailureLedger."
                        ),
                    )
                ]
    return []


def _expression_identifiers(node: ast.AST) -> list[str]:
    """Return the attribute and name identifiers used inside an expression."""
    attributes = [child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)]
    names = [child.id for child in ast.walk(node) if isinstance(child, ast.Name)]
    return [*attributes, *names]


def _is_retry_name(name: str) -> bool:
    """Return True when a name reads like a retry counter or a retry cap."""
    lowered = name.lower()
    return any(stem in lowered for stem in _RETRY_NAME_STEMS)


def _is_retry_cap_identifier(name: str) -> bool:
    """Return True when a name reads like a retry cap or a retry budget."""
    lowered = name.lower()
    return _is_retry_name(name) and any(token in lowered for token in _RETRY_CAP_TOKENS)


def _is_retry_counter_identifier(name: str) -> bool:
    """Return True when a name reads like a retry counter."""
    return _is_retry_name(name) and not _is_retry_cap_identifier(name)


def _is_numeric_literal(node: ast.AST) -> bool:
    """Return True when an expression is a plain numeric literal."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    )


def _split_retry_counter_and_cap(compare: ast.Compare) -> tuple[list[str] | None, list[str] | None]:
    """Return the (counter, cap) identifier groups of a retry-cap comparison."""
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
    """Return True when a comparison reads the ledger's own counters."""
    return any(_is_ledger_receiver(operand) for operand in [compare.left, *compare.comparators])


def _has_repository_safety_token(identifiers: list[str]) -> bool:
    """Return True when a name marks a repository-safety bound rather than agent health."""
    return any(token in name.lower() for name in identifiers for token in _REPOSITORY_SAFETY_TOKENS)


def _names_signal_agent_fate(names: list[str]) -> bool:
    """Return True when a name reads like an agent retry or advance action."""
    for name in names:
        lowered = name.lower()
        if any(token in lowered for token in _AGENT_FATE_ACTION_TOKENS):
            return True
        if _is_retry_name(name) and any(token in lowered for token in ("agent", "chain", "same")):
            return True
    return False


def _is_agent_fate_action_call(call: ast.Call) -> bool:
    """Return True when a call acts on an agent's retry or advance fate."""
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
    """Return True when an assignment names an agent retry or advance action."""
    targets: list[ast.expr] = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Attribute):
            names.append(target.attr)
    return _names_signal_agent_fate(names)


def _branch_performs_agent_fate_action(node: ast.If | ast.While | ast.IfExp) -> bool:
    """Return True when a branch acts on an agent's retry or advance fate."""
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


def _check_production_retry_cap_decision(
    node: ast.If | ast.While | ast.IfExp, rel_path: str
) -> list[SingleOwnerViolation]:
    """Flag a local retry cap that decides an agent's retry or advance fate.

    The single owner (:class:`ralph.recovery.agent_failure_ledger.AgentFailureLedger`)
    decides whether an agent may try again. Production code that declares its own
    retry cap, compares a retry counter against that cap, and acts on the
    comparison by retrying or advancing an agent is a second decision owner and is
    rejected.

    A repository-safety bound (a merge-repair attempt cap, for example) bounds
    repository state rather than agent health, so it stays accepted.
    """
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


def _check_production_subscript(node: ast.Subscript, rel_path: str) -> list[SingleOwnerViolation]:
    if (
        isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
        and node.slice.value in ("consecutive_unproductive", "total_unproductive")
    ):
        return [
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="production_streak_counter_state",
                detail=(
                    f"Production code accesses ledger counter '{node.slice.value}' via subscript; "
                    "all failure and retry decisions must use AgentFailureLedger public status."
                ),
            )
        ]
    return []


def _is_ledger_receiver(node: ast.AST) -> bool:
    """Check if AST expression represents an AgentFailureLedger receiver."""
    if isinstance(node, ast.Name):
        lower = node.id.lower()
        return "ledger" in lower or lower == "agentfailureledger"
    if isinstance(node, ast.Attribute):
        lower = node.attr.lower()
        return "ledger" in lower
    return False


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
