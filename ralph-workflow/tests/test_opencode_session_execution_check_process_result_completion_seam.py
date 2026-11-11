from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest
from loguru import logger as completion_logger

from ralph.agents.execution_state import (
    OpenCodeExecutionStrategy,
)
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentInvocationError,
    CompletionCheckOptions,
    OpenCodeResumableExitError,
    PiContextExhaustedExitError,
    check_process_result,
)
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.process.child_liveness import ChildLivenessRegistry
from tests.fake_handle import _FakeHandle

if TYPE_CHECKING:
    from pathlib import Path


_DESCENDANT_WAIT_POLL_SECONDS = 0.5


class TestCheckProcessResultCompletionSeam:
    def test_explicit_completion_with_sentinel_does_not_raise(self, tmp_path: Path) -> None:
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)
        raw_output = ["Task declared complete: session_id=abc, summary=done, timestamp=1"]
        sentinel = tmp_path / ".agent" / "completion_seen_abc.json"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text('{"run_id": "abc"}', encoding="utf-8")

        check_process_result(
            handle,
            "opencode",
            raw_output,
            CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                captured_session_id="abc",
            ),
        )

    def test_retryable_nonzero_exit_does_not_log_terminal_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=1)
        handle.stderr = io.StringIO("Model returned an empty response with no tool calls")
        seen_errors: list[tuple[object, ...]] = []
        seen_warnings: list[tuple[object, ...]] = []
        monkeypatch.setattr(
            completion_logger,
            "error",
            lambda *args, **kwargs: seen_errors.append(args),
        )
        monkeypatch.setattr(
            completion_logger,
            "warning",
            lambda *args, **kwargs: seen_warnings.append(args),
        )

        with pytest.raises(AgentInvocationError):
            check_process_result(
                handle,
                "opencode",
                ['{"type":"tool_result","tool":"read_file"}'],
                CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                ),
            )

        assert seen_errors == []
        assert seen_warnings != []

    def test_terminal_native_task_regression_still_requires_parent_completion(
        self, tmp_path: Path
    ) -> None:
        registry = ChildLivenessRegistry(
            progress_ttl=30.0,
            heartbeat_ttl=30.0,
            stale_label_ttl=30.0,
            exit_reconcile=30.0,
        )
        strategy = OpenCodeExecutionStrategy(label_scope="parent", registry=registry)
        strategy.observe_line(
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "type": "tool",
                        "tool": "task",
                        "callID": "native-task",
                        "state": {"status": "completed", "output": "done"},
                    },
                }
            )
        )

        with pytest.raises(OpenCodeResumableExitError):
            check_process_result(
                _FakeHandle(returncode=0),
                "opencode",
                [],
                CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
                ),
            )

    def test_no_artifact_requirement_still_requires_explicit_completion(
        self, tmp_path: Path
    ) -> None:
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            check_process_result(
                handle,
                "opencode",
                [],
                CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
                ),
            )

    def test_required_receipt_needs_completion_sentinel(self, tmp_path: Path) -> None:
        run_id = "seam-opencode-on-disk-run-id"
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "plan.md").write_text(
            "---\n"
            "type: plan\n"
            "status: completed\n"
            "---\n\n"
            "## Summary\n\n- [SUM-1] done\n\n"
            "## Files Changed\n\n- [F-1] src/x.py\n",
            encoding="utf-8",
        )
        receipt_dir = tmp_path / ".agent" / "receipts" / run_id
        receipt_dir.mkdir(parents=True)
        (receipt_dir / "plan.json").write_text(
            f'{{"run_id": "{run_id}", "artifact_type": "plan"}}',
            encoding="utf-8",
        )

        registry = ChildLivenessRegistry(
            progress_ttl=30.0,
            heartbeat_ttl=30.0,
            stale_label_ttl=30.0,
            exit_reconcile=30.0,
        )
        strategy = OpenCodeExecutionStrategy(label_scope="parent", registry=registry)
        strategy.observe_line(
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "type": "tool",
                        "tool": "task",
                        "callID": "completed-native-task",
                        "state": {"status": "completed", "output": "done"},
                    },
                }
            )
        )
        handle = _FakeHandle(returncode=0)

        options = CompletionCheckOptions(
            execution_strategy=strategy,
            workspace_path=tmp_path,
            completion_run_id=run_id,
            required_artifact=RequiredArtifact(
                phase="planning",
                artifact_type="plan",
                artifact_path=".agent/artifacts/plan.md",
                markdown_path=None,
                normalizer=None,
            ),
            policy=TimeoutPolicy(
                idle_timeout_seconds=None,
                parent_exit_grace_seconds=0.0,
            ),
        )

        with pytest.raises(OpenCodeResumableExitError):
            check_process_result(
                handle,
                "opencode",
                [],
                options,
            )

        sentinel = tmp_path / ".agent" / f"completion_seen_{run_id}.json"
        sentinel.write_text(f'{{"run_id": "{run_id}"}}', encoding="utf-8")
        check_process_result(
            handle,
            "opencode",
            [],
            options,
        )

    def test_neither_signal_nor_artifact_raises_resumable_exit(self, tmp_path: Path) -> None:
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            check_process_result(
                handle,
                "opencode",
                [],
                CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    required_artifact=RequiredArtifact(
                        phase="planning",
                        artifact_type="plan",
                        artifact_path=".agent/artifacts/plan.md",
                        markdown_path=None,
                        normalizer=None,
                    ),
                    policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
                ),
            )

    def test_pi_length_stop_without_artifact_raises_context_exhausted(self, tmp_path: Path) -> None:
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)
        raw_output = [
            (
                '{"type":"message_update","message":{"role":"assistant"},'
                '"assistantMessageEvent":{"type":"done","stopReason":"length"}}'
            )
        ]

        with pytest.raises(PiContextExhaustedExitError) as excinfo:
            check_process_result(
                handle,
                "pi/zai/glm-5.2",
                raw_output,
                CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    required_artifact=RequiredArtifact(
                        phase="development",
                        artifact_type="development_result",
                        artifact_path=".agent/artifacts/development_result.md",
                        markdown_path=None,
                        normalizer=None,
                    ),
                    policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
                ),
            )

        assert excinfo.value.skip_same_agent_retries is True

    def test_malformed_json_artifact_raises_resumable_exit(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "development_result.json").write_text("not-valid-json")

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            check_process_result(
                handle,
                "opencode",
                [],
                CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    required_artifact=RequiredArtifact(
                        phase="planning",
                        artifact_type="plan",
                        artifact_path=".agent/artifacts/plan.md",
                        markdown_path=None,
                        normalizer=None,
                    ),
                    policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
                ),
            )

    def test_empty_json_object_artifact_raises_resumable_exit(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "development_result.json").write_text("{}")

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            check_process_result(
                handle,
                "opencode",
                [],
                CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    required_artifact=RequiredArtifact(
                        phase="planning",
                        artifact_type="plan",
                        artifact_path=".agent/artifacts/plan.md",
                        markdown_path=None,
                        normalizer=None,
                    ),
                    policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
                ),
            )

    def test_optional_artifact_absent_without_sentinel_raises(self, tmp_path: Path) -> None:
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            check_process_result(
                handle,
                "opencode",
                [],
                CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    required_artifact=RequiredArtifact(
                        phase="planning",
                        artifact_type="plan",
                        artifact_path=".agent/artifacts/plan.md",
                        markdown_path=None,
                        normalizer=None,
                        artifact_required=False,
                    ),
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=0.0,
                    ),
                ),
            )

    def test_optional_artifact_malformed_without_sentinel_raises_at_completion_check(
        self, tmp_path: Path
    ) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "development_result.json").write_text("not-valid-json")

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            check_process_result(
                handle,
                "opencode",
                [],
                CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    required_artifact=RequiredArtifact(
                        phase="planning",
                        artifact_type="plan",
                        artifact_path=".agent/artifacts/plan.md",
                        markdown_path=None,
                        normalizer=None,
                        artifact_required=False,
                    ),
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=0.0,
                    ),
                ),
            )
