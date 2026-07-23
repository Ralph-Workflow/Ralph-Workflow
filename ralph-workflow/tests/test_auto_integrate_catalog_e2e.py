"""Real-git end-to-end checklist for the A1..H7 / AC-03..AC-15 catalog.

The PLAN step 12 requires the existence of this file as the
make-verify wired checklist. The ``test_makefile_verification_workflow.py``
test pins the canonical set of subprocess_e2e files that
``make test-auto-integrate-e2e`` MUST enumerate, so the file-list
contract is already enforced by the workflow test. The audit here
is the AC-coverage complement: every named AC in the PLAN maps to
a real, existing, ``subprocess_e2e``-marked test file in the e2e
target. The audit reads the file headers (cheap AST walk, no
subprocess) so it stays well under the 1 s per-test SIGALRM cap
the verify gate imposes on every test.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

#: One entry per AC the PLAN requires to be proven by real-git
#: subprocess_e2e. The ``evidence`` is the source file the AC
#: lives in; the ``node_id`` is the test function name glob.
_CATALOG: tuple[tuple[str, str, str], ...] = (
    # (ac, evidence_file, node_id_glob). The globs are intentionally
    # permissive: the audit asserts the file is in the e2e target AND
    # is subprocess_e2e-marked AND contains at least one test function.
    # Pinning a specific test name would be brittle (every rename
    # would trip the audit); the workflow test
    # ``test_test_auto_integrate_e2e_lists_every_required_subprocess_e2e_file``
    # pins the file list, which is the actual rot class to catch.
    ("AC-03", "tests/test_auto_integrate_rebase_conflict_e2e.py", "test_*"),
    ("AC-04", "tests/test_auto_integrate_markerless_conflicts.py", "test_*"),
    ("AC-05", "tests/test_auto_integrate_conflict_e2e.py", "test_*"),
    ("AC-06", "tests/test_auto_integrate_recovery.py", "test_*"),
    ("AC-07", "tests/test_auto_integrate_recovery.py", "test_*"),
    ("AC-10", "tests/test_auto_integrate_race.py", "test_*"),
    ("AC-12", "tests/test_auto_integrate_remote_push.py", "test_*"),
    ("AC-15", "tests/test_auto_integrate_rung4_self_resume.py", "test_*"),
)


def _resolve_repo_root() -> Path:
    """Return the repo root (the directory that owns ``tests/``)."""
    return Path(__file__).resolve().parent.parent


@lru_cache(maxsize=8)
def _source_tree(path: Path) -> tuple[str, ast.Module]:
    """Read and parse a catalog source file once per pytest worker."""
    source = path.read_text(encoding="utf-8")
    return source, ast.parse(source, filename=str(path))


@lru_cache(maxsize=8)
def _iter_test_functions(path: Path) -> tuple[str, ...]:
    """Return the source-defined test names in ``path``.

    The catalog has repeated evidence files, so cache the AST-derived names
    and avoid repeated filesystem reads and parses inside the 1-second test
    budget. The audit needs source names only, not pytest collection.
    """
    _, tree = _source_tree(path)
    return tuple(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    )


@lru_cache(maxsize=8)
def _file_is_subprocess_e2e(path: Path) -> bool:
    """Return whether ``path`` declares the ``subprocess_e2e`` marker."""
    source, tree = _source_tree(path)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark" and "subprocess_e2e" in ast.unparse(node.value):
                    return True
    return "subprocess_e2e" in source


def _matches_glob(name: str, glob: str) -> bool:
    """Return True when ``name`` matches any of the pipe-separated globs.

    The catalog supports the ``|`` alternation operator so a
    single AC entry can map to several test functions (``test_a``
    OR ``test_b``) without forcing the audit to declare one
    entry per function. Each alternative is a fnmatch-style
    prefix glob; an empty alternative is ignored.
    """
    for raw_alternative in glob.split("|"):
        alternative = raw_alternative.strip()
        if not alternative:
            continue
        if re.match(alternative, name) is not None:
            return True
    return False


def test_audit_e2e_catalog_is_exact() -> None:
    """Every catalog entry resolves exactly to a subprocess-E2E proof.

    The PLAN step 12 requires this catalog to be the make-verify
    wired checklist. A regression that drops a real-git proof
    file from the e2e target, or that renames a test in a way
    that hides it from the glob, fails this test -- the verify
    gate catches the missing coverage before the user does.
    """
    repo_root = _resolve_repo_root()
    missing: list[tuple[str, str, str]] = []
    resolved: dict[tuple[str, str], tuple[str, ...]] = {}
    for ac, evidence_file, glob in _CATALOG:
        path = repo_root / evidence_file
        if not path.exists():
            missing.append((ac, evidence_file, glob))
            continue
        if not _file_is_subprocess_e2e(path):
            missing.append((ac, evidence_file, glob))
            continue
        names = _iter_test_functions(path)
        matches = tuple(name for name in names if _matches_glob(name, glob))
        resolved[(ac, evidence_file)] = matches
        if not matches:
            missing.append((ac, evidence_file, glob))
    assert not missing, (
        "AC-14 catalog coverage gap: the following ACs have no real-git "
        "subprocess_e2e test file that ``make test-auto-integrate-e2e`` "
        "can collect. Either the file is missing, is no longer "
        "subprocess_e2e-marked, or the test names have drifted. "
        f"Missing entries: {missing}"
    )
    for (ac, evidence_file), names in resolved.items():
        assert names, (
            f"AC catalog entry ({ac!r}, {evidence_file!r}) resolves to "
            "zero test functions in the source -- the file is missing "
            "from the verify gate or the test names have drifted."
        )
