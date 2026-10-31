"""Tests for the single-owner audit's text-classifier provenance rule.

The §11 fix (rev 2) forbids Ralph-authored failure prose from reaching the text
classifier; these tests pin that rule through the public
``audit_production_source`` seam of ``audit_agent_failure_single_owner``.
"""

from __future__ import annotations

from ralph.testing.audit_agent_failure_single_owner import audit_production_source

# --- §11 fix (rev 2): no Ralph-authored prose may reach the text classifier. ---


def test_production_ralph_authored_prose_assignment_then_classify_is_flagged() -> None:
    """The exact regression shape: prose assigned to the session field, then classified."""
    source = """
def handle(session, agent_name):
    session.last_attempt_failure = "unresolved paths remain in worktree"
    classify_failed_resolution_attempt(
        session, agent_name, session.last_attempt_failure, candidates=(), failed_index=0,
    )
"""
    violations = audit_production_source("ralph/pipeline/conflict_resolution/worker.py", source)
    assert any(v.rule == "production_ralph_authored_classifier_input" for v in violations), (
        f"Expected production_ralph_authored_classifier_input, got: {violations}"
    )
    assign_violations = [
        v for v in violations if v.rule == "production_ralph_authored_classifier_input"
    ]
    assert any("last_attempt_failure" in v.detail for v in assign_violations)
    assert any("classifier entry" in v.detail.lower() for v in assign_violations)


def test_production_reworded_prose_variant_is_flagged() -> None:
    """Rewording the authored string does not evade the rule."""
    source = """
def handle(session, agent_name):
    session.last_attempt_evidence = f"candidate gave up without doing anything"
    classify_failed_resolution_attempt(
        session, agent_name, session.last_attempt_evidence, candidates=(), failed_index=0,
    )
"""
    violations = audit_production_source("ralph/pipeline/conflict_resolution/worker.py", source)
    assert any(v.rule == "production_ralph_authored_classifier_input" for v in violations), (
        f"Expected production_ralph_authored_classifier_input, got: {violations}"
    )


def test_production_direct_classifier_construction_with_prose_is_flagged() -> None:
    """A direct FailureClassifier().classify(<internal prose>) call is flagged."""
    source = """
from ralph.recovery.failure_classifier import FailureClassifier

def go() -> None:
    FailureClassifier().classify("candidate produced no output", phase="commit", agent="x")
"""
    violations = audit_production_source("ralph/pipeline/plumbing/worker.py", source)
    assert any(v.rule == "production_ralph_authored_classifier_input" for v in violations), (
        f"Expected production_ralph_authored_classifier_input, got: {violations}"
    )


def test_production_classifier_wrapper_with_prose_is_flagged() -> None:
    """The should_reset_tool_registry wrapper fed internal prose is flagged."""
    source = """
from ralph.recovery.failure_classifier import should_reset_tool_registry

def go() -> bool:
    return should_reset_tool_registry("tool registry is stale", phase="commit", agent="x")
"""
    violations = audit_production_source("ralph/pipeline/plumbing/worker.py", source)
    assert any(v.rule == "production_ralph_authored_classifier_input" for v in violations), (
        f"Expected production_ralph_authored_classifier_input, got: {violations}"
    )


def test_production_exception_object_failure_arg_is_not_flagged() -> None:
    """Exception objects are RAW external runtime text and stay accepted."""
    source = """
from ralph.recovery.failure_classifier import should_reset_tool_registry

def go(exc: BaseException) -> bool:
    return should_reset_tool_registry(exc, phase="commit", agent="x")
"""
    violations = audit_production_source("ralph/pipeline/plumbing/worker.py", source)
    assert not [v for v in violations if v.rule == "production_ralph_authored_classifier_input"], (
        f"Expected no classifier-input violation for an exception object, got: {violations}"
    )


