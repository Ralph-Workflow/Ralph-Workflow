from __future__ import annotations

import ast
import os
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
    "AGENT_FREE_RETRY_RULE",
    "AGENT_PROCESS_LAUNCH_RULE",
    "AGENT_RETRY_DELAY_RULE",
    "AGENT_SELECTION_RULE",
    "BUDGET_LAUNCH_PERMISSION_RULE",
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
    "selftest",
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

AGENT_FREE_RETRY_MODULE: Final = "ralph/pipeline/auto_integrate_backoff.py"

CONFLICT_BUDGET_MODULE: Final = "ralph/pipeline/auto_integrate_conflict_budget.py"

RETRY_DELAY_OWNER_MODULE: Final = "ralph/pipeline/auto_integrate_backoff.py"

SHARED_COOLDOWN_OWNER_MODULE: Final = "ralph/recovery/controller.py"

AGENT_FREE_RETRY_FORBIDDEN_TOKENS: Final[tuple[str, ...]] = (
    "agent",
    "candidate",
    "launch",
    "resolver",
)

ALWAYS_AUDITED_MODULES: Final[frozenset[str]] = frozenset(
    {
        AGENT_FREE_RETRY_MODULE,
        CONFLICT_BUDGET_MODULE,
    }
)

LAUNCH_PERMISSION_NAME_TOKENS: Final[tuple[str, ...]] = (
    "allow",
    "eligible",
    "launch",
    "permit",
)

