use serde::{Deserialize, Serialize};
use specta::Type;

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
