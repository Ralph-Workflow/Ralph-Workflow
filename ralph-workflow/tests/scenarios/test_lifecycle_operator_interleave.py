from __future__ import annotations

from pathlib import Path

import pytest

from tests._scenario_harness import (
    AgentMode,
    FakeAgentRunner,
    git,
    paused_text_and_gitlink_rebase,
    paused_text_rebase,
)
from tests.scenarios._content_equivalence import assert_content_equivalence, drive_integration

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]


@pytest.mark.parametrize("action", ["abort", "continue", "reset", "cherry-pick", "manual-land"])
def test_operator_action_between_ticks_is_observed(tmp_git_repo: Path, action: str) -> None:
    paused_text_rebase(tmp_git_repo)
    feature_sha = git(tmp_git_repo, "rev-parse", "feature")
    if action == "abort":
        git(tmp_git_repo, "rebase", "--abort")
    elif action == "continue":
        (tmp_git_repo / "shared.txt").write_text("operator resolution\n", encoding="utf-8")
        git(tmp_git_repo, "add", "shared.txt")
        git(tmp_git_repo, "-c", "core.editor=:", "rebase", "--continue")
    elif action == "reset":
        git(tmp_git_repo, "rebase", "--abort")
        git(tmp_git_repo, "reset", "--hard", "main")
    elif action == "cherry-pick":
        git(tmp_git_repo, "rebase", "--abort")
        git(tmp_git_repo, "checkout", "-q", "main")
        git(tmp_git_repo, "cherry-pick", feature_sha, check=False)
        git(tmp_git_repo, "checkout", "-q", "feature")
    else:
        git(tmp_git_repo, "rebase", "--abort")
        git(tmp_git_repo, "checkout", "-q", "main")
        git(tmp_git_repo, "merge", "-q", "--no-ff", "-m", "manual landing", "feature")
        git(tmp_git_repo, "checkout", "-q", "feature")

    run = drive_integration(
        tmp_git_repo,
        resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES),
    )

    assert run.error is None, f"the next tick must observe an operator {action}: {run.error}"
    if action != "reset":
        assert_content_equivalence(
            tmp_git_repo,
            contract=f"operator {action} work must reach the target",
            feature=feature_sha,
            source_refs=("main",),
        )


def test_ralph_site_resolved_unstaged_text_and_gitlink_state_lands(tmp_git_repo: Path) -> None:
    paused_text_and_gitlink_rebase(tmp_git_repo)
    feature_sha = git(tmp_git_repo, "rev-parse", "feature")
    (tmp_git_repo / "shared.txt").write_text("operator resolution\n", encoding="utf-8")

    run = drive_integration(tmp_git_repo, resolver=FakeAgentRunner(AgentMode.EDITS_NEVER_STAGES))

    assert run.error is None, f"the Ralph-Site retained state must be recoverable: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract="resolved-but-unstaged text plus gitlink conflict must land autonomously",
        feature=feature_sha,
        source_refs=("main",),
    )
