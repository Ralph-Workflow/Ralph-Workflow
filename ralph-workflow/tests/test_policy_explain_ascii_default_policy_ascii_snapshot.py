from __future__ import annotations

from pathlib import Path

from ralph.policy.explain import (
    explain_policy,
)
from ralph.policy.loader import load_policy
from ralph.policy.render import render_explanation_ascii


def _get_default_policy_path() -> Path:
    candidates = [
        Path(__file__).parent.parent / "ralph" / "policy" / "defaults",
        Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("Default policy directory not found in any candidate location")


class TestDefaultPolicyAsciiSnapshot:
    _FIXTURE = Path(__file__).parent / "fixtures" / "policy_explain_default.txt"

    def test_default_ascii_matches_fixture(self) -> None:
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        actual = render_explanation_ascii(explanation)
        expected = self._FIXTURE.read_text().rstrip("\n")
        assert actual == expected, (
            "Default policy ASCII diagram has changed. "
            "If the change is intentional, regenerate the fixture using the "
            "instructions in the class docstring."
        )
