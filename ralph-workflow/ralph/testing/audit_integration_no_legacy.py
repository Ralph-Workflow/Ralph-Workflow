from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Final, TypeGuard

from ralph.checked_accessors import module_attr
from ralph.testing import _audit_integration_no_legacy_sentinels as _no_legacy_sentinels
from ralph.testing import _integration_no_legacy_rules as _no_legacy_rules
from ralph.testing._audit_integration_no_legacy_sentinels import (
    _RETIRED_REVIEW_PATTERN,
    _RETIRED_REVIEW_RULE,
    _RETIRED_STEM,
    _package_key,
    _walked_nodes,
)
from ralph.testing._integration_no_legacy_rules import (
    _is_exempt_path,
    _operator_parking_literal_violations,
    _operator_parking_state_violations,
    _retired_review_candidate,
    _retired_review_violations,
    _retired_three_sha_message_violations,
)
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

DENIAL_ONLY_PRE_RENAME_SENTINELS = _no_legacy_sentinels.DENIAL_ONLY_PRE_RENAME_SENTINELS

Violation = _no_legacy_rules.Violation

_SELF_AUDIT_MODULES: frozenset[str] = _no_legacy_rules._SELF_AUDIT_MODULES
_RETIRED_THREE_SHA_MESSAGE: Final = _no_legacy_rules._RETIRED_THREE_SHA_MESSAGE
_RETIRED_THREE_SHA_RULE: Final = _no_legacy_rules._RETIRED_THREE_SHA_RULE
_OPERATOR_PARKING_LITERAL_RULE: Final = _no_legacy_rules._OPERATOR_PARKING_LITERAL_RULE
_OPERATOR_PARKING_STATE_RULE: Final = _no_legacy_rules._OPERATOR_PARKING_STATE_RULE

_BOOL_STR_TUPLE_ARITY: Final = 2

_PACKAGE_PROJECT_DIR_NAME: Final = "ralph-workflow"
_PACKAGE_DIR_NAME: Final = "ralph"

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

_LEGACY_CONFLICT_CLASSIFIER_NAMES: frozenset[str] = frozenset({"classify_conflict_attempt"})
_DELETED_PROCESS_LOCAL_STATE_NAMES: frozenset[str] = frozenset(
    {
        "_WAITING_STATE",
        "_retained_round_fingerprints",
    }
)
_REMOVED_RESOLUTION_CAP_NAMES: frozenset[str] = frozenset({"total_resolution_cap_seconds"})
_LEGACY_GITLINK_ESCALATION_NAMES: frozenset[str] = frozenset({"escalate_gitlink_on_sight"})
_LEGACY_OUTCOME_CLASS_NAMES: frozenset[str] = frozenset({"ResolutionOutcome"})
_LEGACY_DEAD_OUTCOME_MODULE_NAMES: Final[frozenset[str]] = frozenset(
    f"_resolution_{kind}_" + "outcome"
    for kind in ("resolved", "config_failure", "waiting", "next_strategy", "unknown")
)
_LEGACY_OUTCOME_MODULE_PATHS: Final[frozenset[str]] = frozenset(
    {
        "ralph/pipeline/conflict_resolution/resolution_outcome.py",
        *(f"ralph/pipeline/{name}.py" for name in _LEGACY_DEAD_OUTCOME_MODULE_NAMES),
    }
)
LEGACY_OUTCOME_MODULE: str = "ralph/pipeline/conflict_resolution/resolution_outcome.py"
_SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".agent",
        ".agents",
        ".claude",
        ".git",
        ".hypothesis",
        ".mypy_cache",
        ".omo",
        ".pi-subagents",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".vscode",
        "_build",
        "build",
        "dist",
        "node_modules",
        "tmp",
        "venv",
        "__pycache__",
    }
)
_RESOLUTION_OUTCOME_TOKENS: tuple[str, ...] = (
    "ResolutionOutcome",
    "StopResolutionOutcome",
    "ResolvedOutcome",
    "WaitingOutcome",
    "RetryLaterOutcome",
    "NextStrategyOutcome",
    "ConfigFailureOutcome",
    "UnknownOutcome",
    "RebaseResolutionResult",
    "classify_resolution_failure",
    "attempt_stop_resolution",
    "resolve_conflicted_rebase",
)
_OUTCOME_PRODUCER_NAME_TOKENS: tuple[str, ...] = (
    "outcome",
    "result",
    "resolution",
    "resolve",
    "classify",
)

