use crate::state::SharedState;
use serde::{Deserialize, Serialize};
use specta::Type;
use std::collections::HashMap;
use std::path::Path;
use std::sync::{Arc, Mutex};
use tauri::AppHandle;
use tauri_plugin_notification::NotificationExt;

/// Status of a single developer iteration.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Type)]
#[serde(rename_all = "PascalCase")]
pub enum IterationStatus {
    Complete,
    Running,
    Failed,
}

/// Summary of a single developer iteration.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct IterationSummary {
    pub iteration_number: u32,
    pub status: IterationStatus,
    pub duration_secs: Option<f64>,
    pub files_changed: u32,
    pub tests_passed: Option<u32>,
    pub tests_total: Option<u32>,
}

/// Status of a single review cycle.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Type)]
#[serde(rename_all = "PascalCase")]
pub enum ReviewStatus {
    Complete,
    Running,
    Failed,
}

/// Summary of a single review cycle.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct ReviewSummary {
    pub review_number: u32,
    pub status: ReviewStatus,
    pub duration_secs: Option<f64>,
    pub findings_count: u32,
}

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

/// Get the run status for a repository (and optional worktree).
///
/// Checks for active process lock file and checkpoint data.
///
/// # Errors
///
/// Returns an error if the path cannot be read.
#[tauri::command]
#[specta::specta]
pub fn get_run_status(
    repo_path: String,
    worktree_path: Option<String>,
) -> Result<RunStatusSummary, String> {
    let base_path_buf = worktree_path.map_or_else(
        || std::path::PathBuf::from(repo_path),
        std::path::PathBuf::from,
    );
    let agent_dir = base_path_buf.join(".agent");

    // Check for active run lock
    let lock_file = agent_dir.join("tmp").join("run.lock");
    if lock_file.exists() {
        let checkpoint = load_checkpoint_summary(&agent_dir);
        return Ok(RunStatusSummary {
            status: RunStatus::Running,
            run_id: checkpoint.as_ref().map(|c| c.run_id.clone()),
            current_phase: checkpoint.as_ref().map(|c| c.current_phase.clone()),
            last_checkpoint: checkpoint.as_ref().and_then(|c| c.last_checkpoint.clone()),
        });
    }

    // Check for checkpoint
    let checkpoint_file = agent_dir.join("checkpoint.json");
    if !checkpoint_file.exists() {
        return Ok(RunStatusSummary {
            status: RunStatus::NotStarted,
            run_id: None,
            current_phase: None,
            last_checkpoint: None,
        });
    }

    let checkpoint = load_checkpoint_summary(&agent_dir);
    let status = checkpoint.as_ref().map_or(RunStatus::Paused, |c| {
        if c.current_phase == "Complete" {
            RunStatus::Completed
        } else {
            RunStatus::Paused
        }
    });

    Ok(RunStatusSummary {
        status,
        run_id: checkpoint.as_ref().map(|c| c.run_id.clone()),
        current_phase: checkpoint.as_ref().map(|c| c.current_phase.clone()),
        last_checkpoint: checkpoint.as_ref().and_then(|c| c.last_checkpoint.clone()),
    })
}

/// Internal helper: collect all resumable (paused/interrupted) runs from a list of paths.
///
/// Each path is checked for `.agent/checkpoint.json`. Completed runs are excluded.
#[must_use]
pub fn collect_resumable_runs(paths: &[std::path::PathBuf]) -> Vec<RunDetail> {
    let mut results = Vec::new();

    for repo_path in paths {
        let agent_dir = repo_path.join(".agent");
        if !agent_dir.exists() {
            continue;
        }
        let checkpoint_file = agent_dir.join("checkpoint.json");
        if !checkpoint_file.exists() {
            continue;
        }
        let Ok(content) = std::fs::read_to_string(&checkpoint_file) else {
            continue;
        };
        let Ok(checkpoint) = serde_json::from_str::<serde_json::Value>(&content) else {
            continue;
        };

        let phase = checkpoint
            .get("phase")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown")
            .to_string();

        if phase == "Complete" {
            continue;
        }

        let run_id = checkpoint
            .get("run_id")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string();

        let timestamp = checkpoint
            .get("timestamp")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let developer_agent = checkpoint
            .get("developer_agent")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let reviewer_agent = checkpoint
            .get("reviewer_agent")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let iteration_count = checkpoint
            .get("iteration_count")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0)
            .try_into()
            .unwrap_or(0u32);

        let last_error = checkpoint
            .get("last_error")
            .and_then(|v| v.as_str())
            .map(String::from);

        let is_degraded = checkpoint
            .get("is_degraded")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false);

        let phase_durations = parse_phase_durations_from_checkpoint(&checkpoint);
        let degraded_info = parse_degraded_info_from_checkpoint(&checkpoint);
        let total_files_changed = checkpoint
            .get("total_files_changed")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0)
            .try_into()
            .unwrap_or(0u32);
        let total_tests_passed = checkpoint
            .get("total_tests_passed")
            .and_then(serde_json::Value::as_u64)
            .map(|v| v.try_into().unwrap_or(0u32));
        let review_count = checkpoint
            .get("review_count")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0)
            .try_into()
            .unwrap_or(0u32);

        results.push(RunDetail {
            run_id,
            status: RunStatus::Paused,
            current_phase: phase.clone(),
            last_checkpoint: Some(timestamp.clone()),
            agent_profile: format!("{developer_agent}/{reviewer_agent}"),
            repo_path: repo_path.to_string_lossy().into_owned(),
            worktree_path: None,
            created_at: timestamp,
            description: format!("Interrupted at {phase}"),
            iteration_count,
            last_error,
            is_degraded,
            phase_durations,
            degraded_info,
            total_duration_secs: None,
            total_files_changed,
            total_tests_passed,
            review_count,
        });
    }

    results
}

/// Get all resumable runs across the primary repository and all known worktrees.
///
/// A run is resumable if it has a checkpoint in an interrupted/paused state.
///
/// # Errors
///
/// Returns an error if the app state lock cannot be acquired.
#[tauri::command]
#[specta::specta]
pub fn get_resumable_runs(
    repo_path: String,
    state: tauri::State<'_, crate::state::SharedState>,
) -> Result<Vec<RunDetail>, String> {
    get_resumable_runs_impl(state.inner(), &repo_path)
}

pub fn get_resumable_runs_impl(
    state: &SharedState,
    repo_path: &str,
) -> Result<Vec<RunDetail>, String> {
    let mut paths = {
        let locked = state
            .lock()
            .map_err(|e| format!("Failed to acquire state lock: {e}"))?;
        locked.known_repos.clone()
    };

    let primary = std::path::PathBuf::from(repo_path);
    if !paths.contains(&primary) {
        paths.push(primary);
    }

    Ok(collect_resumable_runs(&paths))
}

