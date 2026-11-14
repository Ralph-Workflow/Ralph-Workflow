from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class SingleOwnerViolation:
    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.detail}"


TEMPORARY_PRODUCTION_ALLOWLIST: frozenset[str] = frozenset()

PROGRESS_PATH_ALLOWLIST: dict[tuple[str, str], str] = {
    (
        "ralph/pipeline/reducer.py",
        "_reduce_phase_failure",
    ): "Phase-level failure routing preserves workspace progress and delegates the outcome to RecoveryController.handle.",
}

TEMPORARY_TEST_ALLOWLIST: frozenset[str] = frozenset()

TEMPORARY_TEST_COUNTER_READS: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "tests/integration/test_pipeline_commit_review_grading_e2e.py",
            "consecutive_unproductive",
        ),
        ("tests/integration/test_pipeline_commit_review_grading_e2e.py", "total_unproductive"),
        ("tests/recovery/test_dead_agent_acceptance.py", "consecutive_unproductive"),
        ("tests/recovery/test_dead_agent_acceptance.py", "total_unproductive"),
        ("tests/recovery/test_dead_agent_incidents.py", "consecutive_unproductive"),
        ("tests/recovery/test_dead_agent_incidents.py", "total_unproductive"),
        ("tests/recovery/test_engine_origin_ledger_fairness.py", "consecutive_unproductive"),
        ("tests/recovery/test_engine_origin_ledger_fairness.py", "total_unproductive"),
    }
)

TEMPORARY_TRANSITIONAL_RULES: dict[str, frozenset[str]] = {
    "ralph/pipeline/conflict_resolution/session.py": frozenset(
        {
            "dead_tool_surfaces",
        }
    ),
    "ralph/pipeline/conflict_resolution/chain_candidates.py": frozenset(
        {
            "dead_tool_surfaces",
        }
    ),
}


def is_transitional_allowed(rel_path: str, name: str) -> bool:
    allowed = TEMPORARY_TRANSITIONAL_RULES.get(rel_path)
    return bool(allowed and name in allowed)


def is_test_counter_read_allowed(rel_path: str, counter: str) -> bool:
    return (rel_path, counter) in TEMPORARY_TEST_COUNTER_READS


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


def _is_forbidden_target_name(name: str) -> bool:
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
        if (
            target_name is not None
            and rel_path.startswith("ralph/phases/")
            and _is_phase_failure_state_name(target_name)
        ):
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=node.lineno,
                    rule="production_phase_failure_state",
                    detail=(
                        f"Phase-owned state '{target_name}' duplicates cooldown, skip-list, "
                        "or backoff state owned by RecoveryController."
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


def _check_production_call(
    node: ast.Call,
    rel_path: str,
    enclosing_function: str | None = None,
) -> list[SingleOwnerViolation]:
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
    if _call_name(node) == "ClassifiedFailure":
        keyword_values = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
        counts = keyword_values.get("counts_against_budget", _positional_arg(node, 4))
        unavailable = keyword_values.get("is_unavailable", _positional_arg(node, 9))
        if (
            _is_false_constant(counts)
            and (unavailable is None or _is_false_constant(unavailable))
            and not _is_progress_path_allowed(rel_path, enclosing_function)
        ):
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=node.lineno,
                    rule="production_unjustified_non_penalising_failure",
                    detail=(
                        "ClassifiedFailure disables both budget charge and unavailability "
                        "outside an explicitly justified progress-made path."
                    ),
                )
            )
    return violations


