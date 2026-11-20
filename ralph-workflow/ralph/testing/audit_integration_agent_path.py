from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ralph.testing.audit_integration_invariants import (
    _LEDGER_ELIGIBILITY_KEYS,
    FORBIDDEN_PIPELINE_ELIGIBILITY_NAMES,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "AGENT_CLASSIFICATION_RULE",
    "AGENT_PROCESS_LAUNCH_RULE",
    "AGENT_RETRY_DELAY_RULE",
    "AGENT_SELECTION_RULE",
    "CONFLICT_RESOLUTION_PACKAGE",
    "COOLDOWN_WRITE_RULE",
    "FORBIDDEN_AGENT_CLASSIFICATION_NAMES",
    "FORBIDDEN_COOLDOWN_OWNER_CONSTRUCTIONS",
    "FORBIDDEN_COOLDOWN_WRITE_CALLS",
    "FORBIDDEN_PROCESS_LAUNCH_NAMES",
    "FORBIDDEN_RETRY_DELAY_CALLS",
    "GUARDED_INTEGRATION_PATH_PREFIXES",
    "SHARED_INVOCATION_PATH_MODULES",
    "SHARED_PATH_BRANCH_RULE",
    "Violation",
    "audit_modules",
    "audit_source",
    "audit_tree",
    "main",
]

CONFLICT_RESOLUTION_PACKAGE: Final = "ralph/pipeline/conflict_resolution/"

GUARDED_INTEGRATION_PATH_PREFIXES: Final[tuple[str, ...]] = (
    "ralph/pipeline/auto_integrate",
    "ralph/pipeline/_auto_integrate",
    "ralph/pipeline/_integration",
    "ralph/pipeline/integration",
    "ralph/pipeline/_pending_merge_repair",
)

SHARED_INVOCATION_PATH_MODULES: Final[tuple[str, ...]] = (
    "ralph/pipeline/attempt_runner.py",
    "ralph/pipeline/effect_executor.py",
)

PIPELINE_PACKAGE: Final = "ralph/pipeline/"

CONFLICT_RESOLUTION_DIRECTORY: Final = "conflict_resolution"

GUARDED_INTEGRATION_MODULE_PREFIXES: Final[tuple[str, ...]] = (
    "auto_integrate",
    "_auto_integrate",
    "_integration",
    "integration",
    "_pending_merge_repair",
)

SKIP_DIRECTORIES: Final[frozenset[str]] = frozenset({"__pycache__"})

FORBIDDEN_AGENT_CLASSIFICATION_NAMES: Final[frozenset[str]] = frozenset(
    {
        "AttemptFault",
        "ClassifiedFailure",
        "FailureClassifier",
        "classify_attempt_fault",
        "classify_ralph_origin_fault",
    }
)

FORBIDDEN_RETRY_DELAY_CALLS: Final[frozenset[str]] = frozenset(
    {
        "_compute_retry_delay",
        "compute_retry_delay",
        "wait_before_retry",
    }
)

RETRY_DELAY_EXEMPT_PATHS: Final[frozenset[str]] = frozenset(
    {
        "ralph/pipeline/auto_integrate_backoff.py",
        "ralph/pipeline/auto_integrate_catchup.py",
        "ralph/pipeline/auto_integrate_conflict_budget.py",
        "ralph/pipeline/_auto_integrate_attempt_loop.py",
    }
)

FORBIDDEN_COOLDOWN_WRITE_CALLS: Final[frozenset[str]] = frozenset(
    {
        "_mark_agent_unavailable",
        "mark_agent_unavailable",
        "mark_candidate_unavailable",
        "mark_unavailable",
        "record_attempt_fact",
        "record_confirmed_unavailability",
        "reset_backoff",
    }
)

FORBIDDEN_COOLDOWN_OWNER_CONSTRUCTIONS: Final[frozenset[str]] = frozenset(
    {
        "AgentFailureLedger",
        "AgentUnavailabilityTracker",
        "RecoveryController",
    }
)

