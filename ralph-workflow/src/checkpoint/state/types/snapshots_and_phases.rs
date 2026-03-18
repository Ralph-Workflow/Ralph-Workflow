// Checkpoint state type definitions, enums, and basic data structures.
//
// This file contains the core types for the checkpoint system including
// PipelinePhase, RebaseState, and various snapshot structs.

/// Default directory for Ralph's internal files.
const AGENT_DIR: &str = ".agent";

/// Default checkpoint file name.
const CHECKPOINT_FILE: &str = "checkpoint.json";

/// Current checkpoint format version.
///
/// Increment this when making breaking changes to the checkpoint format.
/// This allows for future migration logic if needed.
/// v1: Initial checkpoint format
/// v2: Added `run_id`, `parent_run_id`, `resume_count`, `actual_developer_runs`, `actual_reviewer_runs`
/// v3: Added `execution_history`, `file_system_state` for hardened resume
const CHECKPOINT_VERSION: u32 = 3;

/// Get the checkpoint file path.
///
/// By default, the checkpoint is stored in `.agent/checkpoint.json`
/// relative to the current working directory. This function provides
/// a single point of control for the checkpoint location, making it
/// easier to configure or override in the future if needed.
fn checkpoint_path() -> String {
    format!("{AGENT_DIR}/{CHECKPOINT_FILE}")
}

/// Calculate SHA-256 checksum from bytes.
///
/// This is the core checksum calculation used by both file-based and
/// workspace-based checksum functions.
pub(crate) fn calculate_checksum_from_bytes(content: &[u8]) -> String {
    format!("{:x}", Sha256::digest(content))
}

/// Snapshot of CLI arguments for exact restoration.
///
/// Captures all relevant CLI arguments so that resuming a pipeline
/// uses the exact same configuration as the original run.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CliArgsSnapshot {
    /// Number of developer iterations (-D flag)
    pub developer_iters: u32,
    /// Number of reviewer reviews (-R flag)
    pub reviewer_reviews: u32,
    /// Review depth level (if specified)
    pub review_depth: Option<String>,
    /// Isolation mode: when false, NOTES.md and ISSUES.md persist between iterations.
    /// Default is true (isolation enabled).
    #[serde(default = "default_isolation_mode")]
    pub isolation_mode: bool,
    /// Verbosity level (0=Quiet, 1=Normal, 2=Verbose, 3=Full, 4=Debug).
    /// Default is 2 (Verbose).
    #[serde(default = "default_verbosity")]
    pub verbosity: u8,
    /// Show streaming quality metrics at the end of agent output.
    /// Default is false.
    #[serde(default)]
    pub show_streaming_metrics: bool,
    /// JSON parser override for the reviewer agent (claude, codex, gemini, opencode, generic)
    #[serde(default)]
    pub reviewer_json_parser: Option<String>,
}

/// Default value for `isolation_mode` (true = isolation enabled).
const fn default_isolation_mode() -> bool {
    true
}

/// Default value for verbosity (2 = Verbose).
const fn default_verbosity() -> u8 {
    2
}

/// Builder for creating [`CliArgsSnapshot`] instances.
///
/// Provides a fluent interface for constructing CLI argument snapshots
/// without exceeding function argument limits.
pub struct CliArgsSnapshotBuilder {
    developer_iters: u32,
    reviewer_reviews: u32,
    review_depth: Option<String>,
    isolation_mode: bool,
    verbosity: u8,
    show_streaming_metrics: bool,
    reviewer_json_parser: Option<String>,
}

impl CliArgsSnapshotBuilder {
    /// Create a new builder with required fields.
    #[must_use]
    pub const fn new(
        developer_iters: u32,
        reviewer_reviews: u32,
        review_depth: Option<String>,
        isolation_mode: bool,
    ) -> Self {
        Self {
            developer_iters,
            reviewer_reviews,
            review_depth,
            isolation_mode,
            verbosity: 2,
            show_streaming_metrics: false,
            reviewer_json_parser: None,
        }
    }

