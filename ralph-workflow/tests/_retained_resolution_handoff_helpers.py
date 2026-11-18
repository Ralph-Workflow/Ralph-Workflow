from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git.git_call_policy import classify_git_outcome
from ralph.git.git_run_result import GitRunResult
from ralph.git.subprocess_runner import GitRunOptions
from ralph.pipeline.auto_integrate_record import IntegrationRecord, write_record
from ralph.timeout_defaults import GIT_SUBPROCESS_TIMEOUT_SECONDS
from tests.scenarios._integration_scenario_fixtures import (
    REPO_TEMPLATE_IGNORE,
    cached_repo,
    close_cached_repo,
    interrupted_resolution_repo,
)
from tests.test_pending_merge_commit_recovery import _commit, _fast_rev_parse, _git

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.git.git_call_policy import GitCallClass
    from ralph.prompts.commit_evidence import CommitEvidenceSource

pytestmark = pytest.mark.subprocess_e2e

_SCRUBBED_GIT_ENV_KEYS = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"})
_GIT_BATCH_MODE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "Never",
    "GIT_EDITOR": ":",
    "GIT_SEQUENCE_EDITOR": ":",
    "GIT_PAGER": "cat",
    "EDITOR": ":",
    "VISUAL": ":",
    "LC_ALL": "C",
    "GIT_NO_REPLACE_OBJECTS": "1",
}


def _spawn_env(caller_env: dict[str, str] | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    for source in (os.environ, _GIT_BATCH_MODE_ENV):
        for key, value in source.items():
            if key in _SCRUBBED_GIT_ENV_KEYS:
                continue
            merged[key] = value
    if caller_env is not None:
        for key, value in caller_env.items():
            if key in _SCRUBBED_GIT_ENV_KEYS:
                continue
            merged[key] = value
    return merged


def _fast_spawn_git(
    args: Sequence[str],
    *,
    cwd: Path | None,
    label: str,
    options: GitRunOptions | None = None,
) -> GitRunResult:
    del label
    effective_options = options or GitRunOptions()
    effective_timeout = (
        effective_options.timeout
        if effective_options.timeout is not None
        else GIT_SUBPROCESS_TIMEOUT_SECONDS
    )
    input_data = (
        effective_options.input_data
        if isinstance(effective_options.input_data, bytes)
        else effective_options.input_data.encode()
        if isinstance(effective_options.input_data, str)
        else None
    )
    cmd = ("git", *args)
    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=_spawn_env(effective_options.env),
        input=input_data,
        capture_output=effective_options.capture_output,
        timeout=effective_timeout,
        check=False,
    )
    stdout_raw = completed.stdout if effective_options.capture_output else b""
    stderr_raw = completed.stderr if effective_options.capture_output else b""
    stdout = (
        stdout_raw.decode() if isinstance(stdout_raw, (bytes, bytearray)) else (stdout_raw or "")
    )
    stderr = (
        stderr_raw.decode() if isinstance(stderr_raw, (bytes, bytearray)) else (stderr_raw or "")
    )
    if effective_options.check and completed.returncode != 0:
        raise subprocess.CalledProcessError(completed.returncode, list(cmd), stdout, stderr)
    return GitRunResult(args=cmd, returncode=completed.returncode, stdout=stdout, stderr=stderr)


def _fast_route_run_git(
    args: Sequence[str],
    *,
    cwd: Path | None,
    label: str,
    options: GitRunOptions | None = None,
    call_class: GitCallClass | None = None,
) -> GitRunResult:
    del call_class
    result = _fast_spawn_git(args, cwd=cwd, label=label, options=options)
    return replace(result, outcome=classify_git_outcome(returncode=result.returncode))


def install_fast_git_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ralph.git.git_command_queue.route_run_git", _fast_route_run_git)


def _link_or_copy(src: str, dst: str, *, follow_symlinks: bool = True) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst, follow_symlinks=follow_symlinks)


def _clone_template(template: Path, dest: Path) -> Path:
    close_cached_repo(template)
    close_cached_repo(dest)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        template,
        dest,
        ignore=REPO_TEMPLATE_IGNORE,
        copy_function=_link_or_copy,
    )
    return dest


