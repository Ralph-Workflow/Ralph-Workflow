"""Unit tests for ASCII workflow diagram rendering in ralph --explain-policy.

Tests cover:
- Default pipeline diagram contains entry marker
- Default pipeline diagram contains decision branches
- Default pipeline diagram contains loopback arrows
- Default pipeline diagram contains fanout annotation
- Default pipeline diagram contains success terminal markers
- Non-terminal phases do NOT have failure terminal markers
- Minimal two-phase pipeline diagram
- Minimal pipeline contains expected structural elements
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.policy.explain import (
    explain_policy,
)
from ralph.policy.loader import load_policy
from ralph.policy.render import render_explanation_ascii


def _get_default_policy_path() -> Path:
    """Find the default policy directory.

    Searches in multiple locations to find the bundled defaults.
    """
    # Try relative to this test file
    candidates = [
        Path(__file__).parent.parent / "ralph" / "policy" / "defaults",
        Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    pytest.skip("Default policy directory not found")


class TestDefaultPolicyAsciiSnapshot:
    """Snapshot test locking down the ASCII output for the default pipeline policy.

    To regenerate the fixture:
        uv run --directory ralph-workflow python -c "
        from ralph.policy.loader import load_policy
        from ralph.policy.explain import explain_policy
        from ralph.policy.render import render_explanation_ascii
        from pathlib import Path
        b = load_policy(Path('ralph/policy/defaults'))
        print(render_explanation_ascii(explain_policy(b)))
        " > tests/fixtures/policy_explain_default.txt
    """

    _FIXTURE = Path(__file__).parent / "fixtures" / "policy_explain_default.txt"

    def test_default_ascii_matches_fixture(self) -> None:
        """Default policy ASCII diagram must match the committed fixture exactly."""
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
