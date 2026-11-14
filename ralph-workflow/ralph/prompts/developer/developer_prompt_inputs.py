from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class DeveloperPromptInputs:
    prompt_content: str | None
    plan_content: str | None
    analysis_feedback_content: str | None = None
    plan_path: str = ""
    analysis_feedback_path: str = ""
    analysis_feedback_status: str = ""
    artifact_history_path: str = ""
    artifact_history_dir: str = ""
    product_criteria_path: str = ""
    payload_root: str = ""
    prompt_name_prefix: str = "development"
    last_retry_error: str = ""
    prior_result_markdown: str = ""
    prior_result_status: str = ""
    prior_session_id: str = ""
    skills_inline_content: str = ""
    has_docs_mcp: bool = False
    is_continuation: bool = False
    dev_warn_epoch: float | None = None
    dev_deadline_epoch: float | None = None
    last_review_rejection_reason: str = ""
    rollback_ref: str = ""
    last_proposal_ref: str = ""
    installed_skills: tuple[str, ...] = ()
    skill_variables: Mapping[str, str] | None = None