FORBIDDEN_PROCESS_LAUNCH_NAMES: Final[frozenset[str]] = frozenset(
    {
        "Popen",
        "exec",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "execl",
        "execlp",
        "execle",
        "execlpe",
        "posix_spawn",
        "posix_spawnp",
        "spawn",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
    }
)

PROCESS_LAUNCH_RECEIVERS: Final[frozenset[str]] = frozenset({"os", "pty", "subprocess"})

SHARED_PATH_INTEGRATION_MARKERS: Final[tuple[str, ...]] = (
    "auto_integrate",
    "auto-integrate",
    "conflict resolution",
    "conflict_resolution",
    "integration",
    "rebase conflict",
    "rebase_conflict",
)

AGENT_ELIGIBILITY_NAME_MARKER: Final = "_eligible"

AGENT_ELIGIBILITY_NAME_TOKENS: Final[tuple[str, ...]] = (
    "agent",
    "available",
    "candidate",
    "chain",
    "cooldown",
)

AGENT_SELECTION_NAME_SHAPES: Final[tuple[str, ...]] = (
    "_choose_agent",
    "_select_agent",
)

AGENTS_NOW_AVAILABLE_NAME: Final = "agents_now_available"

VIOLATION_SOURCE_TRIGGERS: Final[tuple[str, ...]] = (
    *FORBIDDEN_AGENT_CLASSIFICATION_NAMES,
    *FORBIDDEN_RETRY_DELAY_CALLS,
    *FORBIDDEN_COOLDOWN_WRITE_CALLS,
    *FORBIDDEN_COOLDOWN_OWNER_CONSTRUCTIONS,
    *FORBIDDEN_PROCESS_LAUNCH_NAMES,
    *FORBIDDEN_PIPELINE_ELIGIBILITY_NAMES,
    AGENT_ELIGIBILITY_NAME_MARKER,
    AGENT_SELECTION_NAME_SHAPES[0],
    AGENT_SELECTION_NAME_SHAPES[1],
    AGENTS_NOW_AVAILABLE_NAME,
    *_LEDGER_ELIGIBILITY_KEYS,
)

AGENT_CLASSIFICATION_RULE: Final = "integration_agent_failure_classification"

AGENT_RETRY_DELAY_RULE: Final = "integration_agent_retry_delay"

COOLDOWN_WRITE_RULE: Final = "integration_agent_cooldown_write"

AGENT_PROCESS_LAUNCH_RULE: Final = "integration_agent_process_launch"

SHARED_PATH_BRANCH_RULE: Final = "shared_path_integration_branch"

AGENT_SELECTION_RULE: Final = "integration_agent_selection"

SHARED_CLASSIFIER_OWNER: Final = "ralph/recovery/failure_classifier.py"

SHARED_COOLDOWN_OWNER: Final = "ralph/recovery/controller.py"

SHARED_ELIGIBILITY_OWNER: Final = "ralph/recovery/agent_failure_ledger.py"


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.detail}"


def _parse_module(path: str, source: str) -> ast.Module | None:
    try:
        return ast.parse(source, filename=path)
    except SyntaxError:
        return None


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _receiver_root_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _receiver_root_name(node.value)
    return ""


def _is_guarded_integration_path(rel_path: str) -> bool:
    if rel_path.startswith(CONFLICT_RESOLUTION_PACKAGE):
        return True
    return any(rel_path.startswith(prefix) for prefix in GUARDED_INTEGRATION_PATH_PREFIXES)


def _is_shared_invocation_path(rel_path: str) -> bool:
    return rel_path in SHARED_INVOCATION_PATH_MODULES


def _classification_detail(name: str, kind: str) -> str:
    return f"{kind} of '{name}'; agent-failure classification is owned by {SHARED_CLASSIFIER_OWNER}"


