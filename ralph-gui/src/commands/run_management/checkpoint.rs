use crate::commands::run_management::checkpoint_boundary;
use crate::domain::run::{DegradedInfo, PhaseDuration};
use serde_json::Value;
use std::path::Path;

/// Internal struct for checkpoint summary used within this module.
pub struct CheckpointSummary {
    pub run_id: String,
    pub current_phase: String,
    pub last_checkpoint: Option<String>,
}

pub fn load_checkpoint_summary(agent_dir: &Path) -> Option<CheckpointSummary> {
    checkpoint_boundary::read_checkpoint(agent_dir)
        .map(|checkpoint| checkpoint_summary_from_value(&checkpoint))
}

fn checkpoint_summary_from_value(checkpoint: &Value) -> CheckpointSummary {
    CheckpointSummary {
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
    }
}

pub fn parse_phase_durations_from_checkpoint(checkpoint: &Value) -> Vec<PhaseDuration> {
    if let Some(arr) = checkpoint.get("phase_history").and_then(|v| v.as_array()) {
        return arr
            .iter()
            .filter_map(|item| {
                let phase_name = item
                    .get("phase_name")
                    .and_then(|v| v.as_str())
                    .map(String::from)?;
                let duration_secs = item.get("duration_secs").and_then(Value::as_f64);
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

pub fn parse_degraded_info_from_checkpoint(checkpoint: &Value) -> Option<DegradedInfo> {
    let is_degraded = checkpoint
        .get("is_degraded")
        .and_then(Value::as_bool)
        .unwrap_or(false);

    if !is_degraded {
        return None;
    }

    let retry_count = checkpoint
        .get("retry_count")
        .and_then(Value::as_u64)
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
