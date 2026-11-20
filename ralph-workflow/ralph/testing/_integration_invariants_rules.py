from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "GITLINK_POLICY_MODULE",
    "GITLINK_RESOLUTION_MODULE",
    "KEY_UNAVAILABLE_TYPE",
    "OPERATOR_CAP_REASON",
    "PIPELINE_PACKAGE",
    "RESOLUTION_KEY_FUNCTION",
    "RESOLUTION_KEY_MODULE",
    "RESOLUTION_KEY_RULE",
    "WATCHDOG_FIRE_REASON_CLASS",
    "Violation",
    "collect_gitlink_violations",
    "collect_resolution_key_violations",
    "collect_watchdog_cap_violations",
]

PIPELINE_PACKAGE: Final = "ralph/pipeline/"

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

RESOLUTION_KEY_MODULE: Final = "ralph/pipeline/_stop_resolution_entry.py"

RESOLUTION_KEY_FUNCTION: Final = "compute_resolution_attempt_key"

KEY_UNAVAILABLE_TYPE: Final = "KeyUnavailable"

RESOLUTION_KEY_RULE: Final = "resolution_key_silent_fallback"


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
        match True:
            case _ if name is not None:
                symbols.add(name)
            case _ if isinstance(node, ast.Assign):
                for target in _assignment_targets(node):
                    if isinstance(target, ast.Name):
                        symbols.add(target.id)
            case _ if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                symbols.add(node.target.id)
    return symbols


def _class_members(node: ast.ClassDef) -> set[str]:
    members: set[str] = set()
    for statement in node.body:
        match True:
            case _ if isinstance(statement, ast.Assign):
                for target in _assignment_targets(statement):
                    if isinstance(target, ast.Name):
                        members.add(target.id)
            case _ if isinstance(statement, ast.AnnAssign) and isinstance(
                statement.target, ast.Name
            ):
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
            match True:
                case _ if isinstance(node, ast.Assign):
                    targets = _assignment_targets(node)
                case _ if isinstance(node, ast.AnnAssign):
                    targets = [node.target]
                case _:
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
                detail=(f"the gitlink pointer-preference owner {GITLINK_POLICY_MODULE} must exist"),
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


def _gitlink_in_scope(modules: Mapping[str, str]) -> bool:
    return any("conflict_resolution" in path or "gitlink" in path for path in modules)


def collect_gitlink_violations(modules: Mapping[str, str]) -> list[Violation]:
    if not _gitlink_in_scope(modules):
        return []
    return [
        *_gitlink_owner_violations(modules),
        *_gitlink_agent_eligible_violations(modules),
    ]


def _watchdog_in_scope(modules: Mapping[str, str]) -> bool:
    return any("watchdog" in path or path.startswith("ralph/agents/") for path in modules)


def collect_watchdog_cap_violations(modules: Mapping[str, str]) -> list[Violation]:
    if not _watchdog_in_scope(modules):
        return []
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


def _is_broad_handler(node: ast.ExceptHandler) -> bool:
    caught = node.type
    if caught is None:
        return True
    if isinstance(caught, ast.Name):
        return caught.id in {"Exception", "BaseException"}
    return False


def _references_key_unavailable(node: ast.AST) -> bool:
    return any(
        (isinstance(part, ast.Name) and part.id == KEY_UNAVAILABLE_TYPE)
        or (isinstance(part, ast.Attribute) and part.attr == KEY_UNAVAILABLE_TYPE)
        for part in ast.walk(node)
    )


def _resolution_key_violations(path: str, source: str, tree: ast.Module) -> list[Violation]:
    if path != RESOLUTION_KEY_MODULE:
        return []
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == RESOLUTION_KEY_FUNCTION
    ]
    if not functions:
        return [
            Violation(
                path=path,
                line=1,
                rule=RESOLUTION_KEY_RULE,
                detail=(
                    f"{RESOLUTION_KEY_MODULE} must define {RESOLUTION_KEY_FUNCTION}; the "
                    "resolution attempt key has one owner"
                ),
            )
        ]
    violations: list[Violation] = []
    for function in functions:
        if not _references_key_unavailable(function):
            violations.append(
                Violation(
                    path=path,
                    line=function.lineno,
                    rule=RESOLUTION_KEY_RULE,
                    detail=(
                        f"{RESOLUTION_KEY_FUNCTION} must return the typed {KEY_UNAVAILABLE_TYPE} "
                        "outcome instead of a differently shaped key"
                    ),
                )
            )
        for handler in ast.walk(function):
            if not isinstance(handler, ast.ExceptHandler):
                continue
            if not _is_broad_handler(handler):
                continue
            violations.append(
                Violation(
                    path=path,
                    line=handler.lineno,
                    rule=RESOLUTION_KEY_RULE,
                    detail=(
                        f"{RESOLUTION_KEY_FUNCTION} must not swallow a broad exception; an "
                        "unreadable key is a typed "
                        f"{KEY_UNAVAILABLE_TYPE} outcome"
                    ),
                )
            )
    if KEY_UNAVAILABLE_TYPE not in source:
        violations.append(
            Violation(
                path=path,
                line=1,
                rule=RESOLUTION_KEY_RULE,
                detail=(
                    f"{RESOLUTION_KEY_MODULE} must import the typed {KEY_UNAVAILABLE_TYPE} outcome"
                ),
            )
        )
    return violations


def collect_resolution_key_violations(modules: Mapping[str, str]) -> list[Violation]:
    violations: list[Violation] = []
    for path, source in sorted(modules.items()):
        tree = _parse_module(path, source)
        if tree is None:
            continue
        violations.extend(_resolution_key_violations(path, source, tree))
    return violations
