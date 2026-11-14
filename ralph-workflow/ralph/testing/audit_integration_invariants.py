from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "AGENT_ELIGIBILITY_OWNER_MODULE",
    "EXPECTED_EXIT_CODE_LABELS",
    "GITLINK_POLICY_MODULE",
    "GITLINK_RESOLUTION_MODULE",
    "PIPELINE_ENTRY_MODULE",
    "PIPELINE_PACKAGE",
    "RESOLUTION_ENTRY_MODULE",
    "RESOLVER_NAMES",
    "SEVERITY_TABLE_ACCESSOR",
    "SEVERITY_TABLE_CONSTANT",
    "SEVERITY_TABLE_MODULE",
    "TERMINAL_EMITTER_MODULE",
    "Violation",
    "audit_modules",
    "audit_tree",
    "collect_eligibility_violations",
    "collect_gitlink_violations",
    "collect_resolver_violations",
    "collect_severity_violations",
    "collect_terminal_emitter_violations",
    "collect_watchdog_cap_violations",
    "load_modules",
    "main",
]

RESOLUTION_ENTRY_MODULE: Final = "ralph/pipeline/_stop_resolution_entry.py"

RESOLVER_NAMES: Final[frozenset[str]] = frozenset(
    {
        "_resolve_rebase_with_config",
        "resolve_rebase_with_config",
    }
)

REBASE_LOOP_MODULE: Final = "ralph/pipeline/conflict_resolution/rebase_loop.py"

REBASE_LOOP_RESOLVER_NAMES: Final[frozenset[str]] = frozenset(
    {"_resolve_rebase_in_progress", "resolve_rebase_in_progress"}
)

PIPELINE_PACKAGE: Final = "ralph/pipeline/"

PIPELINE_ENTRY_MODULE: Final = "ralph/pipeline/__init__.py"

SEVERITY_TABLE_MODULE: Final = "ralph/process/manager/_process_manager_runtime.py"

SEVERITY_TABLE_CONSTANT: Final = "EXPECTED_EXIT_CODES"

SEVERITY_TABLE_ACCESSOR: Final = "expected_exit_codes"

TERMINAL_EMITTER_MODULE: Final = "ralph/pipeline/_terminal_emitter.py"

EXPECTED_EXIT_CODE_LABELS: Final[frozenset[str]] = frozenset(
    {
        "git-is-ancestor",
        "integration-observation-head",
        "integration_diagnostic:check_ancestor",
        "integration_diagnostic:check_merge_head",
        "integration_diagnostic:check_rebase_head",
        "integration_diagnostic:diff_head",
        "integration_diagnostic:gitdir",
        "integration_diagnostic:head_parents",
        "integration_diagnostic:verify_head",
    }
)

_RETURN_CODE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "exit_code",
        "return_code",
        "returncode",
    }
)

_WARNING_METHODS: Final[frozenset[str]] = frozenset(
    {
        "warn",
        "warning",
    }
)

_SKIP_DIRS: Final[frozenset[str]] = frozenset({"__pycache__"})

AGENT_ELIGIBILITY_OWNER_MODULE: Final = "ralph/recovery/agent_failure_ledger.py"

FORBIDDEN_PIPELINE_ELIGIBILITY_NAMES: Final[frozenset[str]] = frozenset(
    {
        "_ledger_candidates_available",
        "is_available",
    }
)

_LEDGER_ELIGIBILITY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "cooldown_until_epoch_ms",
        "cooldown_until_ms",
        "is_spent",
        "spent_reason",
        "unavailable_reason",
    }
)

GITLINK_POLICY_MODULE: Final = "ralph/pipeline/conflict_resolution/gitlink_policy.py"

GITLINK_RESOLUTION_MODULE: Final = "ralph/pipeline/conflict_resolution/deterministic_resolution.py"

GITLINK_POLICY_OWNER_SYMBOLS: Final[frozenset[str]] = frozenset(
    {
        "POLICY_CANDIDATES",
        "choose_gitlink",
    }
)

WATCHDOG_FIRE_REASON_CLASS: Final = "WatchdogFireReason"

