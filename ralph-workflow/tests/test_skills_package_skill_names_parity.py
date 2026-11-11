"""The hand-kept skill lists are gone; the shipped set is derived.

The shipped artifact-skill set is derived from the canonical artifact-type
registry, and the upstream per-skill bookkeeping that the parity test compared
is removed with the skills package.

# RALPH-TEST-REASON: the separate skills package and its hand-kept skill list are removed by this change; the shipped skill set is derived from the artifact-type registry and checked by tests/test_audit_artifact_skill_types.py.
"""

from importlib.resources import files


def test_shipped_skill_manifest_is_not_a_mirror_manifest() -> None:
    """The manifest carries names only, with no per-skill upstream bookkeeping."""
    import json

    document = json.loads(files("ralph.skills").joinpath("content/metadata.json").read_text())
    assert set(document) == {"skills"}
    assert isinstance(document["skills"], list)
    assert all(isinstance(name, str) for name in document["skills"])