/// Internal: find a run in a set of known repos by scanning checkpoints.
fn find_run_in_repos(run_id: &str, repos: &[std::path::PathBuf]) -> Option<RunDetail> {
    for repo_path in repos {
        let checkpoint_file = repo_path.join(".agent").join("checkpoint.json");
        if !checkpoint_file.exists() {
            continue;
        }
        let Ok(content) = std::fs::read_to_string(&checkpoint_file) else {
            continue;
        };
        let Ok(checkpoint) = serde_json::from_str::<serde_json::Value>(&content) else {
            continue;
        };
        let checkpoint_run_id = checkpoint
            .get("run_id")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if checkpoint_run_id != run_id {
            continue;
        }
        let phase = checkpoint
            .get("phase")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown")
            .to_string();
        let timestamp = checkpoint
            .get("timestamp")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let developer_agent = checkpoint
            .get("developer_agent")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let reviewer_agent = checkpoint
            .get("reviewer_agent")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let status = if phase == "Complete" {
            RunStatus::Completed
        } else {
            RunStatus::Paused
        };

        let iteration_count = checkpoint
            .get("iteration_count")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0)
            .try_into()
            .unwrap_or(0u32);

        let last_error = checkpoint
            .get("last_error")
            .and_then(|v| v.as_str())
            .map(String::from);

        let is_degraded = checkpoint
            .get("is_degraded")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false);

        let phase_durations = parse_phase_durations_from_checkpoint(&checkpoint);
        let degraded_info = parse_degraded_info_from_checkpoint(&checkpoint);
        let total_files_changed = checkpoint
            .get("total_files_changed")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0)
            .try_into()
            .unwrap_or(0u32);
        let total_tests_passed = checkpoint
            .get("total_tests_passed")
            .and_then(serde_json::Value::as_u64)
            .map(|v| v.try_into().unwrap_or(0u32));
        let review_count = checkpoint
            .get("review_count")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0)
            .try_into()
            .unwrap_or(0u32);

        return Some(RunDetail {
            run_id: run_id.to_string(),
            status,
            current_phase: phase.clone(),
            last_checkpoint: Some(timestamp.clone()),
            agent_profile: format!("{developer_agent}/{reviewer_agent}"),
            repo_path: repo_path.to_string_lossy().into_owned(),
            worktree_path: None,
            created_at: timestamp,
            description: format!("Phase: {phase}"),
            iteration_count,
            last_error,
            is_degraded,
            phase_durations,
            degraded_info,
            total_duration_secs: None,
            total_files_changed,
            total_tests_passed,
            review_count,
        });
    }
    None
}

/// Parse phase durations from a checkpoint JSON value.
fn parse_phase_durations_from_checkpoint(checkpoint: &serde_json::Value) -> Vec<PhaseDuration> {
    if let Some(arr) = checkpoint.get("phase_history").and_then(|v| v.as_array()) {
        return arr
            .iter()
            .filter_map(|item| {
                let phase_name = item
                    .get("phase_name")
                    .and_then(|v| v.as_str())
                    .map(String::from)?;
                let duration_secs = item
                    .get("duration_secs")
                    .and_then(serde_json::Value::as_f64);
                let status = item
                    .get("status")
                    .and_then(|v| v.as_str())
                    .unwrap_or("completed")
                    .to_string();
                Some(PhaseDuration {
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
            PhaseDuration {
                phase_name: (*name).to_string(),
                duration_secs: None,
                status,
            }
        })
        .collect()
}

/// Parse degraded info from a checkpoint JSON value.
fn parse_degraded_info_from_checkpoint(checkpoint: &serde_json::Value) -> Option<DegradedInfo> {
    let is_degraded = checkpoint
        .get("is_degraded")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);

    if !is_degraded {
        return None;
    }

    let retry_count = checkpoint
        .get("retry_count")
        .and_then(serde_json::Value::as_u64)
        .unwrap_or(0)
        .try_into()
        .unwrap_or(0u32);
    let fallback_agent = checkpoint
        .get("fallback_agent")
        .and_then(|v| v.as_str())
        .map(String::from);
    let reason = checkpoint
        .get("degraded_reason")
        .and_then(|v| v.as_str())
        .map(String::from);

    Some(DegradedInfo {
        retry_count,
        fallback_agent,
        reason,
    })
}

/// Get detailed information for a specific run by scanning all known repo paths.
///
/// # Errors
///
/// Returns an error if the run is not found in any known repository.
#[tauri::command]
#[specta::specta]
pub fn get_run_detail(
    run_id: String,
    state: tauri::State<'_, crate::state::SharedState>,
) -> Result<RunDetail, String> {
    get_run_detail_impl(state.inner(), &run_id)
}

pub fn get_run_detail_impl(state: &SharedState, run_id: &str) -> Result<RunDetail, String> {
    let known_repos = {
        let locked = state
            .lock()
            .map_err(|e| format!("Failed to acquire state lock: {e}"))?;
        locked.known_repos.clone()
    };
    find_run_in_repos(run_id, &known_repos).ok_or_else(|| format!("Run not found: {run_id}"))
}

/// Determine the notification title and optional body for a given run status.
///
/// Returns `(title, body)` where `body` is `None` for passive (routine) status
/// updates and `Some(...)` for interruptive (actionable) alerts.
///
/// This function is extracted for testability — the actual notification sending
/// is done by `notify_run_status_change` using `AppHandle`.
#[must_use]
pub fn notification_params_for_status(
    status: &str,
    run_id: &str,
    context: &str,
) -> (String, Option<String>) {
    match status {
        "Failed" => (
            "Ralph Run Failed".to_string(),
            Some(format!(
                "Run {run_id} failed in {context}. Resume or check logs."
            )),
        ),
        "Paused" => (
            "Ralph Run Paused".to_string(),
            Some(format!(
                "Run {run_id} in {context} was paused or interrupted."
            )),
        ),
        "Completed" => (
            "Ralph Run Completed".to_string(),
            Some(format!("Run {run_id} completed successfully in {context}.")),
        ),
        other => (format!("Ralph: {other}"), None),
    }
}

/// Send a desktop notification for a run status change.
///
/// Notification tiers:
/// - **Passive** (title only): Running and other routine transitions.
/// - **Interruptive** (title + body): Paused, Failed, and Completed transitions.
///
/// # Errors
///
/// Returns an error if the notification plugin is unavailable or the OS rejects the request.
/// The frontend should handle this gracefully and not surface notification errors to users.
#[tauri::command]
#[specta::specta]
pub fn notify_run_status_change(
    app: tauri::AppHandle,
    status: String,
    run_id: String,
    context: String,
) -> Result<(), String> {
    let (title, body) = notification_params_for_status(&status, &run_id, &context);

    let mut builder = app.notification().builder().title(&title);
    if let Some(ref body_text) = body {
        builder = builder.body(body_text.as_str());
    }

    builder
        .show()
        .map_err(|e| format!("Failed to send notification: {e}"))
}

/// Read the last N lines of the Ralph pipeline log for a given repo/worktree context.
///
/// Looks up the current `run_id` from the checkpoint, then reads
/// `.agent/logs-<run_id>/pipeline.log`.
///
/// Returns an empty Vec when no checkpoint or log file exists (non-error: the log
/// may not have been created yet for short or not-yet-started runs).
///
/// # Errors
///
/// Returns an error if the log file exists but cannot be read (permissions, IO error).
#[tauri::command]
#[specta::specta]
pub fn get_run_logs(
    repo_path: String,
    worktree_path: Option<String>,
    max_lines: Option<usize>,
) -> Result<Vec<String>, String> {
    let base = worktree_path.map_or_else(
        || std::path::PathBuf::from(&repo_path),
        std::path::PathBuf::from,
    );
    let agent_dir = base.join(".agent");

    let checkpoint_file = agent_dir.join("checkpoint.json");
    if !checkpoint_file.exists() {
        return Ok(Vec::new());
    }

    let content = std::fs::read_to_string(&checkpoint_file)
        .map_err(|e| format!("Failed to read checkpoint: {e}"))?;
    let checkpoint: serde_json::Value =
        serde_json::from_str(&content).map_err(|e| format!("Failed to parse checkpoint: {e}"))?;

    let run_id = checkpoint
        .get("run_id")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    if run_id.is_empty() {
        return Ok(Vec::new());
    }

    let log_file = agent_dir
        .join(format!("logs-{run_id}"))
        .join("pipeline.log");

    if !log_file.exists() {
        return Ok(Vec::new());
    }

    let log_content =
        std::fs::read_to_string(&log_file).map_err(|e| format!("Failed to read log file: {e}"))?;

    let limit = max_lines.unwrap_or(500);
    let all: Vec<String> = log_content.lines().map(String::from).collect();
    let len = all.len();
    let lines = if len > limit {
        all[len - limit..].to_vec()
    } else {
        all
    };

    Ok(lines)
}

/// A single log line emitted by a running Ralph session.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct RunLogLine {
    pub run_id: String,
    pub line: String,
    pub sequence: u64,
}

/// A file diff entry for a run.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct FileDiff {
    pub path: String,
    pub additions: i32,
    pub deletions: i32,
    pub diff_text: String,
}