def _sha(root: Path, ref: str = "HEAD") -> str:
    fast = _fast_rev_parse(root, ref)
    if fast is not None:
        return fast
    close_cached_repo(root)
    return cached_repo(root).commit(ref).hexsha


def _blob(root: Path, ref: str, path: str) -> str:
    close_cached_repo(root)
    return cached_repo(root).commit(ref).tree[path].data_stream.read().decode("utf-8")


def _stage(root: Path, *paths: str) -> None:
    index = cached_repo(root).index
    path_set = frozenset(paths)
    for key in tuple(index.entries):
        if key[0] in path_set and key[1] != 0:
            del index.entries[key]
    index.add(list(paths))


def _write_tree(root: Path) -> str:
    close_cached_repo(root)
    return str(cached_repo(root).index.write_tree())


class _EmptyCommitEvidenceSource:
    def list_changed_paths(self, _repo_root: Path) -> list[str]:
        return []

    def commit_generation_diff(self, _repo_root: Path) -> str:
        return ""


_EMPTY_COMMIT_EVIDENCE_SOURCE: CommitEvidenceSource = _EmptyCommitEvidenceSource()

_COMPLETED_OWNED_CACHE: dict[str, tuple[Path, str, str]] = {}
_COMPLETED_LANDING_CACHE: dict[str, tuple[Path, str, str, str]] = {}


def _completed_owned_template(operation: str, *, reintegrate: bool) -> tuple[Path, str, str]:
    key = f"{operation}:{reintegrate}"
    if key not in _COMPLETED_OWNED_CACHE:
        from git import Repo

        root = Path(tempfile.mkdtemp(prefix=f"it-completed-owned-{operation}-"))
        repo = Repo.init(root, initial_branch="main")
        with repo.config_writer() as cfg:
            cfg.set_value("user", "name", "Test User")
            cfg.set_value("user", "email", "test@example.com")
            cfg.set_value("core", "fsync", "none")
            cfg.set_value("gc", "auto", "0")
            cfg.set_value("core", "logAllRefUpdates", "true")
            cfg.set_value("commit", "gpgSign", "false")
        (root / "README.md").write_text("test", encoding="utf-8")
        repo.index.add(["README.md"])
        repo.index.commit("initial commit")
        repo.close()
        target = "main"
        _commit(root, "base.txt", "base\n")
        assert _git(root, "switch", "-c", "feature").returncode == 0
        feature = _commit(root, "feature.txt", "feature\n")
        assert _git(root, "switch", target).returncode == 0
        moved = _commit(root, "target.txt", "target\n")
        assert _git(root, "switch", "feature").returncode == 0
        write_record(
            root,
            IntegrationRecord(
                phase="integrating",
                target=target,
                pre_feature_sha=feature,
                pre_target_sha=moved,
                reintegrate_pending=reintegrate,
            ),
        )
        assert _git(root, operation, target).returncode == 0
        completed = _sha(root)
        close_cached_repo(root)
        _COMPLETED_OWNED_CACHE[key] = (root, target, completed)
    return _COMPLETED_OWNED_CACHE[key]


def _completed_landing_template(operation: str, phase: str) -> tuple[Path, str, str, str]:
    key = f"{operation}:{phase}"
    if key not in _COMPLETED_LANDING_CACHE:
        root = Path(tempfile.mkdtemp(prefix=f"it-completed-landing-{key}-")) / "repo"
        target, feature, main = interrupted_resolution_repo(root, operation)
        pre_target = cached_repo(root).commit("main~1").hexsha if phase == "integrated" else main
        write_record(
            root,
            IntegrationRecord(
                phase=phase,
                target=target,
                pre_feature_sha=feature,
                pre_target_sha=pre_target,
                integrated_feature_sha=feature if phase == "integrated" else None,
            ),
        )
        (root / "shared.txt").write_text("valuable manual resolution\n", encoding="utf-8")
        _stage(root, "shared.txt")
        index_before = _write_tree(root)
        head_before = _sha(root)
        close_cached_repo(root)
        _COMPLETED_LANDING_CACHE[key] = (root, target, index_before, head_before)
    return _COMPLETED_LANDING_CACHE[key]