def _imported_classification_names(node: ast.Import | ast.ImportFrom) -> list[str]:
    names: list[str] = []
    for alias in node.names:
        candidate = alias.name
        if isinstance(node, ast.Import):
            candidate = alias.name.rsplit(".", 1)[-1]
        if candidate in FORBIDDEN_AGENT_CLASSIFICATION_NAMES:
            names.append(candidate)
    return names


def _classification_violation(rel_path: str, line: int, name: str, kind: str) -> Violation:
    return Violation(
        path=rel_path,
        line=line,
        rule=AGENT_CLASSIFICATION_RULE,
        detail=_classification_detail(name, kind),
    )


def _retry_delay_violation(rel_path: str, line: int, name: str) -> Violation:
    return Violation(
        path=rel_path,
        line=line,
        rule=AGENT_RETRY_DELAY_RULE,
        detail=(
            f"'{name}' computes an agent retry delay; agent retry timing is owned by "
            f"{SHARED_COOLDOWN_OWNER} preferred_agent_index / earliest_available_wait_ms"
        ),
    )


def _cooldown_write_violation(rel_path: str, line: int, name: str) -> Violation:
    return Violation(
        path=rel_path,
        line=line,
        rule=COOLDOWN_WRITE_RULE,
        detail=(
            f"call to '{name}' writes agent cooldown state; the only permitted cooldown "
            f"write is {SHARED_COOLDOWN_OWNER} finalize_attempt"
        ),
    )


def _cooldown_construction_violation(rel_path: str, line: int, name: str) -> Violation:
    return Violation(
        path=rel_path,
        line=line,
        rule=COOLDOWN_WRITE_RULE,
        detail=(
            f"construction of '{name}' builds private cooldown state; agent cooldown "
            f"state is owned by {SHARED_COOLDOWN_OWNER}"
        ),
    )


def _launch_violation(rel_path: str, line: int) -> Violation:
    return Violation(
        path=rel_path,
        line=line,
        rule=AGENT_PROCESS_LAUNCH_RULE,
        detail="agent process launch; the agent launch path is owned by ralph/agents/invoke/",
    )


def _is_process_launch_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        return (
            func.attr in FORBIDDEN_PROCESS_LAUNCH_NAMES
            and _receiver_root_name(func.value) in PROCESS_LAUNCH_RECEIVERS
        )
    if isinstance(func, ast.Name):
        return func.id in FORBIDDEN_PROCESS_LAUNCH_NAMES and func.id != "exec"
    return False


def _call_violations(rel_path: str, node: ast.Call) -> list[Violation]:
    name = _call_name(node)
    if name is None:
        return []
    violations: list[Violation] = []
    if name in FORBIDDEN_AGENT_CLASSIFICATION_NAMES:
        violations.append(_classification_violation(rel_path, node.lineno, name, "construction"))
    if name in FORBIDDEN_RETRY_DELAY_CALLS and rel_path not in RETRY_DELAY_EXEMPT_PATHS:
        violations.append(_retry_delay_violation(rel_path, node.lineno, name))
    if name in FORBIDDEN_COOLDOWN_WRITE_CALLS:
        violations.append(_cooldown_write_violation(rel_path, node.lineno, name))
    elif name in FORBIDDEN_COOLDOWN_OWNER_CONSTRUCTIONS:
        violations.append(_cooldown_construction_violation(rel_path, node.lineno, name))
    if _is_process_launch_call(node):
        violations.append(_launch_violation(rel_path, node.lineno))
    return violations


def _integration_marker(value: str) -> str | None:
    lowered = value.lower()
    for marker in SHARED_PATH_INTEGRATION_MARKERS:
        if marker in lowered:
            return marker
    return None


def _string_marker(value: str) -> list[str]:
    marker = _integration_marker(value)
    return [value] if marker is not None else []


