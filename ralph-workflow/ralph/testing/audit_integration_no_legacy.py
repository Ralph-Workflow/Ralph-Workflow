from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str

    def __str__(self) -> str:
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
