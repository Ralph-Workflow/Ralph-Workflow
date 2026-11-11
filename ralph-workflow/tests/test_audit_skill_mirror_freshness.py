"""Black-box tests for ``ralph.testing.audit_skill_mirror_freshness``.

The audit pins the wt-013 free-form skill contract: every mirrored
``<root>/<name>/SKILL.md`` must byte-equal the bundled
``ralph.skills.content.get_skill_content(name)`` for every
``name in BASELINE_SKILL_NAMES`` and every root in the FIVE canonical
project-scope skill-root prefixes. Without it, future refactors could
silently leave a divergent mirror and re-introduce the removed
structured contract (the wt-013 regression).

The audit accepts ``--repo-root PATH`` so tests can drive the actual CLI
subprocess against an isolated
``tmp_path`` tree covering the matching / missing / divergent shapes.
That makes the test portable: it runs from any checkout without a
hardcoded absolute path. The audit's own internal default still uses
the module's ``parents[3]`` walk, so the existing structural invariant
test continues to pin the offset.

The rename-regression checks at the end of this module pin the other
half of the same contract: the retired pre-rename identifier is gone
from every path NAME and every skill BODY the packaged bundle and the
five project skill roots expose, and the renamed
``submit-commit-review-artifact`` skill is the one they expose.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import ralph.testing.audit_skill_mirror_freshness as audit_module
from ralph.skills._content import BASELINE_SKILL_NAMES, get_skill_content
from ralph.testing.audit_skill_mirror_freshness import (
    _check_mirror,
    _iter_mirror_targets,
)
from ralph.testing.audit_skill_mirror_freshness import (
    main as audit_main,
)

pytestmark = pytest.mark.timeout_seconds(2.0)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pytest import MonkeyPatch

# The four subprocess CLI cases below spawn ``python -m
# ralph.testing.audit_skill_mirror_freshness`` to exercise the same
# packaging path ``make verify`` runs in the audit_skill_mirror_freshness
# step. MockProcessExecutor cannot replace them: the verify step runs
# the audit as a real subprocess, and the test's job is to mirror that
# path so a packaging regression that breaks ``python -m ...``
# invocation cannot slip past the unit test.
_SUBPROCESS_E2E = pytest.mark.subprocess_e2e


def _build_clean_mirror_workspace(tmp_path: Path) -> Path:
    """Return a tmp_path with one matching mirror for every (root, skill) pair."""
    for skill_root_prefix in audit_module._SKILL_ROOT_PREFIXES:
        mirror_root = tmp_path / skill_root_prefix.rstrip("/")
        for skill_name in BASELINE_SKILL_NAMES:
            skill_dir = mirror_root / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(get_skill_content(skill_name), encoding="utf-8")
    return tmp_path


def _run_cli_against_isolated_workspace(workspace_root: Path) -> subprocess.CompletedProcess[str]:
    """Run the actual audit CLI subprocess against an isolated ``workspace_root``.

    The audit accepts ``--repo-root PATH`` to override its default
    ``parents[3]`` walk. This is the portable shape: the test never
    assumes a particular checkout location, and the subprocess exits
    inside ``workspace_root`` so any relative path resolution inside
    the audit is anchored to the isolated tree.
    """
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ralph.testing.audit_skill_mirror_freshness",
            "--repo-root",
            str(workspace_root),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        cwd=str(Path(__file__).resolve().parents[1]),
    )


def test_audit_returns_zero_when_every_mirror_matches_bundled_content(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """All five roots x all 29 baseline skills -> exit 0."""
    _build_clean_mirror_workspace(tmp_path)
    monkeypatch.setattr(audit_module, "_REPO_ROOT", tmp_path)
    exit_code = audit_main(argv=["--repo-root", str(tmp_path)])
    assert exit_code == 0


def test_audit_returns_nonzero_when_mirror_directory_missing(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """A missing mirror directory (e.g. the recently-added analysis-decision
    skill) causes the audit to fail closed rather than silently skip the
    gap. This is the regression that produced the wt-013 4-of-5-roots gap.
    """
    _build_clean_mirror_workspace(tmp_path)
    # Remove ONE mirror entirely (one specific skill from one specific root).
    target_root = ".codex/skills"
    target_skill = "submit-development-analysis-decision-artifact"
    shutil_rmtree(tmp_path / target_root / target_skill)
    monkeypatch.setattr(audit_module, "_REPO_ROOT", tmp_path)
    exit_code = audit_main(argv=["--repo-root", str(tmp_path)])
    assert exit_code == 1


def test_audit_returns_nonzero_when_mirror_content_diverges(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """A mirror SKILL.md whose bytes differ from the bundled content fails
    the audit, even when the directory layout is otherwise correct. This
    is the contract surface the audit pins: a future skill-content edit
    that updates the bundled ``ralph/skills/content/<name>.md`` but
    forgets to re-run the project-scope installer must fail verify.
    """
    _build_clean_mirror_workspace(tmp_path)
    divergent_skill_file = (
        tmp_path / ".opencode/skills" / "submit-development-result-artifact" / "SKILL.md"
    )
    original = divergent_skill_file.read_text(encoding="utf-8")
    # Mutate the SKILL.md but restore it in a finally to keep the
    # other tests in the same session free of cross-test contamination.
    try:
        divergent_skill_file.write_text(original + "\nMALFORMED-SUFFIX\n", encoding="utf-8")
        monkeypatch.setattr(audit_module, "_REPO_ROOT", tmp_path)
        exit_code = audit_main(argv=["--repo-root", str(tmp_path)])
        assert exit_code == 1
    finally:
        divergent_skill_file.write_text(original, encoding="utf-8")


def test_audit_iter_mirror_targets_uses_canonical_roots_and_baseline_skill_set() -> None:
    """Every (root, skill) pair in the FIVE x N-29 target set is enumerated."""
    targets = _iter_mirror_targets()
    assert len(targets) == 5 * len(BASELINE_SKILL_NAMES)
    roots_seen = {root for root, _ in targets}
    assert roots_seen == set(audit_module._SKILL_ROOT_PREFIXES)
    skills_seen = {skill for _, skill in targets}
    assert skills_seen == set(BASELINE_SKILL_NAMES)


def test_audit_check_mirror_reports_clean_when_content_matches(
    tmp_path: Path,
) -> None:
    """``_check_mirror`` returns no diagnostics for a matching SKILL.md."""
    skill_root = tmp_path / ".opencode/skills"
    skill_dir = skill_root / "submit-development-result-artifact"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        get_skill_content("submit-development-result-artifact"), encoding="utf-8"
    )
    problems = _check_mirror(tmp_path, ".opencode/skills/", "submit-development-result-artifact")
    assert problems == []


def test_audit_check_mirror_reports_missing_directory(
    tmp_path: Path,
) -> None:
    """``_check_mirror`` returns one diagnostic when the mirror directory is absent."""
    problems = _check_mirror(tmp_path, ".opencode/skills/", "submit-development-result-artifact")
    assert any("mirror directory missing" in problem for problem in problems)


def test_audit_check_mirror_reports_diverged_content(
    tmp_path: Path,
) -> None:
    """``_check_mirror`` returns one diagnostic when SKILL.md bytes differ."""
    skill_dir = tmp_path / ".opencode/skills" / "submit-development-result-artifact"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text("stale", encoding="utf-8")
    problems = _check_mirror(tmp_path, ".opencode/skills/", "submit-development-result-artifact")
    assert any("diverges from bundled content" in problem for problem in problems)


def test_audit_module_path_resolution_pins_parents_3() -> None:
    """The audit derives the repository root from ``Path(__file__).resolve().parents[3]``.

    The plan and ``_project_paths.py`` discussion both pin the
    ``parents[3]`` invariant: the audit does NOT call into
    ``_project_paths`` because that module does not perform root
    discovery. The structural check below protects the offset against
    an accidental move of the audit file.
    """
    expected_root = Path(audit_module.__file__).resolve().parents[3]
    assert expected_root == audit_module._REPO_ROOT


@_SUBPROCESS_E2E
def test_audit_subprocess_cli_exits_zero_against_clean_isolated_workspace(
    tmp_path: Path,
) -> None:
    """The actual CLI entry point exits 0 against a portable matching workspace.

    Drives ``python -m ralph.testing.audit_skill_mirror_freshness`` as a
    subprocess against a fresh ``tmp_path``-rooted tree of matching
    mirrors. The audit's ``--repo-root`` override makes the test
    portable (it does not assume a particular checkout location) and
    exercises the same code path ``make verify`` runs in the
    ``audit_skill_mirror_freshness`` step. The subprocess import +
    145-mirror scan finishes well under a second standalone.
    """
    _build_clean_mirror_workspace(tmp_path)
    result = _run_cli_against_isolated_workspace(tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    assert "OK" in result.stdout


@_SUBPROCESS_E2E
def test_audit_subprocess_cli_exits_nonzero_against_isolated_missing_workspace(
    tmp_path: Path,
) -> None:
    """Subprocess CLI rejects an isolated workspace with a missing mirror.

    Removes one specific mirror from one specific root in a
    ``tmp_path``-rooted tree and runs the audit subprocess against it.
    A future skill-content edit that drops a mirror (a partial sync
    that skips one of the FIVE roots) must fail verify via this
    negative CLI path, not silently pass.
    """
    _build_clean_mirror_workspace(tmp_path)
    target_root = ".codex/skills"
    target_skill = "submit-development-analysis-decision-artifact"
    shutil_rmtree(tmp_path / target_root / target_skill)
    result = _run_cli_against_isolated_workspace(tmp_path)
    assert result.returncode == 1, f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    assert "FAILED" in result.stdout


@_SUBPROCESS_E2E
def test_audit_subprocess_cli_exits_nonzero_against_isolated_divergent_workspace(
    tmp_path: Path,
) -> None:
    """Subprocess CLI rejects an isolated workspace with a divergent mirror.

    Mutates one ``SKILL.md`` byte-set in a ``tmp_path``-rooted tree and
    runs the audit subprocess against it. A future skill-content edit
    that updates the bundled ``ralph/skills/content/<name>.md`` but
    forgets to re-run the project-scope installer must fail verify via
    this negative CLI path. The original bytes are restored in a
    ``finally`` so other tests in the same session are not contaminated.
    """
    _build_clean_mirror_workspace(tmp_path)
    divergent_skill_file = (
        tmp_path / ".opencode/skills" / "submit-development-result-artifact" / "SKILL.md"
    )
    original = divergent_skill_file.read_text(encoding="utf-8")
    try:
        divergent_skill_file.write_text(original + "\nMALFORMED-SUFFIX\n", encoding="utf-8")
        result = _run_cli_against_isolated_workspace(tmp_path)
        assert result.returncode == 1, f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        assert "FAILED" in result.stdout
    finally:
        divergent_skill_file.write_text(original, encoding="utf-8")


@_SUBPROCESS_E2E
def test_audit_subprocess_cli_exits_zero_against_clean_repo() -> None:
    """Smoke check: the actual CLI exits 0 against the real repository tree.

    This is the same end-to-end check ``make verify`` runs in the
    ``audit_skill_mirror_freshness`` step: it proves the audit's
    ``main`` function is reachable as a module CLI (the path the verify
    step uses) and that the audit's structural assumptions still match
    the on-disk layout. The audit derives the repo root from its
    module location via the ``--repo-root`` override, which is set to
    the audit's own module-derived ``_REPO_ROOT`` — a portable anchor
    that does not hardcode the current checkout.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ralph.testing.audit_skill_mirror_freshness",
            "--repo-root",
            str(audit_module._REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0, f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    assert "OK" in result.stdout


def shutil_rmtree(path: Path) -> None:
    """Local re-export of ``shutil.rmtree`` to keep imports at the top of the file.

    The function is a small inlined shim so the test file's other
    imports stay flat (one stdlib per import line) and so an
    ``import shutil`` at module scope does not need to be threaded
    into the test's failure-mode messages.
    """
    import shutil

    shutil.rmtree(path)


# Rename regression: the retired pre-rename identifier.
#
# The audit above pins that every mirror byte-equals the bundle. These
# checks pin the other half of the same contract: the retired identifier is
# gone from every path NAME and every skill BODY the packaged bundle and the
# five project skill roots expose, and the renamed skill is the one they
# expose. Without them a half-finished rename could leave a legacy skill
# mirror or a stale retired-token mention in a shipped skill body, and the
# mirror audit would happily keep mirroring it because the audit only
# compares bytes, never names.

#: The retired identifier, matched case-insensitively in both the underscore
#: and the hyphen spelling of the legacy token. The space spelling is prose,
#: not an identifier, so it is not matched.
_LEGACY_IDENTIFIER = re.compile(r"commit[-_]cleanup", re.IGNORECASE)

#: The retired identifier and the retired skill name are assembled from their
#: parts so this guard file carries the retired token only as the detector
#: pattern above, never as a literal occurrence in the tracked tree. The
#: assembled values are exactly the retired spellings the detector looks for.
_LEGACY_TOKEN = "_".join(("commit", "cleanup"))
_LEGACY_SKILL_NAME = "-".join(("submit", "commit", "cleanup", "artifact"))

#: The five canonical project-scope skill roots, relative to the repository
#: root: the roots the mirror-freshness audit mirrors the bundle into.
_PROJECT_SKILL_ROOTS: tuple[str, ...] = (
    ".opencode/skills",
    ".agents/skills",
    ".claude/skills",
    ".codex/skills",
    ".gemini/antigravity-cli/skills",
)

#: The packaged skill bundle relative to the repository root: the source of
#: truth every mirror mirrors.
_BUNDLE_CONTENT_DIR = "ralph-workflow/ralph/skills/content"

#: The packaged Python package relative to the repository root.
_PACKAGE_DIR = "ralph-workflow/ralph"

#: The skill name the rename introduced.
_RENAMED_SKILL = "submit-commit-review-artifact"

#: Derived or vendored directories. Walking them would cost time without
#: adding a name or a body the rename could have left behind.
_SKIPPED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hypothesis",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "_build",
        "build",
        "dist",
    }
)


