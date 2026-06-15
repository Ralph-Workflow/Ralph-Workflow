"""Guard: the commit-message artifact contract has ONE source-of-truth path.

The commit-message path is referenced from three surfaces: the policy
``artifacts.toml`` (what the gate resolves), the writer constant
``COMMIT_MESSAGE_ARTIFACT`` (where the bytes land), and the standalone
``--generate-commit`` resolver ``_commit_required_artifact()``. The resolver
consumes ``COMMIT_MESSAGE_ARTIFACT`` directly, so pinning the policy path to that
same constant transitively pins all three. If the policy and the constant drift,
an artifact could be written where the gate never looks — this test fails the
build the moment they disagree, so the drift can never be merged.
"""

from __future__ import annotations

from ralph.mcp.artifacts.commit_message import COMMIT_MESSAGE_ARTIFACT, COMMIT_MESSAGE_TYPE
from ralph.phases.required_artifacts import resolve_required_artifact
from ralph.policy.loader import default_dir, load_policy

_COMMIT_DRAIN = "development_commit"


def test_policy_and_writer_constant_agree_on_commit_path() -> None:
    bundle = load_policy(default_dir())
    ra = resolve_required_artifact(bundle.artifacts, drain=_COMMIT_DRAIN)
    assert ra is not None, f"no artifact contract for drain {_COMMIT_DRAIN!r}"
    assert ra.artifact_type == COMMIT_MESSAGE_TYPE
    assert ra.json_path == COMMIT_MESSAGE_ARTIFACT
