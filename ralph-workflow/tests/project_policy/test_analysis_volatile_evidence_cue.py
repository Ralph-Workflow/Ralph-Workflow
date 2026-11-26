from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Repo as GitRepo

from ralph.pipeline.cycle_baseline import write_cycle_baseline
from ralph.policy.loader import load_policy
from ralph.project_policy import refreshed_measurement_facts, volatile_citations_in_added_lines
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(8)]

_DIFF = """diff --git a/script/verify/profiles.ts b/script/verify/profiles.ts
--- a/script/verify/profiles.ts
+++ b/script/verify/profiles.ts
@@ -1 +1 @@
-// blocker cites tmp/verify-stages.ndjson:22969
+// blocker cites tmp/verify-stages.ndjson:23262
diff --git a/src/app.ts b/src/app.ts
--- a/src/app.ts
+++ b/src/app.ts
@@ -0,0 +1 @@
+export const app = 1; // see src/util.ts:12
"""


def test_only_added_lines_are_scanned_and_attributed_to_their_file() -> None:
    assert volatile_citations_in_added_lines(_DIFF) == [
        "script/verify/profiles.ts: tmp/verify-stages.ndjson:23262"
    ]


def _render_analysis(repo_root: Path) -> str:
    policy = load_policy(repo_root / ".agent")
    workspace = MemoryWorkspace(root=str(repo_root))
    workspace.write("PROMPT.md", "implement the feature")
    workspace.write(".agent/PLAN.md", "# Execution Plan\n\n1. Do it.\n")
    prompt_path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development_analysis",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=repo_root,
        ),
        PromptPhaseOptions(artifacts_policy=policy.artifacts),
    )
    return workspace.read(prompt_path)


def test_analysis_prompt_lists_volatile_citations_committed_this_cycle(
    tmp_git_repo: Path,
) -> None:
    with GitRepo(tmp_git_repo) as repo:
        write_cycle_baseline(tmp_git_repo, repo.head.commit.hexsha)
        target = tmp_git_repo / "script" / "verify" / "profiles.ts"
        target.parent.mkdir(parents=True)
        target.write_text("// blocker cites tmp/verify-stages.ndjson:23262\n")
        repo.index.add(["script/verify/profiles.ts"])
        repo.index.commit("re-cite the ledger")

    rendered = _render_analysis(tmp_git_repo)

    assert "script/verify/profiles.ts: tmp/verify-stages.ndjson:23262" in rendered


def test_analysis_prompt_omits_the_cue_when_the_cycle_adds_no_volatile_citation(
    tmp_git_repo: Path,
) -> None:
    with GitRepo(tmp_git_repo) as repo:
        write_cycle_baseline(tmp_git_repo, repo.head.commit.hexsha)
        (tmp_git_repo / "app.py").write_text("app = 1\n")
        repo.index.add(["app.py"])
        repo.index.commit("real work")

    rendered = _render_analysis(tmp_git_repo)

    assert "Volatile evidence added this cycle" not in rendered


_REMEASURE_DIFF = """diff --git a/docs/ralph-workflow-policy/verification-policy.md b/docs/ralph-workflow-policy/verification-policy.md
--- a/docs/ralph-workflow-policy/verification-policy.md
+++ b/docs/ralph-workflow-policy/verification-policy.md
@@ -10 +10 @@
-RALPH-FACT: verification_budget_overage: excess: 0s (184.3s charged vs the 270.0s allowance)
+RALPH-FACT: verification_budget_overage: excess: 0s (238.857s charged vs the 270.0s allowance)
@@ -20 +20 @@
-RALPH-FACT: gate_order: lint, types, tests
+RALPH-FACT: gate_order: lint, types, tests, e2e
"""


def test_a_refreshed_budget_measurement_is_reported_but_other_facts_are_not() -> None:
    assert refreshed_measurement_facts(_REMEASURE_DIFF) == [
        "docs/ralph-workflow-policy/verification-policy.md: verification_budget_overage"
    ]


def test_a_newly_recorded_measurement_is_not_a_refresh() -> None:
    diff = (
        "diff --git a/p.md b/p.md\n--- a/p.md\n+++ b/p.md\n@@ -0,0 +1 @@\n"
        "+RALPH-FACT: verification_cost_floor: 12s measured 2026-11-26\n"
    )

    assert refreshed_measurement_facts(diff) == []


def test_analysis_prompt_lists_a_budget_figure_the_cycle_re_recorded(tmp_git_repo: Path) -> None:
    target = tmp_git_repo / "docs" / "ralph-workflow-policy" / "verification-policy.md"
    target.parent.mkdir(parents=True)
    target.write_text("RALPH-FACT: verification_budget_overage: 184.3s charged\n")
    with GitRepo(tmp_git_repo) as repo:
        repo.index.add([str(target.relative_to(tmp_git_repo))])
        repo.index.commit("record budget")
        write_cycle_baseline(tmp_git_repo, repo.head.commit.hexsha)
        target.write_text("RALPH-FACT: verification_budget_overage: 238.857s charged\n")
        repo.index.add([str(target.relative_to(tmp_git_repo))])
        repo.index.commit("reconcile budget evidence")

    rendered = _render_analysis(tmp_git_repo)

    assert "verification-policy.md: verification_budget_overage" in rendered
    assert "Budget measurements re-recorded this cycle" in rendered


def test_the_developer_is_told_before_editing_not_to_re_record_one_runs_timing() -> None:
    from ralph.prompts.template_registry import load_partial_templates, packaged_template_root

    partials = load_partial_templates([packaged_template_root()])
    source = next(
        text for key, text in partials.items() if key.endswith("_verification_commitments")
    )

    assert "re-record one run's timing" in source
    assert "replace the check's oracle" in source
