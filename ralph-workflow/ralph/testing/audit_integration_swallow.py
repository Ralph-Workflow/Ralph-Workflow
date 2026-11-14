from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}"


def _is_broad(handler: ast.ExceptHandler) -> bool:
    caught = handler.type
    return caught is None or (
        isinstance(caught, ast.Name) and caught.id in {"Exception", "BaseException"}
    )


def _normal_return(node: ast.Return) -> bool:
    value = node.value
    empty_sequence = isinstance(value, (ast.Tuple, ast.List, ast.Set)) and not value.elts
    empty_mapping = isinstance(value, ast.Dict) and not value.keys
    default_constant = isinstance(value, ast.Constant) and value.value in {None, False, ""}
    return value is None or empty_sequence or empty_mapping or default_constant


def _handler_returns(handler: ast.ExceptHandler) -> list[ast.Return]:
    return [node for node in ast.walk(handler) if isinstance(node, ast.Return)]


def _has_loud_boundary(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return True
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
        if name in {"critical", "error", "exception", "warning"}:
            return True
        called = node.func.id if isinstance(node.func, ast.Name) else name
        if any(word in called.lower() for word in ("error", "failure", "unknown")):
            return True
    return False


def audit_tree(repo_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    base = repo_root / "ralph" / "pipeline"
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(repo_root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError:
            continue
        integration_functions = (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and any(word in node.name for word in ("resolve", "resolution", "landing"))
            and node.name not in {"_resolve_agent_config", "resolve_failure_ledger"}
        )
        for function in integration_functions:
            for handler in (
                node for node in ast.walk(function) if isinstance(node, ast.ExceptHandler)
            ):
                if not _is_broad(handler) or _has_loud_boundary(handler):
                    continue
                violations.extend(
                    Violation(rel, node.lineno, "broad handler returns an untyped normal value")
                    for node in _handler_returns(handler)
                    if _normal_return(node)
                )
    return violations


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path(__file__).resolve().parents[2]
    violations = audit_tree(root)
    if violations:
        print(f"INTEGRATION SWALLOW AUDIT: {len(violations)} violation(s) found:")
        for violation in violations:
            print(f"  {violation}")
        return 1
    print("INTEGRATION SWALLOW AUDIT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
