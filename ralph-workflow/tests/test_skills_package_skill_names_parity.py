"""Test that skills-package/bin/skills.js SKILL_NAMES matches Python BASELINE_SKILL_NAMES."""
from __future__ import annotations

import re
from pathlib import Path

from ralph.skills._content import BASELINE_SKILL_NAMES


def test_skills_js_skill_names_match_baseline() -> None:
    skills_js = Path(__file__).parent.parent / "skills-package" / "bin" / "skills.js"
    text = skills_js.read_text(encoding="utf-8")
    match = re.search(r"const SKILL_NAMES = \[(.*?)\];", text, re.DOTALL)
    assert match, "Could not find SKILL_NAMES array in skills.js"
    js_skill_names = tuple(re.findall(r"'([a-z][a-z-]+)'", match.group(1)))
    assert js_skill_names == BASELINE_SKILL_NAMES
