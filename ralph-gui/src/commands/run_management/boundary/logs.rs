use super::types::RunLogLine;
use crate::commands::run_management::checkpoint_boundary;
use crate::domain::run::{limit_log_lines, log_file_path};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter};

static LOG_SUBSCRIPTIONS: std::sync::LazyLock<Mutex<HashMap<String, Arc<AtomicBool>>>> =
    std::sync::LazyLock::new(|| Mutex::new(HashMap::new()));

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
    let base = worktree_path.map_or_else(|| PathBuf::from(repo_path.clone()), PathBuf::from);
    let agent_dir = base.join(".agent");

    collect_recent_run_logs(&agent_dir, max_lines.unwrap_or(500))
}

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
    let cancelled = Arc::new(AtomicBool::new(false));
    {
        let mut subs = LOG_SUBSCRIPTIONS
            .lock()
            .map_err(|e| format!("Failed to acquire log subscription lock: {e}"))?;
        subs.insert(run_id.clone(), Arc::clone(&cancelled));
    }

    spawn_log_subscription(cancelled, app, run_id, repo_path, worktree_path);

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
    let cancelled = LOG_SUBSCRIPTIONS
        .lock()
        .map_err(|e| format!("Failed to acquire log subscription lock: {e}"))?
        .remove(&run_id);

    if let Some(cancelled) = cancelled {
        cancelled.store(true, Ordering::Relaxed);
    }

    Ok(())
}

fn collect_recent_run_logs(agent_dir: &Path, max_lines: usize) -> Result<Vec<String>, String> {
    let run_id = match load_run_id(agent_dir)? {
        Some(id) if !id.is_empty() => id,
        _ => return Ok(Vec::new()),
    };

    let log_file = log_file_path(agent_dir, &run_id);
    if !log_file.exists() {
        return Ok(Vec::new());
    }

    let lines = read_log_lines(&log_file)?;
    Ok(limit_log_lines(lines, max_lines))
}

fn load_run_id(agent_dir: &Path) -> Result<Option<String>, String> {
    Ok(
        checkpoint_boundary::read_checkpoint(agent_dir).and_then(|checkpoint| {
            checkpoint
                .get("run_id")
                .and_then(|v| v.as_str())
                .map(String::from)
        }),
    )
}

fn read_log_lines(log_file: &Path) -> Result<Vec<String>, String> {
    let content =
        std::fs::read_to_string(log_file).map_err(|e| format!("Failed to read log file: {e}"))?;
    Ok(content.lines().map(String::from).collect())
}

fn spawn_log_subscription(
    cancelled: Arc<AtomicBool>,
    app: AppHandle,
    run_id: String,
    repo_path: String,
    worktree_path: Option<String>,
) {
    std::thread::spawn(move || {
        let base = worktree_path.map_or_else(|| PathBuf::from(&repo_path), PathBuf::from);
        let agent_dir = base.join(".agent");
        let log_file = agent_dir
            .join(format!("logs-{run_id}"))
            .join("pipeline.log");

        let mut sequence: u64 = 0;
        let mut last_pos: usize = 0;

        loop {
            if cancelled.load(Ordering::Relaxed) {
                break;
            }

            if let Ok(content) = std::fs::read_to_string(&log_file) {
                let all_lines: Vec<&str> = content.lines().collect();
                if last_pos < all_lines.len() {
                    for line in &all_lines[last_pos..] {
                        let payload = RunLogLine {
                            run_id: run_id.clone(),
                            line: line.to_string(),
                            sequence,
                        };
                        let _ = app.emit(&format!("run-log-{run_id}"), &payload);
                        sequence += 1;
                    }
                    last_pos = all_lines.len();
                }
            }

            std::thread::sleep(std::time::Duration::from_millis(500));
        }
    });
}
