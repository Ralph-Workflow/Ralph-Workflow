from __future__ import annotations

import ast
import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ralph.testing._single_owner_retry_cap_rule import _check_production_retry_cap_rule
from ralph.testing._single_owner_rules import (
    _FORBIDDEN_TRACKER_MODULES,
    _FORBIDDEN_TRACKER_SYMBOLS,
    PROGRESS_PATH_ALLOWLIST,
    TEMPORARY_PRODUCTION_ALLOWLIST,
    TEMPORARY_TEST_ALLOWLIST,
    TEMPORARY_TEST_COUNTER_READS,
    TEMPORARY_TRANSITIONAL_RULES,
    SingleOwnerViolation,
    _check_production_assignment,
    _check_production_attribute,
    _check_production_call,
    _check_production_class,
    _check_production_compare,
    _check_production_function,
    _check_production_import,
    _check_production_subscript,
    _check_ralph_authored_classifier_inputs,
    _is_forbidden_target_name,
    _is_ledger_receiver,
    check_agent_failure_ownership,
    is_test_counter_read_allowed,
    is_transitional_allowed,
)
from ralph.testing._single_owner_test_rules import (
    _GETATTR_MIN_ARGS,
    _PRIVATE_FAILURE_OWNER_SERIALISED_KEYS,
    _PRIVATE_LEDGER_COUNTERS,
    _is_ledger_serialised_root,
    _is_private_failure_owner_name,
    _is_serialised_snapshot_call,
    _serialised_counter_violation,
    private_failure_owner_violation,
    serialised_failure_owner_violation,
)

__all__ = [
    "PRODUCTION_SCAN_SUBTREES",
    "PROGRESS_PATH_ALLOWLIST",
    "TEMPORARY_PRODUCTION_ALLOWLIST",
    "TEMPORARY_TEST_ALLOWLIST",
    "TEMPORARY_TEST_COUNTER_READS",
    "TEMPORARY_TRANSITIONAL_RULES",
    "SingleOwnerViolation",
    "audit_production_source",
    "audit_test_source",
    "is_test_counter_read_allowed",
    "is_transitional_allowed",
    "main",
]

PRODUCTION_SCAN_SUBTREES: tuple[str, ...] = ("ralph",)


def audit_production_source(rel_path: str, source: str) -> list[SingleOwnerViolation]:
    if rel_path == "ralph/recovery/agent_failure_ledger.py":
        return []
    if rel_path in TEMPORARY_PRODUCTION_ALLOWLIST:
        return []

    file_stem = Path(rel_path).stem
    if file_stem in _FORBIDDEN_TRACKER_MODULES:
        return [
            SingleOwnerViolation(
                path=rel_path,
                line=1,
                rule="production_legacy_tracker_module",
                detail=(
                    f"Deprecated tracker module '{file_stem}' must be deleted; "
                    "all failure and retry tracking belongs in AgentFailureLedger."
                ),
            )
        ]

    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        return [
            SingleOwnerViolation(
                path=rel_path,
                line=exc.lineno or 1,
                rule="syntax_error",
                detail=f"Cannot parse Python source: {exc}",
            )
        ]

    enclosing_functions = _enclosing_function_names(tree)
    violations: list[SingleOwnerViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            violations.extend(_check_production_class(node, rel_path))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            violations.extend(_check_production_function(node, rel_path))
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            violations.extend(_check_production_assignment(node, rel_path))
        elif isinstance(node, ast.Attribute):
            violations.extend(_check_production_attribute(node, rel_path))
        elif isinstance(node, ast.Subscript):
            violations.extend(_check_production_subscript(node, rel_path))
        elif isinstance(node, ast.Compare):
            violations.extend(_check_production_compare(node, rel_path))
        elif isinstance(node, (ast.If, ast.While, ast.IfExp)):
            violations.extend(_check_production_retry_cap_rule(node, rel_path))
        elif isinstance(node, ast.Call):
            violations.extend(
                _check_production_call(node, rel_path, enclosing_functions.get(id(node)))
            )
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            violations.extend(_check_production_import(node, rel_path))
    violations.extend(_check_ralph_authored_classifier_inputs(tree, rel_path))
    violations.extend(check_agent_failure_ownership(tree, rel_path))
    return violations


def _enclosing_function_names(tree: ast.Module) -> dict[int, str]:
    names: dict[int, str] = {}
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(function):
                names.setdefault(id(child), function.name)
    return names


def _check_test_import(
    node: ast.Import | ast.ImportFrom, rel_path: str
) -> list[SingleOwnerViolation]:
    violations: list[SingleOwnerViolation] = []
    if isinstance(node, ast.Import):
        violations.extend(
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="test_deleted_tracker_import",
                detail=f"Test imports deleted tracker '{alias.name}'.",
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
                    rule="test_deleted_tracker_import",
                    detail=f"Test imports from deleted tracker module '{node.module}'.",
                )
            )
        violations.extend(
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="test_deleted_tracker_import",
                detail=f"Test imports deleted tracker symbol '{alias.name}'.",
            )
            for alias in node.names
            if not is_transitional_allowed(rel_path, alias.name)
            and alias.name in _FORBIDDEN_TRACKER_SYMBOLS
        )
    return violations


