"""Pydantic v2 models for ralph configuration.

This module contains all configuration models that mirror the UnifiedConfig
structure from the Rust implementation.
"""

from __future__ import annotations

import pathlib

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from ralph.config.enums import JsonParserType, ReviewDepth

PATH_RUNTIME_CLASS = pathlib.Path


class AgentConfig(BaseModel):  # type: ignore[explicit-any]
    """Configuration for a single AI agent.

    Attributes:
        cmd: Base command to run the agent.
        output_flag: Output format flag for streaming JSON.
        yolo_flag: Flag for autonomous mode (skip permission prompts).
        verbose_flag: Flag for verbose output.
        can_commit: Whether the agent can run git commit.
        json_parser: Which JSON parser to use for agent output.
        model_flag: Optional model/provider flag.
        display_name: Human-readable display name for UI/UX.
    """

    model_config = ConfigDict(frozen=True)

    cmd: str
    output_flag: str = "--json-stream"
    yolo_flag: str | None = None
    verbose_flag: str | None = None
    can_commit: bool = False
    json_parser: JsonParserType = JsonParserType.GENERIC
    model_flag: str | None = None
    display_name: str | None = None


class CloudConfig(BaseModel):  # type: ignore[explicit-any]
    """Optional cloud reporting configuration.

    Attributes:
        enabled: Whether cloud reporting is enabled.
        api_url: Base URL for the cloud API.
        api_key: API key for authentication.
        timeout_secs: Request timeout in seconds.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    api_url: HttpUrl | None = None
    api_key: str | None = None
    timeout_secs: int = 30


class GeneralBehaviorFlags(BaseModel):  # type: ignore[explicit-any]
    """General configuration behavioral flags.

    Attributes:
        interactive: Interactive mode (keep agent in foreground).
        auto_detect_stack: Auto-detect project stack for review guidelines.
        strict_validation: Strict PROMPT.md validation.
    """

    model_config = ConfigDict(frozen=True)

    interactive: bool = False
    auto_detect_stack: bool = True
    strict_validation: bool = False


class GeneralWorkflowFlags(BaseModel):  # type: ignore[explicit-any]
    """General configuration workflow automation flags.

    Attributes:
        checkpoint_enabled: Enable checkpoint/resume functionality.
    """

    model_config = ConfigDict(frozen=True)

    checkpoint_enabled: bool = True


class GeneralExecutionFlags(BaseModel):  # type: ignore[explicit-any]
    """General configuration execution behavior flags.

    Attributes:
        force_universal_prompt: Force universal review prompt for all agents.
        isolation_mode: Isolation mode (prevent context contamination).
    """

    model_config = ConfigDict(frozen=True)

    force_universal_prompt: bool = False
    isolation_mode: bool = True


class GeneralConfig(BaseModel):  # type: ignore[explicit-any]
    """[general] section of ralph-workflow.toml.

    Attributes:
        verbosity: Verbosity level (0-4).
        behavior: Behavioral flags (interactive, auto-detect, strict validation).
        workflow: Workflow automation flags (checkpoint, auto-rebase).
        execution: Execution behavior flags (universal prompt, isolation mode).
        developer_iters: Number of developer iterations.
        reviewer_reviews: Number of reviewer re-review passes.
        developer_context: Developer context level.
        reviewer_context: Reviewer context level.
        review_depth: Review depth level.
        prompt_path: Path to save last prompt.
        templates_dir: User templates directory for custom template overrides.
        git_user_name: Git user name for commits.
        git_user_email: Git user email for commits.
        provider_fallback: Provider/model fallbacks keyed by agent name.
        max_dev_continuations: Maximum continuation attempts per development iteration.
        max_same_agent_retries: Maximum same-agent retry attempts.
        max_commit_residual_retries: Maximum additional residual commit retries.
        max_retries: Maximum retries per agent.
        retry_delay_ms: Base delay between agent retries in milliseconds.
        backoff_multiplier: Multiplier for exponential retry backoff.
        max_backoff_ms: Maximum retry backoff delay in milliseconds.
        max_cycles: Maximum number of full fallback cycles through a drain.
        execution_history_limit: Maximum execution history entries to keep in memory.
    """

    model_config = ConfigDict(frozen=True)

    verbosity: int = 2
    behavior: GeneralBehaviorFlags = Field(default_factory=GeneralBehaviorFlags)
    workflow: GeneralWorkflowFlags = Field(default_factory=GeneralWorkflowFlags)
    execution: GeneralExecutionFlags = Field(default_factory=GeneralExecutionFlags)
    developer_iters: int = Field(default=5, ge=1)
    reviewer_reviews: int = Field(default=2, ge=0)
    developer_context: int = Field(default=1, ge=1)
    reviewer_context: int = Field(default=0, ge=0)
    review_depth: ReviewDepth = ReviewDepth.STANDARD
    prompt_path: pathlib.Path | None = None
    templates_dir: pathlib.Path | None = None
    git_user_name: str | None = None
    git_user_email: str | None = None
    provider_fallback: dict[str, list[str]] = Field(default_factory=dict)
    max_dev_continuations: int = Field(default=2, ge=0)
    max_same_agent_retries: int = Field(default=2, ge=0)
    max_commit_residual_retries: int = Field(default=10, ge=0)
    max_retries: int = Field(default=3, ge=0)
    retry_delay_ms: int = Field(default=1000, ge=0)
    backoff_multiplier: float = Field(default=2.0, ge=1.0)
    max_backoff_ms: int = Field(default=60000, ge=0)
    max_cycles: int = Field(default=3, ge=1)
    execution_history_limit: int = Field(default=1000, ge=1)


class CcsConfig(BaseModel):  # type: ignore[explicit-any]
    """CCS (Claude Code Switch) defaults configuration.

    Attributes:
        output_flag: Output-format flag for CCS.
        yolo_flag: Flag for autonomous mode.
        verbose_flag: Flag for verbose output.
        print_flag: Print flag for non-interactive mode.
        streaming_flag: Streaming flag for JSON output with -p.
        json_parser: Which JSON parser to use for CCS output.
        session_flag: Session continuation flag template.
        can_commit: Whether CCS can run workflow tools.
    """

    model_config = ConfigDict(frozen=True)

    output_flag: str = "--output-format=stream-json"
    yolo_flag: str = "--dangerously-skip-permissions"
    verbose_flag: str = "--verbose"
    print_flag: str = "--print"
    streaming_flag: str = "--include-partial-messages"
    json_parser: str = "claude"
    session_flag: str = "--resume {}"
    can_commit: bool = True


class CcsAliasConfig(BaseModel):  # type: ignore[explicit-any]
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

    model_config = ConfigDict(frozen=True)

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


class UnifiedConfig(BaseModel):  # type: ignore[explicit-any]
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

    model_config = ConfigDict(frozen=True)

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    ccs: CcsConfig = Field(default_factory=CcsConfig)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    ccs_aliases: dict[str, str | CcsAliasConfig] = Field(default_factory=dict)
    agent_chains: dict[str, list[str]] = Field(default_factory=dict)
    agent_drains: dict[str, str] = Field(default_factory=dict)
    cloud: CloudConfig = Field(default_factory=CloudConfig)
