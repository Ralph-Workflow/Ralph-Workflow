// Well-known path constants for Ralph workspace artifacts.
//
// This file is included by workspace.rs via include!().

/// The `.agent` directory where Ralph stores all artifacts.
pub const AGENT_DIR: &str = ".agent";

/// The `.agent/tmp` directory for temporary files.
pub const AGENT_TMP: &str = ".agent/tmp";

// AGENT_LOGS constant removed - use RunLogContext for per-run log directories.

/// Path to the implementation plan file.
pub const PLAN_MD: &str = ".agent/PLAN.md";

/// Path to the issues file from code review.
pub const ISSUES_MD: &str = ".agent/ISSUES.md";

/// Path to the status file.
pub const STATUS_MD: &str = ".agent/STATUS.md";

/// Path to the notes file.
pub const NOTES_MD: &str = ".agent/NOTES.md";

/// Path to the commit message file.
pub const COMMIT_MESSAGE_TXT: &str = ".agent/commit-message.txt";

/// Path to the checkpoint file for resume support.
pub const CHECKPOINT_JSON: &str = ".agent/checkpoint.json";

/// Path to the start commit tracking file.
pub const START_COMMIT: &str = ".agent/start_commit";

/// Path to the review baseline tracking file.
pub const REVIEW_BASELINE_TXT: &str = ".agent/review_baseline.txt";

/// Path to the prompt file in repository root.
pub const PROMPT_MD: &str = "PROMPT.md";

/// Path to the prompt backup file.
pub const PROMPT_BACKUP: &str = ".agent/PROMPT.md.backup";

/// Path to the agent config file.
pub const AGENT_CONFIG_TOML: &str = ".agent/config.toml";

/// Path to the agents registry file.
pub const AGENTS_TOML: &str = ".agent/agents.toml";

// PIPELINE_LOG constant removed - use RunLogContext::pipeline_log() for per-run log paths.
