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
    Invariant(
        rel_path="project_policy/cli_integration.py",
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


# --- Production-writer registry (wt-012 enforcement) -------------------------
#
# Every deterministic tracked-file writer in ralph/ MUST either route
# through the shared ``commit_deterministic_writes`` helper (preferred)
# OR carry an inline ``# deterministic-writer-ok: <reason>`` marker
# naming its commit route or its classification as non-committable
# runtime state (e.g. ignored cache files, private /tmp dirs). A direct
# chore-purpose ``create_commit`` call in ralph/ production code that
# is NOT inside ``commit_scoped_updates`` / ``commit_deterministic_writes``
# / ``commit_skill_updates`` / ``commit_skill_writes`` / ``commit_policy_updates``
# / ``commit_policy_writes`` is FORBIDDEN -- the helper is the single
# isolation primitive; ad-hoc commits would re-introduce the wt-01 / wt-09
# agent-work-sweep-in bug.
#
# This registry is the source of truth for the writer enumeration.
# Adding a new deterministic writer: register it here AND route its commit
# through ``commit_deterministic_writes`` (or add an inline marker naming
# the commit route).

# Per-file rule: a path is classified as either
#   - "tracked" : the file writes tracked, committable paths (default);
#                  the writer MUST route through ``commit_deterministic_writes``
#                  OR carry an inline ``# deterministic-writer-ok: ...`` marker.
#   - "ignored" : the file writes ONLY non-committable runtime state
#                  (ignored cache files, private /tmp paths, .git/info/exclude
#                  after-the-fact writes, etc.). Such files carry an
#                  inline marker and are not enforced.
# The default is "tracked"; non-committable writers mark themselves.
_WRITER_AUDIT_RULES: tuple[dict[str, object], ...] = (
    # writer_path: string literal or AST pattern that identifies a
    # tracked write site; rule: "tracked" or "ignored".
    {
        "path": "skills/_installer.py",
        "writer_literals": (
            "copytree",  # sibling fan-out materialization
            "_create_symlink",  # sibling symlink materialization
        ),
        "rule": "tracked",
    },
    {
        "path": "project_policy/agents_md.py",
        "writer_literals": (
            "workspace.write",  # bootstrap + condense
        ),
        "rule": "tracked",
    },
    {
        "path": "project_policy/_schema_upgrade.py",
        "writer_literals": (
            "workspace.write",  # schema-freeze marker rewrite
        ),
        "rule": "tracked",
    },
    {
        "path": "project_policy/starters/__init__.py",
        "writer_literals": (
            "workspace.write",  # starter seeding
        ),
        "rule": "tracked",
    },
    {
        "path": "config/bootstrap.py",
        "writer_literals": (
            "_atomic_append_text",  # .gitignore + .git/info/exclude seeding
        ),
        "rule": "tracked",
    },
)


# Helper symbols whose call sites are the legitimate commit entry points.
# Calls to ``create_commit`` inside these helpers are ALLOWED; calls
# elsewhere in ralph/ production code are FORBIDDEN by
# ``_check_no_direct_chore_commit``.
_COMMIT_HELPER_MODULES: frozenset[str] = frozenset(
    {
        "ralph.git.scoped_auto_commit",
        "ralph.git.operations",
        "ralph.skills._auto_commit",
        "ralph.skills._installer",
        "ralph.project_policy._auto_commit",
        "ralph.project_policy.preflight",
        "ralph.project_policy._schema_upgrade",
    }
)


def _check_no_direct_chore_commit() -> list[str]:
    """No direct ``create_commit`` call in production code outside the helpers.

    Any ``create_commit(...)`` call in ralph/ that is NOT inside a
    helper listed in ``_COMMIT_HELPER_MODULES`` (or is not routed
    through ``commit_scoped_updates`` / ``commit_deterministic_writes``)
    is a regression: a future ad-hoc chore commit would re-introduce
    the deterministic-writer isolation bug. The audit scans every
    ralph/ production module and flags direct calls.

    Returns the list of violations (each is ``<file>:<line>`` plus
    the violation reason).
    """
    problems: list[str] = []
    for py_path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        # Skip the audit itself, tests, and non-production submodules.
        rel = py_path.relative_to(_PACKAGE_ROOT).as_posix()
        if rel.startswith("testing/"):
            continue
        if rel.startswith("__pycache__/"):
            continue
        # Use the patchable ``_read`` (matches the rest of the audit)
        # so tests can monkeypatch production file content.
        src = _read(rel)
        if "create_commit" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        # Build a line->content map so we can detect inline
        # ``# deterministic-writer-ok: ...`` markers on the call line
        # or its 3-line prelude (a future refactor moving the marker
        # up to the call site would still be detected).
        src_lines = src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Name) or func.id != "create_commit":
                continue
            # Direct ``create_commit(...)`` call. The only legitimate
            # call sites are inside the helpers listed above. We
            # allow ``create_commit`` inside any of those modules AND
            # inside the testing/ audit (which itself imports the
            # symbol for tests).
            if any(
                rel == f"{name.split('.', 1)[1]}.py".replace("/", "/")
                or rel.startswith(name.split(".", 1)[1].replace(".", "/") + "/")
                for name in _COMMIT_HELPER_MODULES
                if name.startswith("ralph.")
            ):
                continue
            # Check for an inline ``# deterministic-writer-ok:`` marker
            # on the call line or the three lines above it.
            line_idx = max(0, (node.lineno or 1) - 4)
            window = src_lines[line_idx : (node.lineno or 1)]
            has_marker = any("deterministic-writer-ok" in line for line in window)
            if has_marker:
                continue
            problems.append(
                f"  {rel}:{node.lineno}: direct create_commit call in production code; "
                "route via commit_scoped_updates / commit_deterministic_writes "
                "(or add an inline # deterministic-writer-ok: marker)"
            )
    return problems


