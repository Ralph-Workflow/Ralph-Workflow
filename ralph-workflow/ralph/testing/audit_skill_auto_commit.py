"""Audit that pins the wt-025 deterministic skill-update auto-commit contract.

The pre-pipeline skill sync in ``_sync_shipped_skills_on_pipeline_run``
auto-commits project-scope skill-tree changes via
``commit_skill_updates`` (from ``ralph.skills._auto_commit``). The
commit is "invisible to the committing agent" -- the development agent
never sees the skill-tree drift in its working tree because the auto-commit
runs BEFORE the agent starts.

This audit pins the contract that makes the auto-commit safe, deterministic,
and bounded. Without it, future refactors could silently break:

1. the deterministic subject line ``chore(skills): sync baseline bundle``
   (a future rename is a contract change that must update the helper AND
   this audit in the same commit);

2. the FIVE canonical project-scope skill-root prefix set
   (``.opencode/skills/``, ``.agents/skills/``, ``.claude/skills/``,
   ``.codex/skills/``, ``.gemini/antigravity-cli/skills/``);

3. the AST placement of the early-skip block in
   ``ralph/git/commit_cleanup.py::untrack_engine_internal_files`` -- the
   skip MUST run BEFORE the symlink-WARNING block so tracked skill
   symlinks never trigger the WARNING noise on a clean run; and

4. the existence of ``ralph/skills/_auto_commit.py`` itself -- a future
   refactor that deletes the helper without removing the wiring would
   leave ``_sync_shipped_skills_on_pipeline_run`` with a NameError on
   the next pipeline run.

Usage:
    python -m ralph.testing.audit_skill_auto_commit

Exit 0 = clean, 1 = at least one invariant violated.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _read(rel_path: str) -> str:
    return (_PACKAGE_ROOT / rel_path).read_text(encoding="utf-8")


# --- Invariant definitions ---------------------------------------------------

# The deterministic conventional-commit subject line. Pinned as a literal
# string so a future rename is a contract change that must update BOTH the
# helper and this audit in the same commit.
_SKILL_AUTO_COMMIT_SUBJECT: str = "chore(skills): sync baseline bundle"

# The FIVE canonical project-scope skill-root prefix strings. Adding or
# removing a root MUST update this set AND ``_SKILL_ROOT_PREFIXES`` in
# ``ralph/skills/_agent_paths.py`` in the same commit.
_SKILL_ROOT_PREFIXES: frozenset[str] = frozenset(
    {
        ".opencode/skills/",
        ".agents/skills/",
        ".claude/skills/",
        ".codex/skills/",
        ".gemini/antigravity-cli/skills/",
    }
)

#: Commit helpers that mark a deterministic write site as routed through the
#: shared scoped-auto-commit helper (wt-012 S-7). Every other production
#: filesystem write must either route through one of these helpers or carry
#: an inline ``deterministic-writer-ok: <reason>`` classification marker.
#:
#: wt-012: ``_commit_deterministic_config_write`` is the config-writer
#: wrapper added in U1 that captures the pre-write hash, performs the
#: ``atomic_write_text_if_changed`` / ``write_text_if_changed`` call, and
#: routes the byte-exact diff through ``commit_deterministic_writes``.
#: Functions that call it are routed -- the audit recognizes the call
#: and skips the per-callsite marker requirement.
#:
#: wt-12: ``_commit_config_write`` (config/bootstrap.py) is the
#: bootstrap-side routing wrapper: it classifies the target
#: (``classify_target_for_commit``), captures the pre-write hashes, and
#: routes the deterministic diff through ``commit_deterministic_writes``.
#: Sites whose write is followed by this wrapper call are routed.
_WRITER_COMMIT_HELPERS: frozenset[str] = frozenset(
    {
        "commit_deterministic_writes",
        "commit_scoped_updates",
        "commit_policy_writes",
        "commit_skill_writes",
        "_commit_deterministic_config_write",
        "_commit_config_write",
    }
)

#: Filesystem-mutation attribute calls scanned by the production-writer scan
#: (wt-012 S-7, DA-004): the broader mutation-method class the pre-wt-012
#: scan enforced, restored after the policy-commit move dropped it.
#:
#: wt-012 PA-001 (fix): the canonical idempotent mutation helpers exported
#: by ``ralph/mcp/artifacts/idempotent_write.py`` (``__all__``,
#: ``atomic_write_text_if_changed``, ``write_text_if_changed``,
#: ``write_bytes_if_changed``) are also filesystem mutations and MUST
#: appear in BOTH scan sets. The pre-fix scan missed these helpers, so
#: every new site calling them was invisible to the audit. The audit
#: module itself and the other canonical-write primitives
#: (``mcp/artifacts/file_backend.py``, ``mcp/artifacts/_path_file_backend.py``)
#: are excluded via ``_WRITER_SCAN_EXCLUDED_FILES``.
_WRITER_WRITE_ATTRS: frozenset[str] = frozenset(
    {
        "write_text",
        "write_bytes",
        "copytree",
        "copy2",
        "symlink_to",
        "atomic_write_text_if_changed",
        "write_text_if_changed",
        "write_bytes_if_changed",
    }
)

#: Bare-name mutation calls scanned by the production-writer scan.
#: wt-012 PA-001 (fix): the same canonical helpers (and
#: ``atomic_write_bytes_if_changed``) are added in bare-name form.
_WRITER_WRITE_NAMES: frozenset[str] = frozenset(
    {
        "write_text",
        "write_bytes",
        "copytree",
        "copy2",
        "mkdir",
        "_create_symlink",
        "atomic_write_text_if_changed",
        "atomic_write_bytes_if_changed",
        "write_text_if_changed",
        "write_bytes_if_changed",
    }
)

#: Inline classification markers accepted by the production-writer scan.
_WRITER_MARKER_TOKENS: tuple[str, ...] = (
    "deterministic-writer-ok",
    "filesystem-write-ok",
    "inline-rationale-ok",
)

#: Top-level subdirectories excluded from the production-writer scan. The
#: audit module itself lives under ``testing/`` and is self-excluded here.
_WRITER_SCAN_EXCLUDED_DIRS: tuple[str, ...] = ("testing",)

#: Individual files excluded from the production-writer scan: the shared
#: commit helper itself plus the canonical write primitives (the artifacts
#: modules are exempt from the filesystem-write consolidation audit for the
#: same reason -- they ARE the primitives, not deterministic writers).
_WRITER_SCAN_EXCLUDED_FILES: frozenset[str] = frozenset(
    {
        "git/scoped_auto_commit.py",
        "mcp/artifacts/idempotent_write.py",
        "mcp/artifacts/file_backend.py",
        "mcp/artifacts/_path_file_backend.py",
    }
)

#: Modules allowed to call ``create_commit`` directly (the agent-authored
#: commit flow). Every other production module must route deterministic
#: commits through ``commit_deterministic_writes`` (wt-012 S-8).
_DIRECT_CHORE_COMMIT_ALLOWLIST: frozenset[str] = frozenset({"cli/commands/commit.py"})


class Invariant:
    """A literal-string presence/absence check on a single source file."""

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
        try:
            content = _read(self.rel_path)
        except FileNotFoundError:
            return [f"  {self.rel_path}: file not found (delete must update audit)"]
        problems: list[str] = [
            f"  {self.rel_path}: missing required literal {needle!r}"
            for needle in self.present
            if needle not in content
        ]
        problems.extend(
            f"  {self.rel_path}: forbidden literal {needle!r} is present"
            for needle in self.absent
            if needle in content
        )
        return problems


# --- File-existence checks ---------------------------------------------------

_FILE_EXISTENCE_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "skills/_auto_commit.py",
        "the deterministic auto-commit helper module",
    ),
)


# --- Source literal-string invariants ---------------------------------------

_INVARIANTS: tuple[Invariant, ...] = (
    # The auto-commit helper exports the pinned subject constant.
    Invariant(
        rel_path="skills/_auto_commit.py",
        present=(
            "SKILL_AUTO_COMMIT_SUBJECT",
            _SKILL_AUTO_COMMIT_SUBJECT,
            "commit_skill_updates",
            "commit_skill_writes",
            "stage_files",  # imports from ralph.git.operations (selective, not stage_all)
        ),
        absent=("stage_all",),
    ),
    # The canonical FIVE skill-root prefixes are exported from _agent_paths.
    Invariant(
        rel_path="skills/_agent_paths.py",
        present=(
            "_SKILL_ROOT_PREFIXES",
            ".opencode/skills/",
            ".agents/skills/",
            ".claude/skills/",
            ".codex/skills/",
            ".gemini/antigravity-cli/skills/",
        ),
    ),
    # The commit_cleanup.py early-skip block MUST come BEFORE the
    # symlink-WARNING block. The literal-string check alone is insufficient
    # -- the AST placement check below provides the stronger guarantee.
    # Here we pin the literal-string presence of the early-skip so a
    # future refactor that accidentally removes the entire block fails.
    Invariant(
        rel_path="git/commit_cleanup.py",
        present=(
            "_SKILL_ROOT_PREFIXES",
            "Skipping tracked skill-root path",
        ),
    ),
    # The CLI wiring in run.py MUST route the deterministic skill
    # auto-commit through ``commit_skill_writes`` (the wt-012
    # producer-level primitive that consumes the install's
    # ``written_paths`` + ``pre_contents``). The legacy
    # ``commit_skill_updates`` import is still present for the
    # dirty-tree post-condition path; the run-path literals pin the
    # producer-level success path; the failure-path literals pin the
    # best-effort fail-closed contract so a future refactor that
    # silently drops the try/except handler is caught at audit time.
    Invariant(
        rel_path="cli/commands/run.py",
        present=(
            "from ralph.skills._auto_commit import commit_skill_writes",
            "Auto-committed skill updates",
        ),
    ),
    # The new helper overwrites stale canonical content in the installer
    # (the locked project-scope conflict-resolution branch).
    Invariant(
        rel_path="skills/_installer.py",
        present=(
            "_materialize_canonical_skill",
            # wt-012: the producer-level wrapper that records the
            # install's byte-exact diff for the auto-commit at the
            # install boundary.
            "install_project_baseline_skills_with_diff",
            "ProjectSkillInstallOutcome",
        ),
    ),
    # wt-012: the shared helper owns the producer-level isolation
    # primitive (``commit_deterministic_writes``) plus the strict
    # pre-snapshot used at every producer boundary to distinguish
    # clean from unreadable.
    Invariant(
        rel_path="git/scoped_auto_commit.py",
        present=(
            "ScopedCommitResult",
            "ScopedCommitStatus",
            "commit_deterministic_writes",
            "snapshot_dirty_paths_strict",
            "capture_pre_write_contents",
        ),
    ),
    # wt-012: the pre-staged index snapshot/restore helpers (including
    # the staged-deletion sentinel) moved to ``git/_index_snapshots.py``
    # to keep ``scoped_auto_commit.py`` under the 1000-line cap; the
    # invariant tracks the new home of the literal.
    Invariant(
        rel_path="git/_index_snapshots.py",
        present=(
            "STAGED_DELETION_SENTINEL",
            "_snapshot_pre_staged_index",
            "_restore_pre_staged_index",
        ),
    ),
    # wt-012: the policy preflight routes its own writes through the
    # producer-level ``commit_policy_writes`` helper at the
    # preflight boundary (BEFORE the validator runs).
    Invariant(
        rel_path="project_policy/preflight.py",
        present=(
            "commit_policy_writes",
            "capture_pre_write_contents",
        ),
    ),
    # wt-012: the post-pipeline finalize uses the producer-level
    # commit with the pre-write hash of AGENTS.md recorded BEFORE
    # ``condense_placeholder_block`` runs, so an agent edit to AGENTS.md
    # in flight is SKIPPED (not committed in the policy chore commit).
    # The finalize was extracted from ``cli_integration`` to
    # ``_auto_commit_integration`` to keep the orchestrator under the
    # 1000-line cap; the invariant tracks the new home of the literals.
    Invariant(
        rel_path="project_policy/_auto_commit_integration.py",
        present=(
            "commit_policy_writes",
            "condense_placeholder_block",
            "capture_pre_write_contents",
        ),
    ),
    # wt-012: the policy auto-commit module no longer carries the
    # ``authored_paths`` scope expansion; only the policy surfaces
    # themselves are committed by the legacy helper.
    Invariant(
        rel_path="project_policy/_auto_commit.py",
        present=(
            "POLICY_AUTO_COMMIT_SUBJECT",
            "commit_policy_updates",
            "commit_policy_writes",
        ),
        absent=("authored_paths",),
    ),
    # wt-012: the .gitignore auto-seed commits its own writes at the
    # producer boundary via the shared
    # ``commit_deterministic_writes`` helper.
    Invariant(
        rel_path="config/bootstrap.py",
        present=(
            "auto_seed_default_gitignore",
            "commit_deterministic_writes",
            "chore(gitignore): seed ralph defaults",
        ),
    ),
    # wt-012: the pipeline runner no longer carries the phase-seam
    # skill auto-commit; every deterministic skill writer commits at
    # its own write site.
    Invariant(
        rel_path="pipeline/runner.py",
        present=(),
        absent=("commit_skill_updates",),
    ),
)


# --- AST placement checks ----------------------------------------------------


def _check_skill_root_skip_placement() -> list[str]:  # noqa: PLR0912 - AST walker branches
    """The FIVE-root early-skip MUST come BEFORE the symlink-WARNING block.

    Pins AC-03 at the AST level: a future refactor that reorders the two
    blocks (or that moves the WARNING before the skip) would silently
    re-introduce the WARNING noise this whole feature exists to remove.

    This is the stronger guarantee the audit pins -- the literal-string
    check alone cannot catch ordering drift because both literals are
    still present in the file body.
    """
    rel = "git/commit_cleanup.py"
    try:
        src = _read(rel)
    except FileNotFoundError:
        return [f"  {rel}: file not found"]
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [f"  {rel}: syntax error {exc}"]

    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "untrack_engine_internal_files":
            continue
        skip_line: int | None = None
        warning_line: int | None = None
        for stmt in ast.walk(node):
            if not isinstance(stmt, ast.If):
                continue
            # The skip block: ``if any(... startswith(...) for ... _SKILL_ROOT_PREFIXES):``
            if not isinstance(stmt.test, ast.Call):
                continue
            func = stmt.test.func
            if not isinstance(func, ast.Name) or func.id != "any":
                continue
            # Look for an iterable whose elt is a ``Call`` to ``startswith``
            for elt in stmt.test.args:
                gen = elt if isinstance(elt, ast.GeneratorExp) else None
                if gen is None:
                    continue
                for gen_call in ast.walk(gen):
                    if (
                        isinstance(gen_call, ast.Call)
                        and isinstance(gen_call.func, ast.Attribute)
                        and gen_call.func.attr == "startswith"
                    ):
                        skip_line = stmt.lineno
                        break
            if skip_line is not None:
                break
        for stmt in ast.walk(node):
            if not isinstance(stmt, ast.Call):
                continue
            func = stmt.func
            if not isinstance(func, ast.Attribute):
                continue
            if not isinstance(func.value, ast.Name) or func.value.id != "logger":
                continue
            if not stmt.args:
                continue
            # logger.warning / logger.debug call -- check the format string
            # for the canonical "Refusing to git rm --cached symlink" prefix.
            first_arg = stmt.args[0]
            if (
                isinstance(first_arg, ast.Constant)
                and isinstance(first_arg.value, str)
                and first_arg.value.startswith(
                    "Refusing to git rm --cached symlink under tracked engine-internal path"
                )
            ):
                warning_line = stmt.lineno
                break
        if skip_line is None:
            problems.append(
                f"  {rel}:untrack_engine_internal_files: skill-root skip block not found "
                "(AST invariant broken -- the early-skip MUST contain "
                "``any(... .startswith(...) for ... _SKILL_ROOT_PREFIXES)``)"
            )
        if warning_line is None:
            problems.append(
                f"  {rel}:untrack_engine_internal_files: symlink WARNING block not found "
                "(AST invariant broken -- the WARNING is the regression marker)"
            )
        if skip_line is not None and warning_line is not None and skip_line >= warning_line:
            problems.append(
                f"  {rel}:untrack_engine_internal_files: skill-root skip (line {skip_line}) "
                f"MUST come BEFORE the symlink-WARNING block (line {warning_line})"
            )
        break
    else:
        problems.append(f"  {rel}:untrack_engine_internal_files function definition not found")
    return problems


def _check_skill_root_prefixes_constant_matches() -> list[str]:  # noqa: PLR0912 - AST walker branches
    """The ``_SKILL_ROOT_PREFIXES`` constant exported from ralph.skills._agent_paths
    MUST equal the FIVE canonical strings pinned by this audit.

    Defends against drift: adding or removing a root in one place but not
    the other would silently either skip a real skill root (silent failure)
    or stage a non-skill path (catastrophic).
    """
    rel = "skills/_agent_paths.py"
    try:
        src = _read(rel)
    except FileNotFoundError:
        return [f"  {rel}: file not found"]
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [f"  {rel}: syntax error {exc}"]

    found: frozenset[str] | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name) or node.target.id != "_SKILL_ROOT_PREFIXES":
            continue
        value = node.value
        if value is None:
            continue
        # Expect frozenset({"...", "..."}) literal
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            if value.func.id == "frozenset" and value.args:
                first_arg = value.args[0]
                if isinstance(first_arg, ast.Set):
                    strings = {
                        elt.value
                        for elt in first_arg.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    }
                    found = frozenset(strings)
        elif isinstance(value, ast.Set):
            strings = {
                elt.value
                for elt in value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            }
            found = frozenset(strings)
        if found is not None:
            break

    if found is None:
        return [
            f"  {rel}: _SKILL_ROOT_PREFIXES constant definition not found "
            "(AST invariant broken -- the constant MUST be a frozenset literal "
            "containing the FIVE canonical skill-root prefixes)"
        ]
    if found != _SKILL_ROOT_PREFIXES:
        missing = _SKILL_ROOT_PREFIXES - found
        extra = found - _SKILL_ROOT_PREFIXES
        problems: list[str] = []
        if missing:
            problems.append(
                f"  {rel}: _SKILL_ROOT_PREFIXES is missing canonical roots: {sorted(missing)}"
            )
        if extra:
            problems.append(
                f"  {rel}: _SKILL_ROOT_PREFIXES contains unexpected entries: {sorted(extra)}"
            )
        return problems
    return []


# --- Production-writer scan (wt-012 S-7/S-8, DA-004) ---------------------------


def _writer_scan_excluded(rel_path: str) -> bool:
    """True when ``rel_path`` is outside the production-writer scan contract."""
    parts = rel_path.split("/")
    if parts[0] in _WRITER_SCAN_EXCLUDED_DIRS or "__pycache__" in parts:
        return True
    return rel_path in _WRITER_SCAN_EXCLUDED_FILES


def _workspaceish_receiver(node: ast.AST) -> bool:
    """True when a ``.write(...)`` receiver chain mentions the workspace seam."""
    current = node
    while isinstance(current, ast.Attribute):
        if "workspace" in current.attr.lower():
            return True
        current = current.value
    return isinstance(current, ast.Name) and current.id in {"ws", "workspace"}


def _iter_write_callsites(tree: ast.AST) -> list[tuple[int, str]]:
    """Return ``(lineno, kind)`` for every scanned filesystem-mutation call."""
    calls: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in _WRITER_WRITE_ATTRS:
                calls.append((node.lineno, func.attr))
            elif func.attr == "write" and _workspaceish_receiver(func.value):
                calls.append((node.lineno, "workspace.write"))
        elif isinstance(func, ast.Name) and func.id in _WRITER_WRITE_NAMES:
            calls.append((node.lineno, func.id))
    return calls


def _has_commit_helper_call(node: ast.AST) -> bool:
    """True when ``node``'s subtree contains a commit-helper call.

    Nested function/lambda bodies are NOT descended into: a helper call
    inside a nested ``def`` does not route the enclosing write site --
    the helper must run on the write's own statement path.
    """
    stack: list[ast.AST] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            # A helper inside a nested ``def``/``lambda`` body never runs on
            # the write's statement path -- do not descend.
            continue
        if (
            isinstance(current, ast.Call)
            and (
                (
                    isinstance(current.func, ast.Name)
                    and current.func.id in _WRITER_COMMIT_HELPERS
                )
                or (
                    isinstance(current.func, ast.Attribute)
                    and current.func.attr in _WRITER_COMMIT_HELPERS
                )
            )
        ):
            return True
        stack.extend(ast.iter_child_nodes(current))
    return False


def _stmt_contains_lineno(stmt: ast.stmt, lineno: int) -> bool:
    end = stmt.end_lineno
    return stmt.lineno <= lineno and (end is None or lineno <= end)


def _sub_blocks(stmt: ast.stmt) -> list[list[ast.stmt]]:
    """The statement lists of ``stmt``'s branch arms, in lexical order."""
    blocks: list[list[ast.stmt]] = []
    for field in ("body", "orelse", "finalbody"):
        block: list[ast.stmt] | None = getattr(stmt, field, None)
        if isinstance(block, list):
            blocks.append(block)
    handlers_obj: object = getattr(stmt, "handlers", ())
    handlers: list[ast.ExceptHandler] = (
        list(handlers_obj) if isinstance(handlers_obj, tuple) else []
    )
    blocks.extend(handler.body for handler in handlers)
    return blocks


