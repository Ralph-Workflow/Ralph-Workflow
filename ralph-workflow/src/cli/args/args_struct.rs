/// Ralph: PROMPT-driven agent orchestrator for git repos
#[derive(Parser, Debug, Default)]
#[command(name = "ralph")]
#[command(about = "PROMPT-driven multi-agent orchestrator for git repos")]
#[command(
    long_about = "Ralph orchestrates AI coding agents to implement changes based on PROMPT.md.\n\n\
    It runs a developer agent for code implementation, then a reviewer agent for\n\
    review and fixes (default), automatically staging and committing the final result."
)]
#[command(version)]
#[command(after_help = "GETTING STARTED:\n\
    ralph --init                 Smart init (infers what you need)\n\
    ralph --init <work-guide>    Create PROMPT.md from a Work Guide\n\
    ralph                        Run the orchestrator\n\
\n\
More help: ralph --extended-help  (shell completion, all presets, troubleshooting)")]
// CLI arguments naturally use many boolean flags. These represent independent
// user choices, not a state machine, so bools are the appropriate type.
pub struct Args {
    /// Verbosity shorthand flags (--quiet, --full)
    #[command(flatten)]
    pub verbosity_shorthand: VerbosityShorthand,

    /// Debug verbosity flag
    #[command(flatten)]
    pub debug_verbosity: DebugVerbosity,

    /// Quick preset mode flags
    #[command(flatten)]
    pub quick_presets: QuickPresets,

    /// Standard preset mode flags
    #[command(flatten)]
    pub standard_presets: StandardPresets,

    /// Unified config initialization flags
    #[command(flatten)]
    pub unified_init: UnifiedInitFlags,

    /// Agent listing flags
    #[command(flatten)]
    pub agent_list: AgentListFlags,

    /// Provider listing flag
    #[command(flatten)]
    pub provider_list: ProviderListFlag,

    /// Shell completion generation flag
    #[command(flatten)]
    pub completion: CompletionFlag,

    /// Work Guide listing flag
    #[command(flatten)]
    pub work_guide_list: WorkGuideListFlag,

    /// Template management commands
    #[command(flatten)]
    pub template_commands: TemplateCommands,

    /// Commit message plumbing flags
    #[command(flatten)]
    pub commit_plumbing: CommitPlumbingFlags,

    /// Commit display plumbing flags
    #[command(flatten)]
    pub commit_display: CommitDisplayFlags,

    /// Recovery command flags
    #[command(flatten)]
    pub recovery: RecoveryFlags,

    /// Rebase control flags
    #[command(flatten)]
    pub rebase_flags: RebaseFlags,

