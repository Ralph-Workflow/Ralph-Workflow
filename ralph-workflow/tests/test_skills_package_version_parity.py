"""The separate skills package is gone, so it has no version to track.

The shipped skill set is derived from the pipeline's artifact-type registry and
the upstream-sourced universal skills arrive through the runtime updater, so no
npm package with its own version is published any more.

# RALPH-TEST-REASON: the separate skills package is removed by this change; there is no packaged artifact whose version must track the package version.
"""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_skills_package_is_not_shipped() -> None:
    """The retired npm skills package must not come back."""
    assert not (PACKAGE_ROOT / "skills-package").exists()
