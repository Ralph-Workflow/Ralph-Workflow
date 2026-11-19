from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TypeIs


def _is_assign_or_ann(node: ast.AST) -> TypeIs[ast.Assign | ast.AnnAssign]:
    return isinstance(node, ast.Assign | ast.AnnAssign)


def _is_assignment(node: ast.AST) -> TypeIs[ast.Assign | ast.AnnAssign | ast.AugAssign]:
    match node:
        case ast.Assign() | ast.AnnAssign() | ast.AugAssign():
            return True
        case _:
            return False


def _is_function_def(node: ast.AST) -> TypeIs[ast.FunctionDef | ast.AsyncFunctionDef]:
    return isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)


def _is_loop(node: ast.AST) -> TypeIs[ast.For | ast.AsyncFor | ast.While]:
    match node:
        case ast.For() | ast.AsyncFor() | ast.While():
            return True
        case _:
            return False


def _is_for_loop(node: ast.AST) -> TypeIs[ast.For | ast.AsyncFor]:
    return isinstance(node, ast.For | ast.AsyncFor)


def _is_with_block(node: ast.AST) -> TypeIs[ast.With | ast.AsyncWith]:
    return isinstance(node, ast.With | ast.AsyncWith)


def _is_sequence_display(node: ast.AST) -> TypeIs[ast.Tuple | ast.List | ast.Set]:
    match node:
        case ast.Tuple() | ast.List() | ast.Set():
            return True
        case _:
            return False


def _is_flow_exit(node: ast.AST) -> TypeIs[ast.Return | ast.Break | ast.Continue]:
    match node:
        case ast.Return() | ast.Break() | ast.Continue():
            return True
        case _:
            return False


def _is_name_or_attr(node: ast.AST) -> TypeIs[ast.Name | ast.Attribute]:
    return isinstance(node, ast.Name | ast.Attribute)


def _is_identity_cmp(node: ast.AST) -> TypeIs[ast.Is | ast.IsNot]:
    return isinstance(node, ast.Is | ast.IsNot)


def _is_greater_cmp(node: ast.AST) -> TypeIs[ast.GtE | ast.Gt]:
    return isinstance(node, ast.GtE | ast.Gt)


def _is_number(value: object) -> TypeIs[int | float]:
    return isinstance(value, int | float)


def _is_dict_or_call(node: ast.AST) -> TypeIs[ast.Dict | ast.Call]:
    return isinstance(node, ast.Dict | ast.Call)


def _assignment_targets(node: ast.Assign | ast.AnnAssign | ast.AugAssign) -> list[ast.expr]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    return [node.target]


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
    (
        "ralph/recovery/failure_classifier.py",
        "UNRESOLVED_REMAINING",
    ): (
        "Conflict attempt that changed the worktree or reduced the unresolved count; "
        "progress stays non-penalising."
    ),
}

ROUND_CAP_LITERAL_ALLOWLIST: dict[tuple[str, str], str] = {
    (
        "ralph/config/conflict_resolution_config.py",
        "max_rounds_per_stop",
    ): "Canonical validated configuration default, not a phase-local fallback.",
    (
        "ralph/pipeline/_stop_resolution_entry.py",
        "_PRESENTED_ROUNDS_CAP",
    ): "Bound on retained presentation history, not an agent invocation round limit.",
    (
        "ralph/project_policy/cli_integration.py",
        "_MAX_PROMPT_ROUNDS",
    ): "Bound on interactive policy prompts, not an agent invocation round limit.",
}

PHASE_FAILURE_STATE_ALLOWLIST: dict[tuple[str, str], str] = {}

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

TEMPORARY_TRANSITIONAL_RULES: dict[str, frozenset[str]] = {}

_TYPED_OUTCOME_CLASSIFICATION_ARITY = 3
_CONFLICT_RESOLUTION_PATH_PREFIX = "ralph/pipeline/conflict_resolution/"

_CONFLICT_RESOLUTION_CLASSIFICATION_RULE = (
    "production_conflict_resolution_classification_construction"
)

_CONFLICT_RESOLUTION_CLASSIFICATION_MESSAGE = (
    "Conflict-resolution code constructs ClassifiedFailure; typed attempt facts must be "
    "classified by the shared FailureClassifier recovery path."
)

_CONFLICT_RESOLUTION_BUDGET_BINDING_RULE = "production_conflict_resolution_budget_binding"

_CONFLICT_RESOLUTION_BUDGET_BINDING_MESSAGE = (
    "Conflict-resolution code binds counts_against_budget to a statically falsy value; "
    "budget classification belongs to the shared FailureClassifier recovery path."
)

_AUTO_INTEGRATE_PATH_PREFIX = "ralph/pipeline/"

_COOLDOWN_STATE_CONSTRUCTION_SYMBOLS: frozenset[str] = frozenset(
    {
        "RecoveryController",
        "AgentFailureLedger",
        "AgentUnavailabilityTracker",
    }
)

_COOLDOWN_STATE_CONSTRUCTION_RULE = "production_cooldown_state_construction_outside_owner"

_COOLDOWN_STATE_CONSTRUCTION_MESSAGE = (
    "Conflict-resolution and auto-integrate modules must not construct RecoveryController, "
    "AgentFailureLedger, or AgentUnavailabilityTracker; adopt the run's shared controller "
    "through the shared accessor instead."
)

_AGENT_FAILURE_CLASSIFY_SYMBOLS: frozenset[str] = frozenset(
    {
        "FailureClassifier",
        "classify_failure",
        "ClassifiedFailure",
    }
)

