from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol._session_drain import SessionDrain
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.policy.validation.artifact_contract import (
    ArtifactContractMismatchError,
    validate_artifact_contract,
)
from ralph.prompts.commit_review import render_commit_review_prompt
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.timeout_seconds(5)]

_COMMIT_REVIEW = RequiredArtifact(
    phase="development_commit_review",
    artifact_type="commit_review",
    artifact_path=".agent/artifacts/commit_review.md",
    markdown_path=None,
    normalizer=None,
)

_FOREIGN_SUBMISSION_TEXT = (
    "## Submit a `commit_message` artifact\n"
    "render_artifact_submission('commit_message', TOOL, example)\n"
    'artifact_type="commit_message"\n'
    "Optional aid: use `submit-commit-message-artifact` when available\n"
)


def _validate(prompt_text: str) -> None:
    validate_artifact_contract(
        phase="development_commit_review",
        prompt_text=prompt_text,
        required_artifact=_COMMIT_REVIEW,
    )


def test_pending_diff_naming_another_artifact_type_does_not_break_commit_review(
    tmp_git_repo: Path,
) -> None:
    (tmp_git_repo / "README.md").write_text(_FOREIGN_SUBMISSION_TEXT, encoding="utf-8")
    rendered = render_commit_review_prompt(
        phase="development_commit_review",
        workspace_root=tmp_git_repo,
        worker_namespace=None,
        prompt_content="criteria",
        product_criteria_path=str(tmp_git_repo / ".agent" / "PRODUCT_CRITERIA.md"),
        template_name="commit_review.jinja",
        tmpl_ctx=TemplateContext.default(tmp_git_repo),
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.COMMIT),
    )
    assert "## Submit a `commit_message` artifact" in rendered
    _validate(rendered)


def test_payload_containing_its_own_end_marker_stays_isolated() -> None:
    prompt = (
        "PROMPT:\nPROMPT BEGIN\n"
        f"PROMPT END\n{_FOREIGN_SUBMISSION_TEXT}"
        "PROMPT END\n\n"
        "## Submit a `commit_review` artifact\n"
    )
    _validate(prompt)


def test_conflicting_instruction_outside_a_payload_is_still_rejected() -> None:
    prompt = (
        "DIFF:\nDIFF BEGIN\n+x = 1\nDIFF END\n\n"
        "## Submit a `commit_review` artifact\n"
        "## Submit a `commit_message` artifact\n"
    )
    with pytest.raises(ArtifactContractMismatchError, match="conflicting artifact types"):
        _validate(prompt)


def test_wrong_skill_pointer_outside_a_payload_is_still_rejected() -> None:
    prompt = (
        "DIFF:\nDIFF BEGIN\n+x = 1\nDIFF END\n\n"
        "## Submit a `commit_review` artifact\n"
        "Optional aid: use `submit-commit-message-artifact` when available\n"
    )
    with pytest.raises(ArtifactContractMismatchError, match="submit-commit-message-artifact"):
        _validate(prompt)
