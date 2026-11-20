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


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


_UNMERGED_LS_FILES_READERS = frozenset(
    {"ralph/git/conflict_stage_entries.py", "ralph/git/merge.py"}
)
_UNMERGED_XY_OWNER = "ralph/git/hardening.py"
_AUDIT_MODULE = "ralph/testing/audit_integration_single_source.py"
_XY_DEFINITION_OWNERS = frozenset({_UNMERGED_XY_OWNER, _AUDIT_MODULE})
_SANCTIONED_XY_CONSUMERS = frozenset(
    {
        _UNMERGED_XY_OWNER,
        _AUDIT_MODULE,
        "ralph/pipeline/_porcelain_records.py",
        "ralph/pipeline/integration_cleanup.py",
    }
)
_XY_SET_NAME = "UNMERGED_PORCELAIN_XY"
_SET_CONSTRUCTOR_NAMES = frozenset({"frozenset", "set"})
_UNMERGED_XY_CODES: frozenset[str] = frozenset({"UU", "AA", "DD", "AU", "UA", "DU", "UD"})
_OPERATION_OWNER = "ralph/git/operation_snapshot.py"
_ELIGIBILITY_OWNER = "ralph/recovery/agent_failure_ledger.py"
_LANDED_OWNER = "ralph/pipeline/_landed_predicate.py"
_OPERATION_DEFINITIONS = frozenset({"operation_state", "observe_operation_snapshot"})
_ELIGIBILITY_DEFINITIONS = frozenset({"agent_is_eligible", "is_agent_eligible"})
_LANDED_DEFINITIONS = frozenset(
    {
        "operation_landed",
        "resolved_stop_landed",
        "_landing_reached",
    }
)
_RETIRED_PORCELAIN_PATH_EXTRACTORS = frozenset({"porcelain_unmerged_paths"})
_UNMERGED_PATH_SET_OWNER = "ralph/git/merge.py"
_UNMERGED_PATH_SET_DEFINITIONS = frozenset({"observe_unmerged_paths", "unmerged_paths"})
_LANDED_WRAPPER = "ralph/pipeline/landed_verdict.py"
_LANDED_PREDICATE_NAME = "operation_landed"
_MINIMUM_ALLOWLISTED_SHA_FIELDS = 2
_RECORDED_OPERATION_SHA_FIELDS = frozenset(
    {
        "pre_feature_sha",
        "pre_target_sha",
        "integrated_feature_sha",
    }
)


def _string_literals(node: ast.AST) -> set[str]:
    return {
        value.value
        for value in ast.walk(node)
        if isinstance(value, ast.Constant) and isinstance(value.value, str)
    }


def _unmerged_xy_code_literals(node: ast.expr) -> bool:
    return bool(_UNMERGED_XY_CODES & _string_literals(node))