_AGENT_FAILURE_CLASSIFY_RULE = "production_agent_failure_classify_outside_owner"

_AGENT_FAILURE_CLASSIFY_MESSAGE = (
    "Conflict-resolution and auto-integrate modules must not classify agent failures; "
    "hand typed AttemptFacts to RecoveryController.finalize_attempt."
)

_AGENT_RETRY_DELAY_SYMBOLS: frozenset[str] = frozenset(
    {
        "compute_retry_delay",
        "wait_before_retry",
    }
)

_AGENT_RETRY_DELAY_RULE = "production_agent_retry_delay_outside_owner"

_AGENT_RETRY_DELAY_MESSAGE = (
    "Conflict-resolution and auto-integrate modules must not compute agent retry delays; "
    "use RecoveryController preferred_agent_index / earliest_available_wait_ms."
)


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
    targets: list[ast.expr] = _assignment_targets(node)
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
            and _is_phase_implementation_path(rel_path)
            and _is_phase_failure_state_name(target_name)
            and not _is_phase_failure_state_allowed(rel_path, target_name)
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
        if target_name is not None and _assignment_has_hardcoded_round_cap(
            target_name, node, rel_path
        ):
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=node.lineno,
                    rule="production_hardcoded_round_cap",
                    detail=(
                        f"Round-cap assignment '{ast.unparse(node)}' contains a hardcoded "
                        "numeric fallback; round limits must come from the configured policy."
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
        if _is_false_constant(counts) and not _is_progress_path_allowed(
            rel_path, enclosing_function
        ):
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=node.lineno,
                    rule="production_unjustified_non_penalising_failure",
                    detail=(
                        "ClassifiedFailure disables the budget charge outside an explicitly "
                        "justified progress-made path."
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


def _is_conflict_resolution_path(rel_path: str) -> bool:
    return rel_path.startswith(_CONFLICT_RESOLUTION_PATH_PREFIX)


def _is_auto_integrate_module_path(rel_path: str) -> bool:
    if not rel_path.startswith(_AUTO_INTEGRATE_PATH_PREFIX):
        return False
    name = rel_path.rsplit("/", 1)[-1]
    return name.startswith("auto_integrate") and name.endswith(".py")


def _is_cooldown_state_construction_path(rel_path: str) -> bool:
    return _is_conflict_resolution_path(rel_path) or _is_auto_integrate_module_path(rel_path)


def _check_cooldown_state_single_owner(
    tree: ast.Module, rel_path: str
) -> list[SingleOwnerViolation]:
    if not _is_cooldown_state_construction_path(rel_path):
        return []
    violations: list[SingleOwnerViolation] = []
    conflict_path = _is_conflict_resolution_path(rel_path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name is None:
            continue
        if name in _COOLDOWN_STATE_CONSTRUCTION_SYMBOLS:
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=node.lineno,
                    rule=_COOLDOWN_STATE_CONSTRUCTION_RULE,
                    detail=f"Construction of '{name}': {_COOLDOWN_STATE_CONSTRUCTION_MESSAGE}",
                )
            )
            continue
        if not conflict_path:
            continue
        if name in _AGENT_FAILURE_CLASSIFY_SYMBOLS or (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "classify"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "FailureClassifier"
        ):
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=node.lineno,
                    rule=_AGENT_FAILURE_CLASSIFY_RULE,
                    detail=f"Call to '{name}': {_AGENT_FAILURE_CLASSIFY_MESSAGE}",
                )
            )
            continue
        if name in _AGENT_RETRY_DELAY_SYMBOLS:
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=node.lineno,
                    rule=_AGENT_RETRY_DELAY_RULE,
                    detail=f"Call to '{name}': {_AGENT_RETRY_DELAY_MESSAGE}",
                )
            )
    return violations


def _static_bool_op_truth(node: ast.BoolOp) -> bool | None:
    values = [_static_expression_truth(value) for value in node.values]
    if isinstance(node.op, ast.And):
        if any(value is False for value in values):
            return False
        return True if all(value is True for value in values) else None
    if all(value is False for value in values):
        return False
    return True if any(value is True for value in values) else None


def _static_if_exp_truth(node: ast.IfExp) -> bool | None:
    body_truth = _static_expression_truth(node.body)
    orelse_truth = _static_expression_truth(node.orelse)
    return body_truth if body_truth is not None and body_truth == orelse_truth else None


def _static_container_truth(node: ast.AST) -> bool | None:
    match node:
        case ast.Tuple() | ast.List() | ast.Set():
            return bool(node.elts)
        case ast.Dict():
            return bool(node.keys)
        case _:
            return None


def _static_expression_truth(node: ast.AST) -> bool | None:
    match node:
        case ast.Constant():
            return bool(node.value)
        case ast.Tuple() | ast.List() | ast.Set() | ast.Dict():
            return _static_container_truth(node)
        case ast.UnaryOp(op=ast.Not()):
            operand_truth = _static_expression_truth(node.operand)
            return None if operand_truth is None else not operand_truth
        case ast.BoolOp():
            return _static_bool_op_truth(node)
        case ast.IfExp():
            return _static_if_exp_truth(node)
        case _:
            return None


def _is_statically_falsy_expression(node: ast.AST) -> bool:
    return _static_expression_truth(node) is False


def _classified_failure_bindings(tree: ast.Module) -> frozenset[str]:
    bindings: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name == "ClassifiedFailure":
                bindings.add(alias.asname or alias.name)
    return frozenset(bindings)


