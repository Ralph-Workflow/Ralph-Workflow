//! Configuration types and enums.
//!
//! This module defines the core configuration types used throughout Ralph:
//! - [`Config`]: The main configuration struct
//! - [`ReviewDepth`]: Review thoroughness levels
//! - [`Verbosity`]: Output verbosity levels

// Re-export cloud types so existing paths like `config::types::CloudConfig` remain valid.
pub use super::cloud::{
    CloudConfig, CloudStateConfig, GitAuthMethod, GitAuthStateMethod, GitRemoteConfig,
    GitRemoteStateConfig,
};
use super::truncation;
use std::path::PathBuf;

/// Review depth levels for controlling review thoroughness.
///
/// # Variants
///
/// * `Standard` - Balanced review covering functionality, quality, and security
/// * `Comprehensive` - In-depth analysis with priority-ordered checks
/// * `Security` - Security-focused analysis emphasizing OWASP Top 10
/// * `Incremental` - Focused review of changed files only (git diff)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ReviewDepth {
    /// Standard review - balanced coverage of functionality, quality, and security
    #[default]
    Standard,
    /// Comprehensive review - in-depth analysis with priority-ordered checks
    Comprehensive,
    /// Security-focused review - emphasizes security analysis above all else
    Security,
    /// Incremental review - focuses only on changed files (git diff)
    Incremental,
}

impl ReviewDepth {
    /// Parse review depth from string.
    ///
    /// # Arguments
    ///
    /// * `s` - The string to parse
    ///
    /// # Returns
    ///
    /// Returns `Some(ReviewDepth)` if the string matches a known alias,
    /// `None` otherwise.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// assert_eq!(ReviewDepth::from_str("standard"), Some(ReviewDepth::Standard));
    /// assert_eq!(ReviewDepth::from_str("security"), Some(ReviewDepth::Security));
    /// ```
    pub(crate) fn from_str(s: &str) -> Option<Self> {
        match s.to_lowercase().as_str() {
            "standard" | "default" | "normal" => Some(Self::Standard),
            "comprehensive" | "thorough" | "full" => Some(Self::Comprehensive),
            "security" | "secure" | "security-focused" => Some(Self::Security),
            "incremental" | "diff" | "changed" => Some(Self::Incremental),
            _ => None,
        }
    }

    /// Get a description for display.
    pub(crate) const fn description(self) -> &'static str {
        match self {
            Self::Standard => "Balanced review covering functionality, quality, and security",
            Self::Comprehensive => "In-depth analysis with priority-ordered checks",
            Self::Security => "Security-focused analysis emphasizing OWASP Top 10",
            Self::Incremental => "Focused review of changed files only (git diff)",
        }
    }
}

/// Verbosity levels for output.
///
/// # Variants
///
/// * `Quiet` (0) - Minimal output, aggressive truncation
/// * `Normal` (1) - Balanced output with moderate truncation
/// * `Verbose` (2) - Expanded output limits (default)
/// * `Full` (3) - No truncation, show all content
/// * `Debug` (4) - Maximum verbosity, includes raw JSON and detailed info
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verbosity {
    /// Quiet - minimal output, aggressive truncation
    Quiet = 0,
    /// Normal - balanced output with moderate truncation
    Normal = 1,
    /// Verbose - expanded output limits (default)
    Verbose = 2,
    /// Full - no truncation, show all content
    Full = 3,
    /// Debug - maximum verbosity, includes raw JSON and detailed info
    Debug = 4,
}

impl From<u8> for Verbosity {
    fn from(v: u8) -> Self {
        match v {
            0 => Self::Quiet,
            1 => Self::Normal,
            2 => Self::Verbose,
            3 => Self::Full,
            _ => Self::Debug,
        }
    }
}