_AGENT_INVOCATION_CALLS: frozenset[str] = frozenset(
    {
        "execute_agent_effect",
        "invoke_agent",
        "invoke_resolution_agent",
        "run_agent_attempt",
    }
)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_false_constant(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _positional_arg(node: ast.Call, index: int) -> ast.AST | None:
    return node.args[index] if len(node.args) > index else None


def _is_progress_path_allowed(rel_path: str, function_name: str | None) -> bool:
    return function_name is not None and (rel_path, function_name) in PROGRESS_PATH_ALLOWLIST


def _is_phase_failure_state_name(name: str) -> bool:
    lowered = name.lower()
    return any(
        marker in lowered
        for marker in ("cooldown", "backoff", "skip_agents", "skipped_agents", "agent_skip")
    )


def _check_agent_invocation_failure_routing(
    node: ast.Try, rel_path: str
) -> list[SingleOwnerViolation]:
    invocation_calls = [
        child
        for statement in node.body
        for child in ast.walk(statement)
        if isinstance(child, ast.Call) and _call_name(child) in _AGENT_INVOCATION_CALLS
    ]
    if not invocation_calls:
        return []
    bypassed_lines: set[int] = set()
    for handler in node.handlers:
        if any(isinstance(child, ast.Raise) for child in ast.walk(handler)):
            continue
        calls = [child for child in ast.walk(handler) if isinstance(child, ast.Call)]
        classifier_lines = [
            call.lineno
            for call in calls
            if (name := _call_name(call)) is not None and name.startswith("classify_")
        ]
        handle_lines = [call.lineno for call in calls if _call_name(call) == "handle"]
        routes_to_owner = any(
            classifier_line < handle_line
            for classifier_line in classifier_lines
            for handle_line in handle_lines
        )
        if routes_to_owner:
            continue
        bypassed_lines.update(
            invocation.lineno
            for invocation in invocation_calls
            if not _invocation_routes_through_owner(invocation)
        )
    return [
        SingleOwnerViolation(
            path=rel_path,
            line=invocation.lineno,
            rule="production_agent_failure_owner_bypass",
            detail=(
                f"Agent invocation '{_call_name(invocation)}' is caught without "
                "routing the failure through classify_* followed by handle."
            ),
        )
        for invocation in invocation_calls
        if invocation.lineno in bypassed_lines
    ]


def _invocation_routes_through_owner(node: ast.Call) -> bool:
    return _call_name(node) == "run_agent_attempt" and any(
        keyword.arg == "controller" for keyword in node.keywords
    )


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


_CLASSIFIER_ENTRY_FUNCTIONS: dict[str, int] = {
    "classify_failed_resolution_attempt": 2,
    "should_reset_tool_registry": 0,
    "bench_resolver": 2,
}

_FAILURE_TEXT_FIELDS: frozenset[str] = frozenset(
    {
        "last_attempt_failure",
        "last_attempt_evidence",
    }
)

_KNOWN_RALPH_AUTHORED_CONSTANTS: frozenset[str] = frozenset(
    {
        "ATTEMPT_FAILED_EVIDENCE",
    }
)


def _classifier_entry_name(
    call: ast.Call, classifier_instances: set[str] | frozenset[str] = frozenset()
) -> str | None:
    func = call.func
    if isinstance(func, ast.Name) and func.id in _CLASSIFIER_ENTRY_FUNCTIONS:
        return func.id
    if isinstance(func, ast.Attribute):
        if func.attr in _CLASSIFIER_ENTRY_FUNCTIONS:
            return func.attr
        if func.attr == "classify":
            inner = func.value
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "FailureClassifier"
            ):
                return "FailureClassifier.classify"
            if isinstance(inner, ast.Name) and inner.id in classifier_instances:
                return "FailureClassifier.classify"
            if isinstance(inner, ast.Attribute) and inner.attr in classifier_instances:
                return "FailureClassifier.classify"
    return None


def _collect_classifier_instance_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        if not any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "FailureClassifier"
            for child in ast.walk(node.value)
        ):
            continue
        targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def _classifier_failure_arg(call: ast.Call, entry: str) -> ast.AST | None:
    index = _CLASSIFIER_ENTRY_FUNCTIONS.get(entry, 0)
    if len(call.args) > index:
        return call.args[index]
    for keyword in call.keywords:
        if keyword.arg in ("raw_failure", "failure", "exc"):
            return keyword.value
    return None


def _expr_is_ralph_authored_prose(node: ast.AST, module_constants: set[str]) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Name):
        return node.id in module_constants or node.id in _KNOWN_RALPH_AUTHORED_CONSTANTS
    return False


def _expr_contains_ralph_authored_prose(node: ast.AST, module_constants: set[str]) -> bool:
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return any(
            _expr_contains_ralph_authored_prose(value, module_constants) for value in node.values
        )
    return _expr_is_ralph_authored_prose(node, module_constants)


def _expr_reads_failure_field(node: ast.AST, tainted_fields: dict[str, int]) -> str | None:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in tainted_fields:
            return child.id
        if isinstance(child, ast.Attribute) and child.attr in tainted_fields:
            return child.attr
    return None


def _collect_module_string_constants(tree: ast.Module) -> set[str]:
    constants: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    constants.add(target.id)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            constants.add(node.target.id)
    return constants


def _assignment_target_field(target: ast.AST) -> str | None:
    if isinstance(target, ast.Name) and target.id in _FAILURE_TEXT_FIELDS:
        return target.id
    if isinstance(target, ast.Attribute) and target.attr in _FAILURE_TEXT_FIELDS:
        return target.attr
    return None


def _collect_tainted_failure_fields(tree: ast.Module, module_constants: set[str]) -> dict[str, int]:
    tainted_fields: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if value is None:
            continue
        targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            field = _assignment_target_field(target)
            if (
                field is not None
                and _expr_contains_ralph_authored_prose(value, module_constants)
                and field not in tainted_fields
            ):
                tainted_fields[field] = node.lineno
    return tainted_fields