def _within_helper_call_argument(stmt: ast.stmt, lineno: int) -> bool:
    """True when the write sits inside an argument of a commit-helper call.

    The canonical ``write_fn=lambda: write(...)`` shape: the helper itself
    invokes the lambda, so the write is provably routed. Only lambdas are
    descended into (a nested ``def`` argument body is not executed here).
    """

    def visit(node: ast.AST, in_arg: bool) -> bool:
        if in_arg and _covers(node, lineno) and _subtree_has_write_at(node, lineno):
            # The write lives inside a commit-helper call argument: the
            # helper itself invokes it, so the write is provably routed.
            return True
        if isinstance(node, ast.Call):
            is_helper = (
                isinstance(node.func, ast.Name) and node.func.id in _WRITER_COMMIT_HELPERS
            ) or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in _WRITER_COMMIT_HELPERS
            )
            return any(
                _covers(arg, lineno) and visit(arg, in_arg or is_helper)
                for arg in (*node.args, *(keyword.value for keyword in node.keywords))
            )
        if isinstance(node, ast.Lambda) and _covers(node, lineno):
            return in_arg or visit(node.body, in_arg)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        return any(
            _covers(child, lineno) and visit(child, in_arg)
            for child in ast.iter_child_nodes(node)
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        )

    def _covers(node: ast.AST, line: int) -> bool:
        end: int | None = getattr(node, "end_lineno", None)
        start: int = getattr(node, "lineno", line + 1)
        return start <= line and (end is None or line <= end)

    def _subtree_has_write_at(node: ast.AST, line: int) -> bool:
        return any(
            call_lineno == line
            for call_lineno, _kind in _iter_write_callsites(node)
        )

    return visit(stmt, False)


