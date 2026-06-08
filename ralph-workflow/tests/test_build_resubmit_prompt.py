"""Tests for the SINGLE artifact-missing retry-hint builder, `build_retry_hint`.

Both the pipeline phase gates and the commit command call `build_retry_hint`, so
the "agent did not submit the required artifact; here is your prior analysis;
submit it now via <tool>" recovery cannot drift. These tests pin the shared
resubmit guidance (artifact type, submit tool, json fallback path, prior-analysis
echo) produced for any caller.
"""

from __future__ import annotations

from ralph.phases.required_artifacts import RequiredArtifact, build_retry_hint


def _ra(phase: str, artifact_type: str, json_path: str) -> RequiredArtifact:
    return RequiredArtifact(
        phase=phase,
        artifact_type=artifact_type,
        json_path=json_path,
        markdown_path=None,
        normalizer=None,
    )


def test_hint_names_artifact_type_path_and_submit_tool() -> None:
    out = build_retry_hint(
        "commit",
        "agent completed without writing a commit_message artifact",
        registry={"commit": _ra("commit", "commit_message", ".agent/tmp/commit_message.json")},
        submit_tool_name="ralph_submit_artifact",
    )
    assert "commit_message" in out
    assert "ralph_submit_artifact" in out
    assert ".agent/tmp/commit_message.json" in out


def test_hint_echoes_prior_analysis() -> None:
    out = build_retry_hint(
        "planning",
        "missing",
        registry={"planning": _ra("planning", "plan", ".agent/artifacts/plan.json")},
        prior_output=["I analyzed the repo", "the plan is X"],
        submit_tool_name="ralph_submit_artifact",
    )
    assert "the plan is X" in out
    assert "submit it" in out.lower()


def test_hint_includes_example_payload_when_given() -> None:
    out = build_retry_hint(
        "commit",
        "missing",
        registry={"commit": _ra("commit", "commit_message", ".agent/tmp/commit_message.json")},
        submit_tool_name="ralph_submit_artifact",
        example_payload='{"artifact_type":"commit_message","content":"{}"}',
    )
    assert '"artifact_type":"commit_message"' in out


def test_hint_without_registry_still_instructs_submit() -> None:
    out = build_retry_hint("commit", "missing", submit_tool_name="ralph_submit_artifact")
    assert "submit" in out.lower()
    assert "ralph_submit_artifact" in out
