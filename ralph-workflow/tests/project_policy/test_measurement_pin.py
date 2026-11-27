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


_ROW_BEFORE = (
    "| Bounded run budget | Verification owner | the test share stays inside its cap "
    "(59.6% of the 540 second budget, 322.0s charged) | deterministic: make verify |\n"
)
_ROW_AFTER = (
    "| Bounded run budget | Verification owner | the test share exceeds its cap "
    "(70.4% of the 540 second budget, 380.3s charged, 431.6s wall) | deterministic: make verify |\n"
)
_REVIEW_BEFORE = "Dated gate-lane review: the canonical run exited 0 in 256.6s wall.\n"
_REVIEW_AFTER = "Dated gate-lane review: the canonical run exited 5 in 431.6s wall, a breach.\n"


def test_a_run_timing_re_recorded_in_a_table_row_or_prose_is_put_back(
    tmp_git_repo: Path,
) -> None:
    path = _commit_policy(
        tmp_git_repo, "# Verification\n" + _ROW_BEFORE + "Rule text.\n" + _REVIEW_BEFORE
    )
    path.write_text(
        "# Verification\n" + _ROW_AFTER + "Rule text, clarified.\n" + _REVIEW_AFTER,
        encoding="utf-8",
    )

    pinned = pin_measurement_facts(tmp_git_repo)

    assert len(pinned) == 2
    content = path.read_text(encoding="utf-8")
    assert _ROW_BEFORE in content
    assert _REVIEW_BEFORE in content
    assert "Rule text, clarified." in content


def test_a_new_line_or_an_edit_without_a_run_timing_is_left_alone(tmp_git_repo: Path) -> None:
    path = _commit_policy(
        tmp_git_repo, "# Verification\nThe gate covers 53 behaviours.\n" + _ROW_BEFORE
    )
    path.write_text(
        "# Verification\nThe gate covers 54 protected behaviours.\n"
        + _ROW_BEFORE
        + "A new rule measured 12s on 2026-11-26.\n",
        encoding="utf-8",
    )

    assert pin_measurement_facts(tmp_git_repo) == []
    assert "54 protected behaviours" in path.read_text(encoding="utf-8")


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
