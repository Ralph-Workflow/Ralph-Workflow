from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _read(rel_path: str) -> str:
    return (_PACKAGE_ROOT / rel_path).read_text(encoding="utf-8")


class Invariant:
    def __init__(
        self,
        *,
        rel_path: str,
        present: tuple[str, ...] = (),
        absent: tuple[str, ...] = (),
    ) -> None:
        self.rel_path = rel_path
        self.present = present
        self.absent = absent

    def violations(self) -> list[str]:
        content = _read(self.rel_path)
        missing = [
            f"{self.rel_path}: missing required literal {needle!r}"
            for needle in self.present
            if needle not in content
        ]
        forbidden = [
            f"{self.rel_path}: forbidden literal still present {needle!r}"
            for needle in self.absent
            if needle in content
        ]
        return [*missing, *forbidden]


_FAST_PATH_TARGET_FUNCTION: str = "_is_safe_to_delete"

_FAST_PATH_TARGET_SOURCE: str = "is_agent_internal_path(path)"


def _check_fast_path_placement() -> list[str]:
    problems: list[str] = []
    rel_path = "phases/commit_review.py"
    try:
        source = _read(rel_path)
    except FileNotFoundError:
        return [f"{rel_path}: file not found"]

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"{rel_path}: syntax error during AST parse: {exc}"]

    target: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == (
            _FAST_PATH_TARGET_FUNCTION
        ):
            target = node
            break

    if target is None:
        return [f"{rel_path}: function {_FAST_PATH_TARGET_FUNCTION!r} not found"]

    body = target.body
    if not body:
        return [
            f"{rel_path}: function {_FAST_PATH_TARGET_FUNCTION!r} has empty body -- "
            "fast-path placement cannot be verified"
        ]

    docstring_node = ast.get_docstring(target, clean=True)
    body_to_check = list(body)
    if docstring_node is not None and body_to_check:
        first = body_to_check[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            body_to_check = body_to_check[1:]

    if not body_to_check:
        return [
            f"{rel_path}: function {_FAST_PATH_TARGET_FUNCTION!r} body is empty after "
            "skipping the docstring -- fast-path placement cannot be verified"
        ]

    first_stmt = body_to_check[0]
    found = ast.unparse(first_stmt)
    if _FAST_PATH_TARGET_SOURCE not in found:
        problems.append(
            f"{rel_path}: function {_FAST_PATH_TARGET_FUNCTION!r} first executable "
            f"statement is {found!r}, expected to contain {_FAST_PATH_TARGET_SOURCE!r} "
            "(agent-internal fast path must run BEFORE Path(path)/path.lower()/suffix "
            "setup so it cannot be silently bypassed)"
        )

    return problems


_AUTO_SEED_TARGET_FUNCTION: str = "handle_commit_review_phase"

_AUTO_SEED_GITIGNORE_HELPER: str = "auto_seed_default_gitignore"
_AUTO_SEED_GITEXCLUDE_HELPER: str = "auto_seed_default_git_exclude"

_AUTO_SEED_PRIOR_ANCHOR: str = "ensure_git_initialized"
_AUTO_SEED_LATER_ANCHOR: str = "_load_commit_review_artifact"

_PRE_EMPTIVE_UNTRACK_HELPER: str = "untrack_engine_internal_files"


def _check_auto_seed_placement() -> list[str]:
    rel_path = "phases/commit_review.py"
    source = _read_or_synthesize_violation(rel_path)
    if isinstance(source, list):
        return source

    tree = _parse_source_or_synthesize_violation(rel_path, source)
    if isinstance(tree, list):
        return tree

    target = _find_target_function(tree)
    if isinstance(target, list):
        return target

    sites = _collect_call_sites(target)

    problems: list[str] = []
    problems.extend(_anchor_or_seed_missing_violations(rel_path, sites))
    if problems:
        return problems
    problems.extend(_ordering_violations(rel_path, sites))
    return problems


def _read_or_synthesize_violation(rel_path: str) -> str | list[str]:
    try:
        return _read(rel_path)
    except FileNotFoundError:
        return [f"{rel_path}: file not found"]


def _parse_source_or_synthesize_violation(rel_path: str, source: str) -> ast.AST | list[str]:
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        return [f"{rel_path}: syntax error during AST parse: {exc}"]


def _find_target_function(tree: ast.AST) -> ast.FunctionDef | ast.AsyncFunctionDef | list[str]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == (
            _AUTO_SEED_TARGET_FUNCTION
        ):
            return node
    return [
        f"phases/commit_review.py: function {_AUTO_SEED_TARGET_FUNCTION!r} not found -- "
        "auto-seed placement cannot be verified"
    ]


class _CallSites:
    def __init__(self) -> None:
        self.prior_line: int | None = None
        self.later_line: int | None = None
        self.gitignore_lines: list[int] = []
        self.gitexclude_lines: list[int] = []
        self.pre_emptive_untrack_lines: list[int] = []


def _collect_call_sites(target: ast.FunctionDef | ast.AsyncFunctionDef) -> _CallSites:
    sites = _CallSites()
    for stmt in ast.walk(target):
        if not isinstance(stmt, ast.Call):
            continue
        func = stmt.func
        if not isinstance(func, ast.Name):
            continue
        _record_call_site(sites, func.id, stmt.lineno)
    return sites


def _record_call_site(sites: _CallSites, name: str, lineno: int) -> None:
    if name == _AUTO_SEED_PRIOR_ANCHOR:
        if sites.prior_line is None or lineno < sites.prior_line:
            sites.prior_line = lineno
    elif name == _AUTO_SEED_LATER_ANCHOR:
        if sites.later_line is None or lineno > sites.later_line:
            sites.later_line = lineno
    elif name == _AUTO_SEED_GITIGNORE_HELPER:
        sites.gitignore_lines.append(lineno)
    elif name == _AUTO_SEED_GITEXCLUDE_HELPER:
        sites.gitexclude_lines.append(lineno)
    elif name == _PRE_EMPTIVE_UNTRACK_HELPER:
        sites.pre_emptive_untrack_lines.append(lineno)


def _anchor_or_seed_missing_violations(rel_path: str, sites: _CallSites) -> list[str]:
    problems: list[str] = []
    if sites.prior_line is None:
        problems.append(
            f"{rel_path}: function {_AUTO_SEED_TARGET_FUNCTION!r} does not call "
            f"{_AUTO_SEED_PRIOR_ANCHOR!r} -- cannot anchor auto-seed placement"
        )
    if sites.later_line is None:
        problems.append(
            f"{rel_path}: function {_AUTO_SEED_TARGET_FUNCTION!r} does not call "
            f"{_AUTO_SEED_LATER_ANCHOR!r} -- cannot anchor auto-seed placement"
        )
    if not sites.gitignore_lines:
        problems.append(_seed_call_missing_message(rel_path, _AUTO_SEED_GITIGNORE_HELPER, sites))
    if not sites.gitexclude_lines:
        problems.append(_seed_call_missing_message(rel_path, _AUTO_SEED_GITEXCLUDE_HELPER, sites))
    return problems


def _seed_call_missing_message(rel_path: str, helper: str, sites: _CallSites) -> str:
    prior_line = sites.prior_line
    later_line = sites.later_line
    return (
        f"{rel_path}: {_AUTO_SEED_TARGET_FUNCTION!r} MUST call {helper!r} between "
        f"{_AUTO_SEED_PRIOR_ANCHOR!r} (line {prior_line}) and "
        f"{_AUTO_SEED_LATER_ANCHOR!r} (line {later_line}); no call found"
    )


def _ordering_violations(rel_path: str, sites: _CallSites) -> list[str]:
    problems: list[str] = []
    if sites.prior_line is None or sites.later_line is None:
        return problems
    for helper, lines in (
        (_AUTO_SEED_GITIGNORE_HELPER, sites.gitignore_lines),
        (_AUTO_SEED_GITEXCLUDE_HELPER, sites.gitexclude_lines),
    ):
        for lineno in lines:
            problems.extend(_ordering_violations_for_call(rel_path, helper, lineno, sites))
    return problems


def _ordering_violations_for_call(
    rel_path: str,
    helper: str,
    lineno: int,
    sites: _CallSites,
) -> list[str]:
    problems: list[str] = []
    assert sites.prior_line is not None
    assert sites.later_line is not None
    if lineno < sites.prior_line:
        problems.append(
            f"{rel_path}: {helper!r} call at line {lineno} is BEFORE the "
            f"{_AUTO_SEED_PRIOR_ANCHOR!r} anchor at line {sites.prior_line} -- "
            "auto-seed must run AFTER ensure_git_initialized"
        )
    if lineno > sites.later_line:
        problems.append(
            f"{rel_path}: {helper!r} call at line {lineno} is AFTER the "
            f"{_AUTO_SEED_LATER_ANCHOR!r} anchor at line {sites.later_line} -- "
            "auto-seed must run BEFORE the artifact load"
        )
    return problems


def _check_pre_emptive_untrack_placement() -> list[str]:
    rel_path = "phases/commit_review.py"
    source = _read_or_synthesize_violation(rel_path)
    if isinstance(source, list):
        return source

    tree = _parse_source_or_synthesize_violation(rel_path, source)
    if isinstance(tree, list):
        return tree

    target = _find_target_function(tree)
    if isinstance(target, list):
        return target

    sites = _collect_call_sites(target)
    problems: list[str] = []

    if sites.prior_line is None or sites.later_line is None:
        problems.append(
            f"{rel_path}: function {_AUTO_SEED_TARGET_FUNCTION!r} does not call "
            f"both anchors ({_AUTO_SEED_PRIOR_ANCHOR!r} and {_AUTO_SEED_LATER_ANCHOR!r}) "
            f"-- cannot anchor {_PRE_EMPTIVE_UNTRACK_HELPER!r} placement"
        )
        return problems

    pre_emptive_lines = sites.pre_emptive_untrack_lines
    if not pre_emptive_lines:
        problems.append(
            f"{rel_path}: {_AUTO_SEED_TARGET_FUNCTION!r} MUST call "
            f"{_PRE_EMPTIVE_UNTRACK_HELPER!r} between "
            f"{_AUTO_SEED_PRIOR_ANCHOR!r} (line {sites.prior_line}) and "
            f"{_AUTO_SEED_LATER_ANCHOR!r} (line {sites.later_line}); no call found. "
            "The pre-emptive untrack safety net runs BEFORE the artifact load so "
            "tracked engine-internal files never enter the diff the agent sees."
        )
        return problems

    if len(pre_emptive_lines) > 1:
        problems.append(
            f"{rel_path}: {_AUTO_SEED_TARGET_FUNCTION!r} MUST call "
            f"{_PRE_EMPTIVE_UNTRACK_HELPER!r} EXACTLY ONCE; found calls at lines "
            f"{pre_emptive_lines!r}. Duplicates widen the index-walk surface area "
            "for no gain."
        )

    for lineno in pre_emptive_lines:
        if lineno < sites.prior_line:
            problems.append(
                f"{rel_path}: {_PRE_EMPTIVE_UNTRACK_HELPER!r} call at line {lineno} "
                f"is BEFORE the {_AUTO_SEED_PRIOR_ANCHOR!r} anchor at line "
                f"{sites.prior_line} -- pre-emptive untrack must run AFTER "
                "ensure_git_initialized"
            )
        if lineno > sites.later_line:
            problems.append(
                f"{rel_path}: {_PRE_EMPTIVE_UNTRACK_HELPER!r} call at line {lineno} "
                f"is AFTER the {_AUTO_SEED_LATER_ANCHOR!r} anchor at line "
                f"{sites.later_line} -- pre-emptive untrack must run BEFORE "
                "the artifact load so the agent's diff no longer includes tracked "
                "engine-internal files"
            )

    return problems


def _check_best_effort_invariants() -> list[str]:
    problems: list[str] = []
    rel_path = "phases/commit_review.py"
    try:
        source = _read(rel_path)
    except FileNotFoundError:
        return [f"{rel_path}: file not found"]

    forbidden_hard_fail_phrase = "Refusing to delete non-housekeeping"
    if forbidden_hard_fail_phrase in source:
        problems.append(
            f"{rel_path}: forbidden literal still present "
            f"{forbidden_hard_fail_phrase!r} -- the cleanup phase MUST be "
            "best-effort. Unsafe ``delete_file`` actions should be logged at "
            "WARNING and accumulated in skipped_delete_paths, not raised. "
            "Reintroducing this string is a security-policy regression."
        )

    if "build_commit_review_retry_hint" not in source:
        problems.append(
            f"{rel_path}: missing required symbol 'build_commit_review_retry_hint' -- "
            "the phase MUST provide a structured retry-hint producer for "
            "unapplied delete and apply-failed decisions"
        )

    return problems


_BEHAVIORAL_ACCEPT_PATHS: tuple[str, ...] = (
    ".agent/PRODUCT_CRITERIA.md",
    ".agent/PLAN.md",
    ".agent/ISSUES.md",
    ".agent/DEVELOPMENT_RESULT.md",
    ".agent/FIX_RESULT.md",
    ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
    ".agent/PLANNING_ANALYSIS_DECISION.md",
    ".agent/REVIEW_ANALYSIS_DECISION.md",
    ".agent/checkpoint.json",
    ".agent/rebase_checkpoint.json",
    ".agent/rebase_checkpoint.json.bak",
    ".agent/auto_integrate_in_progress.json",
    ".agent/rebase.lock",
    ".agent/start_commit",
    ".agent/mcp.toml",
    ".agent/raw/opencode.log",
    ".agent/tmp/mcp-server.log",
    ".agent/artifacts/plan.md",
    ".agent/workers/unit-a/tmp/checkpoint.json",
    ".agent/receipts/run-1/commit_review.json",
    ".agent/prompt_history/x.json",
    ".agent/artifact-formats/x.md",
    ".agent/completion_seen_abc-123.json",
    ".agent/state.db",
    ".agent/state.db-wal",
    ".agent/state.db-shm",
    "checkpoint.json",
)

_BEHAVIORAL_REJECT_PATHS: tuple[str, ...] = (
    ".agent/test.py",
    ".agent/utils.py",
    ".agent/CHANGELOG.md",
    ".agent/note.txt",
    ".agent/scripts/build.sh",
    ".agent/lib/foo.py",
    ".agent/notes/foo.txt",
    ".agent/raw/script.py",
    ".agent/workers/unit-a/src/main.py",
    ".agent/receipts/run-1/note.md",
    ".agent/tmp/config.yaml",
    "app/controllers/foo.rb",
    "checkpoint.json.bak",
)


def _load_is_agent_internal_path() -> Callable[[str], bool]:
    module_path = _PACKAGE_ROOT / "phases" / "_agent_internal_paths.py"
    spec = importlib.util.spec_from_file_location(
        "ralph_phases_agent_internal_paths_audit_target", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot build import spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast("Callable[[str], bool]", module.is_agent_internal_path)


def _behavioral_invariants() -> list[str]:
    problems: list[str] = []

    try:
        is_agent_internal_path = _load_is_agent_internal_path()
    except Exception as exc:
        return [f"audit_agent_internal_paths: cannot import is_agent_internal_path: {exc}"]

    for path in _BEHAVIORAL_ACCEPT_PATHS:
        try:
            result = is_agent_internal_path(path)
        except Exception as exc:
            problems.append(f"is_agent_internal_path({path!r}) raised {type(exc).__name__}: {exc}")
            continue
        if result is not True:
            problems.append(f"is_agent_internal_path({path!r}) returned {result!r}, expected True")

    for path in _BEHAVIORAL_REJECT_PATHS:
        try:
            result = is_agent_internal_path(path)
        except Exception as exc:
            problems.append(f"is_agent_internal_path({path!r}) raised {type(exc).__name__}: {exc}")
            continue
        if result is not False:
            problems.append(f"is_agent_internal_path({path!r}) returned {result!r}, expected False")

    return problems


_INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        rel_path="phases/_agent_internal_paths.py",
        present=(
            "AGENT_INTERNAL_DIR_GLOBS",
            "AGENT_INTERNAL_TOP_LEVEL_BASENAMES",
            "AGENT_INTERNAL_ROOT_BASENAMES",
            "_AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB",
            "completion_seen_*.json",
            "def is_agent_internal_path",
            "_AGENT_INTERNAL_DIR_FILE_EXTENSIONS",
        ),
        absent=(
            '"completion_sentinel_',
            "'completion_sentinel_",
        ),
    ),
    Invariant(
        rel_path="phases/commit_review.py",
        present=(
            "from ralph.phases._agent_internal_paths import is_agent_internal_path",
            "if is_agent_internal_path(path):",
        ),
    ),
    Invariant(
        rel_path="config/bootstrap.py",
        present=(
            "_DEFAULT_GIT_EXCLUDE_PATTERNS",
            "auto_seed_default_git_exclude",
            "_resolve_git_exclude_path",
            "AGENT_INTERNAL_ROOT_BASENAMES",
            'f"/{name}"',
        ),
        absent=('"checkpoint.json"',),
    ),
    Invariant(
        rel_path="git/operations.py",
        present=(
            "hashlib.sha256(payload.encode(encoding)).hexdigest()[:16]",
            "os.getpid()",
        ),
        absent=("id(payload)",),
    ),
)