/// All changed files for a run or a specific iteration.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct RunChanges {
    pub files: Vec<FileDiff>,
    pub total_additions: i32,
    pub total_deletions: i32,
    pub iteration: Option<u32>,
}

/// Global map of active log subscription cancel handles, keyed by `run_id`.
static LOG_SUBSCRIPTIONS: std::sync::LazyLock<
    Mutex<HashMap<String, Arc<std::sync::atomic::AtomicBool>>>,
> = std::sync::LazyLock::new(|| Mutex::new(HashMap::new()));

/// Subscribe to real-time log streaming for a given run.
///
/// Starts a background task that tails the run's log file and emits Tauri events
/// with payload `RunLogLine` to the event channel named `run-log-{run_id}`.
///
/// # Errors
///
/// Returns an error if the log subscription cannot be started.
#[tauri::command]
#[specta::specta]
pub fn subscribe_run_logs(
    app: AppHandle,
    run_id: String,
    repo_path: String,
    worktree_path: Option<String>,
) -> Result<(), String> {
    use std::sync::atomic::{AtomicBool, Ordering};

    let cancelled = Arc::new(AtomicBool::new(false));
    let cancelled_clone = Arc::clone(&cancelled);

    // Store the cancel handle
    {
        let mut subs = LOG_SUBSCRIPTIONS
            .lock()
            .map_err(|e| format!("Failed to acquire log subscription lock: {e}"))?;
        subs.insert(run_id.clone(), cancelled);
    }

    let run_id_clone = run_id.clone();
    let event_name = format!("run-log-{run_id}");

    std::thread::spawn(move || {
        use tauri::Emitter;
        let base = worktree_path.map_or_else(
            || std::path::PathBuf::from(&repo_path),
            std::path::PathBuf::from,
        );
        let agent_dir = base.join(".agent");
        let log_file = agent_dir
            .join(format!("logs-{run_id_clone}"))
            .join("pipeline.log");

        let mut sequence: u64 = 0;
        let mut last_pos: usize = 0;

        loop {
            if cancelled_clone.load(Ordering::Relaxed) {
                break;
            }

            if let Ok(content) = std::fs::read_to_string(&log_file) {
                let all_lines: Vec<&str> = content.lines().collect();
                if last_pos < all_lines.len() {
                    for line in &all_lines[last_pos..] {
                        let payload = RunLogLine {
                            run_id: run_id_clone.clone(),
                            line: line.to_string(),
                            sequence,
                        };
                        let _ = app.emit(&event_name, &payload);
                        sequence += 1;
                    }
                    last_pos = all_lines.len();
                }
            }

            std::thread::sleep(std::time::Duration::from_millis(500));
        }
    });

    Ok(())
}

/// Unsubscribe from real-time log streaming for a given run.
///
/// # Errors
///
/// Returns an error if the subscription state cannot be accessed.
#[tauri::command]
#[specta::specta]
pub fn unsubscribe_run_logs(run_id: String) -> Result<(), String> {
    use std::sync::atomic::Ordering;

    let cancelled = LOG_SUBSCRIPTIONS
        .lock()
        .map_err(|e| format!("Failed to acquire log subscription lock: {e}"))?
        .remove(&run_id);

    if let Some(cancelled) = cancelled {
        cancelled.store(true, Ordering::Relaxed);
    }

    Ok(())
}

/// Parse a unified diff into `FileDiff` structs.
fn parse_unified_diff(diff_output: &str) -> Vec<FileDiff> {
    let mut files: Vec<FileDiff> = Vec::new();
    let mut current_file: Option<FileDiff> = None;
    let mut diff_lines: Vec<String> = Vec::new();

    for line in diff_output.lines() {
        if line.starts_with("diff --git ") {
            // Save the previous file
            if let Some(mut file) = current_file.take() {
                file.diff_text = diff_lines.join("\n");
                files.push(file);
                diff_lines.clear();
            }
            // Start a new file entry
            current_file = Some(FileDiff {
                path: String::new(),
                additions: 0,
                deletions: 0,
                diff_text: String::new(),
            });
        } else if line.starts_with("+++ b/") {
            if let Some(ref mut file) = current_file {
                file.path = line.trim_start_matches("+++ b/").to_string();
            }
        } else if line.starts_with('+') && !line.starts_with("+++") {
            if let Some(ref mut file) = current_file {
                file.additions += 1;
            }
        } else if line.starts_with('-') && !line.starts_with("---") {
            if let Some(ref mut file) = current_file {
                file.deletions += 1;
            }
        }

        if current_file.is_some() {
            diff_lines.push(line.to_string());
        }
    }

    // Push the last file
    if let Some(mut file) = current_file {
        file.diff_text = diff_lines.join("\n");
        if !file.path.is_empty() {
            files.push(file);
        }
    }

    files
}

/// Get the diff of changed files for a given run.
///
/// Compares the current worktree state against its base branch.
///
/// # Errors
///
/// Returns an error if the git diff command fails or the run cannot be found.
#[tauri::command]
#[specta::specta]
pub fn get_run_changes(
    repo_path: String,
    worktree_path: Option<String>,
    iteration: Option<u32>,
) -> Result<RunChanges, String> {
    let base = worktree_path.as_deref().map_or_else(
        || std::path::PathBuf::from(&repo_path),
        std::path::PathBuf::from,
    );

    if !base.exists() {
        return Ok(RunChanges {
            files: Vec::new(),
            total_additions: 0,
            total_deletions: 0,
            iteration,
        });
    }

    // Run git diff against the merge-base (the point where the branch diverged)
    let output = std::process::Command::new("git")
        .args(["diff", "HEAD~1..HEAD"])
        .current_dir(&base)
        .output();

    let diff_output = match output {
        Ok(out) if out.status.success() => String::from_utf8_lossy(&out.stdout).to_string(),
        Ok(out) => {
            // If HEAD~1 fails (first commit), try diff of everything
            let fallback = std::process::Command::new("git")
                .args(["show", "--format=", "--unified=3"])
                .current_dir(&base)
                .output();
            match fallback {
                Ok(fb) if fb.status.success() => String::from_utf8_lossy(&fb.stdout).to_string(),
                _ => {
                    // Return empty on any git error — not a fatal error
                    let err = String::from_utf8_lossy(&out.stderr).to_string();
                    if err.contains("does not have any commits") || err.contains("unknown revision")
                    {
                        return Ok(RunChanges {
                            files: Vec::new(),
                            total_additions: 0,
                            total_deletions: 0,
                            iteration,
                        });
                    }
                    String::new()
                }
            }
        }
        Err(_) => String::new(),
    };

    let files = parse_unified_diff(&diff_output);
    let total_additions = files.iter().map(|f| f.additions).sum();
    let total_deletions = files.iter().map(|f| f.deletions).sum();

    Ok(RunChanges {
        files,
        total_additions,
        total_deletions,
        iteration,
    })
}

/// Get iteration history for a specific run.
///
/// Reads the iterations array from checkpoint.json. If the array is absent (older
/// checkpoint format), synthesizes a single entry from the scalar `iteration_count`.
///
/// # Errors
///
/// Returns an error if the checkpoint cannot be read.
#[tauri::command]
#[specta::specta]
pub fn get_iteration_history(
    run_id: String,
    state: tauri::State<'_, crate::state::SharedState>,
) -> Result<Vec<IterationSummary>, String> {
    get_iteration_history_impl(state.inner(), &run_id)
}