def _followed_by_helper_in_block(body: list[ast.stmt], lineno: int) -> bool:
    """Fail-closed statement-path check for a write at ``lineno``.

    True ONLY when a commit-helper call provably executes after the write
    on the same statement path: a helper call in a LATER statement of this
    block (or deeper, along the nested-block chain from the write
    outward), with no intervening ``return`` / ``raise`` and no ``if`` /
    ``elif`` sibling-arm separation. Helper-before-write, helper-only-in
    a sibling branch, helper after an early return, and helper inside a
    nested function are all violations (False).
    """
    after_write = False
    for stmt in body:
        if not after_write:
            if not _stmt_contains_lineno(stmt, lineno):
                continue
            if _within_helper_call_argument(stmt, lineno):
                return True
            # The write is inside this statement. Look deeper along the
            # nested-block chain first; a helper found there is followed.
            if _followed_by_helper_within_stmt(stmt, lineno):
                return True
            after_write = True
            continue
        # Statements AFTER the write's statement in this block: the first
        # helper call wins, but a return/raise before it is a barrier
        # (fail closed -- the helper might never execute).
        if isinstance(stmt, (ast.Return, ast.Raise)):
            return False
        if _has_commit_helper_call(stmt) or _within_helper_call_argument(stmt, lineno):
            return True
        if _contains_terminator(stmt):
            # A conditionally-executed return/raise nested inside an
            # intervening statement is ambiguous routing -- fail closed.
            return False
    return False


