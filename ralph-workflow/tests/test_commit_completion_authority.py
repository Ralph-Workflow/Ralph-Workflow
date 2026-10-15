"""Tests for the single commit-completion authority (WU-G5)."""

import ast
from pathlib import Path

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


def test_no_stray_phase_integrated_literals() -> None:
    offenders = []
    for path in sorted(REPO.joinpath("ralph").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel in _ALLOWED_PHASE_INTEGRATED:
            continue
        if 'phase="integrated"' in path.read_text(encoding="utf-8"):
            offenders.append(rel)
    assert offenders == []


def test_return_sites_route_through_authority() -> None:
    for rel in _RETURN_SITES:
        tree = ast.parse(REPO.joinpath(rel).read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert "complete_commit" in names, f"{rel} does not call complete_commit"
