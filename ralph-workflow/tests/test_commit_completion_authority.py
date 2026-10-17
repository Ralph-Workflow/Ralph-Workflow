"""Tests for the single commit-completion authority (WU-G5)."""

import ast
import os
from pathlib import Path

import pytest

from ralph.pipeline._commit_completion_authority import complete_commit
from ralph.pipeline.auto_integrate_record import IntegrationRecord

REPO = Path(__file__).resolve().parent.parent

# The seven return sites that must route through the authority.
_RETURN_SITES = (
    "ralph/pipeline/_pending_merge_commit.py",
    "ralph/pipeline/_pending_rebase_continue.py",
    "ralph/pipeline/_integration_continuation.py",
    "ralph/pipeline/_remote_reconcile_recovery.py",
    "ralph/pipeline/_owned_landing_reintegration.py",
    "ralph/pipeline/auto_integrate.py",
)

_ALLOWED_PHASE_INTEGRATED = (
    "ralph/pipeline/auto_integrate_record.py",
    "ralph/pipeline/_commit_completion_authority.py",
)


def _make_record() -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
    )


def test_complete_commit_promotes_to_integrated() -> None:
    completion = complete_commit(
        _make_record(),
        integrated_feature_sha="feed00",
    )
    assert completion.record.phase == "integrated"
    assert completion.record.integrated_feature_sha == "feed00"
    assert completion.integrated_feature_sha == "feed00"


def test_complete_commit_threads_extra_fields() -> None:
    completion = complete_commit(
        _make_record(),
        integrated_feature_sha="feed00",
        reintegrate_pending=False,
    )
    assert completion.record.reintegrate_pending is False


_STRAY_LITERAL = b'phase="integrated"'


def _scan_for_stray(dir_path: str, repo_str: str, offenders: list[str]) -> None:
    try:
        entries = os.scandir(dir_path)
    except OSError:
        return
    with entries:
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name != "__pycache__":
                        _scan_for_stray(entry.path, repo_str, offenders)
                elif entry.name.endswith(".py") and entry.is_file(follow_symlinks=False):
                    content = Path(entry.path).read_bytes()
                    if _STRAY_LITERAL in content:
                        rel_path = os.path.relpath(entry.path, repo_str).replace(os.sep, "/")
                        if rel_path not in _ALLOWED_PHASE_INTEGRATED:
                            offenders.append(rel_path)
            except OSError:
                continue


@pytest.mark.timeout_seconds(5)
def test_no_stray_phase_integrated_literals() -> None:
    ralph_dir = str(REPO / "ralph")
    repo_str = str(REPO)
    offenders: list[str] = []
    _scan_for_stray(ralph_dir, repo_str, offenders)
    assert sorted(offenders) == []


def test_return_sites_route_through_authority() -> None:
    for rel in _RETURN_SITES:
        tree = ast.parse(REPO.joinpath(rel).read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert "complete_commit" in names, f"{rel} does not call complete_commit"