    /// Number of developer iterations (default: 5)
    #[arg(
        long = "developer-iters",
        short = 'D',
        env = "RALPH_DEVELOPER_ITERS",
        value_name = "N",
        help = "Number of developer agent iterations",
        aliases = ["developer-iteration", "dev-iter", "d-iters"]
    )]
    pub developer_iters: Option<u32>,

    /// Number of review-fix cycles (N=0 skips review, N=1 is one review-fix cycle, etc.)
    #[arg(
        long = "reviewer-reviews",
        short = 'R',
        env = "RALPH_REVIEWER_REVIEWS",
        value_name = "N",
        help = "Number of review-fix cycles (0=skip review, 1=one cycle, default: 2)",
        aliases = ["reviewer-count", "reviewer-review"]
    )]
    pub reviewer_reviews: Option<u32>,

    /// Preset for common agent combinations
    #[arg(
        long,
        env = "RALPH_PRESET",
        value_name = "NAME",
        help = "Use a preset agent combination (default, opencode)",
        hide = true
    )]
    pub preset: Option<super::presets::Preset>,

    /// Developer/driver agent to use (from `agent_chain.developer`)
    #[arg(
        long,
        short = 'a',
        env = "RALPH_DEVELOPER_AGENT",
        aliases = ["driver-agent", "dev-agent", "developer"],
        value_name = "AGENT",
        help = "Developer agent for code implementation (default: first in agent_chain.developer)"
    )]
    pub developer_agent: Option<String>,

    /// Reviewer agent to use (from `agent_chain.reviewer`)
    #[arg(
        long,
        short = 'r',
        env = "RALPH_REVIEWER_AGENT",
        aliases = ["rev-agent", "reviewer"],
        value_name = "AGENT",
        help = "Reviewer agent for code review (default: first in agent_chain.reviewer)"
    )]
    pub reviewer_agent: Option<String>,

    /// Developer model/provider override (e.g., "-m opencode/glm-4.7-free")
    #[arg(
        long,
        env = "RALPH_DEVELOPER_MODEL",
        value_name = "MODEL_FLAG",
        help = "Model flag for developer agent (e.g., '-m opencode/glm-4.7-free')",
        hide = true
    )]
    pub developer_model: Option<String>,

    /// Reviewer model/provider override (e.g., "-m opencode/claude-sonnet-4")
    #[arg(
        long,
        env = "RALPH_REVIEWER_MODEL",
        value_name = "MODEL_FLAG",
        help = "Model flag for reviewer agent (e.g., '-m opencode/claude-sonnet-4')",
        hide = true
    )]
    pub reviewer_model: Option<String>,

    /// Developer provider override (e.g., "opencode", "zai", "anthropic", "openai")
    /// Use this to switch providers at runtime without changing agent config.
    /// Combined with the agent's model to form the full model flag.
    /// Provider types: 'opencode' (Zen gateway), 'zai'/'zhipuai' (Z.AI direct), 'anthropic'/'openai' (direct API)
    #[arg(
        long,
        env = "RALPH_DEVELOPER_PROVIDER",
        value_name = "PROVIDER",
        help = "Provider for developer agent: 'opencode' (Zen), 'zai'/'zhipuai' (Z.AI direct), 'anthropic'/'openai' (direct API)",
        hide = true
    )]
    pub developer_provider: Option<String>,

    /// Reviewer provider override (e.g., "opencode", "zai", "anthropic", "openai")
    /// Use this to switch providers at runtime without changing agent config.
    /// Combined with the agent's model to form the full model flag.
    /// Provider types: 'opencode' (Zen gateway), 'zai'/'zhipuai' (Z.AI direct), 'anthropic'/'openai' (direct API)
    #[arg(
        long,
        env = "RALPH_REVIEWER_PROVIDER",
        value_name = "PROVIDER",
        help = "Provider for reviewer agent: 'opencode' (Zen), 'zai'/'zhipuai' (Z.AI direct), 'anthropic'/'openai' (direct API)",
        hide = true
    )]
    pub reviewer_provider: Option<String>,

    /// JSON parser for the reviewer agent (overrides agent config)
    /// Useful for testing different parsers with problematic agents
    #[arg(
        long,
        env = "RALPH_REVIEWER_JSON_PARSER",
        value_name = "PARSER",
        help = "JSON parser for reviewer (claude, codex, gemini, opencode, generic); overrides agent config",
        hide = true
    )]
    pub reviewer_json_parser: Option<String>,

    /// Verbosity level (0=quiet, 1=normal, 2=verbose, 3=full, 4=debug)
    #[arg(
        short,
        long,
        value_name = "LEVEL",
        value_parser = clap::value_parser!(u8).range(0..=4),
        help = "Output verbosity (0=quiet, 1=normal, 2=verbose [default], 3=full, 4=debug); overrides RALPH_VERBOSITY"
    )]
    pub verbosity: Option<u8>,

    /// Disable isolation mode (allow NOTES.md and ISSUES.md to persist)
    #[arg(
        long,
        help = "Disable isolation mode: keep NOTES.md and ISSUES.md between runs",
        hide = true
    )]
    pub no_isolation: bool,

    /// Review depth level (standard, comprehensive, security, incremental)
    #[arg(
        long,
        value_name = "LEVEL",
        help = "Review depth: standard (balanced), comprehensive (thorough), security (OWASP-focused), incremental (changed files only)",
        hide = true
    )]
    pub review_depth: Option<String>,

    /// Path to configuration file (default: ~/.config/ralph-workflow.toml)
    #[arg(
        long,
        short = 'c',
        value_name = "PATH",
        help = "Path to configuration file (default: ~/.config/ralph-workflow.toml)",
        hide = true
    )]
    pub config: Option<std::path::PathBuf>,

    /// Internal: Working directory override for testing.
    /// When set, `app::run` uses this path instead of discovering the repo root
    /// and does not change the global CWD. This enables test parallelism.
    #[arg(skip)]
    pub working_dir_override: Option<std::path::PathBuf>,

    /// Interactive mode: prompt to create PROMPT.md from template when missing
    #[arg(
        long,
        short = 'i',
        help = "Interactive mode: prompt to create PROMPT.md from template when missing",
        hide = true
    )]
    pub interactive: bool,

    /// Git user name override (highest priority in identity resolution chain)
    #[arg(
        long,
        env = "RALPH_GIT_USER_NAME",
        value_name = "NAME",
        help = "Git user name for commits (overrides config, env, and git config)",
        hide = true
    )]
    pub git_user_name: Option<String>,

    /// Git user email override (highest priority in identity resolution chain)
    #[arg(
        long,
        env = "RALPH_GIT_USER_EMAIL",
        value_name = "EMAIL",
        help = "Git user email for commits (overrides config, env, and git config)",
        hide = true
    )]
    pub git_user_email: Option<String>,

    /// Show streaming quality metrics at the end of agent output
    #[arg(
        long,
        help = "Display streaming quality metrics (delta stats, repairs, violations) after agent completion",
        hide = true
    )]
    pub show_streaming_metrics: bool,

    /// Pause behavior before process exit.
    #[arg(
        long,
        env = "RALPH_PAUSE_ON_EXIT",
        value_name = "MODE",
        default_value = "auto",
        help = "Pause before exit: auto (standalone failure only), always, or never",
        hide = true
    )]
    pub pause_on_exit: PauseOnExitMode,
}
