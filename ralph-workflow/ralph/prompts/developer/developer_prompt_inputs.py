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
    prior_result_status: str = ""
    prior_result_summary: str = ""
    prior_result_next_steps: str = ""
    prior_result_continuation: str = ""
    skills_inline_content: str = ""
    has_docs_mcp: bool = False
    work_unit_id: str = ""
    work_unit_description: str = ""
    work_unit_directories: str = ""
    worker_namespace: str = ""
    is_continuation: bool = False
    # S-5: development timebox publications. ``None`` means "no timebox
    # published" and the run-budget partial renders no minute figures.
    # When the pipeline publishes the warn/deadline epochs (see
    # ``ralph.mcp.protocol.env.DEV_WARN_EPOCH_ENV`` /
    # ``DEV_DEADLINE_EPOCH_ENV``), the partial renders the concrete
    # remaining minutes and the force-cut sentence.
    dev_warn_epoch: float | None = None
    dev_deadline_epoch: float | None = None
    # S-5: which ``AgentTransport`` the executing session is using. ``None``
    # means "unknown" and the partial falls through to the empty-string
    # ``HAS_SUBAGENTS`` (the sequential path). The pipeline always supplies
    # this in production; tests construct inputs directly and may omit it.
    transport: object = None
