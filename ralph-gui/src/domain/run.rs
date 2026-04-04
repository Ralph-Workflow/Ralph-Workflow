//! Pure domain helpers for run operations.
//!
//! Policy decisions about run state belong here, not in boundary modules.

use serde_json::Value;
use std::path::Path;

/// Run status derived from checkpoint state.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DomainRunStatus {
    Running,
    Paused,
    Completed,
    NotStarted,
}

/// Phase duration info for the timeline (domain type).
#[derive(Debug, Clone)]
pub struct DomainPhaseDuration {
    pub phase_name: String,
    pub duration_secs: Option<f64>,
    pub status: String,
}

/// Degradation info (domain type).
#[derive(Debug, Clone)]
pub struct DomainDegradedInfo {
    pub retry_count: u32,
    pub fallback_agent: Option<String>,
    pub reason: Option<String>,
}

/// Summary data extracted from a checkpoint file.
#[derive(Debug, Clone)]
pub struct CheckpointData {
    pub run_id: Option<String>,
    pub phase: Option<String>,
    pub timestamp: Option<String>,
    pub developer_agent: Option<String>,
    pub reviewer_agent: Option<String>,
    pub iteration_count: u32,
    pub last_error: Option<String>,
    pub is_degraded: bool,
    pub total_files_changed: u32,
    pub total_tests_passed: Option<u32>,
    pub review_count: u32,
}

/// Parse a checkpoint JSON value into structured domain data.
pub fn parse_checkpoint_data(checkpoint: &serde_json::Value) -> CheckpointData {
    CheckpointData {
        run_id: checkpoint
            .get("run_id")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(String::from),
        phase: checkpoint
            .get("phase")
            .and_then(|v| v.as_str())
            .map(String::from),
        timestamp: checkpoint
            .get("timestamp")
            .and_then(|v| v.as_str())
            .map(String::from),
        developer_agent: checkpoint
            .get("developer_agent")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(String::from),
        reviewer_agent: checkpoint
            .get("reviewer_agent")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(String::from),
        iteration_count: u32::try_from(
            checkpoint
                .get("iteration_count")
                .and_then(serde_json::Value::as_u64)
                .unwrap_or(0),
        )
        .unwrap_or(0),
        last_error: checkpoint
            .get("last_error")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(String::from),
        is_degraded: checkpoint
            .get("is_degraded")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false),
        total_files_changed: u32::try_from(
            checkpoint
                .get("total_files_changed")
                .and_then(serde_json::Value::as_u64)
                .unwrap_or(0),
        )
        .unwrap_or(0),
        total_tests_passed: checkpoint
            .get("total_tests_passed")
            .and_then(serde_json::Value::as_u64)
            .map(|v| u32::try_from(v).unwrap_or(0)),
        review_count: u32::try_from(
            checkpoint
                .get("review_count")
                .and_then(serde_json::Value::as_u64)
                .unwrap_or(0),
        )
        .unwrap_or(0),
    }
}

/// Determine the run status from lock file presence and checkpoint data.
pub fn determine_run_status(
    has_lock_file: bool,
    checkpoint_opt: Option<&CheckpointData>,
) -> DomainRunStatus {
    if has_lock_file {
        return DomainRunStatus::Running;
    }

    match checkpoint_opt {
        None => DomainRunStatus::NotStarted,
        Some(cp) => {
            if cp.phase.as_deref() == Some("Complete") {
                DomainRunStatus::Completed
            } else {
                DomainRunStatus::Paused
            }
        }
    }
}

/// Extract the run_id from a checkpoint JSON value.
pub fn extract_run_id_from_checkpoint(checkpoint: &serde_json::Value) -> Option<String> {
    checkpoint
        .get("run_id")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(String::from)
}

/// Check if a checkpoint represents a completed run.
pub fn is_checkpoint_complete(checkpoint: &serde_json::Value) -> bool {
    checkpoint
        .get("phase")
        .and_then(|v| v.as_str())
        .map(|phase| phase == "Complete")
        .unwrap_or(false)
}

/// Limit log lines to the last N lines.
pub fn limit_log_lines(lines: Vec<String>, max_lines: usize) -> Vec<String> {
    if lines.len() <= max_lines {
        lines
    } else {
        lines[lines.len() - max_lines..].to_vec()
    }
}