def _is_classified_failure_constructor(node: ast.Call, bindings: frozenset[str]) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in bindings
    return isinstance(node.func, ast.Attribute) and node.func.attr == "ClassifiedFailure"


def _check_conflict_resolution_classification_ownership(
    tree: ast.Module, rel_path: str
) -> list[SingleOwnerViolation]:
    if not _is_conflict_resolution_path(rel_path):
        return []
    bindings = _classified_failure_bindings(tree)
    violations: list[SingleOwnerViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if _is_classified_failure_constructor(node, bindings):
                violations.append(
                    SingleOwnerViolation(
                        path=rel_path,
                        line=node.lineno,
                        rule=_CONFLICT_RESOLUTION_CLASSIFICATION_RULE,
                        detail=_CONFLICT_RESOLUTION_CLASSIFICATION_MESSAGE,
                    )
                )
                continue
            violations.extend(
                SingleOwnerViolation(
                    path=rel_path,
                    line=keyword.value.lineno,
                    rule=_CONFLICT_RESOLUTION_BUDGET_BINDING_RULE,
                    detail=_CONFLICT_RESOLUTION_BUDGET_BINDING_MESSAGE,
                )
                for keyword in node.keywords
                if keyword.arg == "counts_against_budget"
                and _is_statically_falsy_expression(keyword.value)
            )
            continue
        if not _is_assign_or_ann(node) or node.value is None:
            continue
        targets = _assignment_targets(node)
        if any(
            isinstance(target, ast.Attribute) and target.attr == "counts_against_budget"
            for target in targets
        ) and _is_statically_falsy_expression(node.value):
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=node.lineno,
                    rule=_CONFLICT_RESOLUTION_BUDGET_BINDING_RULE,
                    detail=_CONFLICT_RESOLUTION_BUDGET_BINDING_MESSAGE,
                )
            )
    return violations


def _is_false_constant(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _positional_arg(node: ast.Call, index: int) -> ast.AST | None:
    return node.args[index] if len(node.args) > index else None


def _is_progress_path_allowed(rel_path: str, function_name: str | None) -> bool:
    return function_name is not None and (rel_path, function_name) in PROGRESS_PATH_ALLOWLIST


def _is_phase_failure_state_name(name: str) -> bool:
    lowered = name.lower()
    return (
        "cooldown" in lowered
        or "backoff" in lowered
        or "retry_delay" in lowered
        or "dead_tool_surface" in lowered
        or "skip_same_agent" in lowered
        or "excluded_candidate" in lowered
        or "candidate_exclusion" in lowered
        or ("skip" in lowered and any(scope in lowered for scope in ("agent", "candidate")))
    )


def _is_phase_failure_state_allowed(rel_path: str, name: str) -> bool:
    return (rel_path, name) in PHASE_FAILURE_STATE_ALLOWLIST


def _is_phase_implementation_path(rel_path: str) -> bool:
    return rel_path.startswith(("ralph/phases/", "ralph/pipeline/conflict_resolution/"))


def _assigned_name(node: ast.Assign | ast.AnnAssign | ast.AugAssign) -> str | None:
    names = [target.id for target in _assignment_targets(node) if isinstance(target, ast.Name)]
    return names[0] if len(names) == 1 else None


def _phase_local_retry_loop_violation(
    function: ast.FunctionDef | ast.AsyncFunctionDef, rel_path: str
) -> SingleOwnerViolation | None:
    if not _is_phase_implementation_path(rel_path):
        return None
    parameters = _parameter_names(function)
    retry_maps: set[str] = set()
    for node in ast.walk(function):
        if not _is_assign_or_ann(node):
            continue
        name = _assigned_name(node)
        if name is None or "retr" not in name.lower():
            continue
        if node.value is not None and _is_dict_or_call(node.value):
            retry_maps.add(name)
    if not retry_maps:
        return None
    for candidate in ast.walk(function):
        if not _is_loop(candidate):
            continue
        loop = candidate
        calls = [node for node in ast.walk(loop) if isinstance(node, ast.Call)]
        invokes_parameter = any(
            isinstance(call.func, ast.Name) and call.func.id in parameters for call in calls
        )
        call_names = [name for call in calls if (name := _call_name(call)) is not None]
        waits = any(
            any(token in name.lower() for token in ("sleep", "wait", "delay"))
            for name in call_names
        )
        retries = any(isinstance(node, ast.Continue) for node in ast.walk(loop))
        mutated_map = False
        for node in ast.walk(loop):
            if not _is_assignment(node):
                continue
            if any(
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id in retry_maps
                for target in _assignment_targets(node)
            ):
                mutated_map = True
                break
        injected_bound = any(
            isinstance(compare, ast.Compare)
            and any(
                isinstance(child, ast.Name) and child.id in parameters
                for operand in (compare.left, *compare.comparators)
                for child in ast.walk(operand)
            )
            for compare in ast.walk(loop)
            if isinstance(compare, ast.Compare)
        )
        if invokes_parameter and waits and retries and mutated_map and injected_bound:
            return SingleOwnerViolation(
                path=rel_path,
                line=loop.lineno,
                rule="production_phase_local_retry_loop",
                detail=(
                    f"Function '{function.name}' owns a phase-local retry counter and "
                    "sleep-and-retry loop around an injected invocation; retry fate belongs "
                    "to AgentFailureLedger and RecoveryController."
                ),
            )
    return None


def check_phase_local_retry_loops(tree: ast.Module, rel_path: str) -> list[SingleOwnerViolation]:
    violations: list[SingleOwnerViolation] = []
    for function in ast.walk(tree):
        if not _is_function_def(function):
            continue
        violation = _phase_local_retry_loop_violation(function, rel_path)
        if violation is not None:
            violations.append(violation)
    return violations


def _is_round_cap_name(name: str) -> bool:
    lowered = name.lower()
    return "round" in lowered and any(token in lowered for token in ("cap", "max", "limit"))


def _assignment_value(node: ast.Assign | ast.AnnAssign | ast.AugAssign) -> ast.expr | None:
    return node.value


def _assignment_has_hardcoded_round_cap(
    target_name: str,
    node: ast.Assign | ast.AnnAssign | ast.AugAssign,
    rel_path: str,
) -> bool:
    if not _is_round_cap_name(target_name):
        return False
    if (rel_path, target_name) in ROUND_CAP_LITERAL_ALLOWLIST:
        return False
    value = _assignment_value(node)
    if value is None:
        return False
    return any(
        isinstance(child, ast.Constant)
        and _is_number(child.value)
        and not isinstance(child.value, bool)
        for child in ast.walk(value)
    )


_OWNER_ARGUMENT_NAMES: frozenset[str] = frozenset(
    {
        "controller",
        "owner",
        "recovery_controller",
    }
)

_RUNNER_CALL_NAMES: frozenset[str] = frozenset(
    {
        "run_agent_attempt",
    }
)

_INVOKE_SEAM_ARGUMENT_NAMES: frozenset[str] = frozenset(
    {
        "invoke",
        "invoke_agent",
    }
)

_TERMINATING_STATEMENTS: tuple[type[ast.stmt], ...] = (
    ast.Raise,
    ast.Return,
    ast.Break,
    ast.Continue,
)

_PATH_ACTIVE = "active"
_PATH_ROUTED = "routed"
_PATH_DELEGATED = "delegated"
_PATH_SWALLOWED = "swallowed"


@dataclass(frozen=True)
class _OwnershipContext:
    function_chain: dict[int, tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]]
    defined_function_names: frozenset[str]
    invoke_seam_names: frozenset[str]
    try_body_ids: frozenset[int]
    optional_return_names: frozenset[str]