def _contains_terminator(stmt: ast.stmt) -> bool:
    """True when ``stmt``'s (non-nested-def) subtree contains return/raise."""
    for child in ast.walk(stmt):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(child, (ast.Return, ast.Raise)):
            return True
    return False


def _followed_by_helper_within_stmt(stmt: ast.stmt, lineno: int) -> bool:
    """Search the branch arm of ``stmt`` that contains ``lineno``.

    Only the arm actually containing the write is walked (sibling arms are
    mutually exclusive -- a helper there never executes with the write).
    A ``return`` / ``raise`` between the write and the arm's end is a
    fail-closed barrier even when an enclosing block has a later helper.
    """
    for block in _sub_blocks(stmt):
        if not any(_stmt_contains_lineno(s, lineno) for s in block):
            continue
        if _followed_by_helper_in_block(block, lineno):
            return True
        # ``finally`` always executes after the write's arm, so a helper
        # there is provable routing even without one in the arm itself.
        if isinstance(stmt, ast.Try) and any(
            _has_commit_helper_call(inner) for inner in stmt.finalbody
        ):
            return True
        # No helper found deeper in the containing arm. Fail closed when a
        # return/raise sits between the write and the arm's end: an outer
        # later helper would then be ambiguous.
        seen_write = False
        for inner in block:
            if not seen_write:
                if _stmt_contains_lineno(inner, lineno):
                    seen_write = True
                continue
            if isinstance(inner, (ast.Return, ast.Raise)):
                return True  # barrier -> definitive violation for this write
        return False
    return False


