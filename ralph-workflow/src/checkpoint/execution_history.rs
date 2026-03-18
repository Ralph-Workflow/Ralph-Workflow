//! Execution history tracking for checkpoint state.
//!
//! This module provides structures for tracking the execution history of a pipeline,
//! enabling idempotent recovery and validation of state.

pub mod compression;

use crate::checkpoint::timestamp;
use crate::workspace::Workspace;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, VecDeque};
use std::path::Path;
use std::sync::Arc;

fn deserialize_option_boxed_string_slice_none_if_empty<'de, D>(
    deserializer: D,
) -> Result<Option<Box<[String]>>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let opt = Option::<Vec<String>>::deserialize(deserializer)?;
    Ok(match opt {
        None => None,
        Some(v) if v.is_empty() => None,
        Some(v) => Some(v.into_boxed_slice()),
    })
}

fn serialize_option_boxed_string_slice_empty_if_none_field<S, V>(
    value: V,
    serializer: S,
) -> Result<S::Ok, S::Error>
where
    S: serde::Serializer,
    V: std::ops::Deref<Target = Option<Box<[String]>>>,
{
    let values = (*value).as_deref();
    serialize_option_boxed_string_slice_empty_if_none(values, serializer)
}

fn serialize_option_boxed_string_slice_empty_if_none<S>(
    value: Option<&[String]>,
    serializer: S,
) -> Result<S::Ok, S::Error>
where
    S: serde::Serializer,
{
    use serde::ser::SerializeSeq;

    if let Some(values) = value {
        values.serialize(serializer)
    } else {
        let seq = serializer.serialize_seq(Some(0))?;
        seq.end()
    }
}

/// Outcome of an execution step.
///
/// # Memory Optimization
///
/// This enum uses Box<str> for string fields and Option<Box<[String]>> for
/// collections to reduce allocation overhead when fields are empty or small.
/// Vec<T> over-allocates capacity, while Box<[T]> uses exactly the needed space.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum StepOutcome {
    /// Step completed successfully
    Success {
        output: Option<Box<str>>,
        #[serde(
            default,
            deserialize_with = "deserialize_option_boxed_string_slice_none_if_empty",
            serialize_with = "serialize_option_boxed_string_slice_empty_if_none_field"
        )]
        files_modified: Option<Box<[String]>>,
        #[serde(default)]
        exit_code: Option<i32>,
    },
    /// Step failed with error
    Failure {
        error: Box<str>,
        recoverable: bool,
        #[serde(default)]
        exit_code: Option<i32>,
        #[serde(
            default,
            deserialize_with = "deserialize_option_boxed_string_slice_none_if_empty",
            serialize_with = "serialize_option_boxed_string_slice_empty_if_none_field"
        )]
        signals: Option<Box<[String]>>,
    },
    /// Step partially completed (may need retry)
    Partial {
        completed: Box<str>,
        remaining: Box<str>,
        #[serde(default)]
        exit_code: Option<i32>,
    },
    /// Step was skipped (e.g., already done)
    Skipped { reason: Box<str> },
}

impl StepOutcome {
    /// Create a Success outcome with default values.
    pub fn success(output: Option<String>, files_modified: Vec<String>) -> Self {
        Self::Success {
            output: output.map(String::into_boxed_str),
            files_modified: if files_modified.is_empty() {
                None
            } else {
                Some(files_modified.into_boxed_slice())
            },
            exit_code: Some(0),
        }
    }

    /// Create a Failure outcome with default values.
    #[must_use]
    pub fn failure(error: String, recoverable: bool) -> Self {
        Self::Failure {
            error: error.into_boxed_str(),
            recoverable,
            exit_code: None,
            signals: None,
        }
    }

    /// Create a Partial outcome with default values.
    #[must_use]
    pub fn partial(completed: String, remaining: String) -> Self {
        Self::Partial {
            completed: completed.into_boxed_str(),
            remaining: remaining.into_boxed_str(),
            exit_code: None,
        }
    }

