from __future__ import annotations

from pathlib import Path

import pytest

from tests._scenario_harness import (
    AgentMode,
    FakeAgentRunner,
    git,
    paused_text_rebase,
    supervisor_restart,
)
from tests.scenarios._content_equivalence import assert_content_equivalence, drive_integration

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]


@pytest.mark.parametrize("interruption", ["process-crash", "machine-restart", "interrupted-run"])
def test_restart_resumes_from_git_facts_without_redo_or_loss(
    tmp_git_repo: Path,
    interruption: str,
) -> None:
    paused_text_rebase(tmp_git_repo)
    feature_sha = git(tmp_git_repo, "rev-parse", "feature")
    before = git(tmp_git_repo, "rev-parse", "REBASE_HEAD")
    if interruption == "machine-restart":
        restarted = supervisor_restart(tmp_git_repo)
        assert restarted["head"], "a supervisor restart must preserve an inspectable HEAD"

    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    first = drive_integration(tmp_git_repo, resolver=runner)
    second = drive_integration(tmp_git_repo, resolver=runner)

    assert first.error is None and second.error is None, (
        f"the next tick must resume from git facts after {interruption}"
    )
    assert (
        git(tmp_git_repo, "rev-list", "--count", "main")
        == git(tmp_git_repo, "rev-list", "--count", "--all", "--ancestry-path", f"{before}..main")
        or runner.launches <= 1
    ), "resuming must not replay a completed stop twice"
    assert_content_equivalence(
        tmp_git_repo,
        contract=f"{interruption} recovery must land without losing work",
        feature=feature_sha,
        source_refs=("main",),
    )
