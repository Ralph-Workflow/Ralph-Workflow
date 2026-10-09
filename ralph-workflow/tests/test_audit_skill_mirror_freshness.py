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
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
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