OPERATOR_CAP_REASON: Final = "OPERATOR_CAP_REACHED"


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


def _assignment_targets(node: ast.Assign | ast.AnnAssign) -> list[ast.expr]:
    if isinstance(node, ast.Assign):
        return [*node.targets]
    return [node.target]


def _resolver_definitions(tree: ast.Module) -> list[tuple[int, str]]:
    functions = [
        (node.lineno, node.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in RESOLVER_NAMES
    ]
    bindings = [
        (node.lineno, target.id)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign | ast.AnnAssign)
        for target in _assignment_targets(node)
        if isinstance(target, ast.Name) and target.id in RESOLVER_NAMES
    ]
    return sorted([*functions, *bindings])


def _imported_resolver_names(node: ast.Import | ast.ImportFrom) -> list[str]:
    return [alias.name for alias in node.names if alias.name.rsplit(".", 1)[-1] in RESOLVER_NAMES]


def _resolver_references(tree: ast.Module) -> list[tuple[int, str]]:
    imported = [
        (node.lineno, name)
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in _imported_resolver_names(node)
    ]
    attributes = [
        (node.lineno, node.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in RESOLVER_NAMES
    ]
    names = [
        (node.lineno, node.id)
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id in RESOLVER_NAMES
    ]
    return sorted([*imported, *attributes, *names])


def _resolver_export_strings(tree: ast.Module) -> list[tuple[int, str]]:
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in RESOLVER_NAMES
    ]


def _mentions_resolver(source: str) -> bool:
    return any(name in source for name in RESOLVER_NAMES)


def _resolver_module_violations(path: str, source: str) -> list[Violation]:
    tree = _parse_module(path, source)
    if tree is None:
        return []
    definitions = _resolver_definitions(tree)
    defined = {name for _line, name in definitions}
    definition_violations = [
        Violation(
            path=path,
            line=line,
            rule="resolver_defined_outside_entry",
            detail=(
                f"'{name}' is owned by {RESOLUTION_ENTRY_MODULE}; no other module may define it"
            ),
        )
        for line, name in definitions
    ]
    reference_violations = [
        Violation(
            path=path,
            line=line,
            rule="resolver_reference_outside_entry",
            detail=(
                f"'{name}' may only be referenced inside {RESOLUTION_ENTRY_MODULE}; "
                "callers must use the public resolution seam"
            ),
        )
        for line, name in _resolver_references(tree)
        if name not in defined
    ]
    export_violations: list[Violation] = []
    if path == PIPELINE_ENTRY_MODULE:
        export_violations = [
            Violation(
                path=path,
                line=line,
                rule="resolver_export_from_package",
                detail=(
                    f"{PIPELINE_ENTRY_MODULE} must not export '{name}'; "
                    "the package namespace must not re-export the private resolver"
                ),
            )
            for line, name in _resolver_export_strings(tree)
        ]
    return [*definition_violations, *reference_violations, *export_violations]


def _resolver_entry_violations(modules: Mapping[str, str]) -> list[Violation]:
    entry_source = modules.get(RESOLUTION_ENTRY_MODULE)
    if entry_source is None:
        return [
            Violation(
                path=RESOLUTION_ENTRY_MODULE,
                line=1,
                rule="resolver_entry_module_missing",
                detail=(
                    f"the single resolution entry module {RESOLUTION_ENTRY_MODULE} does not exist"
                ),
            )
        ]
    entry_tree = _parse_module(RESOLUTION_ENTRY_MODULE, entry_source)
    if entry_tree is None or _resolver_definitions(entry_tree):
        return []
    return [
        Violation(
            path=RESOLUTION_ENTRY_MODULE,
            line=1,
            rule="resolver_entry_module_without_definition",
            detail=(f"{RESOLUTION_ENTRY_MODULE} must define one of {sorted(RESOLVER_NAMES)}"),
        )
    ]


