//! Cloud types for progress reporting.

use serde::{Deserialize, Serialize};

/// Progress update payload for cloud API.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProgressUpdate {
    /// ISO 8601 timestamp
    pub timestamp: String,
    /// Current pipeline phase
    pub phase: String,
    /// Previous phase (for transition tracking)
    pub previous_phase: Option<String>,
    /// Development iteration (1-based)
    pub iteration: Option<u32>,
    /// Total iterations configured
    pub total_iterations: Option<u32>,
    /// Review pass (1-based)
    pub review_pass: Option<u32>,
    /// Total review passes configured
    pub total_review_passes: Option<u32>,
    /// Human-readable status message
    pub message: String,
    /// Structured event type for programmatic consumption
    pub event_type: ProgressEventType,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ProgressEventType {
    PipelineStarted,
    PhaseTransition {
        from: Option<String>,
        to: String,
    },
    IterationStarted {
        iteration: u32,
    },
    IterationProgress {
        current: u32,
        total: u32,
    },
    IterationCompleted {
        iteration: u32,
        status: String,
    },
    ReviewPassStarted {
        pass: u32,
    },
    ReviewProgress {
        pass: u32,
        total: u32,
    },
    ReviewPassCompleted {
        pass: u32,
        issues_found: bool,
    },
    AgentInvoked {
        role: String,
        agent: String,
    },
    AgentCompleted {
        role: String,
        duration_ms: u64,
    },
    CheckpointSaved,
    CommitCreated {
        sha: String,
    },
    PushCompleted {
        remote: String,
        branch: String,
    },
    PushFailed {
        remote: String,
        branch: String,
        error: String,
    },
    PullRequestCreated {
        url: String,
        number: u32,
    },
    PullRequestFailed {
        error: String,
    },
    PipelineCompleted {
        success: bool,
    },
    PipelineInterrupted {
        reason: String,
    },
    Heartbeat,
}

/// Pipeline result payload for completion reporting.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PipelineResult {
    /// Whether the pipeline completed successfully
    pub success: bool,
    /// Final commit SHA (if any)
    pub commit_sha: Option<String>,
    /// Pull request URL (if created)
    pub pr_url: Option<String>,

    /// Number of successful push operations (cloud mode only).
    #[serde(default)]
    pub push_count: u32,

    /// SHA of the last successfully pushed commit (if any).
    #[serde(default)]
    pub last_pushed_commit: Option<String>,

    /// Commits that could not be pushed after retries.
    #[serde(default)]
    pub unpushed_commits: Vec<String>,

    /// Last push error message (if any).
    #[serde(default)]
    pub last_push_error: Option<String>,
    /// Number of iterations used
    pub iterations_used: u32,
    /// Number of review passes used
    pub review_passes_used: u32,
    /// Whether issues were found
    pub issues_found: bool,
    /// Pipeline duration in seconds
    pub duration_secs: u64,
    /// Error message if pipeline failed
    pub error_message: Option<String>,
}

#[derive(Debug, thiserror::Error)]
pub enum CloudError {
    #[error("Configuration error: {0}")]
    Configuration(String),

    #[error("HTTP error {0}: {1}")]
    HttpError(u16, String),

    #[error("Network error: {0}")]
    NetworkError(String),

    #[error("Serialization error: {0}")]
    Serialization(String),
}

impl From<std::io::Error> for CloudError {
    fn from(e: std::io::Error) -> Self {
        CloudError::NetworkError(e.to_string())
    }
}

impl CloudError {
    pub fn is_success(&self) -> bool {
        matches!(self, CloudError::Configuration(_))
    }
}

pub fn interpret_http_response(status: u16, body: String) -> Result<(), CloudError> {
    if (200..300).contains(&status) {
        Ok(())
    } else {
        Err(CloudError::HttpError(status, body))
    }
}

pub const fn heartbeat_drop_join_timeout() -> std::time::Duration {
    std::time::Duration::from_millis(50)
}

pub fn heartbeat_should_join_thread(done_received: bool) -> bool {
    done_received
}