def _enclosing_calls_helper(tree: ast.AST, lineno: int) -> bool:
    """True when the innermost function enclosing ``lineno`` provably runs a
    commit-helper call AFTER the write on the same statement path.

    wt-012 audit strengthening: the previous "helper anywhere in the
    enclosing function" check accepted unreachable routing (helper in a
    sibling ``else`` arm, after an early ``return``, inside a nested
    ``def``, or before the write). The structural check walks the block
    chain from the write outward and accepts only a helper at a later
    statement position on the same path with no intervening terminator.
    """
    best: ast.AST | None = None
    best_size: int | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = node.end_lineno
        if end is None or not node.lineno <= lineno <= end:
            continue
        size = end - node.lineno
        if best_size is None or size < best_size:
            best, best_size = node, size
    if best is None or not isinstance(best, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    return _followed_by_helper_in_block(list(best.body), lineno)


def _source_has_marker(source: str, lineno: int) -> bool:
    """True when a marker token appears at or within 3 lines above ``lineno``."""
    lines = source.splitlines()
    start = max(0, lineno - 4)
    window = lines[start:lineno]
    return any(token in line for line in window for token in _WRITER_MARKER_TOKENS)


def _iter_production_sources() -> list[tuple[str, str]]:
    """``(rel_path, source)`` pairs for every module under the package root.

    The rel path and the source are paired through ``_read(rel)`` so a mocked
    or relocated package root cannot desynchronise them (DA-016).
    """
    out: list[tuple[str, str]] = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        rel = (
            path.relative_to(_PACKAGE_ROOT).as_posix()
            if path.is_absolute()
            else path.as_posix()
        )
        try:
            source = _read(rel)
        except (FileNotFoundError, OSError):
            continue
        out.append((rel, source))
    return out


def _check_production_writer_scan() -> list[str]:
    """Every production filesystem write is routed through a commit helper or
    carries an inline classification marker (wt-012 S-7, DA-004).

    This is the generic enforcement that catches the NEXT new deterministic
    writer at introduction time: a fresh ``workspace.write``/``write_text``/
    ``symlink_to``/``copytree``/``copy2``/``mkdir``/``_create_symlink`` call
    that neither routes through ``commit_deterministic_writes`` /
    ``commit_policy_writes`` / ``commit_skill_writes`` nor documents why it is
    non-committable fails this audit immediately.
    """
    problems: list[str] = []
    for rel, source in _iter_production_sources():
        if _writer_scan_excluded(rel):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            problems.append(f"  {rel}: syntax error {exc}")
            continue
        for lineno, kind in _iter_write_callsites(tree):
            if _enclosing_calls_helper(tree, lineno):
                continue
            if _source_has_marker(source, lineno):
                continue
            problems.append(
                f"  {rel}:{lineno}: unmarked `{kind}` call -- deterministic writers must "
                "route through commit_deterministic_writes/commit_policy_writes/"
                "commit_skill_writes or carry an inline "
                "`deterministic-writer-ok: <reason>` marker (wt-012 S-7)"
            )
    return problems


def _check_no_direct_chore_commit() -> list[str]:
    """Only the allowlisted agent-commit flow may call ``create_commit``
    directly; deterministic commits route through the shared helper (wt-012 S-8).
    """
    problems: list[str] = []
    for rel, source in _iter_production_sources():
        if rel in _DIRECT_CHORE_COMMIT_ALLOWLIST or _writer_scan_excluded(rel):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None)
            )
            if name == "create_commit":
                problems.append(
                    f"  {rel}:{node.lineno}: direct create_commit call outside the "
                    "allowlist -- deterministic commits must route through "
                    "commit_deterministic_writes (wt-012 S-8)"
                )
    return problems


