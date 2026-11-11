from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

_CATALOG: tuple[tuple[str, str, str], ...] = (
    (
        "AC-03",
        "tests/test_auto_integrate.py",
        "test_no_commits_beyond_target_skips_with_zero_mutation",
    ),
    ("AC-04", "tests/test_auto_integrate_markerless_conflicts.py", "test_*"),
    ("AC-05", "tests/test_auto_integrate.py", "test_diverged_clean_rebase_then_ff"),
    ("AC-06", "tests/test_auto_integrate_recovery.py", "test_*"),
    ("AC-07", "tests/test_auto_integrate_recovery.py", "test_*"),
    ("AC-10", "tests/test_auto_integrate_race.py", "test_*"),
    ("AC-15", "tests/test_auto_integrate_rung4_self_resume.py", "test_*"),
)


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@lru_cache(maxsize=8)
def _source_tree(path: Path) -> tuple[str, ast.Module]:
    source = path.read_text(encoding="utf-8")
    return source, ast.parse(source, filename=str(path))


@lru_cache(maxsize=8)
def _iter_test_functions(path: Path) -> tuple[str, ...]:
    _, tree = _source_tree(path)
    return tuple(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    )


@lru_cache(maxsize=8)
def _file_is_subprocess_e2e(path: Path) -> bool:
    source, tree = _source_tree(path)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "pytestmark"
                    and "subprocess_e2e" in ast.unparse(node.value)
                ):
                    return True
    return "subprocess_e2e" in source


def _matches_glob(name: str, glob: str) -> bool:
    for raw_alternative in glob.split("|"):
        alternative = raw_alternative.strip()
        if not alternative:
            continue
        if re.match(alternative, name) is not None:
            return True
    return False


def test_audit_e2e_catalog_is_exact() -> None:
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