def collect_resolver_violations(modules: Mapping[str, str]) -> list[Violation]:
    module_violations = [
        violation
        for path, source in sorted(modules.items())
        if path != RESOLUTION_ENTRY_MODULE and _mentions_resolver(source)
        for violation in _resolver_module_violations(path, source)
    ]
    audited_loop_names = frozenset({"_resolve_rebase_in_progress"})
    private_loop_violations: list[Violation] = []
    for path, source in sorted(modules.items()):
        if path in {RESOLUTION_ENTRY_MODULE, REBASE_LOOP_MODULE}:
            continue
        tree = _parse_module(path, source)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            if name in audited_loop_names:
                private_loop_violations.append(
                    Violation(
                        path=path,
                        line=node.lineno,
                        rule="rebase_loop_resolver_call_outside_entry",
                        detail=(
                            f"'{name}' may only be called by {RESOLUTION_ENTRY_MODULE}; "
                            "callers must use the public resolution seam"
                        ),
                    )
                )
    return [*_resolver_entry_violations(modules), *module_violations, *private_loop_violations]


def _dict_label_keys(node: ast.Dict) -> list[str]:
    return [
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant)
        and isinstance(key.value, str)
        and key.value in EXPECTED_EXIT_CODE_LABELS
    ]


def _known_label_comparisons(tree: ast.Module) -> list[tuple[int, str]]:
    return [
        (node.lineno, operand.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        for operand in [node.left, *node.comparators]
        if isinstance(operand, ast.Constant)
        and isinstance(operand.value, str)
        and operand.value in EXPECTED_EXIT_CODE_LABELS
    ]


def _references_return_code(tree: ast.Module) -> bool:
    return any(
        (isinstance(node, ast.Attribute) and node.attr in _RETURN_CODE_NAMES)
        or (isinstance(node, ast.Name) and node.id in _RETURN_CODE_NAMES)
        for node in ast.walk(tree)
    )


def _has_warning_call(tree: ast.Module) -> bool:
    return any(
        (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _WARNING_METHODS
        )
        or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _WARNING_METHODS
        )
        for node in ast.walk(tree)
    )


def _mentions_severity_source(source: str) -> bool:
    if SEVERITY_TABLE_CONSTANT in source or SEVERITY_TABLE_ACCESSOR in source:
        return True
    return any(label in source for label in EXPECTED_EXIT_CODE_LABELS)


def _severity_owned_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    return [
        target.id
        for target in _assignment_targets(node)
        if isinstance(target, ast.Name)
        and target.id in {SEVERITY_TABLE_CONSTANT, SEVERITY_TABLE_ACCESSOR}
    ]


def _severity_owner_conflicts(path: str, tree: ast.Module) -> list[Violation]:
    accessor_conflicts = [
        Violation(
            path=path,
            line=node.lineno,
            rule="severity_table_accessor_conflict",
            detail=f"'{SEVERITY_TABLE_ACCESSOR}' is owned by {SEVERITY_TABLE_MODULE}",
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == SEVERITY_TABLE_ACCESSOR
    ]
    constant_conflicts = [
        Violation(
            path=path,
            line=node.lineno,
            rule="severity_table_constant_conflict",
            detail=f"'{name}' is owned by {SEVERITY_TABLE_MODULE}",
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign | ast.AnnAssign)
        for name in _severity_owned_names(node)
    ]
    return [*accessor_conflicts, *constant_conflicts]


def _competing_tables(path: str, tree: ast.Module) -> list[Violation]:
    return [
        Violation(
            path=path,
            line=node.lineno,
            rule="competing_label_exit_code_table",
            detail=(
                f"label->exit-code mapping {sorted(_dict_label_keys(node))} is owned by "
                f"{SEVERITY_TABLE_MODULE}"
            ),
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict) and _dict_label_keys(node)
    ]


def _severity_decision(path: str, tree: ast.Module) -> list[Violation]:
    comparisons = _known_label_comparisons(tree)
    if not comparisons:
        return []
    if not _references_return_code(tree):
        return []
    if not _has_warning_call(tree):
        return []
    line, label = comparisons[0]
    return [
        Violation(
            path=path,
            line=line,
            rule="severity_decision_outside_owner",
            detail=(
                f"'{label}' is compared with a process return code next to a warning call; "
                f"only {SEVERITY_TABLE_MODULE} decides DEBUG vs WARNING"
            ),
        )
    ]


def _severity_module_violations(path: str, source: str) -> list[Violation]:
    tree = _parse_module(path, source)
    if tree is None:
        return []
    return [
        *_severity_owner_conflicts(path, tree),
        *_competing_tables(path, tree),
        *_severity_decision(path, tree),
    ]


def collect_severity_violations(modules: Mapping[str, str]) -> list[Violation]:
    return [
        violation
        for path, source in sorted(modules.items())
        if path != SEVERITY_TABLE_MODULE and _mentions_severity_source(source)
        for violation in _severity_module_violations(path, source)
    ]


def _is_logger_receiver(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "logger" or node.id.endswith("logger")
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "opt"
    )


def _critical_calls(tree: ast.Module) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "critical"
        and _is_logger_receiver(node.func.value)
    ]


