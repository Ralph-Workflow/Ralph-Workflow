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


def audit_tree(repo_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for path in sorted((repo_root / "ralph").rglob("*.py")):
        rel = path.relative_to(repo_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _call_name(node)
                if name == "run_git" and rel not in {
                    "ralph/git/conflict_stage_entries.py",
                    "ralph/git/merge.py",
                }:
                    literals = {
                        value.value
                        for value in ast.walk(node)
                        if isinstance(value, ast.Constant) and isinstance(value.value, str)
                    }
                    if "ls-files" in literals and ("-u" in literals or "--unmerged" in literals):
                        violations.append(Violation(rel, node.lineno, "unmerged index read outside git owner"))
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name in {"operation_landed", "_operation_state"} and rel not in {
                    "ralph/pipeline/_landed_predicate.py",
                    "ralph/git/operation_snapshot.py",
                }:
                    violations.append(Violation(rel, node.lineno, f"duplicate B1 fact owner: {node.name}"))
                if node.name in {"is_agent_eligible", "agent_is_eligible"} and rel != (
                    "ralph/recovery/agent_failure_ledger.py"
                ):
                    violations.append(Violation(rel, node.lineno, "agent eligibility owner is the ledger"))
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