@dataclass(frozen=True)
class _RepositoryScan:
    """The names and bodies the retired identifier could have left behind."""

    #: Entry names found under each project skill root.
    root_names: dict[str, set[str]]
    #: File bodies found under each project skill root.
    root_bodies: dict[str, dict[Path, str]]
    #: Entry names found under the packaged skill bundle.
    bundle_names: set[str]
    #: File bodies found under the packaged skill bundle.
    bundle_bodies: dict[Path, str]
    #: Entry names found under the packaged Python package.
    package_names: set[str]
    #: The parsed packaged bundle metadata.
    bundle_metadata: dict[str, Any]


def _repository_root() -> Path:
    """Return the repository root that holds the five project skill roots."""
    return Path(__file__).resolve().parents[2]


def _scan_tree(
    root: Path,
    *,
    read_contents: bool,
    visited: set[Path] | None = None,
) -> tuple[set[str], dict[Path, str]]:
    """Return the entry names and (optionally) the file bodies under ``root``.

    A directory symlink is followed at most once per resolved target, so the
    four alias roots that point at ``.opencode/skills`` are scanned without
    reading the same bytes four times and without looping on a cycle. The
    names of every entry are collected even when the directory it points at
    was already scanned, because the name of a symlink is what a legacy
    path would have carried.
    """
    names: set[str] = set()
    bodies: dict[Path, str] = {}
    seen = visited if visited is not None else set()
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            resolved = current.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            names.add(entry.name)
            if entry.is_dir():
                if entry.name not in _SKIPPED_DIRECTORY_NAMES:
                    pending.append(entry)
                continue
            if not read_contents:
                continue
            try:
                if entry.is_file():
                    bodies[entry] = entry.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return names, bodies