impl Verbosity {
    /// Get truncation limit for content type.
    ///
    /// # Arguments
    ///
    /// * `content_type` - The type of content:
    ///   - "text": Assistant text output
    ///   - "`tool_result"`: Tool execution results
    ///   - "`tool_input"`: Tool input parameters
    ///   - "user": User messages
    ///   - "result": Final result summaries
    ///   - "command": Command execution strings
    ///   - "`agent_msg"`: Agent messages/thinking
    ///
    /// # Returns
    ///
    /// The maximum number of characters to display for the given content type.
    pub(crate) fn truncate_limit(self, content_type: &str) -> usize {
        truncation::get_limit(self as u8, content_type)
    }

    /// Returns true if this verbosity level should show debug information.
    pub(crate) const fn is_debug(self) -> bool {
        matches!(self, Self::Debug)
    }

    /// Returns true if this verbosity level is at least Verbose.
    pub(crate) const fn is_verbose(self) -> bool {
        matches!(self, Self::Verbose | Self::Full | Self::Debug)
    }

    /// Returns true if tool inputs should be shown (Normal and above).
    ///
    /// Tool inputs provide crucial context for understanding what the agent is doing.
    /// They are shown at Normal level and above for better usability.
    pub(crate) const fn show_tool_input(self) -> bool {
        !matches!(self, Self::Quiet)
    }
}

/// Behavioral flags for Ralph configuration.
///
/// Groups user interaction and validation-related boolean settings.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct BehavioralFlags {
    /// Interactive mode (keep agent in foreground)
    pub(crate) interactive: bool,
    /// Whether to auto-detect project stack for review guidelines
    pub(crate) auto_detect_stack: bool,
    /// Whether to run strict PROMPT.md validation
    pub(crate) strict_validation: bool,
}

/// Feature flags for Ralph configuration.
///
/// Groups optional feature toggle settings.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct FeatureFlags {
    /// Whether to enable checkpoint/resume functionality
    pub(crate) checkpoint_enabled: bool,
    /// Force universal review prompt for all agents (default: auto-detect)
    /// When true, the universal/simplified prompt is always used for review
    pub(crate) force_universal_prompt: bool,
}

