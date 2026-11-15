from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Final

from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

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
_REMOVED_RESOLUTION_CAP_NAMES: frozenset[str] = frozenset({"total_resolution_cap_seconds"})
_LEGACY_GITLINK_ESCALATION_NAMES: frozenset[str] = frozenset({"escalate_gitlink_on_sight"})
_LEGACY_OUTCOME_CLASS_NAMES: frozenset[str] = frozenset({"ResolutionOutcome"})
LEGACY_OUTCOME_MODULE: str = "ralph/pipeline/conflict_resolution/resolution_outcome.py"
_SELF_AUDIT_MODULES: frozenset[str] = frozenset(
    {
        "ralph/testing/audit_integration_invariants.py",
        "ralph/testing/audit_integration_no_legacy.py",
        "tests/audit/test_audit_integration_no_legacy.py",
    }
)
_EXEMPT_HISTORICAL_PATH_FRAGMENTS: tuple[str, ...] = (
    "tests/agents/invoke/fixtures/claude_interactive_real_capture",
)
_SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".agent",
        ".agents",
        ".claude",
        ".git",
        ".hypothesis",
        ".mypy_cache",
        ".pi-subagents",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".vscode",
        "build",
        "dist",
        "node_modules",
        "tmp",
        "venv",
        "__pycache__",
    }
)
_RETIRED_STEM: Final = "commit_" + "cleanup"
_RETIRED_HYPHEN_STEM: Final = "commit-" + "cleanup"
_RETIRED_REVIEW_PATTERN: Final = re.compile(
    rf"(?i)\b[a-zA-Z0-9_-]*commit[-_]?{'clean' + 'up'}[a-zA-Z0-9_.-]*\b"
)
_RETIRED_REVIEW_RULE: Final = f"legacy {_RETIRED_STEM} identifier reappeared"
_RETIRED_REVIEW_DETAIL: Final = f"'{{token}}' was replaced by 'commit_review'; legacy {_RETIRED_STEM} identifiers must not be used"
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
    }
)
_LOCAL_COOLDOWN_WRITE_TARGETS: Final[frozenset[str]] = frozenset(
    {
        "cooldown_until_ms",
        "cooldown_until_epoch_ms",
        "_cooldown_until",
    }
)


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str
    detail: str = ""

    def __str__(self) -> str:
        if self.detail:
            return f"{self.path}:{self.line}: {self.rule}: {self.detail}"
        return f"{self.path}:{self.line}: {self.rule}"


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
    for node in ast.walk(tree):
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
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript) and _bool_str_tuple_annotation(node)
    ]
    for node in ast.walk(tree):
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


