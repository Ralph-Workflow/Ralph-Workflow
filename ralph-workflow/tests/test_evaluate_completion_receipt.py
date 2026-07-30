"""The completion gate must accept a submission receipt as proof of presence.

This is the regression for the "Artifact submitted: X" + "no artifact"
contradiction: even when the gate's configured ``artifact_path`` points nowhere near
where the artifact actually landed, a receipt for the required artifact_type in
the current run makes the gate see the artifact. Path layout can never again
desync completion detection from a successful submission.
"""

from __future__ import annotations

import json
from pathlib import Path

from ralph.agents.completion_signals import evaluate_completion
from ralph.mcp.artifacts.completion_receipts import write_artifact_receipt
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.phases.required_artifacts import RequiredArtifact


def _required(artifact_path: str) -> RequiredArtifact:
    return RequiredArtifact(
        phase="development_commit",
        artifact_type="commit_message",
        artifact_path=artifact_path,
        markdown_path=None,
        normalizer=None,
        artifact_required=True,
    )


def test_receipt_satisfies_required_artifact_when_path_missing(tmp_path: Path) -> None:
    # The gate's configured path is deliberately wrong / nonexistent.
    ra = _required(".agent/tmp/nowhere/commit_message.md")
    write_artifact_receipt(tmp_path, "run-1", "commit_message", backend=DEFAULT_FILE_BACKEND)

    signals = evaluate_completion(tmp_path, [], required_artifact=ra, run_id="run-1")

    assert signals.required_artifact_present is True


def test_no_receipt_and_no_file_means_absent(tmp_path: Path) -> None:
    ra = _required(".agent/tmp/nowhere/commit_message.md")

    signals = evaluate_completion(tmp_path, [], required_artifact=ra, run_id="run-1")

    assert signals.required_artifact_present is False


def test_receipt_for_other_run_does_not_satisfy(tmp_path: Path) -> None:
    ra = _required(".agent/tmp/nowhere/commit_message.md")
    write_artifact_receipt(tmp_path, "other-run", "commit_message", backend=DEFAULT_FILE_BACKEND)

    signals = evaluate_completion(tmp_path, [], required_artifact=ra, run_id="run-1")

    assert signals.required_artifact_present is False


def test_evaluate_receipt_from_legacy_file(tmp_path: Path) -> None:
    """A legacy ``.agent/receipts/<run>/<type>.json`` still satisfies evaluation.

    RFC-013 P3 dual-read: production writes receipts to
    ``.agent/state.db`` only, but the completion gate must still
    honor receipts left behind by the previous release during the
    dual-read rollout window. Without this fallback an in-flight run
    that was upgraded mid-run would fail its completion gate despite
    having a valid pre-upgrade receipt file on disk.
    """

    ra = _required(".agent/tmp/nowhere/commit_message.md")
    legacy_receipt = tmp_path / ".agent" / "receipts" / "run-1" / "commit_message.json"
    legacy_receipt.parent.mkdir(parents=True)
    legacy_receipt.write_text(json.dumps({"run_id": "run-1", "artifact_type": "commit_message"}))

    signals = evaluate_completion(tmp_path, [], required_artifact=ra, run_id="run-1")

    assert signals.required_artifact_present is True