def _terminal_emitter_module_violations(path: str, source: str) -> list[Violation]:
    tree = _parse_module(path, source)
    if tree is None:
        return []
    return [
        Violation(
            path=path,
            line=call.lineno,
            rule="terminal_emitter_bypass",
            detail=(f"pipeline CRITICAL output must be emitted through {TERMINAL_EMITTER_MODULE}"),
        )
        for call in _critical_calls(tree)
    ]


def collect_terminal_emitter_violations(modules: Mapping[str, str]) -> list[Violation]:
    return [
        violation
        for path, source in sorted(modules.items())
        if path.startswith(PIPELINE_PACKAGE)
        and path != TERMINAL_EMITTER_MODULE
        and "critical" in source
        for violation in _terminal_emitter_module_violations(path, source)
    ]


def _ledger_snapshot_calls(node: ast.AST) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for part in ast.walk(node):
        if not isinstance(part, ast.Call):
            continue
        func = part.func
        if not isinstance(func, ast.Attribute) or func.attr != "snapshot":
            continue
        receiver = func.value
        receiver_name = (
            receiver.id
            if isinstance(receiver, ast.Name)
            else receiver.attr
            if isinstance(receiver, ast.Attribute)
            else ""
        )
        if "ledger" in receiver_name.lower():
            calls.append(part)
    return calls