def _annotation_allows_none(annotation: ast.expr) -> bool:
    if isinstance(annotation, ast.Constant):
        return annotation.value is None
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _annotation_allows_none(annotation.left) or _annotation_allows_none(annotation.right)
    if isinstance(annotation, ast.Subscript):
        value = annotation.value
        if isinstance(value, ast.Name):
            return value.id == "Optional"
        if isinstance(value, ast.Attribute):
            return value.attr == "Optional"
    return False


def _function_chain_by_node(
    tree: ast.Module,
) -> dict[int, tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]]:
    chains: dict[int, tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]] = {}

    def visit(node: ast.AST, chain: tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            chains[id(child)] = chain
            if _is_function_def(child):
                visit(child, (*chain, child))
            else:
                visit(child, chain)

    visit(tree, ())
    return chains


def _try_body_node_ids(tree: ast.Module) -> frozenset[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for statement in node.body:
            for child in ast.walk(statement):
                ids.add(id(child))
    return frozenset(ids)


_RUNNER_DEPS_CALL_NAME = "AttemptRunnerDeps"


def _invoke_seam_names(tree: ast.Module) -> frozenset[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) != _RUNNER_DEPS_CALL_NAME:
            continue
        for keyword in node.keywords:
            if keyword.arg in _INVOKE_SEAM_ARGUMENT_NAMES and isinstance(keyword.value, ast.Name):
                names.add(keyword.value.id)
    return frozenset(names)


def _optional_return_names(tree: ast.Module) -> frozenset[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            _is_function_def(node)
            and node.returns is not None
            and _annotation_allows_none(node.returns)
        ):
            names.add(node.name)
    return frozenset(names)


def _build_ownership_context(tree: ast.Module) -> _OwnershipContext:
    return _OwnershipContext(
        function_chain=_function_chain_by_node(tree),
        defined_function_names=frozenset(
            node.name for node in ast.walk(tree) if _is_function_def(node)
        ),
        invoke_seam_names=_invoke_seam_names(tree),
        try_body_ids=_try_body_node_ids(tree),
        optional_return_names=_optional_return_names(tree),
    )


def _parameter_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    arguments = function.args
    names = {
        argument.arg
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    }
    if arguments.vararg is not None:
        names.add(arguments.vararg.arg)
    if arguments.kwarg is not None:
        names.add(arguments.kwarg.arg)
    return frozenset(names)


def _parameter_annotations(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, ast.expr | None]:
    arguments = function.args
    annotations: dict[str, ast.expr | None] = {}
    for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs):
        annotations[argument.arg] = argument.annotation
    return annotations


def _owner_argument_value(call: ast.Call) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg in _OWNER_ARGUMENT_NAMES:
            return keyword.value
    return None


def _expression_is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _owner_is_definitely_null(owner: ast.expr) -> bool:
    if _expression_is_none(owner):
        return True
    if isinstance(owner, ast.IfExp):
        return _expression_is_none(owner.body) or _expression_is_none(owner.orelse)
    if isinstance(owner, ast.BoolOp):
        return any(_expression_is_none(value) for value in owner.values)
    return False


def _null_bound_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    null_assigned: set[str] = set()
    other_assigned: set[str] = set()
    for node in ast.walk(function):
        targets: list[ast.expr]
        value: ast.expr | None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
            value = node.value
        else:
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if value is not None and _expression_is_none(value):
                null_assigned.add(target.id)
            else:
                other_assigned.add(target.id)
    return frozenset(null_assigned - other_assigned)


def _is_narrowed_to_owner(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    for node in ast.walk(function):
        if not isinstance(node, ast.Compare):
            continue
        if not any(_is_identity_cmp(operator) for operator in node.ops):
            continue
        for operand in (node.left, *node.comparators):
            if isinstance(operand, ast.Name) and operand.id == name:
                return True
    return False


def _named_owner_defect(
    runner: str | None,
    name: str,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str | None:
    if name in _null_bound_names(function):
        return (
            f"Runner '{runner}' is invoked with the owner '{name}', which is bound to "
            "None; the agent failure cannot reach the single owner."
        )
    annotation = _parameter_annotations(function).get(name)
    if (
        annotation is not None
        and _annotation_allows_none(annotation)
        and not _is_narrowed_to_owner(function, name)
    ):
        return (
            f"Runner '{runner}' is invoked with the optional owner '{name}', which is "
            "never narrowed to a real owner; the owner ownership is unchecked."
        )
    return None


def _owner_ownership_defect(
    call: ast.Call,
    owner: ast.expr,
    function: ast.FunctionDef | ast.AsyncFunctionDef | None,
    context: _OwnershipContext,
) -> str | None:
    runner = _call_name(call)
    if _owner_is_definitely_null(owner):
        return (
            f"Runner '{runner}' is invoked with an owner argument that can be None; "
            "the agent failure cannot reach the single owner."
        )
    if (
        isinstance(owner, ast.Call)
        and isinstance(owner.func, ast.Name)
        and owner.func.id in context.optional_return_names
    ):
        return (
            f"Runner '{runner}' is invoked with an owner produced by '{owner.func.id}', "
            "whose return type may be None; the owner ownership is unchecked."
        )
    named_defect = (
        _named_owner_defect(runner, owner.id, function)
        if isinstance(owner, ast.Name) and function is not None
        else None
    )
    if named_defect is not None:
        return named_defect
    return None


def _enclosing_function(
    node: ast.AST, context: _OwnershipContext
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    chain = context.function_chain.get(id(node), ())
    return chain[-1] if chain else None


def _check_unguarded_agent_invocation(
    tree: ast.Module, rel_path: str, context: _OwnershipContext
) -> list[SingleOwnerViolation]:
    violations: list[SingleOwnerViolation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name is None or name not in _AGENT_INVOCATION_CALLS:
            continue
        if name in _RUNNER_CALL_NAMES:
            continue
        if not isinstance(node.func, ast.Name):
            continue
        chain = context.function_chain.get(id(node), ())
        if len(chain) != 1:
            continue
        function = chain[0]
        if _is_progress_path_allowed(rel_path, function.name):
            continue
        if name in _parameter_names(function):
            continue
        if name in context.defined_function_names:
            continue
        if id(node) in context.try_body_ids:
            continue
        owner = _owner_argument_value(node)
        if owner is not None and _owner_ownership_defect(node, owner, function, context) is None:
            continue
        if function.name in context.invoke_seam_names:
            continue
        violations.append(
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="production_agent_invoker_unchecked",
                detail=(
                    f"Agent invocation '{name}' is neither wrapped in a try/except that routes the "
                    "failure to the owner nor given an owner argument, and it is not registered as "
                    "the runner's invoke seam; the agent failure cannot reach the single owner."
                ),
            )
        )
    return violations


def _check_runner_ownership(
    tree: ast.Module, rel_path: str, context: _OwnershipContext
) -> list[SingleOwnerViolation]:
    violations: list[SingleOwnerViolation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name is None or name not in _RUNNER_CALL_NAMES:
            continue
        owner = _owner_argument_value(node)
        if owner is None:
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=node.lineno,
                    rule="production_agent_runner_no_controller",
                    detail=(
                        f"Runner '{name}' is invoked without an owner argument; "
                        "the agent failure cannot reach the single owner."
                    ),
                )
            )
            continue
        defect = _owner_ownership_defect(node, owner, _enclosing_function(node, context), context)
        if defect is None:
            continue
        violations.append(
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="production_agent_runner_controller_only",
                detail=defect,
            )
        )
    return violations


def _direct_calls(statement: ast.stmt) -> list[ast.Call]:
    headers: list[ast.AST]
    if isinstance(statement, (ast.If, ast.While)):
        headers = [statement.test]
    elif isinstance(statement, (ast.For, ast.AsyncFor)):
        headers = [statement.iter]
    elif isinstance(statement, (ast.With, ast.AsyncWith)):
        headers = list(statement.items)
    elif isinstance(statement, ast.Match):
        headers = [statement.subject]
    elif isinstance(statement, ast.Try):
        headers = []
    else:
        headers = [statement]
    return [
        child for header in headers for child in ast.walk(header) if isinstance(child, ast.Call)
    ]


def _is_classifier_call(call: ast.Call) -> bool:
    name = _call_name(call)
    return name is not None and name.startswith("classify_")


def _contains_node(outer: ast.AST, inner: ast.AST) -> bool:
    return any(child is inner for child in ast.walk(outer))


def _routes_to_owner(
    calls: list[ast.Call], *, classified_before: bool, context: _OwnershipContext | None = None
) -> bool:
    classifiers = [call for call in calls if _is_classifier_call(call)]
    handles = [call for call in calls if _call_name(call) == "handle"]
    for handle in handles:
        if classified_before:
            return True
        for classifier in classifiers:
            if classifier.lineno <= handle.lineno:
                return True
            if _contains_node(handle, classifier):
                return True
    return False


def _annotation_names(annotation: ast.expr | None) -> frozenset[str]:
    if annotation is None:
        return frozenset()
    names: set[str] = set()
    for child in ast.walk(annotation):
        if _is_name_or_attr(child):
            names.add(child.id if isinstance(child, ast.Name) else child.attr)
    return frozenset(names)


def _is_cooldown_owner_handle(call: ast.Call, context: _OwnershipContext) -> bool:
    if not isinstance(call.func, ast.Attribute):
        return False
    receiver = call.func.value
    if isinstance(receiver, ast.Name):
        name = receiver.id
    else:
        return False
    function = _enclosing_function(call, context)
    if function is None:
        return False
    annotation = _parameter_annotations(function).get(name)
    if "RecoveryController" in _annotation_names(annotation):
        return True
    for node in ast.walk(function):
        if not _is_assign_or_ann(node) or node.value is None:
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == name
            for target in _assignment_targets(node)
        ):
            continue
        if any(
            isinstance(child, ast.Call) and _call_name(child) == "RecoveryController"
            for child in ast.walk(node.value)
        ):
            return True
    return False


def _branch_bodies(statement: ast.stmt) -> list[list[ast.stmt]] | None:
    if isinstance(statement, ast.If):
        return [statement.body, statement.orelse]
    if isinstance(statement, ast.Try):
        branches = [statement.body, statement.orelse, statement.finalbody]
        branches.extend(handler.body for handler in statement.handlers)
        return branches
    if _is_loop(statement):
        return [statement.body, statement.orelse]
    if _is_with_block(statement):
        return [statement.body]
    if isinstance(statement, ast.Match):
        return [case.body for case in statement.cases]
    return None


def _execute_statement(
    statement: ast.stmt, *, classified_before: bool, context: _OwnershipContext
) -> set[tuple[bool, str]]:
    calls = _direct_calls(statement)
    classified = classified_before or any(_is_classifier_call(call) for call in calls)
    if _routes_to_owner(calls, classified_before=classified_before, context=context):
        return {(classified, _PATH_ROUTED)}
    if isinstance(statement, ast.Raise):
        return {(classified, _PATH_DELEGATED)}
    if _is_flow_exit(statement):
        return {(classified, _PATH_SWALLOWED)}
    branches = _branch_bodies(statement)
    if branches is None:
        return {(classified, _PATH_ACTIVE)}
    states: set[tuple[bool, str]] = set()
    for branch in branches:
        states |= _block_states(branch, classified=classified, context=context)
    return states


def _block_states(
    body: list[ast.stmt], *, classified: bool, context: _OwnershipContext
) -> set[tuple[bool, str]]:
    states: set[tuple[bool, str]] = {(classified, _PATH_ACTIVE)}
    for statement in body:
        advanced: set[tuple[bool, str]] = set()
        for state_classified, state_outcome in states:
            if state_outcome != _PATH_ACTIVE:
                advanced.add((state_classified, state_outcome))
                continue
            advanced |= _execute_statement(
                statement, classified_before=state_classified, context=context
            )
        states = advanced
    return states


def _handler_outcomes(handler: ast.ExceptHandler, context: _OwnershipContext) -> set[str]:
    states = _block_states(handler.body, classified=False, context=context)
    return {_PATH_SWALLOWED if outcome == _PATH_ACTIVE else outcome for _, outcome in states}


def _static_truth(node: ast.expr) -> bool | None:
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if _is_sequence_display(node):
        return bool(node.elts)
    return None


def _collect_dead_in_block(body: list[ast.stmt], dead: set[int]) -> None:
    for index, statement in enumerate(body):
        if isinstance(statement, _TERMINATING_STATEMENTS):
            for trailing in body[index + 1 :]:
                dead.update(id(node) for node in ast.walk(trailing))
            break
        _collect_dead_in_statement(statement, dead)


def _collect_dead_in_statement(statement: ast.stmt, dead: set[int]) -> None:
    if isinstance(statement, ast.If):
        truth = _static_truth(statement.test)
        if truth is False:
            for node in statement.body:
                dead.update(id(child) for child in ast.walk(node))
        elif truth is True:
            for node in statement.orelse:
                dead.update(id(child) for child in ast.walk(node))
        _collect_dead_in_block(statement.body, dead)
        _collect_dead_in_block(statement.orelse, dead)
        return
    if isinstance(statement, ast.Try):
        _collect_dead_in_block(statement.body, dead)
        for handler in statement.handlers:
            _collect_dead_in_block(handler.body, dead)
        _collect_dead_in_block(statement.orelse, dead)
        _collect_dead_in_block(statement.finalbody, dead)
        return
    if _is_for_loop(statement):
        _collect_dead_in_block(statement.body, dead)
        _collect_dead_in_block(statement.orelse, dead)
        return
    if isinstance(statement, ast.While):
        if _static_truth(statement.test) is False:
            for node in statement.body:
                dead.update(id(child) for child in ast.walk(node))
        _collect_dead_in_block(statement.body, dead)
        _collect_dead_in_block(statement.orelse, dead)
        return
    if _is_with_block(statement):
        _collect_dead_in_block(statement.body, dead)
        return
    if isinstance(statement, ast.Match):
        for case in statement.cases:
            _collect_dead_in_block(case.body, dead)


_AGENT_FAILURE_EXCEPT_TUPLE_RULE = "production_agent_failure_except_tuple_coverage"

_AGENT_FAILURE_DOMAIN_BASES: frozenset[str] = frozenset(
    {
        "AgentInvocationError",
        "Exception",
        "BaseException",
    }
)


def _exception_matches_domain_base(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id in _AGENT_FAILURE_DOMAIN_BASES
    if isinstance(node, ast.Attribute):
        return node.attr in _AGENT_FAILURE_DOMAIN_BASES
    return False


def _tuple_includes_domain_base(node: ast.Tuple) -> bool:
    for elt in node.elts:
        if _exception_matches_domain_base(elt):
            return True
        if isinstance(elt, ast.Tuple) and _tuple_includes_domain_base(elt):
            return True
    return False


def _recovery_calls(handler: ast.ExceptHandler) -> list[ast.Call]:
    calls = [
        child
        for child in ast.walk(handler)
        if isinstance(child, ast.Call)
        and (_is_classifier_call(child) or _call_name(child) == "handle")
    ]
    return calls


def _check_agent_invocation_failure_routing(
    node: ast.Try, rel_path: str, context: _OwnershipContext
) -> list[SingleOwnerViolation]:
    invocation_calls = [
        child
        for statement in node.body
        for child in ast.walk(statement)
        if isinstance(child, ast.Call) and _call_name(child) in _AGENT_INVOCATION_CALLS
    ]
    if not invocation_calls:
        return []
    violations: list[SingleOwnerViolation] = []
    bypassed_lines: set[int] = set()
    for handler in node.handlers:
        dead: set[int] = set()
        _collect_dead_in_block(handler.body, dead)
        violations.extend(
            SingleOwnerViolation(
                path=rel_path,
                line=call.lineno,
                rule="production_agent_failure_recovery_unreachable",
                detail=(
                    f"Recovery call '{_call_name(call)}' in the caught handler can never run; "
                    "the agent failure is not routed to the single owner."
                ),
            )
            for call in _recovery_calls(handler)
            if id(call) in dead
        )
        if _handler_outcomes(handler, context) <= {_PATH_ROUTED, _PATH_DELEGATED}:
            continue
        bypassed_lines.update(
            invocation.lineno
            for invocation in invocation_calls
            if not _invocation_routes_through_owner(invocation)
        )
    violations.extend(
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
    )
    for handler in node.handlers:
        if isinstance(handler.type, ast.Tuple) and not _tuple_includes_domain_base(handler.type):
            target_name = _call_name(invocation_calls[0]) or "agent invocation"
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=handler.lineno,
                    rule=_AGENT_FAILURE_EXCEPT_TUPLE_RULE,
                    detail=(
                        f"Except tuple wrapping agent invocation '{target_name}' "
                        "excludes the agent-failure domain base 'AgentInvocationError'."
                    ),
                )
            )
        calls = _recovery_calls(handler)
        if (
            any(_is_classifier_call(call) for call in calls)
            and any(_call_name(call) == "handle" for call in calls)
            and not any(_is_cooldown_owner_handle(call, context) for call in calls)
        ):
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=handler.lineno,
                    rule="production_agent_failure_cooldown_bypass",
                    detail=(
                        "Caught agent failure is handled by an object that is not identifiable "
                        "as the recovery/cooldown owner."
                    ),
                )
            )
    return violations


