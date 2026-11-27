from __future__ import annotations

import re

from ralph.project_policy import markers
from ralph.project_policy._findings import PolicyFinding


def _normalized(content: str) -> str:
    return re.sub(r"\s+", " ", content.replace("*", "")).lower()


def check_protected_rules(content: str, path: str, filename: str) -> list[PolicyFinding]:
    protected = markers.PROTECTED_RULES.get(filename, ())
    if not protected or not content.lstrip().startswith(markers.POLICY_SCHEMA_MARKER):
        return []
    text = _normalized(content)
    missing = [clause for clause in protected if clause not in text]
    if not missing:
        return []
    return [
        PolicyFinding(
            requirement_id=f"{markers.ID_PROTECTED_RULE}:{filename}",
            path=path,
            missing_evidence=(
                f"drops or rewords {len(missing)} rule(s) the bundled {filename} starter "
                "ships as non-negotiable: " + "; ".join(f'"{clause}"' for clause in missing)
            ),
            required_outcome=(
                "restore each quoted rule word for word in the file's Default requirements "
                "section, replacing any softened version of it; these "
                "rules are never softened, narrowed, or made conditional for a project, and "
                "existing violations in the code are removed or tracked as work, never "
                "accommodated by changing the rule"
            ),
        )
    ]
