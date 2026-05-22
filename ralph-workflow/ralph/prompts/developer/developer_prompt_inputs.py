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
    artifact_history_path: str = ""
    artifact_history_dir: str = ""
    prompt_name_prefix: str = "development"
    last_retry_error: str = ""
    has_docs_mcp: bool = False
