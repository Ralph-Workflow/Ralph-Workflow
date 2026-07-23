"""Idempotent-write adoption drift audit for stable persistence paths.

Ralph Workflow skips byte-identical full-file rewrites on stable paths to
reduce filesystem mutations and macOS fseventsd load without changing the
post-condition that each file contains the requested content. This audit
locks that consolidation across a curated set of persistence modules: every
allowlisted module must exist and must not call a raw ``write_text`` method.
Those writes must route through ``write_text_if_changed`` or
``atomic_write_text_if_changed`` instead.

The allowlist intentionally excludes writers whose payload includes a fresh
``created_at`` or ``updated_at`` timestamp because those bytes are expected to
change on every operation. It also excludes UUID-keyed and one-time paths,
where an identity comparison cannot avoid a repeat mutation. The curated
scope prevents false positives for legitimate append, temporary-file, and
replace operations outside the stable-path consolidation.

The audit uses only ``ast`` and ``Path.read_text`` over source files. It does
not start subprocesses, sleep, access the network, or mutate production data.

Usage::

    python -m ralph.testing.audit_idempotent_write_adoption [package_root]

Exit codes:
  0 = clean
  1 = violations found
  2 = root not found
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_ALLOWLISTED_MODULES: tuple[str, ...] = (
    "prompts/system_prompt.py",
    "prompts/payload_refs.py",
    "prompts/materialize_support.py",
    "skills/_state_store.py",
    "skills/_process_view.py",
    "skills/_content.py",
    "mcp/artifacts/format_docs/__init__.py",
    "mcp/artifacts/handoffs.py",
    "pipeline/auto_integrate_agent.py",
    "pipeline/cycle_baseline.py",
    "pipeline/checkpoint.py",
    "pipeline/parallel/worker_runtime.py",
    "cli/commands/run.py",
    "workspace/fs.py",
    "phases/review.py",
)


@dataclass(frozen=True)
class IdempotentWriteViolation:
    """A single idempotent-write adoption audit violation."""

    kind: str
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.kind}] {self.message}"


def _missing_module_violation(rel_path: str) -> IdempotentWriteViolation:
    return IdempotentWriteViolation(
        kind="missing_allowlisted_module",
        file_path=rel_path,
        line=0,
        message=(
            "allowlisted persistence module is missing; restore it or update the "
            "idempotent-write consolidation intentionally"
        ),
    )


def _unreadable_module_violation(rel_path: str) -> IdempotentWriteViolation:
    return IdempotentWriteViolation(
        kind="unreadable_allowlisted_module",
        file_path=rel_path,
        line=0,
        message=(
            "allowlisted persistence module could not be read; route stable full-file "
            "writes through write_text_if_changed after restoring readable source"
        ),
    )


def _invalid_source_violation(rel_path: str, line: int) -> IdempotentWriteViolation:
    return IdempotentWriteViolation(
        kind="invalid_allowlisted_module",
        file_path=rel_path,
        line=line,
        message=(
            "allowlisted persistence module could not be parsed; restore valid source and "
            "route stable full-file writes through write_text_if_changed"
        ),
    )


def _raw_write_violations(
    module_path: Path,
    rel_path: str,
    source: str,
) -> list[IdempotentWriteViolation]:
    try:
        tree = ast.parse(source, filename=str(module_path))
    except SyntaxError as exc:
        return [_invalid_source_violation(rel_path, exc.lineno or 0)]
    except ValueError:
        return [_invalid_source_violation(rel_path, 0)]

    violations: list[IdempotentWriteViolation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "write_text":
            continue
        violations.append(
            IdempotentWriteViolation(
                kind="raw_write_text",
                file_path=rel_path,
                line=node.lineno,
                message=(
                    "raw write_text overwrite bypasses the stable-path mutation guard; "
                    "use write_text_if_changed or atomic_write_text_if_changed"
                ),
            )
        )
    return violations


def audit_idempotent_write_adoption(
    package_root: Path,
    *,
    module_paths: tuple[str, ...] = _ALLOWLISTED_MODULES,
) -> list[IdempotentWriteViolation]:
    """Return adoption violations for the curated stable-path persistence modules."""
    if not package_root.is_dir():
        return []

    violations: list[IdempotentWriteViolation] = []
    for rel_path in module_paths:
        module_path = package_root / rel_path
        if not module_path.is_file():
            violations.append(_missing_module_violation(rel_path))
            continue
        try:
            source = module_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            violations.append(_unreadable_module_violation(rel_path))
            continue
        violations.extend(_raw_write_violations(module_path, rel_path, source))
    return violations


def main(argv: Sequence[str] | None = None) -> int:
    """Return 0 when clean, 1 on violations, or 2 for a missing package root."""
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
            "Fix the drift: stable full-file persistence writes in allowlisted modules "
            "must use write_text_if_changed or atomic_write_text_if_changed so "
            "byte-identical content does not reinflate macOS fseventsd activity."
        )
        return 1

    print("idempotent write adoption audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