TRANSITIONAL_PENDING_PRODUCERS: Final[dict[str, frozenset[str]]] = {}

_ALLOWED_PRIVATE_RESOLVER_CALLERS: Final[frozenset[str]] = frozenset(
    {
        "ralph/pipeline/_stop_resolution_entry.py",
        "ralph/pipeline/conflict_resolution/rebase_loop.py",
    }
)
_PRIVATE_RESOLVER_NAMES: Final[frozenset[str]] = frozenset(
    {
        "_resolve_rebase_in_progress",
        "_resolve_rebase_with_config",
    }
)
_INTEGRATION_SIDE_FAILURE_CLASSIFIER_NAMES: Final[frozenset[str]] = frozenset(
    {
        "classify_conflict_attempt",
        "classify_agent_failure",
        "time_agent_retry",
        "compute_agent_retry_delay",
    }
)
_LOCAL_COOLDOWN_WRITE_TARGETS: Final[frozenset[str]] = frozenset(
    {
        "cooldown_until_ms",
        "cooldown_until_epoch_ms",
        "_cooldown_until",
    }
)
_SEALED_OUTCOME_CLASSES: Final[frozenset[str]] = frozenset(
    {
        "ResolvedOutcome",
        "WaitingOutcome",
        "RetryLaterOutcome",
        "NextStrategyOutcome",
        "ConfigFailureOutcome",
        "UnknownOutcome",
    }
)
_RESULT_RESULT_CLASS_NAMES: Final[frozenset[str]] = frozenset({"RebaseResolutionResult"})
_FORBIDDEN_RESOLUTION_SYMBOLS: Final[frozenset[str]] = frozenset(
    {"ExhaustedResolutionFailure", "_controller_for_key"}
)


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


def _legacy_protocol_member(node: ast.ClassDef) -> str | None:
    for statement in node.body:
        if not isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if statement.name in {"__len__", "__iter__", "__getitem__"}:
            return f"tuple protocol method '{statement.name}'"
        if statement.name == "succeeded" and _property_decorated(statement):
            return "legacy boolean 'succeeded' property"
    return None


def _property_decorated(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(decorator, ast.Name) and decorator.id == "property"
        for decorator in node.decorator_list
    )


def _node_identifier(node: ast.AST) -> tuple[str, int] | None:
    identifier: str | None = None
    line = 0
    match True:
        case _ if isinstance(node, ast.Name):
            identifier, line = (node.id, node.lineno)
        case _ if isinstance(node, ast.Attribute):
            identifier, line = (node.attr, node.lineno)
        case _ if isinstance(node, (ast.arg, ast.keyword)):
            identifier, line = (node.arg, node.lineno)
        case _ if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            identifier, line = (node.name, node.lineno)
    if identifier is None:
        return None
    return identifier, line


def _record_identifier_line(lines: dict[str, int], name: str, line: int) -> None:
    current = lines.get(name)
    if current is None or line < current:
        lines[name] = line


def _identifier_lines(tree: ast.Module) -> dict[str, int]:
    lines: dict[str, int] = {}
    for node in _walked_nodes(tree):
        if isinstance(node, ast.alias):
            _record_identifier_line(lines, node.name.rsplit(".", 1)[-1], node.lineno)
            if node.asname is not None:
                _record_identifier_line(lines, node.asname, node.lineno)
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            _record_identifier_line(lines, node.value, node.lineno)
            continue
        identifier = _node_identifier(node)
        if identifier is not None:
            _record_identifier_line(lines, identifier[0], identifier[1])
    return lines


def _forbidden_identifier_violations(
    path: str,
    identifier_lines: Mapping[str, int],
    names: frozenset[str],
    rule: str,
    detail: str,
) -> list[Violation]:
    violations: list[Violation] = []
    reported: set[str] = set()
    occurrences = sorted(
        (identifier_lines[name], name) for name in names if name in identifier_lines
    )
    for line, name in occurrences:
        if name in reported:
            continue
        reported.add(name)
        violations.append(Violation(path=path, line=line, rule=rule, detail=detail))
    return violations


