"""Single-owner guard audit for agent failure and retry tracking.

This audit enforces the single-
owner architectural invariant: Exactly one decision owner
(:class:`ralph.recovery.agent_failure_ledger.AgentFailureLedger`) owns the state and decision
logic for whether an agent is failing, unproductive, or broken, and whether to retry or stop
using it. Recording call sites (such as the controller, reducer, and resolver_health) are
clients that feed attempt facts into the ledger rather than deciding agent failure
independently. Specifically, this audit validates: 1. Production code: No module under
``ralph/`` (except
``ralph/recovery/agent_failure_ledger.py``) declares streak/counter state or logic used for
agent stop/retry decisions (such as duplicate retry trackers, retry progress guards, or
in-session retry counters), imports a deleted tracker, or decides an agent's fate with a
LOCAL retry cap -- a retry counter compared against a locally declared retry cap that then
retries or advances an agent. Genuine repository-safety bounds (a merge-repair attempt cap,
for example) bound repository state rather than agent health and stay accepted. Since the
§11 fix (rev 2) the production scan covers ALL of ``ralph/`` and one more rule applies: no
module feeds Ralph-authored failure prose (a string literal, an f-string, or a module-level
Ralph-authored constant such as ``ATTEMPT_FAILED_EVIDENCE``) into the text classifier
(``classify_failed_resolution_attempt``, ``classify_conflict_attempt``,
``should_reset_tool_registry``, or ``FailureClassifier().classify(...)``), because authored
prose classifies as AMBIGUOUS and poisons the failure ledger's decisions. Exception objects
are RAW external runtime text and stay accepted. 2. Test code:
No test under ``tests/`` accesses private attributes, serialised ledger counters, or internal
counters of ``AgentFailureLedger`` (enforcing black-box testability via the public seam) or
imports deprecated retry tracker classes. A test also may not read the recovery controller's
private failure-owner state through its SERIALISED snapshot (for example
``controller.snapshot()["backoff_attempts"]``): the serialised key and the private attribute
are the same private state, so both coupling forms are rejected.

The production rules and the shared policy data live in the private rule engine
:mod:`ralph.testing._single_owner_rules`; this module owns the scan, the test-side rules,
the self-verification of every rule, and the command-line entry point.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

from ralph.testing._single_owner_rules import (
    _FORBIDDEN_TRACKER_MODULES,
    _FORBIDDEN_TRACKER_SYMBOLS,
    _GETATTR_MIN_ARGS,
    _PRIVATE_FAILURE_OWNER_SERIALISED_KEYS,
    _PRIVATE_LEDGER_COUNTERS,
    TEMPORARY_PRODUCTION_ALLOWLIST,
    TEMPORARY_TEST_ALLOWLIST,
    TEMPORARY_TRANSITIONAL_RULES,
    SingleOwnerViolation,
    _check_production_assignment,
    _check_production_attribute,
    _check_production_call,
    _check_production_class,
    _check_production_compare,
    _check_production_function,
    _check_production_import,
    _check_production_retry_cap_rule,
    _check_production_subscript,
    _check_ralph_authored_classifier_inputs,
    _is_forbidden_target_name,
    _is_ledger_receiver,
    _is_ledger_serialised_root,
    _is_private_failure_owner_name,
    _is_serialised_snapshot_call,
    _serialised_counter_violation,
    is_transitional_allowed,
    private_failure_owner_violation,
    serialised_failure_owner_violation,
)

__all__ = [
    "PRODUCTION_SCAN_SUBTREES",
    "TEMPORARY_PRODUCTION_ALLOWLIST",
    "TEMPORARY_TEST_ALLOWLIST",
    "TEMPORARY_TRANSITIONAL_RULES",
    "SingleOwnerViolation",
    "audit_production_source",
    "audit_test_source",
    "is_transitional_allowed",
    "main",
]

# The production scan covers ALL of ralph/ since the §11 fix (rev 2): the
# text-classifier provenance rule must see every entry into the classifier,
# and no production subtree may own agent failure decisions.
PRODUCTION_SCAN_SUBTREES: tuple[str, ...] = ("ralph",)


def audit_production_source(rel_path: str, source: str) -> list[SingleOwnerViolation]:
    """Audit a production module inside a configured single-owner scan subtree."""
    # The single failure ledger is the authoritative owner.
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
            violations.extend(_check_production_call(node, rel_path))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            violations.extend(_check_production_import(node, rel_path))
    violations.extend(_check_ralph_authored_classifier_inputs(tree, rel_path))
    return violations


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
    if attr in ("consecutive_unproductive", "total_unproductive"):
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
            if kw.arg in ("consecutive_unproductive", "total_unproductive"):
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
        if counter in ("consecutive_unproductive", "total_unproductive"):
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
    if arg_val in ("consecutive_unproductive", "total_unproductive"):
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
    """Flag direct access to ledger streak/counter snapshot fields in tests."""
    if (
        isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
        and node.slice.value
        in (
            "consecutive_unproductive",
            "total_unproductive",
        )
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
    """Audit a single test module under tests/."""
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
    """Deterministic sort key for single-owner audit violations."""
    return (violation.path, violation.line)


def verify_counter_access_detection() -> None:
    """Verify that AST audit detects direct counter coupling in test and production sources.

    Ensures an audit negative verification confirms that direct counter coupling
    and competing counter declarations are detected and enforced.
    """
    # Negative test 1: test asserting on outcome.consecutive_unproductive
    src_test_attr = """
