from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _PACKAGE_ROOT.parent

LEDGER_MODULE = "recovery/agent_failure_ledger.py"
TRACKER_MODULE = "recovery/agent_unavailability_tracker.py"
WAIT_KIND_MODULE = "recovery/wait_kind.py"
SELECTION_MODULE = "recovery/agent_selection.py"

STATUS_BASE = "_AgentStatus"
ALLOWED_STATUS_CLASSES: frozenset[str] = frozenset({"_Eligible", "_Cooldown"})
EXPIRY_FIELD = "until_ms"
ALLOWED_WAIT_KINDS: frozenset[str] = frozenset({"AVAILABLE_NOW", "COOLING_DOWN", "NO_CANDIDATES"})
ALLOWED_ELIGIBILITY_ALIAS_NAMES: frozenset[str] = frozenset({"Eligible", "Cooldown", "CoolingDown"})
ALLOWED_MARK_UNAVAILABLE_MODULES: frozenset[str] = frozenset(
    {"recovery/controller.py", LEDGER_MODULE, TRACKER_MODULE}
)

FORBIDDEN_IDENTIFIER = re.compile(
    r"(?i)(^|_)(spent|demot\w*|bench(ed|es)?|benched)(_|$)"
    r"|spent_agents|retry_exhaust|all_exhausted|requires_intervention|never_eligible"
    r"|skipped_candidates|is_unavailable_state"
)
FORBIDDEN_IDENTIFIER_SCOPES: tuple[str, ...] = ("recovery/", "pipeline/")
FORBIDDEN_IDENTIFIER_EXEMPT: frozenset[str] = frozenset(
    {
        "rounds_spent",
        "stops_spent",
        "RESOLVER_NOT_SPENT_TERMINATION_REASONS",
        "_warn_chain_not_spent",
        "_determine_not_spent_reason",
        "_not_spent_resolution_outcome",
        "_handle_spent_session",
        "record_requires_intervention",
        "_verdict_benches_agent",
        "verdict_benches_agent",
        "benches_agent",
        "bench_applied",
        "bench_reason",
        "note_bench",
    }
)

FORBIDDEN_IDENTIFIER_EXEMPT_PARTS: tuple[str, ...] = ("budget", "false_bench")

GUARD_TESTS: dict[str, tuple[str, ...]] = {
    "tests/recovery/test_cooldown_only_invariant.py": (
        "test_every_failure_sequence_leaves_the_agent_cooling_down_and_then_selectable",
        "test_a_sole_agent_past_its_unproductive_limit_waits_for_its_cooldown_instead_of_failing",
    ),
    "tests/recovery/test_unproductive_limit_cooldown_regression.py": (
        "test_an_agent_past_the_total_unproductive_limit_is_retried_after_its_own_cooldown",
        "test_the_unproductive_limit_cooldown_names_its_reason_and_is_shorter_than_out_of_credits",
    ),
    "tests/test_audit_cooldown_only.py": (
        "test_the_repository_passes_the_cooldown_only_audit",
        "test_a_status_class_without_an_expiry_fails_the_audit",
    ),
}
_DISABLING_MARKERS: frozenset[str] = frozenset({"skip", "skipif", "xfail"})


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.detail}"


def _parse(source: str, rel_path: str) -> ast.Module | Violation:
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        return Violation(rel_path, exc.lineno or 0, f"cannot parse: {exc.msg}")