def _leaf_markers(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, str):
            return []
        return _string_marker(node.value)
    if isinstance(node, ast.Name):
        return _string_marker(node.id)
    if isinstance(node, ast.Attribute):
        found = _string_marker(node.attr)
        found.extend(_leaf_markers(node.value))
        return found
    return []


def _decision_markers(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Compare):
        found = _leaf_markers(node.left)
        for comparator in node.comparators:
            found.extend(_decision_markers(comparator))
        return found
    if isinstance(node, ast.BoolOp):
        return [marker for value in node.values for marker in _decision_markers(value)]
    if isinstance(node, ast.UnaryOp):
        return _decision_markers(node.operand)
    if isinstance(node, ast.IfExp):
        return [
            marker
            for part in (node.test, node.body, node.orelse)
            for marker in _decision_markers(part)
        ]
    if isinstance(node, ast.Call):
        return _decision_markers(node.func)
    return _leaf_markers(node)


def _branch_tests(nodes: list[ast.AST]) -> list[tuple[int, ast.AST]]:
    tests: list[tuple[int, ast.AST]] = []
    for node in nodes:
        if isinstance(node, (ast.If, ast.While, ast.IfExp)):
            tests.append((node.lineno, node.test))
        elif isinstance(node, ast.match_case) and node.guard is not None:
            tests.append((node.guard.lineno, node.guard))
    return tests


def _shared_path_violations(rel_path: str, nodes: list[ast.AST]) -> list[Violation]:
    return [
        Violation(
            path=rel_path,
            line=line,
            rule=SHARED_PATH_BRANCH_RULE,
            detail=(
                f"branch on '{marker}'; the shared invocation path must not distinguish "
                "integration from any other phase"
            ),
        )
        for line, test in _branch_tests(nodes)
        for marker in _decision_markers(test)
    ]


def _is_boolean_expression(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, bool)
    if isinstance(node, ast.Compare | ast.BoolOp):
        return True
    return isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)