def _invocation_routes_through_owner(node: ast.Call) -> bool:
    if _call_name(node) not in _RUNNER_CALL_NAMES:
        return False
    owner = _owner_argument_value(node)
    if owner is None:
        return False
    return not _owner_is_definitely_null(owner)


def _check_forbidden_integration_classifier(
    tree: ast.Module, rel_path: str
) -> list[SingleOwnerViolation]:
    violations: list[SingleOwnerViolation] = []
    for node in ast.walk(tree):
        if _is_function_def(node) and node.name in _FORBIDDEN_INTEGRATION_CLASSIFIER_NAMES:
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=node.lineno,
                    rule=_FORBIDDEN_INTEGRATION_CLASSIFIER_RULE,
                    detail=(
                        f"Definition of '{node.name}' is forbidden. "
                        f"{_FORBIDDEN_INTEGRATION_CLASSIFIER_MESSAGE}"
                    ),
                )
            )
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in _FORBIDDEN_INTEGRATION_CLASSIFIER_NAMES:
                violations.append(
                    SingleOwnerViolation(
                        path=rel_path,
                        line=node.lineno,
                        rule=_FORBIDDEN_INTEGRATION_CLASSIFIER_RULE,
                        detail=(
                            f"Call to '{name}' is forbidden. "
                            f"{_FORBIDDEN_INTEGRATION_CLASSIFIER_MESSAGE}"
                        ),
                    )
                )
    return violations


