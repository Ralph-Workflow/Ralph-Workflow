"""The committed skill-mirror freshness audit is gone.

The committed project mirror trees leave version control: the installer
materializes them locally on every run, so a committed-mirror freshness check
has nothing to compare against. Artifact-skill drift is now caught by the
registry-derived audit instead.

# RALPH-TEST-REASON: the committed skill mirror trees and the mirror-freshness audit are removed by this change; artifact-skill drift is covered by tests/test_audit_artifact_skill_types.py.
"""

from importlib.resources import files


def test_mirror_freshness_audit_is_not_shipped() -> None:
    """The removed audit module must not come back."""
    assert not files("ralph.testing").joinpath("audit_skill_mirror_freshness.py").is_file()


def test_artifact_skill_types_audit_is_shipped() -> None:
    """The replacement drift audit is shipped and runnable."""
    assert files("ralph.testing").joinpath("audit_artifact_skill_types.py").is_file()
