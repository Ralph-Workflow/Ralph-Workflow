use crate::commands::run_management::checkpoint::{
    parse_degraded_info_from_checkpoint, parse_phase_durations_from_checkpoint, CheckpointSummary,
};
use crate::domain::run::{RunDetail, RunStatus, RunStatusSummary};
use serde_json::Value;
use std::path::Path;

pub fn build_status_summary(
    lock_present: bool,
    checkpoint_summary: Option<CheckpointSummary>,
) -> RunStatusSummary {
    if lock_present {
        let checkpoint = checkpoint_summary.as_ref();
        return RunStatusSummary {
            status: RunStatus::Running,
            run_id: checkpoint.map(|c| c.run_id.clone()),
            current_phase: checkpoint.map(|c| c.current_phase.clone()),
            last_checkpoint: checkpoint.and_then(|c| c.last_checkpoint.clone()),
        };
    }

    let Some(summary) = checkpoint_summary else {
        return RunStatusSummary {
            status: RunStatus::NotStarted,
            run_id: None,
            current_phase: None,
            last_checkpoint: None,
        };
    };

    let status = if summary.current_phase == "Complete" {
        RunStatus::Completed
    } else {
        RunStatus::Paused
    };

    RunStatusSummary {
        status,
        run_id: Some(summary.run_id.clone()),
        current_phase: Some(summary.current_phase.clone()),
        last_checkpoint: summary.last_checkpoint.clone(),
    }
}

pub fn run_detail_from_checkpoint(
    run_id: String,
    checkpoint: &Value,
    repo_path: &Path,
    status: RunStatus,
    description: String,
) -> RunDetail {
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
    let iteration_count = checkpoint
        .get("iteration_count")
        .and_then(Value::as_u64)
        .unwrap_or(0)
        .try_into()
        .unwrap_or(0u32);
    let last_error = checkpoint
        .get("last_error")
        .and_then(|v| v.as_str())
        .map(String::from);
    let is_degraded = checkpoint
        .get("is_degraded")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let phase_durations = parse_phase_durations_from_checkpoint(checkpoint);
    let degraded_info = parse_degraded_info_from_checkpoint(checkpoint);
    let total_files_changed = checkpoint
        .get("total_files_changed")
        .and_then(Value::as_u64)
        .unwrap_or(0)
        .try_into()
        .unwrap_or(0u32);
    let total_tests_passed = checkpoint
        .get("total_tests_passed")
        .and_then(Value::as_u64)
        .map(|v| v.try_into().unwrap_or(0u32));
    let review_count = checkpoint
        .get("review_count")
        .and_then(Value::as_u64)
        .unwrap_or(0)
        .try_into()
        .unwrap_or(0u32);

    RunDetail {
        run_id,
        status,
        current_phase: phase.clone(),
        last_checkpoint: Some(timestamp.clone()),
        agent_profile: format!("{developer_agent}/{reviewer_agent}"),
        repo_path: repo_path.to_string_lossy().into_owned(),
        worktree_path: None,
        created_at: timestamp,
        description,
        iteration_count,
        last_error,
        is_degraded,
        phase_durations,
        degraded_info,
        total_duration_secs: None,
        total_files_changed,
        total_tests_passed,
        review_count,
    }
}
