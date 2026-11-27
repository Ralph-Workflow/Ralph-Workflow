from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ralph.language_detector import get_project_stack
from ralph.process.manager._supervisor import sanitize_process_environment
from ralph.project_policy import validators
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Sequence

CHECK_MODULE: Final[str] = "ralph.project_policy.check"


def readiness_check_command() -> str:
    return f"{sys.executable} -m {CHECK_MODULE}"


def main(argv: Sequence[str] | None = None) -> int:
    sanitize_process_environment()
    del argv
    workspace = FsWorkspace(Path.cwd())
    findings = validators.validate_readiness(workspace, get_project_stack(workspace))
    for finding in findings:
        sys.stdout.write(f"{finding.requirement_id} {finding.path}: {finding.missing_evidence}\n")
    sys.stdout.write(f"{len(findings)} readiness finding(s)\n")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
