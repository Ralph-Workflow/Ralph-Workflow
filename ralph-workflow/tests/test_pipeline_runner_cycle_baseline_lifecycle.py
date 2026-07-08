"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from git import Repo as GitRepo
from rich.console import Console

from ralph.config.enums import (
    Verbosity,
)
from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.cycle_baseline import write_cycle_baseline
from ralph.pipeline.effects import (
    CommitEffect,
    ExitSuccessEffect,
    InvokeAgentEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

# All tests in this module exercise real git operations against the
# ``tmp_git_repo`` fixture (per-test process-isolated git repository).
# Wall-clock cost under parallel xdist load is regularly > 1 s on busy
# machines, so the default 1-second per-test ceiling is unsafe.
pytestmark = pytest.mark.timeout_seconds(5)

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.policy.models import (
        PolicyBundle,
    )


DEVELOPER_ITERATIONS = 5
REVIEWER_PASSES = 2
SECOND_ITERATION = 2
INTERRUPT_EXIT_CODE = 130
_TRUNCATED_TEXT_MAX = runner_module.MAX_TEXT_LENGTH + 1  # content + ellipsis
_TRUNCATED_RESULT_BRIEF_MAX = runner_module.MAX_TOOL_RESULT_BRIEF + 1  # content + ellipsis
_TRUNCATED_METADATA_MAX = runner_module.MAX_METADATA_SUMMARY_LENGTH + 1  # content + ellipsis
_AVAILABLE_WIDTH_FLOOR = 40
_TRUNCATE_RESULT_LEN = 6  # 5 chars + 1 ellipsis char


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _policy_bundle_with_loop_counter(counter_name: str, default_max: int) -> PolicyBundle:
    bundle = _load_default_policy_bundle()
    loop_counters = dict(bundle.pipeline.loop_counters)
    loop_counters[counter_name] = loop_counters[counter_name].model_copy(
        update={"default_max": default_max}
    )
    return bundle.model_copy(
        update={"pipeline": bundle.pipeline.model_copy(update={"loop_counters": loop_counters})}
    )


def _registry_factory(return_value: object) -> object:
    class Registry:
        @classmethod
        def from_config(cls, config: object) -> object:
            instance = MagicMock()
            instance.get.return_value = return_value
            return instance

    return Registry


def _install_runner_display_context(
    monkeypatch: MonkeyPatch,
    *,
    width: int = 120,
) -> Console:
    console = Console(record=True, force_terminal=False, width=width, color_system=None)
    ctx = make_display_context(
        console=console,
        force_width=width,
    )
    monkeypatch.setattr(runner_module, "make_display_context", lambda **_kwargs: ctx)
    return console


def _config_with_agents(
    *,
    agent_chains: dict[str, list[str]],
    agent_drains: dict[str, str],
) -> object:
    config = MagicMock()
    config.agent_chains = agent_chains
    config.agent_drains = agent_drains
    return config


def _write_minimal_plan_artifacts(
    root: Path,
    *,
    context: str = "Existing plan",
) -> None:
    (root / ".agent" / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / ".agent" / "artifacts" / "plan.json").write_text(
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": context,
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "skills_mcp": {
                        "skills": [
                            "test-driven-development",
                            "verification-before-completion",
                        ],
                        "mcps": [],
                    },
                    "steps": [{"number": 1, "title": "Revise", "content": "keep context"}],
                    "critical_files": {
                        "primary_files": [{"path": "src/plan.py", "action": "modify"}],
                        "reference_files": [],
                    },
                    "risks_mitigations": [{"risk": "drift", "mitigation": "preserve"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                    "work_units": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (root / ".agent" / "PLAN.md").write_text(
        f"# Execution Plan\n\n{context}.\n",
        encoding="utf-8",
    )


def _write_minimal_plan_draft(root: Path, *, context: str = "Existing draft") -> None:
    artifact_dir = root / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / ".plan_draft.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "started_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:01+00:00",
                "sections": {
                    "summary": {
                        "context": context,
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _stub_workspace_scope_and_policy(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(
        runner_module, "load_policy_or_die", lambda _path: _load_default_policy_bundle()
    )


def test_resolve_display_defaults_to_legacy_console_display() -> None:
    display = runner_module.resolve_display(None, make_display_context())

    assert isinstance(display, runner_module.ParallelDisplay)


def test_materialize_agent_prompt_if_needed_rewrites_existing_prompt_on_fresh_planning_entry(
    tmp_path: Path,
) -> None:
    policy_bundle = _policy_bundle_with_loop_counter("development_analysis_iteration", 5)
    workspace = FsWorkspace(tmp_path)
    workspace.write("PROMPT.md", "Create a fresh plan")
    workspace.write(
        ".agent/tmp/planning_prompt.md",
        "You are in PLANNING EDIT MODE. Revise the existing execution plan.",
    )
    effect = InvokeAgentEffect(
        agent_name="claude",
        phase="planning",
        prompt_file="PROMPT.md",
        drain="planning",
        chain_name="planning",
    )
    state = PipelineState(phase="planning", previous_phase=None)
    registry = MagicMock()
    registry.get.return_value = None

    runner_module.materialize_agent_prompt_if_needed(
        effect,
        state,
        workspace,
        policy_bundle,
        registry,
    )

    rendered = workspace.read(".agent/tmp/planning_prompt.md")
    assert "You are in PLANNING MODE" in rendered
    assert "PLANNING EDIT MODE" not in rendered


def test_materialize_agent_prompt_if_needed_rewrites_stale_planning_prompt_on_analysis_loopback(
    tmp_path: Path,
) -> None:
    policy_bundle = _load_default_policy_bundle()
    workspace = FsWorkspace(tmp_path)
    workspace.write("PROMPT.md", "Revise the plan")
    workspace.write(
        ".agent/artifacts/plan.json",
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "Existing plan",
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "skills_mcp": {
                        "skills": [
                            "test-driven-development",
                            "verification-before-completion",
                        ],
                        "mcps": [],
                    },
                    "steps": [{"number": 1, "title": "Revise", "content": "keep context"}],
                    "critical_files": {
                        "primary_files": [{"path": "src/plan.py", "action": "modify"}],
                        "reference_files": [],
                    },
                    "risks_mitigations": [{"risk": "drift", "mitigation": "revise"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                    "work_units": [],
                },
            }
        ),
    )
    workspace.write(
        ".agent/artifacts/planning_analysis_decision.json",
        json.dumps(
            {
                "type": "planning_analysis_decision",
                "content": {
                    "status": "request_changes",
                    "summary": "Need revisions",
                    "what_came_up_short": ["issue"],
                    "how_to_fix": ["fix it"],
                },
            }
        ),
    )
    workspace.write(
        ".agent/tmp/planning_prompt.md",
        "You are in PLANNING MODE. Create a detailed, structured execution plan.",
    )
    effect = InvokeAgentEffect(
        agent_name="claude",
        phase="planning",
        prompt_file="PROMPT.md",
        drain="planning",
        chain_name="planning",
    )
    state = PipelineState(phase="planning", previous_phase="planning_analysis")
    registry = MagicMock()
    registry.get.return_value = None

    runner_module.materialize_agent_prompt_if_needed(
        effect,
        state,
        workspace,
        policy_bundle,
        registry,
    )

    rendered = workspace.read(".agent/tmp/planning_prompt.md")
    assert "PLANNING EDIT MODE" in rendered
    assert "You are in PLANNING MODE" not in rendered


@pytest.mark.parametrize("analysis_iteration", [2, 3, 4])
def test_materialize_agent_prompt_if_needed_rewrites_stale_development_prompt_on_analysis_loopback(
    tmp_path: Path,
    analysis_iteration: int,
) -> None:
    policy_bundle = _load_default_policy_bundle()
    workspace = FsWorkspace(tmp_path)
    workspace.write(
        "PROMPT.md",
        f"Continue development after analysis iteration {analysis_iteration}",
    )
    workspace.write(
        ".agent/PLAN.md",
        "# Execution Plan\n\n1. Continue implementing the feature\n",
    )
    workspace.write(
        ".agent/tmp/development_prompt.md",
        "You are in IMPLEMENTATION MODE. Execute the plan and make progress.",
    )
    effect = InvokeAgentEffect(
        agent_name="claude",
        phase="development",
        prompt_file="PROMPT.md",
        drain="development",
        chain_name="development",
    )
    state = PipelineState(
        phase="development",
        previous_phase="development_analysis",
        loop_iterations={"development_analysis_iteration": analysis_iteration - 1},
    )
    registry = MagicMock()
    registry.get.return_value = None

    runner_module.materialize_agent_prompt_if_needed(
        effect,
        state,
        workspace,
        policy_bundle,
        registry,
    )

    rendered = workspace.read(".agent/tmp/development_prompt.md")
    assert "continuing a DEVELOPMENT iteration" in rendered
    assert "You are in IMPLEMENTATION MODE" not in rendered


class TestCycleBaselineLifecycle:
    """Regression tests: cycle baseline is cleared at dev-cycle boundaries."""

    def test_run_clears_baseline_at_teardown_on_success(
        self, monkeypatch: MonkeyPatch, tmp_git_repo: Path
    ) -> None:

        with GitRepo(tmp_git_repo) as _r:
            _head_sha = _r.head.commit.hexsha
        write_cycle_baseline(tmp_git_repo, _head_sha)
        assert (tmp_git_repo / ".agent" / "start_commit").exists()

        monkeypatch.setattr(
            runner_module,
            "resolve_workspace_scope",
            lambda: WorkspaceScope(tmp_git_repo),
        )
        monkeypatch.setattr(
            runner_module,
            "determine_effect_from_policy",
            lambda _state, _bundle, _scope: ExitSuccessEffect(),
        )
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
        _install_runner_display_context(monkeypatch)

        state = MagicMock()
        state.phase = "planning"

        runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert not (tmp_git_repo / ".agent" / "start_commit").exists(), (
            "run() must clear .agent/start_commit at pipeline teardown"
        )

    def test_run_clears_baseline_at_teardown_on_failure(
        self, monkeypatch: MonkeyPatch, tmp_git_repo: Path
    ) -> None:

        with GitRepo(tmp_git_repo) as _r:
            _head_sha = _r.head.commit.hexsha
        write_cycle_baseline(tmp_git_repo, _head_sha)
        baseline_path = tmp_git_repo / ".agent" / "start_commit"
        assert baseline_path.exists()

        cleared: list[bool] = []

        def _spy_clear(workspace_root: object) -> None:
            cleared.append(True)
            baseline_path.unlink(missing_ok=True)

        monkeypatch.setattr(
            runner_module,
            "resolve_workspace_scope",
            lambda: WorkspaceScope(tmp_git_repo),
        )
        monkeypatch.setattr(
            runner_module,
            "determine_effect_from_policy",
            lambda _state, _bundle, _scope: ExitSuccessEffect(),
        )
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
        _install_runner_display_context(monkeypatch)
        monkeypatch.setattr(runner_module, "clear_cycle_baseline", _spy_clear)

        state = MagicMock()
        state.phase = "planning"

        runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert cleared, "run() must call clear_cycle_baseline in its finally/teardown block"

    def test_run_pipeline_step_clears_baseline_after_development_commit_success(
        self, monkeypatch: MonkeyPatch, tmp_git_repo: Path
    ) -> None:

        with GitRepo(tmp_git_repo) as _r:
            _head_sha = _r.head.commit.hexsha
        write_cycle_baseline(tmp_git_repo, _head_sha)
        baseline_path = tmp_git_repo / ".agent" / "start_commit"
        assert baseline_path.exists()

        cleared: list[bool] = []

        def _spy_clear(workspace_root: object) -> None:
            cleared.append(True)
            baseline_path.unlink(missing_ok=True)

        commit_effect = CommitEffect(message_file="/dev/null")
        call_count = {"n": 0}

        def _fake_determine_effect(_state: object, _bundle: object, _scope: object) -> object:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return commit_effect
            return ExitSuccessEffect()

        state = MagicMock()
        state.phase = "development_commit"
        state.copy_with = MagicMock(return_value=state)

        monkeypatch.setattr(runner_module, "determine_effect_from_policy", _fake_determine_effect)
        monkeypatch.setattr(
            runner_module,
            "resolve_workspace_scope",
            lambda: WorkspaceScope(tmp_git_repo),
        )
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
        _install_runner_display_context(monkeypatch)
        monkeypatch.setattr(
            runner_module,
            "execute_commit_effect",
            lambda *_args, **_kwargs: PipelineEvent.COMMIT_SUCCESS,
        )
        monkeypatch.setattr(
            runner_module,
            "materialize_agent_prompt_if_needed",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(runner_module, "clear_cycle_baseline", _spy_clear)
        monkeypatch.setattr(
            runner_module,
            "reducer_reduce",
            lambda _state, _event, _policy, recovery=None: (state, []),
        )

        runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert cleared, (
            "clear_cycle_baseline must be called after development_commit COMMIT_SUCCESS"
        )