def _pipeline_eligibility_helper_violations(path: str, tree: ast.Module) -> list[Violation]:
    return [
        Violation(
            path=path,
            line=node.lineno,
            rule="pipeline_agent_eligibility_helper",
            detail=(
                f"'{node.name}' implements agent eligibility inside ralph/pipeline/; "
                f"eligibility is owned by {AGENT_ELIGIBILITY_OWNER_MODULE} and projected by "
                "RecoveryController"
            ),
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name in FORBIDDEN_PIPELINE_ELIGIBILITY_NAMES
    ]


def _pipeline_snapshot_eligibility_violations(path: str, tree: ast.Module) -> list[Violation]:
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        calls = _ledger_snapshot_calls(node)
        if not calls:
            continue
        keys = {
            part.value
            for part in ast.walk(node)
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        }
        if not keys & _LEDGER_ELIGIBILITY_KEYS:
            continue
        violations.extend(
            Violation(
                path=path,
                line=call.lineno,
                rule="pipeline_ledger_snapshot_eligibility",
                detail=(
                    "agent eligibility is decided from a ledger snapshot inside ralph/pipeline/; "
                    f"the canonical answer is {AGENT_ELIGIBILITY_OWNER_MODULE}.agent_eligibility"
                ),
            )
            for call in calls
        )
    return violations


def collect_eligibility_violations(modules: Mapping[str, str]) -> list[Violation]:
    violations: list[Violation] = []
    for path, source in sorted(modules.items()):
        if not path.startswith(PIPELINE_PACKAGE):
            continue
        tree = _parse_module(path, source)
        if tree is None:
            continue
        violations.extend(_pipeline_eligibility_helper_violations(path, tree))
        violations.extend(_pipeline_snapshot_eligibility_violations(path, tree))
    return violations


def _definition_name(node: ast.stmt) -> str | None:
    if isinstance(node, ast.ClassDef):
        return node.name
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return node.name
    return None


def _module_symbols(tree: ast.Module) -> set[str]:
    symbols: set[str] = set()
    for node in tree.body:
        name = _definition_name(node)
        if name is not None:
            symbols.add(name)
        elif isinstance(node, ast.Assign):
            for target in _assignment_targets(node):
                if isinstance(target, ast.Name):
                    symbols.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            symbols.add(node.target.id)
    return symbols


def _class_members(node: ast.ClassDef) -> set[str]:
    members: set[str] = set()
    for statement in node.body:
        if isinstance(statement, ast.Assign):
            for target in _assignment_targets(statement):
                if isinstance(target, ast.Name):
                    members.add(target.id)
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            members.add(statement.target.id)
    return members


def _gitlink_member_names(modules: Mapping[str, str]) -> frozenset[str]:
    names: set[str] = set()
    for path, source in sorted(modules.items()):
        if not path.startswith(PIPELINE_PACKAGE):
            continue
        tree = _parse_module(path, source)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                if not isinstance(statement, ast.Assign | ast.AnnAssign):
                    continue
                for target in _assignment_targets(statement):
                    if isinstance(target, ast.Name) and target.id.startswith("GITLINK"):
                        names.add(target.id)
    return frozenset(names)


def _agent_eligible_container_names(modules: Mapping[str, str]) -> frozenset[str]:
    names: set[str] = set()
    for path, source in sorted(modules.items()):
        if not path.startswith(PIPELINE_PACKAGE):
            continue
        tree = _parse_module(path, source)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = _assignment_targets(node)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and "ELIGIBLE" in target.id.upper():
                    names.add(target.id)
    return frozenset(names)


def _gitlink_agent_eligible_violations(modules: Mapping[str, str]) -> list[Violation]:
    gitlink_members = _gitlink_member_names(modules)
    if not gitlink_members:
        return []
    eligible_names = _agent_eligible_container_names(modules)
    violations: list[Violation] = []
    for path, source in sorted(modules.items()):
        if not path.startswith(PIPELINE_PACKAGE):
            continue
        tree = _parse_module(path, source)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign | ast.AnnAssign):
                targets = _assignment_targets(node)
                eligible = any(
                    isinstance(target, ast.Name) and "ELIGIBLE" in target.id.upper()
                    for target in targets
                )
                if eligible and node.value is not None:
                    violations.extend(
                        Violation(
                            path=path,
                            line=part.lineno,
                            rule="gitlink conflict classified agent-eligible",
                            detail=(
                                f"'{part.attr}' is part of an agent-eligible set; a gitlink "
                                "conflict must never be agent-eligible because the pointer "
                                "policy resolves it in place"
                            ),
                        )
                        for part in ast.walk(node.value)
                        if isinstance(part, ast.Attribute) and part.attr in gitlink_members
                    )
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                gitlink_refs = [
                    part.attr
                    for operand in operands
                    for part in ast.walk(operand)
                    if isinstance(part, ast.Attribute) and part.attr in gitlink_members
                ]
                eligible_refs = [
                    part.id
                    for operand in operands
                    for part in ast.walk(operand)
                    if isinstance(part, ast.Name) and part.id in eligible_names
                ]
                if gitlink_refs and eligible_refs:
                    violations.append(
                        Violation(
                            path=path,
                            line=node.lineno,
                            rule="gitlink conflict classified agent-eligible",
                            detail=(
                                f"'{gitlink_refs[0]}' is compared with the agent-eligible set "
                                f"'{eligible_refs[0]}'; a gitlink conflict must never be "
                                "agent-eligible"
                            ),
                        )
                    )
    return violations


