use super::run_id::RunId;
use crate::workspace::Workspace;
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

/// Context for managing per-run log directories and files.
///
/// This struct owns the `run_id` and provides path resolution for all logs
/// from a single Ralph invocation. All logs are grouped under a per-run
/// directory (`.agent/logs-<run_id>/`) for easy sharing and diagnosis.
///
/// ## Design Rationale
///
/// **Why per-run directories?**
/// - **Shareability**: All logs from a run can be shared as a single tarball
/// - **Resume continuity**: `--resume` continues logging to the same directory
/// - **Isolation**: Multiple concurrent runs don't interfere with each other
/// - **Organization**: Chronological sorting is natural (lexicographic sort)
///
/// **Why not scatter logs across `.agent/logs/`, `.agent/tmp/`, etc?**
/// - Hard to identify which logs belong to which run
/// - Difficult to share logs for debugging
/// - Resume would create fragmented log artifacts
/// - Log rotation and cleanup become complex
///
/// ## Integration with Checkpoint/Resume
///
/// The `run_id` is stored in the checkpoint (`.agent/checkpoint.json`) so that
/// `--resume` can continue logging to the same directory. This ensures:
/// - Logs from the original run and resumed run are in one place
/// - Event loop sequence numbers continue from where they left off
/// - Pipeline log is appended (not overwritten)
///
/// ## Architecture Compliance
///
/// This struct is created once per run in the **impure layer** (effect handlers)
/// and passed to all effect handlers via `PhaseContext`. It must never be used
/// in reducers or orchestrators (which are pure).
///
/// All filesystem operations go through the `Workspace` trait (never `std::fs`
/// in pipeline code) to support both `WorkspaceFs` (production) and
/// `MemoryWorkspace` (tests).
///
/// ## Future Extensibility
///
/// The per-run directory structure includes reserved subdirectories for future use:
/// - `provider/`: Provider streaming logs (infrastructure exists, not yet used)
/// - `debug/`: Future debug artifacts (e.g., memory dumps, profiling data)
///
/// ## Examples
///
/// ### Fresh run
/// ```no_run
/// use ralph_workflow::logging::RunLogContext;
/// use ralph_workflow::workspace::WorkspaceFs;
/// use std::path::PathBuf;
///
/// let workspace = WorkspaceFs::new(PathBuf::from("."));
/// let ctx = RunLogContext::new(&workspace)?;
///
/// // Get log paths
/// let pipeline_log = ctx.pipeline_log();  // .agent/logs-2026-02-06_14-03-27.123Z/pipeline.log
/// let agent_log = ctx.agent_log("planning", 1, None);  // .agent/logs-.../agents/planning_1.log
/// # Ok::<(), anyhow::Error>(())
/// ```
///
/// ### Resume
/// ```no_run
/// use ralph_workflow::logging::RunLogContext;
/// use ralph_workflow::workspace::WorkspaceFs;
/// use std::path::PathBuf;
///
/// let workspace = WorkspaceFs::new(PathBuf::from("."));
/// let run_id = "2026-02-06_14-03-27.123Z";  // From checkpoint
/// let ctx = RunLogContext::from_checkpoint(run_id, &workspace)?;
///
/// // Logs will append to existing files in the same run directory
/// let pipeline_log = ctx.pipeline_log();
/// # Ok::<(), anyhow::Error>(())
/// ```
pub struct RunLogContext {
    run_id: RunId,
    run_dir: PathBuf,
}

impl RunLogContext {
    /// Create a new `RunLogContext` with collision handling.
    ///
    /// Generates a new `run_id` and creates the run directory structure.
    /// If directory exists, tries collision counter variants (rare case
    /// of multiple runs starting in the same millisecond).
    ///
    /// Creates subdirectories:
    /// - `.agent/logs-<run_id>/agents/` for per-agent logs
    /// - `.agent/logs-<run_id>/provider/` for provider streaming logs
    /// - `.agent/logs-<run_id>/debug/` for future debug artifacts
    ///
    /// # Collision Handling
    ///
    /// The collision handling loop tries counter values 0-99:
    /// - Counter 0: Uses the base `run_id` (no suffix)
    /// - Counter 1-99: Appends `-01` through `-99` suffixes
    ///
    /// # TOCTOU Race Condition Handling
    ///
    /// To avoid the time-of-check-to-time-of-use race condition, we:
    /// 1. First check if the directory exists (fast path for common case)
    /// 2. If it doesn't exist, try to create it
    /// 3. If creation succeeds but the directory still doesn't exist afterward,
    ///    another process may have created it, so we try the next collision variant
    /// 4. We use the presence of the "agents" subdirectory as our "created" marker
    ///
    /// Note: If a base directory exists that was actually created as a collision
    /// directory (e.g., due to a bug), the system will still work correctly by
    /// creating the next collision variant. This is acceptable because the directory
    /// naming format is deterministic and we always check for existence before creating.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn new(workspace: &dyn Workspace) -> Result<Self> {
        let base_run_id = RunId::new();