def _scan_repository(repo_root: Path | None = None) -> _RepositoryScan:
    """Scan the repository once for the retired-identifier assertions.

    The five project skill roots share one visited set because four of them
    are symlink farms onto ``.opencode/skills``; the package tree and the
    bundle get their own so neither can prune the other.
    """
    root = _repository_root() if repo_root is None else repo_root
    shared: set[Path] = set()
    root_names: dict[str, set[str]] = {}
    root_bodies: dict[str, dict[Path, str]] = {}
    for relative_root in _PROJECT_SKILL_ROOTS:
        names, bodies = _scan_tree(
            root / relative_root,
            read_contents=True,
            visited=shared,
        )
        root_names[relative_root] = names
        root_bodies[relative_root] = bodies
    bundle_names, bundle_bodies = _scan_tree(
        root / _BUNDLE_CONTENT_DIR,
        read_contents=True,
    )
    package_names, _ = _scan_tree(root / _PACKAGE_DIR, read_contents=False)
    metadata_path = root / _BUNDLE_CONTENT_DIR / "metadata.json"
    bundle_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    return _RepositoryScan(
        root_names=root_names,
        root_bodies=root_bodies,
        bundle_names=bundle_names,
        bundle_bodies=bundle_bodies,
        package_names=package_names,
        bundle_metadata=bundle_metadata,
    )