    /// Set the verbosity level.
    #[must_use]
    pub fn verbosity(self, verbosity: u8) -> Self {
        Self { verbosity, ..self }
    }

    /// Set whether to show streaming metrics.
    #[must_use]
    pub fn show_streaming_metrics(self, show: bool) -> Self {
        Self {
            show_streaming_metrics: show,
            ..self
        }
    }

    /// Set the reviewer JSON parser override.
    #[must_use]
    pub fn reviewer_json_parser(self, parser: Option<String>) -> Self {
        Self {
            reviewer_json_parser: parser,
            ..self
        }
    }

    /// Build the snapshot.
    #[must_use]
    pub fn build(self) -> CliArgsSnapshot {
        CliArgsSnapshot {
            developer_iters: self.developer_iters,
            reviewer_reviews: self.reviewer_reviews,
            review_depth: self.review_depth,
            isolation_mode: self.isolation_mode,
            verbosity: self.verbosity,
            show_streaming_metrics: self.show_streaming_metrics,
            reviewer_json_parser: self.reviewer_json_parser,
        }
    }
}

impl CliArgsSnapshot {
    /// Create a snapshot from CLI argument values.
    ///
    /// This is a convenience method for test code.
    /// For production code, use [`CliArgsSnapshotBuilder`] for better readability.
    #[cfg(test)]
    #[must_use]
    pub fn new(
        developer_iters: u32,
        reviewer_reviews: u32,
        review_depth: Option<String>,
        isolation_mode: bool,
        verbosity: u8,
        show_streaming_metrics: bool,
        reviewer_json_parser: Option<String>,
    ) -> Self {
        CliArgsSnapshotBuilder::new(
            developer_iters,
            reviewer_reviews,
            review_depth,
            isolation_mode,
        )
        .verbosity(verbosity)
        .show_streaming_metrics(show_streaming_metrics)
        .reviewer_json_parser(reviewer_json_parser)
        .build()
    }
}

/// Snapshot of agent configuration.
///
/// Captures the complete agent configuration to ensure
/// the exact same agent behavior is used when resuming.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentConfigSnapshot {
    /// Agent name
    pub name: String,
    /// Agent command
    pub cmd: String,
    /// Output flag for JSON extraction
    pub output_flag: String,
    /// YOLO flag (if any)
    pub yolo_flag: Option<String>,
    /// Whether this agent can commit
    pub can_commit: bool,
    /// Model override (e.g., "-m opencode/glm-4.7-free").
    /// Default is None (use agent's configured model).
    #[serde(default)]
    pub model_override: Option<String>,
    /// Provider override (e.g., "opencode", "anthropic").
    /// Default is None (use agent's configured provider).
    #[serde(default)]
    pub provider_override: Option<String>,
    /// Context level (0=minimal, 1=normal).
    /// Default is 1 (normal context).
    #[serde(default = "default_context_level")]
    pub context_level: u8,
}

/// Default value for `context_level` (1 = normal context).
const fn default_context_level() -> u8 {
    1
}

impl AgentConfigSnapshot {
    /// Create a snapshot from agent configuration.
    #[must_use]
    pub const fn new(
        name: String,
        cmd: String,
        output_flag: String,
        yolo_flag: Option<String>,
        can_commit: bool,
    ) -> Self {
        Self {
            name,
            cmd,
            output_flag,
            yolo_flag,
            can_commit,
            model_override: None,
            provider_override: None,
            context_level: default_context_level(),
        }
    }

    /// Set model override.
    #[must_use]
    pub fn with_model_override(self, model: Option<String>) -> Self {
        Self {
            model_override: model,
            ..self
        }
    }

    /// Set provider override.
    #[must_use]
    pub fn with_provider_override(self, provider: Option<String>) -> Self {
        Self {
            provider_override: provider,
            ..self
        }
    }

    /// Set context level.
    #[must_use]
    pub fn with_context_level(self, level: u8) -> Self {
        Self {
            context_level: level,
            ..self
        }
    }
}