def _returns_boolean(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    annotation = node.returns
    if isinstance(annotation, ast.Name) and annotation.id == "bool":
        return True
    returns = [child for child in ast.walk(node) if isinstance(child, ast.Return)]
    if not returns:
        return False
    return all(child.value is not None and _is_boolean_expression(child.value) for child in returns)


def _is_agent_eligibility_predicate(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if AGENT_ELIGIBILITY_NAME_MARKER not in node.name:
        return False
    if not any(token in node.name for token in AGENT_ELIGIBILITY_NAME_TOKENS):
        return False
    return _returns_boolean(node)


def _selection_name_violation(rel_path: str, line: int, name: str) -> Violation:
    return Violation(
        path=rel_path,
        line=line,
        rule=AGENT_SELECTION_RULE,
        detail=(
            f"definition of '{name}'; agent selection and eligibility are owned by the "
            f"shared reducer / effect router and {SHARED_ELIGIBILITY_OWNER}"
        ),
    )


def _is_forbidden_selection_name(
    name: str, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
) -> bool:
    if name in FORBIDDEN_PIPELINE_ELIGIBILITY_NAMES:
        return True
    if name == AGENTS_NOW_AVAILABLE_NAME:
        return True
    if any(shape in name for shape in AGENT_SELECTION_NAME_SHAPES):
        return True
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
        _is_agent_eligibility_predicate(node)
    )


def _definition_violations(
    rel_path: str, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
) -> list[Violation]:
    name = node.name
    violations: list[Violation] = []
    if name in FORBIDDEN_AGENT_CLASSIFICATION_NAMES:
        violations.append(_classification_violation(rel_path, node.lineno, name, "definition"))
    if name in FORBIDDEN_RETRY_DELAY_CALLS and rel_path not in RETRY_DELAY_EXEMPT_PATHS:
        violations.append(_retry_delay_violation(rel_path, node.lineno, name))
    if _is_forbidden_selection_name(name, node):
        violations.append(_selection_name_violation(rel_path, node.lineno, name))
    return violations


def _import_violations(rel_path: str, node: ast.Import | ast.ImportFrom) -> list[Violation]:
    return [
        _classification_violation(rel_path, node.lineno, name, "import")
        for name in _imported_classification_names(node)
    ]


def _constant_violations(rel_path: str, node: ast.Constant) -> list[Violation]:
    value = node.value
    if not isinstance(value, str) or value not in _LEDGER_ELIGIBILITY_KEYS:
        return []
    return [
        Violation(
            path=rel_path,
            line=node.lineno,
            rule=AGENT_SELECTION_RULE,
            detail=(
                f"read of ledger eligibility key '{value}'; agent eligibility is owned by "
                f"{SHARED_ELIGIBILITY_OWNER}"
            ),
        )
    ]


def _guarded_violations(rel_path: str, nodes: list[ast.AST]) -> list[Violation]:
    violations: list[Violation] = []
    for node in nodes:
        if isinstance(node, ast.Call):
            violations.extend(_call_violations(rel_path, node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            violations.extend(_definition_violations(rel_path, node))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            violations.extend(_import_violations(rel_path, node))
        elif isinstance(node, ast.Constant):
            violations.extend(_constant_violations(rel_path, node))
    return violations


def _source_may_contain_violation(source: str) -> bool:
    return any(trigger in source for trigger in VIOLATION_SOURCE_TRIGGERS)


def audit_source(rel_path: str, source: str) -> list[Violation]:
    shared_path = _is_shared_invocation_path(rel_path)
    if not shared_path and not _is_guarded_integration_path(rel_path):
        return []
    if not shared_path and not _source_may_contain_violation(source):
        return []
    tree = _parse_module(rel_path, source)
    if tree is None:
        return []
    nodes = list(ast.walk(tree))
    if shared_path:
        return _shared_path_violations(rel_path, nodes)
    return _guarded_violations(rel_path, nodes)


def audit_modules(modules: Mapping[str, str]) -> list[Violation]:
    violations: list[Violation] = []
    for rel_path, source in sorted(modules.items()):
        violations.extend(audit_source(rel_path, source))
    return violations


def _module_paths(repo_root: Path) -> tuple[Path, ...]:
    pipeline_dir = repo_root / PIPELINE_PACKAGE
    conflict_dir = pipeline_dir / CONFLICT_RESOLUTION_DIRECTORY
    paths: list[Path] = []
    if conflict_dir.is_dir():
        paths.extend(sorted(conflict_dir.rglob("*.py")))
    shared_names = {Path(module).name for module in SHARED_INVOCATION_PATH_MODULES}
    paths.extend(
        path
        for path in sorted(pipeline_dir.glob("*.py"))
        if path.name in shared_names or path.name.startswith(GUARDED_INTEGRATION_MODULE_PREFIXES)
    )
    return tuple(
        path for path in sorted(paths) if not any(part in SKIP_DIRECTORIES for part in path.parts)
    )


def load_modules(repo_root: Path) -> dict[str, str]:
    modules: dict[str, str] = {}
    for path in _module_paths(repo_root):
        modules[path.relative_to(repo_root).as_posix()] = path.read_text(encoding="utf-8")
    return modules


def audit_tree(repo_root: Path) -> list[Violation]:
    return audit_modules(load_modules(repo_root))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    repo_root = Path(args[0]) if args else Path(__file__).resolve().parents[2]
    violations = audit_tree(repo_root)
    if violations:
        sys.stdout.write(f"INTEGRATION AGENT PATH AUDIT: {len(violations)} violation(s) found:\n")
        for violation in violations:
            sys.stdout.write(f"  {violation}\n")
        return 1
    sys.stdout.write(
        "INTEGRATION AGENT PATH AUDIT: OK (no agent-failure classification, retry delay, "
        "cooldown write, agent process launch, agent selection or eligibility in the "
        "integration packages; shared invocation path is integration-blind)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