pub fn get_iteration_history_impl(
    state: &SharedState,
    run_id: &str,
) -> Result<Vec<IterationSummary>, String> {
    let known_repos = {
        let locked = state
            .lock()
            .map_err(|e| format!("Failed to acquire state lock: {e}"))?;
        locked.known_repos.clone()
    };

    for repo_path in &known_repos {
        let checkpoint_file = repo_path.join(".agent").join("checkpoint.json");
        if !checkpoint_file.exists() {
            continue;
        }
        let Ok(content) = std::fs::read_to_string(&checkpoint_file) else {
            continue;
        };
        let Ok(checkpoint) = serde_json::from_str::<serde_json::Value>(&content) else {
            continue;
        };
        let checkpoint_run_id = checkpoint
            .get("run_id")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if checkpoint_run_id != run_id {
            continue;
        }

        return Ok(parse_iteration_history(&checkpoint));
    }

    Err(format!("Run not found: {run_id}"))
}

/// Parse iteration history from a checkpoint JSON value.
///
/// If a detailed `iterations` array is present, returns that. Otherwise,
/// synthesizes entries from the scalar `iteration_count`.
fn parse_iteration_history(checkpoint: &serde_json::Value) -> Vec<IterationSummary> {
    // Try to parse a detailed iterations array first
    if let Some(arr) = checkpoint.get("iterations").and_then(|v| v.as_array()) {
        let mut result = Vec::with_capacity(arr.len());
        for (idx, item) in arr.iter().enumerate() {
            let fallback_number = u32::try_from(idx + 1).unwrap_or(u32::MAX);
            let iteration_number = item
                .get("iteration_number")
                .and_then(serde_json::Value::as_u64)
                .unwrap_or_else(|| u64::from(fallback_number))
                .try_into()
                .unwrap_or(fallback_number);

            let status_str = item
                .get("status")
                .and_then(|v| v.as_str())
                .unwrap_or("Complete");
            let status = match status_str {
                "Running" => IterationStatus::Running,
                "Failed" => IterationStatus::Failed,
                _ => IterationStatus::Complete,
            };

            let duration_secs = item
                .get("duration_secs")
                .and_then(serde_json::Value::as_f64);

            let files_changed = item
                .get("files_changed")
                .and_then(serde_json::Value::as_u64)
                .unwrap_or(0)
                .try_into()
                .unwrap_or(0u32);

            let tests_passed = item
                .get("tests_passed")
                .and_then(serde_json::Value::as_u64)
                .map(|v| v.try_into().unwrap_or(0u32));

            let tests_total = item
                .get("tests_total")
                .and_then(serde_json::Value::as_u64)
                .map(|v| v.try_into().unwrap_or(0u32));

            result.push(IterationSummary {
                iteration_number,
                status,
                duration_secs,
                files_changed,
                tests_passed,
                tests_total,
            });
        }
        return result;
    }

    // Fall back: synthesize from scalar iteration_count
    let count: u32 = checkpoint
        .get("iteration_count")
        .and_then(serde_json::Value::as_u64)
        .unwrap_or(0)
        .try_into()
        .unwrap_or(0u32);

    (1..=count)
        .map(|n| IterationSummary {
            iteration_number: n,
            status: IterationStatus::Complete,
            duration_secs: None,
            files_changed: 0,
            tests_passed: None,
            tests_total: None,
        })
        .collect()
}

/// Get review history for a specific run.
///
/// Reads the reviews array from checkpoint.json. Returns an empty vec when
/// no review data is available (older format or run not yet in review phase).
///
/// # Errors
///
/// Returns an error if the checkpoint cannot be read.
#[tauri::command]
#[specta::specta]
pub fn get_review_history(
    run_id: String,
    state: tauri::State<'_, crate::state::SharedState>,
) -> Result<Vec<ReviewSummary>, String> {
    get_review_history_impl(state.inner(), &run_id)
}

pub fn get_review_history_impl(
    state: &SharedState,
    run_id: &str,
) -> Result<Vec<ReviewSummary>, String> {
    let known_repos = {
        let locked = state
            .lock()
            .map_err(|e| format!("Failed to acquire state lock: {e}"))?;
        locked.known_repos.clone()
    };

    for repo_path in &known_repos {
        let checkpoint_file = repo_path.join(".agent").join("checkpoint.json");
        if !checkpoint_file.exists() {
            continue;
        }
        let Ok(content) = std::fs::read_to_string(&checkpoint_file) else {
            continue;
        };
        let Ok(checkpoint) = serde_json::from_str::<serde_json::Value>(&content) else {
            continue;
        };
        let checkpoint_run_id = checkpoint
            .get("run_id")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if checkpoint_run_id != run_id {
            continue;
        }

        return Ok(parse_review_history(&checkpoint));
    }

    Err(format!("Run not found: {run_id}"))
}

/// Parse review history from a checkpoint JSON value.
///
/// Returns an empty vec if no reviews array is present.
fn parse_review_history(checkpoint: &serde_json::Value) -> Vec<ReviewSummary> {
    let Some(arr) = checkpoint.get("reviews").and_then(|v| v.as_array()) else {
        return Vec::new();
    };

    let mut result = Vec::with_capacity(arr.len());
    for (idx, item) in arr.iter().enumerate() {
        let fallback_number = u32::try_from(idx + 1).unwrap_or(u32::MAX);
        let review_number = item
            .get("review_number")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or_else(|| u64::from(fallback_number))
            .try_into()
            .unwrap_or(fallback_number);

        let status_str = item
            .get("status")
            .and_then(|v| v.as_str())
            .unwrap_or("Complete");
        let status = match status_str {
            "Running" => ReviewStatus::Running,
            "Failed" => ReviewStatus::Failed,
            _ => ReviewStatus::Complete,
        };

        let duration_secs = item
            .get("duration_secs")
            .and_then(serde_json::Value::as_f64);

        let findings_count = item
            .get("findings_count")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0)
            .try_into()
            .unwrap_or(0u32);

        result.push(ReviewSummary {
            review_number,
            status,
            duration_secs,
            findings_count,
        });
    }
    result
}

/// Cancel an active run by removing its lock file.
///
/// # Errors
///
/// Returns an error if the lock file cannot be removed.
#[tauri::command]
#[specta::specta]
pub fn cancel_run(repo_path: String, worktree_path: Option<String>) -> Result<(), String> {
    let base = worktree_path.map_or_else(
        || std::path::PathBuf::from(&repo_path),
        std::path::PathBuf::from,
    );
    let lock_file = base.join(".agent").join("tmp").join("run.lock");

    if lock_file.exists() {
        std::fs::remove_file(&lock_file).map_err(|e| format!("Failed to remove lock file: {e}"))?;
    }

    Ok(())
}

/// Open a path in the system file manager (Finder on macOS, Explorer on Windows).
///
/// # Errors
///
/// Returns an error if the path does not exist or the system file manager cannot be opened.
#[tauri::command]
#[specta::specta]
pub fn open_in_file_manager(path: String) -> Result<(), String> {
    let path_buf = std::path::PathBuf::from(&path);
    if !path_buf.exists() {
        return Err(format!("Path does not exist: {path}"));
    }

    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("Failed to open file manager: {e}"))?;
    }

    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("Failed to open file manager: {e}"))?;
    }

    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("Failed to open file manager: {e}"))?;
    }

    Ok(())
}