LAUNCH_PERMISSION_SUBJECT_TOKENS: Final[tuple[str, ...]] = (
    "agent",
    "candidate",
    "resolver",
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
    *(name for name in FORBIDDEN_PROCESS_LAUNCH_NAMES if name != "exec"),
    "os.exec",
    "subprocess.Popen",
    "pty.spawn",
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

AGENT_FREE_RETRY_RULE: Final = "integration_agent_free_retry_module"

BUDGET_LAUNCH_PERMISSION_RULE: Final = "integration_budget_launch_permission"

RETRY_DELAY_OWNER_RULE: Final = "integration_retry_delay_second_owner"

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


def _exported_names(tree: ast.Module) -> list[str]:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [
            target.id for target in node.targets if isinstance(target, ast.Name)
        ]
        if "__all__" not in targets:
            continue
        if not isinstance(node.value, ast.List | ast.Tuple):
            continue
        return [
            element.value
            for element in node.value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
    return []


def _is_launch_permission_name(name: str) -> bool:
    lowered = name.lower()
    if not any(token in lowered for token in LAUNCH_PERMISSION_SUBJECT_TOKENS):
        return False
    return any(token in lowered for token in LAUNCH_PERMISSION_NAME_TOKENS)


def _agent_free_retry_violations(rel_path: str, source: str) -> list[Violation]:
    if rel_path != AGENT_FREE_RETRY_MODULE:
        return []
    lowered = source.lower()
    return [
        Violation(
            path=rel_path,
            line=1,
            rule=AGENT_FREE_RETRY_RULE,
            detail=(
                f"'{token}' appears in the agent-free retry module; this module paces "
                "git, network and lock retries only and never times an agent launch"
            ),
        )
        for token in AGENT_FREE_RETRY_FORBIDDEN_TOKENS
        if token in lowered
    ]


def _budget_launch_permission_violations(rel_path: str, tree: ast.Module) -> list[Violation]:
    if rel_path != CONFLICT_BUDGET_MODULE:
        return []
    return [
        Violation(
            path=rel_path,
            line=1,
            rule=BUDGET_LAUNCH_PERMISSION_RULE,
            detail=(
                f"'{name}' exports a resolver-launch permission; the conflict budget is a "
                "no-progress bound and the launch permission is the shared cooldown answer"
            ),
        )
        for name in _exported_names(tree)
        if _is_launch_permission_name(name)
    ]


def _retry_delay_owner_violations(rel_path: str, tree: ast.Module) -> list[Violation]:
    if rel_path in {RETRY_DELAY_OWNER_MODULE, SHARED_COOLDOWN_OWNER_MODULE}:
        return []
    return [
        Violation(
            path=rel_path,
            line=node.lineno,
            rule=RETRY_DELAY_OWNER_RULE,
            detail=(
                f"'{node.name}' is a second retry-delay implementation; the retry delay is "
                f"owned by {RETRY_DELAY_OWNER_MODULE}"
            ),
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name in FORBIDDEN_RETRY_DELAY_CALLS
    ]


def audit_source(rel_path: str, source: str) -> list[Violation]:
    shared_path = _is_shared_invocation_path(rel_path)
    if not shared_path and not _is_guarded_integration_path(rel_path):
        return []
    if (
        not shared_path
        and rel_path not in ALWAYS_AUDITED_MODULES
        and not _source_may_contain_violation(source)
    ):
        return []
    tree = _parse_module(rel_path, source)
    if tree is None:
        return []
    nodes = list(ast.walk(tree))
    if shared_path:
        return _shared_path_violations(rel_path, nodes)
    return [
        *_guarded_violations(rel_path, nodes),
        *_agent_free_retry_violations(rel_path, source),
        *_budget_launch_permission_violations(rel_path, tree),
        *_retry_delay_owner_violations(rel_path, tree),
    ]


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


def _retry_delay_owner_tree_violations(repo_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    base_dir = repo_root / "ralph" / "pipeline"
    if not base_dir.is_dir():
        return []
    exempt = {RETRY_DELAY_OWNER_MODULE, SHARED_COOLDOWN_OWNER_MODULE}
    token_bytes = tuple(name.encode("utf-8") for name in FORBIDDEN_RETRY_DELAY_CALLS)
    for dirpath, dirnames, filenames in os.walk(base_dir):
        if "__pycache__" in dirnames:
            dirnames.remove("__pycache__")
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            full_path = Path(dirpath) / filename
            rel_path = full_path.relative_to(repo_root).as_posix()
            if rel_path in exempt:
                continue
            try:
                raw = full_path.read_bytes()
            except OSError:
                continue
            if not any(token in raw for token in token_bytes):
                continue
            try:
                source = raw.decode("utf-8")
                tree = ast.parse(source, filename=rel_path)
            except (UnicodeDecodeError, SyntaxError):
                continue
            violations.extend(_retry_delay_owner_violations(rel_path, tree))
    return violations


def audit_tree(repo_root: Path) -> list[Violation]:
    return [
        *audit_modules(load_modules(repo_root)),
        *_retry_delay_owner_tree_violations(repo_root),
    ]


def selftest() -> int:
    clean_modules = {
        AGENT_FREE_RETRY_MODULE: (
            "RETRY_BASE_DELAY_SECONDS = 0.5\n"
            "def compute_retry_delay(attempt: int) -> float:\n"
            "    return RETRY_BASE_DELAY_SECONDS\n"
        ),
        CONFLICT_BUDGET_MODULE: (
            "__all__ = [\"ConflictIdentity\", \"apply_conflict_budget\"]\n"
        ),
    }
    clean_violations = audit_modules(clean_modules)
    if clean_violations:
        sys.stdout.write(f"SELFTEST FAILED: clean modules produced violations: {clean_violations}\n")
        return 1

    planted_cases: list[tuple[str, str, str]] = [
        (
            AGENT_FREE_RETRY_MODULE,
            "def wait_before_retry(agent: str) -> None:\n    pass\n",
            AGENT_FREE_RETRY_RULE,
        ),
        (
            CONFLICT_BUDGET_MODULE,
            "__all__ = [\"resolver_allowed\"]\n",
            BUDGET_LAUNCH_PERMISSION_RULE,
        ),
        (
            "ralph/pipeline/auto_integrate_second_backoff.py",
            "def compute_retry_delay(attempt: int) -> float:\n    return 1.0\n",
            RETRY_DELAY_OWNER_RULE,
        ),
    ]
    for rel_path, source, expected_rule in planted_cases:
        violations = audit_modules({rel_path: source})
        rules = [violation.rule for violation in violations]
        if expected_rule not in rules:
            sys.stdout.write(
                f"SELFTEST FAILED: {rel_path} did not produce rule '{expected_rule}', got {rules}\n"
            )
            return 1

    sys.stdout.write(
        f"INTEGRATION AGENT PATH AUDIT: SELFTEST OK ({len(clean_modules) + len(planted_cases)} cases verified)\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in args:
        return selftest()
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
