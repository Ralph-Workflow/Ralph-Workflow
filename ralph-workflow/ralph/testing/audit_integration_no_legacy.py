from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_BOOL_STR_TUPLE_ARITY: Final = 2

_RESOLUTION_CALLS = frozenset(
    {
        "RebaseResolutionResult",
        "attempt_stop_resolution",
        "resolve_conflicted_rebase",
        "resolve_rebase_in_progress",
        "resolve_rebase_with_config",
        "_resolve_rebase_in_progress",
        "_resolve_rebase_with_config",
    }
)
_CAUSE_NAMES = frozenset({"cause", "failure_cause", "failure_reason", "reason", "terminal_reason"})

_LEGACY_CONFLICT_CLASSIFIER_NAMES: frozenset[str] = frozenset({"classify_conflict_attempt"})
_REMOVED_RESOLUTION_CAP_NAMES: frozenset[str] = frozenset({"total_resolution_cap_seconds"})
_LEGACY_GITLINK_ESCALATION_NAMES: frozenset[str] = frozenset({"escalate_gitlink_on_sight"})
_LEGACY_OUTCOME_CLASS_NAMES: frozenset[str] = frozenset({"ResolutionOutcome"})
LEGACY_OUTCOME_MODULE: str = "ralph/pipeline/conflict_resolution/resolution_outcome.py"
_SELF_AUDIT_MODULES: frozenset[str] = frozenset(
    {
        "ralph/testing/audit_integration_invariants.py",
        "ralph/testing/audit_integration_no_legacy.py",
    }
)
_RESOLUTION_OUTCOME_TOKENS: tuple[str, ...] = (
    "ResolutionOutcome",
    "StopResolutionOutcome",
    "ResolvedOutcome",
    "WaitingOutcome",
    "RetryLaterOutcome",
    "NextStrategyOutcome",
    "ConfigFailureOutcome",
    "UnknownOutcome",
    "RebaseResolutionResult",
    "classify_resolution_failure",
    "attempt_stop_resolution",
    "resolve_conflicted_rebase",
)
_OUTCOME_PRODUCER_NAME_TOKENS: tuple[str, ...] = (
    "outcome",
    "result",
    "resolution",
    "resolve",
    "classify",
)


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str
    detail: str = ""

    def __str__(self) -> str:
        if self.detail:
            return f"{self.path}:{self.line}: {self.rule}: {self.detail}"
        return f"{self.path}:{self.line}: {self.rule}"


def _resolution_call(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = (
        node.func.id
        if isinstance(node.func, ast.Name)
        else (node.func.attr if isinstance(node.func, ast.Attribute) else "")
    )
    return name in _RESOLUTION_CALLS


def _cause_expression(node: ast.AST) -> bool:
    for part in ast.walk(node):
        if not isinstance(part, ast.Call) or not isinstance(part.func, ast.Name):
            continue
        if part.func.id != "str" or not part.args:
            continue
        argument = part.args[0]
        if isinstance(argument, ast.Name) and argument.id.lower() in _CAUSE_NAMES:
            return True
        if isinstance(argument, ast.Attribute) and argument.attr.lower() in _CAUSE_NAMES:
            return True
    return False


def _string_cause_match(node: ast.Compare) -> bool:
    expressions: list[ast.AST] = [node.left, *node.comparators]
    return any(
        isinstance(part, ast.Constant) and isinstance(part.value, str)
        for expression in expressions
        for part in ast.walk(expression)
    ) and any(_cause_expression(expression) for expression in expressions)


def _tuple_subclass(node: ast.ClassDef) -> bool:
    return any(
        (isinstance(base, ast.Name) and base.id == "tuple")
        or (
            isinstance(base, ast.Subscript)
            and isinstance(base.value, ast.Name)
            and base.value.id == "tuple"
        )
        for base in node.bases
    )


def _node_identifier(node: ast.AST) -> tuple[str, int] | None:
    identifier: str | None = None
    line = 0
    if isinstance(node, ast.Name):
        identifier, line = node.id, node.lineno
    elif isinstance(node, ast.Attribute):
        identifier, line = node.attr, node.lineno
    elif isinstance(node, ast.arg):
        identifier, line = node.arg, node.lineno
    elif isinstance(node, ast.keyword):
        identifier, line = node.arg, node.lineno
    elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        identifier, line = node.name, node.lineno
    elif isinstance(node, ast.ClassDef):
        identifier, line = node.name, node.lineno
    if identifier is None:
        return None
    return identifier, line


def _identifier_occurrences(tree: ast.Module, names: frozenset[str]) -> list[tuple[int, str]]:
    found: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.alias):
            imported = node.name.rsplit(".", 1)[-1]
            if imported in names:
                found.add((node.lineno, imported))
            if node.asname is not None and node.asname in names:
                found.add((node.lineno, node.asname))
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in names:
                found.add((node.lineno, node.value))
            continue
        identifier = _node_identifier(node)
        if identifier is not None and identifier[0] in names:
            found.add((identifier[1], identifier[0]))
    return sorted(found)


def _forbidden_identifier_violations(
    path: str,
    tree: ast.Module,
    names: frozenset[str],
    rule: str,
    detail: str,
) -> list[Violation]:
    violations: list[Violation] = []
    reported: set[str] = set()
    for line, name in _identifier_occurrences(tree, names):
        if name in reported:
            continue
        reported.add(name)
        violations.append(Violation(path=path, line=line, rule=rule, detail=detail))
    return violations