def _base_names(node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def _annotated_fields(node: ast.ClassDef) -> dict[str, str]:
    return {
        stmt.target.id: ast.unparse(stmt.annotation)
        for stmt in node.body
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
    }


def status_class_violations(tree: ast.Module, rel_path: str) -> list[Violation]:
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not ({STATUS_BASE, "AgentStatus"} & _base_names(node)):
            continue
        if node.name not in ALLOWED_STATUS_CLASSES:
            violations.append(
                Violation(
                    rel_path,
                    node.lineno,
                    f"agent status class {node.name} is a third availability state; an agent "
                    "is either eligible or on an expiring Cooldown",
                )
            )
            continue
        fields = _annotated_fields(node)
        if node.name == "_Cooldown" and fields.get(EXPIRY_FIELD) != "int":
            violations.append(
                Violation(
                    rel_path, node.lineno, f"_Cooldown must carry a required `{EXPIRY_FIELD}: int`"
                )
            )
        if node.name == "_Eligible" and fields:
            violations.append(Violation(rel_path, node.lineno, "_Eligible must carry no state"))
    return violations


def eligibility_alias_violations(tree: ast.Module, rel_path: str) -> list[Violation]:
    for node in tree.body:
        if isinstance(node, ast.TypeAlias) and node.name.id == "AgentEligibility":
            names = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
            extra = names - ALLOWED_ELIGIBILITY_ALIAS_NAMES
            if extra:
                return [
                    Violation(
                        rel_path,
                        node.lineno,
                        f"AgentEligibility admits non-cooldown states: {sorted(extra)}",
                    )
                ]
            return []
    return [Violation(rel_path, 1, "AgentEligibility type alias is missing")]


def wait_kind_violations(tree: ast.Module, rel_path: str) -> list[Violation]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "WaitKind":
            members = {
                target.id
                for stmt in node.body
                if isinstance(stmt, ast.Assign)
                for target in stmt.targets
                if isinstance(target, ast.Name)
            }
            extra = members - ALLOWED_WAIT_KINDS
            if extra:
                return [
                    Violation(
                        rel_path,
                        node.lineno,
                        f"WaitKind admits a wait with no cooldown expiry: {sorted(extra)}",
                    )
                ]
            return []
    return [Violation(rel_path, 1, "WaitKind enum is missing")]


def tracker_entry_violations(tree: ast.Module, rel_path: str) -> list[Violation]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "UnavailabilityEntry":
            if _annotated_fields(node).get("unavailable_until_ms") != "int":
                return [
                    Violation(
                        rel_path,
                        node.lineno,
                        "UnavailabilityEntry.unavailable_until_ms must be a required int expiry",
                    )
                ]
            return []
    return [Violation(rel_path, 1, "UnavailabilityEntry is missing")]


def selection_row_violations(tree: ast.Module, rel_path: str) -> list[Violation]:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "agent_availability":
            params = [arg.arg for arg in node.args.args]
            expected = ["agent", "available", "cooldown_ms_remaining", "cooldown_reason"]
            if params != expected:
                return [
                    Violation(
                        rel_path,
                        node.lineno,
                        f"selection rows may only carry availability and cooldown, got {params}",
                    )
                ]
            return []
    return [Violation(rel_path, 1, "agent_availability is missing")]


def _identifiers(tree: ast.Module) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.append((node.lineno, node.name))
        elif isinstance(node, ast.Name):
            found.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute):
            found.append((node.lineno, node.attr))
        elif isinstance(node, ast.arg):
            found.append((node.lineno, node.arg))
        elif isinstance(node, ast.keyword) and node.arg is not None:
            found.append((node.value.lineno, node.arg))
    return found


def forbidden_identifier_violations(tree: ast.Module, rel_path: str) -> list[Violation]:
    if not rel_path.startswith(FORBIDDEN_IDENTIFIER_SCOPES):
        return []
    return [
        Violation(
            rel_path,
            line,
            f"identifier {name!r} names an agent exclusion state outside the cooldown system",
        )
        for line, name in _identifiers(tree)
        if name not in FORBIDDEN_IDENTIFIER_EXEMPT
        and not any(part in name.lower() for part in FORBIDDEN_IDENTIFIER_EXEMPT_PARTS)
        and FORBIDDEN_IDENTIFIER.search(name)
    ]