def _check_test_attribute(node: ast.Attribute, rel_path: str) -> list[SingleOwnerViolation]:
    attr = node.attr
    if attr in ("consecutive_unproductive", "total_unproductive") and not (
        is_test_counter_read_allowed(rel_path, attr)
    ):
        return [
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="test_counter_access",
                detail=(
                    f"Test accesses counter attribute '{attr}'; "
                    "tests must observe public status, demoted, and behavior rather than internal counters."
                ),
            )
        ]
    if (
        attr in _PRIVATE_LEDGER_COUNTERS
        and _is_ledger_serialised_root(node.value)
        and not is_transitional_allowed(rel_path, attr)
    ):
        return _serialised_counter_violation(node, attr, rel_path)
    if _is_forbidden_target_name(attr) and not is_transitional_allowed(rel_path, attr):
        return [
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="test_deleted_tracker_access",
                detail=f"Test accesses deleted tracker/counter attribute '{attr}'.",
            )
        ]
    if (
        attr.startswith("_")
        and not (attr.startswith("__") and attr.endswith("__"))
        and _is_ledger_receiver(node.value)
    ):
        return [
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="test_private_ledger_access",
                detail=(
                    f"Test accesses private attribute '{attr}' on ledger object; "
                    "tests must use the public seam only."
                ),
            )
        ]
    if _is_private_failure_owner_name(attr) and not is_transitional_allowed(rel_path, attr):
        return private_failure_owner_violation(node, attr, rel_path)
    return []