/// Ralph configuration.
///
/// This struct holds all configuration options for Ralph, populated from
/// environment variables and CLI arguments. Default values are applied
/// via [`Default::default()`].
#[derive(Debug, Clone, PartialEq)]
// Configuration options naturally use many boolean flags. These represent
// independent feature toggles, not a state machine, so bools are appropriate.
pub struct Config {
    /// Developer (driver) agent (set via CLI, env, or `agent_chain`)
    pub(crate) developer_agent: Option<String>,
    /// Reviewer agent (set via CLI, env, or `agent_chain`)
    pub(crate) reviewer_agent: Option<String>,
    /// Developer command override
    pub(crate) developer_cmd: Option<String>,
    /// Reviewer command override
    pub(crate) reviewer_cmd: Option<String>,
    /// Commit command override
    pub(crate) commit_cmd: Option<String>,
    /// Developer model override (e.g., "-m opencode/glm-4.7-free")
    /// Passed to the agent's `model_flag` parameter
    pub(crate) developer_model: Option<String>,
    /// Reviewer model override (e.g., "-m opencode/claude-sonnet-4")
    /// Passed to the agent's `model_flag` parameter
    pub(crate) reviewer_model: Option<String>,
    /// Developer provider override (e.g., "opencode", "anthropic", "openai")
    /// When set, constructs the model flag as "-m {`provider}/{model_name`}"
    pub(crate) developer_provider: Option<String>,
    /// Reviewer provider override (e.g., "opencode", "anthropic", "openai")
    /// When set, constructs the model flag as "-m {`provider}/{model_name`}"
    pub(crate) reviewer_provider: Option<String>,
    /// JSON parser override for the reviewer agent (claude, codex, gemini, opencode, generic)
    /// When set, overrides the agent's configured `json_parser` setting
    pub(crate) reviewer_json_parser: Option<String>,
    /// Feature flags (checkpoint, universal prompt)
    pub(crate) features: FeatureFlags,
    /// Number of developer iterations
    pub(crate) developer_iters: u32,
    /// Number of reviewer re-review passes after fix
    pub(crate) reviewer_reviews: u32,
    /// Fast check command (optional)
    pub(crate) fast_check_cmd: Option<String>,
    /// Full check command (optional)
    pub(crate) full_check_cmd: Option<String>,
    /// Behavioral flags (interactive, auto-detect, strict validation)
    pub(crate) behavior: BehavioralFlags,
    /// Path to save last prompt
    pub(crate) prompt_path: PathBuf,
    /// User templates directory for custom template overrides
    /// When set, templates in this directory take priority over embedded templates
    pub(crate) user_templates_dir: Option<PathBuf>,
    /// Developer context level (0=minimal, 1=normal)
    pub(crate) developer_context: u8,
    /// Reviewer context level (0=minimal/fresh eyes, 1=normal)
    pub(crate) reviewer_context: u8,
    /// Verbosity level
    pub(crate) verbosity: Verbosity,
    /// Review depth level (standard, comprehensive, security, incremental)
    pub(crate) review_depth: ReviewDepth,
    /// Isolation mode: when true, NOTES.md and ISSUES.md are not generated and
    /// any existing ones are deleted at the start of each run. This prevents
    /// context contamination from previous runs. Default: true.
    pub(crate) isolation_mode: bool,
    /// Git user name for commits (optional, falls back to git config)
    pub(crate) git_user_name: Option<String>,
    /// Git user email for commits (optional, falls back to git config)
    pub(crate) git_user_email: Option<String>,
    /// Show streaming quality metrics at the end of agent output.
    ///
    /// This field is `pub(crate)` as streaming metrics are an internal concern.
    /// External access is not required; metrics are displayed via CLI flag.
    pub(crate) show_streaming_metrics: bool,
    /// Maximum number of format correction retries during review output parsing.
    /// When the reviewer agent produces unparseable output, the orchestrator will
    /// retry up to this many times with a format correction prompt. Default: 5.
    pub(crate) review_format_retries: u32,
    /// Maximum continuation attempts when developer returns "partial" or "failed".
    ///
    /// # Semantics
    ///
    /// This value counts *continuation attempts* beyond the initial attempt.
    /// Total valid attempts per iteration is `1 + max_dev_continuations`.
    ///
    /// - `0` = no continuations (1 total attempt)
    /// - `2` = two continuations (3 total attempts)
    ///
    /// # Default Behavior
    ///
    /// **CRITICAL:** The system ALWAYS applies a default of 2 (3 total attempts) when this
    /// field is None. The Option wrapper exists ONLY for backward compatibility with direct
    /// Config construction (`Config::default()`, `Config::test_default()`).
    ///
    /// When loaded via `config_from_unified()`:
    /// - `UnifiedConfig::general.max_dev_continuations` has serde default of 2
    /// - Converted to Some(2) in Config
    /// - Applied unconditionally in `create_initial_state_with_config()`
    ///
    /// This ensures dev loop is ALWAYS bounded, preventing infinite continuation cycles.
    ///
    /// Default: 2 continuations (3 total attempts per iteration).
    pub max_dev_continuations: Option<u32>,
    /// Maximum same-agent retry attempts for invocation failures that should not
    /// immediately trigger agent fallback (e.g., timeout/internal/unknown and other
    /// non-auth, non-rate-limit failures).
    ///
    /// # Semantics
    ///
    /// This value is a *failure budget* for the current agent: it counts consecutive
    /// failures that are routed through the reducer's same-agent retry path.
    ///
    /// With `max_same_agent_retries = 2`:
    /// - 1st failure → retry the same agent
    /// - 2nd failure → fall back to the next agent
    ///
    /// Default: 2 (one retry before falling back).
    pub max_same_agent_retries: Option<u32>,
    /// Maximum additional residual commit retries after the initial residual-files check.
    ///
    /// This value counts retry passes beyond pass 1.
    /// Default: 10 additional retries (carry forward after pass 11).
    pub max_commit_residual_retries: Option<u32>,
    /// Maximum execution history entries to keep in memory (default: 1000).
    /// Prevents unbounded memory growth by dropping oldest entries when limit is reached.
    pub execution_history_limit: usize,
    /// Cloud runtime configuration (internal).
    pub(crate) cloud: CloudConfig,
}