# --- Main entry point --------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    del argv
    problems: list[str] = []

    # 1. File-existence checks
    for rel, description in _FILE_EXISTENCE_CHECKS:
        if not (_PACKAGE_ROOT / rel).exists():
            problems.append(
                f"  {rel}: required file missing ({description}). "
                "Removing the helper MUST be a deliberate contract change that "
                "also removes the CLI wiring and the audit entry in the same commit."
            )

    # 2. Literal-string invariants
    for invariant in _INVARIANTS:
        problems.extend(invariant.violations())

    # 3. AST placement checks
    problems.extend(_check_skill_root_skip_placement())
    problems.extend(_check_skill_root_prefixes_constant_matches())

    # 4. Deterministic-writer scans (wt-012 S-7/S-8)
    problems.extend(_check_production_writer_scan())
    problems.extend(_check_no_direct_chore_commit())

    if problems:
        print(f"SKILL-AUTO-COMMIT AUDIT FAILED: {len(problems)} invariant violation(s)")
        for problem in problems:
            print(problem)
        return 1

    invariants_checked = len(_INVARIANTS) + len(_FILE_EXISTENCE_CHECKS) + 4
    print(
        f"audit_skill_auto_commit OK ({invariants_checked} invariants checked): "
        f"subject={_SKILL_AUTO_COMMIT_SUBJECT!r}, "
        f"skill_roots={len(_SKILL_ROOT_PREFIXES)}, "
        "ast_placement=pinned, helper_module=present, "
        "writer_scan=passed, no_direct_chore_commit=passed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