def _legacy_names(names: Iterable[str]) -> list[str]:
    """Return the names that still carry the retired identifier."""
    return sorted(name for name in names if _LEGACY_IDENTIFIER.search(name))


def _legacy_bodies(bodies: Mapping[Path, str]) -> list[Path]:
    """Return the files whose body still carries the retired identifier."""
    return sorted(path for path, body in bodies.items() if _LEGACY_IDENTIFIER.search(body))


def _legacy_name_offenders(scan: _RepositoryScan) -> dict[str, list[str]]:
    """Return the scopes whose entry names still carry the retired identifier."""
    offenders: dict[str, list[str]] = {}
    for relative_root, names in scan.root_names.items():
        legacy = _legacy_names(names)
        if legacy:
            offenders[relative_root] = legacy
    for scope, names in (
        (_BUNDLE_CONTENT_DIR, scan.bundle_names),
        (_PACKAGE_DIR, scan.package_names),
    ):
        legacy = _legacy_names(names)
        if legacy:
            offenders[scope] = legacy
    return offenders


def _legacy_body_offenders(scan: _RepositoryScan) -> dict[str, list[str]]:
    """Return the scopes whose file bodies still carry the retired identifier."""
    offenders: dict[str, list[str]] = {}
    for relative_root, bodies in scan.root_bodies.items():
        legacy = [str(path) for path in _legacy_bodies(bodies)]
        if legacy:
            offenders[relative_root] = legacy
    legacy_bundle = [str(path) for path in _legacy_bodies(scan.bundle_bodies)]
    if legacy_bundle:
        offenders[_BUNDLE_CONTENT_DIR] = legacy_bundle
    return offenders


