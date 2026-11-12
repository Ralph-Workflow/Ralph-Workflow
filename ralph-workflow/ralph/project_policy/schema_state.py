from __future__ import annotations

from typing import TYPE_CHECKING, Final

from ralph.project_policy import markers

if TYPE_CHECKING:
    from pathlib import Path

POLICY_SCHEMA_STATES: Final[frozenset[str]] = frozenset(
    {"current", "outdated", "absent", "unknown"}
)


def policy_schema_state(workspace_root: Path) -> str:
    try:
        policy_dir = workspace_root / markers.CANONICAL_DIR
        names = (
            *markers.CORE_POLICY_FILES,
            *markers.CONDITIONAL_POLICY_FILES.values(),
        )
        present = [policy_dir / name for name in names if (policy_dir / name).is_file()]
        if not present:
            return "absent"
        for path in present:
            lines = path.read_text(encoding="utf-8").splitlines()
            first_line = next((line for line in lines if line.strip()), "")
            if first_line.strip() != markers.POLICY_SCHEMA_MARKER:
                return "outdated"
    except (OSError, ValueError, UnicodeDecodeError):
        return "unknown"
    return "current"


__all__ = ["POLICY_SCHEMA_STATES", "policy_schema_state"]