def mark_unavailable_violations(tree: ast.Module, rel_path: str) -> list[Violation]:
    if rel_path in ALLOWED_MARK_UNAVAILABLE_MODULES:
        return []
    return [
        Violation(
            rel_path,
            node.lineno,
            "only the recovery controller and the failure ledger may write the cooldown tracker",
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "mark_unavailable"
    ]


def _module_checks(tree: ast.Module, rel_path: str, source: str) -> list[Violation]:
    violations: list[Violation] = []
    if "AgentStatus" in source:
        violations.extend(status_class_violations(tree, rel_path))
    if _SCOPED_TRIGGER.search(source) is not None:
        violations.extend(forbidden_identifier_violations(tree, rel_path))
    if "mark_unavailable" in source:
        violations.extend(mark_unavailable_violations(tree, rel_path))
    if rel_path == LEDGER_MODULE:
        violations.extend(eligibility_alias_violations(tree, rel_path))
    if rel_path == WAIT_KIND_MODULE:
        violations.extend(wait_kind_violations(tree, rel_path))
    if rel_path == TRACKER_MODULE:
        violations.extend(tracker_entry_violations(tree, rel_path))
    if rel_path == SELECTION_MODULE:
        violations.extend(selection_row_violations(tree, rel_path))
    return violations


_SPECIAL_MODULES: frozenset[str] = frozenset(
    {LEDGER_MODULE, TRACKER_MODULE, WAIT_KIND_MODULE, SELECTION_MODULE}
)
_SCOPED_TRIGGER = re.compile(
    r"(?i)spent|demot|bench|exhaust|intervention|never_eligible|skipped_candidates"
    r"|is_unavailable_state"
)


def _needs_parse(source: str, rel_path: str) -> bool:
    if rel_path in _SPECIAL_MODULES or "AgentStatus" in source or "mark_unavailable" in source:
        return True
    return rel_path.startswith(FORBIDDEN_IDENTIFIER_SCOPES) and (
        _SCOPED_TRIGGER.search(source) is not None
    )


def source_violations(source: str, rel_path: str) -> list[Violation]:
    if not _needs_parse(source, rel_path):
        return []
    parsed = _parse(source, rel_path)
    if isinstance(parsed, Violation):
        return [parsed]
    return _module_checks(parsed, rel_path, source)


def _decorator_disables(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return isinstance(target, ast.Attribute) and target.attr in _DISABLING_MARKERS


def guard_test_violations(
    source: str | None, rel_path: str, names: tuple[str, ...]
) -> list[Violation]:
    if source is None:
        return [Violation(rel_path, 0, "cooldown-only guard test module was deleted")]
    parsed = _parse(source, rel_path)
    if isinstance(parsed, Violation):
        return [parsed]
    functions = {node.name: node for node in parsed.body if isinstance(node, ast.FunctionDef)}
    module_disabled = any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets)
        and any(
            isinstance(sub, ast.Attribute) and sub.attr in _DISABLING_MARKERS
            for sub in ast.walk(node.value)
        )
        for node in parsed.body
    )
    violations: list[Violation] = []
    if module_disabled:
        violations.append(Violation(rel_path, 0, "cooldown-only guard module is skip-marked"))
    for name in names:
        function = functions.get(name)
        if function is None:
            violations.append(
                Violation(rel_path, 0, f"cooldown-only guard test {name} was removed")
            )
        elif any(_decorator_disables(decorator) for decorator in function.decorator_list):
            violations.append(
                Violation(rel_path, function.lineno, f"cooldown-only guard test {name} is disabled")
            )
    return violations


def collect_violations(
    package_root: Path = _PACKAGE_ROOT,
    project_root: Path = _PROJECT_ROOT,
    *,
    only: tuple[str, ...] = (),
) -> list[Violation]:
    violations: list[Violation] = []
    for path in sorted(package_root.rglob("*.py")):
        rel_path = path.relative_to(package_root).as_posix()
        if rel_path.startswith("testing/") or (only and not rel_path.startswith(only)):
            continue
        violations.extend(source_violations(path.read_text(encoding="utf-8"), rel_path))
    for rel_path, names in GUARD_TESTS.items():
        guard_path = project_root / rel_path
        source = guard_path.read_text(encoding="utf-8") if guard_path.is_file() else None
        violations.extend(guard_test_violations(source, rel_path, names))
    return violations


def main(argv: list[str] | None = None) -> int:
    del argv
    violations = collect_violations()
    if violations:
        sys.stdout.write(f"COOLDOWN-ONLY AUDIT FAILED: {len(violations)} violation(s)\n")
        sys.stdout.write("=" * 72 + "\n")
        for violation in violations:
            sys.stdout.write(f"  {violation}\n")
        sys.stdout.write(
            "An agent is either on an expiring cooldown or available; no other exclusion "
            "state may exist.\n"
        )
        return 1
    sys.stdout.write("Cooldown-only audit OK: every agent exclusion is an expiring cooldown.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