def _is_xy_set_name(node: ast.expr, xy_names: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in xy_names
    if isinstance(node, ast.Attribute):
        return node.attr == _XY_SET_NAME
    return False


def _unmerged_xy_comparisons(node: ast.Compare, xy_names: set[str]) -> bool:
    if any(_is_xy_set_name(comparator, xy_names) for comparator in node.comparators):
        return True
    return _unmerged_xy_code_literals(node)


def _unmerged_xy_set_definition(node: ast.Assign) -> bool:
    value = node.value
    if isinstance(value, ast.Set):
        return _unmerged_xy_code_literals(value)
    if isinstance(value, ast.Call) and _is_set_constructor(value.func):
        return _unmerged_xy_code_literals(value)
    return False


def _is_set_constructor(func: ast.expr) -> bool:
    if isinstance(func, ast.Name):
        return func.id in _SET_CONSTRUCTOR_NAMES
    if isinstance(func, ast.Attribute):
        return func.attr in _SET_CONSTRUCTOR_NAMES
    return False


def _imported_xy_aliases(tree: ast.AST) -> set[str]:
    aliases = {_XY_SET_NAME}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module != "ralph.git.hardening":
                continue
            for alias in node.names:
                if alias.name == _XY_SET_NAME:
                    aliases.add(alias.asname or alias.name)
    return aliases


def _module_level_functions(
    tree: ast.AST,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    if not isinstance(tree, ast.Module):
        return []
    return [node for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]


def _call_unmerged_index_violations(rel: str, node: ast.Call) -> list[Violation]:
    literals = _string_literals(node)
    if rel in _UNMERGED_LS_FILES_READERS:
        return []
    found: list[Violation] = []
    if "ls-files" in literals and ({"-u", "--unmerged"} & literals):
        found.append(Violation(rel, node.lineno, "unmerged index read outside git owner"))
    if any(
        isinstance(value, str) and value.lstrip().startswith("--diff-filter=" + "U")
        for value in literals
    ):
        found.append(Violation(rel, node.lineno, "unmerged index read outside git owner"))
    return found


def _xy_set_violations(rel: str, node: ast.AST, xy_names: set[str]) -> list[Violation]:
    if (
        isinstance(node, ast.Compare)
        and rel not in _SANCTIONED_XY_CONSUMERS
        and _unmerged_xy_comparisons(node, xy_names)
    ):
        return [Violation(rel, node.lineno, "porcelain unmerged XY set has a second definition")]
    if (
        isinstance(node, ast.Assign)
        and rel not in _XY_DEFINITION_OWNERS
        and _unmerged_xy_set_definition(node)
    ):
        return [Violation(rel, node.lineno, "porcelain unmerged XY set has a second definition")]
    return []


def _named_function_violations(
    rel: str, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> list[Violation]:
    found: list[Violation] = []
    if node.name in _OPERATION_DEFINITIONS and rel != _OPERATION_OWNER:
        found.append(Violation(rel, node.lineno, "operation state has a second reader"))
    if node.name in _ELIGIBILITY_DEFINITIONS and rel != _ELIGIBILITY_OWNER:
        found.append(Violation(rel, node.lineno, "agent eligibility owner is the ledger"))
    if node.name in _LANDED_DEFINITIONS and rel != _LANDED_OWNER:
        found.append(Violation(rel, node.lineno, "operation landed has a second predicate"))
    if node.name in _RETIRED_PORCELAIN_PATH_EXTRACTORS:
        found.append(Violation(rel, node.lineno, "porcelain unmerged path extraction is retired"))
    return found


def _module_level_unmerged_path_set_violations(rel: str, tree: ast.AST) -> list[Violation]:
    if rel == _UNMERGED_PATH_SET_OWNER:
        return []
    return [
        Violation(rel, node.lineno, "unmerged path set has a second reader")
        for node in _module_level_functions(tree)
        if node.name in _UNMERGED_PATH_SET_DEFINITIONS
    ]


def _recorded_operation_sha_fields(node: ast.AST) -> set[str]:
    return {
        part.attr
        for part in ast.walk(node)
        if isinstance(part, ast.Attribute) and part.attr in _RECORDED_OPERATION_SHA_FIELDS
    }


def _landed_sha_allowlist_violations(rel: str, tree: ast.Module) -> list[Violation]:
    if rel == _LANDED_OWNER:
        return []
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, ast.In | ast.NotIn) for op in node.ops):
            continue
        for comparator in node.comparators:
            if not isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                continue
            fields = _recorded_operation_sha_fields(comparator)
            if len(fields) < _MINIMUM_ALLOWLISTED_SHA_FIELDS:
                continue
            violations.append(Violation(rel, node.lineno, "operation landed has a SHA allowlist"))
    return violations


def _is_boolean_expression(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, bool)
    if isinstance(node, ast.Compare | ast.BoolOp):
        return True
    if isinstance(node, ast.UnaryOp):
        return isinstance(node.op, ast.Not)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id in {"isinstance", "issubclass"}
        if isinstance(func, ast.Attribute):
            return func.attr in {"isinstance", "issubclass"}
    return False


def _returns_bool(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    annotation = node.returns
    if isinstance(annotation, ast.Name) and annotation.id == "bool":
        return True
    returns = [child for child in ast.walk(node) if isinstance(child, ast.Return)]
    if not returns:
        return False
    return all(child.value is not None and _is_boolean_expression(child.value) for child in returns)


def _has_record_parameter(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    for argument in arguments:
        if argument.arg == "record":
            return True
        annotation = argument.annotation
        if isinstance(annotation, ast.Name) and annotation.id == "IntegrationRecord":
            return True
        if isinstance(annotation, ast.Constant) and annotation.value == "IntegrationRecord":
            return True
    return False


def _references_landed_predicate(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for part in ast.walk(node):
        if isinstance(part, ast.Name) and part.id == _LANDED_PREDICATE_NAME:
            return True
        if isinstance(part, ast.Attribute) and part.attr == _LANDED_PREDICATE_NAME:
            return True
    return False


def _landed_second_predicate_violations(rel: str, tree: ast.Module) -> list[Violation]:
    if rel in {_LANDED_OWNER, _LANDED_WRAPPER}:
        return []
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if "landed" not in node.name.lower():
            continue
        if not _returns_bool(node):
            continue
        if not _has_record_parameter(node):
            continue
        if _references_landed_predicate(node):
            continue
        violations.append(Violation(rel, node.lineno, "operation landed has a second predicate"))
    return violations


def audit_modules(modules: Mapping[str, str]) -> list[Violation]:
    violations: list[Violation] = []
    for rel, source in sorted(modules.items()):
        if not rel.startswith("ralph/"):
            continue
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            continue
        xy_names = _imported_xy_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                violations.extend(_call_unmerged_index_violations(rel, node))
            violations.extend(_xy_set_violations(rel, node, xy_names))
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                violations.extend(_named_function_violations(rel, node))
        violations.extend(_module_level_unmerged_path_set_violations(rel, tree))
        violations.extend(_landed_sha_allowlist_violations(rel, tree))
        violations.extend(_landed_second_predicate_violations(rel, tree))
    return violations


def audit_tree(repo_root: Path) -> list[Violation]:
    modules: dict[str, str] = {}
    for path in sorted((repo_root / "ralph").rglob("*.py")):
        rel = path.relative_to(repo_root).as_posix()
        try:
            modules[rel] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    return audit_modules(modules)


def selftest() -> int:
    clean_modules = {
        "ralph/git/conflict_stage_entries.py": 'run_git(["ls-files", "-u"])\n',
        "ralph/git/operation_snapshot.py": "def operation_state(): pass\n",
        "ralph/recovery/agent_failure_ledger.py": "def is_agent_eligible(): pass\n",
        "ralph/pipeline/_landed_predicate.py": "def operation_landed(): pass\n",
        "ralph/pipeline/landed_verdict.py": "def landed_verdict(): pass\n",
        "ralph/pipeline/landed_delegate.py": (
            "def _work_landed(root, record):\n"
            "    from ralph.pipeline._landed_predicate import operation_landed\n"
            "    return operation_landed(root, record).landed\n"
        ),
        "ralph/git/merge.py": "def observe_unmerged_paths(): pass\ndef unmerged_paths(): pass\n",
    }
    clean_violations = audit_modules(clean_modules)
    if clean_violations:
        sys.stdout.write(
            f"SELFTEST FAILED: clean modules produced violations: {clean_violations}\n"
        )
        return 1

    planted_cases: list[tuple[str, str, str]] = [
        (
            "ralph/pipeline/unmerged_probe.py",
            'run_git(["ls-files", "-u"])\n',
            "unmerged index read outside git owner",
        ),
        (
            "ralph/pipeline/unmerged_diff_probe.py",
            'run_git(["diff", "--diff-filter=U", "--name-only"])\n',
            "unmerged index read outside git owner",
        ),
        (
            "ralph/pipeline/state_reader.py",
            "def operation_state(): pass\n",
            "operation state has a second reader",
        ),
        (
            "ralph/pipeline/agent_checker.py",
            "def is_agent_eligible(): pass\n",
            "agent eligibility owner is the ledger",
        ),
        (
            "ralph/pipeline/land_checker.py",
            "def operation_landed(): pass\n",
            "operation landed has a second predicate",
        ),
        (
            "ralph/pipeline/land_checker.py",
            "def _landing_reached(): pass\n",
            "operation landed has a second predicate",
        ),
        (
            "ralph/pipeline/unmerged_xy_reader.py",
            'entry.xy in {"UU", "AA", "DD", "AU", "UA", "DU", "UD"}\n',
            "porcelain unmerged XY set has a second definition",
        ),
        (
            "ralph/pipeline/unmerged_xy_reader.py",
            'code = entry.xy\nif code[0] == "UU" or code[1] == "UU":\n    pass\n',
            "porcelain unmerged XY set has a second definition",
        ),
        (
            "ralph/pipeline/unmerged_xy_reader.py",
            (
                "from ralph.git.hardening import UNMERGED_PORCELAIN_XY\n"
                "entry.xy in UNMERGED_PORCELAIN_XY\n"
            ),
            "porcelain unmerged XY set has a second definition",
        ),
        (
            "ralph/pipeline/unmerged_xy_reader.py",
            'UNMERGED_PORCELAIN_XY = frozenset({"UU", "AA", "DD", "AU", "UA", "DU", "UD"})\n',
            "porcelain unmerged XY set has a second definition",
        ),
        (
            "ralph/pipeline/unmerged_xy_reader.py",
            'LOCAL_XY = frozenset({"UU", "AA"})\nentry.xy in LOCAL_XY\n',
            "porcelain unmerged XY set has a second definition",
        ),
        (
            "ralph/pipeline/_porcelain_records.py",
            "def porcelain_unmerged_paths(text):\n    return ()\n",
            "porcelain unmerged path extraction is retired",
        ),
        (
            "ralph/pipeline/integration_resolution.py",
            "def observe_unmerged_paths(root):\n    return ()\n",
            "unmerged path set has a second reader",
        ),
        (
            "ralph/pipeline/integration_resolution.py",
            "def unmerged_paths(root):\n    return []\n",
            "unmerged path set has a second reader",
        ),
        (
            "ralph/pipeline/landed_allowlist.py",
            (
                "def head_matches(record, head):\n"
                "    return head not in (\n"
                "        record.integrated_feature_sha,\n"
                "        record.pre_target_sha,\n"
                "        record.pre_feature_sha,\n"
                "    )\n"
            ),
            "operation landed has a SHA allowlist",
        ),
        (
            "ralph/pipeline/landed_second_predicate.py",
            (
                "def _recorded_work_landed(root, record):\n"
                "    return record.integrated_feature_sha is not None\n"
            ),
            "operation landed has a second predicate",
        ),
    ]

    sanctioned_modules = {
        "ralph/git/hardening.py": (
            'UNMERGED_PORCELAIN_XY = frozenset({"UU", "AA", "DD", "AU", "UA", "DU", "UD"})\n'
        ),
        "ralph/testing/audit_integration_single_source.py": (
            'UNMERGED_PORCELAIN_XY = frozenset({"UU", "AA"})\n'
            "if entry.xy in UNMERGED_PORCELAIN_XY:\n    pass\n"
        ),
        "ralph/pipeline/_porcelain_records.py": (
            "from ralph.git.hardening import UNMERGED_PORCELAIN_XY\n"
            "entry.xy in UNMERGED_PORCELAIN_XY\n"
        ),
        "ralph/pipeline/integration_cleanup.py": (
            "from ralph.git.hardening import UNMERGED_PORCELAIN_XY\n"
            "entry.xy in UNMERGED_PORCELAIN_XY\n"
        ),
    }
    sanctioned_violations = audit_modules(sanctioned_modules)
    if sanctioned_violations:
        sys.stdout.write(
            f"SELFTEST FAILED: sanctioned XY consumers produced violations: {sanctioned_violations}\n"
        )
        return 1

    for rel, source, expected_rule in planted_cases:
        violations = audit_modules({rel: source})
        rules = [v.rule for v in violations]
        if expected_rule not in rules:
            sys.stdout.write(
                f"SELFTEST FAILED: {rel} did not produce rule '{expected_rule}', got {rules}\n"
            )
            return 1

    sys.stdout.write(
        f"INTEGRATION SINGLE-SOURCE AUDIT: SELFTEST OK ({len(clean_modules) + len(planted_cases)} cases verified)\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--selftest" in args:
        return selftest()
    root = Path(args[0]) if args else Path(__file__).resolve().parents[2]
    violations = audit_tree(root)
    if violations:
        sys.stdout.write(
            f"INTEGRATION SINGLE-SOURCE AUDIT: {len(violations)} violation(s) found:\n"
        )
        for violation in violations:
            sys.stdout.write(f"  {violation}\n")
        return 1
    sys.stdout.write("INTEGRATION SINGLE-SOURCE AUDIT: OK\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