def _check_agent_retry_timing_outside_shared_path(
    tree: ast.Module, rel_path: str
) -> list[SingleOwnerViolation]:
    if rel_path in _SHARED_COOLDOWN_OWNER_PATHS:
        return []
    if rel_path.startswith("ralph/recovery/"):
        return []
    violations: list[SingleOwnerViolation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name not in _AGENT_RETRY_TIMING_OUTSIDE_SHARED_PATH:
            continue
        if name == "wait_before_retry" and rel_path.startswith("ralph/pipeline/auto_integrate"):
            continue
        if name == "compute_retry_delay" and rel_path.startswith("ralph/pipeline/auto_integrate"):
            continue
        violations.append(
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule=_AGENT_RETRY_TIMING_RULE,
                detail=f"Call to '{name}': {_AGENT_RETRY_TIMING_MESSAGE}",
            )
        )
    return violations


_TYPED_OUTCOME_CLASSIFICATION_NAME = "_TYPED_OUTCOME_CLASSIFICATION"


def _assignment_target_names(node: ast.Assign | ast.AnnAssign | ast.AugAssign) -> list[str]:
    return [target.id for target in _assignment_targets(node) if isinstance(target, ast.Name)]


def _typed_outcome_key_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _typed_outcome_mapping_is_unpenalised(value: ast.AST) -> bool:
    if not isinstance(value, ast.Tuple) or len(value.elts) < _TYPED_OUTCOME_CLASSIFICATION_ARITY:
        return False
    counts = value.elts[1]
    reason = value.elts[2]
    counts_false = _is_false_constant(counts) or _is_statically_falsy_expression(counts)
    reason_none = isinstance(reason, ast.Constant) and reason.value is None
    return counts_false and reason_none