        let (run_id, run_dir) = crate::logging::collision::create_run_dir_with_collision_handling(
            workspace,
            &base_run_id,
        )?;

        Ok(Self { run_id, run_dir })
    }

    /// Create a `RunLogContext` from an existing checkpoint (for resume).
    ///
    /// Uses the timestamp-based log run ID from the checkpoint (stored in
    /// `PipelineCheckpoint.log_run_id`) to continue logging to the same run
    /// directory. This is distinct from the UUID-based `run_id` field in the
    /// checkpoint which identifies the execution session.
    ///
    /// If the directory doesn't exist (e.g., deleted), it is recreated.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn from_checkpoint(run_id: &str, workspace: &dyn Workspace) -> Result<Self> {
        let run_id = RunId::from_checkpoint(run_id);
        let run_dir = PathBuf::from(format!(".agent/logs-{run_id}"));

        // Ensure directory exists (may have been deleted)
        if !workspace.exists(&run_dir) {
            workspace
                .create_dir_all(&run_dir)
                .context("Failed to recreate run log directory for resume")?;

            workspace
                .create_dir_all(&run_dir.join("agents"))
                .context("Failed to recreate agents log subdirectory for resume")?;

            workspace
                .create_dir_all(&run_dir.join("provider"))
                .context("Failed to recreate provider log subdirectory for resume")?;

            workspace
                .create_dir_all(&run_dir.join("debug"))
                .context("Failed to recreate debug log subdirectory for resume")?;
        }

        Ok(Self { run_id, run_dir })
    }

    /// Test-only helper to create a `RunLogContext` with a fixed `run_id`.
    ///
    /// This allows testing the collision handling logic by providing a predictable
    /// `run_id` that can be pre-created on the filesystem to simulate collisions.
    ///
    /// # Warning
    ///
    /// This is intended for testing only. Using a fixed `run_id` in production
    /// could lead to directory collisions. Always use [`RunLogContext::new`]
    /// or [`RunLogContext::from_checkpoint`] in production code.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// use ralph_workflow::logging::{RunId, RunLogContext};
    ///
    /// // Create a fixed run_id for testing
    /// let fixed_id = RunId::for_test("2026-02-06_14-03-27.123Z");
    /// let ctx = RunLogContext::for_testing(&fixed_id, &workspace)?;
    /// ```
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn for_testing(base_run_id: &RunId, workspace: &dyn Workspace) -> Result<Self> {
        let (run_id, run_dir) = crate::logging::collision::create_run_dir_with_collision_handling(
            workspace,
            base_run_id,
        )?;

        Ok(Self { run_id, run_dir })
    }

    /// Get a reference to the run ID.
    ///
    /// This is the timestamp-based log run ID (format: `YYYY-MM-DD_HH-mm-ss.SSSZ[-NN]`)
    /// used for naming the per-run log directory. It is distinct from the UUID-based
    /// `run_id` field stored in `PipelineCheckpoint`, which uniquely identifies the
    /// execution session.
    #[must_use]
    pub const fn run_id(&self) -> &RunId {
        &self.run_id
    }

    /// Get the run directory path (relative to workspace root).
    #[must_use]
    pub fn run_dir(&self) -> &Path {
        &self.run_dir
    }

    /// Get the path to the pipeline log file.
    #[must_use]
    pub fn pipeline_log(&self) -> PathBuf {
        self.run_dir.join("pipeline.log")
    }

    /// Get the path to the event loop log file.
    #[must_use]
    pub fn event_loop_log(&self) -> PathBuf {
        self.run_dir.join("event_loop.log")
    }

    /// Get the path to the event loop trace file (crash-only).
    #[must_use]
    pub fn event_loop_trace(&self) -> PathBuf {
        self.run_dir.join("event_loop_trace.jsonl")
    }

    /// Get the path to an agent log file.
    ///
    /// # Arguments
    /// * `phase` - Phase name (e.g., "planning", "dev", "reviewer", "commit")
    /// * `index` - Invocation index within the phase (1-based)
    /// * `attempt` - Optional retry attempt counter (1 for first retry, 2 for second retry, etc.; None for initial attempt with no retries)
    ///
    /// # Returns
    /// Path like `.agent/logs-<run_id>/agents/planning_1.log` or
    /// `.agent/logs-<run_id>/agents/dev_2_a1.log` for retries.
    #[must_use]
    pub fn agent_log(&self, phase: &str, index: u32, attempt: Option<u32>) -> PathBuf {
        let filename = attempt.map_or_else(
            || format!("{phase}_{index}.log"),
            |a| format!("{phase}_{index}_a{a}.log"),
        );
        self.run_dir.join("agents").join(filename)
    }

    /// Get the path to a provider streaming log file.
    ///
    /// # Arguments
    /// * `name` - Provider log filename (e.g., "claude-stream_dev_1.jsonl")
    ///
    /// # Returns
    /// Path like `.agent/logs-<run_id>/provider/claude-stream_dev_1.jsonl`.
    #[must_use]
    pub fn provider_log(&self, name: &str) -> PathBuf {
        self.run_dir.join("provider").join(name)
    }

    /// Get the path to the run metadata file (run.json).
    #[must_use]
    pub fn run_metadata(&self) -> PathBuf {
        self.run_dir.join("run.json")
    }

    /// Write run.json metadata file.
    ///
    /// This should be called early in pipeline execution to record
    /// essential metadata for debugging and tooling.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn write_run_metadata(
        &self,
        workspace: &dyn Workspace,
        metadata: &RunMetadata,
    ) -> Result<()> {
        let path = self.run_metadata();
        let json = serde_json::to_string_pretty(metadata).with_context(|| {
            format!(
                "Failed to serialize run metadata for run_id '{}'. \
                 This usually means a field contains data that cannot be represented as JSON.",
                self.run_id
            )
        })?;
        workspace.write(&path, &json).with_context(|| {
            format!(
                "Failed to write run.json to '{}'. Check filesystem permissions and disk space.",
                path.display()
            )
        })
    }
}

