from __future__ import annotations

import ast
import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _read(rel_path: str) -> str:
    return (_PACKAGE_ROOT / rel_path).read_text(encoding="utf-8")


_SKILL_AUTO_COMMIT_SUBJECT: str = "chore(skills): sync baseline bundle"

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


_FILE_EXISTENCE_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "skills/_auto_commit.py",
        "the deterministic auto-commit helper module",
    ),
)


_INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        rel_path="skills/_auto_commit.py",
        present=(
            "SKILL_AUTO_COMMIT_SUBJECT",
            _SKILL_AUTO_COMMIT_SUBJECT,
            "commit_skill_writes",
            "stage_files",
        ),
        absent=("stage_all",),
    ),
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
    Invariant(
        rel_path="git/commit_review.py",
        present=(
            "_SKILL_ROOT_PREFIXES",
            "Skipping tracked skill-root path",
        ),
    ),
    Invariant(
        rel_path="cli/commands/_run_start_setup.py",
        present=(
            "from ralph.skills._auto_commit import commit_skill_writes",
            "Auto-committed skill updates",
        ),
    ),
    Invariant(
        rel_path="skills/_installer.py",
        present=(
            "_materialize_canonical_skill",
            "install_project_baseline_skills_with_diff",
            "ProjectSkillInstallOutcome",
        ),
    ),
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
    Invariant(
        rel_path="git/_index_snapshots.py",
        present=("STAGED_DELETION_SENTINEL",),
    ),
    Invariant(
        rel_path="project_policy/preflight.py",
        present=(
            "commit_policy_writes",
            "capture_pre_write_contents",
        ),
    ),
    Invariant(
        rel_path="project_policy/finalization.py",
        present=(
            "commit_policy_writes",
            "condense_placeholder_block",
            "capture_pre_write_contents",
        ),
    ),
    Invariant(
        rel_path="project_policy/_auto_commit.py",
        present=(
            "POLICY_AUTO_COMMIT_SUBJECT",
            "commit_policy_updates",
            "commit_policy_writes",
        ),
        absent=("authored_paths",),
    ),
    Invariant(
        rel_path="config/bootstrap.py",
        present=(
            "auto_seed_default_gitignore",
            "commit_deterministic_writes",
            "chore(gitignore): seed ralph defaults",
        ),
    ),
    Invariant(
        rel_path="pipeline/runner.py",
        present=(),
        absent=("commit_skill_writes",),
    ),
)


def _find_skill_root_skip_line(node: ast.AST) -> int | None:
    for stmt in ast.walk(node):
        if not isinstance(stmt, ast.If):
            continue
        if not isinstance(stmt.test, ast.Call):
            continue
        func = stmt.test.func
        if not isinstance(func, ast.Name) or func.id != "any":
            continue
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
                    return stmt.lineno
    return None


def _find_symlink_warning_line(node: ast.AST) -> int | None:
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
        first_arg = stmt.args[0]
        if (
            isinstance(first_arg, ast.Constant)
            and isinstance(first_arg.value, str)
            and first_arg.value.startswith(
                "Refusing to git rm --cached symlink under tracked engine-internal path"
            )
        ):
            return stmt.lineno
    return None


def _skill_root_skip_placement_problems(rel: str, node: ast.FunctionDef) -> list[str]:
    problems: list[str] = []
    skip_line = _find_skill_root_skip_line(node)
    warning_line = _find_symlink_warning_line(node)
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
    return problems


def _string_set(node: ast.Set) -> frozenset[str]:
    values: set[str] = set()
    for elt in node.elts:
        if not isinstance(elt, ast.Constant):
            continue
        value = elt.value
        if isinstance(value, str):
            values.add(value)
    return frozenset(values)


def _find_skill_root_prefixes(tree: ast.Module) -> frozenset[str] | None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name) or node.target.id != "_SKILL_ROOT_PREFIXES":
            continue
        value = node.value
        if value is None:
            continue
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            if value.func.id == "frozenset" and value.args:
                first_arg = value.args[0]
                if isinstance(first_arg, ast.Set):
                    return _string_set(first_arg)
            continue
        if isinstance(value, ast.Set):
            return _string_set(value)
    return None


def _check_skill_root_skip_placement() -> list[str]:
    rel = "git/commit_review.py"
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
        problems.extend(_skill_root_skip_placement_problems(rel, node))
        break
    else:
        problems.append(f"  {rel}:untrack_engine_internal_files function definition not found")
    return problems


def _check_skill_root_prefixes_constant_matches() -> list[str]:
    rel = "skills/_agent_paths.py"
    try:
        src = _read(rel)
    except FileNotFoundError:
        return [f"  {rel}: file not found"]
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [f"  {rel}: syntax error {exc}"]

    found = _find_skill_root_prefixes(tree)

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


def main(argv: list[str] | None = None) -> int:
    del argv
    problems: list[str] = []

    for rel, description in _FILE_EXISTENCE_CHECKS:
        if not (_PACKAGE_ROOT / rel).exists():
            problems.append(
                f"  {rel}: required file missing ({description}). "
                "Removing the helper MUST be a deliberate contract change that "
                "also removes the CLI wiring and the audit entry in the same commit."
            )

    for invariant in _INVARIANTS:
        problems.extend(invariant.violations())

    problems.extend(_check_skill_root_skip_placement())
    problems.extend(_check_skill_root_prefixes_constant_matches())

    if problems:
        print(f"SKILL-AUTO-COMMIT AUDIT FAILED: {len(problems)} invariant violation(s)")
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
