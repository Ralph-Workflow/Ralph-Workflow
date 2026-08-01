"""Compatibility entry point for package-wide idempotent-write enforcement.

Every production filesystem mutation is audited by
:mod:`ralph.testing.audit_filesystem_write_consolidation`.  This retained
entry point deliberately delegates to that fail-closed audit rather than
maintaining a curated list of known writers: a newly added module is scanned
by default and must use the shared persistence primitive or state a local
``# filesystem-write-ok: <reason>`` exception.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.testing.audit_filesystem_write_consolidation import (
    FilesystemWriteViolation as IdempotentWriteViolation,
)
from ralph.testing.audit_filesystem_write_consolidation import (
    audit_filesystem_write_consolidation,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def audit_idempotent_write_adoption(package_root: Path) -> list[IdempotentWriteViolation]:
    """Return every package-wide raw-mutation violation under *package_root*.

    The shared audit rejects raw full-file writes, atomic replacements,
    durability barriers, and related mutation paths unless they route through
    the approved primitive or carry a local, reasoned exception marker.
    """
    return audit_filesystem_write_consolidation(package_root, package_roots=())


def main(argv: Sequence[str] | None = None) -> int:
    """Return 0 when all production mutations are consolidated."""
    if argv is None:
        argv = sys.argv[1:]
    package_root = Path(argv[0]) if argv else Path(__file__).parent.parent
    if not package_root.is_dir():
        print(f"Package root not found: {package_root}", file=sys.stderr)
        return 2

    violations = audit_idempotent_write_adoption(package_root)
    if violations:
        print(f"IDEMPOTENT WRITE ADOPTION VIOLATIONS: {len(violations)}")
        print("=" * 72)
        for violation in violations:
            print(f"  {violation}")
        print()
        print(
            "Fix the drift: production filesystem mutations must route through "
            "ralph.mcp.artifacts.idempotent_write or carry a local "
            "`# filesystem-write-ok: <reason>` marker naming the behavioral contract."
        )
        return 1

    print("idempotent write adoption audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