def _bool_str_tuple_annotation(node: ast.AST) -> bool:
    if not isinstance(node, ast.Subscript):
        return False
    if not (isinstance(node.value, ast.Name) and node.value.id == "tuple"):
        return False
    slice_node = node.slice
    if not isinstance(slice_node, ast.Tuple) or len(slice_node.elts) != _BOOL_STR_TUPLE_ARITY:
        return False
    names = {element.id for element in slice_node.elts if isinstance(element, ast.Name)}
    return names == {"bool", "str"}


def _bool_str_tuple_literal(node: ast.Tuple) -> bool:
    if len(node.elts) != _BOOL_STR_TUPLE_ARITY:
        return False
    kinds: set[str] = set()
    for element in node.elts:
        match True:
            case _ if isinstance(element, ast.Constant) and isinstance(element.value, bool):
                kinds.add("bool")
            case _ if isinstance(element, ast.Constant) and isinstance(element.value, str):
                kinds.add("str")
            case _:
                return False
    return kinds == {"bool", "str"}


def _is_outcome_producer(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    lowered = node.name.lower()
    return any(token in lowered for token in _OUTCOME_PRODUCER_NAME_TOKENS)


def _legacy_tuple_violations(path: str, tree: ast.Module) -> list[Violation]:
    violations: list[Violation] = [
        Violation(
            path=path,
            line=node.lineno,
            rule="resolution outcome typed as (bool, str) tuple",
            detail=(
                "a resolution result must be the sealed StopResolutionOutcome union, "
                "never a tuple[bool, str]"
            ),
        )
        for node in _walked_nodes(tree)
        if isinstance(node, ast.Subscript) and _bool_str_tuple_annotation(node)
    ]
    for node in _walked_nodes(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not _is_outcome_producer(node):
            continue
        for statement in ast.walk(node):
            if not isinstance(statement, ast.Return) or statement.value is None:
                continue
            if not isinstance(statement.value, ast.Tuple):
                continue
            if not _bool_str_tuple_literal(statement.value):
                continue
            violations.append(
                Violation(
                    path=path,
                    line=statement.lineno,
                    rule="resolution outcome produced as (bool, str) tuple",
                    detail=(
                        f"'{node.name}' returns a (bool, str) tuple; a resolution result must be "
                        "the sealed StopResolutionOutcome union"
                    ),
                )
            )
    return violations


def _exhaustive_match_violations(rel: str, tree: ast.Module) -> list[Violation]:
    violations: list[Violation] = []
    for node in _walked_nodes(tree):
        if not isinstance(node, ast.Match):
            continue
        matched_classes: set[str] = set()
        has_fallback = False
        for case in node.cases:
            for pattern in ast.walk(case.pattern):
                if isinstance(pattern, ast.MatchClass):
                    cls_name = (
                        pattern.cls.id
                        if isinstance(pattern.cls, ast.Name)
                        else (pattern.cls.attr if isinstance(pattern.cls, ast.Attribute) else "")
                    )
                    if cls_name in _SEALED_OUTCOME_CLASSES:
                        matched_classes.add(cls_name)
                elif isinstance(pattern, ast.MatchAs) and pattern.pattern is None:
                    has_fallback = True
        if matched_classes and not has_fallback and matched_classes != _SEALED_OUTCOME_CLASSES:
            missing = sorted(_SEALED_OUTCOME_CLASSES - matched_classes)
            violations.append(
                Violation(
                    path=rel,
                    line=node.lineno,
                    rule="non-exhaustive outcome match",
                    detail=(
                        "match statement over StopResolutionOutcome is non-exhaustive; "
                        f"missing variants: {', '.join(missing)} without a fallback case"
                    ),
                )
            )
    return violations


def _package_legacy_violations(rel: str, source: str, tree: ast.Module) -> list[Violation]:
    package_key = _package_key(rel)
    identifier_lines = _identifier_lines(tree)
    violations = [
        *_forbidden_identifier_violations(
            rel,
            identifier_lines,
            _DELETED_PROCESS_LOCAL_STATE_NAMES,
            "deleted process-local state reappeared",
            "the run-loop process-local dedupe and waiting-state globals were deleted; "
            "the durable resolution attempt key is the only 'state changed' definition",
        ),
        *_forbidden_identifier_violations(
            rel,
            identifier_lines,
            _LEGACY_CONFLICT_CLASSIFIER_NAMES,
            "legacy conflict-attempt classifier reappeared",
            "'classify_conflict_attempt' was deleted; conflict attempts are classified by "
            "ralph/recovery/agent_failure_ledger.py and must not be re-added",
        ),
        *_forbidden_identifier_violations(
            rel,
            identifier_lines,
            _INTEGRATION_SIDE_FAILURE_CLASSIFIER_NAMES,
            "integration-side failure classification",
            "agent failure classification is owned by ralph/recovery/agent_failure_ledger.py "
            "and must not be re-added to integration",
        ),
        *_forbidden_identifier_violations(
            rel,
            identifier_lines,
            _LEGACY_DEAD_OUTCOME_MODULE_NAMES,
            "legacy resolution outcome module reappeared",
            "legacy dead outcome modules must stay deleted; the canonical sealed union lives "
            "in ralph/pipeline/resolution_outcome.py",
        ),
        *_forbidden_identifier_violations(
            rel,
            identifier_lines,
            _REMOVED_RESOLUTION_CAP_NAMES,
            "removed resolution cap identifier reappeared",
            "'total_resolution_cap_seconds' was removed from the resolver configuration; "
            "the only surviving cap is the watchdog reason OPERATOR_CAP_REACHED",
        ),
        *_forbidden_identifier_violations(
            rel,
            identifier_lines,
            _FORBIDDEN_RESOLUTION_SYMBOLS,
            "deleted legacy resolution symbol reappeared",
            "'ExhaustedResolutionFailure' and '_controller_for_key' were deleted; use the "
            "sealed RetryLaterOutcome(terminal_reason=NO_RUNNABLE_CANDIDATE) and the "
            "session/pipeline_deps recovery controller chain",
        ),
        *_forbidden_identifier_violations(
            rel,
            identifier_lines,
            _LEGACY_GITLINK_ESCALATION_NAMES,
            "legacy gitlink escalation hook reappeared",
            "'escalate_gitlink_on_sight' was removed; gitlink conflicts are resolved in place by "
            "ralph/pipeline/conflict_resolution/deterministic_resolution.py",
        ),
    ]
    if package_key.startswith("ralph/pipeline/"):
        violations.extend(
            _forbidden_identifier_violations(
                rel,
                identifier_lines,
                _LEGACY_OUTCOME_CLASS_NAMES,
                "legacy resolution outcome vocabulary reappeared",
                "'ResolutionOutcome' was replaced by the sealed StopResolutionOutcome union in "
                "ralph/pipeline/resolution_outcome.py",
            )
        )
        if any(token in source for token in _RESOLUTION_OUTCOME_TOKENS):
            violations.extend(_legacy_tuple_violations(rel, tree))
    if package_key in _LEGACY_OUTCOME_MODULE_PATHS:
        violations.append(
            Violation(
                path=rel,
                line=1,
                rule="legacy resolution outcome module reappeared",
                detail=(
                    f"{package_key} must stay deleted; the canonical sealed union lives "
                    "in ralph/pipeline/resolution_outcome.py"
                ),
            )
        )
    return violations


def _is_transitional_allowed(violation: Violation) -> bool:
    allowed_rules = TRANSITIONAL_PENDING_PRODUCERS.get(violation.path)
    if not allowed_rules:
        return False
    if violation.rule in allowed_rules:
        return True
    return any(name in violation.detail for name in allowed_rules)


def resolve_repository_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / _PACKAGE_PROJECT_DIR_NAME / _PACKAGE_DIR_NAME).is_dir():
            return candidate
    for candidate in (current, *current.parents):
        if (candidate / _PACKAGE_DIR_NAME).is_dir():
            return candidate
    return current


def _sealed_outcome_tuple_protocol_violations(rel: str, node: ast.ClassDef) -> list[Violation]:
    if node.name not in _SEALED_OUTCOME_CLASSES:
        return []
    legacy_member = _legacy_protocol_member(node)
    if legacy_member is None or not legacy_member.startswith("tuple protocol"):
        return []
    return [
        Violation(
            rel,
            node.lineno,
            "sealed outcome retains legacy tuple vocabulary",
            detail=(
                f"{node.name} exposes {legacy_member}; sealed variants must not "
                "implement tuple unpacking, indexing, or length"
            ),
        )
    ]


def _classdef_pipeline_violations(rel: str, node: ast.ClassDef) -> list[Violation]:
    violations: list[Violation] = []
    if node.name == "RebaseResolutionResult" and _tuple_subclass(node):
        violations.append(
            Violation(rel, node.lineno, "resolution result retains legacy tuple vocabulary")
        )
    if node.name in _RESULT_RESULT_CLASS_NAMES:
        legacy_member = _legacy_protocol_member(node)
        if legacy_member is not None:
            violations.append(
                Violation(
                    rel,
                    node.lineno,
                    "resolution result retains legacy tuple vocabulary",
                    detail=(
                        f"{node.name} exposes {legacy_member}; results must expose only the "
                        "typed sealed-outcome surface"
                    ),
                )
            )
    violations.extend(_sealed_outcome_tuple_protocol_violations(rel, node))
    return violations


def _pipeline_ast_violations(rel: str, tree: ast.Module) -> list[Violation]:
    violations: list[Violation] = []
    package_key = _package_key(rel)
    for node in _walked_nodes(tree):
        if isinstance(node, ast.Assign) and _resolution_call(node.value):
            violations.extend(
                Violation(rel, node.lineno, "tuple-shaped resolution result unpack")
                for target in node.targets
                if isinstance(target, ast.Tuple)
            )
        if isinstance(node, ast.ClassDef):
            violations.extend(_classdef_pipeline_violations(rel, node))
        if isinstance(node, ast.Compare) and _string_cause_match(node):
            violations.append(Violation(rel, node.lineno, "string matching on failure cause"))
        if isinstance(node, ast.Call):
            callee = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else (node.func.attr if isinstance(node.func, ast.Attribute) else "")
            )
            if (
                callee == "_resolve_rebase_with_config"
                and package_key != "ralph/pipeline/_stop_resolution_entry.py"
            ) or (
                callee == "_resolve_rebase_in_progress"
                and package_key not in _ALLOWED_PRIVATE_RESOLVER_CALLERS
            ):
                violations.append(
                    Violation(
                        rel,
                        node.lineno,
                        "private resolver caller outside entry",
                        detail=(
                            f"'{callee}' is a private resolver and may only be called "
                            "by the resolution entry"
                        ),
                    )
                )
        if isinstance(node, ast.Assign) or (
            isinstance(node, ast.AnnAssign) and node.value is not None
        ):
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                targets.extend(node.targets)
            else:
                targets.append(node.target)
            for assign_target in targets:
                for part in ast.walk(assign_target):
                    target_id = (
                        part.id
                        if isinstance(part, ast.Name)
                        else (part.attr if isinstance(part, ast.Attribute) else None)
                    )
                    if target_id in _LOCAL_COOLDOWN_WRITE_TARGETS:
                        violations.append(
                            Violation(
                                rel,
                                node.lineno,
                                "local cooldown write outside shared ledger",
                                detail=(
                                    f"local write to '{target_id}' violates shared "
                                    "cooldown ownership"
                                ),
                            )
                        )
    violations.extend(_exhaustive_match_violations(rel, tree))
    return violations


