"""Audit that the Ralph runtime-artifact allowlist is consistent across all three consumers.

Without this audit, future drift in any consumer of the canonical allowlist
(``commit_cleanup``, ``bootstrap``, and this audit itself) could silently
re-introduce the original bug: tracked engine-owned files blocked from
deletion by the universal HEAD veto in ``_is_safe_to_delete``.

Enforces non-vacuous invariants across:

1. **LITERAL-STRING** -- ``ralph/phases/_agent_internal_paths.py`` MUST
   expose the canonical allowlist via the documented public symbols:
   ``AGENT_INTERNAL_DIR_GLOBS``, ``AGENT_INTERNAL_TOP_LEVEL_BASENAMES``,
   ``AGENT_INTERNAL_ROOT_BASENAMES``, the
   ``_AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB = 'completion_seen_*.json'``
   literal (NOT ``completion_sentinel_*.json``), and the
   ``is_agent_internal_path`` predicate.

   The audit ALSO asserts that the per-directory file extension allowlist
   (``_AGENT_INTERNAL_DIR_FILE_EXTENSIONS``) is present -- this is the
   tightened boundary that prevents blanket ``.agent/<dir>/`` prefix
   matches from silently deleting user-authored tracked files like
   ``.agent/raw/script.py`` or ``.agent/workers/<unit>/src/main.py``.

2. **LITERAL-STRING** -- ``ralph/phases/commit_cleanup.py`` MUST import
   ``is_agent_internal_path`` from the leaf module AND call it as the
   FIRST statement in ``_is_safe_to_delete`` (the fast-path exemption).

3. **LITERAL-STRING** -- ``ralph/config/bootstrap.py`` MUST define
   ``_DEFAULT_GIT_EXCLUDE_PATTERNS`` and the
   ``auto_seed_default_git_exclude`` function AND include the root-anchored
   ``/checkpoint.json`` in ``_DEFAULT_GITIGNORE_PATTERNS`` (NOT bare
   ``checkpoint.json`` which would silently match every subdirectory).

   The audit ALSO asserts that ``_resolve_git_exclude_path`` exists --
   this is the worktree-aware gitdir resolver required for git worktrees
   and separate-git-dir layouts where ``repo_root/.git`` is a file
   pointing at the real gitdir.

4. **BEHAVIORAL** -- import ``is_agent_internal_path`` and exercise it
   against a representative accept set (canonical basenames + dir-segment
   paths with dir-appropriate extensions + completion-sentinel glob +
   root-level checkpoint.json) and a reject set (user-authored tracked
   files under ``.agent/``, source files inside engine-internal
   directories, paths outside ``.agent/``). All accepts must return
   True, all rejects must return False.

5. **AST PLACEMENT** -- parse ``ralph/phases/commit_cleanup.py`` with the
   ``ast`` module and verify that the FIRST executable statement inside
   ``_is_safe_to_delete`` is the ``is_agent_internal_path(path)`` check.
   This is the stronger guarantee the audit pins: a future refactor
   could easily move the predicate call back behind ``Path(path)`` /
   ``path.lower()`` / ``suffix`` setup statements and silently
   re-introduce the original bug -- the literal-string check alone
   would still pass because the call would still be present in the
   function body. The AST check is independent of the literal-string
   check and catches the placement drift directly.

Usage:
    python -m ralph.testing.audit_agent_internal_paths

Exit 0 = clean, 1 = at least one invariant violated.
"""

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
    """One literal-string check the audit enforces."""

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


# Target function whose fast-path placement the audit pins.
_FAST_PATH_TARGET_FUNCTION: str = "_is_safe_to_delete"

# Expected first executable statement in ``_is_safe_to_delete``. The
# predicate call must be the very first statement in the function body --
# any statement ahead of it (e.g. ``candidate = Path(path)``,
# ``path_lower = path.lower()``, ``suffix = candidate.suffix.lower()``)
# is a violation because it bypasses the engine-owned allowlist for
# paths whose shape trips one of those earlier checks first.
_FAST_PATH_TARGET_SOURCE: str = "is_agent_internal_path(path)"


