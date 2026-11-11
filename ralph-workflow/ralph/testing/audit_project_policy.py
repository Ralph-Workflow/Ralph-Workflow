from __future__ import annotations

import sys
from pathlib import Path

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import validators
from ralph.workspace import FsWorkspace


def _resolve_repo_root() -> Path:
    candidates = (
        Path.cwd(),
        Path(__file__).resolve().parents[3],
        Path(__file__).resolve().parents[2],
    )
    for c in candidates:
        if (c / "docs" / "ralph-workflow-policy").is_dir():
            return c
    return Path(__file__).resolve().parents[3]


REPO_ROOT = _resolve_repo_root()

__all__ = ["collect_violations", "main"]


def collect_violations(repo_root: Path | None = None) -> list[str]:
    root = repo_root if repo_root is not None else REPO_ROOT
    ws = FsWorkspace(root)
    stack = ProjectStack(primary_language="Python")
    findings = validators.validate_readiness(ws, stack)
    return [f"{f.requirement_id}: {f.path}: {f.missing_evidence}" for f in findings if f.blocking]


def main(argv: list[str] | None = None) -> int:
    _ = argv
    violations = collect_violations()
    if violations:
        print(f"Project policy readiness audit: {len(violations)} finding(s):")
        for violation in violations:
            print(f"  {violation}")
        return 1
    print("Project policy readiness audit: OK (pack validates READY under v6)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