#[derive(Debug, Clone)]
pub struct DiffEntry {
    pub path: String,
    pub additions: i32,
    pub deletions: i32,
    pub diff_text: String,
}

pub fn parse_run_changes(diff_output: &str) -> Vec<DiffEntry> {
    diff_output
        .split("diff --git ")
        .skip(1)
        .map(|section| {
            let diff_text = format!("diff --git {section}");
            let path = section
                .lines()
                .find(|line| line.starts_with("+++ b/"))
                .map(|line| line.trim_start_matches("+++ b/").to_string())
                .unwrap_or_default();

            let additions = section
                .lines()
                .filter(|line| line.starts_with('+') && !line.starts_with("+++"))
                .count() as i32;

            let deletions = section
                .lines()
                .filter(|line| line.starts_with('-') && !line.starts_with("---"))
                .count() as i32;

            DiffEntry {
                path,
                additions,
                deletions,
                diff_text,
            }
        })
        .collect()
}

#[derive(Debug, Clone)]
pub struct IterationHistoryEntry {
    pub iteration_number: u32,
    pub status: String,
    pub duration_secs: Option<f64>,
    pub files_changed: u32,
    pub tests_passed: Option<u32>,
    pub tests_total: Option<u32>,
}

pub fn parse_iteration_history(checkpoint: &Value) -> Vec<IterationHistoryEntry> {
    if let Some(arr) = checkpoint.get("iterations").and_then(|v| v.as_array()) {
        arr.iter()
            .enumerate()
            .map(|(idx, item)| {
                let fallback_number = u32::try_from(idx + 1).unwrap_or(u32::MAX);
                let iteration_number = item
                    .get("iteration_number")
                    .and_then(Value::as_u64)
                    .unwrap_or_else(|| fallback_number as u64)
                    .try_into()
                    .unwrap_or(fallback_number);

                let status_str = item
                    .get("status")
                    .and_then(|v| v.as_str())
                    .unwrap_or("Complete");

                let duration_secs = item.get("duration_secs").and_then(Value::as_f64);

                let files_changed = item
                    .get("files_changed")
                    .and_then(Value::as_u64)
                    .unwrap_or(0)
                    .try_into()
                    .unwrap_or(0u32);

                let tests_passed = item
                    .get("tests_passed")
                    .and_then(Value::as_u64)
                    .map(|v| v.try_into().unwrap_or(0u32));

                let tests_total = item
                    .get("tests_total")
                    .and_then(Value::as_u64)
                    .map(|v| v.try_into().unwrap_or(0u32));

                IterationHistoryEntry {
                    iteration_number,
                    status: status_str.to_string(),
                    duration_secs,
                    files_changed,
                    tests_passed,
                    tests_total,
                }
            })
            .collect()
    } else {
        let count: u32 = checkpoint
            .get("iteration_count")
            .and_then(Value::as_u64)
            .unwrap_or(0)
            .try_into()
            .unwrap_or(0u32);

        (1..=count)
            .map(|n| IterationHistoryEntry {
                iteration_number: n,
                status: "Complete".to_string(),
                duration_secs: None,
                files_changed: 0,
                tests_passed: None,
                tests_total: None,
            })
            .collect()
    }
}

#[derive(Debug, Clone)]
pub struct ReviewHistoryEntry {
    pub review_number: u32,
    pub status: String,
    pub duration_secs: Option<f64>,
    pub findings_count: u32,
}

pub fn parse_review_history(checkpoint: &Value) -> Vec<ReviewHistoryEntry> {
    if let Some(arr) = checkpoint.get("reviews").and_then(|v| v.as_array()) {
        arr.iter()
            .enumerate()
            .map(|(idx, item)| {
                let fallback_number = u32::try_from(idx + 1).unwrap_or(u32::MAX);
                let review_number = item
                    .get("review_number")
                    .and_then(Value::as_u64)
                    .unwrap_or_else(|| fallback_number as u64)
                    .try_into()
                    .unwrap_or(fallback_number);

                let status_str = item
                    .get("status")
                    .and_then(|v| v.as_str())
                    .unwrap_or("Complete");

                let duration_secs = item.get("duration_secs").and_then(Value::as_f64);

                let findings_count = item
                    .get("findings_count")
                    .and_then(Value::as_u64)
                    .unwrap_or(0)
                    .try_into()
                    .unwrap_or(0u32);

                ReviewHistoryEntry {
                    review_number,
                    status: status_str.to_string(),
                    duration_secs,
                    findings_count,
                }
            })
            .collect()
    } else {
        Vec::new()
    }
}