/// Open a terminal at the specified path.
///
/// # Errors
///
/// Returns an error if the path does not exist or a terminal cannot be opened.
#[tauri::command]
#[specta::specta]
pub fn open_in_terminal(path: String) -> Result<(), String> {
    let path_buf = std::path::PathBuf::from(&path);
    if !path_buf.exists() {
        return Err(format!("Path does not exist: {path}"));
    }

    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .args(["-a", "Terminal", &path])
            .spawn()
            .map_err(|e| format!("Failed to open terminal: {e}"))?;
    }

    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("cmd")
            .args(["/c", "start", "cmd", "/k", "cd", "/d"])
            .arg(&path)
            .spawn()
            .map_err(|e| format!("Failed to open terminal: {e}"))?;
    }

    #[cfg(target_os = "linux")]
    {
        // Try common terminal emulators
        let terminals = ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"];
        let mut opened = false;
        for terminal in terminals {
            if std::process::Command::new(terminal)
                .args(["--working-directory", &path])
                .spawn()
                .is_ok()
            {
                opened = true;
                break;
            }
        }
        if !opened {
            return Err("Failed to open any known terminal emulator".to_string());
        }
    }

    Ok(())
}

/// Internal struct for checkpoint summary used within this module.
struct CheckpointSummary {
    run_id: String,
    current_phase: String,
    last_checkpoint: Option<String>,
}

