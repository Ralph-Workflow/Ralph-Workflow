from __future__ import annotations

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import validators
from ralph.workspace.fs import FsWorkspace
from tests.doc_roots import REPOSITORY_ROOT


def test_repo_policy_pack_ready() -> None:
    findings = validators.validate_readiness(
        FsWorkspace(REPOSITORY_ROOT),
        ProjectStack(primary_language="Python"),
    )
    assert findings == [], "\n".join(
        f"{finding.requirement_id} | {finding.path} | {finding.missing_evidence}"
        for finding in findings
    )