def audit_modules(
    modules: Mapping[str, str],
    *,
    ignore_transitional: bool = False,
) -> list[Violation]:
    violations: list[Violation] = []
    for rel, source in sorted(modules.items()):
        if rel in _SELF_AUDIT_MODULES or _is_exempt_path(rel):
            continue
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            tree = None
        if tree is not None:
            if _package_key(rel).startswith("ralph/pipeline/"):
                violations.extend(_pipeline_ast_violations(rel, tree))
                violations.extend(_operator_parking_state_violations(rel, tree))
            violations.extend(_package_legacy_violations(rel, source, tree))
        violations.extend(_retired_review_violations(rel, source, tree))
        violations.extend(_retired_three_sha_message_violations(rel, source))
        violations.extend(_operator_parking_literal_violations(rel, source))

    if ignore_transitional:
        return violations
    return [v for v in violations if not _is_transitional_allowed(v)]


def _iter_scannable_files(repo_root: Path) -> Iterator[tuple[str, Path]]:
    ws = FsWorkspace(repo_root)
    for rel_str in sorted(ws.iter_files("")):
        rel_path = PurePath(rel_str)
        if any(part in _SKIP_DIR_NAMES for part in rel_path.parts):
            continue
        if any(part.startswith(("wt-", "worktree-agent-")) for part in rel_path.parts):
            continue
        yield rel_path.as_posix(), repo_root / rel_str