def _check_typed_outcome_classification_mapping(
    tree: ast.Module, rel_path: str
) -> list[SingleOwnerViolation]:
    violations: list[SingleOwnerViolation] = []
    for node in tree.body:
        if not _is_assign_or_ann(node) or node.value is None:
            continue
        if _TYPED_OUTCOME_CLASSIFICATION_NAME not in _assignment_target_names(node):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values, strict=False):
            if value is None or not _typed_outcome_mapping_is_unpenalised(value):
                continue
            outcome_name = _typed_outcome_key_name(key)
            if outcome_name is not None and _is_progress_path_allowed(rel_path, outcome_name):
                continue
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=value.lineno,
                    rule="production_unjustified_non_penalising_failure",
                    detail=(
                        "Typed outcome classification disables both budget charge and "
                        "unavailability outside an explicitly justified progress-made path."
                    ),
                )
            )
    return violations


def check_agent_failure_ownership(tree: ast.Module, rel_path: str) -> list[SingleOwnerViolation]:
    context = _build_ownership_context(tree)
    violations = _check_conflict_resolution_classification_ownership(tree, rel_path)
    violations.extend(_check_typed_outcome_classification_mapping(tree, rel_path))
    violations.extend(_check_cooldown_state_single_owner(tree, rel_path))
    violations.extend(_check_forbidden_integration_classifier(tree, rel_path))
    violations.extend(_check_agent_retry_timing_outside_shared_path(tree, rel_path))
    violations.extend(_check_unguarded_agent_invocation(tree, rel_path, context))
    violations.extend(_check_runner_ownership(tree, rel_path, context))
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            violations.extend(_check_agent_invocation_failure_routing(node, rel_path, context))
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
        if not has_forbidden and not any(_is_greater_cmp(op) for op in node.ops):
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
                or (isinstance(other, ast.Constant) and _is_number(other.value))
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
    "should_reset_tool_registry": 0,
    "record_resolver_failure": 2,
}