/// Metadata recorded in run.json for each pipeline run.
///
/// This file is written at the start of each run to provide context
/// for debugging and tooling. It anchors the run with essential info
/// like command invocation, timestamps, and environment details.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunMetadata {
    /// Timestamp-based run identifier (matches directory name)
    ///
    /// Format: `YYYY-MM-DD_HH-mm-ss.SSSZ[-NN]` (e.g., `2026-02-06_14-03-27.123Z`)
    ///
    /// This is the log run ID used for the per-run log directory and is distinct
    /// from the UUID-based `run_id` field in `PipelineCheckpoint` which uniquely
    /// identifies the execution session.
    pub run_id: String,

    /// Timestamp when run started (UTC, RFC3339)
    pub started_at_utc: String,

    /// Command as invoked by user (e.g., "ralph" or "ralph --resume")
    pub command: String,

    /// Whether this is a resumed session
    pub resume: bool,

    /// Absolute path to repository root
    pub repo_root: String,

    /// Ralph version (from Cargo.toml)
    pub ralph_version: String,

    /// Process ID (if available)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pid: Option<u32>,

    /// Configuration summary (non-secret metadata)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub config_summary: Option<ConfigSummary>,
}

/// Non-secret configuration summary for run.json.
///
/// Captures high-level config info useful for debugging without
/// exposing any sensitive data (API keys, tokens, etc.).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConfigSummary {
    /// Developer agent name (if configured)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub developer_agent: Option<String>,

    /// Reviewer agent name (if configured)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reviewer_agent: Option<String>,

    /// Total iterations configured
    #[serde(skip_serializing_if = "Option::is_none")]
    pub total_iterations: Option<u32>,

    /// Total reviewer passes configured
    #[serde(skip_serializing_if = "Option::is_none")]
    pub total_reviewer_passes: Option<u32>,
}

#[cfg(test)]
mod tests;
