from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

_LEGACY_UNPACK_SIZE = 2


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
    name = node.func.id if isinstance(node.func, ast.Name) else (
        node.func.attr if isinstance(node.func, ast.Attribute) else ""
    )
    return name in {
        "resolve_rebase_with_config",
        "_resolve_rebase_with_config",
        "resolve_rebase_in_progress",
        "_resolve_rebase_in_progress",
    }


def _textifies_cause(node: ast.Compare) -> bool:
    for part in ast.walk(node):
        if not isinstance(part, ast.Call) or not isinstance(part.func, ast.Name):
            continue
        if part.func.id != "str" or not part.args:
            continue
        arg = part.args[0]
        if isinstance(arg, ast.Name) and arg.id.lower() in {"cause", "failure_cause", "reason"}:
            return True
        if isinstance(arg, ast.Attribute) and arg.attr.lower() in {
            "cause",
            "failure_cause",
            "reason",
        }:
            return True
    return False


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
                    Violation(rel, node.lineno, "two-value resolution unpack")
                    for target in node.targets
                    if isinstance(target, ast.Tuple)
                    and len(target.elts) == _LEGACY_UNPACK_SIZE
                )
            if (
                isinstance(node, ast.ClassDef)
                and node.name == "ExhaustedResolutionFailure"
                and any(isinstance(base, ast.Name) and base.id == "str" for base in node.bases)
            ):
                violations.append(
                    Violation(rel, node.lineno, "terminal reason smuggled in str subclass")
                )
            if isinstance(node, ast.Compare) and any(
                isinstance(op, (ast.In, ast.NotIn)) for op in node.ops
            ):
                has_text = any(
                    isinstance(part, ast.Constant) and isinstance(part.value, str)
                    for part in ast.walk(node)
                )
                if has_text and _textifies_cause(node):
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