def test_sample(ledger, fact):
    outcome = ledger.record(fact)
    assert outcome.consecutive_unproductive == 1
"""
    v1 = audit_test_source("tests/recovery/test_sample.py", src_test_attr)
    assert any(
        v.rule == "test_counter_access" and "consecutive_unproductive" in v.detail for v in v1
    ), f"Expected test_counter_access violation for consecutive_unproductive, got: {v1}"

    # Negative test 2: test asserting on outcome.total_unproductive
    src_test_total = """
def test_sample(ledger, fact):
    outcome = ledger.record(fact)
    assert outcome.total_unproductive == 1
"""
    v2 = audit_test_source("tests/recovery/test_sample.py", src_test_total)
    assert any(v.rule == "test_counter_access" and "total_unproductive" in v.detail for v in v2), (
        f"Expected test_counter_access violation for total_unproductive, got: {v2}"
    )

    # Negative test 3: non-ledger production code declaring counter state
    src_prod_decl = """
class Worker:
    def __init__(self):
        self.consecutive_unproductive = 0
"""
    v3 = audit_production_source("ralph/pipeline/worker.py", src_prod_decl)
    assert any("consecutive_unproductive" in v.detail for v in v3), (
        f"Expected violation for non-ledger counter declaration, got: {v3}"
    )

    # Negative test 4: non-ledger production code accessing ledger counter directly
    src_prod_attr = """
def check(ledger):
    return ledger.consecutive_unproductive > 0
"""
    v4 = audit_production_source("ralph/pipeline/worker.py", src_prod_attr)
    assert any("consecutive_unproductive" in v.detail for v in v4), (
        f"Expected violation for non-ledger counter access, got: {v4}"
    )

    # Negative test 5: test reading counter via .get()
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

    # Negative test 6: RC-1 fault hits increment, cap comparison, and dead surfaces exclusion
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

    # Negative test 7: Rival RetryProgressGuard, signature tracking, and attempt caps (PA-001 / permanent bans)
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

    # Negative test 8: Competing progress_guard state assignment (PA-001 / permanent bans)
    src_rival_state = """
class AgentSession:
    def __init__(self):
        self.progress_guard = None
"""
    v8 = audit_production_source("ralph/pipeline/worker.py", src_rival_state)
    assert any("progress_guard" in v.detail for v in v8), (
        f"Expected violation for progress_guard, got: {v8}"
    )

    # Negative test 9: the widened all-of-ralph scope must reject output-derived
    # failure signatures instead of letting them evade the ledger-owner guard.
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

    # Negative test 10: a local retry cap that decides an agent's fate.
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

    # Negative test 11: a test that indexes into the serialised ledger snapshot.
    src_test_serialised = """