pub fn checkpoint_matches_run_id(checkpoint: &Value, run_id: &str) -> bool {
    extract_run_id_from_checkpoint(checkpoint)
        .as_deref()
        .map(|id| id == run_id)
        .unwrap_or(false)
}

/// Determine the base path for a worktree given repo_path and optional worktree_path.
pub fn compute_base_path(repo_path: &Path, worktree_path: Option<&Path>) -> std::path::PathBuf {
    worktree_path
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| repo_path.to_path_buf())
}

/// Compute the agent directory path.
pub fn agent_dir(base_path: &Path) -> std::path::PathBuf {
    base_path.join(".agent")
}

/// Compute the checkpoint file path.
pub fn checkpoint_path(agent_dir: &Path) -> std::path::PathBuf {
    agent_dir.join("checkpoint.json")
}

/// Compute the log file path for a given run_id.
pub fn log_file_path(agent_dir: &Path, run_id: &str) -> std::path::PathBuf {
    agent_dir
        .join(format!("logs-{run_id}"))
        .join("pipeline.log")
}

/// Parse a checkpoint JSON string into domain data.
pub fn parse_checkpoint_json(content: &str) -> Option<CheckpointData> {
    let checkpoint: serde_json::Value = serde_json::from_str(content).ok()?;
    Some(parse_checkpoint_data(&checkpoint))
}

/// Build a description string from the current phase.
pub fn description_from_phase(phase: &str) -> String {
    format!("Phase: {phase}")
}

/// Build a description for an interrupted run.
pub fn interrupted_description(phase: &str) -> String {
    format!("Interrupted at {phase}")
}

/// Parse phase durations from a checkpoint JSON value.
pub fn domain_parse_phase_durations(checkpoint: &serde_json::Value) -> Vec<DomainPhaseDuration> {
    if let Some(arr) = checkpoint.get("phase_history").and_then(|v| v.as_array()) {
        return arr
            .iter()
            .filter_map(|item| {
                let phase_name = item.get("phase_name")?.as_str()?.to_string();
                let duration_secs = item
                    .get("duration_secs")
                    .and_then(serde_json::Value::as_f64);
                let status = item
                    .get("status")
                    .and_then(|v| v.as_str())
                    .unwrap_or("completed")
                    .to_string();
                Some(DomainPhaseDuration {
                    phase_name,
                    duration_secs,
                    status,
                })
            })
            .collect();
    }

    // If no phase_history, synthesize from current_phase
    let current_phase = checkpoint
        .get("phase")
        .and_then(|v| v.as_str())
        .unwrap_or("Unknown");

    let phases = ["Plan", "Develop", "Review", "Commit"];
    let phase_order_lower = ["plan", "develop", "review", "commit"];
    let current_lower = current_phase.to_lowercase();
    let current_idx = phase_order_lower
        .iter()
        .position(|p| current_lower.contains(p));

    phases
        .iter()
        .enumerate()
        .map(|(idx, name)| {
            let status = current_idx.map_or_else(
                || "pending".to_string(),
                |ci| match idx.cmp(&ci) {
                    std::cmp::Ordering::Less => "completed".to_string(),
                    std::cmp::Ordering::Equal => "active".to_string(),
                    std::cmp::Ordering::Greater => "pending".to_string(),
                },
            );
            DomainPhaseDuration {
                phase_name: (*name).to_string(),
                duration_secs: None,
                status,
            }
        })
        .collect()
}

/// Parse degraded info from a checkpoint JSON value.
pub fn domain_parse_degraded_info(checkpoint: &serde_json::Value) -> Option<DomainDegradedInfo> {
    let is_degraded = checkpoint
        .get("is_degraded")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);

    if !is_degraded {
        return None;
    }

    let retry_count = u32::try_from(
        checkpoint
            .get("retry_count")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0),
    )
    .unwrap_or(0);
    let fallback_agent = checkpoint
        .get("fallback_agent")
        .and_then(|v| v.as_str())
        .map(String::from);
    let reason = checkpoint
        .get("degraded_reason")
        .and_then(|v| v.as_str())
        .map(String::from);

    Some(DomainDegradedInfo {
        retry_count,
        fallback_agent,
        reason,
    })
}

