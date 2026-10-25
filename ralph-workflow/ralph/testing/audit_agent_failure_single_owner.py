"""Single-owner guard audit for agent failure and retry tracking.

This audit enforces the single-owner architectural invariant:
Exactly one component (:class:`ralph.recovery.agent_failure_ledger.AgentFailureLedger`)
owns the state and decision logic for whether an agent is failing, unproductive,
or broken, and whether to retry or stop using it.

Specifically, this audit validates:

1. Production code: No module under ``ralph/recovery/`` or ``ralph/pipeline/``
   (except ``ralph/recovery/agent_failure_ledger.py``) declares streak/counter
   state or logic used for agent stop/retry decisions (such as duplicate retry
   trackers or in-session retry counters).

2. Test code: No test under ``tests/`` accesses private attributes or internal
   counters of ``AgentFailureLedger`` (enforcing black-box testability via the
   public seam) or imports deprecated retry tracker classes.
"""

from __future__ import annotations

import ast
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SingleOwnerViolation:
    """A violation of the single-owner failure ledger policy."""

    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.detail}"


# Temporary production allowlist during single-owner consolidation.
# U4 removed the deleted trackers, controller, options, and run_loop.
# The remaining error modules and __init__ re-export remain until U5 removes the tests.
TEMPORARY_PRODUCTION_ALLOWLIST: frozenset[str] = frozenset()

TEMPORARY_TEST_ALLOWLIST: frozenset[str] = frozenset()

# Transitional allowlist for competing shapes (e.g. technical_retry_cap)
# until WU-4 deletes them.
TEMPORARY_TRANSITIONAL_RULES: dict[str, frozenset[str]] = {}


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
    }
)

_FORBIDDEN_TRACKER_SYMBOLS: frozenset[str] = frozenset(
    {
        "SameShapeRetryTracker",
        "BrokenAgentSameShapeTracker",
        "SameShapeRetryLoopError",
        "BrokenAgentSameShapeLimitError",
        "SameShapeRetryTrackerOptions",
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
    }
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
    )


def _check_production_class(node: ast.ClassDef, rel_path: str) -> list[SingleOwnerViolation]:
    if (
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
    node: ast.Assign | ast.AnnAssign, rel_path: str
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
    return [
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
            if (
                any(m in alias.name for m in _FORBIDDEN_TRACKER_MODULES)
                or alias.name in _FORBIDDEN_TRACKER_SYMBOLS
            )
        )
    elif isinstance(node, ast.ImportFrom):
        if node.module and any(m in node.module for m in _FORBIDDEN_TRACKER_MODULES):
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
            if (
                alias.name in _FORBIDDEN_TRACKER_SYMBOLS
                or any(m in alias.name for m in _FORBIDDEN_TRACKER_MODULES)
            )
        )
    return violations


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


def audit_production_source(rel_path: str, source: str) -> list[SingleOwnerViolation]:
    """Audit a single production module under ralph/recovery/ or ralph/pipeline/."""
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
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            violations.extend(_check_production_assignment(node, rel_path))
        elif isinstance(node, ast.Attribute):
            violations.extend(_check_production_attribute(node, rel_path))
        elif isinstance(node, ast.Subscript):
            violations.extend(_check_production_subscript(node, rel_path))
        elif isinstance(node, ast.Call):
            violations.extend(_check_production_call(node, rel_path))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            violations.extend(_check_production_import(node, rel_path))
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
            if (
                any(m in alias.name for m in _FORBIDDEN_TRACKER_MODULES)
                or alias.name in _FORBIDDEN_TRACKER_SYMBOLS
            )
        )
    elif isinstance(node, ast.ImportFrom):
        if node.module and any(m in node.module for m in _FORBIDDEN_TRACKER_MODULES):
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
            if alias.name in _FORBIDDEN_TRACKER_SYMBOLS
        )
    return violations


def _is_ledger_receiver(node: ast.AST) -> bool:
    """Check if AST expression represents an AgentFailureLedger receiver."""
    if isinstance(node, ast.Name):
        lower = node.id.lower()
        return "ledger" in lower or lower == "agentfailureledger"
    if isinstance(node, ast.Attribute):
        lower = node.attr.lower()
        return "ledger" in lower
    return False


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

    return violations


def _check_test_subscript(node: ast.Subscript, rel_path: str) -> list[SingleOwnerViolation]:
    """Flag direct access to ledger streak/counter snapshot fields in tests outside ledger unit tests."""
    if rel_path == "tests/recovery/test_agent_failure_ledger.py":
        return []
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


def audit_single_owner(repo_root: Path) -> list[SingleOwnerViolation]:
    """Scan production and test subtrees for single-owner failure ledger violations."""
    verify_counter_access_detection()
    violations: list[SingleOwnerViolation] = []

    # 1. Scan production subtrees: ralph/recovery and ralph/pipeline
    for sub in ("ralph/recovery", "ralph/pipeline"):
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