def _check_test_call(node: ast.Call, rel_path: str) -> list[SingleOwnerViolation]:
    violations: list[SingleOwnerViolation] = []
    for kw in node.keywords:
        if kw.arg and not is_transitional_allowed(rel_path, kw.arg):
            if kw.arg in ("consecutive_unproductive", "total_unproductive") and not (
                is_test_counter_read_allowed(rel_path, kw.arg)
            ):
                violations.append(
                    SingleOwnerViolation(
                        path=rel_path,
                        line=node.lineno,
                        rule="test_counter_access",
                        detail=f"Test passes ledger counter keyword argument '{kw.arg}'.",
                    )
                )
            elif _is_forbidden_target_name(kw.arg):
                violations.append(
                    SingleOwnerViolation(
                        path=rel_path,
                        line=node.lineno,
                        rule="test_deleted_tracker_access",
                        detail=f"Test passes competing tracker/counter keyword '{kw.arg}'.",
                    )
                )

    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        counter = node.args[0].value
        if counter in ("consecutive_unproductive", "total_unproductive") and not (
            is_test_counter_read_allowed(rel_path, counter)
        ):
            violations.append(
                SingleOwnerViolation(
                    path=rel_path,
                    line=node.lineno,
                    rule="test_private_ledger_access",
                    detail=(
                        f"Test accesses private ledger counter '{counter}' via .get(); "
                        "tests must assert on observable behavior through the public seam only."
                    ),
                )
            )
        elif (
            counter in _PRIVATE_LEDGER_COUNTERS
            and _is_ledger_serialised_root(node.func.value)
            and not is_transitional_allowed(rel_path, counter)
        ):
            violations.extend(_serialised_counter_violation(node, counter, rel_path))
        elif (
            counter in _PRIVATE_FAILURE_OWNER_SERIALISED_KEYS
            and _is_serialised_snapshot_call(node.func.value)
            and not is_transitional_allowed(rel_path, counter)
        ):
            violations.extend(serialised_failure_owner_violation(node, counter, rel_path))

    if not (
        isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= _GETATTR_MIN_ARGS
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return violations

    arg_val = node.args[1].value
    if arg_val in ("consecutive_unproductive", "total_unproductive") and not (
        is_test_counter_read_allowed(rel_path, arg_val)
    ):
        violations.append(
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="test_counter_access",
                detail=f"Test accesses ledger counter attribute '{arg_val}' via getattr.",
            )
        )
    elif _is_forbidden_target_name(arg_val) and not is_transitional_allowed(rel_path, arg_val):
        violations.append(
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="test_deleted_tracker_access",
                detail=f"Test accesses deleted tracker/counter attribute '{arg_val}' via getattr.",
            )
        )

    if (
        arg_val.startswith("_")
        and not (arg_val.startswith("__") and arg_val.endswith("__"))
        and _is_ledger_receiver(node.args[0])
    ):
        violations.append(
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="test_private_ledger_access",
                detail=(
                    f"Test accesses private attribute '{arg_val}' on ledger object via getattr; "
                    "tests must use the public seam only."
                ),
            )
        )

    if _is_private_failure_owner_name(arg_val) and not is_transitional_allowed(rel_path, arg_val):
        violations.extend(
            private_failure_owner_violation(node, arg_val, rel_path, via_getattr=True)
        )

    return violations


def _check_test_subscript(node: ast.Subscript, rel_path: str) -> list[SingleOwnerViolation]:
    if (
        isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
        and node.slice.value
        in (
            "consecutive_unproductive",
            "total_unproductive",
        )
        and not is_test_counter_read_allowed(rel_path, node.slice.value)
    ):
        return [
            SingleOwnerViolation(
                path=rel_path,
                line=node.lineno,
                rule="test_private_ledger_access",
                detail=(
                    f"Test accesses private ledger counter '{node.slice.value}' via snapshot subscript; "
                    "tests must assert on observable behavior through the public seam only."
                ),
            )
        ]
    if (
        isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
        and node.slice.value in _PRIVATE_LEDGER_COUNTERS
        and _is_ledger_serialised_root(node.value)
        and not is_transitional_allowed(rel_path, node.slice.value)
    ):
        return _serialised_counter_violation(node, node.slice.value, rel_path)
    if (
        isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
        and node.slice.value in _PRIVATE_FAILURE_OWNER_SERIALISED_KEYS
        and _is_serialised_snapshot_call(node.value)
        and not is_transitional_allowed(rel_path, node.slice.value)
    ):
        return serialised_failure_owner_violation(node, node.slice.value, rel_path)
    return []