def _collect_local_provenance(
    tree: ast.Module, module_constants: set[str]
) -> tuple[dict[str, int], set[str]]:
    tainted: dict[str, int] = {}
    prose_returners: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is None or not _expr_contains_ralph_authored_prose(value, module_constants):
                continue
            targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                name: str | None = None
                if isinstance(target, ast.Name):
                    name = target.id
                elif isinstance(target, ast.Attribute):
                    name = target.attr
                if name is not None and name not in tainted:
                    tainted[name] = node.lineno
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(
                isinstance(child, ast.Return)
                and child.value is not None
                and _expr_contains_ralph_authored_prose(child.value, module_constants)
                for child in ast.walk(node)
            ):
                prose_returners.add(node.name)
    return tainted, prose_returners


def _expr_reads_tainted_name(node: ast.AST, tainted_names: dict[str, int]) -> str | None:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in tainted_names:
            return child.id
        if isinstance(child, ast.Attribute) and child.attr in tainted_names:
            return child.attr
    return None


def _expr_calls_prose_returner(node: ast.AST, prose_returners: set[str]) -> str | None:
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id in prose_returners
        ):
            return child.func.id
    return None


def _ralph_authored_classifier_call_violation(
    node: ast.Call,
    entry: str,
    arg: ast.AST,
    direct_prose: bool,
    tainted_field: str | None,
    tainted_name: str | None,
    prose_returner: str | None,
    rel_path: str,
) -> SingleOwnerViolation:
    if direct_prose:
        detail = (
            f"Classifier entry '{entry}' receives Ralph-authored failure prose "
            f"('{ast.unparse(arg)}') instead of RAW external runtime text; "
            "authored prose classifies as AMBIGUOUS and poisons failure-ledger "
            "decisions — hand the owner a typed ConflictAttemptOutcome instead."
        )
    elif tainted_field is not None:
        detail = (
            f"Classifier entry '{entry}' classifies '{tainted_field}', a session "
            "field assigned Ralph-authored failure prose in this module; "
            "the classifier must only see RAW external runtime text — "
            "hand the owner a typed ConflictAttemptOutcome instead."
        )
    elif tainted_name is not None:
        detail = (
            f"Classifier entry '{entry}' classifies '{tainted_name}', a local "
            "assigned Ralph-authored failure prose in this module; "
            "the classifier must only see RAW external runtime text — "
            "hand the owner a typed ConflictAttemptOutcome instead."
        )
    else:
        detail = (
            f"Classifier entry '{entry}' receives the result of '{prose_returner}', "
            "a local helper returning Ralph-authored failure prose; "
            "the classifier must only see RAW external runtime text — "
            "hand the owner a typed ConflictAttemptOutcome instead."
        )
    return SingleOwnerViolation(
        path=rel_path,
        line=node.lineno,
        rule="production_ralph_authored_classifier_input",
        detail=detail,
    )


def _check_ralph_authored_classifier_inputs(
    tree: ast.Module, rel_path: str
) -> list[SingleOwnerViolation]:
    module_constants = _collect_module_string_constants(tree)
    tainted_fields = _collect_tainted_failure_fields(tree, module_constants)
    classifier_instances = _collect_classifier_instance_names(tree)
    tainted_names, prose_returners = _collect_local_provenance(tree, module_constants)

    violations: list[SingleOwnerViolation] = [
        SingleOwnerViolation(
            path=rel_path,
            line=line,
            rule="production_ralph_authored_classifier_input",
            detail=(
                f"Assignment of Ralph-authored failure prose to '{field}' feeds the "
                "text classifier authored text instead of RAW external runtime text; "
                "the classifier must only see exception objects or external output."
            ),
        )
        for field, line in tainted_fields.items()
    ]

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        entry = _classifier_entry_name(node, classifier_instances)
        if entry is None:
            continue
        arg = _classifier_failure_arg(node, entry)
        if arg is None:
            continue
        direct_prose = _expr_contains_ralph_authored_prose(arg, module_constants)
        tainted_field = _expr_reads_failure_field(arg, tainted_fields)
        tainted_name = (
            None if tainted_field is not None else _expr_reads_tainted_name(arg, tainted_names)
        )
        prose_returner = _expr_calls_prose_returner(arg, prose_returners)
        if (
            not direct_prose
            and tainted_field is None
            and tainted_name is None
            and (prose_returner is None)
        ):
            continue
        violations.append(
            _ralph_authored_classifier_call_violation(
                node,
                entry,
                arg,
                direct_prose,
                tainted_field,
                tainted_name,
                prose_returner,
                rel_path,
            )
        )
    return violations


def _is_ledger_receiver(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        lower = node.id.lower()
        return "ledger" in lower or lower == "agentfailureledger"
    if isinstance(node, ast.Attribute):
        lower = node.attr.lower()
        return "ledger" in lower
    return False
