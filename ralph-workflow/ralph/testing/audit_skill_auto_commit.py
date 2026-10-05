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
_WRITER_COMMIT_HELPERS: frozenset[str] = frozenset(
    {
        "commit_deterministic_writes",
        "commit_scoped_updates",
        "commit_policy_writes",
        "commit_skill_writes",
    }
)

#: Filesystem-mutation attribute calls scanned by the production-writer scan
#: (wt-012 S-7, DA-004): the broader mutation-method class the pre-wt-012
#: scan enforced, restored after the policy-commit move dropped it.
_WRITER_WRITE_ATTRS: frozenset[str] = frozenset(
    {"write_text", "write_bytes", "copytree", "copy2", "symlink_to"}
)

#: Bare-name mutation calls scanned by the production-writer scan.
_WRITER_WRITE_NAMES: frozenset[str] = frozenset(
    {"write_text", "write_bytes", "copytree", "copy2", "mkdir", "_create_symlink"}
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
            "STAGED_DELETION_SENTINEL",
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


def _enclosing_calls_helper(tree: ast.AST, lineno: int) -> bool:
    """True when the innermost function enclosing ``lineno`` calls a commit helper."""
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
    if best is None:
        return False
    return any(
        isinstance(child, ast.Call)
        and (
            (isinstance(child.func, ast.Name) and child.func.id in _WRITER_COMMIT_HELPERS)
            or (
                isinstance(child.func, ast.Attribute)
                and child.func.attr in _WRITER_COMMIT_HELPERS
            )
        )
        for child in ast.walk(best)
    )


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
