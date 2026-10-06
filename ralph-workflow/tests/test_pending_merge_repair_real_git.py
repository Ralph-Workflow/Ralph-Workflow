from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.pipeline._pending_merge_repair import repair_pending_merge
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.events import PipelineEvent
from ralph.policy.models import PolicyBundle
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.test_pending_merge_commit_recovery import _git, _prepared

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.git.git_run_result import GitRunResult
    from ralph.git.subprocess_runner import GitRunOptions

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def _install_validation_hook(root: Path, *, custom: bool) -> Path:
    hooks = root / "custom-hooks" if custom else root / ".git" / "hooks"
    if custom:
        hooks.mkdir()
        assert _git(root, "config", "core.hooksPath", str(hooks)).returncode == 0
    hook = hooks / "pre-commit"
    hook.write_text(
        "#!/bin/sh\nif [ \"$(git show :shared.txt)\" != fixed ]; then\n"
        "echo 'shared.txt: expected fixed' >&2\nexit 1\nfi\n", encoding="utf-8",
    )
    hook.chmod(0o755)
    return hook


def _assert_recovered_landing(root: Path, target: str, case: str) -> None:
    landed = recover_incomplete_integration(WorkspaceScope(root))
    assert landed is not None and landed.fast_forwarded
    assert _git(root, "rev-parse", target).stdout == _git(root, "rev-parse", "HEAD").stdout
    assert _git(root, "show", f"{target}:shared.txt").stdout == "fixed\n"
    if case == "new_file":
        assert _git(root, "show", f"{target}:required.txt").stdout == "new source\n"


@pytest.mark.parametrize("case", ["fixed", "existing_wip", "before_stage", "after_stage", "new_file", "weak_hook", "staged_intruder"])
def test_hook_source_fix_is_reverified_and_landed_without_sweeping_wip(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    import ralph.pipeline._pending_repair_edits as repair_edits

    root = tmp_git_repo
    target, original = _prepared(root)
    hook = _install_validation_hook(root, custom=case == "weak_hook")
    outcome = recover_incomplete_integration(WorkspaceScope(root))
    assert outcome is not None and outcome.recovery_record_retained
    if case == "existing_wip":
        (root / "shared.txt").write_text("user unfinished work\n", encoding="utf-8")

    def execute(*_args: object, **_kwargs: object) -> PipelineEvent:
        if case == "weak_hook":
            hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        if case == "new_file":
            (root / "required.txt").write_text("new source\n", encoding="utf-8")
        (root / "shared.txt").write_text("fixed\n", encoding="utf-8")
        (root / ".agent" / "tmp" / "pending_commit_repair_paths.json").write_text(
            '["required.txt", "shared.txt"]' if case == "new_file" else '["shared.txt"]', encoding="utf-8",
        )
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr("ralph.pipeline.effect_executor.execute_agent_effect", execute)
    original_write = repair_edits.write_record
    original_git = repair_edits.run_git

    def fail_publication(
        workspace_root: Path, record: IntegrationRecord, *, backend: FileBackend = DEFAULT_FILE_BACKEND,
    ) -> None:
        if record.repair_pending_diff is None:
            raise OSError("record publication interrupted after staging")
        original_write(workspace_root, record, backend=backend)

    def fail_staging(
        args: Sequence[str], *, cwd: Path | None, label: str, options: GitRunOptions | None = None,
    ) -> GitRunResult:
        if label == "repair:stage-scoped":
            raise OSError("process interrupted before staging")
        return original_git(args, cwd=cwd, label=label, options=options)

    with monkeypatch.context() as crash:
        if case == "after_stage":
            crash.setattr(repair_edits, "write_record", fail_publication)
        if case in {"before_stage", "staged_intruder"}:
            crash.setattr(repair_edits, "run_git", fail_staging)
        repaired = repair_pending_merge(
            workspace_scope=WorkspaceScope(root), config=UnifiedConfig(),
            pipeline_deps=make_test_pipeline_deps(make_display_context(console=Console(file=StringIO()))),
            policy_bundle=PolicyBundle.model_construct(),
            display=None, display_context=None, agents=("repair",),
            failure=outcome.last_reason or "",
        )
    if case in {"existing_wip", "weak_hook"}:
        assert not repaired
        assert _git(root, "write-tree").stdout.strip() == original.merge_commit_tree
        assert _git(root, "rev-parse", target).stdout.strip() == original.merge_commit_parent
        if case == "weak_hook":
            blocked = recover_incomplete_integration(WorkspaceScope(root))
            assert blocked is not None and blocked.recovery_record_retained
            assert "restore checks" in (blocked.last_reason or "")
        return
    prepared = read_record(root)
    assert prepared is not None
    if case in {"before_stage", "after_stage", "staged_intruder"}:
        assert not repaired
        assert prepared.repair_pending_diff
    else:
        assert repaired
        assert prepared.merge_commit_tree != original.merge_commit_tree
    assert prepared.repair_original_tree == original.merge_commit_tree
    if case == "staged_intruder":
        (root / "shared.txt").write_text("other actor staged work\n", encoding="utf-8")
        assert _git(root, "add", "shared.txt").returncode == 0
        other_tree = _git(root, "write-tree").stdout
        (root / "shared.txt").write_text("fixed\n", encoding="utf-8")
        blocked = recover_incomplete_integration(WorkspaceScope(root))
        assert blocked is not None and blocked.recovery_record_retained
        assert _git(root, "write-tree").stdout == other_tree
        assert _git(root, "rev-parse", target).stdout.strip() == original.merge_commit_parent
        return
    _assert_recovered_landing(root, target, case)
    assert hook.exists()
