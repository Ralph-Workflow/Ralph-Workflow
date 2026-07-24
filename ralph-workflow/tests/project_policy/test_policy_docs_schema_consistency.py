"""Keep policy-document schema headers and marker guidance synchronized."""

from __future__ import annotations

import re
from pathlib import Path

from ralph.project_policy.markers import POLICY_SCHEMA_MARKER as CURRENT_POLICY_SCHEMA_MARKER

POLICY_DIR = Path(__file__).resolve().parents[3] / "docs" / "ralph-workflow-policy"
POLICY_SCHEMA_MARKER = re.compile(r"<!-- ralph-policy-schema: (v\d+) -->")


def test_policy_schema_header_matches_ralph_markers_description() -> None:
    """Every policy must describe the same schema marker it declares."""
    mismatches: list[str] = []

    for path in sorted(POLICY_DIR.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        header_match = POLICY_SCHEMA_MARKER.match(content)
        marker_section = content.partition("## Ralph markers")[2]
        described_match = POLICY_SCHEMA_MARKER.search(marker_section)

        if header_match is None:
            mismatches.append(f"{path.name}: missing schema header")
        elif header_match.group(0) != CURRENT_POLICY_SCHEMA_MARKER:
            mismatches.append(
                f"{path.name}: stale schema {header_match.group(0)}; "
                f"expected {CURRENT_POLICY_SCHEMA_MARKER}"
            )
        elif described_match is None:
            mismatches.append(f"{path.name}: missing schema marker description")
        elif header_match.group(1) != described_match.group(1):
            mismatches.append(
                f"{path.name}: header {header_match.group(1)} != "
                f"description {described_match.group(1)}"
            )

    assert not mismatches, "\n".join(mismatches)