def _check_production_writer_scan() -> list[str]:
    """Scan all production files for tracked writes, requiring commit routing or an inline marker.

    AST-walks every production file in ralph/ (excluding testing/ and
    __pycache__/). If a call to a tracked write primitive (such as
    workspace.write in project_policy/, or copytree/_create_symlink/symlink_to
    in skills/) is encountered, the file MUST route through
    commit_deterministic_writes / commit_policy_writes / commit_skill_writes
    OR the call line / its 3-line prelude MUST carry an inline
    ``# deterministic-writer-ok: <reason>`` marker.
    """
    problems: list[str] = []
    target_dirs = ("project_policy", "skills", "config")
    for subdir in target_dirs:
        sub_path = _PACKAGE_ROOT / subdir
        if not sub_path.exists():
            continue
        for py_path in sorted(sub_path.rglob("*.py")):
            rel = py_path.relative_to(_PACKAGE_ROOT).as_posix()
            if rel.startswith("testing/"):
                continue
            if "__pycache__" in rel:
                continue
            src = _read(rel)
            if not any(k in src for k in ("write", "copytree", "symlink_to", "_create_symlink")):
                continue
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            src_lines = src.splitlines()
            routes_through_helper = any(
                h in src
                for h in (
                    "commit_deterministic_writes",
                    "commit_policy_writes",
                    "commit_skill_writes",
                    "commit_scoped_updates",
                )
            )
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                is_tracked_write = (
                    (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr == "write"
                        and (rel.startswith("project_policy/") or rel.startswith("skills/"))
                    )
                    or (
                        rel.startswith("skills/")
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"copytree", "symlink_to"}
                    )
                    or (
                        rel.startswith("skills/")
                        and isinstance(node.func, ast.Name)
                        and node.func.id in {"_create_symlink", "copytree"}
                    )
                )

                if not is_tracked_write:
                    continue
                if routes_through_helper:
                    continue

                line_idx = max(0, (node.lineno or 1) - 4)
                window = src_lines[line_idx : (node.lineno or 1)]
                has_marker = any("deterministic-writer-ok" in line for line in window)
                if not has_marker:
                    problems.append(
                        f"  {rel}:{node.lineno}: unmarked deterministic writer site; "
                        "route via commit_deterministic_writes or add an inline "
                        "# deterministic-writer-ok: <reason> marker"
                    )
    return problems


def _check_writer_routing() -> list[str]:
    """Every registered tracked writer MUST route through
    ``commit_deterministic_writes`` OR carry an inline marker.

    The rule is enforced for each writer site in ``_WRITER_AUDIT_RULES``
    whose rule is "tracked". A tracked writer that lacks BOTH routing
    AND a marker is a regression.
    """
    problems: list[str] = []
    for rule in _WRITER_AUDIT_RULES:
        rel_raw: object = rule["path"]
        writer_literals_raw: object = rule["writer_literals"]
        rule_kind_raw: object = rule["rule"]
        if not isinstance(rel_raw, str):
            continue
        rel = rel_raw
        if not isinstance(writer_literals_raw, tuple):
            continue
        writer_literals: tuple[object, ...] = writer_literals_raw
        if not isinstance(rule_kind_raw, str):
            continue
        rule_kind = rule_kind_raw
        try:
            content = _read(rel)
        except FileNotFoundError:
            problems.append(
                f"  {rel}: required writer-audit file missing (delete must update audit registry)"
            )
            continue
        # The writer routes through commit_deterministic_writes if the
        # file imports + calls the helper.
        routes_through_helper = "commit_deterministic_writes" in content
        # Or carries an inline deterministic-writer-ok marker.
        carries_marker = "deterministic-writer-ok" in content
        if rule_kind == "tracked" and not (routes_through_helper or carries_marker):
            problems.append(
                f"  {rel}: tracked writer ({writer_literals!r}) MUST either "
                "route through commit_deterministic_writes or carry an "
                "inline ``# deterministic-writer-ok: <reason>`` marker"
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

    # 4. Production-writer enforcement (wt-012)
    problems.extend(_check_no_direct_chore_commit())
    problems.extend(_check_writer_routing())
    problems.extend(_check_production_writer_scan())

    if problems:
        print(f"SKILL-AUTO-COMMIT AUDIT FAILED: {len(problems)} invariant violation(s)")
        for problem in problems:
            print(problem)
        return 1

    invariants_checked = (
        len(_INVARIANTS)
        + len(_FILE_EXISTENCE_CHECKS)
        + 2  # AST placement checks
        + len(_WRITER_AUDIT_RULES)  # writer-routing checks
        + 1  # direct-chore-commit check
        + 1  # production-wide writer scan
    )
    print(
        f"audit_skill_auto_commit OK ({invariants_checked} invariants checked): "
        f"subject={_SKILL_AUTO_COMMIT_SUBJECT!r}, "
        f"skill_roots={len(_SKILL_ROOT_PREFIXES)}, "
        "ast_placement=pinned, helper_module=present, "
        f"writer_routing={len(_WRITER_AUDIT_RULES)}_tracked, "
        "production_writer_scan=enforced"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