def _check_fast_path_placement() -> list[str]:
    """Verify the agent-internal fast path is the FIRST statement in ``_is_safe_to_delete``.

    Uses Python's ``ast`` module to parse ``ralph/phases/commit_cleanup.py``
    and locate the function body. Walks the body in source order and
    returns a violation if the FIRST executable statement is NOT the
    ``is_agent_internal_path(path)`` check.

    This is the stronger guarantee the audit must pin: a future refactor
    could easily move the predicate call back behind ``Path(path)`` /
    ``path.lower()`` / ``suffix`` setup statements and silently re-introduce
    the original bug -- the literal-string check alone would still pass
    because the call would still be present in the function body.

    Docstrings and ``pass`` statements are skipped; ``Expr`` nodes that are
    plain string literals (e.g. ``"..."`` or ``'...'``) are treated as
    docstrings and skipped per the Python language spec.

    Returns:
        List of violation strings. Empty on success.
    """
    problems: list[str] = []
    rel_path = "phases/commit_cleanup.py"
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

    # Skip docstring statements per CPython convention (PEP 257 / ast.get_docstring).
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


# Target function whose on-entry auto-seed placement the audit pins.
_AUTO_SEED_TARGET_FUNCTION: str = "handle_commit_cleanup_phase"

# Real exported helper names from ralph.config.bootstrap. PA-003 closed:
# the prior audit used a nonexistent symbol ``auto_seed_default_git_execute``
# as a string literal, which would silently pass the AST check if the
# literal was merely present. The audit now uses the actual exported
# names so the placement check matches the real call sites.
_AUTO_SEED_GITIGNORE_HELPER: str = "auto_seed_default_gitignore"
_AUTO_SEED_GITEXCLUDE_HELPER: str = "auto_seed_default_git_exclude"

# Anchor calls whose relative position we use to verify the auto-seed
# block sits between the ensure_git_initialized call and the artifact
# load. Both anchor names are stable identifiers that have existed since
# the original commit_cleanup phase was introduced.
_AUTO_SEED_PRIOR_ANCHOR: str = "ensure_git_initialized"
_AUTO_SEED_LATER_ANCHOR: str = "_load_cleanup_artifact"

# Helper whose placement the new pre-emptive-untrack audit pins. The
# call shape MUST be a plain ``ast.Name`` call (not a module-qualified
# ``ast.Attribute`` call) so ``_collect_call_sites`` records the line.
_PRE_EMPTIVE_UNTRACK_HELPER: str = "untrack_engine_internal_files"


def _check_auto_seed_placement() -> list[str]:
    """Verify both auto-seed helpers are called between the prior and later anchors.

    Walks ``handle_commit_cleanup_phase`` body in source order and verifies
    that:

    1. The ``ensure_git_initialized`` anchor appears BEFORE both
       ``auto_seed_default_gitignore`` and ``auto_seed_default_git_exclude``.
    2. The ``_load_cleanup_artifact`` anchor appears AFTER both seed calls.
    3. Both seed helpers are present in the body (this is the PA-003
       closure -- the prior audit's string-literal check could be
       satisfied by a commented-out line; the AST check requires an
       actual ``ast.Call`` node).

    The audit uses the REAL exported helper names
    (``auto_seed_default_gitignore`` and ``auto_seed_default_git_exclude``)
    and walks the AST to find the actual ``Call`` nodes so a commented-out
    import or a docstring mention cannot satisfy the invariant.

    Returns:
        List of violation strings. Empty on success.
    """
    rel_path = "phases/commit_cleanup.py"
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
    """Read the target file or return a single-element violation list."""
    try:
        return _read(rel_path)
    except FileNotFoundError:
        return [f"{rel_path}: file not found"]


def _parse_source_or_synthesize_violation(rel_path: str, source: str) -> ast.AST | list[str]:
    """Parse source into an AST or return a single-element violation list."""
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        return [f"{rel_path}: syntax error during AST parse: {exc}"]


