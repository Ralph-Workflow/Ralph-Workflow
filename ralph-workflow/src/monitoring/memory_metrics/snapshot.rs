// Memory snapshot types and heap-size estimation.

use serde::{Deserialize, Serialize};

/// Memory usage snapshot at a point in time.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemorySnapshot {
    /// Pipeline iteration when snapshot was taken
    pub iteration: u32,
    /// Execution history length
    pub execution_history_len: usize,
    /// Deterministic size proxy for execution history (bytes).
    ///
    /// This is not a true allocator-backed heap measurement. It uses string lengths as
    /// a stable, platform-independent proxy suitable for regression tracking.
    pub execution_history_heap_bytes: usize,
    /// Checkpoint saved count
    pub checkpoint_count: u32,
    /// Timestamp when snapshot was taken (ISO 8601)
    pub timestamp: String,
}

impl MemorySnapshot {
    /// Create a snapshot from current pipeline state.
    #[must_use]
    pub fn from_pipeline_state(state: &crate::reducer::PipelineState) -> Self {
        let execution_history_heap_bytes = estimate_execution_history_heap_size(state);

        Self {
            iteration: state.iteration,
            execution_history_len: state.execution_history_len(),
            execution_history_heap_bytes,
            checkpoint_count: state.checkpoint_saved_count,
            timestamp: chrono::Utc::now().to_rfc3339(),
        }
    }
}

/// Estimate a deterministic "heap bytes" proxy for execution history.
///
/// Uses string lengths (and collection element lengths) to produce a stable number that
/// tracks payload growth without depending on allocator behavior.
pub(super) fn estimate_execution_history_heap_size(state: &crate::reducer::PipelineState) -> usize {
    use crate::checkpoint::execution_history::StepOutcome;

    state
        .execution_history()
        .iter()
        .map(|step| {
            let modified_files_detail_size = step.modified_files_detail.as_ref().map_or(0, |d| {
                let sum_list = |xs: &Option<Box<[String]>>| {
                    xs.as_ref()
                        .map_or(0, |v| v.iter().map(std::string::String::len).sum::<usize>())
                };

                sum_list(&d.added) + sum_list(&d.modified) + sum_list(&d.deleted)
            });

            let issues_summary_size = step
                .issues_summary
                .as_ref()
                .and_then(|s| s.description.as_ref())
                .map_or(0, std::string::String::len);

            // Approximate heap allocations: string fields + vec allocations
            // Use `len()` consistently as a deterministic size proxy.
            let base_size = step.phase.len()
                + step.step_type.len()
                + step.timestamp.len()
                + step.agent.as_ref().map_or(0, |s| s.len())
                + step
                    .checkpoint_saved_at
                    .as_ref()
                    .map_or(0, std::string::String::len)
                + step
                    .git_commit_oid
                    .as_ref()
                    .map_or(0, std::string::String::len)
                + step
                    .prompt_used
                    .as_ref()
                    .map_or(0, std::string::String::len)
                + modified_files_detail_size
                + issues_summary_size;

            let outcome_size = match &step.outcome {
                StepOutcome::Success {
                    output,
                    files_modified,
                    ..
                } => {
                    output.as_ref().map_or(0, |s| s.len())
                        + files_modified.as_ref().map_or(0, |files| {
                            files.iter().map(std::string::String::len).sum::<usize>()
                        })
                }
                StepOutcome::Failure { error, signals, .. } => {
                    error.len()
                        + signals.as_ref().map_or(0, |sigs| {
                            sigs.iter().map(std::string::String::len).sum::<usize>()
                        })
                }
                StepOutcome::Partial {
                    completed,
                    remaining,
                    ..
                } => completed.len() + remaining.len(),
                StepOutcome::Skipped { reason } => reason.len(),
            };

            base_size + outcome_size
        })
        .sum()
}