    /// Create a Skipped outcome.
    #[must_use]
    pub fn skipped(reason: String) -> Self {
        Self::Skipped {
            reason: reason.into_boxed_str(),
        }
    }
}

/// Detailed information about files modified in a step.
///
/// # Memory Optimization
///
/// Uses `Option<Box<[String]>>` instead of `Vec<String>` to save memory:
/// - Empty collections use `None` instead of empty Vec (saves 24 bytes per field)
/// - Non-empty collections use `Box<[String]>` which is 16 bytes vs Vec's 24 bytes
/// - Total savings: up to 72 bytes per instance when all fields are empty
#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct ModifiedFilesDetail {
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_option_boxed_string_slice_none_if_empty"
    )]
    pub added: Option<Box<[String]>>,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_option_boxed_string_slice_none_if_empty"
    )]
    pub modified: Option<Box<[String]>>,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_option_boxed_string_slice_none_if_empty"
    )]
    pub deleted: Option<Box<[String]>>,
}

/// Summary of issues found and fixed during a step.
#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct IssuesSummary {
    /// Number of issues found
    #[serde(default)]
    pub found: u32,
    /// Number of issues fixed
    #[serde(default)]
    pub fixed: u32,
    /// Description of issues (e.g., "3 clippy warnings, 2 test failures")
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
}

/// A single execution step in the pipeline history.
///
/// # Memory Optimization
///
/// This struct uses Arc<str> for `phase` and `agent` fields to reduce memory
/// usage through string interning. Phase names and agent names are repeated
/// frequently across execution history entries, so sharing allocations via
/// Arc<str> significantly reduces heap usage.
///
/// Serialization/deserialization is backward-compatible - Arc<str> is serialized
/// as a regular string and can be deserialized from both old (String) and new
/// (Arc<str>) checkpoint formats.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ExecutionStep {
    /// Phase this step belongs to (interned via Arc<str>)
    pub phase: Arc<str>,
    /// Iteration number (for development/review iterations)
    pub iteration: u32,
    /// Type of step (e.g., "review", "fix", "commit")
    pub step_type: Box<str>,
    /// When this step was executed (ISO 8601 format string)
    pub timestamp: String,
    /// Outcome of the step
    pub outcome: StepOutcome,
    /// Agent that executed this step (interned via Arc<str>)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent: Option<Arc<str>>,
    /// Duration in seconds (if available)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub duration_secs: Option<u64>,
    /// When a checkpoint was saved during this step (ISO 8601 format string)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub checkpoint_saved_at: Option<String>,
    /// Git commit OID created during this step (if any)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub git_commit_oid: Option<String>,
    /// Detailed information about files modified
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub modified_files_detail: Option<ModifiedFilesDetail>,
    /// The prompt text used for this step (for deterministic replay)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub prompt_used: Option<String>,
    /// Issues summary (found and fixed counts)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub issues_summary: Option<IssuesSummary>,
}

impl ExecutionStep {
    /// Create a new execution step.
    ///
    /// # Performance Note
    ///
    /// For optimal memory usage, use `new_with_pool` to intern repeated phase
    /// and agent names via a `StringPool`. This constructor creates new Arc<str>
    /// allocations for each call.
    #[must_use]
    pub fn new(phase: &str, iteration: u32, step_type: &str, outcome: StepOutcome) -> Self {
        Self {
            phase: Arc::from(phase),
            iteration,
            step_type: Box::from(step_type),
            timestamp: timestamp(),
            outcome,
            agent: None,
            duration_secs: None,
            checkpoint_saved_at: None,
            git_commit_oid: None,
            modified_files_detail: None,
            prompt_used: None,
            issues_summary: None,
        }
    }

