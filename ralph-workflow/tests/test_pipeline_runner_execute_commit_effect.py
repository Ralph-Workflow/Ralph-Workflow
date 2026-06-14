"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

import io
import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline import commit_executor as commit_executor_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import (
    CommitEffect,
    InvokeAgentEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

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


def _policy_bundle_with_loop_counter_max(counter_name: str, default_max: int) -> PolicyBundle:
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
    ctx = make_display_context(console=console, force_width=width, force_mode="wide")
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
    policy_bundle = _load_default_policy_bundle()
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
    policy_bundle = _policy_bundle_with_loop_counter_max("development_analysis_iteration", 5)
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


class TestExecuteCommitEffect:
    def test_returns_success_when_commit_succeeds(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        stage_all = MagicMock()
        create_commit = MagicMock(return_value="sha")
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text("fix: pipeline artifact message", encoding="utf-8")
        monkeypatch.setattr(runner_module, "repo_has_commit_work", lambda _repo_root: True)
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {"type": "commit", "subject": "fix: pipeline artifact message"},
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )

        result = runner_module.execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            create_commit,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_SUCCESS
        stage_all.assert_called_once_with(str(tmp_path))
        create_commit.assert_called_once_with(str(tmp_path), "fix: pipeline artifact message")
        assert not message_file.exists()
        assert not text_file.exists()

    def test_stages_only_files_declared_in_commit_artifact(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        stage_all = MagicMock()
        stage_files = MagicMock()
        create_commit = MagicMock(return_value="sha")
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text("fix: pipeline artifact message", encoding="utf-8")
        monkeypatch.setattr(runner_module, "repo_has_commit_work", lambda _repo_root: True)
        monkeypatch.setattr(commit_executor_module, "_stage_files", stage_files)
        monkeypatch.setattr(
            commit_executor_module,
            "_changed_commit_paths",
            lambda _repo_root: ["src/feature.py", "tests/test_feature.py"],
        )
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {
                        "type": "commit",
                        "subject": "fix: pipeline artifact message",
                        "files": ["src/feature.py", "tests/test_feature.py"],
                    },
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )

        result = runner_module.execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            create_commit,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_SUCCESS
        stage_all.assert_not_called()
        stage_files.assert_called_once_with(
            str(tmp_path),
            ["src/feature.py", "tests/test_feature.py"],
        )
        create_commit.assert_called_once_with(str(tmp_path), "fix: pipeline artifact message")

    def test_rejects_commit_artifact_files_with_parent_traversal(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        stage_all = MagicMock()
        stage_files = MagicMock()
        create_commit = MagicMock(return_value="sha")
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text("fix: pipeline artifact message", encoding="utf-8")
        monkeypatch.setattr(runner_module, "repo_has_commit_work", lambda _repo_root: True)
        monkeypatch.setattr(commit_executor_module, "_stage_files", stage_files)
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {
                        "type": "commit",
                        "subject": "fix: pipeline artifact message",
                        "files": ["src/feature.py", "../secrets.txt"],
                    },
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )

        result = runner_module.execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            create_commit,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_FAILURE
        stage_all.assert_not_called()
        stage_files.assert_not_called()
        create_commit.assert_not_called()

    def test_rejects_commit_artifact_files_not_in_changed_set(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        stage_all = MagicMock()
        stage_files = MagicMock()
        create_commit = MagicMock(return_value="sha")
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text("fix: pipeline artifact message", encoding="utf-8")
        monkeypatch.setattr(runner_module, "repo_has_commit_work", lambda _repo_root: True)
        monkeypatch.setattr(commit_executor_module, "_stage_files", stage_files)
        monkeypatch.setattr(
            commit_executor_module,
            "_changed_commit_paths",
            lambda _repo_root: ["src/feature.py"],
        )
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {
                        "type": "commit",
                        "subject": "fix: pipeline artifact message",
                        "files": ["src/feature.py", "docs/guide.md"],
                    },
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )

        result = runner_module.execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            create_commit,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_FAILURE
        stage_all.assert_not_called()
        stage_files.assert_not_called()
        create_commit.assert_not_called()

    def test_stages_changed_files_except_excluded_paths(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        stage_all = MagicMock()
        stage_files = MagicMock()
        create_commit = MagicMock(return_value="sha")
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text("fix: pipeline artifact message", encoding="utf-8")
        monkeypatch.setattr(runner_module, "repo_has_commit_work", lambda _repo_root: True)
        monkeypatch.setattr(commit_executor_module, "_stage_files", stage_files)
        monkeypatch.setattr(
            commit_executor_module,
            "_changed_commit_paths",
            lambda _repo_root: ["src/feature.py", "tests/test_feature.py", "docs/guide.md"],
        )
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {
                        "type": "commit",
                        "subject": "fix: pipeline artifact message",
                        "excluded_files": [{"path": "docs/guide.md", "reason": "internal_ignore"}],
                    },
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )

        result = runner_module.execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            create_commit,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_SUCCESS
        stage_all.assert_not_called()
        stage_files.assert_called_once_with(
            str(tmp_path),
            ["src/feature.py", "tests/test_feature.py"],
        )
        create_commit.assert_called_once_with(str(tmp_path), "fix: pipeline artifact message")

    @pytest.mark.parametrize(
        ("payload", "changed_paths", "expected"),
        [
            ({}, ["src/feature.py"], None),
            (
                {"files": ["src/feature.py", "tests/test_feature.py"]},
                ["src/feature.py", "tests/test_feature.py", "docs/guide.md"],
                ["src/feature.py", "tests/test_feature.py"],
            ),
            (
                {"excluded_files": [{"path": "docs/guide.md", "reason": "internal_ignore"}]},
                ["src/feature.py", "docs/guide.md"],
                ["src/feature.py"],
            ),
            (
                {"excluded_files": [{"path": "docs/guide.md", "reason": "internal_ignore"}]},
                ["src/feature.py", "src/feature.py", "docs/guide.md"],
                ["src/feature.py"],
            ),
        ],
    )
    def test_commit_include_paths_from_changed_matrix(
        self,
        payload: dict[str, object],
        changed_paths: list[str],
        expected: list[str] | None,
    ) -> None:
        actual = commit_executor_module._commit_include_paths_from_changed(payload, changed_paths)
        assert actual == expected

    @pytest.mark.parametrize(
        "payload",
        [
            {"files": ["../secrets.txt"]},
            {"excluded_files": [{"path": "../secrets.txt", "reason": "internal_ignore"}]},
            {"files": [""]},
        ],
    )
    def test_commit_include_paths_from_changed_rejects_invalid_paths(
        self, payload: dict[str, object]
    ) -> None:
        with pytest.raises(ValueError):
            commit_executor_module._commit_include_paths_from_changed(
                payload,
                ["src/feature.py", "docs/guide.md"],
            )

    def test_renders_commit_message_before_cleanup_when_display_is_available(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        stage_all = MagicMock()
        create_commit = MagicMock(return_value="sha")
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text("fix: pipeline artifact message", encoding="utf-8")
        monkeypatch.setattr(runner_module, "repo_has_commit_work", lambda _repo_root: True)
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {"type": "commit", "subject": "fix: pipeline artifact message"},
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )
        output = io.StringIO()
        display = ParallelDisplay(
            make_display_context(
                console=Console(file=output, force_terminal=False, color_system=None, width=120),
                env={},
            )
        )

        result = runner_module.execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            create_commit,
            stage_all,
            tmp_path,
            display=display,
        )

        assert result == PipelineEvent.COMMIT_SUCCESS
        assert "COMMIT MESSAGE" in output.getvalue()
        assert "fix: pipeline artifact message" in output.getvalue()
        assert not message_file.exists()
        assert not text_file.exists()

    def test_returns_failure_when_create_commit_raises(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        stage_all = MagicMock()
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text("fix: pipeline artifact message", encoding="utf-8")
        monkeypatch.setattr(runner_module, "repo_has_commit_work", lambda _repo_root: True)
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {"type": "commit", "subject": "fix: pipeline artifact message"},
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )

        def fail_create(*_: object) -> None:
            raise RuntimeError("boom")

        result = runner_module.execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            fail_create,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_FAILURE
        assert message_file.exists()
        assert text_file.exists()

    def test_returns_failure_when_message_file_missing(self, tmp_path: Path) -> None:
        stage_all = MagicMock()
        create_commit = MagicMock()

        result = runner_module.execute_commit_effect(
            CommitEffect(message_file=str(tmp_path / "missing.txt")),
            create_commit,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_FAILURE
        stage_all.assert_not_called()
        create_commit.assert_not_called()

    def test_returns_failure_when_commit_message_payload_is_invalid(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        stage_all = MagicMock()
        create_commit = MagicMock()
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        message_file.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(runner_module, "repo_has_commit_work", lambda _repo_root: True)

        result = runner_module.execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            create_commit,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_FAILURE
        stage_all.assert_not_called()
        create_commit.assert_not_called()

    def test_skips_commit_when_worktree_has_no_changes(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        stage_all = MagicMock()
        create_commit = MagicMock()
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text("fix: skip empty worktree", encoding="utf-8")
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {"type": "commit", "subject": "fix: skip empty worktree"},
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(runner_module, "repo_has_commit_work", lambda _repo_root: False)

        result = runner_module.execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            create_commit,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_SKIPPED
        stage_all.assert_not_called()
        create_commit.assert_not_called()
        assert not message_file.exists()
        assert not text_file.exists()

    def test_skips_commit_when_message_is_skip_artifact(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        """_execute_commit_effect must return COMMIT_SKIPPED when the message is a skip response.

        This is the late guard that prevents 'SKIP: reason' from being committed
        as a real git commit subject when the phase handler missed the skip.
        """
        stage_all = MagicMock()
        create_commit = MagicMock()
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text("SKIP: no pending changes visible in diff", encoding="utf-8")
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {
                        "type": "skip",
                        "reason": "no pending changes visible in diff",
                    },
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(runner_module, "repo_has_commit_work", lambda _repo_root: True)

        result = runner_module.execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            create_commit,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_SKIPPED
        stage_all.assert_not_called()
        create_commit.assert_not_called()
        assert not message_file.exists()
        assert not text_file.exists()