def _find_target_function(tree: ast.AST) -> ast.FunctionDef | ast.AsyncFunctionDef | list[str]:
    """Locate the ``handle_commit_cleanup_phase`` function in the parsed AST."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == (
            _AUTO_SEED_TARGET_FUNCTION
        ):
            return node
    return [
        f"phases/commit_cleanup.py: function {_AUTO_SEED_TARGET_FUNCTION!r} not found -- "
        "auto-seed placement cannot be verified"
    ]


class _CallSites:
    """Container for the call-site line numbers the auto-seed audit cares about."""

    def __init__(self) -> None:
        self.prior_line: int | None = None
        self.later_line: int | None = None
        self.gitignore_lines: list[int] = []
        self.gitexclude_lines: list[int] = []
        self.pre_emptive_untrack_lines: list[int] = []


def _collect_call_sites(target: ast.FunctionDef | ast.AsyncFunctionDef) -> _CallSites:
    """Walk the target function and bucket each ``ast.Call`` by helper name."""
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
    """Update ``sites`` with the line of one named call."""
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
    """Return anchor- and seed-presence violations (or [] if all are present)."""
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
    """Format the ``MUST call ... between anchor A and anchor B`` message."""
    prior_line = sites.prior_line
    later_line = sites.later_line
    return (
        f"{rel_path}: {_AUTO_SEED_TARGET_FUNCTION!r} MUST call {helper!r} between "
        f"{_AUTO_SEED_PRIOR_ANCHOR!r} (line {prior_line}) and "
        f"{_AUTO_SEED_LATER_ANCHOR!r} (line {later_line}); no call found"
    )


def _ordering_violations(rel_path: str, sites: _CallSites) -> list[str]:
    """Return ordering violations for each helper call vs. the anchor lines."""
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
    """Return before/after anchor violations for a single helper call line."""
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
    """Verify the pre-emptive untrack call sits between the same two anchors.

    Reuses the prior / later anchors from ``_check_auto_seed_placement``
    (the call must run AFTER ``ensure_git_initialized`` and BEFORE
    ``_load_cleanup_artifact``). The audit also asserts there is
    EXACTLY ONE call to ``untrack_engine_internal_files`` in the
    ``handle_commit_cleanup_phase`` body -- a duplicate call would
    widen the index-walk surface area for no gain.

    The check reuses ``_collect_call_sites`` so the call shape must be
    a plain ``ast.Name`` call (``untrack_engine_internal_files(...)``),
    NOT a module-qualified ``commit_cleanup_module.untrack_engine_internal_files(...)``
    -- the latter would be silently skipped by ``_collect_call_sites``
    and pass the audit, which is exactly the regression mode this
    placement check exists to prevent.

    Returns:
        List of violation strings. Empty on success.
    """
    rel_path = "phases/commit_cleanup.py"
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
    """Verify cleanup is best-effort and has a structured retry-hint helper.

    Asserts:

    1. ``ralph/phases/commit_cleanup.py`` does NOT contain the literal
       string ``raise ValueError`` paired with ``Refusing to delete
       non-housekeeping`` -- the hard fail was removed in favor of a
       WARNING log + skipped_delete_paths return.
    2. ``ralph/phases/commit_cleanup.py`` DOES define a
       ``build_cleanup_retry_hint`` helper (the structured reason
       producer for ``PhaseFailureEvent``).

    Returns:
        List of violation strings. Empty on success.
    """
    problems: list[str] = []
    rel_path = "phases/commit_cleanup.py"
    try:
        source = _read(rel_path)
    except FileNotFoundError:
        return [f"{rel_path}: file not found"]

    forbidden_hard_fail_phrase = "Refusing to delete non-housekeeping"
    if forbidden_hard_fail_phrase in source:
        # A coarse-grained check that catches the original ``raise
        # ValueError(f\"... Refusing to delete non-housekeeping ...\")``
        # pattern. The audit fails if this phrase is anywhere in the
        # file because the only legitimate use was the removed hard
        # fail -- the WARNING log uses a different phrasing.
        problems.append(
            f"{rel_path}: forbidden literal still present "
            f"{forbidden_hard_fail_phrase!r} -- the cleanup phase MUST be "
            "best-effort. Unsafe ``delete_file`` actions should be logged at "
            "WARNING and accumulated in skipped_delete_paths, not raised. "
            "Reintroducing this string is a security-policy regression."
        )

    if "build_cleanup_retry_hint" not in source:
        problems.append(
            f"{rel_path}: missing required symbol 'build_cleanup_retry_hint' -- "
            "the phase MUST provide a structured retry-hint producer for "
            "PhaseFailureEvent when all delete actions are rejected"
        )

    return problems


# Accept set: every canonical Ralph runtime artifact.
_BEHAVIORAL_ACCEPT_PATHS: tuple[str, ...] = (
    # Top-level basenames under .agent/ (15 total per AGENT_INTERNAL_TOP_LEVEL_BASENAMES).
    ".agent/CURRENT_PROMPT.md",
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
    # Dir-segment paths (7 canonical dirs, each with a dir-appropriate
    # extension per ``_AGENT_INTERNAL_DIR_FILE_EXTENSIONS``).
    ".agent/raw/opencode.log",
    ".agent/tmp/mcp-server.log",
    ".agent/artifacts/plan.md",
    ".agent/workers/unit-a/tmp/checkpoint.json",
    ".agent/receipts/run-1/commit_cleanup.json",
    ".agent/prompt_history/x.json",
    ".agent/artifact-formats/x.md",
    # Completion-sentinel glob (canonical on-disk filename pattern).
    ".agent/completion_seen_abc-123.json",
    # StateDB WAL files (RFC-013 P3): engine-internal bookkeeping store.
    # The db/-wal/-shm trio is referenced when persistence write/read
    # code paths mention ``.agent/state.db`` (or the WAL index).
    ".agent/state.db",
    ".agent/state.db-wal",
    ".agent/state.db-shm",
    # Root-level canonical basename.
    "checkpoint.json",
)

# Reject set: user-authored tracked files and paths outside .agent/.
_BEHAVIORAL_REJECT_PATHS: tuple[str, ...] = (
    # Source files under .agent/ that are NOT in the allowlist.
    ".agent/test.py",
    ".agent/utils.py",
    ".agent/CHANGELOG.md",
    ".agent/note.txt",
    ".agent/scripts/build.sh",
    ".agent/lib/foo.py",
    ".agent/notes/foo.txt",
    # Source files INSIDE engine-internal directories -- the security
    # boundary that was widened when the predicate allowed any file
    # under ``.agent/raw/``, ``.agent/tmp/``, ``.agent/workers/``,
    # ``.agent/receipts/``, ``.agent/artifacts/``,
    # ``.agent/prompt_history/``, ``.agent/artifact-formats/``. The
    # per-directory extension allowlist
    # (``_AGENT_INTERNAL_DIR_FILE_EXTENSIONS``) restricts deletion to
    # engine-written file types only.
    ".agent/raw/script.py",
    ".agent/workers/unit-a/src/main.py",
    ".agent/receipts/run-1/note.md",
    ".agent/tmp/config.yaml",
    # Source files outside .agent/ -- never agent-internal.
    "app/controllers/foo.rb",
    # .bak variant of root basename (NOT engine-owned).
    "checkpoint.json.bak",
)


def _load_is_agent_internal_path() -> Callable[[str], bool]:
    """Load the leaf module via ``importlib`` to avoid ``ralph.phases`` package init.

    The leaf module lives at ``ralph/phases/_agent_internal_paths.py``. Importing
    it through the normal ``from ralph.phases._agent_internal_paths import ...``
    form at module level triggers ``ralph.phases.__init__`` to run, which
    transitively touches ``ralph.pipeline -> ralph.config -> ralph.policy`` and
    can fail with a pre-existing circular import when the leaf module is the
    first import in a fresh Python process. Loading the module by file path
    side-steps the package init entirely because the leaf has stdlib-only
    imports (no transitive ``ralph.*`` deps), so this is provably cycle-free.
    """
    module_path = _PACKAGE_ROOT / "phases" / "_agent_internal_paths.py"
    spec = importlib.util.spec_from_file_location(
        "ralph_phases_agent_internal_paths_audit_target", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot build import spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The static type-checker treats ``module.is_agent_internal_path`` as ``Any``
    # because the module was loaded by file path. Narrow it to the documented
    # public signature so downstream ``is_agent_internal_path(path)`` calls and
    # the ``result is not True`` / ``result is not False`` checks type-check
    # cleanly. The runtime contract is exactly the same callable.
    return cast("Callable[[str], bool]", module.is_agent_internal_path)


def _behavioral_invariants() -> list[str]:
    """Exercise ``is_agent_internal_path`` against the canonical accept/reject sets.

    Returns a list of violation strings (empty on success).
    """
    problems: list[str] = []

    try:
        # Load the leaf module via ``importlib`` (see ``_load_is_agent_internal_path``).
        # The TYPE_CHECKING import above gives the function its proper
        # ``Callable[[str], bool]`` type for the static type-checker; the
        # runtime dynamic load is the only path that survives a fresh-process
        # ``ralph.phases`` package init (pre-existing cycle through
        # ``ralph.policy.loader``).
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
            # PA-004: the canonical on-disk filename glob. Check for both
            # single and double quotes so the audit is robust to either
            # style; the value MUST be ``completion_seen_*.json`` and MUST
            # NOT contain the Python abstraction identifier
            # ``completion_sentinel_*``.
            "_AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB",
            "completion_seen_*.json",
            "def is_agent_internal_path",
            # Per-directory file extension allowlist -- the tightened
            # boundary that restricts engine-internal dir-prefix matches
            # to engine-written file types only.
            "_AGENT_INTERNAL_DIR_FILE_EXTENSIONS",
        ),
        absent=(
            # The Python abstraction identifier as a literal Python
            # attribute/string -- the docstring/comments in the leaf
            # module use the identifier in prose to explain what it is NOT,
            # but no code path may define an actual on-disk filename
            # pattern using it. We forbid it as a *quoted literal* in the
            # source body to catch a regression where someone
            # accidentally wires ``completion_sentinel_*`` into the glob.
            '"completion_sentinel_',
            "'completion_sentinel_",
        ),
    ),
    Invariant(
        rel_path="phases/commit_cleanup.py",
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
            # Worktree-aware gitdir resolver -- required for git worktrees
            # and separate-git-dir layouts where ``repo_root/.git`` is a
            # gitfile, not a directory.
            "_resolve_git_exclude_path",
            '"/checkpoint.json"',
        ),
        absent=(
            # PA-002 regression: bare ``checkpoint.json`` (without leading slash)
            # would silently match every nested directory. The root-anchored
            # form is the ONLY acceptable representation.
            '"checkpoint.json"',
        ),
    ),
    Invariant(
        rel_path="git/operations.py",
        # Staging filename in ``_atomic_append_text`` MUST be content-derived
        # so concurrent invocations cannot collide on the staging sibling.
        # The previous ``id(payload)``-derived suffix collided whenever the
        # caller constructed equal-id strings back-to-back (e.g. empty payload
        # or shared string-literal dedup) -- the staging ``write_text`` would
        # then either truncate a sibling's half-written content or hit a
        # ``FileExistsError`` on rename. The sha256(payload).hexdigest()[:16]
        # plus os.getpid() suffix is collision-free across processes and
        # across identical payloads in the same process.
        present=(
            "hashlib.sha256(payload.encode(encoding)).hexdigest()[:16]",
            "os.getpid()",
        ),
        absent=(
            # Legacy ``id(payload)``-derived staging suffix -- a regression
            # back to that pattern is a collision risk for concurrent
            # invocations and must be caught by this audit.
            "id(payload)",
        ),
    ),
)


def main(argv: list[str] | None = None) -> int:
    """Run the agent-internal-paths audit and return the process exit code.

    Iterates over the literal-string ``Invariant`` objects in ``_INVARIANTS``,
    then runs the behavioral predicate check against the canonical accept and
    reject sets. Prints a one-line summary on success or a labeled,
    line-broken failure banner on violation. Has no side effects beyond
    stdout output and ``sys.exit`` semantics.

    Args:
        argv: Unused positional argument list (kept for CLI symmetry with
            other audit entry points). Values are ignored.

    Returns:
        ``0`` when every invariant passes, ``1`` when at least one
        literal-string or behavioral check fails.
    """
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
            "(_agent_internal_paths.py), the commit_cleanup fast-path (literal + AST placement), "
            "and the bootstrap gitignore/exclude seed. Re-read the rework plan in PLAN.md and "
            "restore the missing/forbidden literals and the first-statement placement."
        )
        return 1

    print(
        f"audit_agent_internal_paths OK ({total} invariants checked): "
        "_agent_internal_paths.py exports the canonical frozensets + completion_seen_*.json "
        "glob + is_agent_internal_path predicate, "
        "commit_cleanup.py imports and invokes is_agent_internal_path as the fast-path, "
        "_is_safe_to_delete places is_agent_internal_path(path) as the "
        "FIRST executable statement (AST placement), "
        "handle_commit_cleanup_phase auto-seeds canonical .gitignore + .git/info/exclude "
        "patterns between ensure_git_initialized and _load_cleanup_artifact (AST placement), "
        "handle_commit_cleanup_phase pre-emptively untracks tracked engine-internal files "
        "via untrack_engine_internal_files between ensure_git_initialized and "
        "_load_cleanup_artifact (AST placement, EXACTLY ONCE), "
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