def _package_legacy_violations(rel: str, source: str, tree: ast.Module) -> list[Violation]:
    package_key = _package_key(rel)
    identifier_lines = _identifier_lines(tree)
    violations = [
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
            _REMOVED_RESOLUTION_CAP_NAMES,
            "removed resolution cap identifier reappeared",
            "'total_resolution_cap_seconds' was removed from the resolver configuration; "
            "the only surviving cap is the watchdog reason OPERATOR_CAP_REACHED",
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
    if package_key == LEGACY_OUTCOME_MODULE:
        violations.append(
            Violation(
                path=rel,
                line=1,
                rule="legacy resolution outcome module reappeared",
                detail=(
                    f"{LEGACY_OUTCOME_MODULE} must stay deleted; the canonical sealed union lives "
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


def _package_key(rel: str) -> str:
    return rel.removeprefix(_PACKAGE_PROJECT_DIR_NAME + "/")


def resolve_repository_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / _PACKAGE_PROJECT_DIR_NAME / _PACKAGE_DIR_NAME).is_dir():
            return candidate
    for candidate in (current, *current.parents):
        if (candidate / _PACKAGE_DIR_NAME).is_dir():
            return candidate
    return current


def _pipeline_ast_violations(rel: str, tree: ast.Module) -> list[Violation]:
    violations: list[Violation] = []
    package_key = _package_key(rel)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _resolution_call(node.value):
            violations.extend(
                Violation(rel, node.lineno, "tuple-shaped resolution result unpack")
                for target in node.targets
                if isinstance(target, ast.Tuple)
            )
        if (
            isinstance(node, ast.ClassDef)
            and node.name == "RebaseResolutionResult"
            and _tuple_subclass(node)
        ):
            violations.append(
                Violation(rel, node.lineno, "resolution result retains legacy tuple vocabulary")
            )
        if isinstance(node, ast.Compare) and _string_cause_match(node):
            violations.append(Violation(rel, node.lineno, "string matching on failure cause"))
        if isinstance(node, ast.Call) and package_key not in _ALLOWED_PRIVATE_RESOLVER_CALLERS:
            callee = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else (node.func.attr if isinstance(node.func, ast.Attribute) else "")
            )
            if callee in _PRIVATE_RESOLVER_NAMES:
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
    return violations


def _is_exempt_path(rel: str) -> bool:
    normalized = rel.replace("\\", "/")
    return bool(
        any(fragment in normalized for fragment in _EXEMPT_HISTORICAL_PATH_FRAGMENTS)
        or any(normalized.endswith(mod) for mod in _SELF_AUDIT_MODULES)
        or normalized == ("PROMPT" + ".md")
        or "/.agent/" in normalized
        or normalized.startswith(".agent/")
        or "/.pi-subagents/" in normalized
        or normalized.startswith(".pi-subagents/")
    )


def _retired_review_violations(rel: str, source: str) -> list[Violation]:
    if _is_exempt_path(rel):
        return []
    violations: list[Violation] = []
    filename = Path(rel).name
    if _RETIRED_REVIEW_PATTERN.search(filename):
        violations.append(
            Violation(
                path=rel,
                line=1,
                rule=_RETIRED_REVIEW_RULE,
                detail=f"filename '{filename}' contains legacy {_RETIRED_STEM} spelling",
            )
        )
    lowered = source.lower()
    if (
        _RETIRED_STEM not in lowered
        and _RETIRED_HYPHEN_STEM not in lowered
        and ("commit" + "cleanup") not in lowered
    ):
        return violations

    for lineno, line in enumerate(source.splitlines(), start=1):
        match = _RETIRED_REVIEW_PATTERN.search(line)
        if match:
            violations.append(
                Violation(
                    path=rel,
                    line=lineno,
                    rule=_RETIRED_REVIEW_RULE,
                    detail=_RETIRED_REVIEW_DETAIL.format(token=match.group(0)),
                )
            )
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
            violations.extend(_package_legacy_violations(rel, source, tree))
        violations.extend(_retired_review_violations(rel, source))

    if ignore_transitional:
        return violations
    return [v for v in violations if not _is_transitional_allowed(v)]


def _iter_scannable_files(repo_root: Path) -> Iterator[tuple[str, Path]]:
    ws = FsWorkspace(repo_root)
    for rel_str in sorted(ws.iter_files("")):
        rel_path = PurePath(rel_str)
        if any(part in _SKIP_DIR_NAMES for part in rel_path.parts):
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
        repo_wide_violations.extend(_retired_review_violations(rel, source))

    violations.extend(repo_wide_violations)
    return violations


def selftest() -> int:
    clean_modules = {
        "ralph/pipeline/clean_sealed.py": (
            "from ralph.pipeline.resolution_outcome import StopResolutionOutcome, ResolvedOutcome\n"
            "def resolve_work() -> StopResolutionOutcome:\n"
            "    return ResolvedOutcome()\n"
        ),
        "ralph/pipeline/clean_review.py": (
            "phase = 'development_commit_review'\ndef commit_review(): pass\n"
        ),
    }
    clean_violations = audit_modules(clean_modules, ignore_transitional=True)
    if clean_violations:
        sys.stdout.write(
            f"SELFTEST FAILED: clean modules produced violations: {clean_violations}\n"
        )
        return 1

    planted_cases: list[tuple[str, str, str]] = [
        (
            "ralph/pipeline/planted_classifier.py",
            "def test(): return classify_conflict_attempt()\n",
            "legacy conflict-attempt classifier reappeared",
        ),
        (
            "ralph/pipeline/planted_failure_classifier.py",
            "def classify_agent_failure(): pass\n",
            "integration-side failure classification",
        ),
        (
            "ralph/pipeline/planted_private_resolver.py",
            "def test(): return _resolve_rebase_in_progress()\n",
            "private resolver caller outside entry",
        ),
        (
            "ralph/pipeline/planted_cooldown_write.py",
            "def test():\n    record.cooldown_until_ms = 5000\n",
            "local cooldown write outside shared ledger",
        ),
        (
            "ralph/pipeline/planted_cap.py",
            "total_resolution_cap_seconds = 300\n",
            "removed resolution cap identifier reappeared",
        ),
        (
            "ralph/pipeline/planted_escalate.py",
            "def test(): return escalate_gitlink_on_sight()\n",
            "legacy gitlink escalation hook reappeared",
        ),
        (
            "ralph/pipeline/planted_outcome_class.py",
            "from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome\n",
            "legacy resolution outcome vocabulary reappeared",
        ),
        (
            "ralph/pipeline/planted_tuple_ret.py",
            "StopResolutionOutcome = None\ndef resolve_outcome() -> tuple[bool, str]:\n    return (True, '')\n",
            "resolution outcome typed as (bool, str) tuple",
        ),
        (
            "ralph/pipeline/planted_unpack.py",
            "success, reason = attempt_stop_resolution()\n",
            "tuple-shaped resolution result unpack",
        ),
        (
            "ralph/pipeline/planted_subclass.py",
            "class RebaseResolutionResult(tuple):\n    pass\n",
            "resolution result retains legacy tuple vocabulary",
        ),
        (
            "ralph/pipeline/planted_str_cause.py",
            "if str(failure_cause) == 'conflict':\n    pass\n",
            "string matching on failure cause",
        ),
        (
            LEGACY_OUTCOME_MODULE,
            "class ResolutionOutcome: pass\n",
            "legacy resolution outcome module reappeared",
        ),
        (
            "ralph/pipeline/planted_cleanup.py",
            f"def test(): return {_RETIRED_STEM}()\n",
            _RETIRED_REVIEW_RULE,
        ),
        (
            "ralph/pipeline/planted_cleanup_phase.py",
            f"phase = 'development_{_RETIRED_STEM}'\n",
            _RETIRED_REVIEW_RULE,
        ),
        (
            "ralph/pipeline/planted_cleanup_skill.py",
            f"skill = 'submit-{_RETIRED_HYPHEN_STEM}-artifact'\n",
            _RETIRED_REVIEW_RULE,
        ),
    ]

    for rel, source, expected_rule in planted_cases:
        violations = audit_modules({rel: source}, ignore_transitional=True)
        rules = [v.rule for v in violations]
        if expected_rule not in rules:
            sys.stdout.write(
                f"SELFTEST FAILED: {rel} did not produce rule '{expected_rule}', got {rules}\n"
            )
            return 1

    sys.stdout.write(
        f"INTEGRATION NO-LEGACY AUDIT: SELFTEST OK ({len(clean_modules) + len(planted_cases)} cases verified)\n"
    )
    return 0


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
