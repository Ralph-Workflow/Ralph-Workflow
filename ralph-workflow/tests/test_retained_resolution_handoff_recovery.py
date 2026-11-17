from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.auto_integrate_record import read_record
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.plumbing.commit_integration import prepare_commit_integration
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.scenarios._integration_scenario_fixtures import (
    interrupted_resolution_repo,
    retained_resolution_config,
)
from tests.test_pending_merge_commit_recovery import _commit, _git

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStop

pytestmark = [pytest.mark.timeout_seconds(10)]


def test_stale_resolver_receipt_does_not_adopt_replacement_operator_operation(
    tmp_path: Path,
) -> None:
    for operation in ["merge", "rebase"]:
        root = tmp_path / f"repo-{operation}"
        target, feature, _main = interrupted_resolution_repo(root, operation)
        assert _git(root, operation, "--abort").returncode == 0
        assert _git(root, "switch", "-c", "other", f"{feature}^").returncode == 0
        _commit(root, "shared.txt", "other target\n")
        assert _git(root, "switch", "feature").returncode == 0
        assert _git(root, operation, "other").returncode != 0
        (root / "shared.txt").write_text("operator partial resolution\n", encoding="utf-8")
        head_before = _git(root, "rev-parse", "HEAD").stdout

        def resolve(repo: Path, _branch: str) -> bool:
            (repo / "shared.txt").write_text("incorrectly adopted operation\n", encoding="utf-8")
            return True

        outcome = recover_incomplete_integration(
            WorkspaceScope(root),
            config=retained_resolution_config(target),
            conflict_resolver=resolve,
            rebase_stop_resolver=lambda repo, branch, _stop: resolve(repo, branch),
        )

        assert outcome is not None and outcome.recovery_record_retained
        assert not outcome.fast_forwarded
        assert read_record(root) is not None
        assert _git(root, "rev-parse", "HEAD").stdout == head_before
        assert (root / "shared.txt").read_text(encoding="utf-8") == "operator partial resolution\n"
        assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed


def test_aborted_owned_resolution_retains_landing_until_retry_completes(
    tmp_path: Path,
) -> None:
    operation = "rebase"
    root = tmp_path / f"repo-{operation}"
    target, feature, _moved = interrupted_resolution_repo(root, operation)
    assert _git(root, operation, "--abort").returncode == 0
    config = retained_resolution_config(target, enabled=False)

    waiting = recover_incomplete_integration(WorkspaceScope(root))

    assert waiting is not None and waiting.recovery_record_retained
    assert read_record(root) is not None
    assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == feature

    def resolve(repo: Path, _branch: str, _stop: RebaseStop) -> bool:
        (repo / "shared.txt").write_text("feature and main preserved\n", encoding="utf-8")
        return True

    landed = recover_incomplete_integration(
        WorkspaceScope(root),
        config=config,
        rebase_stop_resolver=resolve,
    )
    assert landed is not None and landed.fast_forwarded
    assert read_record(root) is None
    assert _git(root, "rev-parse", target).stdout == _git(root, "rev-parse", "HEAD").stdout


@pytest.mark.parametrize(
    ("operation", "seam"),
    [
        ("merge", "planning"),
        ("rebase", "after_commit"),
        ("rebase", "standalone_commit"),
    ],
)
def test_retained_resolution_hands_off_and_lands_at_public_integration_seam(
    tmp_path: Path,
    operation: str,
    seam: str,
) -> None:
    root = tmp_path / f"repo-{operation}-{seam}"
    target, _, _ = interrupted_resolution_repo(root, operation)
    calls: list[str] = []

    def merge_resolver(repo: Path, branch: str) -> bool:
        calls.append(branch)
        (repo / "shared.txt").write_text("resolved feature and main\n", encoding="utf-8")
        return True

    def rebase_resolver(repo: Path, branch: str, _stop: RebaseStop) -> bool:
        return merge_resolver(repo, branch)

    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": target,
                "auto_integrate_remote_enabled": False,
                "auto_integrate_enabled": seam != "standalone_commit",
            }
        }
    )
    outcome: RebaseState | None
    if seam == "standalone_commit":
        context = make_display_context()
        verdict = prepare_commit_integration(
            root,
            config,
            make_test_pipeline_deps(context),
            AgentRegistry.from_config(config),
            context,
            conflict_resolver=merge_resolver,
            rebase_stop_resolver=rebase_resolver,
        )
        assert verdict.dispatch_allowed
        outcome = RebaseState(fast_forwarded=True)
    elif seam == "planning":
        outcome = integrate_before_planning(
            config,
            WorkspaceScope(root),
            RebaseState(),
            conflict_resolver=merge_resolver,
            rebase_stop_resolver=rebase_resolver,
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    else:
        outcome = auto_integrate_after_commit(
            config,
            WorkspaceScope(root),
            RebaseState(),
            conflict_resolver=merge_resolver,
            rebase_stop_resolver=rebase_resolver,
            sleep=lambda _s: None,
            jitter=lambda: 0.0,
        )
    assert calls == [target]
    assert outcome is not None and outcome.fast_forwarded
    assert _git(root, "rev-parse", target).stdout == _git(root, "rev-parse", "HEAD").stdout
    assert _git(root, "show", f"{target}:shared.txt").stdout == "resolved feature and main\n"
    assert read_record(root) is None
