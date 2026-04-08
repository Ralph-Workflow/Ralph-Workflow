use super::types::{IterationStatus, IterationSummary, ReviewStatus, ReviewSummary};
use crate::commands::run_management::checkpoint_boundary;
use crate::domain::run::{
    checkpoint_matches_run_id, parse_iteration_history, parse_review_history,
};
use crate::state::SharedState;
use serde_json::Value;
use std::path::PathBuf;

fn known_repos(state: &SharedState) -> Result<Vec<PathBuf>, String> {
    let locked = state
        .lock()
        .map_err(|e| format!("Failed to acquire state lock: {e}"))?;
    Ok(locked.known_repos.clone())
}

fn find_checkpoint_for_run(paths: &[PathBuf], run_id: &str) -> Option<Value> {
    for repo_path in paths {
        let agent_dir = repo_path.join(".agent");
        if let Some(checkpoint) = checkpoint_boundary::read_checkpoint(&agent_dir) {
            if checkpoint_matches_run_id(&checkpoint, run_id) {
                return Some(checkpoint);
            }
        }
    }
    None
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
    state: tauri::State<'_, SharedState>,
) -> Result<Vec<IterationSummary>, String> {
    let repos = known_repos(state.inner())?;
    let checkpoint = find_checkpoint_for_run(&repos, &run_id)
        .ok_or_else(|| format!("Run not found: {run_id}"))?;
    let entries = parse_iteration_history(&checkpoint)
        .into_iter()
        .map(|entry| IterationSummary {
            iteration_number: entry.iteration_number,
            status: map_iteration_status(&entry.status),
            duration_secs: entry.duration_secs,
            files_changed: entry.files_changed,
            tests_passed: entry.tests_passed,
            tests_total: entry.tests_total,
        })
        .collect();
    Ok(entries)
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
    state: tauri::State<'_, SharedState>,
) -> Result<Vec<ReviewSummary>, String> {
    let repos = known_repos(state.inner())?;
    let checkpoint = find_checkpoint_for_run(&repos, &run_id)
        .ok_or_else(|| format!("Run not found: {run_id}"))?;
    let entries = parse_review_history(&checkpoint)
        .into_iter()
        .map(|entry| ReviewSummary {
            review_number: entry.review_number,
            status: map_review_status(&entry.status),
            duration_secs: entry.duration_secs,
            findings_count: entry.findings_count,
        })
        .collect();
    Ok(entries)
}

fn map_iteration_status(status: &str) -> IterationStatus {
    match status {
        "Running" => IterationStatus::Running,
        "Failed" => IterationStatus::Failed,
        _ => IterationStatus::Complete,
    }
}

fn map_review_status(status: &str) -> ReviewStatus {
    match status {
        "Running" => ReviewStatus::Running,
        "Failed" => ReviewStatus::Failed,
        _ => ReviewStatus::Complete,
    }
}
