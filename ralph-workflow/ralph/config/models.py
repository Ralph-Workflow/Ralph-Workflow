"""Pydantic v2 models for ralph configuration.

This module contains all configuration models that mirror the UnifiedConfig
structure from the Rust implementation.
"""

from __future__ import annotations

import pathlib

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from ralph.config.enums import AgentTransport, JsonParserType, ReviewDepth

PATH_RUNTIME_CLASS = pathlib.Path


class _FrozenConfigModel(BaseModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Private base for frozen configuration models.

    Owns ``model_config = ConfigDict(frozen=True)`` once so descendants do not
    repeat it. Pydantic v2 inherits ``model_config`` when descendants do not
    declare one of their own.
    """

    model_config = ConfigDict(frozen=True)


class AgentConfig(_FrozenConfigModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Configuration for a single AI agent.

    Attributes:
        cmd: Base command to run the agent.
        output_flag: Output format flag for streaming JSON.
        yolo_flag: Optional autonomous/non-interactive flag string.
        verbose_flag: Flag for verbose output.
        can_commit: Whether the agent can run git commit.
        json_parser: Which JSON parser to use for agent output.
        model_flag: Optional model/provider flag.
        print_flag: Optional print flag for non-interactive output mode.
        streaming_flag: Optional streaming flag for partial JSON messages.
        session_flag: Optional session continuation flag template.
        display_name: Human-readable display name for UI/UX.
        transport: Invocation/MCP transport type for the agent runtime.
    """

    cmd: str
    output_flag: str = "--json-stream"
    yolo_flag: str | None = None
    verbose_flag: str | None = None
    can_commit: bool = False
    json_parser: JsonParserType = JsonParserType.GENERIC
    model_flag: str | None = None
    print_flag: str | None = None
    streaming_flag: str | None = None
    session_flag: str | None = None
    display_name: str | None = None
    transport: AgentTransport | None = None

    def model_post_init(self, _context: object) -> None:
        if self.transport is not None:
            return

        parser_to_transport = {
            JsonParserType.CLAUDE: AgentTransport.CLAUDE,
            JsonParserType.CODEX: AgentTransport.CODEX,
            JsonParserType.OPENCODE: AgentTransport.OPENCODE,
        }
        command_to_transport = {
            "claude": AgentTransport.CLAUDE,
            "codex": AgentTransport.CODEX,
            "opencode": AgentTransport.OPENCODE,
        }
        command_name = self.cmd.split()[0] if self.cmd else ""
        inferred_transport = parser_to_transport.get(
            self.json_parser,
            command_to_transport.get(command_name, AgentTransport.GENERIC),
        )
        object.__setattr__(self, "transport", inferred_transport)


class CloudConfig(_FrozenConfigModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Optional cloud reporting configuration.

    Attributes:
        enabled: Whether cloud reporting is enabled.
        api_url: Base URL for the cloud API.
        api_key: API key for authentication.
        timeout_secs: Request timeout in seconds.
    """

    enabled: bool = False
    api_url: HttpUrl | None = None
    api_key: str | None = None
    timeout_secs: int = 30


class GeneralBehaviorFlags(_FrozenConfigModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """General configuration behavioral flags.

    Attributes:
        interactive: Interactive mode (keep agent in foreground).
        auto_detect_stack: Auto-detect project stack for review guidelines.
        strict_validation: Strict PROMPT.md validation.
    """

    interactive: bool = False
    auto_detect_stack: bool = True
    strict_validation: bool = False


class GeneralWorkflowFlags(_FrozenConfigModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """General configuration workflow automation flags.

    Attributes:
        checkpoint_enabled: Enable checkpoint/resume functionality.
    """

    checkpoint_enabled: bool = True


class GeneralExecutionFlags(_FrozenConfigModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """General configuration execution behavior flags.

    Attributes:
        force_universal_prompt: Force universal review prompt for all agents.
    """

    force_universal_prompt: bool = False


class GeneralConfig(_FrozenConfigModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """[general] section of ralph-workflow.toml.

    Attributes:
        verbosity: Verbosity level (0-4).
        behavior: Behavioral flags (interactive, auto-detect, strict validation).
        workflow: Workflow automation flags (checkpoint, auto-rebase).
        execution: Execution behavior flags (universal prompt).
        developer_iters: Number of developer iterations.
        reviewer_reviews: Number of reviewer re-review passes.
        max_development_analysis_iterations: Max development analysis loop budget.
        max_review_analysis_iterations: Max review analysis loop budget.
        developer_context: Developer context level.
        reviewer_context: Reviewer context level.
        review_depth: Review depth level.
        prompt_path: Path to save last prompt.
        templates_dir: User templates directory for custom template overrides.
        git_user_name: Git user name for commits.
        git_user_email: Git user email for commits.
        provider_fallback: Provider/model fallbacks keyed by agent name.
        max_same_agent_retries: Maximum same-agent retry attempts.
        max_commit_residual_retries: Maximum additional residual commit retries.
        max_retries: Maximum retries per agent.
        retry_delay_ms: Base delay between agent retries in milliseconds.
        backoff_multiplier: Multiplier for exponential retry backoff.
        max_backoff_ms: Maximum retry backoff delay in milliseconds.
        max_cycles: Maximum number of full fallback cycles through a drain.
        execution_history_limit: Maximum execution history entries to keep in memory.
        agent_idle_timeout_seconds: Maximum idle seconds before killing a stalled agent process.
        agent_idle_drain_window_seconds: Drain window duration after idle deadline before firing.
        agent_idle_max_waiting_on_child_seconds: Hard ceiling on cumulative
            WAITING_ON_CHILD deferral time.
        agent_idle_poll_interval_seconds: How often the read loop polls for new
            output lines from the agent subprocess.
        agent_parent_exit_grace_seconds: Grace window after parent exits normally
            during which late completion signals or children are awaited.
        agent_descendant_wait_timeout_seconds: Maximum time to wait for descendant
            processes to finish after the parent exits.
        agent_process_exit_wait_seconds: Maximum time to wait for the subprocess to
            exit after its stdout closes. Prevents hangs on subprocesses that close
            stdout but never call exit().
        agent_max_session_seconds: Absolute wall-clock ceiling for the entire agent
            session. Activity cannot reset this ceiling. When set, must be greater
            than agent_idle_timeout_seconds.
        agent_waiting_status_interval_seconds: How often a periodic status update is
            emitted while WAITING_ON_CHILD deferral is active. Controls only emission
            cadence; does NOT affect timeout safety or ceiling math.
        agent_suspect_waiting_on_child_seconds: Cumulative WAITING_ON_CHILD time after
            which a 'suspected frozen' warning is emitted. Purely informational; does
            NOT shorten the hard-stop ceiling. Must be less than
            agent_idle_max_waiting_on_child_seconds when set. None disables suspicion.
    """

    verbosity: int = 2
    behavior: GeneralBehaviorFlags = Field(default_factory=GeneralBehaviorFlags)
    workflow: GeneralWorkflowFlags = Field(default_factory=GeneralWorkflowFlags)
    execution: GeneralExecutionFlags = Field(default_factory=GeneralExecutionFlags)
    developer_iters: int = Field(default=5, ge=1)
    reviewer_reviews: int = Field(default=2, ge=0)
    max_development_analysis_iterations: int = Field(default=3, ge=0)
    max_review_analysis_iterations: int = Field(default=2, ge=0)
    developer_context: int = Field(default=1, ge=1)
    reviewer_context: int = Field(default=0, ge=0)
    review_depth: ReviewDepth = ReviewDepth.STANDARD
    prompt_path: pathlib.Path | None = None
    templates_dir: pathlib.Path | None = None
    git_user_name: str | None = None
    git_user_email: str | None = None
    provider_fallback: dict[str, list[str]] = Field(default_factory=dict)
    max_same_agent_retries: int = Field(default=2, ge=0)
    max_commit_residual_retries: int = Field(default=10, ge=0)
    max_retries: int = Field(default=3, ge=0)
    retry_delay_ms: int = Field(default=1000, ge=0)
    backoff_multiplier: float = Field(default=2.0, ge=1.0)
    max_backoff_ms: int = Field(default=60000, ge=0)
    max_cycles: int = Field(default=3, ge=1)
    execution_history_limit: int = Field(default=1000, ge=1)
    agent_idle_timeout_seconds: float = Field(
        default=300.0,
        gt=0.0,
        description=(
            "Maximum seconds of no-output idle time allowed during an agent"
            " invocation before the process is killed."
        ),
    )
    agent_idle_drain_window_seconds: float = Field(
        default=0.5,
        ge=0.0,
        description=(
            "Drain window duration in seconds after idle deadline before firing."
            " Allows late output to flush before the timeout is declared."
        ),
    )
    agent_idle_max_waiting_on_child_seconds: float = Field(
        default=1800.0,
        gt=0.0,
        description=(
            "Hard ceiling on cumulative WAITING_ON_CHILD deferral time in seconds."
            " Prevents indefinite deferral when children oscillate with active state."
        ),
    )
    agent_idle_poll_interval_seconds: float = Field(
        default=0.05,
        gt=0.0,
        description="How often the read loop polls for new output lines in seconds.",
    )
    agent_parent_exit_grace_seconds: float = Field(
        default=5.0,
        ge=0.0,
        description=(
            "Grace window in seconds after parent process exits normally,"
            " during which late completion signals or appearing children are awaited."
        ),
    )
    agent_descendant_wait_timeout_seconds: float = Field(
        default=30.0,
        ge=0.0,
        description=(
            "Maximum time in seconds to wait for descendant processes to finish"
            " after the parent process exits."
        ),
    )
    agent_descendant_wait_poll_seconds: float = Field(
        default=0.5,
        gt=0.0,
        description=(
            "Poll interval in seconds for descendant-wait and process-exit-wait loops."
            " Values < 0.01s are intended for tests only."
        ),
    )
    agent_process_exit_wait_seconds: float = Field(
        default=30.0,
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
        default=30.0,
        gt=0.0,
        description=(
            "How often in seconds a periodic PROGRESS status update is emitted while"
            " WAITING_ON_CHILD deferral is active. Controls only emission cadence;"
            " does NOT affect timeout safety or ceiling math."
        ),
    )
    agent_suspect_waiting_on_child_seconds: float | None = Field(
        default=600.0,
        gt=0.0,
        description=(
            "Cumulative WAITING_ON_CHILD time in seconds after which a 'suspected"
            " frozen' warning is emitted. Purely informational; does NOT shorten the"
            " hard-stop ceiling. Must be strictly less than"
            " agent_idle_max_waiting_on_child_seconds when set."
        ),
    )

    @model_validator(mode="after")
    def _validate_session_ceiling(self) -> GeneralConfig:
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
        return self


class CcsConfig(_FrozenConfigModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """CCS (Claude Code Switch) defaults configuration.

    Attributes:
        output_flag: Output-format flag for CCS.
        yolo_flag: Default autonomous/non-interactive flag for CCS aliases.
        verbose_flag: Flag for verbose output.
        print_flag: Print flag for non-interactive mode.
        streaming_flag: Streaming flag for JSON output with -p.
        json_parser: Which JSON parser to use for CCS output.
        session_flag: Session continuation flag template.
        can_commit: Whether CCS can run workflow tools.
    """

    output_flag: str = "--output-format=stream-json"
    yolo_flag: str = "--permission-mode auto"
    verbose_flag: str = "--verbose"
    print_flag: str = "--print"
    streaming_flag: str = "--include-partial-messages"
    json_parser: str = "claude"
    session_flag: str = "--resume {}"
    can_commit: bool = True


class CcsAliasConfig(_FrozenConfigModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Per-alias CCS configuration (table form).

    Attributes:
        cmd: Base CCS command to run.
        output_flag: Optional output flag override.
        yolo_flag: Optional yolo flag override.
        verbose_flag: Optional verbose flag override.
        print_flag: Optional print flag override.
        streaming_flag: Optional streaming flag override.
        json_parser: Optional JSON parser override.
        can_commit: Optional can_commit override.
        model_flag: Optional model flag appended to the command.
        session_flag: Optional session continuation flag.
    """

    cmd: str
    output_flag: str | None = None
    yolo_flag: str | None = None
    verbose_flag: str | None = None
    print_flag: str | None = None
    streaming_flag: str | None = None
    json_parser: str | None = None
    can_commit: bool | None = None
    model_flag: str | None = None
    session_flag: str | None = None


class UnifiedConfig(_FrozenConfigModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Top-level merged configuration (global + local + CLI overrides).

    This is the sole source of truth for Ralph configuration,
    located at `~/.config/ralph-workflow.toml` or `.agent/ralph-workflow.toml`.

    Attributes:
        general: General settings.
        ccs: CCS defaults for aliases.
        agents: Agent definitions.
        ccs_aliases: CCS alias mappings.
        agent_chains: Named reusable chain definitions.
        agent_drains: Drain-to-chain bindings for built-in drains.
        cloud: Optional cloud reporting configuration.
    """

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    ccs: CcsConfig = Field(default_factory=CcsConfig)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    ccs_aliases: dict[str, str | CcsAliasConfig] = Field(default_factory=dict)
    agent_chains: dict[str, list[str]] = Field(default_factory=dict)
    agent_drains: dict[str, str] = Field(default_factory=dict)
    cloud: CloudConfig = Field(default_factory=CloudConfig)