impl Config {
    /// Get the user templates directory.
    #[must_use]
    pub const fn user_templates_dir(&self) -> Option<&std::path::PathBuf> {
        self.user_templates_dir.as_ref()
    }

    /// Create a test-appropriate Config with safe defaults.
    ///
    /// This function creates a Config suitable for integration tests,
    /// with all agent execution disabled and isolation mode enabled.
    /// It does NOT load from environment variables or config files.
    #[must_use]
    pub fn test_default() -> Self {
        Self {
            developer_agent: Some("codex".to_string()),
            reviewer_agent: Some("codex".to_string()),
            developer_cmd: None,
            reviewer_cmd: None,
            commit_cmd: None,
            developer_model: None,
            reviewer_model: None,
            developer_provider: None,
            reviewer_provider: None,
            reviewer_json_parser: None,
            features: FeatureFlags {
                checkpoint_enabled: true,
                force_universal_prompt: false,
            },
            developer_iters: 0,
            reviewer_reviews: 0,
            fast_check_cmd: None,
            full_check_cmd: None,
            behavior: BehavioralFlags {
                interactive: false,
                auto_detect_stack: false,
                strict_validation: false,
            },
            prompt_path: PathBuf::from(".agent/last_prompt.txt"),
            user_templates_dir: None,
            developer_context: 0,
            reviewer_context: 0,
            verbosity: Verbosity::Quiet,
            review_depth: ReviewDepth::Standard,
            isolation_mode: true,
            git_user_name: Some("Test".to_string()),
            git_user_email: Some("test@example.com".to_string()),
            show_streaming_metrics: false,
            review_format_retries: 5,
            max_dev_continuations: Some(2),
            max_same_agent_retries: Some(2),
            max_commit_residual_retries: Some(10),
            execution_history_limit: 1000,
            cloud: CloudConfig::disabled(),
        }
    }

    /// Set isolation mode and return new config (functional pattern).
    #[must_use]
    pub fn with_isolation_mode(self, isolation_mode: bool) -> Self {
        Self {
            isolation_mode,
            ..self
        }
    }

    /// Set developer iterations and return new config (functional pattern).
    #[must_use]
    pub fn with_developer_iters(self, iters: u32) -> Self {
        Self {
            developer_iters: iters,
            ..self
        }
    }

    /// Set reviewer reviews and return new config (functional pattern).
    #[must_use]
    pub fn with_reviewer_reviews(self, reviews: u32) -> Self {
        Self {
            reviewer_reviews: reviews,
            ..self
        }
    }

    /// Set `auto_detect_stack` and return new config (functional pattern).
    #[must_use]
    pub fn with_auto_detect_stack(self, auto_detect: bool) -> Self {
        Self {
            behavior: BehavioralFlags {
                auto_detect_stack: auto_detect,
                ..self.behavior
            },
            ..self
        }
    }

    /// Set verbosity and return new config (functional pattern).
    #[must_use]
    pub fn with_verbosity(self, verbosity: Verbosity) -> Self {
        Self { verbosity, ..self }
    }

    /// Set `review_depth` and return new config (functional pattern).
    #[must_use]
    pub fn with_review_depth(self, review_depth: ReviewDepth) -> Self {
        Self {
            review_depth,
            ..self
        }
    }

    /// Set `developer_agent` and return new config (functional pattern).
    #[must_use]
    pub fn with_developer_agent(self, agent: String) -> Self {
        Self {
            developer_agent: Some(agent),
            ..self
        }
    }

    /// Set `reviewer_agent` and return new config (functional pattern).
    #[must_use]
    pub fn with_reviewer_agent(self, agent: String) -> Self {
        Self {
            reviewer_agent: Some(agent),
            ..self
        }
    }
}

impl Default for Config {
    fn default() -> Self {
        super::loader::default_config()
    }
}