def audit_test_source(rel_path: str, source: str) -> list[SingleOwnerViolation]:
    if rel_path in TEMPORARY_TEST_ALLOWLIST:
        return []

    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        return [
            SingleOwnerViolation(
                path=rel_path,
                line=exc.lineno or 1,
                rule="syntax_error",
                detail=f"Cannot parse test source: {exc}",
            )
        ]

    violations: list[SingleOwnerViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            violations.extend(_check_test_import(node, rel_path))
        elif isinstance(node, ast.Attribute):
            violations.extend(_check_test_attribute(node, rel_path))
        elif isinstance(node, ast.Compare):
            violations.extend(_check_production_compare(node, rel_path))
        elif isinstance(node, ast.Call):
            violations.extend(_check_test_call(node, rel_path))
        elif isinstance(node, ast.Subscript):
            violations.extend(_check_test_subscript(node, rel_path))
    return violations


def _violation_sort_key(violation: SingleOwnerViolation) -> tuple[str, int]:
    return (violation.path, violation.line)


def verify_counter_access_detection() -> None:
    src_test_attr = """
def test_sample(ledger, fact):
    outcome = ledger.record(fact)
    assert outcome.consecutive_unproductive == 1
"""
    v1 = audit_test_source("tests/recovery/test_sample.py", src_test_attr)
    assert any(
        v.rule == "test_counter_access" and "consecutive_unproductive" in v.detail for v in v1
    ), f"Expected test_counter_access violation for consecutive_unproductive, got: {v1}"

    src_test_total = """
def test_sample(ledger, fact):
    outcome = ledger.record(fact)
    assert outcome.total_unproductive == 1
"""
    v2 = audit_test_source("tests/recovery/test_sample.py", src_test_total)
    assert any(v.rule == "test_counter_access" and "total_unproductive" in v.detail for v in v2), (
        f"Expected test_counter_access violation for total_unproductive, got: {v2}"
    )

    src_prod_decl = """
class Worker:
    def __init__(self):
        self.consecutive_unproductive = 0
"""
    v3 = audit_production_source("ralph/pipeline/worker.py", src_prod_decl)
    assert any("consecutive_unproductive" in v.detail for v in v3), (
        f"Expected violation for non-ledger counter declaration, got: {v3}"
    )

    src_prod_attr = """
def check(ledger):
    return ledger.consecutive_unproductive > 0
"""
    v4 = audit_production_source("ralph/pipeline/worker.py", src_prod_attr)
    assert any("consecutive_unproductive" in v.detail for v in v4), (
        f"Expected violation for non-ledger counter access, got: {v4}"
    )

    src_test_get = """
def test_sample(snap):
    count = snap.get("consecutive_unproductive")
"""
    v5 = audit_test_source("tests/recovery/test_sample.py", src_test_get)
    assert any(
        v.rule == "test_private_ledger_access" and "consecutive_unproductive" in v.detail
        for v in v5
    ), (
        f"Expected test_private_ledger_access violation for .get(consecutive_unproductive), got: {v5}"
    )

    src_rc1 = """
def handle_fault(session, agent_name):
    session.ralph_fault_hits += 1
    if session.ralph_fault_hits >= 5:
        session.stop_dead_surfaces = (*session.stop_dead_surfaces, agent_name)
"""
    v6 = audit_production_source("ralph/pipeline/worker.py", src_rc1)
    assert any("ralph_fault_hits" in v.detail for v in v6), (
        f"Expected violation for ralph_fault_hits, got: {v6}"
    )
    assert any("stop_dead_surfaces" in v.detail for v in v6), (
        f"Expected violation for stop_dead_surfaces, got: {v6}"
    )

    src_rival = """
from ralph.pipeline._retry_progress_guard import (
    MAX_IDENTICAL_RETRY_ATTEMPTS,
    RetryProgressGuard,
    retry_failure_signature,
)

class CustomGuard(RetryProgressGuard):
    def check_progress(self, signature: str) -> bool:
        sig = retry_failure_signature("failed", ["output"])
        return sig == signature and self.max_identical >= MAX_IDENTICAL_RETRY_ATTEMPTS
"""
    v7 = audit_production_source("ralph/pipeline/worker.py", src_rival)
    assert any("RetryProgressGuard" in v.detail for v in v7), (
        f"Expected violation for RetryProgressGuard, got: {v7}"
    )
    assert any("retry_failure_signature" in v.detail for v in v7), (
        f"Expected violation for retry_failure_signature, got: {v7}"
    )
    assert any("MAX_IDENTICAL_RETRY_ATTEMPTS" in v.detail for v in v7), (
        f"Expected violation for MAX_IDENTICAL_RETRY_ATTEMPTS, got: {v7}"
    )

    src_rival_state = """
class AgentSession:
    def __init__(self):
        self.progress_guard = None
"""
    v8 = audit_production_source("ralph/pipeline/worker.py", src_rival_state)
    assert any("progress_guard" in v.detail for v in v8), (
        f"Expected violation for progress_guard, got: {v8}"
    )

    assert "ralph" in PRODUCTION_SCAN_SUBTREES
    src_agents_invoke_signature = """
def _retry_failure_signature(exc: Exception) -> tuple[str, str]:
    return type(exc).__name__, str(exc)
"""
    v9 = audit_production_source(
        "ralph/agents/invoke/_direct_mcp_recovery.py",
        src_agents_invoke_signature,
    )
    assert any("_retry_failure_signature" in v.detail for v in v9), (
        f"Expected violation for agents/invoke retry_failure_signature, got: {v9}"
    )

    src_local_retry_cap = """
def _retry_or_fall_over(state, chain):
    max_retries = 3
    if chain.retries < max_retries:
        new_chain = chain.with_retry_increment()
        return state.with_phase_chain(state.phase, new_chain), []
    return None
"""
    v10 = audit_production_source("ralph/pipeline/reducer.py", src_local_retry_cap)
    assert any(v.rule == "production_local_retry_cap_decision" for v in v10), (
        f"Expected production_local_retry_cap_decision violation, got: {v10}"
    )

    src_test_serialised = """
def test_snapshot(state):
    assert state.ledger_snapshot["entries"]["development:claude"]["is_spent"] is True
"""
    v11 = audit_test_source("tests/recovery/test_sample.py", src_test_serialised)
    assert any(v.rule == "test_serialised_ledger_access" for v in v11), (
        f"Expected test_serialised_ledger_access violation, got: {v11}"
    )

    src_repo_safety_bound = """
MAX_MERGE_REPAIR_ATTEMPTS = 3

def repair_pending_merge(record):
    if record.merge_commit_repair_attempts < MAX_MERGE_REPAIR_ATTEMPTS:
        return handoff_pending_merge_repair(record)
    return record
"""
    v12 = audit_production_source("ralph/pipeline/_pending_merge_repair.py", src_repo_safety_bound)
    assert not v12, f"Expected no violation for a repository-safety bound, got: {v12}"

    src_public_seam = """
def test_public_seam(ledger, fact):
    status = ledger.status("development", "claude")
    assert status.is_spent is False
    assert ledger.is_available("development", "claude")
    outcome = ledger.record(fact)
    assert outcome.demoted is False
    snap = ledger.snapshot()
    restored = AgentFailureLedger.from_snapshot(snap)
    assert restored.is_available("development", "claude")
"""
    v13 = audit_test_source("tests/recovery/test_sample.py", src_public_seam)
    assert not v13, f"Expected no violation for the public ledger seam, got: {v13}"

    src_private_backoff = """
def test_backoff(controller):
    assert controller._backoff_attempts.get("development:claude") == 2
"""
    v14 = audit_test_source("tests/recovery/test_sample.py", src_private_backoff)
    assert any(v.rule == "test_private_failure_owner_access" for v in v14), (
        f"Expected test_private_failure_owner_access for _backoff_attempts, got: {v14}"
    )

    src_private_tracker = """
def test_tracker(controller):
    assert controller._unavailability_tracker.is_available("development", "claude")
"""
    v15 = audit_test_source("tests/recovery/test_sample.py", src_private_tracker)
    assert any(v.rule == "test_private_failure_owner_access" for v in v15), (
        f"Expected test_private_failure_owner_access for _unavailability_tracker, got: {v15}"
    )

    src_private_marker = """
def test_marker(controller):
    cooldown_ms = controller._mark_agent_unavailable("development", "claude", reason)
"""
    v16 = audit_test_source("tests/recovery/test_sample.py", src_private_marker)
    assert any(v.rule == "test_private_failure_owner_access" for v in v16), (
        f"Expected test_private_failure_owner_access for _mark_agent_unavailable, got: {v16}"
    )

    src_private_getattr = """
def test_getattr(controller):
    attempts = getattr(controller, "_backoff_attempts")
"""
    v17 = audit_test_source("tests/recovery/test_sample.py", src_private_getattr)
    assert any(v.rule == "test_private_failure_owner_access" for v in v17), (
        f"Expected test_private_failure_owner_access for getattr(_backoff_attempts), got: {v17}"
    )

    _verify_controller_failure_owner_seam()

    _verify_classifier_provenance_shapes()


def _verify_controller_failure_owner_seam() -> None:
    src_controller_public_seam = """
def test_controller_public_seam(controller):
    controller.handle(event)
    controller.reset_backoff("development", "opencode")
    assert controller.preferred_agent_index("development", ["opencode"]) == 0
    assert controller.unavailability_store.is_available("development", "opencode")
    assert controller.ledger.status("development", "opencode").is_spent is False
"""
    v18 = audit_test_source("tests/recovery/test_sample.py", src_controller_public_seam)
    assert not v18, f"Expected no violation for the public controller seam, got: {v18}"

    src_public_snapshot_key = """
def test_controller_snapshot(controller):
    assert controller.snapshot()["cycle_cap"] == 5
"""
    v19 = audit_test_source("tests/recovery/test_sample.py", src_public_snapshot_key)
    assert not v19, f"Expected no violation for a public snapshot key, got: {v19}"

    src_serialised_backoff = """
def test_snapshot(controller):
    assert controller.snapshot()["backoff_attempts"] == {}
"""
    v20 = audit_test_source("tests/recovery/test_sample.py", src_serialised_backoff)
    assert any(v.rule == "test_serialised_failure_owner_access" for v in v20), (
        f"Expected test_serialised_failure_owner_access for snapshot['backoff_attempts'], got: {v20}"
    )

    src_serialised_backoff_get = """
def test_snapshot(controller):
    attempts = controller.snapshot().get("backoff_attempts")
"""
    v21 = audit_test_source("tests/recovery/test_sample.py", src_serialised_backoff_get)
    assert any(v.rule == "test_serialised_failure_owner_access" for v in v21), (
        f"Expected test_serialised_failure_owner_access for snapshot().get('backoff_attempts'), "
        f"got: {v21}"
    )

    src_serialised_timeouts = """
def test_snapshot(controller):
    timeouts = controller.snapshot()["unavailable_timeouts"]
"""
    v22 = audit_test_source("tests/recovery/test_sample.py", src_serialised_timeouts)
    assert any(v.rule == "test_serialised_failure_owner_access" for v in v22), (
        f"Expected test_serialised_failure_owner_access for snapshot['unavailable_timeouts'], "
        f"got: {v22}"
    )

    src_serialised_bare_snapshot = """
def test_snapshot():
    attempts = snapshot()["backoff_attempts"]
"""
    v23 = audit_test_source("tests/recovery/test_sample.py", src_serialised_bare_snapshot)
    assert any(v.rule == "test_serialised_failure_owner_access" for v in v23), (
        f"Expected test_serialised_failure_owner_access for snapshot()['backoff_attempts'], "
        f"got: {v23}"
    )


def _verify_classifier_provenance_shapes() -> None:
    src_wrapper = """
def handoff(controller, agent):
    bench_resolver(
        controller,
        agent,
        f"repair agent '{agent}' left no verifiable repair declaration",
        workspace_changed=False,
    )
"""
    v = audit_production_source("ralph/pipeline/_scratch_wrapper.py", src_wrapper)
    assert any(v.rule == "production_ralph_authored_classifier_input" for v in v), (
        f"Expected production_ralph_authored_classifier_input for bench_resolver prose, got: {v}"
    )

    src_reworded = """
def handoff(controller, agent):
    bench_resolver(controller, agent, "resolver produced nothing Ralph can verify")
"""
    v = audit_production_source("ralph/pipeline/_scratch_reworded.py", src_reworded)
    assert any(v.rule == "production_ralph_authored_classifier_input" for v in v), (
        f"Expected production_ralph_authored_classifier_input for reworded prose, got: {v}"
    )

    src_stored = """
from ralph.recovery.failure_classifier import FailureClassifier

class Runner:
    def __init__(self):
        self._classifier = FailureClassifier()

    def go(self) -> None:
        self._classifier.classify("candidate gave up without doing anything", phase="commit", agent="x")
"""
    v = audit_production_source("ralph/pipeline/_scratch_stored.py", src_stored)
    assert any(
        v.rule == "production_ralph_authored_classifier_input" and "stored" not in v.detail
        for v in v
    ), f"Expected production_ralph_authored_classifier_input for a stored instance, got: {v}"

    src_local = """
def handoff(controller, agent, failure_detail):
    detail = failure_detail() if failure_detail is not None else None
    text = detail or f"resolver '{agent}' ended without a verified completion proof"
    bench_resolver(controller, agent, text, workspace_changed=False)
"""
    v = audit_production_source("ralph/pipeline/_scratch_local.py", src_local)
    assert any(
        v.rule == "production_ralph_authored_classifier_input" and "'text'" in v.detail for v in v
    ), f"Expected production_ralph_authored_classifier_input for a local authored value, got: {v}"

    src_helper = """
def _failure_text(agent, failure_detail):
    detail = failure_detail() if failure_detail is not None else None
    return detail or f"resolver '{agent}' ended without a verified completion proof"

def handoff(controller, agent, failure_detail):
    bench_resolver(controller, agent, _failure_text(agent, failure_detail), workspace_changed=False)
"""
    v = audit_production_source("ralph/pipeline/_scratch_helper.py", src_helper)
    assert any(
        v.rule == "production_ralph_authored_classifier_input" and "_failure_text" in v.detail
        for v in v
    ), f"Expected production_ralph_authored_classifier_input for a prose helper, got: {v}"

    src_external = """
from ralph.recovery.failure_classifier import FailureClassifier

class Runner:
    def __init__(self):
        self._classifier = FailureClassifier()

    def go(self, exc: BaseException, controller, agent) -> None:
        bench_resolver(controller, agent, exc, workspace_changed=False)
        self._classifier.classify(exc, phase="commit", agent="x")
"""
    v = audit_production_source("ralph/pipeline/_scratch_external.py", src_external)
    assert not [x for x in v if x.rule == "production_ralph_authored_classifier_input"], (
        f"Expected no classifier-input violation for exception objects, got: {v}"
    )


_PARALLEL_MIN_FILES = 16

_POOL_WORKER_CAP = 8

_POOL_CONTEXT = multiprocessing.get_context("spawn")


def _audit_file_payload(payload: tuple[str, str, str]) -> list[SingleOwnerViolation]:
    kind, rel_path, source = payload
    if kind == "production":
        return audit_production_source(rel_path, source)
    return audit_test_source(rel_path, source)


def _collect_payloads(repo_root: Path) -> list[tuple[str, str, str]]:
    payloads: list[tuple[str, str, str]] = []
    scan_dirs = [("production", repo_root / sub) for sub in PRODUCTION_SCAN_SUBTREES]
    scan_dirs.append(("test", repo_root / "tests"))
    for kind, base_dir in scan_dirs:
        if not base_dir.is_dir():
            continue
        for parent, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                if not name.endswith(".py"):
                    continue
                file_path = Path(parent) / name
                rel_path = file_path.relative_to(repo_root).as_posix()
                source = file_path.read_text(encoding="utf-8")
                payloads.append((kind, rel_path, source))
    return payloads


def audit_single_owner(repo_root: Path) -> list[SingleOwnerViolation]:
    verify_counter_access_detection()
    violations: list[SingleOwnerViolation] = []
    payloads = _collect_payloads(repo_root)
    workers = min(_POOL_WORKER_CAP, len(payloads), os.cpu_count() or 1)
    if workers <= 1 or len(payloads) < _PARALLEL_MIN_FILES:
        for payload in payloads:
            violations.extend(_audit_file_payload(payload))
    else:
        with ProcessPoolExecutor(max_workers=workers, mp_context=_POOL_CONTEXT) as pool:
            for result in pool.map(_audit_file_payload, payloads, chunksize=32):
                violations.extend(result)
    return sorted(violations, key=_violation_sort_key)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    repo_root = Path(args[0]) if args else Path(__file__).resolve().parents[2]
    violations = audit_single_owner(repo_root)
    if violations:
        print(f"AGENT FAILURE SINGLE OWNER AUDIT: {len(violations)} violation(s) found:")
        for v in violations:
            print(f"  {v}")
        return 1
    print("AGENT FAILURE SINGLE OWNER AUDIT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
