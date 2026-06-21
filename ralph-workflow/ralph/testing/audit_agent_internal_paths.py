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

Usage:
    python -m ralph.testing.audit_agent_internal_paths

Exit 0 = clean, 1 = at least one invariant violated.
"""

from __future__ import annotations

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


# Accept set: every canonical Ralph runtime artifact.
_BEHAVIORAL_ACCEPT_PATHS: tuple[str, ...] = (
    # Top-level basenames under .agent/ (14 total per AGENT_INTERNAL_TOP_LEVEL_BASENAMES).
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
    ".agent/rebase.lock",
    ".agent/start_commit",
    ".agent/mcp.toml",
    # Dir-segment paths (7 canonical dirs, each with a dir-appropriate
    # extension per ``_AGENT_INTERNAL_DIR_FILE_EXTENSIONS``).
    ".agent/raw/opencode.log",
    ".agent/tmp/mcp-server.log",
    ".agent/artifacts/x.json",
    ".agent/workers/unit-a/tmp/checkpoint.json",
    ".agent/receipts/run-1/commit_cleanup.json",
    ".agent/prompt_history/x.json",
    ".agent/artifact-formats/x.md",
    # Completion-sentinel glob (canonical on-disk filename pattern).
    ".agent/completion_seen_abc-123.json",
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

    literal_count = sum(len(i.present) + len(i.absent) for i in _INVARIANTS)
    behavioral_count = len(_BEHAVIORAL_ACCEPT_PATHS) + len(_BEHAVIORAL_REJECT_PATHS)
    total = literal_count + behavioral_count

    if problems:
        print(f"AGENT-INTERNAL-PATHS AUDIT FAILED: {len(problems)} invariant violation(s)")
        print("=" * 72)
        for line in problems:
            print(f"  {line}")
        print()
        print(
            "The Ralph runtime-artifact allowlist has drifted between the leaf module "
            "(_agent_internal_paths.py), the commit_cleanup fast-path, and the bootstrap "
            "gitignore/exclude seed. Re-read the rework plan in PLAN.md and restore the "
            "missing/forbidden literals."
        )
        return 1

    print(
        f"audit_agent_internal_paths OK ({total} invariants checked): "
        "_agent_internal_paths.py exports the canonical frozensets + completion_seen_*.json "
        "glob + is_agent_internal_path predicate, "
        "commit_cleanup.py imports and invokes is_agent_internal_path as the fast-path, "
        "bootstrap.py defines _DEFAULT_GIT_EXCLUDE_PATTERNS + auto_seed_default_git_exclude "
        "+ root-anchored /checkpoint.json (NOT bare checkpoint.json), "
        f"behavioral check accepts all {len(_BEHAVIORAL_ACCEPT_PATHS)} canonical paths "
        f"and rejects all {len(_BEHAVIORAL_REJECT_PATHS)} negative paths."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