def test_snapshot(state):
    assert state.ledger_snapshot["entries"]["development:claude"]["is_spent"] is True
"""
    v11 = audit_test_source("tests/recovery/test_sample.py", src_test_serialised)
    assert any(v.rule == "test_serialised_ledger_access" for v in v11), (
        f"Expected test_serialised_ledger_access violation, got: {v11}"
    )

    # Positive test 1: a repository-safety bound bounds repository state, not agent health.
    src_repo_safety_bound = """
MAX_MERGE_REPAIR_ATTEMPTS = 3

def repair_pending_merge(record):
    if record.merge_commit_repair_attempts < MAX_MERGE_REPAIR_ATTEMPTS:
        return handoff_pending_merge_repair(record)
    return record
"""
    v12 = audit_production_source("ralph/pipeline/_pending_merge_repair.py", src_repo_safety_bound)
    assert not v12, f"Expected no violation for a repository-safety bound, got: {v12}"

    # Positive test 2: the owner's public answers stay observable from a test.
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

    # Negative test 12: a test reading the controller's private backoff counter.
    src_private_backoff = """
def test_backoff(controller):
    assert controller._backoff_attempts.get("development:claude") == 2
"""
    v14 = audit_test_source("tests/recovery/test_sample.py", src_private_backoff)
    assert any(v.rule == "test_private_failure_owner_access" for v in v14), (
        f"Expected test_private_failure_owner_access for _backoff_attempts, got: {v14}"
    )

    # Negative test 13: a test reading the controller's private unavailability store.
    src_private_tracker = """
def test_tracker(controller):
    assert controller._unavailability_tracker.is_available("development", "claude")
"""
    v15 = audit_test_source("tests/recovery/test_sample.py", src_private_tracker)
    assert any(v.rule == "test_private_failure_owner_access" for v in v15), (
        f"Expected test_private_failure_owner_access for _unavailability_tracker, got: {v15}"
    )

    # Negative test 14: a test calling the private unavailable marker in an
    # assignment context (the rule must not be limited to asserts).
    src_private_marker = """
def test_marker(controller):
    cooldown_ms = controller._mark_agent_unavailable("development", "claude", reason)
"""
    v16 = audit_test_source("tests/recovery/test_sample.py", src_private_marker)
    assert any(v.rule == "test_private_failure_owner_access" for v in v16), (
        f"Expected test_private_failure_owner_access for _mark_agent_unavailable, got: {v16}"
    )

    # Negative test 15: the same private state reached through getattr().
    src_private_getattr = """
def test_getattr(controller):
    attempts = getattr(controller, "_backoff_attempts")
"""
    v17 = audit_test_source("tests/recovery/test_sample.py", src_private_getattr)
    assert any(v.rule == "test_private_failure_owner_access" for v in v17), (
        f"Expected test_private_failure_owner_access for getattr(_backoff_attempts), got: {v17}"
    )

    # The controller's failure-owner coupling rules and its public seam are
    # verified by the dedicated helper below.
    _verify_controller_failure_owner_seam()


def _verify_controller_failure_owner_seam() -> None:
    """Verify the controller's failure-owner coupling rules and its public seam.

    The recovery controller publishes its private failure-owner state inside its
    serialised snapshot under a public key name. A test that indexes one of
    those keys couples to the same private state that the direct-access rule
    already rejects, so the serialised form must be rejected too, while the
    controller's public seam and its non-failure-owner snapshot keys stay
    observable.
    """
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

    # Positive test 4: a snapshot key that is NOT private failure-owner state
    # stays legal.
    src_public_snapshot_key = """
def test_controller_snapshot(controller):
    assert controller.snapshot()["cycle_cap"] == 5
