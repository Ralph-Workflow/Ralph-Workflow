"""Tests for artifact submit/rollback symmetry via submit_ops_for_artifact."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.handoffs import HANDOFF_PATHS
from ralph.mcp.artifacts.history import history_dir_for_artifact, history_index_path
from ralph.mcp.artifacts.plan import PLAN_ARTIFACT_TYPE
from ralph.mcp.tools.artifact import (
    ArtifactHandlerDeps,
    handle_submit_artifact,
    submit_ops_for_artifact,
)

if TYPE_CHECKING:
    from pathlib import Path

_COMMIT_MESSAGE_OP_COUNT = 3
_PLAN_OP_COUNT = 3
_GENERIC_OP_COUNT = 2
_EXPECTED_IMMEDIATE_HISTORY_SNAPSHOTS = 2

# Minimal valid content for each artifact type that passes validation
_VALID_CONTENT: dict[str, str] = {
    "plan": json.dumps(
        {
            "summary": {
                "context": "Test plan submission",
                "scope_items": [
                    {"text": "Implement feature"},
                    {"text": "Write tests"},
                    {"text": "Verify"},
                ],
            },
            "steps": [{"number": 1, "title": "Step 1", "content": "Do the work"}],
            "critical_files": {
                "primary_files": [{"path": "ralph/pipeline/runner.py", "action": "modify"}]
            },
            "risks_mitigations": [{"risk": "Regression", "mitigation": "Tests"}],
            "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
        }
    ),
    "development_result": json.dumps(
        {"status": "completed", "summary": "Feature implemented", "files_changed": "- src/a.py"}
    ),
    "issues": json.dumps(
        {
            "status": "no_issues",
            "summary": "No issues found",
            "issues": [],
            "what_came_up_short": [],
            "how_to_fix": [],
        }
    ),
    "fix_result": json.dumps({"summary": "Fixed the regression", "files_changed": "- src/b.py"}),
    "commit_message": json.dumps({"type": "commit", "subject": "feat: add per-type tests"}),
    "development_analysis_decision": json.dumps(
        {"status": "completed", "summary": "Analysis complete"}
    ),
    "review_analysis_decision": json.dumps({"status": "completed", "summary": "Review approved"}),
}

# Invalid content (wrong type / missing required field) for each artifact type
_INVALID_CONTENT: dict[str, str] = {
    "plan": json.dumps({"summary": "missing steps field"}),
    "development_result": json.dumps({"status": "bad_status"}),
    "issues": json.dumps({"status": "bad_status"}),
    "fix_result": json.dumps({}),  # missing required summary
    "commit_message": json.dumps({"subject": "no-conventional-prefix"}),
    "development_analysis_decision": json.dumps({"status": "unknown_status"}),
    "review_analysis_decision": json.dumps({"status": "unknown_status"}),
}

_ALL_ARTIFACT_TYPES = list(_VALID_CONTENT.keys())


class TestHistoryIntegrationInSubmitOps:
    """Tests for artifact history archival integrated into submit_ops_for_artifact."""

    class _Session:
        session_id = "sess-1"

        def check_capability(self, cap: str) -> object:
            assert cap == "artifact.submit"
            return "approved"

    class _DrainSession:
        session_id = "sess-1"

        def __init__(self, drain: str) -> None:
            self.drain = drain

        def check_capability(self, cap: str) -> object:
            assert cap == "artifact.submit"
            return "approved"

    class _Workspace:
        def __init__(self, root: Path) -> None:
            self._root = root

        def absolute_path(self, path: str) -> str:
            return str((self._root / path).resolve())

    def _now_iso(self) -> str:
        return "2026-05-06T12:00:00+00:00"

    def test_first_plan_submit_creates_current_and_history_snapshot(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        workspace = _Workspace(tmp_path)
        session = _DrainSession("planning")

        handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "plan",
                "content": _VALID_CONTENT["plan"],
            },
        )

        current_json = artifact_dir / "plan.json"
        current_md = tmp_path / HANDOFF_PATHS["plan"]
        hist_dir = history_dir_for_artifact(artifact_dir, "plan")
        index_file = history_index_path(artifact_dir, "plan")
        json_archives = list(hist_dir.glob("*.json"))
        md_archives = list(hist_dir.glob("*.md"))

        assert current_json.exists()
        assert current_md.exists()
        assert index_file.exists()
        assert len(json_archives) >= 1, "first submit must create a history JSON snapshot"
        assert any(path.name != "index.md" for path in md_archives), (
            "first submit must create a history Markdown snapshot"
        )

    def test_first_analysis_submit_creates_current_and_history_snapshot(
        self,
        tmp_path: Path,
    ) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        workspace = _Workspace(tmp_path)
        session = _DrainSession("planning_analysis")

        handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "planning_analysis_decision",
                "content": _VALID_CONTENT["development_analysis_decision"],
            },
        )

        current_json = artifact_dir / "planning_analysis_decision.json"
        current_md = tmp_path / HANDOFF_PATHS["planning_analysis_decision"]
        hist_dir = history_dir_for_artifact(artifact_dir, "planning_analysis_decision")
        index_file = history_index_path(artifact_dir, "planning_analysis_decision")
        json_archives = list(hist_dir.glob("*.json"))
        md_archives = list(hist_dir.glob("*.md"))

        assert current_json.exists()
        assert current_md.exists()
        assert index_file.exists()
        assert len(json_archives) >= 1, "first analysis submit must create a history JSON snapshot"
        assert any(path.name != "index.md" for path in md_archives), (
            "first analysis submit must create a history Markdown snapshot"
        )

    def test_history_snapshot_uses_unique_timestamped_names_when_same_second_repeats(
        self, tmp_path: Path
    ) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        workspace = _Workspace(tmp_path)
        session = _DrainSession("planning_analysis")
        deps = ArtifactHandlerDeps(now_iso=self._now_iso, history_enabled=True)

        handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "planning_analysis_decision",
                "content": _VALID_CONTENT["development_analysis_decision"],
            },
            deps=deps,
        )
        handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "planning_analysis_decision",
                "content": _VALID_CONTENT["development_analysis_decision"],
            },
            deps=deps,
        )

        hist_dir = history_dir_for_artifact(artifact_dir, "planning_analysis_decision")
        json_archives = sorted(hist_dir.glob("*.json"))
        md_archives = sorted(path for path in hist_dir.glob("*.md") if path.name != "index.md")
        assert len(json_archives) == _EXPECTED_IMMEDIATE_HISTORY_SNAPSHOTS
        assert len(md_archives) == _EXPECTED_IMMEDIATE_HISTORY_SNAPSHOTS
        assert json_archives[0].name != json_archives[1].name
        assert md_archives[0].name != md_archives[1].name

    def test_history_disabled_does_not_prepend_extra_op(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        deps = ArtifactHandlerDeps(history_enabled=False)

        ops = submit_ops_for_artifact(
            "development_analysis_decision",
            tmp_path,
            artifact_dir,
            {"status": "completed", "summary": "done"},
            deps=deps,
        )

        assert len(ops) == _GENERIC_OP_COUNT

    def test_history_enabled_appends_snapshot_op_to_plan(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        deps = ArtifactHandlerDeps(history_enabled=True)
        content: dict[str, object] = {
            "summary": {"context": "ctx", "scope_items": [{"text": "item"}]},
            "steps": [{"number": 1, "title": "t", "content": "c"}],
            "critical_files": {"primary_files": [], "reference_files": []},
            "risks_mitigations": [],
            "verification_strategy": [{"method": "m", "expected_outcome": "o"}],
        }

        ops = submit_ops_for_artifact(
            PLAN_ARTIFACT_TYPE,
            tmp_path,
            artifact_dir,
            content,
            deps=deps,
        )

        # history snapshot op appended: main JSON + markdown + history + delete_draft
        assert len(ops) == _PLAN_OP_COUNT + 1

    def test_history_op_archives_existing_artifact(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "plan.json").write_text('{"type":"plan","old":true}', encoding="utf-8")

        deps = ArtifactHandlerDeps(now_iso=self._now_iso, history_enabled=True)
        content = json.loads(_VALID_CONTENT["plan"])

        ops = submit_ops_for_artifact(
            PLAN_ARTIFACT_TYPE,
            tmp_path,
            artifact_dir,
            content,
            deps=deps,
        )

        # Run artifact submit + handoff + history snapshot ops.
        for op in ops[:3]:
            op.run()

        hist_dir = history_dir_for_artifact(artifact_dir, PLAN_ARTIFACT_TYPE)
        json_archives = list(hist_dir.glob("*.json"))
        assert len(json_archives) == 1
        assert "Test plan submission" in json_archives[0].read_text(encoding="utf-8")

    def test_history_undo_removes_archived_files(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "plan.json").write_text('{"type":"plan"}', encoding="utf-8")

        deps = ArtifactHandlerDeps(now_iso=self._now_iso, history_enabled=True)
        content = json.loads(_VALID_CONTENT["plan"])

        ops = submit_ops_for_artifact(
            PLAN_ARTIFACT_TYPE,
            tmp_path,
            artifact_dir,
            content,
            deps=deps,
        )

        # Run artifact submit + handoff + history snapshot, then undo snapshot only.
        for op in ops[:3]:
            op.run()
        ops[2].undo()

        hist_dir = history_dir_for_artifact(artifact_dir, PLAN_ARTIFACT_TYPE)
        json_archives = list(hist_dir.glob("*.json"))
        assert json_archives == [], "history snapshot files must be removed after undo"


_Session = TestHistoryIntegrationInSubmitOps._Session
_DrainSession = TestHistoryIntegrationInSubmitOps._DrainSession
_Workspace = TestHistoryIntegrationInSubmitOps._Workspace
