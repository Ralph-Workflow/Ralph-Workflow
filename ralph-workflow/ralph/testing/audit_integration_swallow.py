from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


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


def audit_modules(modules: Mapping[str, str]) -> list[Violation]:
    violations: list[Violation] = []
    for rel, source in sorted(modules.items()):
        if not rel.startswith("ralph/pipeline/"):
            continue
        try:
            tree = ast.parse(source, filename=rel)
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


def audit_tree(repo_root: Path) -> list[Violation]:
    base = repo_root / "ralph" / "pipeline"
    modules: dict[str, str] = {}
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(repo_root).as_posix()
        try:
            modules[rel] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    return audit_modules(modules)


def selftest() -> int:
    clean_modules = {
        "ralph/pipeline/clean_narrow.py": (
            "def resolve_foo():\n"
            "    try:\n"
            "        return 1\n"
            "    except ValueError:\n"
            "        return None\n"
        ),
        "ralph/pipeline/clean_loud.py": (
            "def resolve_bar():\n"
            "    try:\n"
            "        return 1\n"
            "    except Exception:\n"
            "        logger.warning('swallowed')\n"
            "        return None\n"
        ),
        "ralph/pipeline/clean_raise.py": (
            "def resolution_step():\n"
            "    try:\n"
            "        return 1\n"
            "    except Exception:\n"
            "        raise\n"
        ),
    }
    clean_violations = audit_modules(clean_modules)
    if clean_violations:
        print(f"SELFTEST FAILED: clean modules produced violations: {clean_violations}")
        return 1

    planted_cases: list[tuple[str, str]] = [
        (
            "def resolve_conflict():\n"
            "    try:\n"
            "        return 1\n"
            "    except Exception:\n"
            "        return None\n",
            "broad handler returns an untyped normal value",
        ),
        (
            "def resolution_retry():\n"
            "    try:\n"
            "        return 1\n"
            "    except:\n"
            "        return False\n",
            "broad handler returns an untyped normal value",
        ),
        (
            "def landing_continue():\n"
            "    try:\n"
            "        return 1\n"
            "    except BaseException:\n"
            "        return ()\n",
            "broad handler returns an untyped normal value",
        ),
    ]

    for index, (source, expected_rule) in enumerate(planted_cases):
        path = f"ralph/pipeline/planted_{index}.py"
        violations = audit_modules({path: source})
        rules = [v.rule for v in violations]
        if expected_rule not in rules:
            print(
                f"SELFTEST FAILED: case {index} did not produce rule '{expected_rule}', got {rules}"
            )
            return 1

    print(
        f"INTEGRATION SWALLOW AUDIT: SELFTEST OK ({len(clean_modules) + len(planted_cases)} cases verified)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--selftest" in args:
        return selftest()
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