def test_production_or_chain_with_authored_constant_is_flagged() -> None:
    """An or-fallback chain carrying ATTEMPT_FAILED_EVIDENCE is flagged."""
    source = """
ATTEMPT_FAILED_EVIDENCE = "conflict attempt failed"

def handle(session, agent_name):
    classify_failed_resolution_attempt(
        session,
        agent_name,
        session.last_attempt_failure or session.last_attempt_evidence or ATTEMPT_FAILED_EVIDENCE,
        candidates=(),
        failed_index=0,
    )
"""
    violations = audit_production_source("ralph/pipeline/conflict_resolution/worker.py", source)
    assert any(v.rule == "production_ralph_authored_classifier_input" for v in violations), (
        f"Expected production_ralph_authored_classifier_input, got: {violations}"
    )


def test_production_classifier_provenance_allowlist_is_removed() -> None:
    """The U3 §11 conversion removed the temporary classifier-input allowlist.

    Every formerly allowlisted shape — prose assignments to the session
    failure fields, a classify call reading a tainted field, an or-chain
    carrying ATTEMPT_FAILED_EVIDENCE — must flag again now that production
    routes typed outcomes instead. This is the completion guard, asserted
    purely through the public ``audit_production_source`` seam: a
    reintroduced allowlist covering any of these shapes would silence the
    matching flag below and turn this test red.
    """
    driver_source = """
def handle(session, agent_name, attempt):
    session.last_attempt_failure = "unresolved paths remain in worktree"
    session.last_attempt_evidence = "unresolved paths remain in worktree"
    classify_failed_resolution_attempt(
        session, agent_name, session.last_attempt_failure, candidates=(), failed_index=0,
    )
"""
    violations = audit_production_source(
        "ralph/pipeline/conflict_resolution/driver.py", driver_source
    )
    assert [v for v in violations if v.rule == "production_ralph_authored_classifier_input"], (
        f"Expected flagged driver.py sites once the allowlist is removed, got: {violations}"
    )

    rounds_source = """
def handle(session, agent_name):
    _driver_mod.classify_failed_resolution_attempt(
        session,
        agent_name,
        session.last_attempt_failure or session.last_attempt_evidence or ATTEMPT_FAILED_EVIDENCE,
        candidates=(),
        failed_index=0,
    )
"""
    violations = audit_production_source(
        "ralph/pipeline/conflict_resolution/_driver_rounds.py", rounds_source
    )
    assert [v for v in violations if v.rule == "production_ralph_authored_classifier_input"], (
        f"Expected flagged _driver_rounds.py site once the allowlist is removed, got: {violations}"
    )

    session_source = """
def note(session):
    if not session.last_attempt_evidence:
        session.last_attempt_evidence = "candidate produced no activity before it exited"
"""
    violations = audit_production_source(
        "ralph/pipeline/conflict_resolution/session.py", session_source
    )
    assert [v for v in violations if v.rule == "production_ralph_authored_classifier_input"], (
        f"Expected flagged session.py site once the allowlist is removed, got: {violations}"
    )


# --- §11 fix (rev 2), WU-2: the guard covers the wrapper and laundering shapes. ---


def test_production_bench_resolver_with_authored_fstring_is_flagged() -> None:
    """The pending-merge repair wrapper flow: authored f-string through bench_resolver."""
    source = """
def handoff(controller, agent):
    bench_resolver(
        controller,
        agent,
        f"repair agent '{agent}' left no verifiable repair declaration for the pending commit",
        workspace_changed=False,
    )
"""
    violations = audit_production_source("ralph/pipeline/_pending_merge_repair.py", source)
    assert any(
        v.rule == "production_ralph_authored_classifier_input" and "bench_resolver" in v.detail
        for v in violations
    ), f"Expected bench_resolver prose violation, got: {violations}"