@pytest.fixture(scope="module")
def repository_scan() -> _RepositoryScan:
    """Scan the repository once; the rename assertions share the result."""
    return _scan_repository()


def test_scan_tree_reports_a_planted_legacy_identifier(tmp_path: Path) -> None:
    """The walker is not vacuous: a planted legacy name and body are reported.

    A rename guard that cannot fail is not a guard. This pins the detector
    itself on an isolated tree: a directory named after the retired skill and
    a body that mentions the retired identifier must both be reported.
    """
    skill_dir = tmp_path / _LEGACY_SKILL_NAME
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(f"# {_LEGACY_TOKEN}\n", encoding="utf-8")

    names, bodies = _scan_tree(tmp_path, read_contents=True)

    assert _legacy_names(names) == [_LEGACY_SKILL_NAME]
    assert [path.name for path in _legacy_bodies(bodies)] == ["SKILL.md"]


def test_scan_repository_flags_a_planted_legacy_identifier(tmp_path: Path) -> None:
    """The repository-wide scan reports a legacy mirror planted in a fake tree.

    This is the end-to-end proof that the two repository-level assertions
    are not vacuous: the same scan and the same offender aggregation that
    run against the real checkout are driven here against an isolated tree
    that carries the retired identifier, and they must report it.
    """
    for relative_root in _PROJECT_SKILL_ROOTS:
        legacy_dir = tmp_path / relative_root / _LEGACY_SKILL_NAME
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "SKILL.md").write_text(f"# {_LEGACY_TOKEN}\n", encoding="utf-8")
    bundle_dir = tmp_path / _BUNDLE_CONTENT_DIR
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "metadata.json").write_text(
        json.dumps({"skills": [], "bundles": {}, "skill_sources": {}}),
        encoding="utf-8",
    )

    scan = _scan_repository(tmp_path)

    assert _legacy_name_offenders(scan)
    assert _legacy_body_offenders(scan)


def test_no_skill_path_name_carries_the_retired_identifier(
    repository_scan: _RepositoryScan,
) -> None:
    """No path NAME under the five roots or the package keeps the legacy token."""
    offenders = _legacy_name_offenders(repository_scan)

    assert not offenders, f"retired identifier still present in path names: {offenders}"


def test_no_skill_body_carries_the_retired_identifier(
    repository_scan: _RepositoryScan,
) -> None:
    """No skill BODY under the bundle or the five roots keeps the legacy token."""
    offenders = _legacy_body_offenders(repository_scan)

    assert not offenders, f"retired identifier still present in skill bodies: {offenders}"


def test_packaged_bundle_metadata_lists_no_legacy_skill(
    repository_scan: _RepositoryScan,
) -> None:
    """The packaged metadata names the renamed skill and carries no legacy entry."""
    metadata = repository_scan.bundle_metadata

    assert _LEGACY_SKILL_NAME not in metadata["skills"]
    assert _LEGACY_SKILL_NAME not in metadata["bundles"]
    assert _LEGACY_SKILL_NAME not in metadata["skill_sources"]
    assert _RENAMED_SKILL in metadata["skills"]


def test_bundle_and_every_project_skill_root_expose_the_renamed_review_skill() -> None:
    """The bundle and all five roots expose the renamed commit-review skill."""
    repo_root = _repository_root()

    assert (repo_root / _BUNDLE_CONTENT_DIR / f"{_RENAMED_SKILL}.md").is_file()
    missing = [
        relative_root
        for relative_root in _PROJECT_SKILL_ROOTS
        if not (repo_root / relative_root / _RENAMED_SKILL / "SKILL.md").is_file()
    ]

    assert not missing, f"skill roots missing {_RENAMED_SKILL}: {missing}"
