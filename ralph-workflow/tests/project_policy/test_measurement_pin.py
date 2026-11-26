from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Repo as GitRepo

from ralph.project_policy import pin_measurement_facts

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(8)]

_POLICY = "docs/ralph-workflow-policy/verification-policy.md"


def _commit_policy(root: Path, text: str) -> Path:
    path = root / _POLICY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    with GitRepo(root) as repo:
        repo.index.add([_POLICY])
        repo.index.commit("policy")
    return path


def test_a_development_change_to_a_measured_figure_is_put_back_and_the_rest_is_kept(
    tmp_git_repo: Path,
) -> None:
    path = _commit_policy(
        tmp_git_repo,
        "# Verification\nRALPH-FACT: verification_budget_overage: 0s (238.857s charged)\n"
        "Rule text.\n",
    )
    path.write_text(
        "# Verification\nRALPH-FACT: verification_budget_overage: 95.738s (365.738s charged)\n"
        "Rule text, clarified.\n",
        encoding="utf-8",
    )

    pinned = pin_measurement_facts(tmp_git_repo)

    assert pinned == [f"{_POLICY}: verification_budget_overage"]
    content = path.read_text(encoding="utf-8")
    assert "RALPH-FACT: verification_budget_overage: 0s (238.857s charged)" in content
    assert "Rule text, clarified." in content


def test_a_newly_recorded_measurement_and_other_facts_are_left_alone(tmp_git_repo: Path) -> None:
    path = _commit_policy(tmp_git_repo, "# Verification\nRALPH-FACT: gate_order: lint\n")
    path.write_text(
        "# Verification\nRALPH-FACT: gate_order: lint, tests\n"
        "RALPH-FACT: verification_cost_floor: 12s measured 2026-11-26\n",
        encoding="utf-8",
    )

    assert pin_measurement_facts(tmp_git_repo) == []
    assert "verification_cost_floor: 12s" in path.read_text(encoding="utf-8")
    assert "gate_order: lint, tests" in path.read_text(encoding="utf-8")


def test_a_repository_without_policy_changes_is_untouched(tmp_git_repo: Path) -> None:
    assert pin_measurement_facts(tmp_git_repo) == []


def test_commit_review_seals_the_committed_measurement_not_the_refreshed_one(
    tmp_git_repo: Path,
) -> None:
    from ralph.phases.commit_review import run_commit_review_stage1_preflight
    from ralph.policy.loader import default_dir, load_policy

    path = _commit_policy(
        tmp_git_repo, "RALPH-FACT: verification_budget_overage: 0s (238.857s charged)\n"
    )
    path.write_text(
        "RALPH-FACT: verification_budget_overage: 95.738s (365.738s charged)\n",
        encoding="utf-8",
    )
    (tmp_git_repo / "feature.py").write_text("x = 1\n", encoding="utf-8")
    bundle = load_policy(default_dir())

    run_commit_review_stage1_preflight(
        tmp_git_repo,
        "run",
        "1",
        bundle.pipeline,
        "development_commit_review",
        artifacts_policy=bundle.artifacts,
    )

    assert "0s (238.857s charged)" in path.read_text(encoding="utf-8")
    assert (tmp_git_repo / "feature.py").exists()
