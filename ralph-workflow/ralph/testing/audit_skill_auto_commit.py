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
    # The CLI wiring in run.py MUST call commit_skill_updates inside
    # _sync_shipped_skills_on_pipeline_run.
    Invariant(
        rel_path="cli/commands/run.py",
        present=(
            "from ralph.skills._auto_commit import commit_skill_updates",
            "commit_skill_updates(target_root, create_commit)",
            "Auto-committed skill updates",
        ),
    ),
    # The new helper overwrites stale canonical content in the installer
    # (the locked project-scope conflict-resolution branch).
    Invariant(
        rel_path="skills/_installer.py",
        present=(
            "_materialize_canonical_skill",
        ),
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
        problems.append(
            f"  {rel}:untrack_engine_internal_files function definition not found"
        )
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

    if problems:
        print(
            f"SKILL-AUTO-COMMIT AUDIT FAILED: {len(problems)} invariant violation(s)"
        )
        for problem in problems:
            print(problem)
        return 1

    invariants_checked = len(_INVARIANTS) + len(_FILE_EXISTENCE_CHECKS) + 2
    print(
        f"audit_skill_auto_commit OK ({invariants_checked} invariants checked): "
        f"subject={_SKILL_AUTO_COMMIT_SUBJECT!r}, "
        f"skill_roots={len(_SKILL_ROOT_PREFIXES)}, "
        "ast_placement=pinned, helper_module=present"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