fn load_checkpoint_summary(agent_dir: &Path) -> Option<CheckpointSummary> {
    let checkpoint_file = agent_dir.join("checkpoint.json");
    if !checkpoint_file.exists() {
        return None;
    }

    let content = std::fs::read_to_string(&checkpoint_file).ok()?;
    let checkpoint: serde_json::Value = serde_json::from_str(&content).ok()?;

    Some(CheckpointSummary {
        run_id: checkpoint
            .get("run_id")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string(),
        current_phase: checkpoint
            .get("phase")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown")
            .to_string(),
        last_checkpoint: checkpoint
            .get("timestamp")
            .and_then(|v| v.as_str())
            .map(String::from),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_get_run_status_returns_not_started_for_fresh_repo() {
        let dir = TempDir::new().unwrap();
        let result = get_run_status(dir.path().to_string_lossy().to_string(), None);
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let status = result.unwrap();
        assert_eq!(status.status, RunStatus::NotStarted);
        assert!(status.run_id.is_none());
        assert!(status.current_phase.is_none());
        assert!(status.last_checkpoint.is_none());
    }

    #[test]
    fn test_get_run_status_returns_running_when_lock_exists() {
        let dir = TempDir::new().unwrap();
        let agent_tmp = dir.path().join(".agent").join("tmp");
        std::fs::create_dir_all(&agent_tmp).unwrap();
        std::fs::write(agent_tmp.join("run.lock"), "locked").unwrap();

        let result = get_run_status(dir.path().to_string_lossy().to_string(), None);
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        assert_eq!(result.unwrap().status, RunStatus::Running);
    }

    #[test]
    fn test_get_run_status_includes_phase_and_checkpoint_fields() {
        let dir = TempDir::new().unwrap();
        let result = get_run_status(dir.path().to_string_lossy().to_string(), None);
        let status = result.unwrap();
        // For NotStarted, all optional fields should be None
        assert!(status.run_id.is_none());
        assert!(status.current_phase.is_none());
        assert!(status.last_checkpoint.is_none());
    }

    #[test]
    fn test_collect_resumable_runs_returns_empty_when_no_checkpoint() {
        let dir = TempDir::new().unwrap();
        let runs = collect_resumable_runs(&[dir.path().to_path_buf()]);
        assert!(runs.is_empty());
    }

    #[test]
    fn test_collect_resumable_runs_excludes_completed_runs() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "test-run-123",
            "phase": "Complete",
            "timestamp": "2024-01-01 00:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex"
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let runs = collect_resumable_runs(&[dir.path().to_path_buf()]);
        assert!(runs.is_empty(), "Completed runs should not be resumable");
    }

    #[test]
    fn test_collect_resumable_runs_includes_interrupted_runs() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "test-run-456",
            "phase": "Interrupted",
            "timestamp": "2024-01-01 12:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex"
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let runs = collect_resumable_runs(&[dir.path().to_path_buf()]);
        assert_eq!(runs.len(), 1, "Interrupted run should be resumable");
        assert_eq!(runs[0].run_id, "test-run-456");
        assert_eq!(runs[0].status, RunStatus::Paused);
    }

    #[test]
    fn test_get_run_detail_finds_run_by_id() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "target-run-id",
            "phase": "Development",
            "timestamp": "2024-06-01 10:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex"
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let repos = vec![dir.path().to_path_buf()];
        let detail = find_run_in_repos("target-run-id", &repos);
        assert!(detail.is_some(), "Expected Some but got None");
        let d = detail.unwrap();
        assert_eq!(d.run_id, "target-run-id");
        assert_eq!(d.current_phase, "Development");
    }

    #[test]
    fn test_get_run_detail_not_found() {
        let repos: Vec<std::path::PathBuf> = Vec::new();
        let detail = find_run_in_repos("nonexistent-run-id", &repos);
        assert!(detail.is_none());
    }

    #[test]
    fn test_get_run_detail_skips_repos_without_matching_id() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "other-run-id",
            "phase": "Development",
            "timestamp": "2024-06-01 10:00:00",
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let repos = vec![dir.path().to_path_buf()];
        let detail = find_run_in_repos("target-run-id", &repos);
        assert!(detail.is_none(), "Should not find unrelated run");
    }

    #[test]
    fn test_collect_resumable_runs_finds_paused_run_in_worktree() {
        // Main repo has no checkpoint (fresh)
        let main_dir = TempDir::new().unwrap();

        // Worktree has a paused checkpoint
        let worktree_dir = TempDir::new().unwrap();
        let agent_dir = worktree_dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "wt-run-789",
            "phase": "Review",
            "timestamp": "2024-06-01 09:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex"
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        // Scanning only the main repo must NOT find the worktree run
        let main_only = collect_resumable_runs(&[main_dir.path().to_path_buf()]);
        assert!(
            main_only.is_empty(),
            "Main-only scan should not find worktree run"
        );

        // Scanning both must find the worktree run
        let both = collect_resumable_runs(&[
            main_dir.path().to_path_buf(),
            worktree_dir.path().to_path_buf(),
        ]);
        assert_eq!(both.len(), 1, "Should find the paused run in the worktree");
        assert_eq!(both[0].run_id, "wt-run-789");
        assert_eq!(both[0].status, RunStatus::Paused);
    }

    // --- Notification params tests ---

    #[test]
    fn test_notification_params_for_failed_status_has_interruptive_body() {
        let (title, body) = notification_params_for_status("Failed", "run-abc-123", "My Repo");
        assert_eq!(title, "Ralph Run Failed");
        assert!(
            body.is_some(),
            "Failed status should have an interruptive body"
        );
        assert!(body.unwrap().contains("run-abc-123"));
    }

    #[test]
    fn test_notification_params_for_paused_status_has_interruptive_body() {
        let (title, body) = notification_params_for_status("Paused", "run-xyz", "wt-50-feature");
        assert_eq!(title, "Ralph Run Paused");
        assert!(
            body.is_some(),
            "Paused status should have an interruptive body"
        );
    }

    #[test]
    fn test_notification_params_for_completed_status_has_body() {
        let (title, body) = notification_params_for_status("Completed", "run-done", "/my/repo");
        assert_eq!(title, "Ralph Run Completed");
        assert!(body.is_some(), "Completed should have a body");
    }

    #[test]
    fn test_notification_params_for_running_status_has_no_body() {
        let (title, body) = notification_params_for_status("Running", "run-active", "/my/repo");
        // Running is a passive update — no body, just title
        assert!(title.contains("Running"), "Title should mention Running");
        assert!(
            body.is_none(),
            "Running status should have no interruptive body"
        );
    }

    #[test]
    fn test_notification_params_unknown_status_gracefully_handled() {
        let (title, body) = notification_params_for_status("Unknown", "run-x", "ctx");
        // Should not panic and should produce some title
        assert!(!title.is_empty());
        assert!(body.is_none(), "Unknown status should have no body");
    }

    // --- RunDetail diagnostics field tests ---

    #[test]
    fn test_run_detail_defaults_iteration_count_to_zero_when_missing() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        // Old checkpoint without iteration_count field
        let checkpoint = serde_json::json!({
            "run_id": "diag-run-1",
            "phase": "Development",
            "timestamp": "2024-01-01 10:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex"
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let repos = vec![dir.path().to_path_buf()];
        let detail = find_run_in_repos("diag-run-1", &repos).unwrap();
        assert_eq!(
            detail.iteration_count, 0,
            "Missing iteration_count should default to 0"
        );
    }

    #[test]
    fn test_run_detail_parses_iteration_count_from_checkpoint() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "diag-run-2",
            "phase": "Review",
            "timestamp": "2024-01-01 10:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex",
            "iteration_count": 5
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let repos = vec![dir.path().to_path_buf()];
        let detail = find_run_in_repos("diag-run-2", &repos).unwrap();
        assert_eq!(
            detail.iteration_count, 5,
            "Should parse iteration_count from checkpoint"
        );
    }

    #[test]
    fn test_run_detail_defaults_last_error_to_none_when_missing() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "diag-run-3",
            "phase": "Development",
            "timestamp": "2024-01-01 10:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex"
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let repos = vec![dir.path().to_path_buf()];
        let detail = find_run_in_repos("diag-run-3", &repos).unwrap();
        assert!(
            detail.last_error.is_none(),
            "Missing last_error should default to None"
        );
    }

    #[test]
    fn test_run_detail_parses_last_error_from_checkpoint() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "diag-run-4",
            "phase": "Development",
            "timestamp": "2024-01-01 10:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex",
            "last_error": "Agent timeout after 120s"
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let repos = vec![dir.path().to_path_buf()];
        let detail = find_run_in_repos("diag-run-4", &repos).unwrap();
        assert_eq!(
            detail.last_error.as_deref(),
            Some("Agent timeout after 120s"),
            "Should parse last_error from checkpoint"
        );
    }

    #[test]
    fn test_run_detail_defaults_is_degraded_to_false_when_missing() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "diag-run-5",
            "phase": "Development",
            "timestamp": "2024-01-01 10:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex"
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let repos = vec![dir.path().to_path_buf()];
        let detail = find_run_in_repos("diag-run-5", &repos).unwrap();
        assert!(
            !detail.is_degraded,
            "Missing is_degraded should default to false"
        );
    }

    #[test]
    fn test_run_detail_parses_is_degraded_true_from_checkpoint() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "diag-run-6",
            "phase": "Development",
            "timestamp": "2024-01-01 10:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex",
            "is_degraded": true
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let repos = vec![dir.path().to_path_buf()];
        let detail = find_run_in_repos("diag-run-6", &repos).unwrap();
        assert!(
            detail.is_degraded,
            "Should parse is_degraded=true from checkpoint"
        );
    }

    // --- get_run_logs tests ---

    #[test]
    fn test_get_run_logs_returns_empty_when_no_log_file() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        // Checkpoint exists with run_id, but no log directory
        let checkpoint = serde_json::json!({
            "run_id": "log-test-run-1",
            "phase": "Development",
            "timestamp": "2024-01-01 10:00:00",
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let result = get_run_logs(dir.path().to_string_lossy().to_string(), None, None);
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        assert!(
            result.unwrap().is_empty(),
            "Should return empty when no log file exists"
        );
    }

    #[test]
    fn test_get_run_logs_returns_lines_from_log_file() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let run_id = "log-test-run-2";
        let checkpoint = serde_json::json!({
            "run_id": run_id,
            "phase": "Development",
            "timestamp": "2024-01-01 10:00:00",
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let log_dir = agent_dir.join(format!("logs-{run_id}"));
        std::fs::create_dir_all(&log_dir).unwrap();
        std::fs::write(
            log_dir.join("pipeline.log"),
            "line one\nline two\nline three\n",
        )
        .unwrap();

        let result = get_run_logs(dir.path().to_string_lossy().to_string(), None, None);
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let lines = result.unwrap();
        assert_eq!(lines.len(), 3, "Should return 3 lines");
        assert_eq!(lines[0], "line one");
        assert_eq!(lines[1], "line two");
        assert_eq!(lines[2], "line three");
    }

    #[test]
    fn test_get_run_logs_limits_to_last_500_lines() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let run_id = "log-test-run-3";
        let checkpoint = serde_json::json!({
            "run_id": run_id,
            "phase": "Development",
            "timestamp": "2024-01-01 10:00:00",
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let log_dir = agent_dir.join(format!("logs-{run_id}"));
        std::fs::create_dir_all(&log_dir).unwrap();
        let mut content = String::with_capacity(600 * 10);
        for i in 1..=600_usize {
            content.push_str("line ");
            content.push_str(&i.to_string());
            content.push('\n');
        }
        std::fs::write(log_dir.join("pipeline.log"), content).unwrap();

        let result = get_run_logs(dir.path().to_string_lossy().to_string(), None, Some(500));
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let lines = result.unwrap();
        assert_eq!(lines.len(), 500, "Should return exactly 500 lines");
        assert_eq!(
            lines[0], "line 101",
            "Should start from line 101 (last 500 of 600)"
        );
        assert_eq!(lines[499], "line 600", "Should end at line 600");
    }

    #[test]
    fn test_collect_resumable_runs_includes_diagnostics_fields() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "diag-run-7",
            "phase": "Review",
            "timestamp": "2024-01-01 12:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex",
            "iteration_count": 3,
            "last_error": "Retry limit reached",
            "is_degraded": true
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let runs = collect_resumable_runs(&[dir.path().to_path_buf()]);
        assert_eq!(runs.len(), 1);
        assert_eq!(runs[0].iteration_count, 3);
        assert_eq!(runs[0].last_error.as_deref(), Some("Retry limit reached"));
        assert!(runs[0].is_degraded);
    }

    // --- parse_unified_diff tests ---

    #[test]
    fn test_parse_unified_diff_parses_multi_file_diff() {
        let diff = "\
diff --git a/src/main.rs b/src/main.rs
--- a/src/main.rs
+++ b/src/main.rs
@@ -1,3 +1,5 @@
 fn main() {
+    println!(\"Hello\");
+    println!(\"World\");
-    // old code
 }
diff --git a/src/lib.rs b/src/lib.rs
--- a/src/lib.rs
+++ b/src/lib.rs
@@ -10,4 +10,5 @@
 pub fn foo() {
+    let x = 1;
 }";

        let files = parse_unified_diff(diff);
        assert_eq!(files.len(), 2, "Should parse two file diffs");

        let main_rs = &files[0];
        assert_eq!(main_rs.path, "src/main.rs");
        assert_eq!(main_rs.additions, 2, "main.rs should have 2 additions");
        assert_eq!(main_rs.deletions, 1, "main.rs should have 1 deletion");
        assert!(!main_rs.diff_text.is_empty());

        let lib_rs = &files[1];
        assert_eq!(lib_rs.path, "src/lib.rs");
        assert_eq!(lib_rs.additions, 1, "lib.rs should have 1 addition");
        assert_eq!(lib_rs.deletions, 0, "lib.rs should have 0 deletions");
    }

    #[test]
    fn test_parse_unified_diff_returns_empty_for_empty_string() {
        let files = parse_unified_diff("");
        assert!(files.is_empty(), "Empty input should return empty Vec");
    }

    #[test]
    fn test_parse_unified_diff_returns_empty_for_whitespace_only() {
        let files = parse_unified_diff("   \n   \n");
        assert!(
            files.is_empty(),
            "Whitespace-only input should return empty Vec"
        );
    }

    #[test]
    fn test_parse_unified_diff_does_not_panic_without_plus_plus_plus_lines() {
        // A diff section with diff --git header but no +++ b/ line
        let diff = "\
diff --git a/src/binary.bin b/src/binary.bin
new file mode 100644
index 0000000..1234567
Binary files /dev/null and b/src/binary.bin differ";

        // Should not panic and should return a result (may be empty since no +++ b/ line)
        let files = parse_unified_diff(diff);
        // Binary diffs have no +++ b/ line so path stays empty and the file is not pushed
        assert!(
            files.is_empty(),
            "Binary diff without +++ b/ should produce no file entries"
        );
    }

    #[test]
    fn test_parse_unified_diff_does_not_panic_for_partial_diff() {
        // A malformed/partial diff sequence
        let diff = "\
diff --git a/src/partial.rs b/src/partial.rs
+++ b/src/partial.rs
@@ -1,1 +1,2 @@
+new line
malformed";

        // Should not panic
        let files = parse_unified_diff(diff);
        assert_eq!(files.len(), 1, "Should produce one file from partial diff");
        let file = &files[0];
        assert_eq!(file.path, "src/partial.rs");
        assert_eq!(file.additions, 1, "Should count 1 addition");
    }

    #[test]
    fn test_parse_unified_diff_handles_plus_plus_plus_header_lines() {
        // +++ lines at the start of diff headers should NOT be counted as additions
        let diff = "\
diff --git a/src/foo.rs b/src/foo.rs
--- a/src/foo.rs
+++ b/src/foo.rs
@@ -1,2 +1,3 @@
 context
+added line
 context end";

        let files = parse_unified_diff(diff);
        assert_eq!(files.len(), 1);
        assert_eq!(
            files[0].additions, 1,
            "Only the +added line should be counted"
        );
        assert_eq!(files[0].deletions, 0);
    }

    #[test]
    fn test_parse_unified_diff_handles_minus_minus_minus_header_lines() {
        // --- lines at the start of diff headers should NOT be counted as deletions
        let diff = "\
diff --git a/src/bar.rs b/src/bar.rs
--- a/src/bar.rs
+++ b/src/bar.rs
@@ -1,2 +1,1 @@
 context
-removed line";

        let files = parse_unified_diff(diff);
        assert_eq!(files.len(), 1);
        assert_eq!(
            files[0].deletions, 1,
            "Only the -removed line should be counted"
        );
        assert_eq!(files[0].additions, 0);
    }

    // --- log subscription lifecycle tests ---

    #[test]
    fn test_subscribe_run_logs_stores_cancel_handle_in_map() {
        use std::sync::atomic::Ordering;

        let run_id = "test-sub-run-001".to_string();

        // Clear any existing entry first
        {
            let mut subs = LOG_SUBSCRIPTIONS.lock().unwrap();
            subs.remove(&run_id);
        }

        // Verify there is no entry before subscribe
        assert!(
            !LOG_SUBSCRIPTIONS.lock().unwrap().contains_key(&run_id),
            "Should not have entry before subscribe"
        );

        // Insert a cancel handle manually (simulates what subscribe does)
        let cancel = Arc::new(std::sync::atomic::AtomicBool::new(false));
        {
            let mut subs = LOG_SUBSCRIPTIONS.lock().unwrap();
            subs.insert(run_id.clone(), Arc::clone(&cancel));
        }

        // Verify it is in the map and not yet cancelled
        assert!(
            !LOG_SUBSCRIPTIONS
                .lock()
                .unwrap()
                .get(&run_id)
                .expect("Should have cancel handle")
                .load(Ordering::Relaxed),
            "Cancel flag should be false before unsubscribe"
        );

        // Clean up
        let mut subs = LOG_SUBSCRIPTIONS.lock().unwrap();
        subs.remove(&run_id);
    }

    #[test]
    fn test_unsubscribe_run_logs_sets_atomic_bool_and_removes_handle() {
        use std::sync::atomic::Ordering;

        let run_id = "test-unsub-run-002".to_string();

        // Insert a cancel handle
        let cancel = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let cancel_clone = Arc::clone(&cancel);
        {
            let mut subs = LOG_SUBSCRIPTIONS.lock().unwrap();
            subs.insert(run_id.clone(), cancel);
        }

        // Unsubscribe
        let result = unsubscribe_run_logs(run_id.clone());
        assert!(result.is_ok(), "unsubscribe should return Ok");

        // The AtomicBool via the clone should now be true
        assert!(
            cancel_clone.load(Ordering::Relaxed),
            "Cancel flag should be true after unsubscribe"
        );

        // The entry should be removed from the map
        assert!(
            !LOG_SUBSCRIPTIONS.lock().unwrap().contains_key(&run_id),
            "Handle should be removed from map after unsubscribe"
        );
    }

    #[test]
    fn test_unsubscribe_run_logs_non_existent_id_returns_ok() {
        let run_id = "nonexistent-run-id-xyz-999".to_string();

        // Ensure it doesn't exist
        {
            let mut subs = LOG_SUBSCRIPTIONS.lock().unwrap();
            subs.remove(&run_id);
        }

        let result = unsubscribe_run_logs(run_id);
        assert!(
            result.is_ok(),
            "Unsubscribing a non-existent run_id should return Ok"
        );
    }

    // --- IterationSummary deserialization tests ---

    #[test]
    fn test_iteration_summary_deserializes_with_all_fields() {
        let json = serde_json::json!({
            "iteration_number": 2,
            "status": "Complete",
            "duration_secs": 252.5,
            "files_changed": 5,
            "tests_passed": 8,
            "tests_total": 10
        });
        let summary: IterationSummary = serde_json::from_value(json).unwrap();
        assert_eq!(summary.iteration_number, 2);
        assert_eq!(summary.status, IterationStatus::Complete);
        assert_eq!(summary.duration_secs, Some(252.5));
        assert_eq!(summary.files_changed, 5);
        assert_eq!(summary.tests_passed, Some(8));
        assert_eq!(summary.tests_total, Some(10));
    }

    #[test]
    fn test_iteration_summary_deserializes_with_missing_optional_fields() {
        let json = serde_json::json!({
            "iteration_number": 1,
            "status": "Running",
            "duration_secs": null,
            "files_changed": 0,
            "tests_passed": null,
            "tests_total": null
        });
        let summary: IterationSummary = serde_json::from_value(json).unwrap();
        assert_eq!(summary.iteration_number, 1);
        assert_eq!(summary.status, IterationStatus::Running);
        assert!(summary.duration_secs.is_none());
        assert!(summary.tests_passed.is_none());
        assert!(summary.tests_total.is_none());
    }

    #[test]
    fn test_iteration_status_deserializes_correctly() {
        let complete: IterationStatus = serde_json::from_str("\"Complete\"").unwrap();
        assert_eq!(complete, IterationStatus::Complete);

        let running: IterationStatus = serde_json::from_str("\"Running\"").unwrap();
        assert_eq!(running, IterationStatus::Running);

        let failed: IterationStatus = serde_json::from_str("\"Failed\"").unwrap();
        assert_eq!(failed, IterationStatus::Failed);
    }

    // --- ReviewSummary deserialization tests ---

    #[test]
    fn test_review_summary_deserializes_with_all_fields() {
        let json = serde_json::json!({
            "review_number": 1,
            "status": "Complete",
            "duration_secs": 45.0,
            "findings_count": 3
        });
        let summary: ReviewSummary = serde_json::from_value(json).unwrap();
        assert_eq!(summary.review_number, 1);
        assert_eq!(summary.status, ReviewStatus::Complete);
        assert_eq!(summary.duration_secs, Some(45.0));
        assert_eq!(summary.findings_count, 3);
    }

    #[test]
    fn test_review_summary_deserializes_with_missing_optional_fields() {
        let json = serde_json::json!({
            "review_number": 2,
            "status": "Running",
            "duration_secs": null,
            "findings_count": 1
        });
        let summary: ReviewSummary = serde_json::from_value(json).unwrap();
        assert_eq!(summary.review_number, 2);
        assert_eq!(summary.status, ReviewStatus::Running);
        assert!(summary.duration_secs.is_none());
    }

    // --- parse_iteration_history tests ---

    #[test]
    fn test_parse_iteration_history_from_detailed_array() {
        let checkpoint = serde_json::json!({
            "run_id": "test",
            "iterations": [
                {
                    "iteration_number": 1,
                    "status": "Complete",
                    "duration_secs": 120.0,
                    "files_changed": 3,
                    "tests_passed": 5,
                    "tests_total": 5
                },
                {
                    "iteration_number": 2,
                    "status": "Running",
                    "duration_secs": null,
                    "files_changed": 0,
                    "tests_passed": null,
                    "tests_total": null
                }
            ]
        });
        let history = parse_iteration_history(&checkpoint);
        assert_eq!(history.len(), 2);
        assert_eq!(history[0].iteration_number, 1);
        assert_eq!(history[0].status, IterationStatus::Complete);
        assert_eq!(history[0].duration_secs, Some(120.0));
        assert_eq!(history[0].files_changed, 3);
        assert_eq!(history[1].iteration_number, 2);
        assert_eq!(history[1].status, IterationStatus::Running);
    }

    #[test]
    fn test_parse_iteration_history_synthesizes_from_scalar_count() {
        let checkpoint = serde_json::json!({
            "run_id": "test",
            "iteration_count": 3
        });
        let history = parse_iteration_history(&checkpoint);
        assert_eq!(
            history.len(),
            3,
            "Should synthesize 3 entries from iteration_count"
        );
        assert_eq!(history[0].iteration_number, 1);
        assert_eq!(history[0].status, IterationStatus::Complete);
        assert!(
            history[0].duration_secs.is_none(),
            "Synthesized entries have no duration"
        );
        assert_eq!(history[2].iteration_number, 3);
    }

    #[test]
    fn test_parse_iteration_history_returns_empty_when_no_data() {
        let checkpoint = serde_json::json!({ "run_id": "test" });
        let history = parse_iteration_history(&checkpoint);
        assert!(
            history.is_empty(),
            "Should return empty when no iteration data"
        );
    }

    // --- parse_review_history tests ---

    #[test]
    fn test_parse_review_history_from_reviews_array() {
        let checkpoint = serde_json::json!({
            "run_id": "test",
            "reviews": [
                {
                    "review_number": 1,
                    "status": "Complete",
                    "duration_secs": 45.0,
                    "findings_count": 2
                }
            ]
        });
        let history = parse_review_history(&checkpoint);
        assert_eq!(history.len(), 1);
        assert_eq!(history[0].review_number, 1);
        assert_eq!(history[0].status, ReviewStatus::Complete);
        assert_eq!(history[0].findings_count, 2);
    }

    #[test]
    fn test_parse_review_history_returns_empty_when_no_reviews() {
        let checkpoint = serde_json::json!({ "run_id": "test" });
        let history = parse_review_history(&checkpoint);
        assert!(
            history.is_empty(),
            "Should return empty when no reviews in checkpoint"
        );
    }

    // --- RunDetail new fields backward compat tests ---

    #[test]
    fn test_run_detail_new_fields_default_correctly_for_old_checkpoints() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        // Old checkpoint without new fields
        let checkpoint = serde_json::json!({
            "run_id": "compat-run-1",
            "phase": "Development",
            "timestamp": "2024-01-01 10:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex"
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let repos = vec![dir.path().to_path_buf()];
        let detail = find_run_in_repos("compat-run-1", &repos).unwrap();

        assert_eq!(detail.total_files_changed, 0, "Should default to 0");
        assert!(
            detail.total_tests_passed.is_none(),
            "Should default to None"
        );
        assert_eq!(detail.review_count, 0, "Should default to 0");
        assert!(
            detail.total_duration_secs.is_none(),
            "Should default to None"
        );
        assert!(
            !detail.phase_durations.is_empty(),
            "Should synthesize phase_durations from current_phase"
        );
    }

    #[test]
    fn test_run_detail_parses_new_fields_from_checkpoint() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "new-fields-run-1",
            "phase": "Development",
            "timestamp": "2024-01-01 10:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex",
            "total_files_changed": 7,
            "total_tests_passed": 18,
            "review_count": 2,
            "is_degraded": true,
            "retry_count": 3,
            "fallback_agent": "codex-fallback",
            "degraded_reason": "Max retries reached"
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let repos = vec![dir.path().to_path_buf()];
        let detail = find_run_in_repos("new-fields-run-1", &repos).unwrap();

        assert_eq!(detail.total_files_changed, 7);
        assert_eq!(detail.total_tests_passed, Some(18));
        assert_eq!(detail.review_count, 2);
        assert!(detail.degraded_info.is_some());
        let degraded = detail.degraded_info.unwrap();
        assert_eq!(degraded.retry_count, 3);
        assert_eq!(degraded.fallback_agent.as_deref(), Some("codex-fallback"));
        assert_eq!(degraded.reason.as_deref(), Some("Max retries reached"));
    }

    // --- PhaseDuration synthesis tests ---

    #[test]
    fn test_parse_phase_durations_synthesizes_from_current_phase() {
        let checkpoint = serde_json::json!({
            "phase": "Develop"
        });
        let durations = parse_phase_durations_from_checkpoint(&checkpoint);
        assert!(!durations.is_empty(), "Should synthesize phases");
        // Find Plan — should be completed (before Develop)
        let plan = durations.iter().find(|d| d.phase_name == "Plan");
        assert!(plan.is_some(), "Plan should be in durations");
        assert_eq!(plan.unwrap().status, "completed");
        // Develop should be active
        let develop = durations.iter().find(|d| d.phase_name == "Develop");
        assert!(develop.is_some());
        assert_eq!(develop.unwrap().status, "active");
        // Review and Commit should be pending
        let review = durations.iter().find(|d| d.phase_name == "Review");
        assert_eq!(review.unwrap().status, "pending");
    }

    #[test]
    fn test_parse_phase_durations_from_phase_history_array() {
        let checkpoint = serde_json::json!({
            "phase_history": [
                { "phase_name": "Plan", "duration_secs": 30.0, "status": "completed" },
                { "phase_name": "Develop", "duration_secs": 120.0, "status": "completed" }
            ]
        });
        let durations = parse_phase_durations_from_checkpoint(&checkpoint);
        assert_eq!(durations.len(), 2);
        assert_eq!(durations[0].phase_name, "Plan");
        assert_eq!(durations[0].duration_secs, Some(30.0));
        assert_eq!(durations[1].phase_name, "Develop");
    }

    // --- DegradedInfo tests ---

    #[test]
    fn test_parse_degraded_info_returns_none_when_not_degraded() {
        let checkpoint = serde_json::json!({ "is_degraded": false });
        let info = parse_degraded_info_from_checkpoint(&checkpoint);
        assert!(info.is_none());
    }

    #[test]
    fn test_parse_degraded_info_returns_some_when_degraded() {
        let checkpoint = serde_json::json!({
            "is_degraded": true,
            "retry_count": 2,
            "fallback_agent": "backup-agent",
            "degraded_reason": "Primary timed out"
        });
        let info = parse_degraded_info_from_checkpoint(&checkpoint).unwrap();
        assert_eq!(info.retry_count, 2);
        assert_eq!(info.fallback_agent.as_deref(), Some("backup-agent"));
        assert_eq!(info.reason.as_deref(), Some("Primary timed out"));
    }
}