def test_production_bench_resolver_with_reworded_literal_is_flagged() -> None:
    """Rewording the authored wrapper string does not evade the rule."""
    source = """
def handoff(controller, agent):
    bench_resolver(
        controller,
        agent,
        "resolver produced nothing Ralph can verify",
        workspace_changed=False,
    )
"""
    violations = audit_production_source("ralph/pipeline/_pending_merge_repair.py", source)
    assert any(v.rule == "production_ralph_authored_classifier_input" for v in violations), (
        f"Expected reworded wrapper prose violation, got: {violations}"
    )


def test_production_stored_classifier_instance_with_prose_is_flagged() -> None:
    """A FailureClassifier stored on self and fed internal prose is flagged."""
    source = """
from ralph.recovery.failure_classifier import FailureClassifier

class Runner:
    def __init__(self):
        self._classifier = FailureClassifier()

    def go(self) -> None:
        self._classifier.classify("candidate gave up without doing anything", phase="commit", agent="x")
"""
    violations = audit_production_source("ralph/pipeline/plumbing/worker.py", source)
    assert any(
        v.rule == "production_ralph_authored_classifier_input"
        and "FailureClassifier.classify" in v.detail
        for v in violations
    ), f"Expected stored-instance prose violation, got: {violations}"


def test_production_local_assigned_authored_value_is_flagged() -> None:
    """The or-chain laundering shape: a local assigned authored prose, then classified."""
    source = """
def handoff(controller, agent, failure_detail):
    detail = failure_detail() if failure_detail is not None else None
    text = detail or f"resolver '{agent}' ended without a verified completion proof"
    bench_resolver(controller, agent, text, workspace_changed=False)
"""
    violations = audit_production_source(
        "ralph/pipeline/_integration_diagnostic_handoff.py", source
    )
    assert any(
        v.rule == "production_ralph_authored_classifier_input" and "'text'" in v.detail
        for v in violations
    ), f"Expected local-authored-value violation, got: {violations}"


def test_production_prose_returning_helper_result_is_flagged() -> None:
    """The diagnostic-handoff wrapper flow: a same-module helper returning authored prose."""
    source = """
def _failure_text(agent, failure_detail):
    detail = failure_detail() if failure_detail is not None else None
    return detail or f"resolver '{agent}' ended without a verified completion proof"

def handoff(controller, agent, failure_detail):
    bench_resolver(controller, agent, _failure_text(agent, failure_detail), workspace_changed=False)
"""
    violations = audit_production_source(
        "ralph/pipeline/_integration_diagnostic_handoff.py", source
    )
    assert any(
        v.rule == "production_ralph_authored_classifier_input" and "_failure_text" in v.detail
        for v in violations
    ), f"Expected prose-returning-helper violation, got: {violations}"


def test_production_external_exception_through_wrapper_and_instance_is_not_flagged() -> None:
    """Exception objects are RAW external runtime text and stay accepted."""
    source = """
from ralph.recovery.failure_classifier import FailureClassifier

class Runner:
    def __init__(self):
        self._classifier = FailureClassifier()

    def go(self, exc: BaseException, controller, agent) -> None:
        bench_resolver(controller, agent, exc, workspace_changed=False)
        self._classifier.classify(exc, phase="commit", agent="x")
"""
    violations = audit_production_source("ralph/pipeline/plumbing/worker.py", source)
    assert not [v for v in violations if v.rule == "production_ralph_authored_classifier_input"], (
        f"Expected no classifier-input violation for exception objects, got: {violations}"
    )


def test_production_typed_bench_resolver_outcome_is_not_flagged() -> None:
    """The converted wrapper flows: typed outcome with no authored prose stays accepted."""
    source = """
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome

def handoff(controller, agent, workspace_changed):
    bench_resolver(
        controller,
        agent,
        None,
        workspace_changed=workspace_changed,
        outcome=ConflictAttemptOutcome.ATTEMPT_FAILED,
    )
"""
    violations = audit_production_source("ralph/pipeline/_pending_merge_repair.py", source)
    assert not violations, (
        f"Expected no violation for a typed bench_resolver outcome, got: {violations}"
    )
