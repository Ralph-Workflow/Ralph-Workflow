from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from ralph.project_policy import remediation
from ralph.project_policy.models import PolicyFinding

if TYPE_CHECKING:
    from pathlib import Path

_FINDING = PolicyFinding(
    requirement_id="RWP-PROTECTED-RULE:clean-code-policy.md",
    path="docs/ralph-workflow-policy/clean-code-policy.md",
    missing_evidence="drops a protected rule",
    required_outcome="restore it",
)


def test_the_remediation_prompt_names_this_engines_readiness_check() -> None:
    prompt = remediation.render_remediation_prompt([_FINDING])

    assert f"{sys.executable} -m ralph.project_policy.check" in prompt
    assert "never another installed Ralph Workflow" in prompt


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(30)
def test_the_readiness_check_reports_findings_and_fails(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ralph.project_policy.check"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "RWP-" in result.stdout
