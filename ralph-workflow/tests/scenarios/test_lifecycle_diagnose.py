from __future__ import annotations

import functools
import os
import re
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path

import pytest
from git import GitCommandError, Repo
from typer.testing import CliRunner

from ralph.cli.main import app
from ralph.pipeline.auto_integrate_record import IntegrationRecord, write_record

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "paused_diverged_template_reuse": (
        "diagnose paused-rebase workspace materializes a cached diverged conflict topology"
    ),
    "GitPython_repo_seed_and_sha_reads": (
        "workspace seeding and tip reads use GitPython instead of git subprocess helpers"
    ),
}

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _link_git_objects_or_copy(src: str, dst: str) -> None:
    src_path = Path(src)
    parts = src_path.parts
    if ".git" in parts:
        git_index = parts.index(".git")
        rest = parts[git_index + 1 :]
        if rest and rest[0] == "objects" and len(rest) >= 2:
            try:
                os.link(src, dst)
            except OSError:
                pass
            else:
                return
    shutil.copy2(src, dst)


def _write_commit(repo: Repo, relative: str, content: str, message: str) -> str:
    root = Path(repo.working_tree_dir or ".")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.index.add([relative])
    return repo.index.commit(message).hexsha


@functools.lru_cache(maxsize=1)
def _paused_diverged_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="lifecycle-diagnose-paused-"))
    repo = Repo.init(base, initial_branch="main")
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Ralph Test")
        writer.set_value("user", "email", "ralph@example.com")
        writer.set_value("commit", "gpgsign", "false")
    _write_commit(repo, "shared.txt", "base\n", "base")
    repo.git.checkout("-qb", "feature")
    _write_commit(repo, "shared.txt", "feature\n", "feature work")
    repo.git.checkout("-q", "main")
    _write_commit(repo, "shared.txt", "main\n", "main work")
    repo.git.checkout("-q", "feature")
    with suppress(GitCommandError):
        repo.git.rebase("main")
    repo.close()
    return base


def _materialize(dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        _paused_diverged_template(),
        dest,
        copy_function=_link_git_objects_or_copy,
        ignore=shutil.ignore_patterns("hooks", "*.sample", "description", "branches"),
    )
    return dest


def _sha(root: Path, ref: str) -> str:
    repo = Repo(root)
    try:
        return repo.commit(ref).hexsha
    finally:
        repo.close()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("HOME", str(tmp_path / ".home"))
    (tmp_path / ".config").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".home").mkdir(parents=True, exist_ok=True)
    root = _materialize(tmp_path / "workspace")
    monkeypatch.chdir(root)
    return root


def _table_text(output: str) -> str:
    cells: list[str] = []
    for line in _ANSI_ESCAPE.sub("", output).splitlines():
        if "│" not in line:
            continue
        parts = line.split("│")
        cells.extend(part.strip() for part in parts[1:-1] if part.strip())
    return " ".join(cells)


def test_diagnose_reports_relation_strategy_progress_and_risk_for_a_paused_rebase(
    workspace: Path,
) -> None:
    write_record(
        workspace,
        IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha=_sha(workspace, "feature"),
            pre_target_sha=_sha(workspace, "main"),
            resolving_rebase=True,
            resolving_paths=("shared.txt",),
        ),
    )

    result = CliRunner().invoke(app, ["--diagnose"], catch_exceptions=False)
    assert result.exit_code in (0, 1), result.output
    table = _table_text(result.output)

    assert "Relation (feature vs target)" in table, table
    assert "diverged" in table, table
    assert "Chosen strategy" in table, table
    assert "merge" in table, table
    assert "Why this strategy" in table, table
    assert "Done 0" in table, table
    assert "Remaining 1" in table, table
    assert "At risk of loss" in table, table
    assert "shared.txt" in table, table