def _gitlink_owner_violations(modules: Mapping[str, str]) -> list[Violation]:
    violations: list[Violation] = []
    owner_source = modules.get(GITLINK_POLICY_MODULE)
    if owner_source is None:
        violations.append(
            Violation(
                path=GITLINK_POLICY_MODULE,
                line=1,
                rule="gitlink_policy_owner_missing",
                detail=(
                    f"the gitlink pointer-preference owner {GITLINK_POLICY_MODULE} must exist"
                ),
            )
        )
    else:
        owner_tree = _parse_module(GITLINK_POLICY_MODULE, owner_source)
        defined = _module_symbols(owner_tree) if owner_tree is not None else set()
        violations.extend(
            Violation(
                path=GITLINK_POLICY_MODULE,
                line=1,
                rule="gitlink_policy_owner_incomplete",
                detail=f"'{symbol}' must be owned by {GITLINK_POLICY_MODULE}",
            )
            for symbol in sorted(GITLINK_POLICY_OWNER_SYMBOLS - defined)
        )
    resolution_source = modules.get(GITLINK_RESOLUTION_MODULE)
    if resolution_source is None:
        violations.append(
            Violation(
                path=GITLINK_RESOLUTION_MODULE,
                line=1,
                rule="gitlink_resolution_module_missing",
                detail=f"{GITLINK_RESOLUTION_MODULE} must exist to resolve gitlinks in place",
            )
        )
    elif "choose_gitlink" not in resolution_source:
        violations.append(
            Violation(
                path=GITLINK_RESOLUTION_MODULE,
                line=1,
                rule="gitlink_not_resolved_in_place",
                detail=(
                    f"{GITLINK_RESOLUTION_MODULE} must resolve a gitlink conflict in place "
                    f"through the pointer policy owner {GITLINK_POLICY_MODULE}"
                ),
            )
        )
    return violations


def collect_gitlink_violations(modules: Mapping[str, str]) -> list[Violation]:
    return [
        *_gitlink_owner_violations(modules),
        *_gitlink_agent_eligible_violations(modules),
    ]


def collect_watchdog_cap_violations(modules: Mapping[str, str]) -> list[Violation]:
    detail = (
        f"'{WATCHDOG_FIRE_REASON_CLASS}.{OPERATOR_CAP_REASON}' must survive; it is the shared "
        "watchdog cap reason and is independent of the removed resolver cap identifier"
    )
    for path, source in sorted(modules.items()):
        tree = _parse_module(path, source)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name != WATCHDOG_FIRE_REASON_CLASS:
                continue
            if OPERATOR_CAP_REASON in _class_members(node):
                return []
            return [
                Violation(
                    path=path,
                    line=node.lineno,
                    rule="operator_cap_reason_missing",
                    detail=detail,
                )
            ]
    for path, source in sorted(modules.items()):
        if WATCHDOG_FIRE_REASON_CLASS not in source:
            continue
        tree = _parse_module(path, source)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == WATCHDOG_FIRE_REASON_CLASS:
                return [
                    Violation(
                        path=path,
                        line=node.lineno,
                        rule="operator_cap_reason_missing",
                        detail=detail,
                    )
                ]
    return [
        Violation(
            path=_watchdog_reason_owner_path(modules),
            line=1,
            rule="operator_cap_reason_missing",
            detail=detail,
        )
    ]


def _watchdog_reason_owner_path(modules: Mapping[str, str]) -> str:
    candidates = sorted(path for path in modules if path.endswith("watchdog_fire_reason.py"))
    if candidates:
        return candidates[0]
    return "ralph/agents/watchdog_fire_reason.py"


def audit_modules(modules: Mapping[str, str]) -> list[Violation]:
    return [
        *collect_resolver_violations(modules),
        *collect_severity_violations(modules),
        *collect_terminal_emitter_violations(modules),
        *collect_eligibility_violations(modules),
        *collect_gitlink_violations(modules),
        *collect_watchdog_cap_violations(modules),
    ]


def _module_paths(repo_root: Path) -> tuple[Path, ...]:
    base = repo_root / "ralph"
    return tuple(
        sorted(
            path
            for path in base.rglob("*.py")
            if not any(part in _SKIP_DIRS for part in path.parts)
        )
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
        print(f"INTEGRATION INVARIANTS AUDIT: {len(violations)} violation(s) found:")
        for violation in violations:
            print(f"  {violation}")
        return 1
    print(
        "INTEGRATION INVARIANTS AUDIT: OK "
        "(private-resolver containment, severity authority, terminal-emitter routing, "
        "agent-eligibility ownership, gitlink policy ownership, watchdog cap reason)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
