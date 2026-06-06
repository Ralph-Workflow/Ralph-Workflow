"""Test that packaged skill metadata matches Python BASELINE_SKILL_NAMES."""

from __future__ import annotations

import json
from pathlib import Path

from ralph.skills._content import BASELINE_SKILL_NAMES


def test_skills_package_metadata_skill_names_match_baseline() -> None:
    metadata_path = Path(__file__).parent.parent / "ralph" / "skills" / "content" / "metadata.json"
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert tuple(data["skills"]) == BASELINE_SKILL_NAMES