/// Check if a checkpoint represents a resumable run (not completed).
pub fn is_resumable_checkpoint(checkpoint: &serde_json::Value) -> bool {
    !is_checkpoint_complete(checkpoint)
}

/// Compute new lines from a file's content given the last position.
///
/// Returns (lines_to_emit, new_position). This is pure domain logic for
/// determining which lines are new since the last read.
pub fn compute_new_lines(content: &str, last_pos: usize) -> (Vec<String>, usize) {
    let all_lines: Vec<&str> = content.lines().collect();
    if last_pos < all_lines.len() {
        let new_lines: Vec<String> = all_lines[last_pos..]
            .iter()
            .map(|s| s.to_string())
            .collect();
        (new_lines, all_lines.len())
    } else {
        (Vec::new(), last_pos)
    }
}

/// Run status domain type used for building RunDetail.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RunDetailStatus {
    Paused,
    Completed,
}

impl RunDetailStatus {
    pub fn from_phase(phase: &str) -> Self {
        if phase == "Complete" {
            RunDetailStatus::Completed
        } else {
            RunDetailStatus::Paused
        }
    }
}

// =============================================================================
// Boundary types previously in commands/run_management/types.rs
// (These are Specta/Serialize types for the GUI boundary layer)
// =============================================================================

use serde::{Deserialize, Serialize};
use specta::Type;

/// Phase duration info for the timeline.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct PhaseDuration {
    pub phase_name: String,
    pub duration_secs: Option<f64>,
    pub status: String,
}

/// Detailed degradation info.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct DegradedInfo {
    pub retry_count: u32,
    pub fallback_agent: Option<String>,
    pub reason: Option<String>,
}

/// Current status of a Ralph run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Type)]
#[serde(rename_all = "PascalCase")]
pub enum RunStatus {
    Running,
    Paused,
    Completed,
    Failed,
    NotStarted,
}

/// Detailed information about a Ralph run.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct RunDetail {
    pub run_id: String,
    pub status: RunStatus,
    pub current_phase: String,
    pub last_checkpoint: Option<String>,
    pub agent_profile: String,
    pub repo_path: String,
    pub worktree_path: Option<String>,
    pub created_at: String,
    pub description: String,
    /// Number of developer iterations completed in the current run.
    /// Defaults to 0 for checkpoints that pre-date this field.
    #[serde(default)]
    pub iteration_count: u32,
    /// Last error message recorded in the checkpoint, if any.
    /// Defaults to None for checkpoints that pre-date this field.
    #[serde(default)]
    pub last_error: Option<String>,
    /// True when the run is operating with degraded conditions (retries exceeded,
    /// fallback agents used, etc.). Defaults to false for older checkpoints.
    #[serde(default)]
    pub is_degraded: bool,
    /// Per-phase duration info for the timeline visualization.
    /// Defaults to empty vec for checkpoints that pre-date this field.
    #[serde(default)]
    pub phase_durations: Vec<PhaseDuration>,
    /// Detailed degradation info when `is_degraded` is true.
    /// Defaults to None for older checkpoints.
    #[serde(default)]
    pub degraded_info: Option<DegradedInfo>,
    /// Total run duration in seconds from `created_at` to last checkpoint or now.
    /// Defaults to None for older checkpoints.
    #[serde(default)]
    pub total_duration_secs: Option<f64>,
    /// Total files changed across all iterations.
    /// Defaults to 0 for older checkpoints.
    #[serde(default)]
    pub total_files_changed: u32,
    /// Total tests passed from the most recent iteration with test data.
    /// Defaults to None for older checkpoints.
    #[serde(default)]
    pub total_tests_passed: Option<u32>,
    /// Number of completed reviews.
    /// Defaults to 0 for older checkpoints.
    #[serde(default)]
    pub review_count: u32,
}

/// Summary of run status for a repository/worktree context.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct RunStatusSummary {
    pub status: RunStatus,
    pub run_id: Option<String>,
    pub current_phase: Option<String>,
    pub last_checkpoint: Option<String>,
}
