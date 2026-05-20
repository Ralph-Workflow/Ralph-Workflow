"""General Ralph configuration model definitions."""

from pathlib import Path
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from ralph.config._general_workflow_flags import GeneralWorkflowFlags
from ralph.pydantic_compat import RalphBaseModel
from ralph.timeout_defaults import (
    CHILD_EXIT_RECONCILE_SECONDS,
    CHILD_HEARTBEAT_TTL_SECONDS,
    CHILD_PROGRESS_TTL_SECONDS,
    CHILD_STALE_LABEL_TTL_SECONDS,
    DESCENDANT_WAIT_POLL_SECONDS,
    DESCENDANT_WAIT_TIMEOUT_SECONDS,
    DRAIN_WINDOW_SECONDS,
    IDLE_POLL_INTERVAL_SECONDS,
    IDLE_TIMEOUT_SECONDS,
    MAX_WAITING_ON_CHILD_NO_PROGRESS_SECONDS,
    MAX_WAITING_ON_CHILD_SECONDS,
    PARENT_EXIT_GRACE_SECONDS,
    PROCESS_EXIT_WAIT_SECONDS,
    SUSPECT_WAITING_ON_CHILD_SECONDS,
    WAITING_STATUS_INTERVAL_SECONDS,
)


class GeneralConfig(RalphBaseModel):
    """[general] section of ralph-workflow.toml."""

    model_config = ConfigDict(frozen=True)

    verbosity: int = 2
    workflow: GeneralWorkflowFlags = Field(default_factory=GeneralWorkflowFlags)
    developer_iters: int = Field(default=5, ge=1)
    developer_context: int = Field(default=1, ge=1)
    prompt_path: Path | None = None
    templates_dir: Path | None = None
    git_user_name: str | None = None
    git_user_email: str | None = None
    provider_fallback: dict[str, list[str]] = Field(default_factory=dict)
    max_same_agent_retries: int = Field(default=10, ge=0)
    max_commit_residual_retries: int = Field(default=10, ge=0)
    max_retries: int = Field(default=3, ge=0)
    retry_delay_ms: int = Field(default=1000, ge=0)
    backoff_multiplier: float = Field(default=2.0, ge=1.0)
    max_backoff_ms: int = Field(default=60000, ge=0)
    max_cycles: int = Field(default=3, ge=1)
    execution_history_limit: int = Field(default=1000, ge=1)
    agent_idle_timeout_seconds: float = Field(
        default=IDLE_TIMEOUT_SECONDS,
        gt=0.0,
        description=(
            "Maximum seconds of no-output idle time allowed during an agent"
            " invocation before the process is killed."
        ),
    )
    agent_idle_drain_window_seconds: float = Field(
        default=DRAIN_WINDOW_SECONDS,
        ge=0.0,
        description=(
            "Drain window duration in seconds after idle deadline before firing."
            " Allows late output to flush before the timeout is declared."
        ),
    )
    agent_idle_max_waiting_on_child_seconds: float = Field(
        default=MAX_WAITING_ON_CHILD_SECONDS,
        gt=0.0,
        description=(
            "Hard ceiling on cumulative WAITING_ON_CHILD deferral time in seconds."
            " Prevents indefinite deferral when children oscillate with active state."
        ),
    )
    agent_idle_poll_interval_seconds: float = Field(
        default=IDLE_POLL_INTERVAL_SECONDS,
        gt=0.0,
        description="How often the read loop polls for new output lines in seconds.",
    )
    agent_parent_exit_grace_seconds: float = Field(
        default=PARENT_EXIT_GRACE_SECONDS,
        ge=0.0,
        description=(
            "Grace window in seconds after parent process exits normally,"
            " during which late completion signals or appearing children are awaited."
        ),
    )
    agent_descendant_wait_timeout_seconds: float = Field(
        default=DESCENDANT_WAIT_TIMEOUT_SECONDS,
        ge=0.0,
        description=(
            "Maximum time in seconds to wait for descendant processes to finish"
            " after the parent process exits."
        ),
    )
    agent_descendant_wait_poll_seconds: float = Field(
        default=DESCENDANT_WAIT_POLL_SECONDS,
        gt=0.0,
        description=(
            "Poll interval in seconds for descendant-wait and process-exit-wait loops."
            " Values < 0.01s are intended for tests only."
        ),
    )
    agent_process_exit_wait_seconds: float = Field(
        default=PROCESS_EXIT_WAIT_SECONDS,
        ge=0.0,
        description=(
            "Maximum time in seconds to wait for the subprocess to exit after its"
            " stdout closes. Prevents hangs on subprocesses that close stdout but"
            " never call exit()."
        ),
    )
    agent_max_session_seconds: float | None = Field(
        default=None,
        gt=0.0,
        description=(
            "Absolute wall-clock ceiling in seconds for the entire agent session."
            " Activity cannot reset this ceiling. Must be >= agent_idle_timeout_seconds"
            " when set."
        ),
    )
    agent_waiting_status_interval_seconds: float = Field(
        default=WAITING_STATUS_INTERVAL_SECONDS,
        gt=0.0,
        description=(
            "How often in seconds a periodic PROGRESS status update is emitted while"
            " WAITING_ON_CHILD deferral is active. Controls only emission cadence;"
            " does NOT affect timeout safety or ceiling math."
        ),
    )
    agent_suspect_waiting_on_child_seconds: float | None = Field(
        default=SUSPECT_WAITING_ON_CHILD_SECONDS,
        gt=0.0,
        description=(
            "Cumulative WAITING_ON_CHILD time in seconds after which a 'suspected"
            " frozen' warning is emitted. Purely informational; does NOT shorten the"
            " hard-stop ceiling. Must be strictly less than"
            " agent_idle_max_waiting_on_child_seconds when set."
        ),
    )
    agent_idle_no_progress_waiting_on_child_seconds: float | None = Field(
        default=MAX_WAITING_ON_CHILD_NO_PROGRESS_SECONDS,
        gt=0.0,
        description=(
            "Hard ceiling on cumulative WAITING_ON_CHILD time when corroboration shows"
            " the child is alive but not making progress (heartbeat-only, stale-label,"
            " or OS-descendant-only evidence). Must be <= agent_idle_max_waiting_on_child_seconds."
            " When None, the no-progress ceiling is disabled."
        ),
    )
    agent_child_progress_ttl_seconds: float = Field(
        default=CHILD_PROGRESS_TTL_SECONDS,
        gt=0.0,
        description=(
            "Maximum seconds since last child progress signal"
            " before the child is treated as not-progressing."
        ),
    )
    agent_child_heartbeat_ttl_seconds: float = Field(
        default=CHILD_HEARTBEAT_TTL_SECONDS,
        gt=0.0,
        description="Maximum seconds since last child heartbeat before heartbeat is stale.",
    )
    agent_child_stale_label_ttl_seconds: float = Field(
        default=CHILD_STALE_LABEL_TTL_SECONDS,
        gt=0.0,
        description=(
            "Grace period during which a child label may persist"
            " after the underlying child evidence has gone stale."
        ),
    )
    agent_child_exit_reconcile_seconds: float = Field(
        default=CHILD_EXIT_RECONCILE_SECONDS,
        ge=0.0,
        description=(
            "Reconciliation window after stdout EOF during which"
            " late terminal acks are still accepted."
        ),
    )

    @model_validator(mode="after")
    def _validate_session_ceiling(self) -> Self:
        if (
            self.agent_max_session_seconds is not None
            and self.agent_max_session_seconds < self.agent_idle_timeout_seconds
        ):
            msg = (
                "agent_max_session_seconds must be >= agent_idle_timeout_seconds"
                f" (got {self.agent_max_session_seconds} < {self.agent_idle_timeout_seconds})"
            )
            raise ValueError(msg)
        if (
            self.agent_suspect_waiting_on_child_seconds is not None
            and self.agent_suspect_waiting_on_child_seconds
            >= self.agent_idle_max_waiting_on_child_seconds
        ):
            msg = (
                "agent_suspect_waiting_on_child_seconds must be strictly less than"
                " agent_idle_max_waiting_on_child_seconds"
                f" (got {self.agent_suspect_waiting_on_child_seconds}"
                f" >= {self.agent_idle_max_waiting_on_child_seconds})"
            )
            raise ValueError(msg)
        if self.agent_child_heartbeat_ttl_seconds > self.agent_child_progress_ttl_seconds:
            msg = (
                "agent_child_heartbeat_ttl_seconds must be <= agent_child_progress_ttl_seconds"
                f" (got {self.agent_child_heartbeat_ttl_seconds}"
                f" > {self.agent_child_progress_ttl_seconds})"
            )
            raise ValueError(msg)
        if self.agent_child_stale_label_ttl_seconds > self.agent_child_progress_ttl_seconds:
            msg = (
                "agent_child_stale_label_ttl_seconds must be <= agent_child_progress_ttl_seconds"
                f" (got {self.agent_child_stale_label_ttl_seconds}"
                f" > {self.agent_child_progress_ttl_seconds})"
            )
            raise ValueError(msg)
        if (
            self.agent_idle_no_progress_waiting_on_child_seconds is not None
            and self.agent_idle_no_progress_waiting_on_child_seconds
            > self.agent_idle_max_waiting_on_child_seconds
        ):
            msg = (
                "agent_idle_no_progress_waiting_on_child_seconds must be <="
                " agent_idle_max_waiting_on_child_seconds"
                f" (got {self.agent_idle_no_progress_waiting_on_child_seconds}"
                f" > {self.agent_idle_max_waiting_on_child_seconds})"
            )
            raise ValueError(msg)
        return self


__all__ = ["GeneralConfig", "GeneralWorkflowFlags"]
