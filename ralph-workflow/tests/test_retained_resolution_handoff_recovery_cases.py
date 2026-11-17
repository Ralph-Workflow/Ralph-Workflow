from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope
from tests import test_retained_resolution_handoff as _retained_resolution_handoff
from tests._resolver_controller import make_resolver_controller
from tests.scenarios._integration_scenario_fixtures import (
    cached_repo,
    diverged_repo_template,
    interrupted_resolution_repo,
    retained_resolution_config,
)
from tests.test_retained_resolution_handoff import (
    _blob,
    _clone_template,
    _completed_landing_template,
    _completed_owned_template,
    _git,
    _sha,
    _write_tree,
)

_patch_git_spawn = _retained_resolution_handoff._patch_git_spawn

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStop

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def test_owned_landing_reintegrates_moved_target_without_releasing_dispatch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo-owned-landing"
    diverged, target, feature, _main = diverged_repo_template()
    _clone_template(diverged, root)
    base = cached_repo(root).commit("main~1").hexsha
    write_record(
        root,
        IntegrationRecord(
            phase="integrated",
            target=target,
            pre_feature_sha=feature,
            pre_target_sha=base,
            integrated_feature_sha=feature,
        ),
    )
    config = retained_resolution_config(target, enabled=True)

    controller = make_resolver_controller(FakeClock())
    declined = recover_incomplete_integration(
        WorkspaceScope(root),
        config=config,
        rebase_stop_resolver=lambda _root, _target, _stop: False,
        conflict_resolver=lambda _root, _target: False,
        recovery_controller=controller,
    )
    assert declined is not None and not declined.fast_forwarded
    assert read_record(root) is not None
    assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed

    interrupted = read_record(root)
    assert interrupted is not None
    write_record(
        root,
        interrupted.model_copy(
            update={
                "resolving_rebase": False,
                "resolving_paths": (),
            }
        ),
    )

    def resolve(repo: Path, _branch: str, _stop: RebaseStop) -> bool:
        assert read_record(root) is not None
        assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed
        (repo / "shared.txt").write_text("feature and target preserved\n", encoding="utf-8")
        return True

    outcome = recover_incomplete_integration(
        WorkspaceScope(root),
        config=config,
        rebase_stop_resolver=resolve,
        recovery_controller=controller,
    )
    assert outcome is not None and outcome.fast_forwarded
    assert read_record(root) is None
    assert _sha(root, target) == _sha(root)
    assert _blob(root, target, "shared.txt") == "feature and target preserved\n"


@pytest.mark.parametrize(
    ("operation", "reintegrate"),
    [("merge", False), ("rebase", True)],
)
def test_completed_owned_reintegration_lands_after_receipt_write_interruption(
    tmp_path: Path,
    operation: str,
    *,
    reintegrate: bool,
) -> None:
    template, target, completed = _completed_owned_template(operation, reintegrate=reintegrate)
    root = _clone_template(template, tmp_path / f"owned-{operation}")
    config = retained_resolution_config(target, enabled=False)

    outcome = recover_incomplete_integration(
        WorkspaceScope(root),
        config=config,
        recovery_controller=make_resolver_controller(FakeClock()),
    )

    assert outcome is not None and outcome.fast_forwarded
    assert read_record(root) is None
    assert inspect_integration_resolution(root, RebaseState()).dispatch_allowed
    assert _sha(root, target) == completed
    assert _blob(root, target, "feature.txt") == "feature\n"
    assert _blob(root, target, "target.txt") == "target\n"


@pytest.mark.parametrize(
    ("operation", "phase"),
    [("merge", "integrating"), ("rebase", "integrated")],
)
def test_completed_landing_receipt_preserves_later_operator_resolution(
    tmp_path: Path,
    operation: str,
    phase: str,
) -> None:
    template, target, index_before, head_before = _completed_landing_template(operation, phase)
    root = _clone_template(template, tmp_path / f"landing-{operation}-{phase}")
    config = retained_resolution_config(target, enabled=False)

    outcome = recover_incomplete_integration(
        WorkspaceScope(root),
        config=config,
        recovery_controller=make_resolver_controller(FakeClock()),
    )

    assert outcome is not None and not outcome.fast_forwarded
    assert read_record(root) is not None
    assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed
    assert (root / "shared.txt").read_text(encoding="utf-8") == "valuable manual resolution\n"
    assert _write_tree(root) == index_before
    assert _sha(root) == head_before


@pytest.mark.parametrize("operation", ["merge", "rebase"])
def test_aborted_operation_reset_to_target_cannot_be_misreported_as_completed(
    tmp_path: Path, operation: str
) -> None:
    root = tmp_path / f"case-{operation}"
    target, feature, main = interrupted_resolution_repo(root, operation)
    assert read_record(root) is not None
    assert _git(root, operation, "--abort").returncode == 0
    controller = make_resolver_controller(FakeClock())
    aborted = recover_incomplete_integration(
        WorkspaceScope(root),
        recovery_controller=controller,
    )
    assert aborted is not None and aborted.recovery_record_retained
    assert read_record(root) is not None
    assert _git(root, "reset", "--hard", main).returncode == 0
    outcome = integrate_before_planning(
        retained_resolution_config(target),
        WorkspaceScope(root),
        RebaseState(),
        sleep=lambda _s: None,
        jitter=lambda: 0.0,
        recovery_controller=controller,
    )
    assert outcome is not None and not outcome.fast_forwarded
    assert outcome.recovery_record_retained
    assert _sha(root, target) == main
    assert _git(root, "cat-file", "-e", feature).returncode == 0
