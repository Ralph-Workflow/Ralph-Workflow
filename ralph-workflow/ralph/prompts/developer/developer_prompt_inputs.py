"""Developer prompt iteration inputs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeveloperPromptInputs:
    """Inputs for rendering a developer-iteration prompt."""

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
    # Routing metadata for a prior free-form ``partial`` development
    # result. The original markdown is carried whole so the next
    # iteration reads the next agent's exact handoff rather than the
    # three short fields the structured proof contract used to expose;
    # those fields were the only things the old partial pipeline could
    # forward, and a free-form body often says more than three strings
    # can hold. The status, session id, and partial-route are read
    # alongside when present.
    prior_result_markdown: str = ""
    prior_result_status: str = ""
    prior_session_id: str = ""
    skills_inline_content: str = ""
    has_docs_mcp: bool = False
    is_continuation: bool = False
    # S-5: development timebox publications. ``None`` means "no timebox
    # published" and the run-budget partial renders no minute figures.
    # When the pipeline publishes the warn/deadline epochs (see
    # ``ralph.mcp.protocol.env.DEV_WARN_EPOCH_ENV`` /
    # ``DEV_DEADLINE_EPOCH_ENV``), the partial renders the concrete
    # remaining minutes and the force-cut sentence.
    dev_warn_epoch: float | None = None
    dev_deadline_epoch: float | None = None