def audit_tree(repo_root: Path, *, ignore_transitional: bool = False) -> list[Violation]:
    modules: dict[str, str] = {}
    ralph_dir = repo_root / _PACKAGE_DIR_NAME
    package_dir = repo_root / _PACKAGE_PROJECT_DIR_NAME / _PACKAGE_DIR_NAME
    if not ralph_dir.is_dir() and package_dir.is_dir():
        ralph_dir = package_dir

    if ralph_dir.is_dir():
        for path in sorted(ralph_dir.rglob("*.py")):
            rel = path.relative_to(repo_root).as_posix()
            if rel in _SELF_AUDIT_MODULES or _is_exempt_path(rel):
                continue
            try:
                modules[rel] = path.read_text(encoding="utf-8")
            except OSError:
                continue

    violations = list(audit_modules(modules, ignore_transitional=ignore_transitional))

    repo_wide_violations: list[Violation] = []
    for rel, path in _iter_scannable_files(repo_root):
        if rel in _SELF_AUDIT_MODULES or _is_exempt_path(rel):
            continue
        if rel in modules:
            continue
        if _RETIRED_REVIEW_PATTERN.search(path.name):
            repo_wide_violations.append(
                Violation(
                    path=rel,
                    line=1,
                    rule=_RETIRED_REVIEW_RULE,
                    detail=f"filename '{path.name}' contains legacy {_RETIRED_STEM} spelling",
                )
            )
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _retired_review_candidate(source):
            tree = None
            if path.suffix == ".py":
                try:
                    tree = ast.parse(source, filename=rel)
                except SyntaxError:
                    tree = None
            repo_wide_violations.extend(_retired_review_violations(rel, source, tree))
    violations.extend(repo_wide_violations)
    return violations


