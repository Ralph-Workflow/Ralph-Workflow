from __future__ import annotations

import re

from ralph.project_policy import markers
from ralph.project_policy._findings import PolicyFinding


def _normalized(content: str) -> str:
    return re.sub(r"\s+", " ", content.replace("*", "")).lower()


def check_retired_schema_wording(content: str, path: str, filename: str) -> list[PolicyFinding]:
    first_line = next((line for line in content.splitlines() if line.strip()), "")
    if first_line != markers.POLICY_SCHEMA_MARKER:
        return []
    version = markers.SCHEMA_VERSION
    retired = markers.RETIRED_SCHEMA_WORDING.get(version, {}).get(filename, ())
    text = _normalized(content)
    present = [phrase for phrase in retired if phrase in text]
    if not present:
        return []
    return [
        PolicyFinding(
            requirement_id=f"{markers.ID_SCHEMA_CONTENT}:{filename}",
            path=path,
            missing_evidence=(
                f"carries the {version} schema marker but keeps {len(present)} rule(s) "
                f"{version} retired: " + "; ".join(f'"{phrase}"' for phrase in present)
            ),
            required_outcome=(
                f"finish the {version} upgrade: rewrite each retired rule to the bundled "
                f"{filename} starter's {version} rule ({markers.RETIRED_SCHEMA_RULE[version]}), "
                "keeping the project's own figures and facts; a marker change alone is not an "
                "upgrade"
            ),
        )
    ]
