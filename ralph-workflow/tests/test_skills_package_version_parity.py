"""Test that skills-package/package.json version matches ralph.__version__."""

from __future__ import annotations

import json
from pathlib import Path

import ralph


def test_skills_package_version_matches_ralph_version() -> None:
    package_json = Path(__file__).parent.parent / "skills-package" / "package.json"
    data = json.loads(package_json.read_text(encoding="utf-8"))
    assert data["version"] == ralph.__version__
