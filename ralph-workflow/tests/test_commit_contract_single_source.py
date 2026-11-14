from __future__ import annotations

from ralph.mcp.artifacts.commit_message import COMMIT_MESSAGE_ARTIFACT, COMMIT_MESSAGE_TYPE
from ralph.phases.required_artifacts import resolve_required_artifact
from ralph.policy.loader import default_dir, load_policy

_COMMIT_DRAIN = "development_commit"

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "retired_guard_cleanup": "removed redundant test containing retired identifier literal that conflicted with test_repo_retired_identifier_guard",
}


def test_policy_and_writer_constant_agree_on_commit_path() -> None:
    bundle = load_policy(default_dir())
    ra = resolve_required_artifact(bundle.artifacts, drain=_COMMIT_DRAIN)
    assert ra is not None, f"no artifact contract for drain {_COMMIT_DRAIN!r}"
    assert ra.artifact_type == COMMIT_MESSAGE_TYPE
    assert ra.artifact_path == COMMIT_MESSAGE_ARTIFACT