def main(argv: list[str] | None = None) -> int:
    del argv
    problems: list[str] = []
    for invariant in _INVARIANTS:
        problems.extend(invariant.violations())
    problems.extend(_behavioral_invariants())
    problems.extend(_check_fast_path_placement())
    problems.extend(_check_auto_seed_placement())
    problems.extend(_check_pre_emptive_untrack_placement())
    problems.extend(_check_best_effort_invariants())

    literal_count = sum(len(i.present) + len(i.absent) for i in _INVARIANTS)
    behavioral_count = len(_BEHAVIORAL_ACCEPT_PATHS) + len(_BEHAVIORAL_REJECT_PATHS)
    placement_count = 1
    auto_seed_count = 1
    pre_emptive_untrack_count = 1
    total = (
        literal_count
        + behavioral_count
        + placement_count
        + auto_seed_count
        + pre_emptive_untrack_count
    )

    if problems:
        print(f"AGENT-INTERNAL-PATHS AUDIT FAILED: {len(problems)} invariant violation(s)")
        print("=" * 72)
        for line in problems:
            print(f"  {line}")
        print()
        print(
            "The Ralph runtime-artifact allowlist has drifted between the leaf module "
            "(_agent_internal_paths.py), the commit_review fast-path (literal + AST placement), "
            "and the bootstrap gitignore/exclude seed. Re-read the rework plan in PLAN.md and "
            "restore the missing/forbidden literals and the first-statement placement."
        )
        return 1

    print(
        f"audit_agent_internal_paths OK ({total} invariants checked): "
        "_agent_internal_paths.py exports the canonical frozensets + completion_seen_*.json "
        "glob + is_agent_internal_path predicate, "
        "commit_review.py imports and invokes is_agent_internal_path as the fast-path, "
        "_is_safe_to_delete places is_agent_internal_path(path) as the "
        "FIRST executable statement (AST placement), "
        "handle_commit_review_phase auto-seeds canonical .gitignore + .git/info/exclude "
        "patterns between ensure_git_initialized and _load_commit_review_artifact (AST placement), "
        "handle_commit_review_phase pre-emptively untracks tracked engine-internal files "
        "via untrack_engine_internal_files between ensure_git_initialized and "
        "_load_commit_review_artifact (AST placement, EXACTLY ONCE), "
        "bootstrap.py defines _DEFAULT_GIT_EXCLUDE_PATTERNS + auto_seed_default_git_exclude "
        "+ root-anchored /checkpoint.json (NOT bare checkpoint.json), "
        "git/operations.py _atomic_append_text staging filename is content-derived "
        "(sha256(payload).hexdigest()[:16] + os.getpid()), NOT id(payload)-derived, "
        f"behavioral check accepts all {len(_BEHAVIORAL_ACCEPT_PATHS)} canonical paths "
        f"and rejects all {len(_BEHAVIORAL_REJECT_PATHS)} negative paths."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
