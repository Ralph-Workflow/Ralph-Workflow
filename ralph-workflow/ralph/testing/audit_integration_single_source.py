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


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


_UNMERGED_READERS = frozenset({"ralph/git/conflict_stage_entries.py", "ralph/git/merge.py"})
_OPERATION_OWNER = "ralph/git/operation_snapshot.py"
_ELIGIBILITY_OWNER = "ralph/recovery/agent_failure_ledger.py"
_LANDED_OWNER = "ralph/pipeline/_landed_predicate.py"
_OPERATION_DEFINITIONS = frozenset({"operation_state", "observe_operation_snapshot"})
_ELIGIBILITY_DEFINITIONS = frozenset({"agent_is_eligible", "is_agent_eligible"})
_LANDED_DEFINITIONS = frozenset({"operation_landed", "resolved_stop_landed"})


def _string_literals(node: ast.AST) -> set[str]:
    return {
        value.value
        for value in ast.walk(node)
        if isinstance(value, ast.Constant) and isinstance(value.value, str)
    }


def audit_tree(repo_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for path in sorted((repo_root / "ralph").rglob("*.py")):
        rel = path.relative_to(repo_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                literals = _string_literals(node)
                if (
                    rel not in _UNMERGED_READERS
                    and "ls-files" in literals
                    and ({"-u", "--unmerged"} & literals)
                ):
                    violations.append(
                        Violation(rel, node.lineno, "unmerged index read outside git owner")
                    )
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name in _OPERATION_DEFINITIONS and rel != _OPERATION_OWNER:
                    violations.append(
                        Violation(rel, node.lineno, "operation state has a second reader")
                    )
                if node.name in _ELIGIBILITY_DEFINITIONS and rel != _ELIGIBILITY_OWNER:
                    violations.append(
                        Violation(rel, node.lineno, "agent eligibility owner is the ledger")
                    )
                if node.name in _LANDED_DEFINITIONS and rel != _LANDED_OWNER:
                    violations.append(
                        Violation(rel, node.lineno, "operation landed has a second predicate")
                    )
    return violations


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path(__file__).resolve().parents[2]
    violations = audit_tree(root)
    if violations:
        print(f"INTEGRATION SINGLE-SOURCE AUDIT: {len(violations)} violation(s) found:")
        for violation in violations:
            print(f"  {violation}")
        return 1
    print("INTEGRATION SINGLE-SOURCE AUDIT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