def _bool_str_tuple_annotation(node: ast.AST) -> bool:
    if not isinstance(node, ast.Subscript):
        return False
    if not (isinstance(node.value, ast.Name) and node.value.id == "tuple"):
        return False
    slice_node = node.slice
    if not isinstance(slice_node, ast.Tuple) or len(slice_node.elts) != _BOOL_STR_TUPLE_ARITY:
        return False
    names = {element.id for element in slice_node.elts if isinstance(element, ast.Name)}
    return names == {"bool", "str"}


def _bool_str_tuple_literal(node: ast.Tuple) -> bool:
    if len(node.elts) != _BOOL_STR_TUPLE_ARITY:
        return False
    kinds: set[str] = set()
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, bool):
            kinds.add("bool")
        elif isinstance(element, ast.Constant) and isinstance(element.value, str):
            kinds.add("str")
        else:
            return False
    return kinds == {"bool", "str"}


def _is_outcome_producer(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    lowered = node.name.lower()
    return any(token in lowered for token in _OUTCOME_PRODUCER_NAME_TOKENS)


def _legacy_tuple_violations(path: str, tree: ast.Module) -> list[Violation]:
    violations: list[Violation] = [
        Violation(
            path=path,
            line=node.lineno,
            rule="resolution outcome typed as (bool, str) tuple",
            detail=(
                "a resolution result must be the sealed StopResolutionOutcome union, "
                "never a tuple[bool, str]"
            ),
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript) and _bool_str_tuple_annotation(node)
    ]
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not _is_outcome_producer(node):
            continue
        for statement in ast.walk(node):
            if not isinstance(statement, ast.Return) or statement.value is None:
                continue
            if not isinstance(statement.value, ast.Tuple):
                continue
            if not _bool_str_tuple_literal(statement.value):
                continue
            violations.append(
                Violation(
                    path=path,
                    line=statement.lineno,
                    rule="resolution outcome produced as (bool, str) tuple",
                    detail=(
                        f"'{node.name}' returns a (bool, str) tuple; a resolution result must be "
                        "the sealed StopResolutionOutcome union"
                    ),
                )
            )
    return violations


def _package_legacy_violations(rel: str, source: str) -> list[Violation]:
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError:
        return []
    violations = [
        *_forbidden_identifier_violations(
            rel,
            tree,
            _LEGACY_CONFLICT_CLASSIFIER_NAMES,
            "legacy conflict-attempt classifier reappeared",
            "'classify_conflict_attempt' was deleted; conflict attempts are classified by "
            "ralph/recovery/agent_failure_ledger.py and must not be re-added",
        ),
        *_forbidden_identifier_violations(
            rel,
            tree,
            _REMOVED_RESOLUTION_CAP_NAMES,
            "removed resolution cap identifier reappeared",
            "'total_resolution_cap_seconds' was removed from the resolver configuration; "
            "the only surviving cap is the watchdog reason OPERATOR_CAP_REACHED",
        ),
        *_forbidden_identifier_violations(
            rel,
            tree,
            _LEGACY_GITLINK_ESCALATION_NAMES,
            "legacy gitlink escalation hook reappeared",
            "'escalate_gitlink_on_sight' was removed; gitlink conflicts are resolved in place by "
            "ralph/pipeline/conflict_resolution/deterministic_resolution.py",
        ),
    ]
    if rel.startswith("ralph/pipeline/"):
        violations.extend(
            _forbidden_identifier_violations(
                rel,
                tree,
                _LEGACY_OUTCOME_CLASS_NAMES,
                "legacy resolution outcome vocabulary reappeared",
                "'ResolutionOutcome' was replaced by the sealed StopResolutionOutcome union in "
                "ralph/pipeline/resolution_outcome.py",
            )
        )
        if any(token in source for token in _RESOLUTION_OUTCOME_TOKENS):
            violations.extend(_legacy_tuple_violations(rel, tree))
    if rel == LEGACY_OUTCOME_MODULE:
        violations.append(
            Violation(
                path=rel,
                line=1,
                rule="legacy resolution outcome module reappeared",
                detail=(
                    f"{LEGACY_OUTCOME_MODULE} must stay deleted; the canonical sealed union lives "
                    "in ralph/pipeline/resolution_outcome.py"
                ),
            )
        )
    return violations


def _package_modules(repo_root: Path) -> list[tuple[str, str]]:
    base = repo_root / "ralph"
    modules: list[tuple[str, str]] = []
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(repo_root).as_posix()
        if rel in _SELF_AUDIT_MODULES:
            continue
        modules.append((rel, path.read_text(encoding="utf-8")))
    return modules


def audit_tree(repo_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for path in sorted((repo_root / "ralph" / "pipeline").rglob("*.py")):
        rel = path.relative_to(repo_root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and _resolution_call(node.value):
                violations.extend(
                    Violation(rel, node.lineno, "tuple-shaped resolution result unpack")
                    for target in node.targets
                    if isinstance(target, ast.Tuple)
                )
            if (
                isinstance(node, ast.ClassDef)
                and node.name == "RebaseResolutionResult"
                and _tuple_subclass(node)
            ):
                violations.append(
                    Violation(rel, node.lineno, "resolution result retains legacy tuple vocabulary")
                )
            if isinstance(node, ast.Compare) and _string_cause_match(node):
                violations.append(Violation(rel, node.lineno, "string matching on failure cause"))
    for rel, source in _package_modules(repo_root):
        violations.extend(_package_legacy_violations(rel, source))
    return violations


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path(__file__).resolve().parents[2]
    violations = audit_tree(root)
    if violations:
        print(f"INTEGRATION NO-LEGACY AUDIT: {len(violations)} violation(s) found:")
        for violation in violations:
            print(f"  {violation}")
        return 1
    print("INTEGRATION NO-LEGACY AUDIT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
