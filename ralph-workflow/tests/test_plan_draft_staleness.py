"""Plan draft cleanup preserves unsubmitted work and removes stale provenance."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from ralph.agents.completion_signals import completion_signals_terminal, evaluate_completion
from ralph.mcp.artifacts.md_draft_io import (
    md_draft_workspace_path,
    save_md_draft,
    seeded_draft_workspace_path,
)
from ralph.mcp.artifacts.plan import PLAN_ARTIFACT_PATH
from ralph.mcp.artifacts.state_db import RunStateDB
from ralph.phases import PhaseContext, handle_phase, register_role_handlers
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.pipeline.effects import PreparePromptEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.phase_entry_cleaner import clear_phase_entry_drains
from ralph.policy.loader import load_policy
from ralph.workspace.fs import FsWorkspace


def _policy() -> object:
    return load_policy(Path("ralph/policy/defaults"))


def _context(root: Path, policy: object) -> PhaseContext:
    return cast(
        "PhaseContext",
        SimpleNamespace(
            workspace=FsWorkspace(root),
            pipeline_policy=policy.pipeline,
            artifacts_policy=policy.artifacts,
        ),
    )


def test_plan_draft_staleness_regression_keeps_different_content_despite_older_mtime(
    tmp_path: Path,
) -> None:
    """S-3: timestamp rewriting cannot discard authored plan repairs."""
    policy = _policy()
    workspace = FsWorkspace(tmp_path)
    draft_path = md_draft_workspace_path("plan")
    workspace.write(draft_path, "authored repair")
    workspace.write(PLAN_ARTIFACT_PATH, "submitted plan")

    register_role_handlers(policy.pipeline)
    events = handle_phase(
        PreparePromptEffect(phase="planning", iteration=1), _context(tmp_path, policy)
    )

    assert events == [PipelineEvent.PROMPT_PREPARED]
    assert workspace.read(draft_path) == "authored repair"


def test_plan_draft_staleness_regression_clears_submitted_content(tmp_path: Path) -> None:
    """S-3: only a draft identical to canonical content is cleared."""
    policy = _policy()
    workspace = FsWorkspace(tmp_path)
    draft_path = md_draft_workspace_path("plan")
    workspace.write(draft_path, "submitted plan")
    workspace.write(PLAN_ARTIFACT_PATH, "submitted plan")

    register_role_handlers(policy.pipeline)
    handle_phase(PreparePromptEffect(phase="planning", iteration=1), _context(tmp_path, policy))

    assert not workspace.exists(draft_path)


def test_plan_draft_staleness_regression_keeps_seeded_canonical_draft(tmp_path: Path) -> None:
    """S-4: loopback-seeded drafts remain available for in-place repair."""
    policy = _policy()
    workspace = FsWorkspace(tmp_path)
    workspace.write(md_draft_workspace_path("plan"), "submitted plan")
    workspace.write(seeded_draft_workspace_path("plan"), "")
    workspace.write(PLAN_ARTIFACT_PATH, "submitted plan")

    register_role_handlers(policy.pipeline)
    handle_phase(PreparePromptEffect(phase="planning", iteration=1), _context(tmp_path, policy))

    assert workspace.exists(md_draft_workspace_path("plan"))


def test_plan_draft_staleness_regression_fallback_promotion_checks_retained_draft(
    tmp_path: Path,
) -> None:
    """S-1: promoted fallback cannot hide a longer staged draft."""
    artifact_dir = tmp_path / ".agent" / "artifacts"
    save_md_draft(
        artifact_dir,
        "plan",
        "complete staged plan with all authored details and verification evidence " * 8,
    )
    fallback = tmp_path / ".agent" / "tmp" / "plan.md"
    fallback.parent.mkdir(parents=True)
    fallback.write_text(
        """---
type: plan
---
## Outcome
Submit a shorter but valid fallback plan so the completion gate must compare it
with the retained staged draft before allowing the planning phase to finish.

### [S-1] Promote fallback
Persist this fallback through the canonical submission path.
Type: discovery
Location: .agent/artifacts/plan.md
""",
        encoding="utf-8",
    )
    required = RequiredArtifact(
        phase="planning",
        artifact_type="plan",
        artifact_path=PLAN_ARTIFACT_PATH,
        markdown_path=".agent/PLAN.md",
        normalizer=None,
    )

    state = RunStateDB(tmp_path)
    state.upsert_completion_sentinel("plan-run", "sentinel")
    state.close()

    assert not completion_signals_terminal(
        evaluate_completion(tmp_path, required_artifact=required, run_id="plan-run")
    )
    assert (artifact_dir / "plan.md").exists()


def test_plan_draft_staleness_regression_fresh_entry_removes_seed_marker(tmp_path: Path) -> None:
    """S-3: a fresh phase entry cannot leave stale seeded provenance behind."""
    policy = _policy()
    workspace = FsWorkspace(tmp_path)
    workspace.write(md_draft_workspace_path("plan"), "seeded plan")
    workspace.write(seeded_draft_workspace_path("plan"), "")

    clear_phase_entry_drains(workspace, "planning", None, policy.pipeline, policy.artifacts)

    assert not workspace.exists(md_draft_workspace_path("plan"))
    assert not workspace.exists(seeded_draft_workspace_path("plan"))