    /// Create a new execution step using a `StringPool` for interning.
    ///
    /// This is the preferred constructor when creating many `ExecutionSteps`,
    /// as it reduces memory usage by sharing allocations for repeated phase
    /// and agent names.
    pub fn new_with_pool(
        phase: &str,
        iteration: u32,
        step_type: &str,
        outcome: StepOutcome,
        pool: &mut crate::checkpoint::StringPool,
    ) -> Self {
        Self {
            phase: pool.intern_str(phase),
            iteration,
            step_type: Box::from(step_type),
            timestamp: timestamp(),
            outcome,
            agent: None,
            duration_secs: None,
            checkpoint_saved_at: None,
            git_commit_oid: None,
            modified_files_detail: None,
            prompt_used: None,
            issues_summary: None,
        }
    }

    /// Set the agent that executed this step.
    #[must_use]
    pub fn with_agent(mut self, agent: &str) -> Self {
        self.agent = Some(Arc::from(agent));
        self
    }

    /// Set the agent using a `StringPool` for interning.
    #[must_use]
    pub fn with_agent_pooled(
        mut self,
        agent: &str,
        pool: &mut crate::checkpoint::StringPool,
    ) -> Self {
        self.agent = Some(pool.intern_str(agent));
        self
    }

    /// Set the duration of this step.
    #[must_use]
    pub const fn with_duration(mut self, duration_secs: u64) -> Self {
        self.duration_secs = Some(duration_secs);
        self
    }

    /// Set the git commit OID created during this step.
    #[must_use]
    pub fn with_git_commit_oid(mut self, oid: &str) -> Self {
        self.git_commit_oid = Some(oid.to_string());
        self
    }
}

include!("execution_history/file_snapshot.rs");

/// Execution history tracking.
#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct ExecutionHistory {
    /// All execution steps in order
    pub steps: VecDeque<ExecutionStep>,
    /// File snapshots for key files at checkpoint time
    pub file_snapshots: HashMap<String, FileSnapshot>,
}

impl ExecutionHistory {
    /// Execution history must be bounded.
    ///
    /// The historical unbounded `add_step` API is intentionally not available in
    /// non-test builds to avoid reintroducing unbounded growth.
    ///
    /// ```compile_fail
    /// use ralph_workflow::checkpoint::ExecutionHistory;
    /// use ralph_workflow::checkpoint::execution_history::{ExecutionStep, StepOutcome};
    ///
    /// let mut history = ExecutionHistory::new();
    /// let step = ExecutionStep::new("Development", 0, "dev_run", StepOutcome::success(None, vec![]));
    ///
    /// // Unbounded push is not part of the public API.
    /// history.add_step(step);
    /// ```
    /// Create a new execution history.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Add an execution step with explicit bounding (preferred method).
    ///
    /// This is the preferred method that enforces bounded memory growth.
    /// Use this to prevent unbounded growth.
    pub fn add_step_bounded(&mut self, step: ExecutionStep, limit: usize) {
        let drop_count = self.steps.len().saturating_sub(limit.saturating_sub(1));
        self.steps = self
            .steps
            .iter()
            .skip(drop_count)
            .chain(std::iter::once(&step))
            .cloned()
            .collect();
    }

    /// Clone this execution history while enforcing a hard step limit.
    ///
    /// This is intended for resume paths where a legacy checkpoint may contain an
    /// oversized `steps` buffer. Cloning only the tail avoids allocating memory
    /// proportional to the checkpoint's full history.
    #[must_use]
    pub fn clone_bounded(&self, limit: usize) -> Self {
        if limit == 0 {
            return Self {
                steps: VecDeque::new(),
                file_snapshots: self.file_snapshots.clone(),
            };
        }

        let len = self.steps.len();
        if len <= limit {
            return self.clone();
        }

        let keep_from = len.saturating_sub(limit);
        let steps: VecDeque<_> = self.steps.iter().skip(keep_from).cloned().collect();
        Self {
            steps,
            file_snapshots: self.file_snapshots.clone(),
        }
    }
}

#[cfg(test)]
mod tests;