/// Snapshot of environment variables for idempotent recovery.
///
/// Captures environment variables that affect pipeline execution,
/// particularly RALPH_* variables, to ensure the same configuration
/// when resuming.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct EnvironmentSnapshot {
    /// All RALPH_* environment variables at checkpoint time
    #[serde(default)]
    pub ralph_vars: HashMap<String, String>,
    /// Other relevant environment variables
    #[serde(default)]
    pub other_vars: HashMap<String, String>,
}

pub(crate) fn is_sensitive_env_key(key: &str) -> bool {
    let upper = key.to_ascii_uppercase();
    ["TOKEN", "KEY", "SECRET", "PASSWORD"]
        .iter()
        .any(|pattern| upper.contains(pattern))
}

const RELEVANT_OTHER_KEYS: &[&str] = &[
    "EDITOR",
    "VISUAL",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
];

impl EnvironmentSnapshot {
    /// Build an environment snapshot from an arbitrary iterator of key-value pairs.
    ///
    /// Applies the same sensitive-variable filtering and partitioning as `capture_current`.
    /// Use this in tests to build snapshots without touching the process environment.
    #[must_use]
    pub fn from_env_vars(vars: impl IntoIterator<Item = (String, String)>) -> Self {
        let (ralph_vars, other_vars): (HashMap<_, _>, HashMap<_, _>) = vars
            .into_iter()
            .filter(|(key, _)| !is_sensitive_env_key(key))
            .partition(|(key, _)| key.starts_with("RALPH_"));

        let other_vars: HashMap<String, String> = other_vars
            .into_iter()
            .filter(|(key, _)| RELEVANT_OTHER_KEYS.contains(&key.as_str()))
            .collect();

        Self {
            ralph_vars,
            other_vars,
        }
    }

    /// Capture the current environment variables relevant to Ralph.
    #[must_use]
    pub fn capture_current() -> Self {
        crate::checkpoint::env_capture::capture_environment()
    }
}

/// Parameters for creating a new checkpoint.
///
/// Groups all the parameters needed to create a checkpoint, avoiding
/// functions with too many individual parameters.
pub struct CheckpointParams<'a> {
    /// Current pipeline phase
    pub phase: PipelinePhase,
    /// Current developer iteration number
    pub iteration: u32,
    /// Total developer iterations configured
    pub total_iterations: u32,
    /// Current reviewer pass number
    pub reviewer_pass: u32,
    /// Total reviewer passes configured
    pub total_reviewer_passes: u32,
    /// Display name of the developer agent
    pub developer_agent: &'a str,
    /// Display name of the reviewer agent
    pub reviewer_agent: &'a str,
    /// Snapshot of CLI arguments
    pub cli_args: CliArgsSnapshot,
    /// Snapshot of developer agent configuration
    pub developer_agent_config: AgentConfigSnapshot,
    /// Snapshot of reviewer agent configuration
    pub reviewer_agent_config: AgentConfigSnapshot,
    /// Current rebase state
    pub rebase_state: RebaseState,
    /// Git user name for commits (if overridden)
    pub git_user_name: Option<&'a str>,
    /// Git user email for commits (if overridden)
    pub git_user_email: Option<&'a str>,
    /// Unique identifier for this run (UUID v4)
    pub run_id: &'a str,
    /// Parent run ID if this is a resumed session
    pub parent_run_id: Option<&'a str>,
    /// Number of times this session has been resumed
    pub resume_count: u32,
    /// Actual completed developer iterations
    pub actual_developer_runs: u32,
    /// Actual completed reviewer passes
    pub actual_reviewer_runs: u32,
    /// Working directory at checkpoint time (repo root)
    pub working_dir: String,
    /// PROMPT.md checksum captured at checkpoint time
    pub prompt_md_checksum: Option<String>,
    /// Config path stored with checkpoint (if any)
    pub config_path: Option<String>,
    /// Config checksum stored with checkpoint (if any)
    pub config_checksum: Option<String>,
}