"""
    v19 = audit_test_source("tests/recovery/test_sample.py", src_public_snapshot_key)
    assert not v19, f"Expected no violation for a public snapshot key, got: {v19}"

    # Negative test 16: a test reading the controller's private backoff counter
    # through its SERIALISED snapshot.
    src_serialised_backoff = """
def test_snapshot(controller):
    assert controller.snapshot()["backoff_attempts"] == {}
"""
    v20 = audit_test_source("tests/recovery/test_sample.py", src_serialised_backoff)
    assert any(v.rule == "test_serialised_failure_owner_access" for v in v20), (
        f"Expected test_serialised_failure_owner_access for snapshot['backoff_attempts'], got: {v20}"
    )

    # Negative test 17: the same serialised state reached through .get().
    src_serialised_backoff_get = """
def test_snapshot(controller):
    attempts = controller.snapshot().get("backoff_attempts")
"""
    v21 = audit_test_source("tests/recovery/test_sample.py", src_serialised_backoff_get)
    assert any(v.rule == "test_serialised_failure_owner_access" for v in v21), (
        f"Expected test_serialised_failure_owner_access for snapshot().get('backoff_attempts'), "
        f"got: {v21}"
    )

    # Negative test 18: the serialised unavailability timeout history.
    src_serialised_timeouts = """
def test_snapshot(controller):
    timeouts = controller.snapshot()["unavailable_timeouts"]
"""
    v22 = audit_test_source("tests/recovery/test_sample.py", src_serialised_timeouts)
    assert any(v.rule == "test_serialised_failure_owner_access" for v in v22), (
        f"Expected test_serialised_failure_owner_access for snapshot['unavailable_timeouts'], "
        f"got: {v22}"
    )

    # Negative test 19: the bare snapshot() spelling reaches the same state.
    src_serialised_bare_snapshot = """
def test_snapshot():
    attempts = snapshot()["backoff_attempts"]
"""
    v23 = audit_test_source("tests/recovery/test_sample.py", src_serialised_bare_snapshot)
    assert any(v.rule == "test_serialised_failure_owner_access" for v in v23), (
        f"Expected test_serialised_failure_owner_access for snapshot()['backoff_attempts'], "
        f"got: {v23}"
    )


def audit_single_owner(repo_root: Path) -> list[SingleOwnerViolation]:
    """Scan production and test subtrees for single-owner failure ledger violations."""
    verify_counter_access_detection()
    violations: list[SingleOwnerViolation] = []

    # 1. Scan production subtrees that can own or influence agent failure decisions.
    for sub in PRODUCTION_SCAN_SUBTREES:
        base_dir = repo_root / sub
        # filesystem-read-ok: audit scanner checks directory existence before tree traversal
        if not base_dir.is_dir():
            continue
        # filesystem-read-ok: audit scanner walks directory tree for static AST inspection
        for parent, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                if not name.endswith(".py"):
                    continue
                file_path = Path(parent) / name
                rel_path = file_path.relative_to(repo_root).as_posix()
                # filesystem-read-ok: audit scanner loads module source for static AST inspection
                source = file_path.read_text(encoding="utf-8")
                violations.extend(audit_production_source(rel_path, source))

    # 2. Scan test subtree: tests
    tests_dir = repo_root / "tests"
    # filesystem-read-ok: audit scanner checks directory existence before tree traversal
    if tests_dir.is_dir():
        # filesystem-read-ok: audit scanner walks directory tree for static AST inspection
        for parent, dirs, files in os.walk(tests_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                if not name.endswith(".py"):
                    continue
                file_path = Path(parent) / name
                rel_path = file_path.relative_to(repo_root).as_posix()
                # filesystem-read-ok: audit scanner loads module source for static AST inspection
                source = file_path.read_text(encoding="utf-8")
                violations.extend(audit_test_source(rel_path, source))

    return sorted(violations, key=_violation_sort_key)


def main(argv: list[str] | None = None) -> int:
    """Run the single-owner failure ledger audit from the command line."""
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