def _is_object_callable(value: object) -> TypeGuard[Callable[..., object]]:
    return callable(value)


def _deleted_process_local_state_selftest() -> int:
    clean_violations = audit_modules(
        {
            "ralph/pipeline/run_loop.py": "def _resolve_paused_rebase():\n    return None\n",
        }
    )
    if clean_violations:
        sys.stdout.write(f"SELFTEST FAILED: clean module produced violations: {clean_violations}\n")
        return 1
    planted_cases: list[tuple[str, str]] = [
        ("_retained_round_fingerprints", "def _retained_round_fingerprints():\n    return {}\n"),
        ("_WAITING_STATE", "_WAITING_STATE = {}\n"),
    ]
    for expected, source in planted_cases:
        violations = audit_modules({"ralph/pipeline/run_loop.py": source})
        rules = [violation.rule for violation in violations]
        if "deleted process-local state reappeared" not in rules:
            sys.stdout.write(
                f"SELFTEST FAILED: '{expected}' did not produce the deleted-state rule, "
                f"got {rules}\n"
            )
            return 1
    return 0


def selftest() -> int:
    module = importlib.import_module("ralph.testing._integration_no_legacy_selftest")
    run_selftest = module_attr(module, "selftest")
    if not _is_object_callable(run_selftest):
        raise TypeError("expected callable selftest on no-legacy selftest module")
    result = run_selftest()
    if not isinstance(result, int) or isinstance(result, bool):
        raise TypeError("expected int exit code from no-legacy selftest")
    if result != 0:
        return result
    return _deleted_process_local_state_selftest()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--selftest" in args:
        return selftest()
    root = Path(args[0]) if args else resolve_repository_root(Path.cwd())
    violations = audit_tree(root)
    if violations:
        sys.stdout.write(f"INTEGRATION NO-LEGACY AUDIT: {len(violations)} violation(s) found:\n")
        for violation in violations:
            sys.stdout.write(f"  {violation}\n")
        return 1
    sys.stdout.write("INTEGRATION NO-LEGACY AUDIT: OK\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