_FORBIDDEN_INTEGRATION_CLASSIFIER_NAMES: frozenset[str] = frozenset(
    {"classify_failed_resolution_attempt"}
)

_AGENT_RETRY_TIMING_OUTSIDE_SHARED_PATH: frozenset[str] = frozenset(
    {"wait_before_retry", "compute_retry_delay"}
)

_SHARED_COOLDOWN_OWNER_PATHS: frozenset[str] = frozenset(
    {
        "ralph/recovery/controller.py",
        "ralph/recovery/agent_failure_ledger.py",
        "ralph/recovery/agent_unavailability_tracker.py",
        "ralph/pipeline/auto_integrate_backoff.py",
        "ralph/pipeline/auto_integrate_conflict_budget.py",
        "ralph/pipeline/_auto_integrate_attempt_loop.py",
        "ralph/testing/_single_owner_rules.py",
        "ralph/testing/audit_agent_failure_single_owner.py",
        "ralph/testing/audit_integration_no_legacy.py",
    }
)

_AGENT_RETRY_TIMING_RULE = "production_agent_retry_timing_outside_shared_path"

_AGENT_RETRY_TIMING_MESSAGE = (
    "Agent-retry timing outside the shared RecoveryController cooldown path is forbidden; "
    "use preferred_agent_index / earliest_available_wait_ms / finalize_attempt instead."
)

_FORBIDDEN_INTEGRATION_CLASSIFIER_RULE = "production_forbidden_integration_classifier"

_FORBIDDEN_INTEGRATION_CLASSIFIER_MESSAGE = (
    "Integration-only agent-failure classifier is forbidden; build AttemptFacts and "
    "finalise through RecoveryController.finalize_attempt / run_agent_attempt."
)

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
        if not _is_assign_or_ann(node) or node.value is None:
            continue
        if not any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "FailureClassifier"
            for child in ast.walk(node.value)
        ):
            continue
        for target in _assignment_targets(node):
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
        if not _is_assign_or_ann(node):
            continue
        value = node.value
        if value is None:
            continue
        for target in _assignment_targets(node):
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
        if _is_assign_or_ann(node):
            value = node.value
            if value is None or not _expr_contains_ralph_authored_prose(value, module_constants):
                continue
            for target in _assignment_targets(node):
                name: str | None = None
                if isinstance(target, ast.Name):
                    name = target.id
                elif isinstance(target, ast.Attribute):
                    name = target.attr
                if name is not None and name not in tainted:
                    tainted[name] = node.lineno
        if _is_function_def(node) and any(
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
    *,
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
                direct_prose=direct_prose,
                tainted_field=tainted_field,
                tainted_name=tainted_name,
                prose_returner=prose_returner,
                rel_path=rel_path,
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
